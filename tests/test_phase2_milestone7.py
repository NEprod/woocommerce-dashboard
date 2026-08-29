import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from app import create_app, db
from app.models import CatalogueOperation, CatalogueOperationItem, Collection, Product, Settings, User
from config import Config
from app.utils.scan_runner import BoundedLogQueue, _notify_once, _runs


@pytest.fixture
def operations_app(tmp_path, monkeypatch):
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
            user = User(email="operations@example.test", username="operator", password="unused")
            settings = Settings(
                product_folder=str(catalogue),
                output_folder=str(output),
                url_prefix="https://uploads.invalid/",
            )
            collection = Collection(
                name="Shared metadata title",
                root_path=str(catalogue / "Folder Collection"),
                source_relpath="Folder Collection",
                shared_json_path=str(catalogue / "Folder Collection" / "product_info.json"),
                shared_json_relpath="Folder Collection/product_info.json",
                sku_prefix="FIX-",
                collection_type="Simple",
            )
            db.session.add_all([user, settings, collection])
            db.session.flush()
            product = Product(
                sku="FIX-001", title="Fictional Product", product_type="simple",
                collection_id=collection.id, source_relpath="Folder Collection/Product",
            )
            db.session.add(product)
            now = datetime.now()
            for index in range(65):
                status = ["succeeded", "failed", "partial", "interrupted"][index % 4]
                operation = CatalogueOperation(
                    id=f"operation{index:023d}", operation_type=["append", "product_update", "full"][index % 3],
                    status=status, scope=json.dumps({"sku": "FIX-001", "collection_relpath": "Folder Collection"}),
                    started_at=now - timedelta(hours=index + 1), finished_at=now - timedelta(hours=index),
                    products_attempted=3, products_succeeded=2, products_failed=int(status != "succeeded"),
                    recovery_state="marker_recovery_required" if index == 2 else "none",
                    error="safe failure" if status != "succeeded" else None,
                )
                db.session.add(operation)
                db.session.add(CatalogueOperationItem(
                    operation_id=operation.id, source_path="Folder Collection/Product", sku="FIX-001",
                    status="failed" if status != "succeeded" else "succeeded",
                    error="Authorization: Bearer secret-value" if index == 1 else None,
                ))
            db.session.commit()
        yield app
    finally:
        Config.SQLALCHEMY_DATABASE_URI = original_uri


def _client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True
    return client


@pytest.mark.parametrize("path", ["/scanner", "/operations", "/operations/operation00000000000000000000000"])
def test_workspaces_require_authentication(operations_app, path):
    assert operations_app.test_client().get(path).status_code in {302, 401}


def test_scanner_lists_only_supported_modes_and_safe_readiness(operations_app):
    html = _client(operations_app).get("/scanner").get_data(as_text=True)
    assert all(label in html for label in ("Append", "Update", "Full"))
    assert "Reconstruction" not in html
    assert "Catalogue available" in html
    assert "Output available" in html
    assert str(operations_app.instance_path) not in html
    assert "/tmp/" not in html


def test_scanner_start_requires_confirmation_and_valid_mode(operations_app):
    client = _client(operations_app)
    assert client.post("/scanner/start", json={"mode": "append"}).status_code == 400
    assert client.post("/scanner/start", json={"mode": "reconstruction", "confirm": True}).status_code == 400
    assert client.post("/scanner/start", json={"mode": "full", "confirm": True}).status_code == 400


def test_confirmed_scanner_start_uses_existing_runner_once(operations_app, monkeypatch):
    from app import routes
    calls = []
    monkeypatch.setattr(routes, "start_scan", lambda app, run_id, **kwargs: calls.append((run_id, kwargs)) or "new-operation-id")
    response = _client(operations_app).post("/scanner/start", json={"mode": "append", "confirm_operation": True})
    assert response.status_code == 202
    assert response.get_json() == {
        "ok": True,
        "operation_id": "new-operation-id",
        "destination": "/operations/new-operation-id",
    }
    assert len(calls) == 1
    assert calls[0][1]["scan_mode"] == "append"


def test_active_operation_is_visible_after_scanner_refresh(operations_app, monkeypatch):
    from app import operations_workspace

    monkeypatch.setattr(
        operations_workspace,
        "get_active_operation",
        lambda: {"id": "operation00000000000000000000000", "operation_type": "full", "started_at": "2026-08-21T09:00:00"},
    )
    html = _client(operations_app).get("/scanner").get_data(as_text=True)
    assert "Full is running" in html
    assert 'href="/operations/operation00000000000000000000000"' in html
    assert "Follow progress" in html


def test_dashboard_links_directly_to_the_same_active_operation(operations_app, monkeypatch):
    from app import dashboard

    operation_id = "operation00000000000000000000000"
    monkeypatch.setattr(
        dashboard,
        "get_active_operation",
        lambda: {"id": operation_id, "operation_type": "full", "started_at": "2026-08-21T09:00:00"},
    )
    html = _client(operations_app).get("/").get_data(as_text=True)
    assert "Operation in progress" in html
    assert f'href="/operations/{operation_id}"' in html
    assert "Open Operation" in html


def test_missing_output_mount_blocks_start_without_exposing_path(operations_app, monkeypatch):
    from app import routes
    with operations_app.app_context():
        Settings.query.first().output_folder = "/not/a/real/output/location"
        db.session.commit()
    monkeypatch.setattr(routes, "start_scan", lambda *a, **k: pytest.fail("runner started"))
    response = _client(operations_app).post("/scanner/start", json={"mode": "append", "confirm_operation": True})
    assert response.status_code == 409
    assert "/not/a/real" not in response.get_data(as_text=True)


def test_operations_browser_is_paginated_filterable_and_uses_folder_name(operations_app):
    response = _client(operations_app).get("/operations?status=failed&type=append&per_page=25")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Operation History" in html
    assert "Page 1" in html
    assert "Folder Collection" in html
    assert "Shared metadata title" not in html
    assert len(html.encode()) < 250_000


def test_operation_detail_is_safe_bounded_and_links_related_entities(operations_app):
    response = _client(operations_app).get("/operations/operation00000000000000000000001")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Fictional Product" in html
    assert "Folder Collection" in html
    assert "secret-value" not in html
    assert "Cancellation is not supported" in html


def test_unknown_operation_is_controlled_404(operations_app):
    assert _client(operations_app).get("/operations/not-present").status_code == 404


def test_operation_status_api_is_bounded_and_polling_is_observational(operations_app):
    client = _client(operations_app)
    response = client.get("/api/operations/operation00000000000000000000000/status")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["terminal"] is True
    assert payload["operation"]["status"] == "succeeded"


def test_running_operation_duration_uses_utc_not_local_wall_clock(operations_app, monkeypatch):
    from app import operations_workspace

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 21, 13, 0, 30, tzinfo=tz)

    monkeypatch.setattr(operations_workspace, "datetime", FixedDateTime)
    with operations_app.app_context(), operations_app.test_request_context("/operations/test"):
        operation = CatalogueOperation(
            id="utc-duration-operation",
            operation_type="full",
            status="running",
            scope="{}",
            started_at=datetime(2026, 8, 21, 13, 0, 0),
        )
        db.session.add(operation)
        db.session.commit()
        assert operations_workspace.operation_view(operation)["duration"] == 30


def test_operation_browser_and_detail_queries_remain_bounded(operations_app):
    statements = []
    with operations_app.app_context():
        engine = db.engine
        def count_statement(*_args):
            statements.append(1)
        event.listen(engine, "before_cursor_execute", count_statement)
        try:
            browser = _client(operations_app).get("/operations?per_page=25")
            browser_queries = len(statements)
            statements.clear()
            detail = _client(operations_app).get("/operations/operation00000000000000000000000")
            detail_queries = len(statements)
        finally:
            event.remove(engine, "before_cursor_execute", count_statement)
    assert browser.status_code == detail.status_code == 200
    assert browser_queries <= 8, (browser_queries, detail_queries)
    assert detail_queries <= 8
    assert len(browser.data) < 250_000
    assert len(detail.data) < 200_000


def test_navigation_marks_operation_detail_active(operations_app):
    html = _client(operations_app).get("/operations/operation00000000000000000000000").get_data(as_text=True)
    assert 'title="Operations"' in html
    assert 'aria-current="page"' in html


def test_persisted_warning_summary_survives_process_memory_loss(operations_app):
    with operations_app.app_context():
        operation = db.session.get(CatalogueOperation, "operation00000000000000000000000")
        operation.status = "partial"
        scope = json.loads(operation.scope)
        scope["operation_summary"] = {
            "warnings": 3,
            "warning_summary": [{"category": "missing source images", "count": 3, "samples": ["Folder Collection/Product"]}],
            "parent_images": 2,
            "variation_images": 4,
            "total_images": 6,
            "output_images_copied": 5,
        }
        operation.scope = json.dumps(scope)
        db.session.commit()
        with operations_app.test_request_context("/operations/operation00000000000000000000000"):
            view = __import__("app.operations_workspace", fromlist=["operation_view"]).operation_view(operation)
        assert view["warning_count"] == 3
        assert view["summary"]["total_images"] == 6
        assert view["warning_summary"][0]["category"] == "missing source images"


def test_large_log_history_is_bounded_paginated_and_chronological(operations_app):
    with operations_app.app_context():
        operation = db.session.get(CatalogueOperation, "operation00000000000000000000000")
        operation.products_attempted = 500
        operation.products_succeeded = 492
        operation.products_failed = 8
        operation.scope = json.dumps({
            "collection_relpath": "Large Fictional Collection", "variations_processed": 5000,
            "products_created": 240, "products_updated": 180, "products_skipped": 80,
            "warnings": 12, "images_discovered": 900, "parent_images": 400,
            "variation_images": 500, "output_images_copied": 875,
        })
        for index in range(300):
            db.session.add(CatalogueOperationItem(
                operation_id=operation.id, source_path=f"Large Fictional Collection/Product {index:03d}",
                sku=f"LARGE-{index:04d}", status="failed" if index < 8 else "succeeded",
                database_state="rolled_back" if index < 8 else "committed",
                marker_state="database_recovery_required" if index < 8 else "finalized",
            ))
        db.session.commit()
    queue = BoundedLogQueue()
    for index in range(3000):
        queue.put(f"12:00:00 [ℹ️] fictional line {index:04d}")
    assert len(queue.snapshot()) <= 2000
    _runs["large-fictional-run"] = {
        "queue": queue, "operation_id": "operation00000000000000000000000",
        "status": "done", "total": 28, "done": 28,
        "summary": {"products_attempted": 500, "variations_processed": 5000, "warnings": 12,
                    "images_discovered": 900, "output_images_copied": 875},
        "operation_type": "append", "scope": {}, "stage": "completed",
        "current_item": None, "warnings": 0, "errors": 0,
        "discord": {"state": "sent", "label": "Sent", "events": []},
    }
    try:
        payload = _client(operations_app).get(
            "/api/operations/operation00000000000000000000000/logs?page=2&per_page=50&q=fictional"
        ).get_json()
        assert len(payload["items"]) == 50
        assert payload["total"] <= 2000
        assert payload["items"] == sorted(payload["items"])
    finally:
        _runs.pop("large-fictional-run", None)


def test_notification_event_is_process_idempotent():
    _runs["notification-idempotency"] = {
        "notification_events": set(),
        "discord": {"state": "pending", "label": "Pending", "events": []},
    }
    calls = []
    try:
        assert _notify_once("notification-idempotency", "completed", lambda: calls.append(1) or (True, "sent"))[0]
        assert _notify_once("notification-idempotency", "completed", lambda: calls.append(2) or (True, "sent")) == (False, "duplicate skipped")
        assert calls == [1]
    finally:
        _runs.pop("notification-idempotency", None)
