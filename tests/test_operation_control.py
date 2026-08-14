import time

import pytest

from app import create_app, db
from app.models import CatalogueOperation, Product, ProductAsset
from app.utils.operation_control import (
    CatalogueOperationActive,
    acquire_catalogue_operation,
    finish_catalogue_operation,
    get_active_operation,
    operation_context,
    reset_operation_control_for_tests,
)
from app.utils.scan_runner import get_progress, start_scan
from config import Config


@pytest.fixture
def operation_app(tmp_path):
    database = tmp_path / "operation.db"
    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{database}"
    reset_operation_control_for_tests()
    try:
        app = create_app()
        app.config.update(TESTING=True, LOGIN_DISABLED=True, WTF_CSRF_ENABLED=False)
        yield app
    finally:
        with app.app_context():
            db.session.remove()
        reset_operation_control_for_tests()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


def test_operation_lock_rejects_second_mutation_and_persists_completion(operation_app):
    with operation_app.app_context():
        first = acquire_catalogue_operation("append", {"mode": "append"})

        with pytest.raises(CatalogueOperationActive) as error:
            acquire_catalogue_operation("full", {"mode": "full"})

        assert error.value.active["id"] == first.id
        assert error.value.active["operation_type"] == "append"
        assert get_active_operation()["id"] == first.id

        finish_catalogue_operation(
            first.id,
            status="succeeded",
            products_attempted=2,
            products_succeeded=2,
            products_failed=0,
        )
        row = db.session.get(CatalogueOperation, first.id)
        assert row.status == "succeeded"
        assert row.finished_at is not None
        assert row.products_attempted == 2
        assert row.products_succeeded == 2
        assert get_active_operation() is None


def test_operation_context_releases_lock_and_records_sanitized_exception(operation_app):
    with operation_app.app_context():
        with pytest.raises(RuntimeError, match="fixture failure"):
            with operation_context(
                "product_update", {"sku": "FIC-0001", "token": "scope-secret"}
            ):
                raise RuntimeError(
                    "fixture failure token=do-not-store "
                    "https://discord.com/api/webhooks/123/private"
                )

        row = CatalogueOperation.query.one()
        assert row.status == "failed"
        assert row.finished_at is not None
        assert "do-not-store" not in row.error
        assert "private" not in row.error
        assert "[REDACTED]" in row.error
        assert "scope-secret" not in row.scope
        assert get_active_operation() is None

        next_operation = acquire_catalogue_operation("append")
        finish_catalogue_operation(next_operation.id, status="succeeded")


def test_startup_recovery_marks_stale_running_operations_interrupted(operation_app):
    with operation_app.app_context():
        stale = CatalogueOperation(
            id="stale-operation",
            operation_type="reconstruction",
            status="running",
            scope="{}",
        )
        db.session.add(stale)
        db.session.commit()
        db.session.remove()

    restarted = create_app()

    assert restarted.config["INTERRUPTED_OPERATIONS_RECOVERED"] == 1
    with restarted.app_context():
        stale = db.session.get(CatalogueOperation, "stale-operation")
        assert stale.status == "interrupted"
        assert stale.recovery_state == "review_required"
        assert stale.finished_at is not None


def test_scan_start_route_reports_active_operation_conflict(operation_app):
    with operation_app.app_context():
        lease = acquire_catalogue_operation("append", {"mode": "append"})

    response = operation_app.test_client().post(
        "/initial-scan/start", json={"mode": "full"}
    )

    assert response.status_code == 409
    body = response.get_json()
    assert body["error"] == "catalogue_operation_active"
    assert body["active_operation"]["id"] == lease.id
    assert body["active_operation"]["operation_type"] == "append"

    with operation_app.app_context():
        finish_catalogue_operation(lease.id, status="succeeded")


def test_editor_rejects_active_operation_before_changing_json(
    operation_app, tmp_path
):
    metadata = tmp_path / "product_info.json"
    metadata.write_text('{"title": "Original"}', encoding="utf-8")
    with operation_app.app_context():
        product = Product(sku="FIC-LOCK", title="Fixture Product")
        db.session.add(product)
        db.session.flush()
        db.session.add(
            ProductAsset(
                product_id=product.id,
                path=str(metadata),
                kind="info",
                label="override",
            )
        )
        db.session.commit()
        lease = acquire_catalogue_operation("append")

    response = operation_app.test_client().post(
        "/edit_products/FIC-LOCK/save",
        json={"kind": "override", "data": {"title": "Changed"}},
    )

    assert response.status_code == 409
    assert response.get_json()["active_operation"]["id"] == lease.id
    assert metadata.read_text(encoding="utf-8") == '{"title": "Original"}'
    with operation_app.app_context():
        finish_catalogue_operation(lease.id, status="succeeded")


def test_scan_thread_exception_records_failure_and_releases_lock(
    operation_app, tmp_path, monkeypatch
):
    from app.models import Settings

    catalogue = tmp_path / "catalogue"
    output = tmp_path / "output"
    catalogue.mkdir()
    output.mkdir()
    with operation_app.app_context():
        db.session.add(
            Settings(
                product_folder=str(catalogue),
                output_folder=str(output),
                url_prefix="https://invalid.example/",
            )
        )
        db.session.commit()

    def fail_ingest(*args, **kwargs):
        raise RuntimeError("fixture ingest failure")

    monkeypatch.setattr("app.utils.scan_runner.ingest_rows_to_db", fail_ingest)
    run_id = "failing-run"
    start_scan(operation_app, run_id, scan_mode="append")

    deadline = time.monotonic() + 5
    while get_progress(run_id)["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.01)

    assert get_progress(run_id)["status"] == "error"
    with operation_app.app_context():
        row = CatalogueOperation.query.one()
        assert row.status == "failed"
        assert "fixture ingest failure" in row.error
        assert get_active_operation() is None


def test_thread_start_failure_records_failure_and_releases_lock(
    operation_app, monkeypatch
):
    monkeypatch.setattr(
        "app.utils.scan_runner.threading.Thread.start",
        lambda self: (_ for _ in ()).throw(RuntimeError("thread start failure")),
    )

    with pytest.raises(RuntimeError, match="thread start failure"):
        start_scan(operation_app, "thread-start-failure")

    with operation_app.app_context():
        row = CatalogueOperation.query.one()
        assert row.status == "failed"
        assert "thread start failure" in row.error
        assert get_active_operation() is None


def test_notification_exception_does_not_leak_operation_lock(
    operation_app, tmp_path, monkeypatch
):
    from app.models import Settings

    catalogue = tmp_path / "catalogue"
    output = tmp_path / "output"
    catalogue.mkdir()
    output.mkdir()
    with operation_app.app_context():
        db.session.add(
            Settings(
                product_folder=str(catalogue),
                output_folder=str(output),
                url_prefix="https://invalid.example/",
            )
        )
        db.session.commit()

    monkeypatch.setattr(
        "app.utils.scan_runner.ingest_rows_to_db",
        lambda *args, **kwargs: {
            "products_created": 0,
            "products_updated": 0,
            "variations_created": 0,
            "variations_updated": 0,
        },
    )
    monkeypatch.setattr(
        "app.utils.scan_runner.notify_scan_completed",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("notify failure")),
    )
    run_id = "notification-run"
    start_scan(operation_app, run_id, scan_mode="append")

    deadline = time.monotonic() + 5
    while get_progress(run_id)["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.01)

    assert get_progress(run_id)["status"] == "done"
    with operation_app.app_context():
        row = CatalogueOperation.query.one()
        assert row.status == "succeeded"
        assert get_active_operation() is None


def test_scan_history_records_partial_parent_projection_counts(
    operation_app, tmp_path, monkeypatch
):
    from app.models import Settings

    catalogue = tmp_path / "catalogue"
    output = tmp_path / "output"
    catalogue.mkdir()
    output.mkdir()
    with operation_app.app_context():
        db.session.add(
            Settings(
                product_folder=str(catalogue),
                output_folder=str(output),
                url_prefix="https://invalid.example/",
            )
        )
        db.session.commit()

    received = {}

    def partial_ingest(*args, **kwargs):
        received["operation_id"] = kwargs.get("operation_id")
        return {
            "products_created": 1,
            "products_updated": 1,
            "products_failed": 1,
            "variations_created": 2,
            "variations_updated": 1,
        }

    monkeypatch.setattr(
        "app.utils.scan_runner.ingest_rows_to_db", partial_ingest
    )
    run_id = "partial-parent-run"
    operation_id = start_scan(operation_app, run_id, scan_mode="append")

    deadline = time.monotonic() + 5
    while get_progress(run_id)["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.01)

    assert get_progress(run_id)["status"] == "done"
    assert received["operation_id"] == operation_id
    with operation_app.app_context():
        row = db.session.get(CatalogueOperation, operation_id)
        assert row.status == "partial"
        assert row.products_attempted == 3
        assert row.products_succeeded == 2
        assert row.products_failed == 1
        assert "1 parent projection(s) failed" in row.error
        assert get_active_operation() is None


def test_scan_history_does_not_claim_success_when_marker_recovery_is_required(
    operation_app, tmp_path, monkeypatch
):
    from app.models import Settings

    catalogue = tmp_path / "catalogue"
    output = tmp_path / "output"
    catalogue.mkdir()
    output.mkdir()
    with operation_app.app_context():
        db.session.add(
            Settings(
                product_folder=str(catalogue),
                output_folder=str(output),
                url_prefix="https://invalid.example/",
            )
        )
        db.session.commit()

    monkeypatch.setattr(
        "app.utils.scan_runner.ingest_rows_to_db",
        lambda *args, **kwargs: {
            "products_created": 1,
            "products_updated": 0,
            "products_failed": 0,
            "variations_created": 0,
            "variations_updated": 0,
        },
    )
    monkeypatch.setattr(
        "app.utils.scan_runner.finalize_ingested_markers",
        lambda *args, **kwargs: {
            "finalized": 0,
            "database_recovery_required": 0,
            "marker_recovery_required": 1,
            "errors": ["fixture marker failure"],
        },
    )
    run_id = "marker-recovery-run"
    operation_id = start_scan(operation_app, run_id, scan_mode="append")

    deadline = time.monotonic() + 5
    while get_progress(run_id)["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.01)

    assert get_progress(run_id)["status"] == "done"
    with operation_app.app_context():
        row = db.session.get(CatalogueOperation, operation_id)
        assert row.status == "failed"
        assert row.products_attempted == 1
        assert row.products_succeeded == 0
        assert row.products_failed == 1
        assert row.marker_state == "marker_recovery_required"
        assert row.recovery_state == "marker_recovery_required"
        assert "require recovery" in row.error
        assert get_active_operation() is None
