"""Local operation receipts only: progress cannot execute/replay Shopify writes."""
import json
from app import db
from app.models import CatalogueOperation, ShopifyProductIdentity, Product
from app import shopify_drafts as drafts
from app.shopify_connection import ShopifyError
from itsdangerous import BadSignature
from flask import g, url_for
from sqlalchemy import func, case

STAGES = {
    "confirmation_validation":"Validating signed confirmation",
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
    "required_collection_create":"Registering required collection",
    "collection_membership":"Assigning collection memberships", "variant_create":"Adding reviewed variants",
    "original_image_uploads":"Original image uploads accepted", "independent_readback":"Verifying independent Shopify readback",
}


def review_data(product_id, token, *, max_age):
    try:
        data=drafts.serializer().loads(token,max_age=max_age)
    except BadSignature:
        raise ShopifyError("Review is invalid or expired. Inspect History; do not resubmit an uncertain write.") from None
    if not isinstance(data,dict) or data.get("product_id") != product_id:
        raise ShopifyError("Review belongs to another product.")
    return data


def accept(product_id,token,acknowledged):
    from app.utils.operation_control import acquire_catalogue_operation
    reviewed=review_data(product_id,token,max_age=900)
    if not acknowledged:
        raise ShopifyError("Explicit publishing acknowledgement is required; no write was accepted.")
    product=db.get_or_404(Product,product_id)
    g.shopify_review_ticket=drafts.digest(token)
    mode=reviewed.get("classification") or ("update" if reviewed.get("mode")=="update" else "create")
    lease=acquire_catalogue_operation("shopify_draft_publish",{"product_id":product_id,"sku":product.sku,
        "title":product.title,"mode":mode,"scope":reviewed.get("scope","full"),"stage":"confirmation_validation"})
    g.shopify_confirm_lease=lease
    return lease  # committed BEFORE source/API revalidation or any remote write


def finalise_unfinished(operation_id, error=None):
    """Keep failed validation/final-receipt failures visible; never replay writes."""
    from app.utils.operation_control import finish_catalogue_operation
    db.session.rollback()
    operation=db.session.get(CatalogueOperation,operation_id)
    if not operation or operation.status not in {"running","pending"}: return
    row=ShopifyProductIdentity.query.filter_by(operation_id=operation_id).first()
    data=drafts.evidence(row) if row else {}
    attempted=bool(data.get("create_attempted") or data.get("update_attempted") or data.get("inventory_update"))
    finish_catalogue_operation(operation_id,status="partial" if attempted else "failed",
        error=error or ("Publishing finalisation was interrupted. Inspect retained stages and reconcile read-only; no retry." if attempted else "Confirmation validation did not finish; no Shopify product write was attempted."),
        recovery_state="review_required" if attempted else "none")


def receipt(product_id, token):
    # Read access outlives execution approval; Confirm still expires at 15 min.
    review_data(product_id,token,max_age=86400)
    ticket = drafts.digest(token)
    safe_scope=case((func.json_valid(CatalogueOperation.scope),CatalogueOperation.scope),else_="{}")
    operations=CatalogueOperation.query.filter_by(operation_type="shopify_draft_publish").filter(func.json_extract(safe_scope,"$.review_ticket")==ticket).order_by(CatalogueOperation.started_at.desc()).limit(2).all()
    for operation in operations:
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
        elif operation.status == "interrupted": state = "recovery_required"
        else: state = "attention"
        stage=data.get("stage") or scope.get("stage","confirmation_validation")
        label=STAGES.get(stage,"Recorded operation: "+stage.replace("_"," "))
        if running and data.get("stage")=="verified": label="Base product verified; finishing required operation stages"
        stamp=drafts.now()
        return {"state":state,"operation_id":operation.id,"stage":label,"title":data.get("plan",{}).get("title") or scope.get("title",""),
            "sku":row.sku if row else scope.get("sku",""),"operation_type":scope.get("mode","publish"),"started_at":operation.started_at.isoformat()+"Z",
            "elapsed_seconds":max(0,int((stamp-operation.started_at).total_seconds())),"server_checked_at":stamp.isoformat()+"Z",
            "completed_stages":[STAGES.get(s,s.replace("_"," ")) for s in data.get("completed_steps",[])],
            "pending_stages":[] if state=="completed" else [label],
            "api_requests":data.get("api_requests"),
            "message":operation.error or data.get("error") or ("Inventory verified; full synchronisation remains outstanding." if row and row.state=="scoped_verified" else ""),
            "history_url":url_for("commerce.draft_identity",identity_id=row.id) if row else None}
    return {"state":"waiting","stage":"Waiting for durable operation receipt","message":"No completion is assumed. Do not resubmit while the request is pending."}
