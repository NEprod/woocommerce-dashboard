"""Reviewed bounded taxonomy application; catalogue folders are never storefront groups."""
import json
import uuid
import threading

from app import db, shopify_drafts as drafts, shopify_taxonomy as taxonomy
from app.models import Product, CatalogueOperation
from app.shopify_connection import ShopifyAdapter, ShopifyError
from app.shopify_comparison import mapping_digest
from app.taxonomy_assignments import product_document
from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation

MAX_SOURCES, MAX_PRODUCTS = 20, 25
_step_lock = threading.Lock()


def source_fingerprint(product):
    return drafts.digest({"source": product.source_relpath, "sku": product.sku,
        "authored": product_document(product), "taxonomy": taxonomy.fingerprint(product)})


def save_batch(row, data, status=None):
    encoded = json.dumps(data, sort_keys=True)
    if len(encoded.encode()) > 131072:
        raise ShopifyError("Taxonomy proposal exceeds 128 KiB. Review a smaller scope.")
    row.scope = encoded
    if status:
        row.status = status
    db.session.add(row)
    db.session.commit()


def overview():
    result_registry = taxonomy.load_configured_registry()
    if not result_registry.available:
        return []
    paths = taxonomy.category_paths(result_registry.snapshot.data)
    rows = [{"key": kind + ":" + r["key"], "kind": kind, "label": paths[r["key"]] if kind == "categories" else r["name"],
        "active": r["state"] == "active", "order": r.get("order", 0), "name": r["name"], "parent": r.get("parent")}
        for kind in ("categories", "storefront_collections") for r in result_registry.snapshot.data[kind]]
    rows.sort(key=lambda r: (r["kind"], r["order"], r["label"]))
    mappings = taxonomy.ShopifyCollectionIdentity.query.all()
    result = []
    for source in rows:
        linked = [m for m in mappings if m.source_key == source["key"]]
        result.append({**source, "mapping": "Mapped" if len(linked) == 1 and linked[0].state == "verified" else "Needs attention" if linked else "Not mapped"})
    return result


def evidence_totals():
    from app.models import ShopifyProductComparison
    from app.shopify_comparison import recent, MAX_AGE
    counts = {"memberships_matching": 0, "memberships_join": 0, "memberships_unverified": 0,
        "tags_matching": 0, "tags_pending": 0, "tags_unmanaged": 0, "needs_attention": 0}
    try:
        config = drafts.configuration().fingerprint()
    except Exception:
        return counts
    for row in ShopifyProductComparison.query.limit(1000).all():
        data = json.loads(row.summary_json)
        if data.get("config") != config:
            continue
        if data.get("refresh_failure") or data.get("blockers"):
            counts["needs_attention"] += 1
        fresh = recent(data.get("related_checked_at"), MAX_AGE)
        for group in data.get("collections", []):
            counts["memberships_matching" if fresh and group["action"] == "Already assigned" else "memberships_join" if fresh and group["action"] == "Join" else "memberships_unverified"] += 1
        if data.get("tags_managed") is False:
            counts["tags_unmanaged"] += 1
        elif data.get("tags_managed") and fresh:
            counts["tags_pending" if any(r["field"] == "tags" for r in data.get("changes", [])) else "tags_matching"] += 1
    return counts


def review(keys, adapter=None, *, after=0):
    lookup = {s["key"]: s for s in taxonomy.sources()}
    if (not isinstance(keys, list) or len(keys) > 80 or any(not isinstance(k, str) or len(k) > 200 for k in keys)
            or not 1 <= len(set(keys)) <= MAX_SOURCES or any(k not in lookup for k in keys)):
        raise ShopifyError("Select one to 20 existing browsing categories/storefront collections.")
    if type(after) is not int or after < 0:
        raise ShopifyError("Invalid product scope cursor.")
    keys = list(dict.fromkeys(keys))
    for key in list(keys):
        cursor = lookup[key]
        while key.startswith("categories:") and cursor.get("parent"):
            parent = "categories:" + cursor["parent"]
            if parent not in lookup:
                raise ShopifyError("Required category parent is inactive/unavailable. Resolve hierarchy before writes.")
            if parent not in keys:
                keys.append(parent)
            cursor = lookup[parent]
    if len(keys) > MAX_SOURCES:
        raise ShopifyError("Selected groups and required ancestors exceed 20; choose a smaller reviewed scope.")
    adapter = adapter or ShopifyAdapter()
    config = drafts.configuration()
    shop = adapter.test_connection(config)
    groups, steps, blockers = [], [], []
    for key in dict.fromkeys(keys):
        source, row = lookup[key], taxonomy.mapping(shop["shop_id"], key)
        item = {"source": source, "remote_id": row.remote_id if row else None, "action": "No Change", "candidates": []}
        try:
            if row and row.state == "verified":
                remote = taxonomy.validate_collection(adapter.collection_state(config, row.remote_id), row.remote_id)
                item.update(remote_name=remote["title"], mapping_fingerprint=mapping_digest(row))
                if remote["title"] != source["name"]:
                    item["warning"] = "Local name differs; remote rename is not part of this operation. Stable mapping retained."
            elif row:
                raise ShopifyError("Retained collection attempt requires explicit reconciliation; no Create retry.")
            else:
                inspected, _ = taxonomy.review_mapping(key, "inspect", adapter=adapter)
                item["candidates"] = inspected["candidates"]["nodes"]
                if item["candidates"]:
                    item["action"] = "Explicit Link required"
                    raise ShopifyError("Remote candidates require individual verified Link before batch approval.")
                created, token = taxonomy.review_mapping(key, "create", adapter=adapter)
                item["action"] = "Create"
                steps.append({"kind": "collection", "key": key, "review": token, "nonce": created["nonce"]})
        except ShopifyError as exc:
            if item["action"] != "Explicit Link required":
                item["action"] = "Needs attention"
            item["blocker"] = str(exc)
        groups.append(item)
    products = []
    if type(after) is not int or after < 0:
        raise ShopifyError("Invalid product scope cursor.")
    parents = Product.query.filter_by(product_type="simple", catalogue_status="active").filter(Product.id > after).order_by(Product.id).limit(MAX_PRODUCTS + 1).all()
    next_after = parents[MAX_PRODUCTS - 1].id if len(parents) > MAX_PRODUCTS else None
    for product in parents[:MAX_PRODUCTS]:
        assigned, issues = taxonomy.assignments(product)
        relevant = [s for s in assigned if s["key"] in keys]
        authored = product_document(product)
        if not relevant and "tags" not in authored:
            continue
        item = {"product_id": product.id, "sku": product.sku, "title": product.title, "memberships": [], "tags": "Unverified", "blockers": issues}
        item["blockers"] += [g["source"]["label"] + ": mapping blocked" for g in groups if g.get("blocker") and g["source"]["key"] in {s["key"] for s in relevant}]
        try:
            identity = drafts.matching_identity(shop["shop_id"], product)
            if not identity or identity.state != "verified" or identity.verification_state != "verified":
                item["blockers"].append("Product is unverified/recovery-required; publish or reconcile individually first. No product Create in taxonomy sync.")
            else:
                retained = drafts.evidence(identity)
                managed = retained["plan"].get("managed")
                if not isinstance(managed, dict) or not isinstance(managed.get("namespace"), str):
                    raise ShopifyError("Legacy managed-field evidence is missing. Open an individual product Review before including it in taxonomy sync; the retained identity is protected.")
                remote = adapter.read_simple(config, identity.shopify_product_id, managed["namespace"])
                drafts.simple_identity(identity, retained, remote)
                authored = product_document(product)
                if "tags" in authored and (not isinstance(authored["tags"], list) or any(not isinstance(t, str) or not t.strip() or len(t) > 255 for t in authored["tags"]) or len(authored["tags"]) > 250):
                    raise ShopifyError("Authored tags are invalid; validate JSON first.")
                tags = sorted(set(authored["tags"])) if "tags" in authored else None
                if tags is not None and (not isinstance(remote.get("tags"), list) or any(not isinstance(t, str) for t in remote["tags"])):
                    raise ShopifyError("Remote tags are incomplete.")
                item.update(tags="Unmanaged" if tags is None else "Matching" if sorted(remote["tags"]) == tags else "Pending changes",
                    removed_tags=sorted(set(remote.get("tags", [])) - set(tags)) if tags is not None else [])
                for source in relevant:
                    mapping = taxonomy.mapping(shop["shop_id"], source["key"])
                    member = False
                    if mapping and mapping.state == "verified":
                        member = taxonomy.validate_collection(adapter.collection_state(config, mapping.remote_id, identity.shopify_product_id), mapping.remote_id)["hasProduct"]
                    item["memberships"].append({"key": source["key"], "name": source["label"], "action": "Already assigned" if member else "Join"})
                if not item["blockers"] and (item["tags"] == "Pending changes" or any(m["action"] == "Join" for m in item["memberships"])):
                    if "write_products" not in shop["scopes"]:
                        raise ShopifyError("Missing write_products for membership/tag changes.")
                    steps.append({"kind": "product", "product_id": product.id, "identity_id": identity.id,
                        "source_fingerprint": source_fingerprint(product), "remote_id": identity.shopify_product_id,
                        "remote_digest": drafts.digest(remote), "keys": [s["key"] for s in relevant], "tags": tags,
                        "identity_digest": drafts.digest(identity.media_evidence_json)})
        except ShopifyError as exc:
            item["blockers"].append(str(exc))
        products.append(item)
    # Unverified products are explicitly excluded, not reported as synchronized.
    plan = {"version": 1, "store_id": shop["shop_id"], "config": config.fingerprint(), "sources": [lookup[k] for k in dict.fromkeys(keys)],
        "groups": groups, "products": products, "steps": steps, "blockers": blockers, "after": after, "next_after": next_after,
        "totals": {"collections_create": sum(s["kind"] == "collection" for s in steps), "products_apply": sum(s["kind"] == "product" for s in steps),
            "memberships_join": sum(m["action"] == "Join" for p in products for m in p["memberships"]),
            "tags_change": sum(p["tags"] == "Pending changes" for p in products), "excluded_products": sum(bool(p["blockers"]) for p in products),
            "excluded_groups": sum(bool(g.get("blocker")) for g in groups)}}
    row = CatalogueOperation(id=uuid.uuid4().hex, operation_type="shopify_taxonomy_batch", status="reviewed", finished_at=drafts.now())
    save_batch(row, {"version": 1, "plan": plan, "results": []})
    token = drafts.serializer().dumps({"taxonomy_batch": row.id, "digest": drafts.digest(plan)})
    return row, plan, token


def approve(token, acknowledged):
    from itsdangerous import BadSignature
    if not acknowledged:
        raise ShopifyError("Explicit taxonomy batch approval is required.")
    try:
        signed = drafts.serializer().loads(token, max_age=900)
        row = db.session.get(CatalogueOperation, signed["taxonomy_batch"])
        data = json.loads(row.scope)
    except (BadSignature, KeyError, AttributeError, ValueError):
        raise ShopifyError("Taxonomy review expired or is unavailable. Review again.") from None
    if row.status != "reviewed" or signed["digest"] != drafts.digest(data["plan"]) or data["plan"]["blockers"]:
        raise ShopifyError("Proposal changed, was already approved or has collection blockers. Resolve and review again.")
    data.update(approved_at=drafts.now().isoformat(), execution_nonce=uuid.uuid4().hex)
    row.finished_at = None
    save_batch(row, data, "approved")
    return row


def execution_token(row):
    data = json.loads(row.scope)
    return drafts.serializer().dumps({"taxonomy_execution": row.id, "nonce": data["execution_nonce"]})


def execute_one(token, adapter=None):
    from itsdangerous import BadSignature
    from app.shopify_comparison import recent
    from datetime import timedelta
    adapter = adapter or ShopifyAdapter()
    if not isinstance(token, str) or len(token) > 65536:
        raise ShopifyError("Invalid taxonomy execution token. Review the retained batch.")
    try:
        signed = drafts.serializer().loads(token, max_age=3600)
        row = db.session.get(CatalogueOperation, signed["taxonomy_execution"])
        data = json.loads(row.scope)
        if data["execution_nonce"] != signed["nonce"] or not recent(data["approved_at"], timedelta(hours=1)):
            raise KeyError()
    except (BadSignature, KeyError, AttributeError, ValueError):
        raise ShopifyError("Taxonomy execution approval expired. Preserve results; build a new review.") from None
    if row.status in {"succeeded", "partial"}:
        return row
    if not _step_lock.acquire(blocking=False):
        raise ShopifyError("Taxonomy step is busy. No duplicate step was started.")
    claimed = False
    try:
        claimed = CatalogueOperation.query.filter_by(id=row.id, status="approved").update({"status": "executing"}) == 1
        db.session.commit()
        if not claimed:
            raise ShopifyError("Batch is active or interrupted. Inspect/reconcile its retained step before continuing; no retry.")
        plan = data["plan"]
        lookup = {s["key"]: s for s in taxonomy.sources()}
        if plan["config"] != drafts.configuration().fingerprint() or any(lookup.get(s["key"]) != s for s in plan["sources"]):
            raise ShopifyError("Taxonomy/configuration changed after review. Completed results are retained; build a new review.")
        index = len(data["results"])
        if index >= len(plan["steps"]):
            save_batch(row, data, "partial" if plan["totals"]["excluded_products"] or plan["totals"]["excluded_groups"] or plan["next_after"] else "succeeded")
            row.finished_at = drafts.now()
            db.session.commit()
            return row
        step = plan["steps"][index]
        data["in_flight"] = {"index": index, "step": step}
        save_batch(row, data)  # durable claimed step BEFORE any child mutation
        if step["kind"] == "collection":
            result = taxonomy.confirm_mapping(step["review"], True, adapter)
            completed = {"kind": "collection", "key": step["key"], "remote_id": result.remote_id, "operation_id": result.operation_id, "state": "verified"}
        else:
            completed = apply_product(step, plan["store_id"], row.id, adapter)
        data["results"].append(completed)
        data.pop("in_flight", None)
        terminal = len(data["results"]) == len(plan["steps"])
        save_batch(row, data, ("partial" if plan["totals"]["excluded_products"] or plan["totals"]["excluded_groups"] or plan["next_after"] else "succeeded") if terminal else "approved")
        if terminal:
            row.finished_at = drafts.now()
            db.session.commit()
        return row
    except Exception as exc:
        db.session.rollback()
        if claimed:
            data["error"] = str(exc) if isinstance(exc, ShopifyError) else "Taxonomy step did not complete safely. Inspect retained identity/operation evidence before any retry."
            save_batch(row, data, "partial")
            row.recovery_state = "review_required"
            row.finished_at = drafts.now()
            db.session.commit()
        raise ShopifyError(data.get("error", str(exc))) from None
    finally:
        _step_lock.release()


def apply_product(step, store, batch_id, adapter):
    from app.models import ShopifyProductIdentity
    from app.shopify_comparison import mapping_digest, invalidate
    product = db.session.get(Product, step["product_id"])
    identity = db.session.get(ShopifyProductIdentity, step["identity_id"])
    lease = acquire_catalogue_operation("shopify_taxonomy_batch", {"mode": "product_taxonomy", "batch": batch_id, "product": step["product_id"]})
    attempted = False
    try:
        config = drafts.configuration()
        if adapter.test_connection(config)["shop_id"] != store:
            raise ShopifyError("Store identity changed; no taxonomy writes.")
        if (not product or source_fingerprint(product) != step["source_fingerprint"] or not identity or identity.state != "verified"
                or identity.store_id != store or identity.shopify_product_id != step["remote_id"]
                or drafts.digest(identity.media_evidence_json) != step["identity_digest"]):
            raise ShopifyError("Product source/identity changed or requires recovery. Build a new review; no retry.")
        data = drafts.evidence(identity)
        remote = adapter.read_simple(config, identity.shopify_product_id, data["plan"]["managed"]["namespace"])
        drafts.simple_identity(identity, data, remote)
        if drafts.digest(remote) != step["remote_digest"]:
            raise ShopifyError("Product changed remotely since taxonomy review. Build a new review.")
        targets = []
        batch_data = json.loads(db.session.get(CatalogueOperation, batch_id).scope)
        for key in step["keys"]:
            mapped = taxonomy.mapping(store, key)
            expected = next(g["remote_id"] for g in batch_data["plan"]["groups"] if g["source"]["key"] == key)
            if expected is None:
                expected = next((r["remote_id"] for r in batch_data["results"] if r.get("key") == key), None)
            if not mapped or mapped.state != "verified" or mapped.remote_id != expected:
                raise ShopifyError("Required mapping is unverified; reconcile first.")
            targets.append({"key": key, "remote_id": mapped.remote_id, "mapping_fingerprint": mapping_digest(mapped)})
        data["taxonomy_attempt"] = {"version": 1, "batch": batch_id, "tags": step["tags"], "collections": targets}
        identity.operation_id = lease.id
        identity.verification_state = "unverified"
        data["update_attempted"] = True
        drafts.save(identity, data, "updating")
        invalidate(identity.store_id, identity.source_identity)
        if step["tags"] is not None and sorted(remote["tags"]) != step["tags"]:
            attempted = True
            drafts.payload_result(adapter.update_simple_product(config, {"id": identity.shopify_product_id, "tags": step["tags"]}), "productUpdate")
        for item in targets:
            state = taxonomy.validate_collection(adapter.collection_state(config, item["remote_id"], identity.shopify_product_id), item["remote_id"])
            if not state["hasProduct"]:
                attempted = True
                drafts.payload_result(adapter.join_collection(config, item["remote_id"], identity.shopify_product_id), "collectionAddProducts")
        verify_product_attempt(identity, data, adapter.read_simple(config, identity.shopify_product_id, data["plan"]["managed"]["namespace"]), config, adapter)
        finish_catalogue_operation(lease.id, status="succeeded", products_attempted=1, products_succeeded=1, operation_summary={"batch": batch_id, "taxonomy_verified": True})
        return {"kind": "product", "product_id": product.id, "state": "taxonomy_verified", "operation_id": lease.id}
    except Exception as exc:
        db.session.rollback()
        message = str(exc) if isinstance(exc, ShopifyError) else "Taxonomy write/readback did not complete safely. Reconcile the retained product; no blind retry."
        if attempted:
            data["error"] = message
            drafts.save(identity, data, "uncertain")
        elif identity and "data" in locals() and data.get("taxonomy_attempt"):
            data.pop("taxonomy_attempt", None)
            identity.verification_state = "verified"
            drafts.save(identity, data, "verified")
        finish_catalogue_operation(lease.id, status="partial" if attempted else "failed", error=message, recovery_state="review_required" if attempted else "none")
        raise ShopifyError(message) from None


def verify_product_attempt(identity, data, remote, config, adapter):
    drafts.simple_identity(identity, data, remote)
    attempt = data["taxonomy_attempt"]
    if attempt["tags"] is not None and sorted(remote.get("tags", [])) != attempt["tags"]:
        raise ShopifyError("Reviewed tags remain unresolved. Reconcile before any retry.")
    for item in attempt["collections"]:
        if not taxonomy.validate_collection(adapter.collection_state(config, item["remote_id"], identity.shopify_product_id), item["remote_id"])["hasProduct"]:
            raise ShopifyError("Reviewed membership remains unresolved. Reconcile, never retry the whole batch blindly.")
    if attempt["tags"] is not None:
        data["plan"]["managed"]["product"]["tags"] = attempt["tags"]
    data["last_taxonomy_verification"] = {**attempt, "at": drafts.now().isoformat()}
    data.pop("taxonomy_attempt", None)
    data.pop("error", None)
    identity.verification_state = "verified"
    # Do not promote subset verification to a new complete post-write equality timestamp.
    drafts.save(identity, data, "verified")


def recover_batch(row, adapter=None):
    from app.utils.operation_control import get_active_operation
    if not _step_lock.acquire(blocking=False):
        raise ShopifyError("Taxonomy execution is active. Wait before read-only inspection.")
    try:
        if get_active_operation():
            raise ShopifyError("A catalogue operation is active. Wait before reconciliation.")
        return _recover_batch(row, adapter)
    finally:
        _step_lock.release()


def _recover_batch(row, adapter=None):
    """Explicit read-only inspection of an interrupted step, not an execution retry."""
    from app.models import ShopifyProductIdentity
    adapter = adapter or ShopifyAdapter()
    data = json.loads(row.scope)
    flight = data.get("in_flight")
    if not flight:
        raise ShopifyError("No interrupted step is retained. Completed results remain; build a fresh review.")
    config = drafts.configuration()
    if adapter.test_connection(config)["shop_id"] != data["plan"]["store_id"]:
        raise ShopifyError("Restore the reviewed store before reconciliation.")
    step = flight["step"]
    if step["kind"] == "collection":
        mapped = taxonomy.mapping(data["plan"]["store_id"], step["key"])
        if not mapped or mapped.state != "verified" or json.loads(mapped.evidence_json).get("nonce") != step["nonce"]:
            raise ShopifyError("Use individual collection reconciliation to verify the retained attempt marker first. Do not retry Create.")
        remote = taxonomy.validate_collection(adapter.collection_state(config, mapped.remote_id), mapped.remote_id)
        if (remote.get("metafield") or {}).get("value") != step["nonce"]:
            raise ShopifyError("Remote collection attempt marker is not verified.")
        result = {"kind": "collection", "key": step["key"], "remote_id": mapped.remote_id, "operation_id": mapped.operation_id, "state": "verified"}
    else:
        identity = db.session.get(ShopifyProductIdentity, step["identity_id"])
        if not identity or identity.store_id != data["plan"]["store_id"]:
            raise ShopifyError("Product identity needs administrator review.")
        if drafts.evidence(identity).get("taxonomy_attempt"):
            drafts.reconcile(identity, adapter=adapter)
        proof = drafts.evidence(identity).get("last_taxonomy_verification")
        if not proof or proof["batch"] != row.id or identity.state != "verified":
            raise ShopifyError("No completed taxonomy readback for this batch. Inspect the product and build a new review; no blind retry.")
        remote = adapter.read_simple(config, identity.shopify_product_id, drafts.evidence(identity)["plan"]["managed"]["namespace"])
        temporary = {**drafts.evidence(identity), "taxonomy_attempt": proof}
        verify_product_attempt(identity, temporary, remote, config, adapter)
        result = {"kind": "product", "product_id": step["product_id"], "state": "taxonomy_verified", "operation_id": identity.operation_id}
    data["results"].append(result)
    data.pop("in_flight", None)
    data.pop("error", None)
    complete = len(data["results"]) == len(data["plan"]["steps"])
    save_batch(row, data, ("partial" if data["plan"]["totals"]["excluded_products"] or data["plan"]["totals"]["excluded_groups"] or data["plan"]["next_after"] else "succeeded") if complete else "approved")
    return row
