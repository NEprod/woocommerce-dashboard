# app/utils/ingest.py
from typing import List, Dict, Tuple
from decimal import Decimal, InvalidOperation
from datetime import datetime
import os
import json
import re

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
)
from app.utils.discord import notify_ingest_product  # NEW

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
        if ".scanned" in filenames:
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
    ProductAsset.query.filter_by(product_id=product.id, kind="info").delete(
        synchronize_session="fetch"
    )
    for label, path in info_paths.items():
        try:
            db.session.add(
                ProductAsset(
                    product_id=product.id,
                    variation_id=None,
                    path=path,
                    source_relpath=_portable_relpath(path, catalogue_root),
                    kind="info",
                    label=label,  # 'shared' or 'override'
                    is_primary=(label == "shared"),
                )
            )
        except Exception as e:
            log(f"⚠️ Failed to add info asset ({label}) for {product.sku}: {e}", "WARN")


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
    ProductAttribute.query.filter_by(product_id=product.id).delete(
        synchronize_session="fetch"
    )
    for i in ATTR_SLOTS:
        name = _pick(row.get(ATTR_NAME_FMT.format(i)))
        values = _pick(row.get(ATTR_VALUE_FMT.format(i)))
        if not name or not values:
            continue
        db.session.add(
            ProductAttribute(
                product_id=product.id,
                name=name,
                values=values,
                visible=_to_bool(row.get(f"Attribute {i} visible")),
                is_global=_to_bool(row.get(f"Attribute {i} global")),
                position=i - 1,
            )
        )


# ---------------- main ingest ----------------


def ingest_rows_to_db(rows: List[Dict], log=print) -> Dict[str, int]:
    created_products = 0
    updated_products = 0
    created_variations = 0
    updated_variations = 0

    # Build SKU → folder index from .scanned
    settings = Settings.query.first()
    catalogue_root = settings.product_folder if settings else ""
    sku_to_folder = _scan_sku_folder_index(
        catalogue_root, log=log
    )
    source_by_sku = {
        sku: _source_context(folder, catalogue_root, log=log)
        for sku, folder in sku_to_folder.items()
    }

    # Track per-product data for Discord pings
    per_product: Dict[str, Dict] = {}

    # 1) Products (simple + variable parent)
    parent_rows = [r for r in rows if r.get("Type") in ("simple", "variable")]
    for r in parent_rows:
        sku = _pick(r.get("SKU"))
        if not sku:
            log(f"⚠️ Skipping product row without SKU: {r}", "WARN")
            continue

        context = source_by_sku.get(sku, {})
        fields = _row_to_product_fields(r, context)
        prod = Product.query.filter_by(sku=sku).first()
        if not prod:
            prod = Product(**fields)
            db.session.add(prod)
            created_products += 1
            log(f"🆕 Product created: {sku}", "INFO")
        else:
            for k, v in fields.items():
                setattr(prod, k, v)
            updated_products += 1
            log(f"🔁 Product updated: {sku}", "INFO")

        collection = _upsert_collection(context)
        if collection:
            prod.collection = collection
            prod.collection_type = collection.collection_type
            prod.product_dir = context["product_path"]
            prod.shared_json_path = context["shared_json_path"]
            prod.override_json_path = context.get("override_json_path")
            prod.effective_json_path = context["effective_json_path"]

        # gallery
        all_imgs = _all_images(r.get("Images", ""))
        prod.image_url = all_imgs[0] if all_imgs else None
        db.session.flush()
        ProductImage.query.filter_by(product_id=prod.id).delete()
        for pos, url in enumerate(all_imgs):
            db.session.add(ProductImage(product_id=prod.id, url=url, position=pos))

        _sync_taxonomy(prod, r)
        _sync_product_attributes(prod, r)

        # info assets
        folder = sku_to_folder.get(sku)
        if context.get("shared_json_path"):
            info_paths = {"shared": context["shared_json_path"]}
            if context.get("override_json_path"):
                info_paths["override"] = context["override_json_path"]
        else:
            info_paths = _info_paths_for_folder(folder) if folder else {}
        _upsert_info_assets(prod, info_paths, catalogue_root, log=log)

        # record for ping
        per_product[sku] = {
            "name": fields.get("title"),
            "type": fields.get("product_type"),
            "images_count": len(all_imgs),
            "has_shared": "shared" in info_paths,
            "has_override": "override" in info_paths,
            "folder_path": folder,
            "variations_count": 0,  # will be incremented below
        }

    db.session.commit()

    # Index parents by SKU
    product_by_sku = {
        p.sku: p
        for p in Product.query.filter(
            Product.sku.in_(
                [_pick(r.get("SKU")) for r in parent_rows if _pick(r.get("SKU"))]
            )
        ).all()
    }

    # 2) Variations
    variation_rows = [r for r in rows if r.get("Type") == "variation"]
    for r in variation_rows:
        parent_sku = _pick(r.get("Parent"))
        var_sku = _pick(r.get("SKU"))
        if not parent_sku or not var_sku:
            log(
                f"⚠️ Skipping variation without parent or SKU: parent={parent_sku}, sku={var_sku}",
                "WARN",
            )
            continue

        parent = (
            product_by_sku.get(parent_sku)
            or Product.query.filter_by(sku=parent_sku).first()
        )
        if not parent:
            log(f"❌ Variation references missing parent SKU: {parent_sku}", "ERROR")
            continue

        context = source_by_sku.get(parent_sku, {})
        var_fields, attrs = _row_to_variation_fields(r, context)
        variation = Variation.query.filter_by(sku=var_sku).first()

        if not variation:
            variation = Variation(product_id=parent.id, **var_fields)
            db.session.add(variation)
            db.session.flush()
            created_variations += 1
            log(f"🆕 Variation created: {var_sku} (parent {parent_sku})", "INFO")
        else:
            variation.product_id = parent.id
            for k, v in var_fields.items():
                setattr(variation, k, v)
            db.session.flush()
            updated_variations += 1
            log(f"🔁 Variation updated: {var_sku}", "INFO")

        # attributes
        VariationAttribute.query.filter_by(variation_id=variation.id).delete()
        for attribute in attrs:
            db.session.add(
                VariationAttribute(variation_id=variation.id, **attribute)
            )

        # gallery
        v_imgs = _all_images(r.get("Images", ""))
        variation.image_url = v_imgs[0] if v_imgs else None
        VariationImage.query.filter_by(variation_id=variation.id).delete()
        for pos, url in enumerate(v_imgs):
            db.session.add(
                VariationImage(variation_id=variation.id, url=url, position=pos)
            )

        # count per-product variations
        if parent_sku in per_product:
            per_product[parent_sku]["variations_count"] += 1

    db.session.commit()

    # 3) Per‑product Discord pings (after variations are known)
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

    summary = {
        "products_created": created_products,
        "products_updated": updated_products,
        "variations_created": created_variations,
        "variations_updated": updated_variations,
    }
    log(f"✅ Ingest complete — {summary}", "INFO")
    return summary
