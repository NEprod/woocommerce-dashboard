"""Read-only composition for Product Detail and authoritative metadata editors."""

from __future__ import annotations

import json
import math
import os
from decimal import Decimal
from pathlib import Path, PurePosixPath

from flask import url_for
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from app import db
from app.catalogue_images import (
    primary_image_alt,
    product_image_diagnostics,
    product_thumbnail_url,
    variation_image_diagnostics,
)
from app.collection_identity import collection_display_name
from app.models import (
    CatalogueOperation,
    CatalogueOperationItem,
    Collection,
    Product,
    Settings,
    Variation,
)
from app.product_info import FIELD_BY_KEY, FIELD_INVENTORY, validate_product_info
from app.publishing import resolved_publishing_intent
from app.utils.json_utils import merge_product_json


MAX_METADATA_BYTES = 1024 * 1024
EDITOR_FIELDS = tuple(
    field for field in FIELD_INVENTORY if field["collection_allowed"]
)


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


def _inside(path: Path, root: Path):
    try:
        return os.path.commonpath((str(path), str(root))) == str(root)
    except ValueError:
        return False


def _source_candidate(product: Product, kind: str):
    if kind == "shared":
        values = (
            product.shared_json_relpath,
            product.collection.shared_json_relpath if product.collection else None,
            (
                f"{product.collection.source_relpath}/product_info.json"
                if product.collection and product.collection.source_relpath
                else None
            ),
        )
    else:
        values = (
            product.override_json_relpath,
            f"{product.source_relpath}/product_info.json" if product.source_relpath else None,
        )
    for value in values:
        parts = _portable_parts(value)
        if parts:
            return PurePosixPath(*parts).as_posix()
    return None


def metadata_source(product: Product, kind: str):
    """Resolve one metadata source through portable identity and catalogue confinement."""

    if kind not in {"shared", "override"}:
        raise ValueError("Unsupported metadata source")
    reference = _source_candidate(product, kind)
    root = _catalogue_root()
    if root and not reference:
        asset = next(
            (
                item
                for item in sorted(product.assets, key=lambda value: value.id or 0, reverse=True)
                if item.kind == "info" and item.label == kind
            ),
            None,
        )
        if asset and asset.source_relpath and _portable_parts(asset.source_relpath):
            reference = PurePosixPath(*_portable_parts(asset.source_relpath)).as_posix()
        elif asset and asset.path:
            try:
                asset_path = Path(asset.path).resolve(strict=True)
                if asset_path.is_file() and _inside(asset_path, root):
                    reference = asset_path.relative_to(root).as_posix()
            except (OSError, RuntimeError, ValueError):
                reference = None
    path = None
    if root and reference:
        try:
            candidate = root.joinpath(*PurePosixPath(reference).parts).resolve(strict=True)
            if candidate.is_file() and _inside(candidate, root):
                path = candidate
        except (OSError, RuntimeError):
            path = None
    data = {}
    error = None
    if path:
        try:
            if path.stat().st_size > MAX_METADATA_BYTES:
                raise ValueError("Metadata source exceeds the supported size limit.")
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Metadata source must contain a JSON object.")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            error = str(exc)
            data = {}
    elif reference:
        error = "Source file is not currently available in the mounted catalogue."
    else:
        error = "No source file is registered for this metadata layer."
    validation = validate_product_info(data, "collection" if kind == "shared" else "override")
    return {
        "kind": kind,
        "label": "Collection metadata" if kind == "shared" else "Product override",
        "reference": reference,
        "path": path,
        "exists": path is not None,
        "data": data,
        "text": json.dumps(data, ensure_ascii=False, indent=2),
        "error": error,
        "validation": validation.to_dict(),
    }


def resolved_metadata(product: Product, shared: dict, override: dict):
    folder_name = PurePosixPath(product.source_relpath or "").name
    return merge_product_json(shared, override, path=folder_name)


def _display(value):
    if value is None:
        return "Not set"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def metadata_comparison(shared, override, resolved):
    known = [field["key"] for field in EDITOR_FIELDS]
    unknown = sorted((set(shared) | set(override) | set(resolved)) - set(known))
    rows = []
    for key in known + unknown:
        shared_set = key in shared
        override_set = key in override
        resolved_set = key in resolved
        if not (shared_set or override_set or resolved_set):
            continue
        field = FIELD_BY_KEY.get(key, {})
        rows.append(
            {
                "key": key,
                "label": (
                    "Publishing intent"
                    if key == "live"
                    else key.replace("_", " ").title()
                ),
                "collection": shared.get(key),
                "collection_display": _display(shared.get(key)) if shared_set else "Not set",
                "override": override.get(key),
                "override_display": _display(override.get(key)) if override_set else "Inherited value",
                "resolved": resolved.get(key),
                "resolved_display": _display(resolved.get(key)) if resolved_set else "Missing",
                "state": "overridden" if override_set else "inherited" if shared_set else "scanner-generated",
                "implementation_status": field.get("implementation_status", "Unknown field preserved."),
            }
        )
    return rows


def _money(value):
    return f"{Decimal(value):.2f}" if value is not None else None


def _public_image_rows(rows, route_name, owner_id):
    result = []
    for row in rows:
        item = {key: value for key, value in row.items() if key != "path"}
        if row.get("path"):
            item["preview_url"] = url_for(route_name, **owner_id, index=row["index"])
        else:
            item["preview_url"] = None
        result.append(item)
    return result


def _variation_view(variation):
    attributes = sorted(
        variation.attributes,
        key=lambda item: (
            item.position if item.position is not None else 2**31,
            item.id or 0,
        ),
    )
    diagnostics = variation_image_diagnostics(variation)
    return {
        "id": variation.id,
        "sku": variation.sku,
        "status": variation.catalogue_status,
        "regular_price": _money(variation.regular_price),
        "sale_price": _money(variation.sale_price),
        "stock_quantity": variation.stock_quantity,
        "updated_at": variation.local_updated_at,
        "attributes": [{"name": item.name, "value": item.value} for item in attributes],
        "images": _public_image_rows(
            diagnostics,
            "main.catalogue_variation_gallery_image",
            {"variation_id": variation.id},
        ),
    }


def variation_page(product_id: int, page=1, per_page=24):
    query = Variation.query.filter_by(product_id=product_id).order_by(
        Variation.menu_order.asc(), Variation.id.asc()
    )
    total = query.count()
    pages = max(1, math.ceil(total / per_page))
    page = min(max(page, 1), pages)
    records = (
        query.options(
            selectinload(Variation.images),
            selectinload(Variation.attributes),
            selectinload(Variation.assets),
            selectinload(Variation.product).selectinload(Product.assets),
            selectinload(Variation.product).selectinload(Product.images),
        )
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return {
        "items": [_variation_view(item) for item in records],
        "pagination": {
            "page": page,
            "pages": pages,
            "per_page": per_page,
            "total": total,
        },
    }


def product_workspace(product: Product):
    from app.product_relationships import relationship_workspace

    shared_source = metadata_source(product, "shared")
    override_source = metadata_source(product, "override")
    override_data = override_source["data"] if override_source["exists"] else {}
    resolved = resolved_metadata(product, shared_source["data"], override_data)
    publishing_intent = resolved_publishing_intent(
        shared_source["data"], override_data, resolved
    )
    variation_data = variation_page(product.id)
    minimum, maximum = db.session.query(
        func.min(func.coalesce(Variation.sale_price, Variation.regular_price)),
        func.max(func.coalesce(Variation.sale_price, Variation.regular_price)),
    ).filter(Variation.product_id == product.id).one()
    if minimum is None and (product.sale_price is not None or product.regular_price is not None):
        minimum = maximum = product.sale_price or product.regular_price
    operations = (
        db.session.query(CatalogueOperationItem, CatalogueOperation)
        .join(CatalogueOperation, CatalogueOperation.id == CatalogueOperationItem.operation_id)
        .filter(CatalogueOperationItem.sku == product.sku)
        .order_by(CatalogueOperation.started_at.desc(), CatalogueOperationItem.id.desc())
        .limit(8)
        .all()
    )
    parent_images = _public_image_rows(
        product_image_diagnostics(product),
        "main.catalogue_product_gallery_image",
        {"product_id": product.id},
    )
    primary_preview = next(
        (image for image in parent_images if image.get("preview_url")), None
    )
    if primary_preview is None:
        for variation in variation_data["items"]:
            primary_preview = next(
                (image for image in variation["images"] if image.get("preview_url")),
                None,
            )
            if primary_preview:
                primary_preview = {**primary_preview, "variation_fallback": True}
                break
    return {
        "product": product,
        "collection_display_name": (
            collection_display_name(product.collection)
            if product.collection
            else "Unassigned"
        ),
        "shared": shared_source,
        "override": override_source,
        "resolved": resolved,
        "publishing_intent": publishing_intent,
        "comparison": metadata_comparison(shared_source["data"], override_data, resolved),
        "parent_images": parent_images,
        "primary_preview": primary_preview,
        "variations": variation_data["items"],
        "variation_pagination": variation_data["pagination"],
        "price_min": _money(minimum),
        "price_max": _money(maximum),
        "operations": [
            {
                "id": operation.id,
                "type": operation.operation_type,
                "status": item.status,
                "started_at": operation.started_at,
                "finished_at": operation.finished_at,
            }
            for item, operation in operations
        ],
        "relationships": relationship_workspace(product),
    }


def editor_workspace(product: Product, kind: str):
    shared = metadata_source(product, "shared")
    override = metadata_source(product, "override")
    authored = shared if kind == "shared" else override
    override_data = override["data"] if override["exists"] else {}
    resolved = resolved_metadata(product, shared["data"], override_data)
    publishing_intent = resolved_publishing_intent(
        shared["data"], override_data, resolved
    )
    collection_publishing_intent = resolved_publishing_intent(
        shared["data"], {}, shared["data"]
    )
    product_images = _public_image_rows(
        product_image_diagnostics(product),
        "main.catalogue_product_gallery_image",
        {"product_id": product.id},
    )
    affected_total = (
        Product.query.filter_by(collection_id=product.collection_id).count()
        if product.collection_id
        else 1
    )
    preview_products = (
        Product.query.options(
            selectinload(Product.images),
            selectinload(Product.variations),
        )
        .filter_by(collection_id=product.collection_id)
        .order_by(Product.title.asc(), Product.id.asc())
        .limit(6)
        .all()
        if product.collection_id
        else [product]
    )
    preview = [
        {
            "id": item.id,
            "sku": item.sku,
            "title": item.title,
            "thumbnail": product_thumbnail_url(item),
            "thumbnail_alt": primary_image_alt(item),
        }
        for item in preview_products
    ]
    return {
        "product": product,
        "collection_display_name": (
            collection_display_name(product.collection)
            if product.collection
            else "Unassigned"
        ),
        "shared_product_title": shared["data"].get("title"),
        "kind": kind,
        "authored": authored,
        "shared": shared,
        "override": override,
        "resolved": resolved,
        "publishing_intent": publishing_intent,
        "collection_publishing_intent": collection_publishing_intent,
        "comparison": metadata_comparison(shared["data"], override_data, resolved),
        "fields": EDITOR_FIELDS,
        "affected_total": affected_total,
        "variation_count": Variation.query.filter_by(product_id=product.id).count(),
        "affected_preview": preview,
        "product_images": product_images,
        "variation_image_preview": (
            variation_page(product.id, page=1, per_page=6)["items"]
            if product.product_type == "variable"
            else []
        ),
    }


def affected_products_page(collection: Collection, page: int, per_page: int):
    query = Product.query.options(
        selectinload(Product.images),
        selectinload(Product.variations).selectinload(Variation.images),
        selectinload(Product.variations).selectinload(Variation.attributes),
    ).filter_by(collection_id=collection.id).order_by(Product.title.asc(), Product.id.asc())
    total = query.count()
    pages = max(1, math.ceil(total / per_page))
    page = min(max(page, 1), pages)
    products = query.offset((page - 1) * per_page).limit(per_page).all()
    return {
        "items": [
            {
                "id": product.id,
                "sku": product.sku,
                "title": product.title,
                "type": product.product_type,
                "variation_count": len(product.variations),
                "thumbnail": product_thumbnail_url(product),
                "thumbnail_alt": primary_image_alt(product),
                "detail_url": url_for("main.product_detail", product_id=product.id),
            }
            for product in products
        ],
        "pagination": {
            "page": page,
            "pages": pages,
            "per_page": per_page,
            "total": total,
        },
    }
