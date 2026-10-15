"""Durable, bounded publishing events in the existing operation ledger.

SQLite compare-and-swap claims serialize delivery across workers. A crashed or
timed-out send is uncertain, not automatically retried. No browser GET sends.
"""
import json
import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app import db
from app.models import CatalogueOperation, Product, ShopifyProductIdentity
from app.utils.redaction import redact_diagnostic

KEY = "shopify_discord_events"
RETRYABLE = {"pending", "failed", "disabled", "uncertain"}


def scope_of(operation):
    try:
        value = json.loads(operation.scope or "{}")
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def now():
    return datetime.now(UTC).replace(tzinfo=None)


def read_only(operation):
    scope = scope_of(operation)
    return operation.operation_type == "shopify_status_refresh" or str(scope.get("mode", "")).startswith("read_only") or scope.get("mode") == "collection_mapping"


def complete(identity, data):
    if not identity or identity.state != "verified" or identity.verification_state != "verified":
        return False
    verification = data.get("verification", {})
    if any(verification.get(k) != "verified" for k in ("identity", "fields", "media", "memberships", "attributes")):
        return False
    if data.get("plan", {}).get("native", {}).get("enabled"):
        if data.get("kind") == "variable":
            proofs = data.get("variant_native", {})
            wanted = data["plan"].get("variants", [])
            return bool(wanted) and all(proofs.get(v["identity"], {}).get("state") == "verified" for v in wanted)
        return data.get("native_dimensions", {}).get("state") == "verified"
    return True


def product_result(operation):
    from app.shopify_drafts import evidence
    scope = scope_of(operation)
    summary = scope.get("operation_summary", {})
    identity = ShopifyProductIdentity.query.filter_by(operation_id=operation.id).first()
    data = evidence(identity) if identity else {}
    plan = data.get("plan", {})
    product = db.session.get(Product, identity.product_id or scope.get("product_id")) if identity else db.session.get(Product, scope["product_id"]) if scope.get("product_id") else None
    if not product and not scope.get("title") and not plan.get("title"):
        return None  # taxonomy-only operations are not product publishing
    if operation.recovery_state not in (None, "none") and identity and identity.state not in {"processing", "attribute_review_required", "scoped_verified"}:
        outcome = "recovery_required"
    elif operation.status == "succeeded" and complete(identity, data):
        outcome = "verified_success"
    elif operation.status in {"failed", "interrupted"} and not data.get("completed_steps") and (not identity or identity.state == "failed_before_create" or summary.get("write_attempted") is False):
        outcome = "failed_before_write"
    else:
        outcome = "partial_completion"
    result = {"title": product.title if product else scope.get("title") or plan.get("title"),
        "sku": identity.sku if identity else scope.get("sku") or (product.sku if product else "Unknown"),
        "operation": "Updated" if scope.get("mode") == "update" or summary.get("mode") == "update" or data.get("update_attempted") else "Created",
        "outcome": outcome, "stage": data.get("stage") or summary.get("stage"),
        "completed_at": operation.finished_at.isoformat() + "Z",
        "deferred": [str(v)[:300] for v in plan.get("blockers", [])[:5]],
        "reason": operation.error or data.get("error")}
    if plan.get("scope") == "inventory" or scope.get("scope") == "inventory":
        result["deferred"].append("Inventory-only scope: other managed fields remain unverified.")
    if scope.get('mode')=='publication':
        result['operation']='Publication updated'
        result['deferred'].append('Status/channel scope only; metadata verification is retained, not renewed.')
    if scope.get('mode') in {'sale_activate','sale_end'}:
        result['operation']='Sale Activated' if scope['mode']=='sale_activate' else 'Sale Ended'
        result['sale_status']=('active' if data.get('sale_transition',{}).get('phase')=='activate' else 'completed') if outcome=='verified_success' else summary.get('sale_status') or ('Recovery Required' if outcome=='recovery_required' else 'Needs Attention')
    if outcome == "verified_success":
        result["shopify_status"] = data.get("observed_status")  # independently read, never intended status
        result.pop("reason", None)  # original uncertainty stays in history, not current-success warnings
    if data.get("verification", {}).get("identity") == "verified":
        result["variants"] = len(data["variant_maps"]) if data.get("kind") == "variable" and data.get("variant_maps") else 1 if data.get("variant_id") else None
    if data.get("verification", {}).get("media") == "verified":
        result["images"] = len(data.get("media", []))
        result["image_action"] = "Synchronised/retained" if result["operation"] == "Updated" else "Synchronised"
    domain, gid = (identity.store_domain, identity.shopify_product_id) if identity else (None, None)
    if data.get("verification", {}).get("identity") == "verified" and re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", domain or "") and re.fullmatch(r"gid://shopify/Product/[0-9]+", gid or ""):
        result["url"] = f"https://{domain}/admin/products/{gid.rsplit('/', 1)[-1]}"
    # Only allowlisted operational fields, never payloads/credentials/customer data.
    return {k: redact_diagnostic(v, limit=500) if isinstance(v, str) else v for k, v in result.items() if v is not None}


def aggregate(products):
    """Future multi-product envelope; no bulk execution is introduced."""
    if not 1 <= len(products) <= 25:
        raise ValueError("Notification summaries support 1–25 products.")
    return {"products": products, "attempted": len(products), **{
        name: sum(p["outcome"] == name for p in products) for name in
        ("verified_success", "failed_before_write", "recovery_required", "partial_completion")}}


def enqueue(operation):
    """Caller commits this event with its authoritative terminal transition."""
    if operation.operation_type != "shopify_draft_publish" or read_only(operation):
        return None
    if scope_of(operation).get('mode') in {'sale_activate','sale_end'} and operation.status=='succeeded' and scope_of(operation).get('operation_summary',{}).get('write_attempted') is False:
        return None  # catch-up already matches: no invented "newly activated" event
    product = product_result(operation)
    if not product:
        return None
    scope = scope_of(operation)
    events = scope.setdefault(KEY, {})
    event = product["outcome"]
    if event in events:
        return None
    events[event] = {"version": 1, "event": event, "state": "pending", "attempts": 0,
        "summary": aggregate([product])}
    operation.scope = json.dumps(scope, sort_keys=True)
    return event


def deliver(operation_id, event, *, retry=False, acknowledge_uncertain=False):
    """One attempt, outside the publishing transaction. No ambiguous retries."""
    operation = db.session.get(CatalogueOperation, operation_id)
    if not operation:
        raise ValueError("Operation not found.")
    db.session.refresh(operation)
    before = operation.scope
    scope = scope_of(operation)
    notice = scope.get(KEY, {}).get(event)
    if not notice or operation.status in {"running", "pending"}:
        return False
    if notice["state"] == "sending":
        if now() - datetime.fromisoformat(notice["attempted_at"]) < timedelta(minutes=2):
            return False
        # The worker may have died after Discord accepted the request.
        notice.update(state="uncertain", result="Delivery worker stopped; acceptance is unknown.")
    if notice["state"] not in RETRYABLE or notice["state"] != "pending" and not retry:
        return False
    if notice["state"] == "uncertain" and not acknowledge_uncertain:
        raise ValueError("Acknowledge possible duplicate delivery before retrying an uncertain send.")
    claim = uuid.uuid4().hex
    notice.update(state="sending", claim=claim, attempted_at=now().isoformat(), attempts=notice["attempts"] + 1)
    encoded = json.dumps(scope, sort_keys=True)
    changed = db.session.execute(update(CatalogueOperation).where(CatalogueOperation.id == operation_id,
        CatalogueOperation.scope == before).values(scope=encoded), execution_options={"synchronize_session": False}).rowcount
    db.session.commit()  # durable claim BEFORE the external webhook; no DB lock held during delivery
    if changed != 1:
        return False
    from app.utils.discord import publishing_message
    try:
        ok, message = publishing_message(notice["summary"])
    except Exception:
        ok, message = False, "uncertain delivery"
    db.session.refresh(operation)
    final_before = operation.scope
    final = scope_of(operation)
    saved = final[KEY][event]
    if saved.get("claim") != claim:
        return False
    saved.update(state="sent" if ok else "disabled" if message in {"disabled", "not configured", "invalid webhook configuration"} else "uncertain" if "uncertain" in message else "failed",
        result=redact_diagnostic(message, limit=240))
    saved.pop("claim", None)
    db.session.execute(update(CatalogueOperation).where(CatalogueOperation.id == operation_id,
        CatalogueOperation.scope == final_before).values(scope=json.dumps(final, sort_keys=True)), execution_options={"synchronize_session": False})
    db.session.commit()
    return ok


def settle(identity):
    """A later complete readback can resolve an original asynchronous write.

    Reconciliation itself creates no event. The original publishing transition
    is retained and superseded only after every required domain is verified.
    """
    from app.shopify_drafts import evidence
    operation = db.session.get(CatalogueOperation, identity.operation_id)
    if not operation or operation.status in {"running", "pending", "succeeded"} or read_only(operation) or not complete(identity, evidence(identity)):
        return
    scope = scope_of(operation)
    # Pre-milestone history is not backfilled into unsolicited notifications.
    if KEY not in scope:
        return
    scope["resolved_previous_outcome"] = {"status": operation.status, "recovery_state": operation.recovery_state,
        "finished_at": operation.finished_at.isoformat()}
    operation.scope = json.dumps(scope)
    operation.status, operation.recovery_state = "succeeded", "none"
    operation.products_attempted = max(1, operation.products_attempted or 0)
    operation.products_succeeded, operation.products_failed = 1, 0
    operation.finished_at = now()
    event = enqueue(operation)
    db.session.commit()
    if event:
        try:
            deliver(operation.id, event)
        except Exception:
            from flask import current_app
            db.session.rollback()
            current_app.logger.warning("Discord resolved publishing delivery remains pending; verification retained")


def events_view(operation):
    events = scope_of(operation).get(KEY, {})
    result = []
    for event, notice in events.items():
        state = notice["state"]
        if state == "sending" and now() - datetime.fromisoformat(notice["attempted_at"]) >= timedelta(minutes=2):
            state = "uncertain"
        result.append({"event": event, "state": state, "result": notice.get("result", ""),
            "retryable": state in RETRYABLE, "attempts": notice["attempts"]})
    return result
