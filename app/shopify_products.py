"""Local-only Shopify workspace evidence. Never authorizes a remote write."""
import hashlib
import json
import re
from types import SimpleNamespace

from sqlalchemy import or_

from app.models import ShopifyProductIdentity, ShopifyProductComparison, ShopifyCollectionIdentity
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
    comparisons = ShopifyProductComparison.query.filter(ShopifyProductComparison.source_identity.in_([p.source_relpath for p in products if p.source_relpath])).all()
    mappings = {(r.store_id, r.source_key): r for r in ShopifyCollectionIdentity.query.all()}
    return {p.id: product_state(p, [r for r in rows if r.product_id == p.id or r.source_identity == p.source_relpath],
                [r for r in comparisons if r.source_identity == p.source_relpath], mappings)
            for p in products}


def product_state(product, rows, comparisons=(), mappings=None):
    from flask import url_for
    from app.shopify_drafts import evidence
    state = {"product_id": product.id, "state": "unknown", "label": "Not yet verified", "remote_id": None,
             "remote_status": "Unknown", "publication": "Not verified", "verified_at": None,
             "identity_url": None, "store_id": None, "shopify_url": None,
             "review_url": url_for("commerce.drafts", product_id=product.id) if product.product_type == "simple" and product.catalogue_status == "active" else None,
             "message": "No retained identity. Remote duplicate checks are still required before Create.",
             "intent": "Unresolved", "intent_value": None, "compared_at": None,
             "change_count": None, "change_summary": [], "blockers": [], "revision_checked_at": None}
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
            domain = data.get("plan", {}).get("domain", "")
            if (re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", domain)
                    and re.fullmatch(r"gid://shopify/Product/[0-9]+", row.shopify_product_id or "")):
                state["shopify_url"] = f"https://{domain}/admin/products/{row.shopify_product_id.rsplit('/', 1)[-1]}"
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
                    state.update(state="local_changes", label="Local Changes Detected", message="Local source changed; a current comparison is pending, not yet a remotely confirmed update.")
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
            if state["state"] == "no_change":
                state.update(state="attention", label="Needs Attention",
                    message="Managed metadata matches cached verification, but intended publication is unresolved. " + state["message"])
        if state["state"] not in {"recovery_required", "processing"}:
            from app.shopify_comparison import current
            identity = rows[0] if rows else None
            for comparison in sorted(comparisons, key=lambda r: r.compared_at, reverse=True):
                if identity and comparison.store_id != identity.store_id:
                    continue
                data = current(product, comparison, identity, mappings or {})
                if data:
                    classification = data["classification"]
                    state.update(state="create" if classification == "create" else "update" if classification == "update" else "no_change",
                        label="Needs Creating" if classification == "create" else "Update Available" if classification == "update" else "Up to Date",
                        compared_at=comparison.compared_at.isoformat() + "Z", change_count=data["change_count"],
                        change_summary=data.get("display_changes", []), blockers=data["blockers"],
                        remote_status=data["actual_status"].title() + " (last compared)",
                        revision_checked_at=data["revision_checked_at"] + "Z" if data.get("revision_checked_at") else None,
                        message="Cached read-only comparison; this is not post-write verification.")
                    if classification == "no_change" and (data["blockers"] or live is not False):
                        state.update(state="attention", label="Needs Attention", message="Metadata matches, but publication or taxonomy blockers remain.")
                    if data.get("refresh_failure"):
                        state.update(state="unavailable", label="Verification Unavailable", change_count=None,
                            change_summary=[], blockers=[data["refresh_failure"]["message"]],
                            message="Last refresh failed; retained publication verification and last comparison are historical. Review again before writing.")
                    if data.get("conflicting_changes"):
                        state["message"] += " Both local and Shopify data changed since the checkpoint; inspect the reviewed before/after values before overwriting."
                    break
            else:
                if identity:
                    from app.shopify_comparison import fingerprint
                    from app.shopify_drafts import digest, configuration
                    for comparison in comparisons:
                        known = json.loads(comparison.summary_json)
                        if (comparison.store_id == identity.store_id and known.get("identity") == digest(identity.media_evidence_json)
                                and known.get("config") == configuration().fingerprint()
                                and known.get("local") != fingerprint(product, known["images"])):
                            state.update(state="local_changes", label="Local Changes Detected", message="Local metadata or taxonomy changed; pending remote comparison. The old change count is invalid.")
                            break
                if state["state"] == "no_change":
                    # Last publish fingerprints predate managed taxonomy and cannot prove it.
                    state.update(state="unavailable", label="Verification Unavailable", message="Fresh managed comparison required; retained publishing evidence remains available.")
                from app.shopify_drafts import configuration
                for comparison in comparisons:
                    failed = json.loads(comparison.summary_json)
                    from app.shopify_comparison import recent, REVISION_AGE
                    if failed.get("config") == configuration().fingerprint() and recent(failed.get("observed_revision_at"), REVISION_AGE):
                        state.update(remote_status=failed["observed_status"].title() + " (revision checked)", revision_checked_at=failed["observed_revision_at"] + "Z")
                    if failed.get("refresh_failure") and failed.get("config") == configuration().fingerprint():
                        state.update(state="unavailable", label="Verification Unavailable", message=failed["refresh_failure"]["message"])
                        break
    except (ValueError, OSError, KeyError, TypeError, AttributeError, RuntimeError):
        state.update(state="unavailable", label="Verification Unavailable", message="Local source or retained evidence needs review; no current equality is claimed.")
    return state
