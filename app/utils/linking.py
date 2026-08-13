import os
from app import db
from app.models import Settings, Product, Collection
from app.utils.file_markers import load_scanned

SHARED_FILENAME = "product_info.json"
OVERRIDE_FILENAME = "product_info.json"  # same filename but at product folder


def discover_collections(log=print):
    s = Settings.query.first()
    if not s or not s.product_folder:
        log("Settings missing product_folder", "ERROR")
        return []

    root = s.product_folder
    found = []
    for name in sorted(os.listdir(root)):
        cpath = os.path.join(root, name)
        if not (os.path.isdir(cpath) and not name.startswith(".")):
            continue

        shared_path = os.path.join(cpath, SHARED_FILENAME)
        if not os.path.exists(shared_path):
            log(f"Collection missing shared JSON: {cpath}", "WARN")
            continue

        try:
            import json

            with open(shared_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            prefix = data.get("sku_prefix")
            title = data.get("title") or name
            if not prefix:
                log(f"Missing sku_prefix in {shared_path}", "WARN")
                continue

            col = Collection.query.filter_by(root_path=cpath).first()
            if not col:
                col = Collection(
                    name=title,
                    slug=name,
                    root_path=cpath,
                    sku_prefix=prefix,
                    shared_json_path=shared_path,
                )
                db.session.add(col)
            else:
                col.name = title
                col.slug = name
                col.sku_prefix = prefix
                col.shared_json_path = shared_path
            found.append(col)
        except Exception as e:
            log(f"Failed reading {shared_path}: {e}", "ERROR")

    db.session.commit()
    return found


def relink_products(log=print):
    """Relink products to collections using sku_prefix + .scanned main SKU."""
    s = Settings.query.first()
    if not s or not s.product_folder:
        log("Settings missing product_folder", "ERROR")
        return

    collections = {c.sku_prefix: c for c in Collection.query.all()}
    root = s.product_folder

    # Walk all product folders under each collection root
    for c in Collection.query.all():
        for item in sorted(os.listdir(c.root_path)):
            ipath = os.path.join(c.root_path, item)
            if not os.path.isdir(ipath) or item.startswith("."):
                continue

            scanned = load_scanned(ipath, log=lambda *a, **k: None) or {}
            sku = scanned.get("sku")
            if not sku:
                # not processed yet; will be linked once scanned
                continue

            # upsert product by SKU
            prod = Product.query.filter_by(sku=sku).first()
            if not prod:
                # product row should already exist from ingest, but be defensive
                continue

            prod.collection_id = c.id
            prod.product_dir = ipath
            prod.shared_json_path = c.shared_json_path

            override_path = os.path.join(ipath, OVERRIDE_FILENAME)
            if os.path.exists(override_path):
                prod.override_json_path = override_path
                prod.effective_json_path = override_path
            else:
                prod.override_json_path = None
                prod.effective_json_path = c.shared_json_path

    db.session.commit()
