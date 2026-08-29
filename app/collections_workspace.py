"""Server-backed presentation data for the Phase 2 Collections workspace."""

from __future__ import annotations

import json
import math
import os
from collections import Counter
from datetime import datetime
from pathlib import Path, PurePosixPath

from flask import url_for
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import selectinload

from app import db
from app.catalogue_images import primary_image_alt, projected_image_coverage
from app.collection_identity import collection_display_name, collection_source_provenance
from app.dashboard import OPERATION_LABELS
from app.models import (
    CatalogueOperation,
    Collection,
    Product,
    ProductAsset,
    Settings,
    Variation,
)
from app.product_info import validate_product_info
from app.publishing import projected_publishing_intent


COLLECTION_TYPES = {"Simple", "Variable Collection", "Single Variable"}
HEALTH_FILTERS = {"healthy", "issues", "missing", "invalid"}
INTENT_FILTERS = {"published", "draft", "mixed", "unresolved"}
LIFECYCLE_FILTERS = {"active", "missing", "archived"}
BOOLEAN_FILTERS = {"yes", "no"}
IMAGE_FILTERS = {"complete", "missing", "fallback", "corrupt"}
SORTS = {"name", "products", "variations", "issues", "updated"}
ORDERS = {"asc", "desc"}
PAGE_SIZES = {25, 50, 100}
PRODUCT_PAGE_SIZES = {12, 24, 50}
MAX_METADATA_BYTES = 1024 * 1024


def _blank(column):
    return or_(column.is_(None), func.trim(column) == "")


def parse_collection_filters(args):
    values = {
        "q": args.get("q", "").strip()[:191],
        "type": args.get("type", "").strip(),
        "health": args.get("health", "").strip(),
        "intent": args.get("intent", "").strip(),
        "lifecycle": args.get("lifecycle", "").strip(),
        "overrides": args.get("overrides", "").strip(),
        "images": args.get("images", "").strip(),
        "sort": args.get("sort", "name").strip(),
        "order": args.get("order", "asc").strip(),
    }
    if values["type"] and values["type"] not in COLLECTION_TYPES:
        raise ValueError("Unsupported collection type filter")
    if values["health"] and values["health"] not in HEALTH_FILTERS:
        raise ValueError("Unsupported metadata health filter")
    if values["intent"] and values["intent"] not in INTENT_FILTERS:
        raise ValueError("Unsupported publishing intent filter")
    if values["lifecycle"] and values["lifecycle"] not in LIFECYCLE_FILTERS:
        raise ValueError("Unsupported catalogue lifecycle filter")
    if values["overrides"] and values["overrides"] not in BOOLEAN_FILTERS:
        raise ValueError("Unsupported override filter")
    if values["images"] and values["images"] not in IMAGE_FILTERS:
        raise ValueError("Unsupported image filter")
    if values["sort"] not in SORTS or values["order"] not in ORDERS:
        raise ValueError("Unsupported collection sorting")
    try:
        values["page"] = max(1, int(args.get("page", "1")))
        values["per_page"] = int(args.get("per_page", "25"))
    except ValueError as error:
        raise ValueError("Invalid collection pagination") from error
    if values["per_page"] not in PAGE_SIZES:
        raise ValueError("Unsupported collection page size")
    return values


def parse_product_pagination(args):
    try:
        page = max(1, int(args.get("products_page", "1")))
        per_page = int(args.get("products_per_page", "12"))
    except ValueError as error:
        raise ValueError("Invalid affected-product pagination") from error
    if per_page not in PRODUCT_PAGE_SIZES:
        raise ValueError("Unsupported affected-product page size")
    issue = args.get("product_issue", "").strip()
    if issue not in {"", "metadata", "images", "overrides"}:
        raise ValueError("Unsupported affected-product issue filter")
    return {"page": page, "per_page": per_page, "issue": issue}


def _portable_parts(value):
    if not value:
        return None
    path = PurePosixPath(str(value).replace("\\", "/"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path.parts


def _catalogue_root():
    settings = Settings.query.first()
    if not settings or not settings.product_folder:
        return None
    try:
        root = Path(settings.product_folder).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return root if root.is_dir() else None


def _inside(path, root):
    try:
        return os.path.commonpath((str(path), str(root))) == str(root)
    except ValueError:
        return False


def collection_metadata_source(collection, root):
    reference = collection.shared_json_relpath
    if not _portable_parts(reference) and collection.source_relpath:
        reference = f"{collection.source_relpath}/product_info.json"
    parts = _portable_parts(reference)
    reference = PurePosixPath(*parts).as_posix() if parts else None
    path = None
    if root and parts:
        try:
            candidate = root.joinpath(*parts).resolve(strict=True)
            if candidate.is_file() and _inside(candidate, root):
                path = candidate
        except (OSError, RuntimeError):
            path = None
    data = {}
    parse_error = False
    if path:
        try:
            if path.stat().st_size > MAX_METADATA_BYTES:
                raise ValueError("metadata size")
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("metadata root")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            data = {}
            parse_error = True
    validation = validate_product_info(data, "collection").to_dict()
    unsupported = bool(data.get("collection_type")) and data.get("collection_type") not in COLLECTION_TYPES
    completeness = sum(
        not data.get(key)
        for key in ("title", "short_description", "meta_title", "meta_description")
    )
    if path is None:
        state, label = "missing", "Missing metadata"
    elif parse_error or not validation["valid"] or unsupported:
        state, label = "invalid", "Invalid metadata"
    elif validation["warnings"] or completeness:
        state, label = "issues", "Has issues"
    else:
        state, label = "healthy", "Valid metadata"
    return {
        "reference": reference,
        "exists": path is not None,
        "data": data,
        "parse_error": parse_error,
        "validation": validation,
        "unsupported_type": unsupported,
        "state": state,
        "label": label,
        "issue_count": len(validation["errors"]) + len(validation["warnings"]) + completeness,
        "modified_at": path.stat().st_mtime if path else None,
    }


def _variation_count_subquery():
    return (
        select(func.count(Variation.id))
        .join(Product, Variation.product_id == Product.id)
        .where(Product.collection_id == Collection.id)
        .correlate(Collection)
        .scalar_subquery()
    )


def _product_count(condition=None):
    query = select(func.count(Product.id)).where(Product.collection_id == Collection.id)
    if condition is not None:
        query = query.where(condition)
    return query.correlate(Collection).scalar_subquery()


def _aggregate_rows(query_text=""):
    variation_count = _variation_count_subquery()
    override_condition = or_(~_blank(Product.override_json_path), ~_blank(Product.override_json_relpath))
    metadata_condition = or_(
        and_(_blank(Product.short_description), _blank(Product.description)),
        _blank(Product.meta_title),
        _blank(Product.meta_description),
    )
    last_updated = (
        select(func.max(Product.local_updated_at))
        .where(Product.collection_id == Collection.id)
        .correlate(Collection)
        .scalar_subquery()
    )
    query = db.session.query(
        Collection,
        _product_count().label("product_count"),
        variation_count.label("variation_count"),
        _product_count(Product.catalogue_status == "active").label("active_count"),
        _product_count(Product.catalogue_status == "missing").label("missing_count"),
        _product_count(Product.catalogue_status == "archived").label("archived_count"),
        _product_count(Product.published.is_(True)).label("published_count"),
        _product_count(Product.published.is_(False)).label("draft_count"),
        _product_count(Product.published.is_(None)).label("unresolved_count"),
        _product_count(override_condition).label("override_count"),
        _product_count(metadata_condition).label("product_metadata_issues"),
        last_updated.label("last_updated"),
    )
    if query_text:
        pattern = f"%{query_text}%"
        product_match = (
            select(Product.id)
            .where(
                Product.collection_id == Collection.id,
                or_(Product.title.ilike(pattern), Product.sku.ilike(pattern)),
            )
            .exists()
        )
        query = query.filter(
            or_(
                Collection.name.ilike(pattern),
                Collection.sku_prefix.ilike(pattern),
                product_match,
            )
        )
    return query.order_by(Collection.id.asc()).all()


def _row_view(row, metadata):
    collection = row[0]
    display_name = collection_display_name(collection)
    product_count = int(row.product_count or 0)
    published = int(row.published_count or 0)
    draft = int(row.draft_count or 0)
    unresolved = int(row.unresolved_count or 0)
    if published and draft:
        intent_state = "mixed"
    elif published and not draft and not unresolved:
        intent_state = "published"
    elif draft and not published and not unresolved:
        intent_state = "draft"
    else:
        intent_state = "unresolved"
    return {
        "collection": collection,
        "id": collection.id,
        "name": display_name,
        "title": display_name,
        "shared_product_title": metadata["data"].get("title"),
        "source_provenance": collection_source_provenance(collection),
        "type": collection.collection_type or metadata["data"].get("collection_type") or "Not projected",
        "sku_prefix": collection.sku_prefix,
        "source_reference": metadata["reference"],
        "metadata": metadata,
        "product_count": product_count,
        "variation_count": int(row.variation_count or 0),
        "active_count": int(row.active_count or 0),
        "missing_count": int(row.missing_count or 0),
        "archived_count": int(row.archived_count or 0),
        "published_count": published,
        "draft_count": draft,
        "unresolved_count": unresolved,
        "intent_state": intent_state,
        "override_count": int(row.override_count or 0),
        "product_metadata_issues": int(row.product_metadata_issues or 0),
        "issue_count": metadata["issue_count"] + int(row.product_metadata_issues or 0),
        "last_updated": row.last_updated,
        "detail_url": url_for("main.collection_detail", collection_id=collection.id),
        "edit_url": url_for("main.collection_metadata_edit", collection_id=collection.id),
        "products_url": url_for("main.products", collection=collection.id),
    }


def _load_products(collection_ids):
    if not collection_ids:
        return {}
    products = (
        Product.query.options(selectinload(Product.assets), selectinload(Product.images))
        .filter(Product.collection_id.in_(collection_ids))
        .order_by(Product.collection_id.asc(), Product.title.asc(), Product.id.asc())
        .all()
    )
    grouped = {collection_id: [] for collection_id in collection_ids}
    for product in products:
        grouped.setdefault(product.collection_id, []).append(product)
    return grouped


def _apply_image_coverage(items, product_groups):
    for item in items:
        coverages = [
            (product, projected_image_coverage(product))
            for product in product_groups.get(item["id"], [])
        ]
        parent = [(product, coverage) for product, coverage in coverages if coverage["usable_parent"]]
        fallback = [(product, coverage) for product, coverage in coverages if coverage["variation_fallback"]]
        representative = parent[0] if parent else fallback[0] if fallback else None
        item["image"] = {
            "usable_count": sum(coverage["usable"] for _product, coverage in coverages),
            "missing_count": sum(coverage["missing"] for _product, coverage in coverages),
            "parent_count": len(parent),
            "fallback_count": len(fallback),
            "source_only_count": sum(coverage["source_only"] for _product, coverage in coverages),
            "url_only_count": sum(coverage["url_only"] for _product, coverage in coverages),
            "corrupt_count": sum(coverage["corrupt"] for _product, coverage in coverages),
            "parent_gallery_count": sum(coverage["parent_gallery_count"] for _product, coverage in coverages),
            "variation_source_count": sum(coverage["variation_source_count"] for _product, coverage in coverages),
            "thumbnail": representative[1]["thumbnail"] if representative else None,
            "thumbnail_alt": primary_image_alt(representative[0]) if representative else "",
            "state": "missing" if coverages and all(coverage["missing"] for _product, coverage in coverages) else "issues" if any(coverage["missing"] or coverage["corrupt"] or coverage["variation_fallback"] for _product, coverage in coverages) else "complete",
        }
        item["issue_count"] = (
            item["metadata"]["issue_count"]
            + item["product_metadata_issues"]
            + item["image"]["missing_count"]
            + item["image"]["corrupt_count"]
        )


def _matches_without_images(item, filters):
    if filters["type"] and item["type"] != filters["type"]:
        return False
    if filters["health"] and item["metadata"]["state"] != filters["health"]:
        return False
    if filters["intent"] and item["intent_state"] != filters["intent"]:
        return False
    if filters["lifecycle"] and item[f"{filters['lifecycle']}_count"] < 1:
        return False
    if filters["overrides"] == "yes" and not item["override_count"]:
        return False
    if filters["overrides"] == "no" and item["override_count"]:
        return False
    return True


def _matches_images(item, filters):
    if filters["images"] == "complete" and item["image"]["state"] != "complete":
        return False
    if filters["images"] == "missing" and not item["image"]["missing_count"]:
        return False
    if filters["images"] == "fallback" and not item["image"]["fallback_count"]:
        return False
    if filters["images"] == "corrupt" and not item["image"]["corrupt_count"]:
        return False
    return True


def _page_url(endpoint, filters, page):
    values = {key: value for key, value in filters.items() if value not in {"", None}}
    values["page"] = page
    return url_for(endpoint, **values)


def build_collections_browser(filters):
    root = _catalogue_root()
    rows = _aggregate_rows()
    items = [
        _row_view(row, collection_metadata_source(row[0], root))
        for row in rows
    ]
    display_counts = Counter(item["name"].casefold() for item in items)
    for item in items:
        item["show_provenance"] = display_counts[item["name"].casefold()] > 1
    if filters["q"]:
        term = filters["q"].casefold()
        product_collection_ids = {
            collection_id
            for (collection_id,) in db.session.query(Product.collection_id)
            .filter(
                or_(
                    Product.title.ilike(f"%{filters['q']}%"),
                    Product.sku.ilike(f"%{filters['q']}%"),
                )
            )
            .distinct()
            .all()
        }
        items = [
            item
            for item in items
            if term in item["name"].casefold()
            or term in (item["source_provenance"] or "").casefold()
            or term in (item["shared_product_title"] or "").casefold()
            or term in (item["sku_prefix"] or "").casefold()
            or item["id"] in product_collection_ids
        ]
    items = [item for item in items if _matches_without_images(item, filters)]
    needs_all_image_facts = bool(filters["images"] or filters["sort"] == "issues")
    if needs_all_image_facts:
        groups = _load_products([item["id"] for item in items])
        _apply_image_coverage(items, groups)
        items = [item for item in items if _matches_images(item, filters)]
    sort_key = {
        "name": lambda item: (item["name"].casefold(), item["id"]),
        "products": lambda item: (item["product_count"], item["name"].casefold()),
        "variations": lambda item: (item["variation_count"], item["name"].casefold()),
        "issues": lambda item: (item.get("issue_count", item["metadata"]["issue_count"] + item["product_metadata_issues"]), item["name"].casefold()),
        "updated": lambda item: (item["last_updated"] is not None, item["last_updated"], item["name"].casefold()),
    }[filters["sort"]]
    items.sort(key=sort_key, reverse=filters["order"] == "desc")
    total = len(items)
    pages = max(1, math.ceil(total / filters["per_page"]))
    page = min(filters["page"], pages)
    start = (page - 1) * filters["per_page"]
    page_items = items[start : start + filters["per_page"]]
    if not needs_all_image_facts:
        groups = _load_products([item["id"] for item in page_items])
        _apply_image_coverage(page_items, groups)
    active_filters = any(filters[key] for key in ("q", "type", "health", "intent", "lifecycle", "overrides", "images"))
    return {
        "items": page_items,
        "filters": {**filters, "page": page},
        "summary": {
            "collections": len(rows),
            "products": sum(item["product_count"] for item in items),
            "variations": sum(item["variation_count"] for item in items),
            "issues": sum(item["issue_count"] for item in items),
        },
        "pagination": {
            "page": page,
            "pages": pages,
            "per_page": filters["per_page"],
            "total": total,
            "from": start + 1 if total else 0,
            "to": min(start + filters["per_page"], total),
            "previous_url": _page_url("main.collections", filters, page - 1) if page > 1 else None,
            "next_url": _page_url("main.collections", filters, page + 1) if page < pages else None,
        },
        "empty_reason": "filtered" if not total and active_filters else "catalogue" if not total else None,
    }


def _affected_product_item(product, variation_count):
    coverage = projected_image_coverage(product)
    metadata_issue_count = sum(
        (
            not (product.short_description or product.description),
            not product.meta_title,
            not product.meta_description,
        )
    )
    has_override = bool(product.override_json_path or product.override_json_relpath)
    return {
        "id": product.id,
        "title": product.title,
        "sku": product.sku,
        "type": product.product_type,
        "catalogue_status": product.catalogue_status or "active",
        "publishing_intent": projected_publishing_intent(product.published),
        "has_override": has_override,
        "variation_count": int(variation_count or 0),
        "metadata_issue_count": metadata_issue_count,
        "image": coverage,
        "updated_at": product.local_updated_at,
        "detail_url": url_for("main.product_detail", product_id=product.id),
    }


def _affected_products(collection_id, pagination):
    variation_count = (
        select(func.count(Variation.id))
        .where(Variation.product_id == Product.id)
        .correlate(Product)
        .scalar_subquery()
    )
    image_issue_ids = None
    if pagination["issue"] == "images":
        image_candidates = (
            Product.query.options(selectinload(Product.assets), selectinload(Product.images))
            .filter(Product.collection_id == collection_id)
            .all()
        )
        image_issue_ids = [
            product.id
            for product in image_candidates
            if (
                (coverage := projected_image_coverage(product))["missing"]
                or coverage["corrupt"]
            )
        ]
    base = db.session.query(Product).filter(Product.collection_id == collection_id)
    if pagination["issue"] == "metadata":
        base = base.filter(or_(_blank(Product.meta_title), _blank(Product.meta_description), and_(_blank(Product.short_description), _blank(Product.description))))
    if pagination["issue"] == "overrides":
        base = base.filter(or_(~_blank(Product.override_json_path), ~_blank(Product.override_json_relpath)))
    if image_issue_ids is not None:
        base = base.filter(Product.id.in_(image_issue_ids))
    total = base.count()
    pages = max(1, math.ceil(total / pagination["per_page"]))
    page = min(pagination["page"], pages)
    rows_query = db.session.query(Product, variation_count.label("variation_count")).filter(Product.collection_id == collection_id)
    if pagination["issue"] == "metadata":
        rows_query = rows_query.filter(or_(_blank(Product.meta_title), _blank(Product.meta_description), and_(_blank(Product.short_description), _blank(Product.description))))
    if pagination["issue"] == "overrides":
        rows_query = rows_query.filter(or_(~_blank(Product.override_json_path), ~_blank(Product.override_json_relpath)))
    if image_issue_ids is not None:
        rows_query = rows_query.filter(Product.id.in_(image_issue_ids))
    rows = (
        rows_query.options(selectinload(Product.assets), selectinload(Product.images))
        .order_by(Product.title.asc(), Product.id.asc())
        .offset((page - 1) * pagination["per_page"])
        .limit(pagination["per_page"])
        .all()
    )
    items = [_affected_product_item(product, count) for product, count in rows]
    return {
        "items": items,
        "pagination": {
            "page": page,
            "pages": pages,
            "per_page": pagination["per_page"],
            "total": total,
            "from": (page - 1) * pagination["per_page"] + 1 if total else 0,
            "to": min(page * pagination["per_page"], total),
        },
        "issue": pagination["issue"],
    }


def _recent_activity(collection, product_skus):
    operations = (
        CatalogueOperation.query.order_by(CatalogueOperation.started_at.desc(), CatalogueOperation.id.desc())
        .limit(100)
        .all()
    )
    result = []
    for operation in operations:
        try:
            scope = json.loads(operation.scope or "{}")
        except (TypeError, json.JSONDecodeError):
            scope = {}
        matches = scope.get("collection_relpath") == collection.source_relpath
        if not matches and scope.get("sku") in product_skus:
            matches = True
        if not matches and operation.operation_type in {"full", "reconstruction"}:
            matches = True
        if not matches:
            continue
        result.append(
            {
                "id": operation.id,
                "label": OPERATION_LABELS.get(operation.operation_type, operation.operation_type.replace("_", " ").title()),
                "status": operation.status,
                "started_at": operation.started_at,
                "finished_at": operation.finished_at,
                "products_attempted": operation.products_attempted,
                "products_succeeded": operation.products_succeeded,
                "products_failed": operation.products_failed,
            }
        )
        if len(result) == 8:
            break
    return result


def build_collection_detail(collection, product_pagination):
    root = _catalogue_root()
    rows = _aggregate_rows()
    row = next(row for row in rows if row[0].id == collection.id)
    item = _row_view(row, collection_metadata_source(collection, root))
    all_products = _load_products([collection.id]).get(collection.id, [])
    _apply_image_coverage([item], {collection.id: all_products})
    affected = _affected_products(collection.id, product_pagination)
    metadata = item["metadata"]["data"]
    collection_default = projected_publishing_intent(metadata.get("live") if "live" in metadata else None)
    item.update(
        metadata_summary={
            "collection_type": metadata.get("collection_type") or collection.collection_type,
            "sku_prefix": metadata.get("sku_prefix") or collection.sku_prefix,
            "shared_product_title": metadata.get("title"),
            "categories_count": len(metadata.get("categories", [])) if isinstance(metadata.get("categories"), list) else 0,
            "tags_count": len(metadata.get("tags", [])) if isinstance(metadata.get("tags"), list) else 0,
            "attributes": metadata.get("attributes") if isinstance(metadata.get("attributes"), dict) else {},
            "image_attributes": metadata.get("image_attributes") if isinstance(metadata.get("image_attributes"), list) else [],
            "modifier_count": len(metadata.get("variation_modifiers", {})) if isinstance(metadata.get("variation_modifiers"), dict) else 0,
            "price": metadata.get("price"),
            "weight": metadata.get("weight"),
            "dimensions": {key: metadata.get(key) for key in ("length", "width", "height")},
            "seo_complete": bool(metadata.get("meta_title") and metadata.get("meta_description")),
            "modified_at": (
                datetime.fromtimestamp(item["metadata"]["modified_at"])
                if item["metadata"]["modified_at"]
                else None
            ),
        },
        collection_default_intent=collection_default,
        affected=affected,
        operations=_recent_activity(collection, {product.sku for product in all_products}),
        can_edit=bool(all_products),
    )
    return item
