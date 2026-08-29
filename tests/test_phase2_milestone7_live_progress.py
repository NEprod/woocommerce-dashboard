import json
import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import event

from app import create_app, db
from app.models import (
    CatalogueOperation, Collection, Product, ProductAsset, ProductImage,
    Settings, User, Variation, VariationImage,
)
from app.utils.operation_control import get_active_operation, reset_operation_control_for_tests
from app.utils.operation_live import (
    PERSISTED_LOG_BYTE_LIMIT,
    PERSISTED_LOG_LINE_LIMIT,
    persist_live_state,
    persisted_live_state,
)
from config import Config


@pytest.fixture
def live_app(tmp_path, monkeypatch):
    database = tmp_path / "instance" / "site.db"
    database.parent.mkdir()
    catalogue = tmp_path / "catalogue"
    output = tmp_path / "output"
    catalogue.mkdir()
    output.mkdir()
    monkeypatch.setenv("DISCORD_ENABLED", "false")
    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{database}"
    try:
        app = create_app()
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with app.app_context():
            db.session.add(User(email="live@example.test", username="live", password="unused"))
            db.session.add(Settings(product_folder=str(catalogue), output_folder=str(output), url_prefix="https://uploads.invalid/"))
            db.session.add(CatalogueOperation(
                id="liveoperation0000000000000000000", operation_type="full", status="running",
                scope=json.dumps({"scan_mode": "full"}), started_at=datetime.now(UTC).replace(tzinfo=None),
            ))
            db.session.commit()
        yield app
    finally:
        reset_operation_control_for_tests()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


def _client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True
    return client


def _write(app, *, stage="scanning", completed=1, warnings=0, status="running", logs=()):
    with app.app_context():
        persist_live_state(
            "liveoperation0000000000000000000",
            {
                "stage": stage, "status": status, "current_item": "Fictional Collection",
                "latest_message": "Meaningful progress", "heartbeat_at": datetime.now(UTC).isoformat(),
                "progress": {"completed": completed, "total": 4, "percent": completed * 25, "unit": "collections"},
                "counts": {"collections": completed, "products": completed * 2, "variations": completed * 5, "warnings": warnings, "failures": 0},
                "summary": {"variations_processed": completed * 5, "warnings": warnings},
                "discord": {"state": "pending", "label": "Pending", "events": []},
                "next_sequence": len(logs) + 1,
            },
            logs,
        )


def test_separate_reader_observes_persisted_stage_counts_heartbeat_and_logs(live_app):
    entries = [{"sequence": 1, "severity": "info", "line": "12:00:00 [ℹ️] first safe line"}]
    _write(live_app, stage="comparing_projection", completed=2, warnings=1, logs=entries)
    reset_operation_control_for_tests()
    client = _client(live_app)
    status = client.get("/api/operations/liveoperation0000000000000000000/status")
    logs = client.get("/api/operations/liveoperation0000000000000000000/logs?after=0")
    assert status.status_code == logs.status_code == 200
    assert status.headers["Cache-Control"].startswith("no-store")
    assert logs.headers["Cache-Control"].startswith("no-store")
    payload = status.get_json()
    assert payload["live"]["stage"] == "comparing_projection"
    assert payload["live"]["counts"]["products"] == 4
    assert payload["last_activity"]
    assert logs.get_json()["entries"][0]["sequence"] == 1


def test_reader_observes_changes_and_terminal_state_without_writer_memory(live_app):
    client = _client(live_app)
    _write(live_app, stage="scanning", completed=1)
    assert client.get("/api/operations/liveoperation0000000000000000000/status").get_json()["live"]["stage"] == "scanning"
    _write(live_app, stage="ingesting", completed=3, warnings=2)
    with live_app.app_context():
        row = db.session.get(CatalogueOperation, "liveoperation0000000000000000000")
        row.status = "partial"
        row.finished_at = datetime.now(UTC).replace(tzinfo=None)
        db.session.commit()
    reset_operation_control_for_tests()
    payload = client.get("/api/operations/liveoperation0000000000000000000/status").get_json()
    assert payload["live"]["stage"] == "ingesting"
    assert payload["live"]["counts"]["warnings"] == 2
    assert payload["terminal"] is True
    assert payload["operation"]["status_label"] == "Completed with warnings"


def test_log_cursor_is_stable_empty_safe_and_reports_rollover(live_app):
    entries = [
        {"sequence": index, "severity": "info", "line": f"12:00:00 [ℹ️] line {index}"}
        for index in range(1, PERSISTED_LOG_LINE_LIMIT + 40)
    ]
    _write(live_app, logs=entries)
    client = _client(live_app)
    first = client.get("/api/operations/liveoperation0000000000000000000/logs?after=1").get_json()
    assert first["gap"] is True
    assert first["entries"][0]["sequence"] == 40
    cursor = first["next_cursor"]
    later = client.get(f"/api/operations/liveoperation0000000000000000000/logs?after={cursor}").get_json()
    assert all(entry["sequence"] > cursor for entry in later["entries"])
    empty = client.get(f"/api/operations/liveoperation0000000000000000000/logs?after={PERSISTED_LOG_LINE_LIMIT + 39}").get_json()
    assert empty["entries"] == []
    assert empty["terminal"] is False
    stale = client.get("/api/operations/liveoperation0000000000000000000/logs?after=9999").get_json()
    assert stale["gap"] is True
    assert stale["entries"][0]["sequence"] == 40


def test_persisted_feed_is_bounded_and_active_operation_is_database_visible(live_app):
    entries = [
        {"sequence": index, "severity": "warning", "line": "x" * 4000}
        for index in range(1, 900)
    ]
    _write(live_app, logs=entries)
    reset_operation_control_for_tests()
    with live_app.app_context():
        row = db.session.get(CatalogueOperation, "liveoperation0000000000000000000")
        live = persisted_live_state(row)
        assert len(live["logs"]) <= PERSISTED_LOG_LINE_LIMIT
        assert sum(len(item["line"].encode()) for item in live["logs"]) <= PERSISTED_LOG_BYTE_LIMIT
        active = get_active_operation()
        assert active["id"] == row.id
        assert active["stage"] == "scanning"
        assert active["heartbeat_at"]
    client = _client(live_app)
    status_response = client.get("/api/operations/liveoperation0000000000000000000/status")
    log_response = client.get("/api/operations/liveoperation0000000000000000000/logs?after=0")
    assert len(status_response.data) < 20_000
    assert len(log_response.data) < 450_000


def test_live_endpoints_use_two_read_queries_each(live_app):
    _write(live_app, logs=[{
        "sequence": 1, "severity": "info", "line": "12:00:00 [ℹ️] safe line",
    }])
    client = _client(live_app)
    statements = []

    def count_query(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    with live_app.app_context():
        engine = db.engine
        event.listen(engine, "before_cursor_execute", count_query)
    try:
        assert client.get(
            "/api/operations/liveoperation0000000000000000000/status"
        ).status_code == 200
        status_queries = len(statements)
        statements.clear()
        assert client.get(
            "/api/operations/liveoperation0000000000000000000/logs?after=0"
        ).status_code == 200
        log_queries = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", count_query)

    # One authenticated-user load and one operation-row read per request.
    assert status_queries == 2
    assert log_queries == 2


def test_live_state_redacts_sensitive_diagnostics(live_app):
    _write(live_app, logs=[{
        "sequence": 1, "severity": "error",
        "line": "Authorization: Bearer secret-value at /Users/person/private/catalogue",
    }])
    with live_app.app_context():
        serialized = db.session.get(CatalogueOperation, "liveoperation0000000000000000000").scope
    assert "secret-value" not in serialized
    assert "/Users/person" not in serialized


def test_prepopulated_full_scan_exposes_live_stages_and_terminal_warning(live_app, monkeypatch):
    from app.utils import scan_runner

    with live_app.app_context():
        seed = db.session.get(CatalogueOperation, "liveoperation0000000000000000000")
        db.session.delete(seed)
        settings = Settings.query.one()
        collection_path = __import__("pathlib").Path(settings.product_folder) / "Existing Collection"
        collection_path.mkdir()
        (collection_path / ".scanned").write_text('{"sku":"PRE-001"}')
        collection = Collection(
            name="Existing Collection", root_path=str(collection_path), source_relpath="Existing Collection",
            shared_json_path=str(collection_path / "product_info.json"), shared_json_relpath="Existing Collection/product_info.json",
            sku_prefix="PRE-", collection_type="Single Variable",
        )
        db.session.add(collection)
        db.session.flush()
        product = Product(sku="PRE-001", title="Existing Product", product_type="variable", collection_id=collection.id, source_relpath="Existing Collection")
        db.session.add(product)
        db.session.flush()
        variation = Variation(product_id=product.id, sku="PRE-001-A", source_identity="Style=A", source_relpath="Existing Collection/Style A")
        db.session.add(variation)
        db.session.flush()
        db.session.add_all([
            ProductImage(product_id=product.id, url="https://uploads.invalid/parent.webp", position=0),
            VariationImage(variation_id=variation.id, url="https://uploads.invalid/variation.webp", position=0),
            ProductAsset(product_id=product.id, path=str(collection_path / "Parent" / "parent.png"), source_relpath="Existing Collection/Parent/parent.png", kind="image"),
            ProductAsset(product_id=product.id, variation_id=variation.id, path=str(collection_path / "Style A" / "variation.png"), source_relpath="Existing Collection/Style A/variation.png", kind="image"),
        ])
        db.session.commit()

    plan = scan_runner.ScanScopePlan((str(collection_path),), frozenset(), {}, False, True)
    monkeypatch.setattr(scan_runner, "build_scan_scope", lambda *args, **kwargs: plan)
    monkeypatch.setattr(scan_runner, "LIVE_HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(scan_runner, "recover_committed_markers", lambda *args, **kwargs: {"recovered": 0})

    def slow_scan(*args, **kwargs):
        time.sleep(0.12)
        kwargs["log"]("fixture source image warning", level="WARN")
        return [
            {"Type": "variable", "SKU": "PRE-001", "Images": "https://uploads.invalid/parent.webp"},
            {"Type": "variation", "SKU": "PRE-001-A", "Parent": "PRE-001", "Images": "https://uploads.invalid/variation.webp"},
        ]

    def slow_ingest(*args, **kwargs):
        time.sleep(0.12)
        return {"products_created": 0, "products_updated": 1, "products_failed": 0, "variations_created": 0, "variations_updated": 1}

    monkeypatch.setattr(scan_runner, "scan_collection", slow_scan)
    monkeypatch.setattr(scan_runner, "ingest_rows_to_db", slow_ingest)
    monkeypatch.setattr(scan_runner, "finalize_ingested_markers", lambda *args, **kwargs: {"finalized": 1, "database_recovery_required": 0, "marker_recovery_required": 0, "errors": []})
    operation_id = scan_runner.start_scan(live_app, "prepopulated-full-live", scan_mode="full")
    client = _client(live_app)
    observed_stages = set()
    observed_heartbeats = set()
    terminal = None
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        payload = client.get(f"/api/operations/{operation_id}/status").get_json()
        observed_stages.add(payload["live"].get("stage"))
        observed_heartbeats.add(payload.get("last_activity"))
        if payload["terminal"]:
            terminal = payload
            break
        time.sleep(0.03)
    assert {"scanning", "ingesting"}.issubset(observed_stages)
    assert len({value for value in observed_heartbeats if value}) >= 2
    assert terminal["operation"]["status"] == "partial"
    assert terminal["operation"]["warning_count"] >= 1
    assert terminal["summary"]["parent_images"] == 1
    assert terminal["summary"]["variation_images"] == 1
    logs = client.get(f"/api/operations/{operation_id}/logs?after=0").get_json()
    assert any("fixture source image warning" in entry["line"] for entry in logs["entries"])
