"""Offline, one-way copy from a verified backup, never an in-place migration."""
import argparse
from collections import Counter
from contextlib import closing
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import tempfile
import uuid

from app.database import MigrationFailure, ensure_database
from app.deployment_guard import DEV_DOMAIN, DEV_SHOP, DeploymentError, Identity, MARKER, _mount_aliases


# Deliberately pin the schema this transfer was reviewed against, not "latest".
REVISION = "0025_promotions"
KEEP = set("""category collection customisation_template inventory_identity inventory_item
inventory_location inventory_movement inventory_recipe_material inventory_transaction
packaging_package product product_asset product_attribute product_categories
product_relationship product_tags tag variation variation_attribute promotion""".split())
DETACH = set("""automatic_publishing packaging_evidence packaging_link product_image variation_image
scheduled_task scheduled_task_run shopify_api_capacity shopify_attribute_identity
shopify_collection_identity shopify_due_event shopify_product_comparison shopify_product_identity
shopify_publish_job shopify_publishing_event shopify_sale_schedule woo_product_identity
woo_taxonomy_identity woo_variation_identity""".split())
EXCLUDE = set("""catalogue_operation catalogue_operation_item service settings shopify_order
shopify_order_delivery shopify_order_event shopify_order_line shopify_order_sync user""".split())
ASSETS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".tif", ".tiff", ".bmp", ".heic",
          ".svg", ".pdf", ".stl", ".3mf", ".step", ".stp", ".obj", ".dxf", ".ai", ".psd"}
REMOTE = re.compile(r"gid://shopify/(?!TaxonomyCategory/)|tlcp1_[a-f0-9]{64}|"
                    r"shp(?:at|ca|pa|ss)_[A-Za-z0-9_-]+|/private/(?:bind|retrieve)/", re.I)
PORTABLE = {
    "collection": {"root_path": "source_relpath", "shared_json_path": "shared_json_relpath"},
    "product": {"product_dir": "source_relpath", "shared_json_path": "shared_json_relpath",
                "override_json_path": "override_json_relpath", "effective_json_path": "effective_json_relpath"},
    "product_asset": {"path": "source_relpath"},
}


class Refused(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def digest(file):
    with file.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def nonlocal_value(value):
    if isinstance(value, str):
        return bool(REMOTE.search(value))
    if isinstance(value, dict):
        return any(nonlocal_value(k) or nonlocal_value(v) for k, v in value.items())
    if isinstance(value, list):
        return any(map(nonlocal_value, value))
    return False


def json_object(raw):
    def unique(pairs):
        value = dict(pairs)
        if len(value) != len(pairs):
            raise Refused("JSON contains duplicate keys.")
        return value
    parsed = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(parsed, dict):
        raise Refused("Expected a JSON object.")
    return parsed


def relative(raw):
    if (not isinstance(raw, str) or not raw or raw.startswith("/") or "\\" in raw
            or nonlocal_value(raw) or any(p in {"", ".", ".."} for p in raw.split("/"))):
        raise Refused("Catalogue contains an unsafe or missing relative reference.")
    return PurePosixPath(raw)


def schema(con):
    objects = con.execute(
        """SELECT type,name,tbl_name,CASE WHEN type='trigger' THEN sql END
           FROM sqlite_schema WHERE substr(name,1,7) != 'sqlite_' ORDER BY type,name""").fetchall()
    tables = {name for kind, name, _, _ in objects if kind == "table"}
    if tables != KEEP | DETACH | EXCLUDE | {"alembic_version"} or any(
            k not in {"table", "index", "trigger"} for k, _, _, _ in objects):
        raise Refused("Unrecognised database schema objects.")
    if con.execute("SELECT version_num FROM alembic_version").fetchall() != [(REVISION,)]:
        raise Refused("Unsupported source schema revision; no partial migration is permitted.")
    return objects, {name: (con.execute(f'PRAGMA table_xinfo("{name}")').fetchall(),
                            con.execute(f'PRAGMA foreign_key_list("{name}")').fetchall())
                     for name in tables}


def integrity(con):
    if con.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise Refused("SQLite integrity check failed.")
    if con.execute("PRAGMA foreign_key_check").fetchone():
        raise Refused("SQLite foreign key integrity check failed.")


def quiescent(source):
    db = source / "instance/site.db"
    if (not (source / "instance").is_dir() or (source / "instance").is_symlink()
            or os.path.lexists(source / "instance" / MARKER)
            or not db.is_file() or db.is_symlink() or db.stat().st_nlink != 1):
        raise Refused("Use a verified offline backup bundle with instance/site.db and catalogue/.")
    if any(os.path.lexists(db.with_name(db.name + ext)) for ext in ("-wal", "-shm", "-journal")):
        raise Refused("SQLite sidecars indicate an unsettled backup. Supply a standalone SQLite backup.")
    manifest = source / "instance/relationship-transactions"
    if os.path.lexists(manifest) and (manifest.is_symlink() or not manifest.is_dir() or any(manifest.iterdir())):
        raise Refused("Relationship recovery evidence must be reconciled before migration.")
    return db


def scrub_document(doc, items, changes, review, *, authored=False):
    market_ids = doc.pop("shopify_market_ids", None)
    if market_ids is not None:
        if not isinstance(market_ids, list) or any(not isinstance(x, str) or not re.fullmatch(
                r"gid://shopify/Market/[0-9]{1,30}", x) for x in market_ids):
            raise Refused("An authored or projected market selection has an unknown shape.")
        changes["market_ids"] += len(market_ids)
        if authored and market_ids:
            review["market_documents"] += 1
    # A scheduled local release can switch intent after a scan. Keep that
    # instruction in the retained source backup, not in the detached copy.
    if doc.pop("shopify_release_at", None):
        changes["release_schedules"] += 1
    if "production" in doc:
        from app.inventory.product_bom import validate
        bom = doc["production"]
        try:
            validate(bom)
        except (TypeError, ValueError):
            raise Refused("A stored BOM does not match the supported recipe schema.") from None
        layers = [bom, *bom.get("variants", {}).values(),
                  *(rule for rules in bom.get("options", {}).values() for rule in rules.values())]
        if any(line.get("item_id") not in items for layer in layers
               for line in layer.get("lines", {}).values() if "item_id" in line):
            raise Refused("A BOM line refers to a missing inventory item.")
    if nonlocal_value(doc):
        raise Refused("Unknown Shopify or customer-upload reference in retained metadata.")
    return doc


def scrub_promotion(raw, con, changes, review):
    from app.promotions.domain import definition
    source = json_object(raw)
    doc = definition(source)
    if doc != source:
        raise Refused("Promotion document is not in its recognised canonical form.")
    needs_review = False
    for key in ("targets", "rewards"):
        if key not in doc:
            continue
        kept = []
        for target in doc[key]:
            table = "collection" if target["mode"] == "collection" else "product"
            if not con.execute(f"SELECT 1 FROM {table} WHERE id=? AND source_relpath=?",
                               (target["id"], target["source"])).fetchone():
                raise Refused("A promotion refers to an unknown local catalogue identity.")
            if target["mode"] == "fixed":
                needs_review = True
                changes["fixed_variant_targets"] += 1
                review["variant_targets"] += 1
            else:
                kept.append(target)
        doc[key] = kept
    if doc.get("segment"):
        needs_review = True
        doc["segment"] = None
        changes["segments"] += 1
        review["customer_segments"] += 1
    if doc["intent"] != "archived":
        doc["intent"] = "draft"
    if nonlocal_value(doc):
        raise Refused("Unknown Shopify reference in retained promotion rules.")
    return canonical(doc), needs_review


def verify_marker(value, folder, con):
    parent = con.execute("SELECT id,sku FROM product WHERE source_relpath=?",
                         (folder.as_posix(),)).fetchall()
    rows = value.get("variations", [])
    if (len(parent) != 1 or not value.get("sku") or parent[0][1] != value["sku"]
            or not isinstance(rows, list) or any(not isinstance(r, dict) or not r.get("sku") for r in rows)):
        return False
    observed = [r["sku"] for r in rows]
    expected = {r[0] for r in con.execute(
        "SELECT sku FROM variation WHERE product_id=? AND catalogue_status='active'", (parent[0][0],))}
    return len(observed) == len(expected) and set(observed) == expected


def catalogue(root, con, items, report):
    if not root.is_dir() or root.is_symlink() or (root / MARKER).exists():
        raise Refused("Expected the catalogue directory of an unmarked offline source backup.")
    entries, directories = {}, []
    allowed_json = {"product_info.json", "sku_index.json", ".scanned", ".scanned.pending"}
    for folder, children, files in os.walk(root, followlinks=False):
        children.sort()
        directory = Path(folder)
        for name in children:
            item = directory / name
            if item.is_symlink() or os.path.ismount(item):
                raise Refused("Catalogue contains a symlink or mounted directory.")
            directories.append(item.relative_to(root))
        for name in sorted(files):
            path = directory / name
            ref = path.relative_to(root)
            relative(ref.as_posix())
            if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
                raise Refused("Catalogue contains a linked or non-regular file.")
            if name in {".DS_Store", "Thumbs.db"}:
                continue
            content = None
            hashed = digest(path)
            if name in allowed_json:
                if path.stat().st_size > 8 * 1024 * 1024:
                    raise Refused("Catalogue JSON is too large to validate.")
                raw = path.read_bytes()
                if sha256(raw).hexdigest() != hashed:
                    raise Refused("Catalogue source changed during analysis.")
                obj = json_object(raw)
                if name in {".scanned", ".scanned.pending"}:
                    marker = obj if name == ".scanned" else obj.get("marker")
                    matches = isinstance(marker, dict) and verify_marker(marker, ref.parent, con)
                    if name == ".scanned.pending":
                        report["scanner_recovery"]["pending_matching" if matches else "pending_unmatched"] += 1
                        continue
                    if not matches:
                        report["scanner_recovery"]["settled_mismatch"] += 1
                if name == "product_info.json":
                    original = canonical(obj)
                    scrub_document(obj, items, report["detached_fields"], report["review"], authored=True)
                    if canonical(obj) != original:
                        content = (canonical(obj) + "\n").encode()
                elif nonlocal_value(obj):
                    raise Refused("Unknown Shopify or customer-upload reference in a scanner file.")
            elif name == ".update":
                # It is a flag, not user metadata. Never carry arbitrary contents across.
                content = b""
            elif path.suffix.lower() not in ASSETS:
                raise Refused("Unsupported catalogue file; review content before migration.")
            entries[ref] = (hashed, content)
    return entries, directories


def copy_rows(src, dst, report, files, dirs, items):
    changes, review = report["detached_fields"], report["review"]
    dirs = set(dirs)
    dst.execute("PRAGMA defer_foreign_keys=ON")
    # The reserved unassigned location is seeded and immutable. Keep the seed
    # row and require the source to contain the identical identity/location.
    dst.execute('DELETE FROM packaging_package')
    for table in sorted(KEEP):
        cursor = src.execute(f'SELECT * FROM "{table}"')
        columns = [d[0] for d in cursor.description]
        sql = 'INSERT INTO "' + table + '" VALUES (' + ",".join("?" for _ in columns) + ")"
        for raw in cursor:
            row = dict(zip(columns, raw))
            if table in {"inventory_identity", "inventory_location"} and row["id"] == 0:
                if dst.execute(f'SELECT * FROM "{table}" WHERE id=0').fetchone() != raw:
                    raise Refused("The reserved Unassigned identity or location differs from the reviewed seed.")
                continue
            for field, relkey in PORTABLE.get(table, {}).items():
                if row[relkey] is not None:
                    path = relative(row[relkey])
                    candidates = dirs if field in {"product_dir", "root_path"} else files
                    if path not in candidates and not (table == "product" and row["catalogue_status"] == "missing"):
                        raise Refused("A retained catalogue row refers to a missing source path.")
                    row[field] = "/catalogue/" + path.as_posix()
                elif row[field] is not None and (field != "override_json_path" or row[field]):
                    raise Refused("A legacy absolute catalogue reference has no safe portable counterpart.")
            for k in ("woo_id", "woo_synced_at", "woo_updated_at", "image_url"):
                if k in row and row[k] is not None:
                    row[k] = None
                    changes[k] += 1
            if row.get("resolved_row_json"):
                doc = json_object(row["resolved_row_json"])
                for key in ("Images", "Image", "image_url"):
                    if doc.pop(key, None):
                        changes["generated_image_projections"] += 1
                row["resolved_row_json"] = canonical(scrub_document(doc, items, changes, review))
            if table == "promotion":
                doc, needs_review = scrub_promotion(row["document"], src, changes, review)
                row.update(document=doc, state="migration_review" if needs_review else "local",
                           receipt=None, revision=row["revision"] + 1)
            if any(nonlocal_value(v) for v in row.values()):
                raise Refused("Unknown Shopify or customer-upload reference in retained SQLite data.")
            dst.execute(sql, tuple(row.values()))
    dst.execute("UPDATE scheduled_task SET enabled=0,state='disabled'")
    integrity(dst)
    for table in sorted(t for t in KEEP if t.startswith("inventory_")):
        q = f'SELECT * FROM "{table}" ORDER BY id'
        if any(a != b for a, b in zip(src.execute(q), dst.execute(q), strict=True)):
            raise Refused("Inventory ledger or identity differs from the source.")
    q = """SELECT item_id, location_id, SUM(qty) FROM inventory_movement
           GROUP BY item_id,location_id ORDER BY item_id,location_id"""
    balances = src.execute(q).fetchall()
    if balances != dst.execute(q).fetchall():
        raise Refused("Inventory balances did not reconcile.")
    report["balance_groups_verified"] = len(balances)
    dst.commit()


def exclusive_copy(source, destination, expected, replacement=None):
    with source.open("rb") as inp, destination.open("xb") as out:
        calculated = sha256()
        for block in iter(lambda: inp.read(1024 * 1024), b""):
            calculated.update(block)
            if replacement is None:
                out.write(block)
        if calculated.hexdigest() != expected:
            raise Refused("A source asset changed after review.")
        if replacement is not None:
            out.write(replacement)


def destination_path(source, requested):
    parent = requested.absolute().parent
    destination = requested.absolute()
    if (not parent.is_dir() or parent.resolve() != parent or destination.resolve() != destination
            or os.path.lexists(destination) or source in destination.parents or destination in source.parents
            or any(source.samefile(ancestor) for ancestor in (parent, *parent.parents))
            or any(os.path.lexists(ancestor / MARKER) for ancestor in (parent, *parent.parents))):
        raise Refused("Staging needs a wholly new destination outside the source and any marked roots.")
    aliases = _mount_aliases([source, destination])
    if len(aliases) == 2:
        (left, a), (right, b) = aliases
        if left == right and (a == b or a in b.parents or b in a.parents):
            raise Refused("Staging destination aliases the source backup.")
    return destination


def run(source, *, destination=None, approval=None):
    report = {"schema": REVISION, "preserved": {}, "detached": {}, "excluded": {},
              "review": Counter(), "detached_fields": Counter(), "scanner_recovery": Counter(),
              "blockers": [], "staged": False}
    try:
        source = source.absolute()
        if not source.is_dir() or source.resolve() != source:
            raise Refused("Supply a real, offline backup directory, not an alias.")
        if destination is not None:
            destination = destination_path(source, destination)
        db = quiescent(source)
        db_hash = digest(db)
        with tempfile.TemporaryDirectory(prefix="tlc-migration-") as tmp:
            scratch = Path(tmp) / "site.db"
            ensure_database("sqlite:///" + str(scratch))
            with closing(sqlite3.connect(db.as_uri() + "?mode=ro&immutable=1", uri=True)) as src, \
                    closing(sqlite3.connect(scratch)) as dst:
                src.execute("PRAGMA trusted_schema=OFF")
                dst.execute("PRAGMA foreign_keys=ON")
                if schema(src) != schema(dst):
                    raise Refused("Source table declarations differ from the reviewed schema.")
                integrity(src)
                for tag, tables in (("preserved", KEEP), ("detached", DETACH), ("excluded", EXCLUDE)):
                    report[tag] = {t: src.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in sorted(tables)}
                pending = src.execute("""SELECT COUNT(*) FROM catalogue_operation
                    WHERE operation_type NOT LIKE 'shopify_%' AND (status IN ('running','pending')
                    OR recovery_state NOT IN ('none','resolved'))""").fetchone()[0]
                if pending:
                    report["scanner_recovery"]["unsettled_operations"] = pending
                items = {r[0] for r in src.execute("SELECT id FROM inventory_item")}
                files, dirs = catalogue(source / "catalogue", src, items, report)
                report["catalogue_files"] = len(files)
                if report["scanner_recovery"]:
                    raise Refused("Scanner state is not settled or does not match the database. No staging allowed.")
                copy_rows(src, dst, report, files, dirs, items)
            if quiescent(source) != db or digest(db) != db_hash:
                raise Refused("The source database changed while being inspected.")
            fingerprint = canonical([report, db_hash, sorted(d.as_posix() for d in dirs),
                                     [[str(p), h] for p, (h, _) in sorted(files.items())]])
            report["review_digest"] = sha256(fingerprint.encode()).hexdigest()
            if destination is not None:
                if approval != report["review_digest"]:
                    raise Refused("Staging requires the matching reviewed dry-run digest.")
                destination.mkdir(mode=0o700)  # Exclusive: never replace an existing path.
                for role in ("instance", "catalogue", "output"):
                    (destination / role).mkdir(mode=0o700)
                    # An incomplete stage has no usable identity and must not
                    # start even if someone inadvertently selects legacy mode.
                    (destination / role / MARKER).write_text('{"state":"staging"}\n')
                for directory in dirs:
                    (destination / "catalogue" / directory).mkdir(mode=0o700)
                for ref, (hashed, content) in files.items():
                    exclusive_copy(source / "catalogue" / ref, destination / "catalogue" / ref, hashed, content)
                for ref in files:
                    if ref.name == ".scanned":
                        # Generated output and image projections are not copied.
                        (destination / "catalogue" / ref.parent / ".update").touch(mode=0o600)
                exclusive_copy(scratch, destination / "instance/site.db", digest(scratch))
                (destination / "instance/site.db").chmod(0o600)
                (destination / "instance/commerce.json").write_text(
                    canonical({"version": 1, "active_platform": "shopify", "shopify": {}}) + "\n")
                (destination / "instance/commerce.json").chmod(0o600)
                if quiescent(source) != db or digest(db) != db_hash:
                    raise Refused("The source changed during staging; do not use the incomplete destination.")
                storage_id = uuid.uuid4().hex
                owner = Identity("development", storage_id, DEV_DOMAIN, DEV_SHOP, "")
                # Valid instance marker last: an interrupted stage cannot boot.
                for role in ("catalogue", "output", "instance"):
                    (destination / role / MARKER).write_text(canonical(owner.marker(role)) + "\n")
                report.update(staged=True, storage_id=storage_id)
    except Refused as exc:
        report["blockers"].append(str(exc))
    except (OSError, ValueError, TypeError, KeyError, OverflowError, sqlite3.Error, MigrationFailure, DeploymentError):
        # Exception bodies can include source values, paths or JSON text.
        report["blockers"].append("Unsupported, unreadable or inconsistent source data. No safe migration was established.")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("dry-run", "stage"):
        command = sub.add_parser(action)
        command.add_argument("--source", required=True, type=Path)
        if action == "stage":
            command.add_argument("--destination", required=True, type=Path)
            command.add_argument("--approve", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    result = run(args.source, destination=getattr(args, "destination", None), approval=getattr(args, "approve", None))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
