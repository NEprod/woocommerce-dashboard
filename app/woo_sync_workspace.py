"""Local rendering of shared Woo evidence; never a remote page load."""
from collections import Counter
from flask import g, has_request_context
from app.models import WooProductIdentity
from app.woo_publish_preview import _product_query, store_identity
from app.woocommerce_connection import WooConnectionError, build_woocommerce_workspace
from app import woo_live_state as live

LABELS = {"all": "Active local", "linked": "Woo linked", **live.LABELS}


def overview():
    if has_request_context() and hasattr(g, "woo_overview"):
        return g.woo_overview
    try:
        store = store_identity()
    except (ValueError, WooConnectionError):
        store = None
    identities = {r.product_id: r for r in WooProductIdentity.query.filter_by(store_key=store["key"]).all()} if store else {}
    rows = []
    for product in _product_query().filter_by(catalogue_status="active").order_by("title", "id"):
        identity = identities.get(product.id)
        state = live.product_state(product, store=store["key"], identity=identity) if store else live._view(
            "blocked", message="Configure WooCommerce in Integration health.", refresh=False)
        rows.append(dict(product=product, variation_count=len(product.variations), identity=identity,
            linked=live._trusted(product, identity), local_only=identity is None,
            state=state["state"], label=state["label"], fresh=not state["needs_refresh"],
            live=state, recovery_id=state["recovery_id"]))
    counts = Counter(row["state"] for row in rows)
    counts.update(all=len(rows), linked=sum(r["linked"] for r in rows))
    connection = build_woocommerce_workspace()
    latest = connection["health"]["latest"]
    same_host = bool(store and latest.get("hostname") == store["host"])
    health = {"label": connection["health"]["state"].replace("_", " ") if same_host else
              "not configured" if not store else "review Integration health",
              "checked_at": connection["history"][0]["finished_at"] if same_host and connection["history"] else None}
    result = dict(rows=rows, counts=counts, labels=LABELS, store=store, health=health,
                  checked_at=max((r["live"]["checked_at"] for r in rows if r["live"]["checked_at"]), default=None),
                  by_id={r["product"].id: r for r in rows})
    if has_request_context():
        g.woo_overview = result
    return result


def product_status(product_id):
    row = overview()["by_id"].get(product_id)
    return row["label"] if row else "Inactive local product"


def workspace(args):
    data = overview()
    status = args.get("status", "all")
    if status not in LABELS:
        status = "all"
    query = args.get("q", "").strip()[:100]
    collection = args.get("collection", "")
    kind = args.get("type", "")
    rows = [row for row in data["rows"] if
            (status == "all" or (row.get(status) if status in {"linked"} else row["state"] == status))
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
