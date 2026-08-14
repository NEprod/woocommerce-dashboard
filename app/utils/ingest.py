# app/utils/ingest.py
from typing import List, Dict, Tuple
from decimal import Decimal, InvalidOperation
from datetime import UTC, datetime
import os
import json
import re
from sqlalchemy import select

from app import db
from app.models import (
    Product,
    ProductAttribute,
    Variation,
    VariationAttribute,
    ProductImage,
    VariationImage,
    ProductAsset,
    Settings,
    Collection,
    Category,
    Tag,
    CatalogueOperationItem,
)
from app.utils.discord import notify_ingest_product  # NEW
from app.utils.file_markers import PENDING_FILE, load_pending_scanned
from app.utils.operation_control import sanitize_operation_error

# CSV-style keys
ATTR_NAME_FMT = "Attribute {} name"
ATTR_VALUE_FMT = "Attribute {} value(s)"
ATTR_SLOTS = range(1, 6)

# ---------------- helpers: parsing ----------------


def _all_images(csv_img_field: str):
    if not csv_img_field:
        return []
    return [p.strip() for p in csv_img_field.split(",") if p.strip()]


def _first_or_none(csv_img_field: str):
    if not csv_img_field:
        return None
    parts = [p.strip() for p in csv_img_field.split(",") if p.strip()]
    return parts[0] if parts else None


def _pick(v, default=None):
    return v if v not in (None, "", "None") else default


def _to_decimal(v):
    v = _pick(v)
    if v is None:
        return None
    try:
        return Decimal(str(v).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _to_int(v):
    v = _pick(v)
    if v is None:
        return None
    try:
        return int(str(v).strip())
    except ValueError:
        return None


def _to_bool(v):
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "y"}


def _backorders(v):
    return {"0": "no", "1": "notify", "2": "yes"}.get(str(v), _pick(v))


def _slugify(value):
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return slug or "collection"


def _split_list(value):
    if not value:
        return []
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _portable_relpath(path, root):
    if not path or not root:
        return None
    root = os.path.realpath(root)
    path = os.path.realpath(path)
    try:
        if os.path.commonpath([root, path]) != root:
            return None
    except ValueError:
        return None
    relative = os.path.relpath(path, root)
    return relative.replace(os.sep, "/")


def _load_json_object(path, log=print):
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except Exception as error:
        log(f"⚠️ Failed reading catalogue provenance JSON: {error}", "WARN")
        return {}


_DATE_FORMATS = [
    "%Y-%m-%d",
    "%Y-%m-%d %H:%M:%S",
    "%d/%m/%Y",
    "%d/%m/%Y %H:%M",
    "%m/%d/%Y",
    "%m/%d/%Y %H:%M",
    "%Y-%m-%dT%H:%M:%S",
]


def _to_datetime(v):
    v = _pick(v)
    if v is None:
        return None
    try:
        return datetime.fromisoformat(v)
    except Exception:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt)
        except Exception:
            continue
    return None


# ---------------- helpers: .scanned index ----------------


def _scan_sku_folder_index(root: str, log=print) -> Dict[str, str]:
    sku_to_folder = {}
    if not root or not os.path.isdir(root):
        log(f"⚠️ Invalid product root for .scanned indexing: {root}", "WARN")
        return sku_to_folder
    for dirpath, dirnames, filenames in os.walk(root):
        if PENDING_FILE in filenames:
            pending = load_pending_scanned(dirpath, log=log)
            sku = pending.get("marker", {}).get("sku")
            if sku:
                sku_to_folder[sku] = dirpath
        elif ".scanned" in filenames:
            scanned_path = os.path.join(dirpath, ".scanned")
            try:
                with open(scanned_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                sku = data.get("sku")
                if sku:
                    sku_to_folder[sku] = dirpath
            except Exception as e:
                log(f"⚠️ Failed reading {scanned_path}: {e}", "WARN")
        dirnames[:] = [d for d in dirnames if not d.startswith("_")]
    log(f"🧭 Indexed .scanned files → {len(sku_to_folder)} SKUs", "INFO")
    return sku_to_folder


def _info_paths_for_folder(product_folder: str) -> Dict[str, str]:
    paths = {}
    if not product_folder:
        return paths
    override = os.path.join(product_folder, "product_info.json")
    if os.path.exists(override):
        paths["override"] = override
    parent = os.path.dirname(product_folder) if product_folder != "/" else "/"
    shared = os.path.join(parent, "product_info.json")
    if os.path.exists(shared):
        paths["shared"] = shared
    else:
        same_level = os.path.join(product_folder, "product_info.json")
        if os.path.exists(same_level):
            paths["shared"] = same_level
    return paths


def _source_context(product_folder, catalogue_root, log=print):
    product_relpath = _portable_relpath(product_folder, catalogue_root)
    if not product_relpath:
        return {
            "product_path": product_folder,
            "product_relpath": product_relpath,
        }

    collection_segment = product_relpath.split("/", 1)[0]
    collection_folder = os.path.join(catalogue_root, collection_segment)
    shared_json = os.path.join(collection_folder, "product_info.json")
    shared_data = _load_json_object(shared_json, log=log)
    if not (shared_data.get("collection_type") and shared_data.get("sku_prefix")):
        return {
            "product_path": product_folder,
            "product_relpath": product_relpath,
        }

    own_json = os.path.join(product_folder, "product_info.json")
    override_json = (
        own_json
        if os.path.realpath(product_folder) != os.path.realpath(collection_folder)
        and os.path.isfile(own_json)
        else None
    )

    return {
        "product_path": product_folder,
        "product_relpath": product_relpath,
        "collection_path": collection_folder,
        "collection_relpath": _portable_relpath(collection_folder, catalogue_root),
        "collection_name": os.path.basename(collection_folder),
        "collection_type": shared_data["collection_type"],
        "sku_prefix": shared_data["sku_prefix"],
        "shared_json_path": shared_json,
        "shared_json_relpath": _portable_relpath(shared_json, catalogue_root),
        "override_json_path": override_json,
        "override_json_relpath": _portable_relpath(override_json, catalogue_root),
        "effective_json_path": override_json or shared_json,
        "effective_json_relpath": _portable_relpath(
            override_json or shared_json, catalogue_root
        ),
    }


def _upsert_collection(context):
    source_relpath = context.get("collection_relpath")
    if not source_relpath:
        return None
    collection = Collection.query.filter_by(source_relpath=source_relpath).first()
    if not collection:
        collection = Collection.query.filter_by(
            root_path=context["collection_path"]
        ).first()
    if not collection:
        collection = Collection.query.filter_by(
            sku_prefix=context["sku_prefix"]
        ).first()
    if not collection:
        collection = Collection()
        db.session.add(collection)

    collection.name = context["collection_name"]
    collection.slug = _slugify(source_relpath)
    collection.root_path = context["collection_path"]
    collection.sku_prefix = context["sku_prefix"]
    collection.shared_json_path = context["shared_json_path"]
    collection.collection_type = context["collection_type"]
    collection.source_relpath = source_relpath
    collection.shared_json_relpath = context["shared_json_relpath"]
    return collection


def _upsert_info_assets(
    product: Product, info_paths: Dict[str, str], catalogue_root, log=print
):
    if not info_paths:
        return
    existing = {
        asset.label: asset
        for asset in ProductAsset.query.filter_by(
            product_id=product.id, kind="info"
        ).all()
    }
    for label, path in info_paths.items():
        asset = existing.pop(label, None)
        if not asset:
            asset = ProductAsset(
                product_id=product.id,
                variation_id=None,
                kind="info",
                label=label,
            )
            db.session.add(asset)
        asset.path = path
        asset.source_relpath = _portable_relpath(path, catalogue_root)
        asset.is_primary = label == "shared"
    for stale_asset in existing.values():
        db.session.delete(stale_asset)


# ---------------- mappers ----------------


def _row_to_product_fields(row: Dict, context=None) -> Dict:
    context = context or {}
    prod_type = "variable" if row.get("Type") == "variable" else "simple"
    published = _to_bool(row.get("Published"))
    stock = _to_int(row.get("Stock"))
    return {
        "sku": _pick(row.get("SKU")),
        "title": _pick(row.get("Name"), "-"),
        "product_type": prod_type,
        "collection_type": context.get("collection_type")
        or ("Variable" if row.get("Type") == "variable" else "Simple"),
        "source_relpath": context.get("product_relpath"),
        "shared_json_relpath": context.get("shared_json_relpath"),
        "override_json_relpath": context.get("override_json_relpath"),
        "effective_json_relpath": context.get("effective_json_relpath"),
        "resolved_row_json": json.dumps(row, ensure_ascii=False, sort_keys=True),
        "regular_price": _to_decimal(row.get("Regular price")),
        "sale_price": _to_decimal(row.get("Sale price")),
        "sale_start": _to_datetime(row.get("Date sale price starts")),
        "sale_end": _to_datetime(row.get("Date sale price ends")),
        "weight": _to_decimal(row.get("Weight (g)")),
        "length": _to_decimal(row.get("Length (mm)")),
        "width": _to_decimal(row.get("Width (mm)")),
        "height": _to_decimal(row.get("Height (mm)")),
        "shipping_class": _pick(row.get("Shipping class")),
        "published": published,
        "status": "publish" if published is not False else "draft",
        "featured": _to_bool(row.get("Is featured?")),
        "catalog_visibility": _pick(row.get("Visibility in catalog")),
        "tax_status": _pick(row.get("Tax status")),
        "tax_class": _pick(row.get("Tax class")),
        "in_stock": _to_bool(row.get("In stock?")),
        "manage_stock": stock is not None,
        "stock_quantity": stock,
        "backorders": _backorders(row.get("Backorders allowed?")),
        "sold_individually": _to_bool(row.get("Sold individually?")),
        "reviews_allowed": _to_bool(row.get("Allow customer reviews?")),
        "purchase_note": _pick(row.get("Purchase note")),
        "download_limit": _to_int(row.get("Download limit")),
        "download_expiry_days": _to_int(row.get("Download expiry days")),
        "grouped_products": _pick(row.get("Grouped products")),
        "short_description": _pick(row.get("Short description")),
        "description": _pick(row.get("Description")),
        "external_url": _pick(row.get("External URL")),
        "button_text": _pick(row.get("Button text")),
        "upsell_ids": _pick(row.get("Upsells")),
        "cross_sell_ids": _pick(row.get("Cross-sells")),
        "menu_order": _to_int(row.get("Position")),
        "meta_title": _pick(row.get("Meta: _yoast_wpseo_title")),
        "meta_description": _pick(row.get("Meta: _yoast_wpseo_metadesc")),
        "image_url": _first_or_none(row.get("Images", "")),
    }


def _row_to_variation_fields(row: Dict, context=None) -> Tuple[Dict, list[Dict]]:
    context = context or {}
    variation_fields = {
        "sku": _pick(row.get("SKU")),
        "source_relpath": context.get("product_relpath"),
        "resolved_row_json": json.dumps(row, ensure_ascii=False, sort_keys=True),
        "regular_price": _to_decimal(row.get("Regular price")),
        "sale_price": _to_decimal(row.get("Sale price")),
        "sale_start": _to_datetime(row.get("Date sale price starts")),
        "sale_end": _to_datetime(row.get("Date sale price ends")),
        "weight": _to_decimal(row.get("Weight (g)")),
        "length": _to_decimal(row.get("Length (mm)")),
        "width": _to_decimal(row.get("Width (mm)")),
        "height": _to_decimal(row.get("Height (mm)")),
        "image_url": _first_or_none(row.get("Images", "")),
    }
    attrs = []
    for i in ATTR_SLOTS:
        name = _pick(row.get(ATTR_NAME_FMT.format(i)))
        value = _pick(row.get(ATTR_VALUE_FMT.format(i)))
        if not name or not value:
            continue
        first_value = value.split(",")[0].strip()
        attrs.append(
            {
                "name": name,
                "value": first_value,
                "visible": _to_bool(row.get(f"Attribute {i} visible")),
                "is_global": _to_bool(row.get(f"Attribute {i} global")),
                "position": i - 1,
            }
        )
    return variation_fields, attrs


def _sync_taxonomy(product, row):
    categories = []
    for name in _split_list(row.get("Categories")):
        category = Category.query.filter_by(name=name).first()
        if not category:
            category = Category(name=name, slug=_slugify(name))
            db.session.add(category)
        categories.append(category)
    product.categories = categories

    tags = []
    for name in _split_list(row.get("Tags")):
        tag = Tag.query.filter_by(name=name).first()
        if not tag:
            tag = Tag(name=name, slug=_slugify(name))
            db.session.add(tag)
        tags.append(tag)
    product.tags = tags


def _sync_product_attributes(product, row):
    existing = {
        attribute.name: attribute
        for attribute in ProductAttribute.query.filter_by(
            product_id=product.id
        ).all()
    }
    for i in ATTR_SLOTS:
        name = _pick(row.get(ATTR_NAME_FMT.format(i)))
        values = _pick(row.get(ATTR_VALUE_FMT.format(i)))
        if not name or not values:
            continue
        attribute = existing.pop(name, None)
        if not attribute:
            attribute = ProductAttribute(
                product_id=product.id,
                name=name,
            )
            db.session.add(attribute)
        attribute.values = values
        attribute.visible = _to_bool(row.get(f"Attribute {i} visible"))
        attribute.is_global = _to_bool(row.get(f"Attribute {i} global"))
        attribute.position = i - 1
    for stale_attribute in existing.values():
        db.session.delete(stale_attribute)


def _sync_product_images(product, row):
    existing = {}
    for image in ProductImage.query.filter_by(product_id=product.id).all():
        existing.setdefault(image.url, []).append(image)

    image_urls = _all_images(row.get("Images", ""))
    product.image_url = image_urls[0] if image_urls else None
    for position, url in enumerate(image_urls):
        matches = existing.get(url, [])
        image = matches.pop(0) if matches else None
        if not image:
            image = ProductImage(product_id=product.id, url=url)
            db.session.add(image)
        image.position = position
    for matches in existing.values():
        for stale_image in matches:
            db.session.delete(stale_image)


def _sync_variation_attributes(variation, attributes):
    existing = {
        attribute.name: attribute
        for attribute in VariationAttribute.query.filter_by(
            variation_id=variation.id
        ).all()
    }
    for fields in attributes:
        attribute = existing.pop(fields["name"], None)
        if not attribute:
            attribute = VariationAttribute(
                variation_id=variation.id, name=fields["name"]
            )
            db.session.add(attribute)
        attribute.value = fields["value"]
        attribute.visible = fields["visible"]
        attribute.is_global = fields["is_global"]
        attribute.position = fields["position"]
    for stale_attribute in existing.values():
        db.session.delete(stale_attribute)


def _sync_variation_images(variation, row):
    existing = {}
    for image in VariationImage.query.filter_by(variation_id=variation.id).all():
        existing.setdefault(image.url, []).append(image)

    image_urls = _all_images(row.get("Images", ""))
    variation.image_url = image_urls[0] if image_urls else None
    for position, url in enumerate(image_urls):
        matches = existing.get(url, [])
        image = matches.pop(0) if matches else None
        if not image:
            image = VariationImage(variation_id=variation.id, url=url)
            db.session.add(image)
        image.position = position
    for matches in existing.values():
        for stale_image in matches:
            db.session.delete(stale_image)


# ---------------- main ingest ----------------


def _utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


def _checkpoint(failure_injector, stage, sku):
    if failure_injector:
        failure_injector(stage, sku)


def _record_failed_item(operation_id, sku, source_path, error):
    if not operation_id:
        return
    with db.session.begin():
        db.session.add(
            CatalogueOperationItem(
                operation_id=operation_id,
                source_path=source_path,
                sku=sku,
                status="failed",
                database_state="rolled_back",
                marker_state="not_started",
                error=sanitize_operation_error(error),
                finished_at=_utcnow(),
            )
        )


def _ingest_complete_parent(
    parent_row,
    variation_rows,
    *,
    catalogue_root,
    folder,
    context,
    operation_id,
    failure_injector,
    log,
):
    sku = _pick(parent_row.get("SKU"))
    collection = _upsert_collection(context)
    _checkpoint(failure_injector, "collection", sku)

    fields = _row_to_product_fields(parent_row, context)
    product = Product.query.filter_by(sku=sku).first()
    product_created = product is None
    if product_created:
        product = Product(**fields)
        db.session.add(product)
    else:
        for key, value in fields.items():
            setattr(product, key, value)

    if collection:
        product.collection = collection
        product.collection_type = collection.collection_type
        product.product_dir = context["product_path"]
        product.shared_json_path = context["shared_json_path"]
        product.override_json_path = context.get("override_json_path")
        product.effective_json_path = context["effective_json_path"]
    db.session.flush()
    _checkpoint(failure_injector, "parent", sku)

    _sync_product_images(product, parent_row)
    db.session.flush()
    _checkpoint(failure_injector, "product_images", sku)

    if context.get("shared_json_path"):
        info_paths = {"shared": context["shared_json_path"]}
        if context.get("override_json_path"):
            info_paths["override"] = context["override_json_path"]
    else:
        info_paths = _info_paths_for_folder(folder) if folder else {}
    _upsert_info_assets(product, info_paths, catalogue_root, log=log)
    db.session.flush()
    _checkpoint(failure_injector, "assets", sku)

    _sync_taxonomy(product, parent_row)
    db.session.flush()
    _checkpoint(failure_injector, "categories_tags", sku)

    _sync_product_attributes(product, parent_row)
    db.session.flush()
    _checkpoint(failure_injector, "product_attributes", sku)

    created_variations = 0
    updated_variations = 0
    for variation_row in variation_rows:
        variation_sku = _pick(variation_row.get("SKU"))
        if not variation_sku:
            raise ValueError(f"Variation for parent {sku} is missing its SKU")
        variation_fields, attributes = _row_to_variation_fields(
            variation_row, context
        )
        variation = Variation.query.filter_by(sku=variation_sku).first()
        if variation is None:
            variation = Variation(product_id=product.id, **variation_fields)
            db.session.add(variation)
            created_variations += 1
        else:
            variation.product_id = product.id
            for key, value in variation_fields.items():
                setattr(variation, key, value)
            updated_variations += 1
        db.session.flush()
        _checkpoint(failure_injector, "variations", sku)

        _sync_variation_attributes(variation, attributes)
        db.session.flush()
        _checkpoint(failure_injector, "variation_attributes", sku)

        _sync_variation_images(variation, variation_row)
        db.session.flush()
        _checkpoint(failure_injector, "variation_images", sku)

    if operation_id:
        db.session.add(
            CatalogueOperationItem(
                operation_id=operation_id,
                source_path=context.get("product_relpath"),
                sku=sku,
                status="succeeded",
                database_state="committed",
                marker_state="not_started",
                finished_at=_utcnow(),
            )
        )
        db.session.flush()
    _checkpoint(failure_injector, "operation_item", sku)

    return {
        "product_created": product_created,
        "variations_created": created_variations,
        "variations_updated": updated_variations,
        "notification": {
            "name": fields.get("title"),
            "type": fields.get("product_type"),
            "images_count": len(_all_images(parent_row.get("Images", ""))),
            "has_shared": "shared" in info_paths,
            "has_override": "override" in info_paths,
            "folder_path": folder,
            "variations_count": len(variation_rows),
        },
    }


def ingest_rows_to_db(
    rows: List[Dict],
    log=print,
    *,
    operation_id=None,
    failure_injector=None,
) -> Dict[str, int]:
    # Ingestion owns its transaction boundaries. End any read-only transaction
    # left open on the request-scoped session before starting parent units.
    db.session.rollback()
    summary = {
        "products_created": 0,
        "products_updated": 0,
        "products_failed": 0,
        "variations_created": 0,
        "variations_updated": 0,
    }

    # Read settings without opening a transaction on the scoped ORM session.
    with db.engine.connect() as connection:
        catalogue_root = connection.execute(
            select(Settings.product_folder).limit(1)
        ).scalar_one_or_none() or ""
    sku_to_folder = _scan_sku_folder_index(catalogue_root, log=log)
    source_by_sku = {
        sku: _source_context(folder, catalogue_root, log=log)
        for sku, folder in sku_to_folder.items()
    }

    parent_rows = [row for row in rows if row.get("Type") in ("simple", "variable")]
    variation_rows = [row for row in rows if row.get("Type") == "variation"]
    parents_by_sku = {
        _pick(row.get("SKU")): row for row in parent_rows if _pick(row.get("SKU"))
    }
    variations_by_parent = {}
    missing_parent_rows = {}
    for row in variation_rows:
        parent_sku = _pick(row.get("Parent"))
        if parent_sku and parent_sku in parents_by_sku:
            variations_by_parent.setdefault(parent_sku, []).append(row)
        else:
            failed_sku = parent_sku or _pick(row.get("SKU")) or "unknown"
            missing_parent_rows.setdefault(failed_sku, []).append(row)

    per_product: Dict[str, Dict] = {}
    for sku, parent_row in parents_by_sku.items():
        context = source_by_sku.get(sku, {})
        folder = sku_to_folder.get(sku)
        try:
            with db.session.begin():
                result = _ingest_complete_parent(
                    parent_row,
                    variations_by_parent.get(sku, []),
                    catalogue_root=catalogue_root,
                    folder=folder,
                    context=context,
                    operation_id=operation_id,
                    failure_injector=failure_injector,
                    log=log,
                )
        except Exception as error:
            db.session.rollback()
            summary["products_failed"] += 1
            _record_failed_item(
                operation_id,
                sku,
                context.get("product_relpath"),
                error,
            )
            log(
                f"❌ Parent {sku} rolled back: {sanitize_operation_error(error)}",
                "ERROR",
            )
            continue

        key = "products_created" if result["product_created"] else "products_updated"
        summary[key] += 1
        summary["variations_created"] += result["variations_created"]
        summary["variations_updated"] += result["variations_updated"]
        per_product[sku] = result["notification"]
        action = "created" if result["product_created"] else "updated"
        log(f"✅ Product {action}: {sku}", "INFO")

    for missing_sku in missing_parent_rows:
        error = ValueError(
            f"Variation rows reference missing parent row: {missing_sku}"
        )
        summary["products_failed"] += 1
        context = source_by_sku.get(missing_sku, {})
        _record_failed_item(
            operation_id,
            missing_sku,
            context.get("product_relpath"),
            error,
        )
        log(f"❌ {error}", "ERROR")

    # Per-product notifications are best effort and run only after each parent commits.
    for sku, info in per_product.items():
        try:
            notify_ingest_product(
                sku=sku,
                name=info["name"],
                product_type=info["type"],
                images_count=info["images_count"],
                has_shared=info["has_shared"],
                has_override=info["has_override"],
                folder_path=info["folder_path"],
                variations_count=(info["variations_count"] or None),
            )
        except Exception as e:
            log(f"⚠️ Discord ingest notify failed for {sku}: {e}", "WARN")

    log(f"✅ Ingest complete — {summary}", "INFO")
    return summary
