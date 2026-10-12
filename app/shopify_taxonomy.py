"""Operational Shopify mappings over the existing TLC registry; no authored writes."""
import json
import uuid
import hashlib

from app import db
from app.models import Collection, ShopifyCollectionIdentity
from app.shopify_connection import ShopifyAdapter, ShopifyError, _gid
from app.taxonomy_assignments import product_document, resolve
from app.taxonomy_registry import load_configured_registry
from app.taxonomy_workspace import category_paths

MAX_GROUPS = 12


def encode(data):
    value = json.dumps(data, sort_keys=True)
    if len(value.encode()) > 32768 or data.get("version") != 1:
        raise ShopifyError("Collection evidence exceeds the supported bound.")
    return value


def sources():
    from flask import g, has_request_context, request
    if has_request_context() and request.method == "GET" and hasattr(g, "shopify_taxonomy_sources"):
        return g.shopify_taxonomy_sources
    registry = load_configured_registry()
    rows = []
    if registry.available:
        paths = category_paths(registry.snapshot.data)
        for kind in ("categories", "storefront_collections"):
            for item in registry.snapshot.data[kind]:
                if item["state"] == "active":
                    rows.append({"key": kind + ":" + item["key"], "name": item["name"],
                        "label": paths[item["key"]] if kind == "categories" else item["name"], "slug": item["slug"],
                        "parent": item.get("parent"), "order": item.get("order"), "aliases": list(item.get("aliases", []))})
    for collection in Collection.query.order_by(Collection.id).all():
        if collection.source_relpath:
            rows.append({"key": "collection:" + collection.source_relpath, "name": collection.name,
                "label": "Catalogue collection · " + collection.name,
                "slug": "tlc-" + hashlib.sha256(collection.source_relpath.encode()).hexdigest()[:20]})
    if has_request_context() and request.method == "GET":
        g.shopify_taxonomy_sources = rows
    return rows


def assignments(product):
    doc = product_document(product)
    resolved = resolve(doc)
    wanted, blockers = [], []
    for kind in ("categories", "storefront_collections"):
        for item in resolved[kind]:
            definition = item["definition"]
            if not definition or definition["state"] != "active":
                blockers.append(f'Unresolved active {kind}: {item["value"]}')
            else:
                wanted.append(kind + ":" + definition["key"])
    if product.collection and product.collection.source_relpath:
        wanted.append("collection:" + product.collection.source_relpath)
    lookup = {r["key"]: r for r in sources()}
    if len(set(wanted)) > MAX_GROUPS:
        blockers.append("At most 12 collection memberships can be reviewed per Simple product.")
    return [lookup[k] for k in sorted(set(wanted))[:MAX_GROUPS] if k in lookup], blockers


def fingerprint(product):
    from app.shopify_drafts import digest
    rows, blockers = assignments(product)
    # Include effective definitions/order/aliases/parent paths, not unrelated registry entries.
    return digest({"sources": rows, "blockers": blockers,
        "categories": product_document(product).get("categories", []),
        "collections": product_document(product).get("storefront_collections", [])})


def mapping(store, key):
    return ShopifyCollectionIdentity.query.filter_by(store_id=store, source_key=key).first()


def membership_plan(product, store, remote_id, config, adapter):
    from app.shopify_comparison import mapping_digest
    rows, blockers = assignments(product)
    result, standards = [], set()
    for source in rows:
        row = mapping(store, source["key"])
        item = dict(source, remote_id=row.remote_id if row else None, mapping_state=row.state if row else "missing",
            mapping_fingerprint=mapping_digest(row), action="Resolve mapping", member=None)
        if not row or row.state != "verified" or not row.remote_id:
            blockers.append("Collection mapping required: " + source["label"])
        else:
            remote = adapter.collection_state(config, row.remote_id, remote_id or "gid://shopify/Product/0")
            validate_collection(remote, row.remote_id)
            item.update(remote_name=remote["title"], member=remote["hasProduct"] if remote_id else False,
                action="Already assigned" if remote_id and remote["hasProduct"] else "Join")
            if row.standard_category_id:
                standards.add(row.standard_category_id)
        result.append(item)
    if len(standards) > 1:
        blockers.append("Multiple explicit Shopify standard categories conflict; review their mappings.")
    return result, blockers, next(iter(standards)) if len(standards) == 1 else None


def validate_collection(remote, expected):
    if (not isinstance(remote, dict) or remote.get("id") != expected or not isinstance(remote.get("title"), str)
            or "ruleSet" not in remote or remote["ruleSet"] is not None or not isinstance(remote.get("hasProduct"), bool)):
        raise ShopifyError("Collection missing, automated or incomplete; only verified manual collections are supported.")
    sources = remote.get("sources")
    if not isinstance(sources, list):
        raise ShopifyError("Collection source evidence is incomplete; review a manual product collection.")
    for source in sources:
        if (source.get("__typename") != "CollectionConditionsSource" or source.get("targetType") != "PRODUCTS"
                or source.get("shareable") is not False or source.get("inclusion", {}).get("conditions") != []):
            raise ShopifyError("Rule-based, shared or subcollection sources are unsupported; choose a manual product collection.")
        exclusion = source.get("exclusion")
        if exclusion is not None and (exclusion.get("conditions") != [] or exclusion.get("selections", {}).get("nodes") != []):
            raise ShopifyError("Collection exclusion rules are unsupported; choose a manual product collection.")
    return remote


def review_mapping(source_key, mode, remote_id=None, standard=None, adapter=None):
    from app.shopify_drafts import configuration, digest, serializer
    config, adapter = configuration(), adapter or ShopifyAdapter()
    shop = adapter.test_connection(config)
    source = next((r for r in sources() if r["key"] == source_key), None)
    if not source:
        raise ShopifyError("Local collection definition changed or is inactive. Reload Taxonomy.")
    row = mapping(shop["shop_id"], source_key)
    if row and row.state != "verified" and mode not in {"reconcile", "inspect"}:
        raise ShopifyError("Collection recovery required. Reconcile its recorded attempt; never retry Create.")
    candidates = adapter.collection_candidates(config, source["name"], source["slug"])
    if candidates["pageInfo"]["hasNextPage"]:
        raise ShopifyError("Collection candidates exceed the read bound; inspect Shopify before linking.")
    remote = None
    if mode == "inspect":
        pass
    elif mode == "create":
        if row or candidates["nodes"]:
            raise ShopifyError("A retained mapping or remote candidate exists. Explicitly review Link instead of Create.")
        if "write_products" not in shop["scopes"]:
            raise ShopifyError("Missing write_products for collection creation. Read-only linking remains available.")
    elif mode in {"link", "reconcile"}:
        if not remote_id:
            raise ShopifyError("Choose an explicit Collection GID to link; title matching never adopts it.")
        remote_id = _gid(remote_id, "Collection")
        owner = ShopifyCollectionIdentity.query.filter_by(store_id=shop["shop_id"], remote_id=remote_id).first()
        if owner and owner.source_key != source_key:
            raise ShopifyError("This Shopify collection is already mapped to a different stable TLC identity.")
        remote = validate_collection(adapter.collection_state(config, remote_id), remote_id)
        if row and row.remote_id and row.remote_id != remote_id:
            raise ShopifyError("A retained mapping cannot be replaced with another collection.")
        if mode == "reconcile":
            if not row:
                raise ShopifyError("No uncertain collection attempt is retained.")
            receipt = json.loads(row.evidence_json)
            if (remote.get("metafield") or {}).get("value") != receipt.get("nonce"):
                raise ShopifyError("Candidate lacks the retained creation-attempt marker. Automatic recovery refused.")
    else:
        raise ShopifyError("Choose Inspect, Create, Link or read-only Reconcile.")
    if standard:
        standard = adapter.standard_category(config, standard)["id"]
    plan = {"version": 1, "source": source, "mode": mode, "store_id": shop["shop_id"],
        "config": config.fingerprint(), "remote_id": remote_id, "standard": standard or None,
        "candidates": candidates, "remote_digest": digest(remote), "row_digest": digest(row.evidence_json) if row else None,
        "row_state": row.state if row else None, "nonce": uuid.uuid4().hex}
    return plan, None if mode == "inspect" else serializer().dumps({"collection_mapping": plan})


def confirm_mapping(token, acknowledged, adapter=None):
    from itsdangerous import BadSignature
    from app.shopify_drafts import configuration, digest, serializer, payload_result, now
    from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation
    if not acknowledged:
        raise ShopifyError("Explicit collection-mapping approval is required.")
    try:
        plan = serializer().loads(token, max_age=900)["collection_mapping"]
    except (BadSignature, KeyError):
        raise ShopifyError("Collection review expired. Review again.") from None
    if plan["mode"] not in {"create", "link", "reconcile"}:
        raise ShopifyError("Candidate inspection cannot authorize a mapping or write.")
    adapter = adapter or ShopifyAdapter()
    lease = acquire_catalogue_operation("shopify_draft_publish", {"mode": "collection_mapping", "source": plan["source"]["key"]})
    attempted, row = False, None
    try:
        fresh, _ = review_mapping(plan["source"]["key"], plan["mode"], plan["remote_id"], plan["standard"], adapter)
        for key in ("source", "mode", "store_id", "config", "remote_id", "standard", "candidates", "remote_digest", "row_digest", "row_state"):
            if fresh[key] != plan[key]:
                raise ShopifyError("Collection/store/source/candidates changed after review. Review again.")
        row = mapping(plan["store_id"], plan["source"]["key"])
        if row is None and plan["mode"] == "create":
            row = ShopifyCollectionIdentity(store_id=plan["store_id"], source_key=plan["source"]["key"],
                state="pending", evidence_json=encode(plan), operation_id=lease.id)
            db.session.add(row)
            db.session.commit()  # SQLite uniqueness reservation before create.
        config = configuration()
        if plan["mode"] == "create":
            row.state, row.operation_id = "creating", lease.id
            row.evidence_json = encode(plan)
            db.session.commit()
            attempted = True
            result = payload_result(adapter.create_collection(config, {"title": plan["source"]["name"],
                "handle": plan["source"]["slug"], "metafields": [{"namespace": "tlc_dashboard", "key": "collection_attempt", "type": "single_line_text_field", "value": plan["nonce"]}]}), "collectionCreate")
            row.remote_id = _gid((result.get("collection") or {}).get("id"), "Collection")
            db.session.commit()  # retain successful create even if readback fails
        else:
            # Read-only Link must not leave an unresolved create reservation if reads fail.
            remote = validate_collection(adapter.collection_state(config, plan["remote_id"]), plan["remote_id"])
            if row is None:
                row = ShopifyCollectionIdentity(store_id=plan["store_id"], source_key=plan["source"]["key"],
                    state="pending", evidence_json=encode(plan))
                db.session.add(row)
            row.remote_id = plan["remote_id"]
        if plan["mode"] == "create":
            remote = validate_collection(adapter.collection_state(config, row.remote_id), row.remote_id)
        if plan["mode"] == "create" and (remote.get("metafield") or {}).get("value") != plan["nonce"]:
            raise ShopifyError("Collection creation marker was not independently verified.")
        row.state, row.standard_category_id, row.operation_id = "verified", plan["standard"], lease.id
        # Preserve original nonce on recovery; never rewrite uncertain create history.
        row.evidence_json = encode({**json.loads(row.evidence_json), "last_review": plan,
            "remote_name": remote["title"], "verified_at": now().isoformat()})
        row.updated_at = now()
        db.session.commit()
        finish_catalogue_operation(lease.id, status="succeeded", operation_summary={"collection_identity": row.id, "remote_writes": int(attempted)})
        return row
    except Exception as exc:
        db.session.rollback()
        message = str(exc) if isinstance(exc, ShopifyError) else "Collection identity/write uncertain. Reconcile before any create retry."
        if attempted and row:
            row.state = "uncertain"
            row.evidence_json = encode({**json.loads(row.evidence_json), "error": message})
            db.session.commit()
        finish_catalogue_operation(lease.id, status="partial" if attempted else "failed", error=message,
            recovery_state="review_required" if attempted else "none")
        raise ShopifyError(message) from None


def join_reviewed(row, data, config, adapter):
    from app.shopify_drafts import save, payload_result, local_plan, configuration
    from app.models import Product
    for item in data["plan"].get("collections", []):
        if item["action"] != "Join":
            continue
        if (local_plan(db.session.get(Product, row.product_id))["digest"] != data["plan"]["digest"]
                or configuration().fingerprint() != data["plan"]["config_digest"]):
            raise ShopifyError("Local source/configuration changed before membership write. Reconcile retained product.")
        identity = mapping(row.store_id, item["key"])
        if not identity or identity.state != "verified" or identity.remote_id != item["remote_id"]:
            raise ShopifyError("Collection mapping changed during execution; reconcile retained product before further writes.")
        remote = validate_collection(adapter.collection_state(config, item["remote_id"], row.shopify_product_id), item["remote_id"])
        if not remote["hasProduct"]:
            data.update(stage="collection_membership", update_attempted=True)
            save(row, data, "updating")
            payload_result(adapter.join_collection(config, item["remote_id"], row.shopify_product_id), "collectionAddProducts")
        data.setdefault("completed_steps", []).append("join:" + item["key"])
        save(row, data)


def verify_memberships(plan, config, adapter, product_id):
    for item in plan.get("collections", []):
        remote = validate_collection(adapter.collection_state(config, item["remote_id"], product_id), item["remote_id"])
        if not remote["hasProduct"]:
            raise ShopifyError("Managed collection membership is unresolved. Reconcile; do not retry the whole product blindly.")
