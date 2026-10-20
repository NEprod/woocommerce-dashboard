"""Local-only Shopify workspace evidence. Never authorizes a remote write."""
import hashlib
import json
import re
from types import SimpleNamespace

from sqlalchemy import or_

from app.models import ShopifyProductIdentity, ShopifyProductComparison, ShopifyCollectionIdentity
from app.taxonomy_assignments import product_document
from app.shopify_connection import ShopifyError


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
    from app.shopify_package_assignments import fingerprint as package_fingerprint
    # Reuse the publisher's field semantics without loading variations/taxonomy or
    # reading image bytes. Unsupported metadata cannot invent a publishable update.
    snapshot = SimpleNamespace(data={"authored": authored, "sale_price": product.sale_price,
        "shipping": shipping_details(authored, product), "tags": [tag.name for tag in product.tags]})
    base = {"title": authored.get("title") or product.title,
            "description": authored.get("description", product.description) or "",
            "price": fields.number(authored.get("price", product.regular_price), "price", money=True),
            "sku": product.sku}
    managed, _ = fields.contract(snapshot, base)
    from app.shopify_dimension_metadata import POLICY_VERSION
    data = {"dimension_metadata_policy": POLICY_VERSION, "managed": managed, "intent": authored.get("live"), "sku": product.sku,
            "package_assignment_policy":1,"package_assignments":package_fingerprint(product,authored),
            "source": product.source_relpath, "type": product.product_type,
            "images": [(i.url, i.position, i.alt_text) for i in product.images],
            "image_stats": stats}
    if product.product_type == "variable":
        from app.shopify_variables import source_parts
        # Status invalidation uses the effective publishing projection. The
        # signed Preview still binds the complete source document independently.
        parts = source_parts(product)
        # Variant source invalidation includes calculation inputs, not unrelated
        # proof/customisation/production notes. Actual publishing still validates
        # projection() against scanner results before allowing a write.
        parts["authored"] = {key: parts["authored"][key] for key in (
            "attributes", "variation_attributes", "weight", "length", "width", "height",
            "manage_stock", "backorders", "stock_quantity", "harmonized_system_code",
            "country_of_origin", "requires_shipping") if key in parts["authored"]}
        data["variable"] = parts
        from app.packaging_contract import effective as packing_effective
        from app.shopify_variables import packed_policy
        packaging_values = [packing_effective(authored,dict(v['attributes'])) for v in parts['variations']]
        if any(any(k in value for k in ('packed_dimensions','packed_dimensions_cm')) for value in packaging_values):
            data['variable_packed'] = [packed_policy(dict(zip(('length','width','height'),v['dimensions'])),value)
                for v,value in zip(parts['variations'],packaging_values)]
        for child,value in zip(parts['variations'],packaging_values):
            if 'weight_g' in value:
                from decimal import Decimal
                child['shipping_weight_g'] = str(Decimal(str(value['weight_g'])).normalize())
        from app.packaging_contract import finished_mm
        for child in parts['variations']:
            size=finished_mm(authored,dict(child['attributes']))
            if size: child['finished_mm']=size
        from app.sale_pricing import project, variant_sale
        data['managed'] = managed['product']
        data['inventory_policy']={key:value for key,value in managed['variant'].items() if key=='inventoryPolicy'}
        data['inventory_policy']['inventoryItem']={key:value for key,value in managed['variant']['inventoryItem'].items() if key in {'tracked','requiresShipping','countryCodeOfOrigin'}}
        for child in parts['variations']:
            from app.utils.json_utils import apply_variation_modifiers
            calculated=apply_variation_modifiers(authored,dict(child['attributes']),log=lambda *a,**k:None)
            child['pricing'] = project(dict(authored, sale_price=variant_sale(authored, dict(child['attributes']),child['sale'])),calculated.get('price',child['price']))
            child['pricing'].pop('state',None)
            for key in ('price','sale','dates'): child.pop(key,None)
    from app.shopify_publication import intent
    from app.storefront_customisations import public_configuration
    customisation = authored.get('customisation') or {}
    if 'storefront_enabled' in customisation:
        data['storefront_configuration'] = public_configuration(customisation)
    data['publication_intent'] = intent(authored, required=False)
    from app.shopify_native_dimensions import desired
    native_policy = desired(product)
    if native_policy["enabled"] or native_policy.get('desired'):
        data["native_policy"] = native_policy
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def states_for(products):
    products = list(products)
    if not products:
        return {}
    variable_ids = [p.id for p in products if p.product_type == "variable"]
    if variable_ids:
        from sqlalchemy.orm import selectinload
        from app.models import Product, Variation
        Product.query.filter(Product.id.in_(variable_ids)).options(
            selectinload(Product.variations).selectinload(Variation.attributes),
            selectinload(Product.variations).selectinload(Variation.images)).all()
    rows = ShopifyProductIdentity.query.filter(or_(
        ShopifyProductIdentity.product_id.in_([p.id for p in products]),
        ShopifyProductIdentity.source_identity.in_([p.source_relpath for p in products if p.source_relpath]))).all()
    comparisons = ShopifyProductComparison.query.filter(ShopifyProductComparison.source_identity.in_([p.source_relpath for p in products if p.source_relpath])).all()
    mappings = {(r.store_id, r.source_key): r for r in ShopifyCollectionIdentity.query.all()}
    from app.models import ShopifyAttributeIdentity
    mappings.update({(r.store_id,r.source_key):r for r in ShopifyAttributeIdentity.query.all()})
    return {p.id: product_state(p, [r for r in rows if r.product_id == p.id or r.source_identity == p.source_relpath],
                [r for r in comparisons if r.source_identity == p.source_relpath], mappings)
            for p in products}


def product_state(product, rows, comparisons=(), mappings=None):
    from flask import url_for
    from app.shopify_drafts import evidence
    native_comparison = None
    state = {"product_id": product.id, "state": "unknown", "label": "Not yet verified", "remote_id": None,
             "remote_status": "Unknown", "publication": "Not verified", "verified_at": None,
             "identity_url": None, "store_id": None, "shopify_url": None,
             "review_url": url_for("commerce.drafts", product_id=product.id) if product.product_type in {"simple","variable"} and product.catalogue_status == "active" else None,
             "message": "No retained identity. Remote duplicate checks are still required before Create.",
             "intent": "Unresolved", "intent_value": None, "compared_at": None,
             "change_count": None, "change_summary": [], "blockers": [], "revision_checked_at": None}
    try:
        live = product_document(product).get("live")
        from app.shopify_publication import intent
        try: desired_publication=intent(product_document(product))
        except ShopifyError: desired_publication={'status':None,'channels':{}}
        state["intent_value"] = live if isinstance(live, bool) else None
        state['intent']=desired_publication['status'].title()+' intended' if desired_publication['status'] else 'Unresolved · edit metadata'
        if len(rows) > 1:
            state.update(state="recovery_required", label="Recovery Required",
                         message="Multiple retained store identities require administrator review; automatic migration is not supported.")
            return state
        if rows:
            row = rows[0]
            data = evidence(row)
            if row.state == "remote_deleted" and row.verification_state == "remote_absent" and not row.shopify_product_id:
                state.update(state="create", label="Needs Creating", identity_url=url_for("commerce.draft_identity", identity_id=row.id),
                    message="Former exact Shopify identity independently verified absent. History retained; fresh duplicate checks and signed Create are required.",
                    remote_status="Deleted (verified absent)", native={"state":"disabled", "message":"Former inventory/media bindings are historical only."})
                return state
            domain = data.get("plan", {}).get("domain", "")
            if (re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", domain)
                    and re.fullmatch(r"gid://shopify/Product/[0-9]+", row.shopify_product_id or "")):
                state["shopify_url"] = f"https://{domain}/admin/products/{row.shopify_product_id.rsplit('/', 1)[-1]}"
            state.update(remote_id=row.shopify_product_id, store_id=row.store_id, identity_url=url_for("commerce.draft_identity", identity_id=row.id),
                         verified_at=row.verified_at.isoformat() + "Z" if row.verified_at else None,
                         remote_status=(data.get('observed_status') or 'Draft').title()+" (last verified)" if row.verification_state == "verified" else "Not currently verified")
            observed_publication=data.get('publication_update',{}).get('observed')
            if observed_publication:
                state['channel_evidence']=observed_publication
                state['publication']='Channel-specific cached evidence; inspect Review & Sync'
            state["can_delete"] = row.state == "verified" and row.verification_state == "verified" and not any(p.get("state") in {"pending","uncertain"} for p in data.get("variant_native",{}).values()) and data.get("native_dimensions",{}).get("state") not in {"pending","uncertain"}
            if any(p.get('state') in {'pending','uncertain'} for p in data.get('package_assignments',{}).values()):
                state.update(state='recovery_required',label='Recovery Required',can_delete=False,
                    message='Saved-package assignment outcome is uncertain. No public assignment readback exists; inspect Shopify Admin before any retry.')
                return state
            if row.state == "processing":
                state.update(state="processing", label="Processing", message="Read-only reconciliation is required to check media processing.")
            elif row.state=="attribute_review_required":
                state.update(state="attention",label="Needs Attention",message="Base metadata/media/memberships are verified; remaining informational stages need a fresh signed Review. No automatic write replay.")
            elif row.state == "scoped_verified":
                state.update(state="attention",label="Needs Attention",message="Inventory-only correction verified. Full metadata, attributes and publication comparison remains outstanding.")
            elif row.state == "verified" and row.verification_state == "verified":
                plan = data["plan"]
                baseline = plan.get("workspace_source_fingerprint")
                if baseline and baseline == source_fingerprint(product, plan["images"]):
                    state.update(state="no_change", label="No Change", message="Last verified managed state; local metadata/image stats match. Remote freshness requires explicit Review or reconciliation.")
                elif baseline:
                    state.update(state="local_changes", label="Checking Changes", message="Effective local evidence changed; read-only comparison is pending, not a confirmed Shopify update.")
                else:
                    state.update(state="unavailable", label="Verification Unavailable", message="Retained draft is verified historically; build Review Changes for a current local/remote comparison.")
            elif row.state == "failed_before_create":
                state.update(state="attention", label="Needs Attention", message=data.get("error") or "Previous attempt failed before product creation. Review again; duplicate checks remain mandatory.")
            else:
                state.update(state="recovery_required", label="Recovery Required", message=data.get("error") or "Unresolved attempt: reconcile the retained identity before another write.")
        if product.product_type not in {"simple","variable"} or product.catalogue_status != "active":
            state["review_url"] = None
            state["message"] += " Only active Simple/Variable parents can be reviewed."
        from app.shopify_publication import proof_matches
        publication_pending=(desired_publication['status']!='DRAFT' or bool(desired_publication['channels'])) and not (rows and proof_matches(product,evidence(rows[0])))
        if publication_pending:
            state["message"] += " Publication intent must be included in a signed Review & Sync."
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
                stale = False
                if not data:
                    cached = current(product, comparison, identity, mappings or {}, freshness=False)
                    if cached and cached.get("read_complete") is True and cached.get("scope","full") == "full" and cached.get("classification") != "create":
                        data, stale = cached, True
                if data:
                    native_comparison = data.get("native")
                    classification = data["classification"]
                    state.update(state="create" if classification == "create" else "update" if classification == "update" else "no_change",
                        label="Needs Creating" if classification == "create" else "Update Available" if classification == "update" else "Up to Date",
                        compared_at=(data.get("full_compared_at") or comparison.compared_at.isoformat()) + "Z", change_count=data["change_count"],
                        change_summary=data.get("display_changes", []), blockers=data["blockers"],
                        remote_status=data["actual_status"].title() + " (last compared)",
                        revision_checked_at=data["revision_checked_at"] + "Z" if data.get("revision_checked_at") else None,
                        message="Cached read-only comparison; this is not post-write verification.")
                    if stale:
                        state.update(evidence_stale=True,
                            label="Last check matched" if classification == "no_change" else "Update Available (last check)",
                            message="Verification is due. Saved comparison only; the background Scheduled Task refreshes it independently. Manual Refresh remains available.")
                    if classification == "no_change" and (data["blockers"] or (data.get('publication') or {}).get('pending') or publication_pending and not data.get('publication')):
                        state.update(state="attention", label="Needs Attention", message="Metadata matches, but publication or taxonomy blockers remain.")
                    if data.get('package_assignments_unverified'):
                        state['package_assignment_notice'] = 'Optional package: ID/acceptance or manual action may be needed; accepted assignments are not independently verified. Inspect Optional Package in Review & Sync.'
                        state['message'] += ' ' + state['package_assignment_notice']
                    if data.get("refresh_failure"):
                        state.update(state="unavailable", label="Verification Unavailable", change_count=None,
                            change_summary=[], blockers=[data["refresh_failure"]["message"]],
                            message=data['refresh_failure']['message'])
                        kind=data["refresh_failure"].get("kind")
                        if kind == 'readiness':
                            state.update(state='attention', label='Publishing prerequisite needed')
                        if kind in {"identity_conflict","variant_missing"}:
                            state.update(state="recovery_required" if kind=="identity_conflict" else "attention",label="Recovery Required" if kind=="identity_conflict" else "Needs Attention",message=data["refresh_failure"]["message"])
                    if data.get("conflicting_changes"):
                        state["message"] += " Both local and Shopify data changed since the checkpoint; inspect the reviewed before/after values before overwriting."
                    break
            else:
                if identity:
                    from app.shopify_comparison import fingerprint
                    from app.shopify_drafts import digest, configuration
                    for comparison in comparisons:
                        known = json.loads(comparison.summary_json)
                        if (comparison.store_id == identity.store_id and known.get("config") == configuration().fingerprint()
                                and known.get("local") == fingerprint(product, known.get("images", []))):
                            required_keys = set(known.get("mappings", {})) | set(known.get("attribute_mappings", {}))
                            if any(mappings.get((identity.store_id, key)) and mappings[(identity.store_id, key)].state in {"pending", "uncertain"} for key in required_keys):
                                state.update(state="recovery_required", label="Recovery Required", message="A required retained collection/attribute mapping has unresolved write evidence. Reconcile that identity before publishing; no automatic retry.")
                                break
                        if (comparison.store_id == identity.store_id and known.get("identity") == digest(identity.media_evidence_json)
                                and known.get("config") == configuration().fingerprint()
                                and known.get("local") != fingerprint(product, known["images"])):
                            state.update(state="local_changes", label="Checking Changes", message="Effective managed data changed; bounded comparison is queued. Historical differences are not current differences.",
                                last_known_at=known.get('full_compared_at'),last_known_classification=known.get('classification'),
                                last_known_change_count=known.get('change_count'))
                            break
                if state["state"] == "no_change":
                    # Last publish fingerprints predate managed taxonomy and cannot prove it.
                    state.update(state="local_changes", label="Checking Changes", message="Comparison checkpoint is stale or predates the current managed projection. A bounded read-only check is pending; historical verification is retained.")
                from app.shopify_drafts import configuration
                for comparison in comparisons:
                    if state["state"] == "recovery_required":
                        break
                    failed = json.loads(comparison.summary_json)
                    from app.shopify_comparison import recent, REVISION_AGE
                    if failed.get("config") == configuration().fingerprint() and recent(failed.get("observed_revision_at"), REVISION_AGE):
                        state.update(remote_status=failed["observed_status"].title() + " (revision checked)", revision_checked_at=failed["observed_revision_at"] + "Z")
                    if failed.get("refresh_failure") and failed.get("config") == configuration().fingerprint():
                        state.update(state="unavailable", label="Verification Unavailable", message=failed["refresh_failure"]["message"])
                        kind=failed["refresh_failure"].get("kind")
                        if kind == 'readiness':
                            state.update(state='attention', label='Publishing prerequisite needed')
                        if kind in {"identity_conflict","variant_missing"}:
                            state.update(state="recovery_required" if kind=="identity_conflict" else "attention",label="Recovery Required" if kind=="identity_conflict" else "Needs Attention")
                        break
    except (ValueError, OSError, KeyError, TypeError, AttributeError, RuntimeError, ShopifyError):
        state.update(state="unavailable", label="Verification Unavailable", message="Local source or retained evidence needs review; no current equality is claimed.")
    from app.shopify_native_dimensions import local_status
    try:
        if product.product_type == "variable":
            from app.shopify_variables import native_status
            local_status = native_status
        native = local_status(product, rows[0] if len(rows) == 1 else None, native_comparison)
        state["native"] = native
        if native["state"] == "uncertain":
            state.update(state="recovery_required", label="Recovery Required", message=native["message"])
        elif native['state'] == 'stale' and state['state'] == 'no_change':
            state.update(evidence_stale=True, label='Last check matched', message=native['message'])
        elif native["state"] not in {"disabled", "verified"} and state["state"] == "no_change":
            state.update(state="update" if native["state"] == "update" else "attention", label="Update Available" if native["state"] == "update" else "Needs Attention", message="Metadata evidence matches; " + native["message"])
    except ShopifyError as exc:
        state.update(state="attention", label="Needs Attention", message=str(exc))
    return state
