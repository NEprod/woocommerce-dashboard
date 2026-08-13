"""Versioned SQLite migration, backup, and restoration support."""

from __future__ import annotations

import argparse
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url


BASELINE_REVISION = "0001_phase0"
MIGRATIONS_PATH = Path(__file__).resolve().parents[1] / "migrations"

PHASE0_TABLE_COLUMNS = {
    "category": {"id", "name", "slug", "woo_id", "created_at", "updated_at"},
    "collection": {
        "id", "name", "slug", "root_path", "sku_prefix", "shared_json_path",
        "created_at", "updated_at",
    },
    "product": {
        "id", "sku", "title", "slug", "product_type", "collection_type",
        "collection_id", "product_dir", "shared_json_path", "override_json_path",
        "effective_json_path", "regular_price", "sale_price", "sale_start",
        "sale_end", "manage_stock", "stock_quantity", "backorders", "weight",
        "length", "width", "height", "shipping_class", "short_description",
        "description", "external_url", "button_text", "upsell_ids",
        "cross_sell_ids", "status", "catalog_visibility", "reviews_allowed",
        "featured", "image_url", "woo_id", "woo_synced_at", "woo_updated_at",
        "local_updated_at", "created_at",
    },
    "product_asset": {
        "id", "product_id", "variation_id", "path", "kind", "label",
        "is_primary", "created_at",
    },
    "product_categories": {"product_id", "category_id"},
    "product_image": {
        "id", "product_id", "url", "alt_text", "position", "woo_id",
    },
    "product_tags": {"product_id", "tag_id"},
    "service": {"id", "name", "type", "renewal_date", "auto_renew", "notes"},
    "settings": {"id", "product_folder", "output_folder", "url_prefix"},
    "tag": {"id", "name", "slug", "woo_id", "created_at", "updated_at"},
    "user": {"id", "email", "username", "password", "is_admin"},
    "variation": {
        "id", "product_id", "sku", "regular_price", "sale_price", "sale_start",
        "sale_end", "manage_stock", "stock_quantity", "backorders", "weight",
        "length", "width", "height", "image_url", "is_default", "visible",
        "status", "menu_order", "woo_id", "woo_synced_at", "woo_updated_at",
        "local_updated_at",
    },
    "variation_attribute": {"id", "variation_id", "name", "value"},
    "variation_image": {"id", "variation_id", "url", "alt_text", "position"},
}


class MigrationFailure(RuntimeError):
    """Migration failed; ``backup_path`` identifies the recoverable original."""

    def __init__(self, message: str, backup_path: Path | None = None):
        super().__init__(message)
        self.backup_path = backup_path


class Phase0SchemaMismatch(MigrationFailure):
    """An unversioned database is not the frozen Phase 0 schema."""


@dataclass(frozen=True)
class MigrationReport:
    action: str
    revision: str
    backup_path: Path | None = None


def _database_path(database_url: str) -> Path:
    url = make_url(database_url)
    if url.drivername != "sqlite" or not url.database or url.database == ":memory:":
        raise MigrationFailure("Only file-backed SQLite databases are supported")
    return Path(url.database).expanduser().resolve()


def _alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_PATH))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _head_revision(config: Config) -> str:
    head = ScriptDirectory.from_config(config).get_current_head()
    if not head:
        raise MigrationFailure("Migration history has no head revision")
    return head


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _revision(connection: sqlite3.Connection) -> str | None:
    if "alembic_version" not in _tables(connection):
        return None
    row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    return row[0] if row else None


def _assert_phase0_schema(connection: sqlite3.Connection) -> None:
    actual_tables = _tables(connection)
    expected_tables = set(PHASE0_TABLE_COLUMNS)
    if actual_tables != expected_tables:
        raise Phase0SchemaMismatch(
            "Unversioned database is not the Phase 0 schema: "
            f"expected tables {sorted(expected_tables)}, found {sorted(actual_tables)}"
        )
    for table, expected_columns in PHASE0_TABLE_COLUMNS.items():
        actual_columns = {
            row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
        if actual_columns != expected_columns:
            raise Phase0SchemaMismatch(
                f"Unversioned table {table!r} does not match the Phase 0 columns"
            )


def _integrity_check(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        connection.close()
    if result != "ok":
        raise MigrationFailure(f"SQLite integrity check failed for {path}: {result}")


def backup_database(database_path: Path, backup_root: Path | None = None) -> Path:
    """Create a consistent SQLite backup using the SQLite backup API."""

    database_path = database_path.resolve()
    root = (backup_root or database_path.parent / "backups").resolve()
    root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = root / f"{database_path.stem}.pre-migration-{timestamp}.sqlite3"
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.unlink(missing_ok=True)

    source_connection = sqlite3.connect(database_path)
    destination_connection = sqlite3.connect(temporary)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()
    _integrity_check(temporary)
    os.replace(temporary, destination)
    return destination


def restore_database(backup_path: Path, database_path: Path) -> Path:
    """Restore a validated SQLite backup by atomic replacement."""

    backup_path = backup_path.resolve()
    database_path = database_path.resolve()
    if not backup_path.is_file():
        raise MigrationFailure(f"Backup does not exist: {backup_path}")
    _integrity_check(backup_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = database_path.with_suffix(database_path.suffix + ".restore.tmp")
    temporary.unlink(missing_ok=True)

    source_connection = sqlite3.connect(backup_path)
    destination_connection = sqlite3.connect(temporary)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()
    _integrity_check(temporary)
    os.replace(temporary, database_path)
    return database_path


def ensure_database(
    database_url: str,
    *,
    backup_root: Path | None = None,
    upgrade: Callable[[Config, str], None] | None = None,
) -> MigrationReport:
    """Initialize, adopt, or upgrade a file-backed SQLite database."""

    database_path = _database_path(database_url)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    config = _alembic_config(database_url)
    head = _head_revision(config)
    upgrade_command = upgrade or command.upgrade
    backup_path = None

    connection = sqlite3.connect(database_path)
    try:
        tables = _tables(connection)
        current = _revision(connection)
        if not tables:
            action = "initialized"
        elif current is None:
            _assert_phase0_schema(connection)
            action = "adopted"
        elif current == head:
            return MigrationReport("current", head, None)
        else:
            action = "upgraded"
    finally:
        connection.close()

    try:
        if action == "adopted":
            backup_path = backup_database(database_path, backup_root)
            command.stamp(config, BASELINE_REVISION)
        elif action == "upgraded":
            backup_path = backup_database(database_path, backup_root)
        upgrade_command(config, "head")
    except Exception as error:
        raise MigrationFailure(str(error), backup_path) from error

    connection = sqlite3.connect(database_path)
    try:
        current = _revision(connection)
    finally:
        connection.close()
    if current != head:
        raise MigrationFailure(
            f"Migration ended at {current!r}; expected {head!r}", backup_path
        )
    _integrity_check(database_path)
    return MigrationReport(action, current, backup_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("upgrade", "restore"))
    parser.add_argument("database", type=Path)
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args(argv)

    if args.action == "upgrade":
        report = ensure_database(f"sqlite:///{args.database.resolve()}")
        print(f"action={report.action} revision={report.revision}")
        if report.backup_path:
            print(f"backup={report.backup_path}")
        return 0
    if not args.backup:
        parser.error("restore requires --backup")
    restored = restore_database(args.backup, args.database)
    print(f"restored={restored}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
