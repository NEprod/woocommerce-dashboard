"""Exact remote deletion/absence evidence. Never deletes or recreates local data."""
import json
import uuid
from itsdangerous import BadSignature
from app import db, shopify_drafts as drafts
from app.models import Product, ShopifyProductIdentity, CatalogueOperation
from app.shopify_connection import ShopifyAdapter, ShopifyError
from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation


def connection(row, adapter):
    config = drafts.configuration()
    shop = adapter.test_connection(config)
    if shop["shop_id"] != row.store_id or "read_products" not in shop["scopes"]:
        raise ShopifyError("Restore the verified store and read_products before checking this exact identity.")
    return config, shop


def trusted(row, remote):
    data = drafts.evidence(row)
    if (not isinstance(remote, dict) or remote.get("id") != row.shopify_product_id
            or remote.get("status") not in {"DRAFT", "ACTIVE", "ARCHIVED"}
            or (remote.get("metafield") or {}).get("value") != data.get("plan", {}).get("nonce")):
        raise ShopifyError("Remote product/store/correlation identity is untrusted; deletion is blocked.")
    for kind, bound in (("variants", 32), ("media", 20)):
        part = remote.get(kind)
        if not isinstance(part, dict) or part.get("pageInfo", {}).get("hasNextPage") is not False or len(part.get("nodes", [])) > bound:
            raise ShopifyError("Remote deletion review is incomplete or exceeds the bounded scope.")
    variants = {v["id"]: v for v in remote["variants"]["nodes"]}
    retained = list(data.get("variant_maps", {}).values()) if data.get("kind") == "variable" else [
        {"id": data.get("variant_id"), "inventory_id": data.get("inventory_item_id"), "sku": row.sku}]
    for item in retained:
        current = variants.get(item["id"])
        if (not current or current.get("sku") != item["sku"] or current.get("inventoryItem", {}).get("id") != item["inventory_id"]
                or current["inventoryItem"].get("sku") != item["sku"]):
            raise ShopifyError("Retained variant/InventoryItem identity differs. Resolve it before destructive review.")
    return remote


def review(product, adapter=None):
    adapter = adapter or ShopifyAdapter()
    config = drafts.configuration()
    shop = adapter.test_connection(config)
    row = drafts.matching_identity(shop["shop_id"], product)
    if not row or row.state != "verified" or row.verification_state != "verified" or not row.shopify_product_id:
        raise ShopifyError("Deletion requires one verified retained product identity, not an uncertain Create/update.")
    if row.sku!=product.sku or row.source_identity!=product.source_relpath:
        raise ShopifyError("Local source/SKU no longer matches the retained Shopify identity. Deletion blocked.")
    data = drafts.evidence(row)
    if data.get("native_dimensions", {}).get("state") in {"pending", "uncertain"} or any(v.get("state") in {"pending", "uncertain"} for v in data.get("variant_native", {}).values()):
        raise ShopifyError("Reconcile uncertain inventory stages before deletion.")
    from app.shopify_package_assignments import guard as package_guard
    package_guard(data)
    remote = trusted(row, adapter.lifecycle_product(config, row.shopify_product_id))
    if "write_products" not in shop["scopes"]:
        raise ShopifyError("Missing write_products or product deletion permission.")
    plan = {"version": 1, "product_id": product.id, "identity_id": row.id, "sku": row.sku,
        "local_title": product.title, "remote": remote, "store_id": row.store_id,
        "config": config.fingerprint(), "identity": drafts.digest(row.media_evidence_json),
        "publication": "Not independently verified; ACTIVE is not Online Store publication."}
    from app.models import ShopifyProductComparison
    from app.shopify_comparison import recent, MAX_AGE
    compared=ShopifyProductComparison.query.filter_by(store_id=row.store_id,source_identity=row.source_identity).first()
    proof=json.loads(compared.summary_json).get("online_store",{}) if compared else {}
    if proof.get("config")==config.fingerprint() and proof.get("remote_id")==row.shopify_product_id and proof.get("status")==remote["status"] and recent(proof.get("checked_at"),MAX_AGE):
        plan["publication"]="Last independently checked Online Store: "+("eligible" if proof.get("published") and remote["status"]=="ACTIVE" else "not eligible")+" · cached "+proof["checked_at"]+" · "+proof["publication"]
    return plan, drafts.serializer().dumps({"delete_shopify_product": plan})


def archive_absence(row, remote_id, origin, operation_id=None):
    """Archive full previous evidence BEFORE clearing active child bindings."""
    old = drafts.evidence(row)
    if operation_id:
        archive = db.session.get(CatalogueOperation, operation_id)
    else:
        archive = CatalogueOperation(id=uuid.uuid4().hex, operation_type="shopify_product_lifecycle",
            status="succeeded", finished_at=drafts.now(), recovery_state="none")
        db.session.add(archive)
    evidence = {"version": 1, "identity_id": row.id, "source": row.source_identity, "sku": row.sku,
        "store_id": row.store_id, "remote_id": remote_id, "origin": origin, "absent_at": drafts.now().isoformat(),
        "previous_operation": row.operation_id, "previous_evidence": old}
    # For reviewed delete, scope was already captured before mutation; preserve it.
    if not operation_id:
        archive.scope = json.dumps(evidence, sort_keys=True)
    scope=json.loads(archive.scope or "{}")
    scope["operation_summary"]={"version":1,"remote_id":remote_id,"confirmed_absent":True,"origin":origin}
    archive.scope=json.dumps(scope,sort_keys=True)
    from app.shopify_capacity import fence_owner
    fence_owner()
    db.session.commit()
    row.shopify_product_id = None
    row.operation_id = archive.id
    row.verified_at = None
    row.verification_state = "remote_absent"
    drafts.save(row, {"version": 1, "stage": "remote_absent", "history_operation": archive.id,
        "previous_remote_id": remote_id, "absent_at": evidence["absent_at"]}, "remote_deleted")
    from app.shopify_comparison import invalidate
    invalidate(row.store_id, row.source_identity)
    return row


def check_absence(row, adapter, *, operation_id=None, origin="remote_admin", first=None):
    """Only two successful null reads under a fresh verified store attest absence."""
    config, _ = connection(row, adapter)
    remote_id = row.shopify_product_id
    if not remote_id:
        return row.state == "remote_deleted"
    remote = adapter.lifecycle_product(config, remote_id)
    if remote is not None:
        trusted(row, remote)
        return False
    # A separate request—not an error, timeout, omitted field or revision hint.
    if adapter.lifecycle_product(config, remote_id) is not None:
        raise ShopifyError("Conflicting existence evidence; retained identity remains active.")
    archive_absence(row, remote_id, origin, operation_id)
    return True


def confirm(token, acknowledged, sku, *, expected_product_id, adapter=None):
    if not acknowledged:
        raise ShopifyError("Separate destructive deletion acknowledgement is required.")
    try:
        plan = drafts.serializer().loads(token, max_age=900)["delete_shopify_product"]
    except (BadSignature, KeyError):
        raise ShopifyError("Deletion review expired or is not a deletion authorisation.") from None
    if plan["product_id"] != expected_product_id or sku != plan["sku"]:
        raise ShopifyError("Typed SKU or reviewed product does not match. Nothing was deleted.")
    adapter = adapter or ShopifyAdapter()
    lease = acquire_catalogue_operation("shopify_product_lifecycle", {"mode": "delete", "product_id": expected_product_id})
    row, attempted = None, False
    try:
        product = db.session.get(Product, expected_product_id)
        fresh, _ = review(product, adapter)
        if fresh != plan:
            raise ShopifyError("Exact remote/retained identity changed since Delete review. Review again.")
        row = db.session.get(ShopifyProductIdentity, plan["identity_id"])
        operation = db.session.get(CatalogueOperation, lease.id)
        operation.scope = json.dumps({"version": 1, "plan": plan, "previous_evidence": drafts.evidence(row)}, sort_keys=True)
        data = drafts.evidence(row)
        data["lifecycle"] = {"state": "delete_pending", "remote_id": row.shopify_product_id, "operation_id": lease.id}
        drafts.save(row, data, "deleting")
        attempted = True
        response = adapter.delete_product(drafts.configuration(), row.shopify_product_id)
        payload = drafts.payload_result(response, "productDelete")
        if payload.get("deletedProductId") != row.shopify_product_id:
            raise ShopifyError("Deletion receipt is uncertain; reconcile the exact identity, never retry blindly.")
        if not check_absence(row, adapter, operation_id=lease.id, origin="reviewed_delete"):
            raise ShopifyError("Product still exists after deletion receipt; retain identity and reconcile.")
        finish_catalogue_operation(lease.id, status="succeeded", operation_summary={"confirmed_absent": True, "remote_id": plan["remote"]["id"]})
        return row
    except Exception as exc:
        db.session.rollback()
        if row and attempted:
            data = drafts.evidence(row)
            data["lifecycle"] = {"state": "delete_uncertain", "operation_id": lease.id, "remote_id": plan["remote"]["id"]}
            data["error"] = "Shopify deletion not independently established. Reconcile exact existence; no retry is authorised."
            drafts.save(row, data, "delete_uncertain")
        finish_catalogue_operation(lease.id, status="partial" if attempted else "failed", recovery_state="review_required" if attempted else "none",
            error=str(exc) if isinstance(exc, ShopifyError) else "Deletion interrupted; use exact read-only reconciliation.")
        if attempted:
            return row
        raise


def reconcile(row, adapter=None):
    adapter = adapter or ShopifyAdapter()
    lease = acquire_catalogue_operation("shopify_product_lifecycle", {"mode": "read_only", "identity_id": row.id})
    try:
        old = drafts.evidence(row)
        original = old.get("lifecycle", {}).get("operation_id")
        if check_absence(row, adapter, operation_id=original, origin="delete_reconciliation"):
            result = "absent"
        else:
            config, _ = connection(row, adapter)
            trusted(row, adapter.lifecycle_product(config, row.shopify_product_id))
            # Survival proves the delete did not take effect, not full metadata equality.
            old.pop("lifecycle", None); old.pop("error", None)
            drafts.save(row, old, "verified")
            row.verification_state = "verified"
            db.session.commit()
            from app.shopify_comparison import invalidate
            invalidate(row.store_id, row.source_identity)
            result = "exists; refresh comparison before updating"
        finish_catalogue_operation(lease.id, status="succeeded", operation_summary={"read_only": True, "existence": result})
        return row
    except Exception:
        finish_catalogue_operation(lease.id, status="failed", recovery_state="review_required", error="Exact product existence not established; identity remains protected.")
        raise
