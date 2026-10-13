"""Incremental, serialized Shopify reads. Never calls a Shopify mutation."""
import json
from datetime import timedelta

from app import db
from app.models import Product, ShopifyProductComparison, ShopifyCollectionIdentity
from app.shopify_connection import ShopifyAdapter, ShopifyError
from app import shopify_comparison as comparison, shopify_drafts as drafts
from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation

BATCH = 25
FULL_PER_STEP = 2


def retry_due(data):
    from datetime import datetime
    try:
        return drafts.now() >= datetime.fromisoformat(data["retry_after"])
    except (KeyError, TypeError, ValueError):
        return True


def mark_failure(product, row, message):
    if row is None:
        config = drafts.configuration()
        row = ShopifyProductComparison(store_id="unverified:" + config.fingerprint(),
            source_identity=product.source_relpath, compared_at=drafts.now())
        data = {"version": 1, "config": config.fingerprint(), "classification": "unavailable"}
    else:
        data = json.loads(row.summary_json)
    failures = min(int(data.get("failures", 0)) + 1, 6)
    data.update(failures=failures, retry_after=(drafts.now() + timedelta(seconds=min(900, 30 * 2 ** (failures - 1)))).isoformat(),
        refresh_failure={"at": drafts.now().isoformat(), "message": message[:500]})
    comparison.store_summary(row, data)


def refresh(products, *, force=False, adapter=None):
    products = list(products)
    if len(products) > BATCH:
        raise ShopifyError("At most 25 visible products can be checked per request.")
    adapter = adapter or ShopifyAdapter()
    lease = acquire_catalogue_operation("shopify_status_refresh", {"products": [p.id for p in products]})
    results, pending, errors, full = [], [], [], 0
    try:
        config = drafts.configuration()
        config_digest = config.fingerprint()
        mappings = {(r.store_id, r.source_key): r for r in ShopifyCollectionIdentity.query.all()}
        from app.models import ShopifyAttributeIdentity
        mappings.update({(r.store_id,r.source_key):r for r in ShopifyAttributeIdentity.query.all()})
        work, due = [], []
        for product in products:
            if product.product_type not in {"simple","variable"} or product.catalogue_status != "active":
                continue
            rows = ShopifyProductComparison.query.filter_by(source_identity=product.source_relpath).order_by(ShopifyProductComparison.compared_at.desc()).all()
            saved = next((r for r in rows if json.loads(r.summary_json).get("config") == config_digest), None)
            data = json.loads(saved.summary_json) if saved else {}
            if not force and not retry_due(data):
                results.append(product.id)
                continue
            # Trust the persisted Shop GID only under the same complete configuration.
            identity = drafts.matching_identity(saved.store_id, product) if saved and not saved.store_id.startswith("unverified:") else None
            if identity and identity.state != "processing" and (identity.state != "verified" or identity.verification_state != "verified"):
                results.append(product.id)  # operation recovery is never cleared by background checks
                continue
            valid = comparison.current(product, saved, identity, mappings, freshness=False) if saved and data.get("local") else None
            native_due = product.product_type == "variable" and (data.get("native") or {}).get("enabled") and not comparison.recent((data.get("native") or {}).get("checked_at") or data.get("full_compared_at"),comparison.MAX_AGE)
            if valid and not force and not native_due and not data.get("refresh_failure") and comparison.current(product, saved, identity, mappings):
                results.append(product.id)
                continue
            item = (product, saved, identity, valid)
            work.append(item)
            if identity and data.get("remote_updated_at") and not force and not comparison.recent(data.get("observed_revision_at"), comparison.REVISION_AGE):
                due.append(item)
        if not work:
            return {"checked": results, "pending": [], "errors": [], "full_comparisons": 0}
        shop = adapter.test_connection(config)
        if "read_products" not in shop["scopes"]:
            raise ShopifyError("Missing read_products for status reconciliation.")
        revisions = adapter.product_revisions(config, [item[2].shopify_product_id for item in due]) if due else {}
        fetched_revisions = set(revisions)
        for _, saved, identity, _ in work:
            if saved and identity and identity.shopify_product_id not in revisions:
                observed = json.loads(saved.summary_json)
                if comparison.recent(observed.get("observed_revision_at"), comparison.REVISION_AGE) and observed.get("observed_updated_at"):
                    revisions[identity.shopify_product_id] = {"id": identity.shopify_product_id, "updatedAt": observed["observed_updated_at"], "status": observed["observed_status"]}
        for product, saved, identity, valid in work:
            try:
                identity = drafts.matching_identity(shop["shop_id"], product)
                if identity and identity.state == "processing":
                    if full >= FULL_PER_STEP:
                        pending.append(product.id)
                        continue
                    full += 1
                    drafts.finalize_processing(identity, adapter, force=force)
                    if identity.state == "processing":
                        results.append(product.id)
                        continue  # no tight polling/automatic write-resume
                    saved = ShopifyProductComparison.query.filter_by(store_id=shop["shop_id"], source_identity=product.source_relpath).first()
                    valid = comparison.current(product, saved, identity, mappings) if saved else None
                    if valid:
                        results.append(product.id)
                        continue  # complete retained-plan readback already recorded comparison
                if identity and (identity.state != "verified" or identity.verification_state != "verified"):
                    results.append(product.id)
                    continue
                revision = revisions.get(identity.shopify_product_id) if identity else None
                if saved and revision and identity.shopify_product_id in fetched_revisions:
                    observed = json.loads(saved.summary_json)
                    observed.update(observed_status=revision["status"], observed_updated_at=revision["updatedAt"], observed_revision_at=drafts.now().isoformat())
                    comparison.store_summary(saved, observed)
                native_due = product.product_type == "variable" and ((valid or {}).get("native") or {}).get("enabled") and not comparison.recent(((valid or {}).get("native") or {}).get("checked_at") or (valid or {}).get("full_compared_at"),comparison.MAX_AGE)
                needs_full = force or native_due or not valid or valid.get("scope","full") != "full" or not identity or not revision or not comparison.recent(valid.get("full_compared_at"), comparison.FULL_AGE)
                if revision and valid and (revision["updatedAt"] != valid.get("remote_updated_at") or revision["status"] != valid.get("actual_status")):
                    needs_full = True
                if identity and identity.shopify_product_id in revisions and revision is None:
                    from app.shopify_lifecycle import check_absence
                    if check_absence(identity, adapter):
                        results.append(product.id)
                        continue
                    raise ShopifyError("Retained Shopify product is missing or inaccessible. No Create is permitted; inspect identity and permissions.")
                if needs_full:
                    if full >= FULL_PER_STEP:
                        pending.append(product.id)
                        continue
                    full += 1
                    previous = json.loads(saved.summary_json) if saved else {}
                    drafts.preview(product, adapter, context={"shop": shop, "config": config.fingerprint()})  # no mutation, token discarded
                    if identity and not valid and revision and previous.get("remote_updated_at") and previous["remote_updated_at"] != revision["updatedAt"]:
                        updated = ShopifyProductComparison.query.filter_by(store_id=shop["shop_id"], source_identity=product.source_relpath).first()
                        if updated:
                            changed = json.loads(updated.summary_json)
                            changed["conflicting_changes"] = True
                            comparison.store_summary(updated, changed)
                    # Remove obsolete unverified retry markers after successful store-verified evidence.
                    ShopifyProductComparison.query.filter_by(source_identity=product.source_relpath).filter(ShopifyProductComparison.store_id.like("unverified:%")).delete()
                    db.session.commit()
                else:
                    data = dict(valid)
                    # updatedAt cannot attest memberships/media/publication. Refresh memberships independently.
                    if not comparison.recent(data.get("related_checked_at"), comparison.MAX_AGE):
                        from app.shopify_taxonomy import membership_plan
                        collections, blockers, _ = membership_plan(product, shop["shop_id"], identity.shopify_product_id, config, adapter)
                        joins = [{"field": "collections." + r["key"], "after": r["name"]} for r in collections if r["action"] == "Join"]
                        data.update(collections=collections, mappings={r["key"]: r["mapping_fingerprint"] for r in collections},
                            related_checked_at=drafts.now().isoformat(),
                            changes=data.get("metadata_changes", []) + joins, change_count=len(data.get("metadata_changes", [])) + len(joins),
                            display_changes=data.get("metadata_display", []) + ["Collection: " + r["after"] for r in joins],
                            blockers=[b for b in data.get("blockers", []) if not b.startswith("Collection mapping required:")] + blockers)
                        data["classification"] = "update" if data["change_count"] else "no_change"
                    data.update(revision_checked_at=drafts.now().isoformat() if identity.shopify_product_id in fetched_revisions else json.loads(saved.summary_json)["observed_revision_at"], actual_status=revision["status"])
                    data.pop("refresh_failure", None)
                    data.pop("retry_after", None)
                    comparison.store_summary(saved, data)
                results.append(product.id)
            except ShopifyError as exc:
                from app.shopify_connection import ShopifyProductAbsent
                if isinstance(exc, ShopifyProductAbsent) and identity:
                    from app.shopify_lifecycle import check_absence
                    try:
                        if check_absence(identity, adapter):
                            results.append(product.id)
                            continue
                    except ShopifyError as failure:
                        exc=failure
                mark_failure(product, saved, str(exc))
                errors.append({"product_id": product.id, "message": str(exc)})
                results.append(product.id)
        return {"checked": results, "pending": pending, "errors": errors, "full_comparisons": full}
    except ShopifyError as exc:
        errors.append({"message": str(exc)})
        for product, saved, _, _ in locals().get("work", []):
            mark_failure(product, saved, str(exc))
        return {"checked": [p.id for p in products], "pending": [], "errors": errors, "full_comparisons": full}
    finally:
        finish_catalogue_operation(lease.id, status="partial" if errors else "succeeded", recovery_state="none", operation_summary={"mode": "read_only", "checked": len(results), "errors": errors, "full_comparisons": full})
