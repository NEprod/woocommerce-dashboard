import sqlite3
from pathlib import Path

import pytest

from app import create_app, db
from app.database import (
    BASELINE_REVISION,
    MigrationFailure,
    Phase0SchemaMismatch,
    ensure_database,
    restore_database,
)
from config import Config
from app.models import Product, User, Variation


PHASE0_SCHEMA = Path(__file__).parent / "fixtures" / "db" / "phase0_schema.sql"


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
        for table in (
            "category", "collection", "product", "product_asset",
            "product_categories", "product_image", "product_tags", "service",
            "settings", "tag", "user", "variation", "variation_attribute",
            "variation_image",
        ):
            rows = connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            data[table] = [dict(row) for row in rows]
        return data
    finally:
        connection.close()


def _schema_signature(path):
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
        for table in sorted(tables):
            columns = [tuple(row[1:6]) for row in connection.execute(
                f'PRAGMA table_info("{table}")'
            )]
            indexes = []
            for index in connection.execute(f'PRAGMA index_list("{table}")'):
                index_columns = tuple(
                    row[2] for row in connection.execute(
                        f'PRAGMA index_info("{index[1]}")'
                    )
                )
                indexes.append((index[1], index[2], index_columns))
            foreign_keys = [tuple(row[2:8]) for row in connection.execute(
                f'PRAGMA foreign_key_list("{table}")'
            )]
            signature[table] = (columns, sorted(indexes), sorted(foreign_keys))
        return signature
    finally:
        connection.close()


def test_fresh_database_initializes_at_migration_head(tmp_path):
    database = tmp_path / "fresh.db"
    report = ensure_database(_url(database), backup_root=tmp_path / "backups")

    assert report.action == "initialized"
    assert report.revision == BASELINE_REVISION
    assert report.backup_path is None
    assert _snapshot(database)["revision"] == BASELINE_REVISION


def test_fresh_migration_schema_matches_frozen_phase0_fixture(tmp_path):
    fresh = tmp_path / "fresh.db"
    phase0 = tmp_path / "phase0.db"
    ensure_database(_url(fresh), backup_root=tmp_path / "backups")
    _create_phase0_database(phase0)

    assert _schema_signature(fresh) == _schema_signature(phase0)


def test_application_factory_uses_migrations_for_fresh_database(tmp_path):
    database = tmp_path / "factory.db"
    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = _url(database)
    try:
        app = create_app()
        assert app.config["DATABASE_MIGRATION_REPORT"].revision == BASELINE_REVISION
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
    database = tmp_path / "phase0.db"
    _create_phase0_database(database)
    before = _all_phase0_data(database)

    report = ensure_database(_url(database), backup_root=tmp_path / "backups")
    snapshot = _snapshot(database)

    assert report.action == "adopted"
    assert report.revision == BASELINE_REVISION
    assert report.backup_path and report.backup_path.exists()
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


def test_failed_adoption_leaves_backup_that_can_be_restored_and_used(
    tmp_path, monkeypatch
):
    database = tmp_path / "phase0.db"
    backup_root = tmp_path / "backups"
    _create_phase0_database(database)

    def fail_upgrade(*args, **kwargs):
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr("app.database.command.upgrade", fail_upgrade)
    with pytest.raises(MigrationFailure, match="injected migration failure") as error:
        ensure_database(_url(database), backup_root=backup_root)

    backup = error.value.backup_path
    assert backup and backup.exists()
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
    assert report.revision == BASELINE_REVISION
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
