"""Explicit signed inventory-only correction, never full synchronisation proof."""
import copy
from app import db
from app.models import Product, ShopifyProductIdentity
from app.shopify_connection import ShopifyAdapter, ShopifyError
from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation


def restrict(plan):
    plan["scope"] = "inventory"
    for field in plan["fields"]:
        field["deferred"] = field["field"].split(".")[-1] not in {"tracked", "inventoryPolicy"}
    plan["blockers"] = [b for b in plan["blockers"] if not b.startswith("Live intent:")]
    plan["warnings"].append("Inventory-only correction: metadata, informational attributes, collections, media and publication are deferred. This does not establish full synchronisation.")
    plan["classification"] = "update" if any(f["changed"] and not f["deferred"] for f in plan["fields"]) else "no_change"


def confirm(reviewed, adapter=None):
    from app import shopify_drafts as drafts
    adapter = adapter or ShopifyAdapter()
    product = db.session.get(Product, reviewed["product_id"])
    row = db.session.get(ShopifyProductIdentity, reviewed.get("identity_id"))
    if not product or not row or row.state not in {"verified", "scoped_verified"}:
        raise ShopifyError("Inventory-only correction requires a trusted retained identity; reconcile uncertain operations first.")
    lease = acquire_catalogue_operation("shopify_draft_publish", {"product_id":product.id,"mode":"inventory_only"})
    attempted = False
    previous = copy.deepcopy(drafts.evidence(row))
    old_verification = row.verification_state
    try:
        plan, _ = drafts.preview(product, adapter, scope="inventory")
        for key in ("digest","remote_digest","identity_digest","store_id","config_digest","taxonomy_review_digest"):
            if plan.get(key) != reviewed.get(key):
                raise ShopifyError("Source, inventory, identity or configuration changed since Review; no write attempted.")
        if plan["blockers"] or plan["classification"] != "update":
            raise ShopifyError("Inventory correction is blocked or already matching; build a fresh Review.")
        config = drafts.configuration()
        variants = plan.get("variants") or [{"remote_id":plan["variant_id"],"inventory_id":plan["inventory_item_id"],"managed":plan["managed"]["variant"]}]
        changes = {f["field"] for f in plan["fields"] if f["changed"] and not f["deferred"]}
        inputs = []
        for variant in variants:
            if variant.get("action") == "Create":
                continue  # new variant creation is outside explicit inventory correction
            prefix = "variants." + variant["sku"] + "." if plan.get("kind") == "variable" else ""
            value = {"id":variant["remote_id"]}
            if prefix + "inventoryPolicy" in changes:
                value["inventoryPolicy"] = variant["managed"]["inventoryPolicy"]
            if prefix + "tracked" in changes:
                value["inventoryItem"] = {"tracked":variant["managed"]["inventoryItem"]["tracked"]}
            if len(value) > 1:
                inputs.append(value)
        if not inputs:
            raise ShopifyError("No reviewed inventory differences remain.")
        data = copy.deepcopy(previous)
        data["inventory_update"] = {"version":1,"state":"pending","inputs":inputs,"digest":plan["digest"],
            "config":config.fingerprint(),"previous_verification":old_verification,"operation_id":lease.id}
        data["stage"] = "inventory_update"
        row.operation_id, row.verification_state = lease.id, "unverified"
        drafts.save(row,data,"updating")
        from app.shopify_comparison import invalidate
        invalidate(row.store_id,row.source_identity)
        attempted = True  # durable receipt precedes mutation; no blind replay
        result = adapter.update_variants(config,row.shopify_product_id,inputs) if plan.get("kind") == "variable" else adapter.update_simple_variant(config,row.shopify_product_id,inputs[0])
        drafts.payload_result(result,"productVariantsBulkUpdate")
        verify(row,data,adapter)
        finish_catalogue_operation(lease.id,status="partial",products_attempted=1,products_succeeded=1,
            recovery_state="none",operation_summary={"identity_id":row.id,"stage":"inventory_verified","scope":"inventory","deferred":"Full metadata/attributes/publication comparison required"})
        return row
    except Exception as exc:
        db.session.rollback()
        message = str(exc) if isinstance(exc,ShopifyError) else "Inventory outcome uncertain; reconcile before another write."
        if attempted:
            data = drafts.evidence(row)
            data["inventory_update"]["state"] = "uncertain"
            data["error"] = message
            drafts.save(row,data,"uncertain")
        finish_catalogue_operation(lease.id,status="partial" if attempted else "failed",error=message,
            recovery_state="review_required" if attempted else "none")
        if not attempted:
            raise ShopifyError(message) from None
        return row


def verify(row,data,adapter):
    from app import shopify_drafts as drafts
    config = drafts.configuration()
    proof = data["inventory_update"]
    if config.fingerprint() != proof["config"]:
        raise ShopifyError("Restore the reviewed configuration before inventory reconciliation.")
    if data.get("kind") == "variable":
        from app.shopify_variables import remote_identity
        remote = adapter.read_variable(config,row.shopify_product_id,data["plan"]["managed"]["namespace"])
        nodes = remote_identity(row,data,remote)
    else:
        remote = adapter.read_simple(config,row.shopify_product_id,data["plan"]["managed"]["namespace"])
        nodes = [drafts.simple_identity(row,data,remote)]
    for wanted in proof["inputs"]:
        actual = next((v for v in nodes if v["id"] == wanted["id"]),None)
        if not actual or ("inventoryPolicy" in wanted and actual.get("inventoryPolicy") != wanted["inventoryPolicy"]) or ("inventoryItem" in wanted and actual["inventoryItem"].get("tracked") is not wanted["inventoryItem"]["tracked"]):
            raise ShopifyError("Inventory correction readback differs; no automatic replay.")
    proof.update(state="verified",checked_at=drafts.now().isoformat())
    data.update(stage="inventory_verified")
    data.pop("error",None)
    row.verification_state = "inventory_verified"
    drafts.save(row,data,"scoped_verified")
    return row
