"""Local operational overview. Never discovers or mutates Woo state."""
from collections import Counter
import json
from datetime import UTC, datetime, timedelta

from flask import g, has_request_context
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app import db
from app.models import CatalogueOperation, Product, Variation, WooProductIdentity
from app.woo_publish_preview import (
    BUILDER_VERSION, cached_plan, operation_summary, plan_is_stale, store_identity,
)
from app.woocommerce_connection import WooConnectionError, build_woocommerce_workspace

LABELS = {"all": "Active local", "linked": "Woo linked", "no_change": "No Change",
          "update": "Updates", "local_only": "Local only", "create": "Reviewed Create",
          "link_candidate": "Link required", "blocked": "Blocked",
          "recovery_required": "Recovery required", "unknown": "Preview required"}
PREVIEW_MAX_AGE = timedelta(minutes=15)


def overview():
    """Identity and bounded, locally revalidated preview evidence, not remote truth."""
    if has_request_context() and hasattr(g, "woo_overview"):
        return g.woo_overview
    try:
        store = store_identity()
    except (ValueError, WooConnectionError):
        store = None
    identities = {row.product_id: row for row in WooProductIdentity.query.filter_by(
        store_key=store["key"]).all()} if store else {}
    previews = {}
    recovery = {}
    if store:
        for operation in CatalogueOperation.query.filter_by(operation_type="woo_controlled_publish").order_by(CatalogueOperation.started_at.desc()).limit(20):
            try:
                scope = json.loads(operation.scope or "{}")
            except (ValueError, TypeError):
                continue
            if scope.get("store_identity") != store["key"]:
                continue
            summary = scope.get("operation_summary", {})
            for product_id in scope.get("product_ids", []):
                result = next((r for r in summary.get("product_results", []) if r.get("product_id") == product_id), {})
                needs_review = (operation.recovery_state not in (None, "none")
                                and result.get("status") not in {"verified", "verified_with_warnings"})
                recovery.setdefault(product_id, operation.id if needs_review else None)
        operations = CatalogueOperation.query.filter_by(operation_type="woo_publish_preview").order_by(
            CatalogueOperation.started_at.desc()).limit(20).all()
        for operation in operations:
            plan = cached_plan(operation.id)
            summary = operation_summary(operation)
            if not plan or summary.get("store_identity") != store["key"]:
                continue
            try:
                generated = datetime.fromisoformat(summary["generated_at"])
                if generated.tzinfo is None:
                    generated = generated.replace(tzinfo=UTC)
                fresh = (timedelta(0) <= datetime.now(UTC) - generated <= PREVIEW_MAX_AGE
                         and summary.get("builder_version") == BUILDER_VERSION
                         and not plan_is_stale(plan))
            except (ValueError, KeyError, TypeError):
                fresh = False
            for item in plan["products"]:
                previews.setdefault(item["product_id"], dict(item, fresh=fresh,
                    operation_id=operation.id, preview_digest=summary.get("preview_digest"),
                    checked_at=summary.get("generated_at")))
    variation_count = select(func.count(Variation.id)).where(
        Variation.product_id == Product.id).correlate(Product).scalar_subquery()
    rows = []
    for product, count in db.session.query(Product, variation_count).options(
            joinedload(Product.collection)).filter(Product.catalogue_status == "active").order_by(Product.title, Product.id):
        identity = identities.get(product.id)
        linked = bool(identity and identity.woo_product_id and identity.verification_state == "verified"
                      and identity.sku == product.sku
                      and identity.stable_identity == (product.source_relpath or f"product:{product.id}"))
        preview = previews.get(product.id)
        fresh = bool(preview and preview["fresh"])
        state = preview["action"] if fresh else "unknown"
        recovery_id = recovery.get(product.id)
        if recovery_id:
            state = "recovery_required"
        rows.append({"product": product, "variation_count": count, "linked": linked,
                     "local_only": identity is None, "identity": identity,
                     "state": state, "label": LABELS.get(state, "Preview required"),
                     "preview": preview, "fresh": fresh, "recovery_id": recovery_id})
    counts = Counter(row["state"] for row in rows)
    counts.update(all=len(rows), linked=sum(row["linked"] for row in rows),
                  local_only=sum(row["local_only"] for row in rows))
    # Reuse persisted health only; a test for another host is not this store's health.
    connection = build_woocommerce_workspace()
    latest = connection["health"]["latest"]
    same_host = bool(store and latest.get("hostname") == store["host"])
    health = {"label": connection["health"]["state"].replace("_", " ") if same_host else
              "not configured" if not store else "review Integration health",
              "checked_at": connection["history"][0]["finished_at"] if same_host and connection["history"] else None}
    result = {"rows": rows, "counts": counts, "labels": LABELS, "store": store, "health": health,
              "by_id": {row["product"].id: row for row in rows}}
    if has_request_context():
        g.woo_overview = result
    return result


def product_status(product_id):
    row = overview()["by_id"].get(product_id)
    if not row:
        return "Inactive local product"
    if row["fresh"] or row["recovery_id"]:
        return row["label"]
    return "Woo linked · Preview required" if row["linked"] else "Local only · Preview required" if row["local_only"] else "Identity needs review"


def workspace(args):
    data = overview()
    status = args.get("status", "all")
    if status not in LABELS:
        status = "all"
    query = args.get("q", "").strip()[:100]
    collection = args.get("collection", "")
    kind = args.get("type", "")
    rows = [row for row in data["rows"] if
            (status == "all" or (row.get(status) if status in {"linked", "local_only"} else row["state"] == status))
            and (not query or query.casefold() in f'{row["product"].title} {row["product"].sku}'.casefold())
            and (not collection or str(row["product"].collection_id) == collection)
            and (not kind or row["product"].product_type == kind)]
    try:
        page = max(1, int(args.get("page", 1)))
    except ValueError:
        page = 1
    pages = max(1, (len(rows) + 49) // 50)
    page = min(page, pages)
    displayed = rows[(page-1)*50:page*50]
    from app.catalogue_images import product_thumbnail_url
    for row in displayed:
        row["thumbnail"] = product_thumbnail_url(row["product"])
    collections = {row["product"].collection_id: row["product"].collection.name
                   for row in data["rows"] if row["product"].collection}
    return dict(data, rows=displayed, total=len(rows), page=page, pages=pages,
                collections=collections, filters=dict(status=status, q=query, collection=collection, type=kind))
