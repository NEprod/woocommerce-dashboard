"""Bounded read-comparison evidence, independent of publishing verification."""
import json
from datetime import timedelta

from app import db
from app.models import ShopifyProductComparison

MAX_AGE = timedelta(minutes=10)


def mapping_digest(row):
    from app.shopify_drafts import digest
    return digest({"remote": row.remote_id, "state": row.state, "standard": row.standard_category_id,
        "evidence": row.evidence_json}) if row else None


def fingerprint(product, images):
    from app.shopify_products import source_fingerprint
    from app.shopify_taxonomy import fingerprint as taxonomy_fingerprint
    from app.shopify_drafts import digest
    return digest({"local": source_fingerprint(product, images), "taxonomy": taxonomy_fingerprint(product)})


def record(product, plan, identity=None):
    from app.shopify_drafts import now, digest
    from app.shopify_taxonomy import mapping
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
    changes = [{"field": r["field"], "after": r["after"]} for r in plan["fields"] if r["changed"]]
    changes += [{"field": "collections." + r["key"], "after": r["name"]} for r in plan.get("collections", []) if r["action"] == "Join"]
    summary = {"version": 1, "product_id": product.id, "source": product.source_relpath,
        "store_id": plan["store_id"], "remote_id": plan.get("remote_id"),
        "local": expected_local, "images": [{"source": i["source"]} for i in plan["images"]],
        "remote_digest": plan.get("remote_digest"), "classification": plan["classification"],
        "changes": changes[:100], "change_count": len(changes), "blockers": plan.get("blockers", []),
        "identity": digest(identity.media_evidence_json) if identity else None,
        "mappings": {r["key"]: mapping_digest(mapping(plan["store_id"], r["key"])) for r in plan.get("collections", [])},
        "actual_status": plan.get("actual_status", "Not yet created")}
    summary["config"] = plan.get("config_digest")
    from app.shopify_simple_fields import review_rows
    summary["display_changes"] = [r["label"] + ": " + r["after"][:120]
        for r in review_rows(plan) if r["action"] != "Already matching"][:10]
    summary["display_changes"] += ["Collection: " + r["name"] for r in plan.get("collections", []) if r["action"] == "Join"]
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


def current(product, row, identity, mappings):
    from app.shopify_drafts import now, digest
    from app.shopify_drafts import configuration
    data = json.loads(row.summary_json)
    if (data.get("version") != 1 or now() - row.compared_at > MAX_AGE
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
    return data
