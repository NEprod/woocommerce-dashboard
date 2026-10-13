"""Local operation receipts only: progress cannot execute/replay Shopify writes."""
import json
from app.models import CatalogueOperation, ShopifyProductIdentity
from app import shopify_drafts as drafts
from app.shopify_connection import ShopifyError
from itsdangerous import BadSignature

STAGES = {
    "pending":"Preparing reviewed operation", "update_pending":"Preparing reviewed update",
    "reviewed_update":"Preparing retained product update", "staged_uploads":"Uploading original images",
    "product_create":"Creating Shopify draft", "product_update":"Updating product information",
    "variant_update":"Updating variants", "variants_update":"Updating variants", "variants_create":"Creating reviewed variant additions",
    "attribute_registration":"Registering required attributes / terms", "attribute_assignment":"Assigning product attributes",
    "collection_registration":"Registering required collections", "collection_memberships":"Assigning collection memberships",
    "media_alt":"Updating image alt text", "media_order":"Updating image order",
    "managed_field_verification":"Verifying product fields", "collection_verification":"Verifying memberships",
    "media_verification":"Verifying image identities", "media_processing":"Media processing",
    "media_reordering":"Media order processing", "inventory_update":"Updating reviewed inventory policy",
    "inventory_verified":"Inventory verified; other fields deferred", "verified":"Verified",
}


def receipt(product_id, token):
    try:
        reviewed = drafts.serializer().loads(token,max_age=900)
    except BadSignature:
        raise ShopifyError("Progress review expired; inspect Identity & History. Do not resubmit the write.") from None
    if reviewed.get("product_id") != product_id:
        raise ShopifyError("Progress review belongs to another product.")
    ticket = drafts.digest(token)
    for operation in CatalogueOperation.query.filter_by(operation_type="shopify_draft_publish").order_by(CatalogueOperation.started_at.desc()).limit(100).all():
        scope = json.loads(operation.scope)
        if scope.get("review_ticket") != ticket:
            continue
        row = ShopifyProductIdentity.query.filter_by(operation_id=operation.id).first()
        data = drafts.evidence(row) if row else {}
        running = operation.status in {"running","pending"}
        native = data.get("native_dimensions",{}).get("state")
        if running: state = "running"
        elif row and row.state == "verified" and row.verification_state == "verified" and operation.status == "succeeded" and native not in {"pending","uncertain","blocked"}: state = "completed"
        elif row and row.state == "processing": state = "processing"
        elif row and row.state in {"uncertain","deleting","delete_uncertain","creating","updating"}: state = "recovery_required"
        elif operation.status == "failed": state = "failed"
        else: state = "attention"
        from flask import url_for
        label=STAGES.get(data.get("stage"),"Recorded operation: "+data.get("stage","validating review").replace("_"," "))
        if running and data.get("stage")=="verified": label="Base product verified; finishing required operation stages"
        return {"state":state,"operation_id":operation.id,"stage":label,
            "message":operation.error or data.get("error") or ("Inventory verified; full synchronisation remains outstanding." if row and row.state=="scoped_verified" else ""),
            "history_url":url_for("commerce.draft_identity",identity_id=row.id) if row else None}
    return {"state":"waiting","stage":"Waiting for durable operation receipt","message":"No completion is assumed. Do not resubmit while the request is pending."}
