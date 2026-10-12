"""Local-only Shopify workspace evidence. Never authorizes a remote write."""
import hashlib
import json
from types import SimpleNamespace

from sqlalchemy import or_

from app.models import ShopifyProductIdentity
from app.taxonomy_assignments import product_document


def source_fingerprint(product, images):
    """Cheap metadata/stat comparison, not a substitute for Preview byte hashing."""
    from app.catalogue_images import _source_context
    context = _source_context(product)
    stats = []
    for image in images:
        if not context:
            raise ValueError("Catalogue source is unavailable")
        root, folder, _ = context
        path = (root / image["source"]).resolve(strict=True)
        path.relative_to(root)
        path.relative_to(folder)
        info = path.stat()
        stats.append([image["source"], info.st_size, info.st_mtime_ns, info.st_ctime_ns])
    from app.catalogue_fields import shipping_details
    from app import shopify_simple_fields as fields
    authored = product_document(product)
    # Reuse the publisher's field semantics without loading variations/taxonomy or
    # reading image bytes. Unsupported metadata cannot invent a publishable update.
    snapshot = SimpleNamespace(data={"authored": authored, "sale_price": product.sale_price,
        "shipping": shipping_details(authored, product), "tags": [tag.name for tag in product.tags]})
    base = {"title": authored.get("title") or product.title,
            "description": authored.get("description", product.description) or "",
            "price": fields.number(authored.get("price", product.regular_price), "price", money=True),
            "sku": product.sku}
    managed, _ = fields.contract(snapshot, base)
    data = {"managed": managed, "intent": authored.get("live"), "sku": product.sku,
            "source": product.source_relpath, "type": product.product_type,
            "images": [(i.url, i.position, i.alt_text) for i in product.images],
            "image_stats": stats}
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def states_for(products):
    products = list(products)
    if not products:
        return {}
    rows = ShopifyProductIdentity.query.filter(or_(
        ShopifyProductIdentity.product_id.in_([p.id for p in products]),
        ShopifyProductIdentity.source_identity.in_([p.source_relpath for p in products if p.source_relpath]))).all()
    return {p.id: product_state(p, [r for r in rows if r.product_id == p.id or r.source_identity == p.source_relpath])
            for p in products}


def product_state(product, rows):
    from flask import url_for
    from app.shopify_drafts import evidence
    state = {"state": "unknown", "label": "Not yet verified", "remote_id": None,
             "remote_status": "Unknown", "publication": "Not verified", "verified_at": None,
             "identity_url": None, "store_id": None,
             "review_url": url_for("commerce.drafts", product_id=product.id) if product.product_type == "simple" and product.catalogue_status == "active" else None,
             "message": "No retained identity. Remote duplicate checks are still required before Create.",
             "intent": "Unresolved", "intent_value": None}
    try:
        live = product_document(product).get("live")
        state["intent_value"] = live if isinstance(live, bool) else None
        state["intent"] = "Live intended · channel publication deferred" if live is True else "Draft intended" if live is False else "Unresolved · edit metadata"
        if len(rows) > 1:
            state.update(state="recovery_required", label="Recovery Required",
                         message="Multiple retained store identities require administrator review; automatic migration is not supported.")
            return state
        if rows:
            row = rows[0]
            data = evidence(row)
            state.update(remote_id=row.shopify_product_id, store_id=row.store_id, identity_url=url_for("commerce.draft_identity", identity_id=row.id),
                         verified_at=row.verified_at.isoformat() + "Z" if row.verified_at else None,
                         remote_status="Draft (last verified)" if row.verification_state == "verified" else "Not currently verified")
            if row.state == "processing":
                state.update(state="processing", label="Processing", message="Read-only reconciliation is required to check media processing.")
            elif row.state == "verified" and row.verification_state == "verified":
                plan = data["plan"]
                baseline = plan.get("workspace_source_fingerprint")
                if baseline and baseline == source_fingerprint(product, plan["images"]):
                    state.update(state="no_change", label="No Change", message="Last verified managed state; local metadata/image stats match. Remote freshness requires explicit Review or reconciliation.")
                elif baseline:
                    state.update(state="update", label="Update Available", message="Local source changed since the verified plan. Review determines supported writes.")
                else:
                    state.update(state="unavailable", label="Verification Unavailable", message="Retained draft is verified historically; build Review Changes for a current local/remote comparison.")
            elif row.state == "failed_before_create":
                state.update(state="attention", label="Needs Attention", message=data.get("error") or "Previous attempt failed before product creation. Review again; duplicate checks remain mandatory.")
            else:
                state.update(state="recovery_required", label="Recovery Required", message=data.get("error") or "Unresolved attempt: reconcile the retained identity before another write.")
        if product.product_type != "simple" or product.catalogue_status != "active":
            state["review_url"] = None
            state["message"] += " Only active Simple products can be reviewed in this milestone."
        if live is not False:
            state["message"] += " Publishing requires explicit Draft intent; Live transitions are deferred."
    except (ValueError, OSError, KeyError, TypeError, AttributeError, RuntimeError):
        state.update(state="unavailable", label="Verification Unavailable", message="Local source or retained evidence needs review; no current equality is claimed.")
    return state
