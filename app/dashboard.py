"""Read-only data composition for the Phase 2 catalogue health dashboard."""

from __future__ import annotations

from datetime import UTC, datetime

from flask import g, has_request_context
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import joinedload, selectinload

from app import db
from app.catalogue_images import (
    primary_image_alt,
    product_thumbnail_url,
    resolve_product_catalogue_image,
)
from app.collection_identity import collection_display_name
from app.models import (
    CatalogueOperation,
    Collection,
    Product,
    Variation,
)
from app.publishing import projected_publishing_intent
from app.utils.operation_control import get_active_operation


OPERATION_LABELS = {
    "append": "New catalogue scan",
    "product_update": "Product update",
    "shared_collection_update": "Collection refresh",
    "full": "Full regeneration",
    "reconstruction": "Catalogue reconstruction",
    "intake_group": "Catalogue Intake — Group Images",
    "intake_structured_import": "Catalogue Intake — Import Structured Folder",
    "woo_connection_test": "WooCommerce connection test",
}

METADATA_ISSUE_DEFINITIONS = {
    "missing_description": {
        "label": "Missing descriptions",
        "count_key": "missing_descriptions",
    },
    "missing_image": {
        "label": "Missing images",
        "count_key": "missing_images",
    },
    "missing_seo": {
        "label": "Missing SEO metadata",
        "count_key": "missing_seo",
    },
}


def _blank(column):
    return or_(column.is_(None), func.trim(column) == "")


def metadata_issue_condition(issue_key):
    """Return the active-parent condition used by dashboard issue counts."""

    active_product = Product.catalogue_status == "active"
    if issue_key == "missing_description":
        return and_(
            active_product,
            _blank(Product.short_description),
            _blank(Product.description),
        )
    if issue_key == "missing_image":
        return and_(active_product, Product.id.in_(_missing_image_product_ids()))
    if issue_key == "missing_seo":
        return and_(
            active_product,
            or_(_blank(Product.meta_title), _blank(Product.meta_description)),
        )
    raise KeyError(issue_key)


def _missing_image_product_ids():
    """Return active parents with no safe, resolvable parent or variation source."""

    cache_key = "_missing_catalogue_image_product_ids"
    if has_request_context() and hasattr(g, cache_key):
        return getattr(g, cache_key)
    products = (
        Product.query.options(
            selectinload(Product.images),
            selectinload(Product.variations).selectinload(Variation.images),
            selectinload(Product.variations).selectinload(Variation.attributes),
        )
        .filter_by(catalogue_status="active")
        .all()
    )
    result = tuple(
        product.id
        for product in products
        if resolve_product_catalogue_image(product) is None
    )
    if has_request_context():
        setattr(g, cache_key, result)
    return result


def _operation_view(operation):
    finished_at = operation.finished_at
    elapsed = None
    if operation.started_at and finished_at:
        elapsed = max(0, int((finished_at - operation.started_at).total_seconds()))
    return {
        "id": operation.id,
        "type": operation.operation_type,
        "label": OPERATION_LABELS.get(
            operation.operation_type, operation.operation_type.replace("_", " ").title()
        ),
        "status": operation.status,
        "started_at": operation.started_at,
        "finished_at": finished_at,
        "elapsed_seconds": elapsed,
        "products_attempted": operation.products_attempted,
        "products_succeeded": operation.products_succeeded,
        "products_failed": operation.products_failed,
        "recovery_state": operation.recovery_state,
        "requires_attention": (
            operation.status in {"failed", "partial", "interrupted"}
            or operation.recovery_state not in {None, "none"}
        ),
    }


def build_dashboard_data():
    """Build genuine dashboard facts without mutating application state."""

    summary = {
        "collections": Collection.query.count(),
        "products": Product.query.count(),
        "variations": Variation.query.count(),
        "active_products": Product.query.filter_by(catalogue_status="active").count(),
        "missing_products": Product.query.filter_by(catalogue_status="missing").count(),
        "active_variations": Variation.query.filter_by(
            catalogue_status="active"
        ).count(),
        "missing_variations": Variation.query.filter_by(
            catalogue_status="missing"
        ).count(),
        "overrides": Product.query.filter(
            Product.override_json_path.isnot(None),
            func.trim(Product.override_json_path) != "",
        ).count(),
    }

    missing_descriptions = Product.query.filter(
        metadata_issue_condition("missing_description")
    ).count()
    missing_images = Product.query.filter(
        metadata_issue_condition("missing_image")
    ).count()
    missing_seo = Product.query.filter(
        metadata_issue_condition("missing_seo")
    ).count()
    metadata_issues = {
        "missing_descriptions": missing_descriptions,
        "missing_images": missing_images,
        "missing_seo": missing_seo,
        "total": missing_descriptions + missing_images + missing_seo,
    }
    metadata_issues["categories"] = [
        {
            "key": key,
            "label": definition["label"],
            "count": metadata_issues[definition["count_key"]],
        }
        for key, definition in METADATA_ISSUE_DEFINITIONS.items()
    ]

    total_items = summary["products"] + summary["variations"]
    active_items = summary["active_products"] + summary["active_variations"]
    missing_items = summary["missing_products"] + summary["missing_variations"]
    availability_percent = round((active_items / total_items) * 100) if total_items else 0

    operations = (
        CatalogueOperation.query.order_by(
            CatalogueOperation.started_at.desc(), CatalogueOperation.id.desc()
        )
        .limit(8)
        .all()
    )
    recent_operations = [_operation_view(operation) for operation in operations]
    failed_operations = CatalogueOperation.query.filter(
        CatalogueOperation.status.in_({"failed", "partial", "interrupted"})
    ).count()
    recovery_required = CatalogueOperation.query.filter(
        and_(
            CatalogueOperation.recovery_state.isnot(None),
            CatalogueOperation.recovery_state != "none",
        )
    ).count()

    active_operation = get_active_operation()
    if active_operation:
        active_operation = {
            **active_operation,
            "label": OPERATION_LABELS.get(
                active_operation["operation_type"],
                active_operation["operation_type"].replace("_", " ").title(),
            ),
        }

    variation_count = (
        select(func.count(Variation.id))
        .where(Variation.product_id == Product.id)
        .correlate(Product)
        .scalar_subquery()
    )
    product_rows = (
        db.session.query(Product, variation_count.label("variation_count"))
        .options(
            joinedload(Product.collection),
            selectinload(Product.assets),
            selectinload(Product.images),
            selectinload(Product.variations).selectinload(Variation.assets),
            selectinload(Product.variations).selectinload(Variation.images),
            selectinload(Product.variations).selectinload(Variation.attributes),
        )
        .order_by(Product.local_updated_at.desc(), Product.id.desc())
        .limit(6)
        .all()
    )
    recent_products = [
        {
            "id": product.id,
            "sku": product.sku,
            "title": product.title,
            "product_type": product.product_type,
            "catalogue_status": product.catalogue_status,
            "collection": collection_display_name(product.collection) if product.collection else "Unassigned",
            "variation_count": count,
            "updated_at": product.local_updated_at,
            "thumbnail": product_thumbnail_url(product),
            "thumbnail_alt": primary_image_alt(product),
            "has_override": bool(product.override_json_path),
            "publishing_intent": projected_publishing_intent(product.published),
        }
        for product, count in product_rows
    ]

    needs_attention = bool(
        missing_items
        or metadata_issues["total"]
        or failed_operations
        or recovery_required
    )
    if not total_items:
        health_label = "No catalogue data"
    elif needs_attention:
        health_label = "Needs attention"
    else:
        health_label = "Healthy"

    operation_counts = {
        "succeeded": sum(item["status"] == "succeeded" for item in recent_operations),
        "failed": sum(item["status"] == "failed" for item in recent_operations),
        "partial": sum(
            item["status"] in {"partial", "interrupted"}
            for item in recent_operations
        ),
        "recovery_required": sum(
            item["recovery_state"] not in {None, "none"}
            for item in recent_operations
        ),
    }

    return {
        "generated_at": datetime.now(UTC),
        "summary": summary,
        "health": {
            "label": health_label,
            "total_items": total_items,
            "active_items": active_items,
            "missing_items": missing_items,
            "availability_percent": availability_percent,
        },
        "metadata_issues": metadata_issues,
        "scanner": {
            "active": active_operation,
            "recent_counts": operation_counts,
            "latest": recent_operations[0] if recent_operations else None,
        },
        "attention": {
            "missing_products": summary["missing_products"],
            "missing_variations": summary["missing_variations"],
            "metadata_issues": metadata_issues["total"],
            "failed_operations": failed_operations,
            "recovery_required": recovery_required,
        },
        "recent_operations": recent_operations,
        "recent_products": recent_products,
        "woo_status": "Not configured",
    }
