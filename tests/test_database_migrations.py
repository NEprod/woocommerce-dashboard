import sqlite3
import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig

from app import create_app, db
from app.database import (
    BASELINE_REVISION,
    MIGRATIONS_PATH,
    MigrationFailure,
    PHASE0_TABLE_COLUMNS,
    Phase0SchemaMismatch,
    backup_database,
    ensure_database,
    migration_head,
    restore_database,
)
from config import Config
from app.models import Product, User, Variation


PHASE0_SCHEMA = Path(__file__).parent / "fixtures" / "db" / "phase0_schema.sql"
MIGRATION_HEAD = migration_head()
PHASE0_TABLES = set(PHASE0_TABLE_COLUMNS)


def _url(path):
    return f"sqlite:///{path}"


def _create_phase0_database(path):
    connection = sqlite3.connect(path)
    try:
        connection.executescript(PHASE0_SCHEMA.read_text(encoding="utf-8"))
        connection.executescript(
            """
            INSERT INTO user VALUES
                (7, 'fixture@example.invalid', 'fixture-admin', 'hash', 1);
            INSERT INTO settings VALUES
                (3, '/fictional/catalogue', '/fictional/output', 'https://invalid.example/');
            INSERT INTO collection VALUES
                (5, 'Fixture Collection', 'fixture', '/fictional/catalogue/fixture',
                 'FIC-', '/fictional/catalogue/fixture/product_info.json',
                 '2025-01-02 03:04:05', '2025-02-03 04:05:06');
            INSERT INTO product VALUES
                (11, 'FIC-0042', 'Fixture Product', 'fixture-product', 'variable',
                 'Variable', 5, '/fictional/catalogue/fixture/product',
                 '/fictional/catalogue/fixture/product_info.json',
                 '/fictional/catalogue/fixture/product/product_info.json',
                 '/fictional/catalogue/fixture/product/product_info.json',
                 12.50, 10.00, '2025-01-01', '2025-01-31', 1, 4, 'notify',
                 40, 10, 20, 3, 'fixture-class', 'Short', 'Long', NULL, NULL,
                 'UP-1', 'CROSS-1', 'publish', 'visible', 1, 0,
                 'https://invalid.example/product.webp', 101,
                 '2025-03-04 05:06:07', '2025-03-05 05:06:07',
                 '2025-03-06 05:06:07', '2025-01-02 03:04:05');
            INSERT INTO variation VALUES
                (21, 11, 'FIC-0042-1', 12.50, 10.00, '2025-01-01',
                 '2025-01-31', 1, 2, 'no', 40, 10, 20, 3,
                 'https://invalid.example/variation.webp', 1, 1, 'publish', 2,
                 201, '2025-03-04 05:06:07', '2025-03-05 05:06:07',
                 '2025-03-06 05:06:07');
            INSERT INTO product_image VALUES
                (31, 11, 'https://invalid.example/product.webp', 'Fixture', 0, 301);
            INSERT INTO variation_image VALUES
                (41, 21, 'https://invalid.example/variation.webp', 'Fixture', 0);
            INSERT INTO variation_attribute VALUES (51, 21, 'Size', 'Large');
            INSERT INTO product_asset VALUES
                (61, 11, NULL, '/fictional/catalogue/fixture/product_info.json',
                 'info', 'shared', 1, '2025-01-02 03:04:05');
            INSERT INTO category VALUES
                (71, 'Fixture Category', 'fixture-category', 401,
                 '2025-01-02 03:04:05', '2025-02-03 04:05:06');
            INSERT INTO tag VALUES
                (81, 'fixture-tag', 'fixture-tag', 501,
                 '2025-01-02 03:04:05', '2025-02-03 04:05:06');
            INSERT INTO product_categories VALUES (11, 71);
            INSERT INTO product_tags VALUES (11, 81);
            """
        )
        connection.commit()
    finally:
        connection.close()


def _snapshot(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        def row(table):
            value = connection.execute(f'SELECT * FROM "{table}"').fetchone()
            return dict(value) if value else None

        return {
            "revision": connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0],
            "user": row("user"),
            "settings": row("settings"),
            "collection": row("collection"),
            "product": row("product"),
            "variation": row("variation"),
            "asset": row("product_asset"),
            "product_image": row("product_image"),
        }
    finally:
        connection.close()


def _all_phase0_data(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        data = {}
        for table in sorted(PHASE0_TABLES):
            columns = sorted(PHASE0_TABLE_COLUMNS[table])
            selection = ", ".join(f'"{column}"' for column in columns)
            rows = connection.execute(
                f'SELECT {selection} FROM "{table}" ORDER BY rowid'
            ).fetchall()
            data[table] = [dict(row) for row in rows]
        return data
    finally:
        connection.close()


def _schema_signature(path, table_columns=None):
    connection = sqlite3.connect(path)
    try:
        signature = {}
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "AND name != 'alembic_version'"
            )
        }
        if table_columns is not None:
            tables &= set(table_columns)
        for table in sorted(tables):
            expected_columns = table_columns.get(table) if table_columns else None
            columns = [
                tuple(row[1:6])
                for row in connection.execute(f'PRAGMA table_info("{table}")')
                if expected_columns is None or row[1] in expected_columns
            ]
            indexes = []
            for index in connection.execute(f'PRAGMA index_list("{table}")'):
                index_columns = tuple(
                    row[2] for row in connection.execute(
                        f'PRAGMA index_info("{index[1]}")'
                    )
                )
                if expected_columns is None or set(index_columns) <= expected_columns:
                    indexes.append((index[1], index[2], index_columns))
            foreign_keys = [
                tuple(row[2:8])
                for row in connection.execute(f'PRAGMA foreign_key_list("{table}")')
                if expected_columns is None or row[3] in expected_columns
            ]
            signature[table] = (columns, sorted(indexes), sorted(foreign_keys))
        return signature
    finally:
        connection.close()


def test_fresh_database_initializes_at_migration_head(tmp_path):
    database = tmp_path / "fresh.db"
    report = ensure_database(_url(database), backup_root=tmp_path / "backups")

    assert report.action == "initialized"
    assert report.revision == MIGRATION_HEAD
    assert report.backup_path is None
    assert _snapshot(database)["revision"] == MIGRATION_HEAD
    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        connection.close()
    assert {
        "catalogue_operation",
        "catalogue_operation_item",
        "product_attribute",
    } <= tables

    connection = sqlite3.connect(database)
    try:
        collection_columns = {
            row[1]
            for row in connection.execute('PRAGMA table_info("collection")')
        }
        product_columns = {
            row[1] for row in connection.execute('PRAGMA table_info("product")')
        }
        variation_columns = {
            row[1] for row in connection.execute('PRAGMA table_info("variation")')
        }
        asset_columns = {
            row[1]
            for row in connection.execute('PRAGMA table_info("product_asset")')
        }
        operation_columns = {
            row[1]
            for row in connection.execute('PRAGMA table_info("catalogue_operation")')
        }
    finally:
        connection.close()
    assert {"collection_type", "source_relpath", "shared_json_relpath"} <= (
        collection_columns
    )
    assert {
        "source_relpath",
        "shared_json_relpath",
        "override_json_relpath",
        "effective_json_relpath",
        "resolved_row_json",
        "meta_title",
        "meta_description",
        "catalogue_status",
        "missing_at",
        "restored_at",
    } <= product_columns
    assert {
        "source_relpath",
        "source_identity",
        "resolved_row_json",
        "catalogue_status",
        "missing_at",
        "restored_at",
    } <= variation_columns
    assert {
        "products_missing",
        "products_restored",
        "variations_missing",
        "variations_restored",
    } <= operation_columns
    assert "source_relpath" in asset_columns


def test_fresh_migration_schema_matches_frozen_phase0_fixture(tmp_path):
    fresh = tmp_path / "fresh.db"
    phase0 = tmp_path / "phase0.db"
    ensure_database(_url(fresh), backup_root=tmp_path / "backups")
    _create_phase0_database(phase0)

    assert _schema_signature(fresh, PHASE0_TABLE_COLUMNS) == _schema_signature(phase0)


def test_application_factory_uses_migrations_for_fresh_database(tmp_path):
    database = tmp_path / "factory.db"
    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = _url(database)
    try:
        app = create_app()
        assert app.config["DATABASE_MIGRATION_REPORT"].revision == MIGRATION_HEAD
    finally:
        Config.SQLALCHEMY_DATABASE_URI = original_uri


def test_unknown_unversioned_schema_is_rejected_without_stamping(tmp_path):
    database = tmp_path / "unknown.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE unknown_fixture (id INTEGER PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(Phase0SchemaMismatch):
        ensure_database(_url(database), backup_root=tmp_path / "backups")

    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        connection.close()
    assert tables == {"unknown_fixture"}
    assert not (tmp_path / "backups").exists()


def test_unversioned_phase0_upgrade_preserves_data_ids_and_placeholders(tmp_path):
    database = tmp_path / "instance" / "phase0.db"
    database.parent.mkdir()
    _create_phase0_database(database)
    before = _all_phase0_data(database)

    report = ensure_database(_url(database))
    snapshot = _snapshot(database)

    assert report.action == "adopted"
    assert report.revision == MIGRATION_HEAD
    assert report.backup_path and report.backup_path.exists()
    assert report.backup_path.parent == database.parent / "backups"
    assert report.backup_path.parent != Path(tempfile.gettempdir())
    assert f"unversioned-to-{MIGRATION_HEAD}" in report.backup_path.name
    assert _all_phase0_data(report.backup_path) == before
    assert snapshot["user"]["id"] == 7
    assert snapshot["settings"]["id"] == 3
    assert snapshot["collection"]["id"] == 5
    assert snapshot["product"]["id"] == 11
    assert snapshot["variation"]["id"] == 21
    assert snapshot["asset"]["id"] == 61
    assert snapshot["product"]["created_at"] == "2025-01-02 03:04:05"
    assert snapshot["product"]["woo_id"] == 101
    assert snapshot["product"]["woo_synced_at"] == "2025-03-04 05:06:07"
    assert snapshot["variation"]["woo_id"] == 201
    assert snapshot["product_image"]["woo_id"] == 301
    assert snapshot["collection"]["source_relpath"] is None
    assert snapshot["collection"]["collection_type"] is None
    assert snapshot["product"]["source_relpath"] is None
    assert snapshot["product"]["resolved_row_json"] is None
    assert snapshot["variation"]["source_relpath"] is None
    assert snapshot["variation"]["resolved_row_json"] is None
    assert snapshot["asset"]["source_relpath"] is None
    connection = sqlite3.connect(database)
    try:
        lifecycle = connection.execute(
            "SELECT p.catalogue_status, p.missing_at, p.restored_at, "
            "v.catalogue_status, v.missing_at, v.restored_at "
            "FROM product p JOIN variation v ON v.product_id = p.id "
            "WHERE p.id = 11 AND v.id = 21"
        ).fetchone()
    finally:
        connection.close()
    assert lifecycle == ("active", None, None, "active", None, None)
    assert _all_phase0_data(database) == before


def test_repeated_upgrade_is_safe_and_does_not_create_another_backup(tmp_path):
    database = tmp_path / "phase0.db"
    backup_root = tmp_path / "backups"
    _create_phase0_database(database)
    first = ensure_database(_url(database), backup_root=backup_root)
    before = _snapshot(database)

    second = ensure_database(_url(database), backup_root=backup_root)

    assert first.backup_path is not None
    assert second.backup_path is None
    assert second.action == "current"
    assert _snapshot(database) == before
    assert len(list(backup_root.glob("*.sqlite3"))) == 1


def test_versioned_operation_history_survives_projection_upgrade(tmp_path):
    database = tmp_path / "instance" / "versioned.db"
    database.parent.mkdir()
    database_url = _url(database)
    config = AlembicConfig()
    config.set_main_option("script_location", str(MIGRATIONS_PATH))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0002_operations")

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO catalogue_operation "
            "(id, operation_type, status, scope) VALUES (?, ?, ?, ?)",
            ("fixture-operation", "append", "succeeded", "{}"),
        )
        connection.commit()
    finally:
        connection.close()

    report = ensure_database(database_url)

    assert report.action == "upgraded"
    assert report.revision == MIGRATION_HEAD
    assert report.backup_path is not None
    assert report.backup_path.parent == database.parent / "backups"
    connection = sqlite3.connect(database)
    try:
        operation = connection.execute(
            "SELECT operation_type, status FROM catalogue_operation WHERE id = ?",
            ("fixture-operation",),
        ).fetchone()
    finally:
        connection.close()
    assert operation == ("append", "succeeded")


def test_default_backup_names_are_unique_and_do_not_overwrite(tmp_path):
    database = tmp_path / "instance" / "phase0.db"
    database.parent.mkdir()
    _create_phase0_database(database)

    first = backup_database(
        database, source_revision="unversioned", target_revision=BASELINE_REVISION
    )
    second = backup_database(
        database, source_revision="unversioned", target_revision=BASELINE_REVISION
    )

    assert first != second
    assert first.exists() and second.exists()
    assert first.parent == second.parent == database.parent / "backups"
    assert _all_phase0_data(first) == _all_phase0_data(second)


def test_failed_adoption_leaves_backup_that_can_be_restored_and_used(
    tmp_path, monkeypatch
):
    database = tmp_path / "instance" / "phase0.db"
    database.parent.mkdir()
    _create_phase0_database(database)

    def fail_upgrade(*args, **kwargs):
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr("app.database.command.upgrade", fail_upgrade)
    with pytest.raises(MigrationFailure, match="injected migration failure") as error:
        ensure_database(_url(database))

    backup = error.value.backup_path
    assert backup and backup.exists()
    assert backup.parent == database.parent / "backups"
    assert f"unversioned-to-{MIGRATION_HEAD}" in backup.name
    restore_database(backup, database)
    monkeypatch.undo()

    connection = sqlite3.connect(database)
    try:
        assert "alembic_version" not in {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        connection.close()

    report = ensure_database(_url(database), backup_root=tmp_path / "retry-backups")
    snapshot = _snapshot(database)
    assert report.revision == MIGRATION_HEAD
    assert snapshot["user"]["id"] == 7
    assert snapshot["product"]["id"] == 11
    assert snapshot["variation"]["id"] == 21
    assert snapshot["product"]["woo_id"] == 101

    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = _url(database)
    try:
        app = create_app()
        with app.app_context():
            assert db.session.get(User, 7).username == "fixture-admin"
            assert db.session.get(Product, 11).woo_id == 101
            assert db.session.get(Variation, 21).woo_id == 201
    finally:
        Config.SQLALCHEMY_DATABASE_URI = original_uri
