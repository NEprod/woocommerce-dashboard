"""Bounded read-comparison evidence, independent of publishing verification."""
import json
from datetime import timedelta

from app import db
from app.models import ShopifyProductComparison

MAX_AGE = timedelta(minutes=10)
REVISION_AGE = timedelta(minutes=10)
FULL_AGE = MAX_AGE
ABSENCE_AGE = MAX_AGE


def native_required(native):
    """An opt-in flag alone does not author a managed measurement target."""
    return bool(native.get('enabled') and (any(not v.get('unconfigured') for v in native['variants'].values())
        if 'variants' in native else not native.get('unconfigured')))


def missing_evidence(plan):
    """Safe component diagnostics, not desired values or fabricated read receipts."""
    from app.utils.operation_control import sanitize_operation_error
    result = []
    def add(component, reason):
        result.append({'component': component, 'reason': sanitize_operation_error(reason)[:350]})
    publication = plan.get('publication') or {}
    if publication and publication.get('read_complete') is not True:
        add('publication', publication.get('reason') or 'Status/channel membership readback is incomplete.')
        if publication.get('configuration_error') == 'publication_configuration':
            result[-1]['kind'] = 'authored_configuration'
    if plan.get('scope') != 'inventory':
        for diagnostic in plan.get('attribute_diagnostics', []):
            add('informational_attributes', diagnostic.get('action') or 'Required attribute readback is incomplete.')
    native = plan.get('native') or {}
    if native_required(native) and native.get('classification') == 'blocked':
        add('native_packed_dimensions', native.get('reason') or 'Native InventoryItem measurement readback is incomplete.')
    # One shared failure can affect many attributes/variants; retain it once.
    return list({(r['component'], r['reason']): r for r in result}.values())[:20]


def recent(value, age):
    from datetime import datetime
    from app.shopify_drafts import now
    try:
        return timedelta(0) <= now() - datetime.fromisoformat(value) <= age
    except (ValueError, TypeError):
        return False


def store_summary(row, data):
    from app.shopify_capacity import guard_owner
    guard_owner()
    encoded = json.dumps(data, sort_keys=True)
    if len(encoded.encode()) > 32768:
        from app.shopify_connection import ShopifyError
        raise ShopifyError("Comparison exceeds its evidence bound.")
    from app.shopify_capacity import fence_owner
    fence_owner()
    if row.id is not None:
        from sqlalchemy import update
        original = row.summary_json
        updated = db.session.execute(update(ShopifyProductComparison).where(
            ShopifyProductComparison.id == row.id, ShopifyProductComparison.summary_json == original).values(
                summary_json=encoded), execution_options={'synchronize_session':False}).rowcount
        if not updated:
            db.session.rollback()
            return False  # a newer comparison/transition won; never overwrite it
        db.session.expire(row)
    else:
        row.summary_json = encoded
        db.session.add(row)
    db.session.commit()


def mapping_digest(row):
    from app.shopify_drafts import digest
    return digest({"remote": row.remote_id, "state": row.state, "standard": row.standard_category_id,
        "evidence": row.evidence_json}) if row else None


def attribute_mapping_digest(row):
    from app.shopify_drafts import digest
    return digest({"remote":row.remote_id,"state":row.state,"evidence":row.evidence_json}) if row else None


def fingerprint(product, images):
    from app.shopify_products import source_fingerprint
    from app.shopify_taxonomy import fingerprint as taxonomy_fingerprint
    from app.shopify_drafts import digest
    return digest({"local": source_fingerprint(product, images), "taxonomy": taxonomy_fingerprint(product)})


def identity_revision(identity):
    from app.shopify_drafts import digest
    return digest([identity.media_evidence_json, identity.state, identity.verification_state,
        identity.operation_id, identity.shopify_product_id]) if identity else None


def record(product, plan, identity=None, *, post_write=False):
    from app.shopify_capacity import guard_owner
    guard_owner()
    from app.shopify_drafts import now, digest
    from sqlalchemy import select, update
    from app.models import ShopifyProductIdentity
    if not post_write and identity and plan.get('read_identity_digest'):
        # A read started against an earlier operation/identity cannot attest the
        # newer one (or erase its recovery state), even if prices match again.
        actual = db.session.execute(select(ShopifyProductIdentity.media_evidence_json, ShopifyProductIdentity.state,
            ShopifyProductIdentity.verification_state, ShopifyProductIdentity.operation_id, ShopifyProductIdentity.shopify_product_id).where(
            ShopifyProductIdentity.id == identity.id)).one()
        if digest(list(actual)) != plan['read_identity_digest']:
            return False
    from app.shopify_attributes import CAPABILITY_POLICY_VERSION
    if plan.get("attributes") and plan.get("attribute_capability_policy") != CAPABILITY_POLICY_VERSION:
        return False  # reconciling an old signed plan proves only its historical scope
    if not plan.get("taxonomy_fingerprint") or not plan.get("workspace_source_fingerprint"):
        return False  # legacy publishing verification did not cover the new taxonomy contract
    expected_local = digest({"local": plan["workspace_source_fingerprint"], "taxonomy": plan["taxonomy_fingerprint"]})
    if fingerprint(product, plan["images"]) != expected_local:
        # A source edit during remote reads cannot borrow equality from the older plan.
        # Verified remote write evidence still stands; a fresh Review is required locally.
        return False
    row = ShopifyProductComparison.query.filter_by(store_id=plan["store_id"], source_identity=product.source_relpath).populate_existing().first()
    original = row.summary_json if row else None
    if not post_write and row and plan.get('comparison_started_at') and row.compared_at <= now() and row.compared_at.isoformat() > plan['comparison_started_at']:
        return False
    if row is None:
        row = ShopifyProductComparison(store_id=plan["store_id"], source_identity=product.source_relpath)
    def summary_value(value):
        raw = json.dumps(value,sort_keys=True,ensure_ascii=False)
        # Comparison is an index, not write authority. Full values remain in the
        # signed/hash-bound plan and lossless durable publishing evidence.
        return {"sha256":digest(value),"bytes":len(raw.encode()),"detail":"See full reviewed value"} if len(raw.encode()) > 512 else value
    scoped = plan.get("scope") == "inventory"
    prior = json.loads(row.summary_json) if row.summary_json else {}
    # A diagnostic for a failed required attribute read is not successful
    # comparison evidence. Keep its previous success checkpoint, if any.
    failures = missing_evidence(plan)
    read_failed = bool(failures)
    native = plan.get('native') or {}
    pending_native = native.get('pending_capability', False) or bool(not native.get('enabled') and (native.get('desired') or
        any(v.get('desired') for v in native.get('variants', {}).values())))
    # Package assignment is write-only. Its acknowledgement/warnings remain in
    # the separate receipt/group; it cannot supply OR invalidate readable fields.
    # Pending/uncertain writes are still fenced by the identity/recovery guards.
    incomplete = scoped or read_failed or pending_native
    changes = [{"field": r["field"], "after": summary_value(r["after"])} for r in plan["fields"] if r["changed"] and not r.get("deferred")]
    changes += [{"field": "collections." + r["key"], "after": r["name"]} for r in plan.get("collections", []) if r["action"] in {"Join","Create & Join"}]
    summary = {"version": 1, "product_id": product.id, "source": product.source_relpath,
        "store_id": plan["store_id"], "remote_id": plan.get("remote_id"),
        "local": expected_local, "images": [{"source": i["source"]} for i in plan["images"]],
        "remote_digest": plan.get("remote_digest"), "classification": plan["classification"],
        "changes": changes[:100], "change_count": len(changes), "scope":plan.get("scope","full"),
        "blockers": plan.get("blockers", []) + (plan.get('publication') or {}).get('blockers',[]) +
            ([native.get('reason') or 'Packed dimensions await verified Shopify capability/permission; supported metadata can complete independently.'] if pending_native else []) +
            (["Inventory-only comparison: metadata, attributes, collections and publication are not fully compared."] if scoped else []),
        "identity": digest(identity.media_evidence_json) if identity else None,
        "mappings": {r["key"]: r.get("mapping_fingerprint") for r in plan.get("collections", [])},
        "actual_status": plan.get("actual_status", "Not yet created")}
    summary["config"] = plan.get("config_digest")
    from app.shopify_attributes import resource
    summary["attribute_mappings"]={r["source"]:attribute_mapping_digest(resource(plan["store_id"],r["source"]))
        for a in plan.get("attributes",[]) for r in a["resources"]}
    summary["attribute_capability_policy"] = plan.get("attribute_capability_policy")
    summary["native"] = plan.get("native")
    summary['dimension_metadata_policy'] = (plan.get('dimension_metadata') or {}).get('policy')
    summary['local_package_changes'] = plan.get('local_package_changes', 0)
    summary['package_assignments_unverified']=plan.get('package_assignments_unverified',False)
    summary['package_readback_policy'] = 2
    summary['missing_evidence'] = failures
    summary['publication'] = plan.get('publication')
    summary["conflicting_changes"] = bool(plan.get("conflicting_changes")) and plan["classification"] == "update"
    stamp = now().isoformat()
    summary.update(sku=product.sku, remote_updated_at=plan.get("remote_updated_at"),
        tags_managed="tags" in plan["managed"]["product"],
        revision_checked_at=stamp if plan.get("remote_updated_at") else None,
        full_compared_at=prior.get("full_compared_at") if incomplete else stamp,
        related_checked_at=prior.get("related_checked_at") if incomplete else stamp,
        read_complete=not incomplete, collections=plan.get("collections", []),
        metadata_changes=[r for r in changes if not r["field"].startswith("collections.")],
        metadata_display=[], verified_at=identity.verified_at.isoformat() if identity and identity.verified_at else None,
        absence_checked_at=stamp if plan["classification"] == "create" else None,
        equality_at=stamp if plan["classification"] == "no_change" and not plan.get("blockers") and not incomplete else prior.get("equality_at"))
    if read_failed:
        reason = '; '.join(r['component'] + ': ' + r['reason'] for r in failures)
        kind = 'authored_configuration' if all(r.get('kind') == 'authored_configuration' for r in failures) else 'incomplete_evidence'
        summary["refresh_failure"] = {"at": stamp, "kind": kind,
            "message": ('Required managed evidence is incomplete: ' + reason)[:500], "components": failures}
        summary["retry_after"] = (now() + timedelta(seconds=30)).isoformat()
        summary["failure_local"] = expected_local
    equality = plan["classification"] == "no_change" and not plan.get("blockers") and not incomplete
    summary.update(equality_local=expected_local if equality else prior.get("equality_local"),
        equality_remote_updated_at=plan.get("remote_updated_at") if equality else prior.get("equality_remote_updated_at"),
        equality_mappings=summary["mappings"] if equality else prior.get("equality_mappings"))
    from app.shopify_simple_fields import review_rows
    summary["display_changes"] = [r["label"] + ": " + r["after"][:120]
        for r in review_rows(plan) if r["action"] not in {"Already matching","Deferred (inventory-only scope)"}][:10]
    summary["display_changes"] += ["Collection: " + r["name"] for r in plan.get("collections", []) if r["action"] == "Join"]
    summary["metadata_display"] = summary["display_changes"][:len(summary["display_changes"]) - sum(r["action"] == "Join" for r in plan.get("collections", []))]
    encoded = json.dumps(summary, sort_keys=True)
    if len(encoded.encode()) > 32768:
        from app.shopify_connection import ShopifyError
        raise ShopifyError("Comparison exceeds its evidence bound. Reduce reviewed data.")
    from app.shopify_capacity import fence_owner
    fence_owner()
    if row.id is not None:
        updated = db.session.execute(update(ShopifyProductComparison).where(
            ShopifyProductComparison.id == row.id, ShopifyProductComparison.summary_json == original).values(
                summary_json=encoded, compared_at=now()), execution_options={'synchronize_session':False}).rowcount
        if not updated:
            db.session.rollback()
            return False
        db.session.expire(row)
    else:
        row.summary_json, row.compared_at = encoded, now()
        db.session.add(row)
    db.session.commit()


def invalidate(store_id, source):
    from app.shopify_capacity import fence_owner
    fence_owner()
    # Preserve historical comparison evidence, but never reuse equality after
    # a new/uncertain mutation has begun. A new full read replaces this marker.
    from app.shopify_drafts import now
    row=ShopifyProductComparison.query.filter_by(store_id=store_id,source_identity=source).first()
    if row:
        data=json.loads(row.summary_json)
        data.update(invalidated=True,invalidated_at=now().isoformat())
        store_summary(row,data)


def failed(product, message, *, started=None):
    """Preserve last-known comparison, but never reuse equality after a failed check."""
    from app.shopify_drafts import configuration, now
    from app.shopify_capacity import guard_owner
    guard_owner()
    for row in ShopifyProductComparison.query.filter_by(source_identity=product.source_relpath).populate_existing().all():
        if started and row.compared_at.isoformat() > started:
            continue
        data = json.loads(row.summary_json)
        if data.get("config") == configuration().fingerprint():
            data["refresh_failure"] = {"at": now().isoformat(), "message": message[:500]}
            if len(json.dumps(data, sort_keys=True).encode()) <= 32768:
                store_summary(row, data)
    from app.shopify_capacity import fence_owner
    fence_owner()
    db.session.commit()


def current(product, row, identity, mappings, *, freshness=True):
    from app.shopify_drafts import now, digest
    from app.shopify_drafts import configuration
    data = json.loads(row.summary_json)
    if data.get('package_assignments_unverified') and data.get('read_complete') is not True and data.get('package_readback_policy') != 2:
        return None  # an8f83e6e incomplete checkpoint requires a real fresh read
    from app.shopify_dimension_metadata import POLICY_VERSION
    if data.get('dimension_metadata_policy') != POLICY_VERSION and data.get('scope', 'full') == 'full':
        return None  # old value-only evidence cannot attest definitions/access
    from app.shopify_attributes import CAPABILITY_POLICY_VERSION
    if data.get("attribute_mappings") and data.get("attribute_capability_policy") != CAPABILITY_POLICY_VERSION:
        return None  # access-only historical proof cannot attest newly managed flags
    fresh = (recent(data.get("absence_checked_at"), ABSENCE_AGE) if data.get("classification") == "create" else
        recent(data.get("full_compared_at"), FULL_AGE) and recent(data.get("revision_checked_at"), REVISION_AGE)
        and recent(data.get("related_checked_at"), MAX_AGE))
    # Legacy summaries remain usable for their original ten-minute policy only.
    if not data.get("full_compared_at"):
        fresh = now() - row.compared_at <= MAX_AGE
    if (data.get("invalidated") or data.get("version") != 1 or freshness and not fresh
            or data.get("config") != configuration().fingerprint()
            or data.get("product_id") != product.id or data.get("source") != product.source_relpath
            or data.get("identity") != (digest(identity.media_evidence_json) if identity else None)
            or data.get("remote_id") != (identity.shopify_product_id if identity else None)
            or data.get("local") != fingerprint(product, data["images"])):
        return None
    for key, expected in data.get("mappings", {}).items():
        mapping = mappings.get((row.store_id, key))
        if expected != mapping_digest(mapping):
            return None
    for key,expected in data.get("attribute_mappings",{}).items():
        if attribute_mapping_digest(mappings.get((row.store_id,key)))!=expected:
            return None
    return data
