"""Read-only presentation data for the Phase 2 Products browser."""

from __future__ import annotations

import math
from decimal import Decimal

from flask import url_for
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import joinedload, selectinload

from app import db
from app.dashboard import METADATA_ISSUE_DEFINITIONS, metadata_issue_condition
from app.models import Collection, Product, ProductAsset, Variation


SUPPORTED_PRODUCT_TYPES = {"simple", "variable"}
SUPPORTED_CATALOGUE_STATUSES = {"active", "missing"}
SUPPORTED_METADATA_SOURCES = {"shared", "override", "none"}
SUPPORTED_PAGE_SIZES = {25, 50, 100}
DEFAULT_PAGE_SIZE = 50
VARIATION_PREVIEW_LIMIT = 8


def _blank(column):
    return or_(column.is_(None), func.trim(column) == "")


def metadata_problem_condition():
    return or_(
        metadata_issue_condition("missing_description"),
        metadata_issue_condition("missing_image"),
        metadata_issue_condition("missing_seo"),
    )


def parse_products_filters(args):
    """Validate and normalize URL-backed browser controls."""

    issue = args.get("issue", "").strip()
    product_type = args.get("type", "").strip()
    status = args.get("status", "").strip()
    source = args.get("source", "").strip()
    collection = args.get("collection", "").strip()
    query = args.get("q", "").strip()[:191]

    if issue and issue not in METADATA_ISSUE_DEFINITIONS:
        raise ValueError("Unsupported metadata issue filter")
    if product_type and product_type not in SUPPORTED_PRODUCT_TYPES:
        raise ValueError("Unsupported product type filter")
    if status and status not in SUPPORTED_CATALOGUE_STATUSES:
        raise ValueError("Unsupported catalogue status filter")
    if source and source not in SUPPORTED_METADATA_SOURCES:
        raise ValueError("Unsupported metadata source filter")
    if collection:
        try:
            collection_id = int(collection)
        except ValueError as error:
            raise ValueError("Unsupported collection filter") from error
        if collection_id < 1:
            raise ValueError("Unsupported collection filter")
    else:
        collection_id = None

    try:
        page = max(1, int(args.get("page", "1")))
        per_page = int(args.get("per_page", str(DEFAULT_PAGE_SIZE)))
    except ValueError as error:
        raise ValueError("Invalid pagination") from error
    if per_page not in SUPPORTED_PAGE_SIZES:
        raise ValueError("Unsupported page size")

    return {
        "q": query,
        "collection": collection_id,
        "type": product_type,
        "status": status,
        "source": source,
        "issue": issue,
        "page": page,
        "per_page": per_page,
    }


def _source_exists(label):
    return (
        select(ProductAsset.id)
        .where(
            ProductAsset.product_id == Product.id,
            ProductAsset.variation_id.is_(None),
            ProductAsset.kind == "info",
            ProductAsset.label == label,
        )
        .exists()
    )


def _source_conditions(source):
    override = or_(~_blank(Product.override_json_path), _source_exists("override"))
    shared = or_(~_blank(Product.shared_json_path), _source_exists("shared"))
    if source == "override":
        return override
    if source == "shared":
        return and_(~override, shared)
    if source == "none":
        return and_(~override, ~shared)
    return None


def _apply_filters(query, filters):
    if filters["q"]:
        pattern = f"%{filters['q']}%"
        query = query.filter(or_(Product.title.ilike(pattern), Product.sku.ilike(pattern)))
    if filters["collection"]:
        query = query.filter(Product.collection_id == filters["collection"])
    if filters["type"]:
        query = query.filter(Product.product_type == filters["type"])
    if filters["status"]:
        query = query.filter(Product.catalogue_status == filters["status"])
    if filters["source"]:
        query = query.filter(_source_conditions(filters["source"]))
    if filters["issue"]:
        query = query.filter(metadata_issue_condition(filters["issue"]))
    return query


def _money(value):
    if value is None:
        return None
    return f"{Decimal(value):.2f}"


def _effective_price(regular, sale):
    return sale if sale is not None else regular


def _metadata_source(product, assets):
    labels = {asset.label for asset in assets if asset.kind == "info"}
    if product.override_json_path or "override" in labels:
        return "override"
    if product.shared_json_path or "shared" in labels:
        return "shared"
    return "none"


def _asset_presence(assets, label):
    return any(asset.kind == "info" and asset.label == label for asset in assets)


def _product_view(product, variation_count, minimum_price, maximum_price):
    assets = list(product.assets)
    shared_present = _asset_presence(assets, "shared")
    override_present = _asset_presence(assets, "override")
    source = _metadata_source(product, assets)
    minimum = minimum_price
    maximum = maximum_price
    if variation_count == 0:
        minimum = maximum = _effective_price(product.regular_price, product.sale_price)

    edit_label = "override" if override_present else "shared" if shared_present else None
    view_label = edit_label
    thumbnail = product.image_url
    if not thumbnail and product.images:
        thumbnail = product.images[0].url

    row = {
        "id": product.id,
        "sku": product.sku or "",
        "title": product.title or "Untitled product",
        "type": "variable" if product.product_type == "variable" else "simple",
        "collection": product.collection.name if product.collection else "Unassigned",
        "collection_id": product.collection_id,
        "catalogue_status": product.catalogue_status or "active",
        "variation_count": int(variation_count or 0),
        "price": {"minimum": _money(minimum), "maximum": _money(maximum)},
        "metadata_source": source,
        "shared_present": shared_present,
        "override_present": override_present,
        "shared_path": next(
            (asset.path for asset in assets if asset.kind == "info" and asset.label == "shared"),
            product.shared_json_path or "",
        ),
        "override_path": next(
            (asset.path for asset in assets if asset.kind == "info" and asset.label == "override"),
            product.override_json_path or "",
        ),
        "thumbnail": thumbnail,
        "thumbnail_alt": product.images[0].alt_text if product.images else product.title,
        "updated_at": product.local_updated_at.isoformat() if product.local_updated_at else None,
        "has_metadata_issue": bool(
            (not product.short_description and not product.description)
            or not thumbnail
            or not product.meta_title
            or not product.meta_description
        )
        and product.catalogue_status == "active",
        "view_url": (
            url_for("main.open_info_asset", product_id=product.id, label=view_label)
            if view_label
            else None
        ),
        "edit_url": (
            url_for("main.product_edit", product_id=product.id, label=edit_label)
            if edit_label
            else None
        ),
    }
    return row


def _summary():
    return {
        "collections": Collection.query.count(),
        "products": Product.query.count(),
        "variations": Variation.query.count(),
        "active": Product.query.filter_by(catalogue_status="active").count(),
        "missing": Product.query.filter_by(catalogue_status="missing").count(),
        "metadata_issues": Product.query.filter(metadata_problem_condition()).count(),
    }


def build_products_data(filters):
    """Return a paged collection-grouped view without eager variation rows."""

    variation_count = (
        select(func.count(Variation.id))
        .where(Variation.product_id == Product.id)
        .correlate(Product)
        .scalar_subquery()
    )
    effective_variation_price = func.coalesce(Variation.sale_price, Variation.regular_price)
    minimum_price = (
        select(func.min(effective_variation_price))
        .where(Variation.product_id == Product.id)
        .correlate(Product)
        .scalar_subquery()
    )
    maximum_price = (
        select(func.max(effective_variation_price))
        .where(Variation.product_id == Product.id)
        .correlate(Product)
        .scalar_subquery()
    )

    base = db.session.query(Product)
    filtered = _apply_filters(base, filters)
    total = filtered.count()
    pages = max(1, math.ceil(total / filters["per_page"]))
    page = min(filters["page"], pages)
    rows = (
        _apply_filters(
            db.session.query(
                Product,
                variation_count.label("variation_count"),
                minimum_price.label("minimum_price"),
                maximum_price.label("maximum_price"),
            ),
            filters,
        )
        .options(
            joinedload(Product.collection),
            selectinload(Product.assets),
            selectinload(Product.images),
        )
        .outerjoin(Collection, Product.collection_id == Collection.id)
        .order_by(
            case((Collection.name.is_(None), 1), else_=0),
            Collection.name.asc(),
            Product.title.asc(),
            Product.sku.asc(),
        )
        .offset((page - 1) * filters["per_page"])
        .limit(filters["per_page"])
        .all()
    )

    group_stats_rows = (
        _apply_filters(
            db.session.query(
                Product.collection_id,
                Collection.name,
                func.count(func.distinct(Product.id)).label("product_count"),
                func.count(Variation.id).label("variation_count"),
                func.count(
                    func.distinct(case((Product.catalogue_status == "active", Product.id)))
                ).label("active_count"),
                func.count(
                    func.distinct(case((Product.catalogue_status == "missing", Product.id)))
                ).label("missing_count"),
                func.max(Product.local_updated_at).label("last_updated"),
            ).outerjoin(Collection, Product.collection_id == Collection.id),
            filters,
        )
        .outerjoin(Variation, Variation.product_id == Product.id)
        .group_by(Product.collection_id, Collection.name)
        .all()
    )
    stats = {
        collection_id: {
            "id": collection_id,
            "name": name or "Unassigned",
            "product_count": int(product_count or 0),
            "variation_count": int(variation_total or 0),
            "active_count": int(active_count or 0),
            "missing_count": int(missing_count or 0),
            "last_updated": last_updated.isoformat() if last_updated else None,
        }
        for (
            collection_id,
            name,
            product_count,
            variation_total,
            active_count,
            missing_count,
            last_updated,
        ) in group_stats_rows
    }

    groups = []
    group_lookup = {}
    flat_items = []
    for product, count, minimum, maximum in rows:
        item = _product_view(product, count, minimum, maximum)
        flat_items.append(item)
        key = product.collection_id
        if key not in group_lookup:
            group = {**stats[key], "products": []}
            groups.append(group)
            group_lookup[key] = group
        group_lookup[key]["products"].append(item)

    active_filters = any(
        filters[key] for key in ("q", "collection", "type", "status", "source", "issue")
    )
    return {
        "summary": _summary(),
        "groups": groups,
        "items": flat_items,
        "filters": filters,
        "pagination": {
            "page": page,
            "per_page": filters["per_page"],
            "pages": pages,
            "total": total,
            "from": ((page - 1) * filters["per_page"] + 1) if total else 0,
            "to": min(page * filters["per_page"], total),
        },
        "empty_reason": "filtered" if not total and active_filters else "catalogue" if not total else None,
    }


def build_variation_data(product, *, include_all=False):
    query = (
        Variation.query.filter_by(product_id=product.id)
        .options(selectinload(Variation.attributes))
        .order_by(Variation.sku.asc(), Variation.id.asc())
    )
    total = query.count()
    variations = query.all() if include_all else query.limit(VARIATION_PREVIEW_LIMIT).all()
    parent_source = _metadata_source(product, list(product.assets))
    items = []
    for variation in variations:
        price = _effective_price(variation.regular_price, variation.sale_price)
        items.append(
            {
                "id": variation.id,
                "sku": variation.sku or "",
                "attributes": [
                    {"name": attribute.name, "value": attribute.value}
                    for attribute in sorted(
                        variation.attributes,
                        key=lambda attribute: (
                            attribute.position if attribute.position is not None else 999,
                            attribute.name,
                        ),
                    )
                ],
                "price": _money(price),
                "stock_quantity": variation.stock_quantity,
                "catalogue_status": variation.catalogue_status or "active",
                "metadata_source": parent_source,
                "updated_at": (
                    variation.local_updated_at.isoformat()
                    if variation.local_updated_at
                    else None
                ),
            }
        )
    return {
        "product_id": product.id,
        "product_sku": product.sku,
        "total": total,
        "items": items,
        "truncated": len(items) < total,
    }
