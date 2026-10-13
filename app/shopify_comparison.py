"""Bounded read-comparison evidence, independent of publishing verification."""
import json
from datetime import timedelta

from app import db
from app.models import ShopifyProductComparison

MAX_AGE = timedelta(minutes=10)
REVISION_AGE = timedelta(minutes=10)
FULL_AGE = timedelta(minutes=30)
ABSENCE_AGE = timedelta(minutes=30)


def recent(value, age):
    from datetime import datetime
    from app.shopify_drafts import now
    try:
        return timedelta(0) <= now() - datetime.fromisoformat(value) <= age
    except (ValueError, TypeError):
        return False


def store_summary(row, data):
    encoded = json.dumps(data, sort_keys=True)
    if len(encoded.encode()) > 32768:
        from app.shopify_connection import ShopifyError
        raise ShopifyError("Comparison exceeds its evidence bound.")
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


def record(product, plan, identity=None):
    from app.shopify_drafts import now, digest
    if not plan.get("taxonomy_fingerprint") or not plan.get("workspace_source_fingerprint"):
        return False  # legacy publishing verification did not cover the new taxonomy contract
    expected_local = digest({"local": plan["workspace_source_fingerprint"], "taxonomy": plan["taxonomy_fingerprint"]})
    if fingerprint(product, plan["images"]) != expected_local:
        # A source edit during remote reads cannot borrow equality from the older plan.
        # Verified remote write evidence still stands; a fresh Review is required locally.
        return False
    row = ShopifyProductComparison.query.filter_by(store_id=plan["store_id"], source_identity=product.source_relpath).first()
    if row is None:
        row = ShopifyProductComparison(store_id=plan["store_id"], source_identity=product.source_relpath)
    def summary_value(value):
        raw = json.dumps(value,sort_keys=True,ensure_ascii=False)
        # Comparison is an index, not write authority. Full values remain in the
        # signed/hash-bound plan and lossless durable publishing evidence.
        return {"sha256":digest(value),"bytes":len(raw.encode()),"detail":"See full reviewed value"} if len(raw.encode()) > 512 else value
    scoped = plan.get("scope") == "inventory"
    changes = [{"field": r["field"], "after": summary_value(r["after"])} for r in plan["fields"] if r["changed"] and not r.get("deferred")]
    changes += [{"field": "collections." + r["key"], "after": r["name"]} for r in plan.get("collections", []) if r["action"] in {"Join","Create & Join"}]
    summary = {"version": 1, "product_id": product.id, "source": product.source_relpath,
        "store_id": plan["store_id"], "remote_id": plan.get("remote_id"),
        "local": expected_local, "images": [{"source": i["source"]} for i in plan["images"]],
        "remote_digest": plan.get("remote_digest"), "classification": plan["classification"],
        "changes": changes[:100], "change_count": len(changes), "scope":plan.get("scope","full"),
        "blockers": plan.get("blockers", []) + (["Inventory-only comparison: metadata, attributes, collections and publication are not fully compared."] if scoped else []),
        "identity": digest(identity.media_evidence_json) if identity else None,
        "mappings": {r["key"]: r.get("mapping_fingerprint") for r in plan.get("collections", [])},
        "actual_status": plan.get("actual_status", "Not yet created")}
    summary["config"] = plan.get("config_digest")
    from app.shopify_attributes import resource
    summary["attribute_mappings"]={r["source"]:attribute_mapping_digest(resource(plan["store_id"],r["source"]))
        for a in plan.get("attributes",[]) for r in a["resources"]}
    summary["native"] = plan.get("native")
    summary["conflicting_changes"] = bool(plan.get("conflicting_changes")) and plan["classification"] == "update"
    stamp = now().isoformat()
    summary.update(sku=product.sku, remote_updated_at=plan.get("remote_updated_at"),
        tags_managed="tags" in plan["managed"]["product"],
        revision_checked_at=stamp if plan.get("remote_updated_at") else None,
        full_compared_at=stamp, related_checked_at=stamp, collections=plan.get("collections", []),
        metadata_changes=[r for r in changes if not r["field"].startswith("collections.")],
        metadata_display=[], verified_at=identity.verified_at.isoformat() if identity and identity.verified_at else None,
        absence_checked_at=stamp if plan["classification"] == "create" else None,
        equality_at=stamp if plan["classification"] == "no_change" and not plan.get("blockers") and not scoped else (json.loads(row.summary_json).get("equality_at") if row.summary_json else None))
    prior = json.loads(row.summary_json) if row.summary_json else {}
    equality = plan["classification"] == "no_change" and not plan.get("blockers") and not scoped
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
    row.summary_json, row.compared_at = encoded, now()
    db.session.add(row)
    db.session.commit()


def invalidate(store_id, source):
    ShopifyProductComparison.query.filter_by(store_id=store_id, source_identity=source).delete()
    db.session.commit()


def failed(product, message):
    """Preserve last-known comparison, but never reuse equality after a failed check."""
    from app.shopify_drafts import configuration, now
    for row in ShopifyProductComparison.query.filter_by(source_identity=product.source_relpath).all():
        data = json.loads(row.summary_json)
        if data.get("config") == configuration().fingerprint():
            data["refresh_failure"] = {"at": now().isoformat(), "message": message[:500]}
            encoded = json.dumps(data, sort_keys=True)
            if len(encoded.encode()) <= 32768:
                row.summary_json = encoded
    db.session.commit()


def current(product, row, identity, mappings, *, freshness=True):
    from app.shopify_drafts import now, digest
    from app.shopify_drafts import configuration
    data = json.loads(row.summary_json)
    fresh = (recent(data.get("absence_checked_at"), ABSENCE_AGE) if data.get("classification") == "create" else
        recent(data.get("full_compared_at"), FULL_AGE) and recent(data.get("revision_checked_at"), REVISION_AGE)
        and recent(data.get("related_checked_at"), MAX_AGE))
    # Legacy summaries remain usable for their original ten-minute policy only.
    if not data.get("full_compared_at"):
        fresh = now() - row.compared_at <= MAX_AGE
    if (data.get("version") != 1 or freshness and not fresh
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
