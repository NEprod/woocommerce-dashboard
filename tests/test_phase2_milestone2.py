from pathlib import Path
from queue import Queue

import pytest

from app import create_app, db
from app.models import User
from app.utils.scan_runner import _runs, get_progress, make_logger
from config import Config


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def milestone2_app(tmp_path):
    database = tmp_path / "instance" / "site.db"
    database.parent.mkdir()
    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{database}"
    try:
        app = create_app()
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with app.app_context():
            db.session.add(
                User(
                    email="milestone2@example.com",
                    username="milestone2-admin",
                    password="unused-test-password",
                    is_admin=True,
                )
            )
            db.session.commit()
        yield app
    finally:
        with app.app_context():
            db.session.remove()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


@pytest.fixture
def milestone2_client(milestone2_app):
    client = milestone2_app.test_client()
    with milestone2_app.app_context():
        user_id = User.query.one().id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def test_scan_progress_payload_adds_normalized_observational_state():
    run_id = "milestone2-progress"
    _runs[run_id] = {
        "total": 4,
        "done": 2,
        "status": "running",
        "queue": Queue(),
        "operation_id": "operation-123",
        "operation_type": "append",
        "scope": {"scan_mode": "append"},
        "stage": "scanning",
        "current_item": "Fictional Collection",
        "warnings": 1,
        "errors": 0,
        "summary": {
            "folders": 4,
            "collections_processed": 2,
            "products_attempted": 3,
            "products_succeeded": 2,
            "products_failed": 1,
            "variations_processed": 7,
            "started_at": "2026-01-01T00:00:00",
            "finished_at": None,
        },
    }
    try:
        payload = get_progress(run_id)
    finally:
        _runs.pop(run_id, None)

    # Existing clients retain their established contract.
    assert payload["total"] == 4
    assert payload["done"] == 2
    assert payload["status"] == "running"
    assert payload["summary"]["folders"] == 4

    assert payload["operation"] == {
        "id": "operation-123",
        "type": "append",
        "status": "running",
        "stage": "scanning",
        "current_item": "Fictional Collection",
        "scope": {"scan_mode": "append"},
    }
    assert payload["progress"] == {
        "completed": 2,
        "total": 4,
        "percent": 50,
        "unit": "collections",
    }
    assert payload["counts"] == {
        "collections": 2,
        "products": 3,
        "variations": 7,
        "warnings": 1,
        "failures": 1,
    }


def test_progress_logger_observes_warning_and_error_counts_without_changing_lines():
    run_id = "milestone2-logger"
    _runs[run_id] = {
        "queue": Queue(),
        "warnings": 0,
        "errors": 0,
    }
    try:
        log = make_logger(run_id)
        log("A warning", level="WARN")
        log("A failure", level="ERROR")
        first = _runs[run_id]["queue"].get_nowait()
        second = _runs[run_id]["queue"].get_nowait()
        assert "[⚠️] A warning" in first
        assert "[❌] A failure" in second
        assert _runs[run_id]["warnings"] == 1
        assert _runs[run_id]["errors"] == 1
    finally:
        _runs.pop(run_id, None)


def test_initial_scan_renders_accessible_shared_progress_and_completion_actions(
    milestone2_client, monkeypatch
):
    from app import routes
    from app.utils.reconstruction import SetupState

    monkeypatch.setattr(
        routes,
        "detect_setup_state",
        lambda: SetupState(
            "new_catalogue",
            2,
            3,
            0,
            0,
            0,
            True,
            "append",
            False,
            message="Ready for a safe initial scan.",
        ),
    )

    response = milestone2_client.get("/initial-scan")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert 'data-operation-progress' in html
    assert 'role="status"' in html
    assert 'aria-live="polite"' in html
    assert 'id="setupCompletion"' in html
    assert "Open Dashboard" in html
    assert "View Products" in html
    assert "View Operation Details" in html
    assert "Collections processed" in html
    assert "Parent products" in html
    assert "Variations" in html
    assert "Warnings" in html
    assert "Failures" in html
    assert "window.location" not in html


def test_shared_progress_component_is_used_by_update_modal():
    modal = (ROOT / "app/templates/includes/_scan_modal.html").read_text(
        encoding="utf-8"
    )
    component = (ROOT / "app/templates/includes/_operation_progress.html").read_text(
        encoding="utf-8"
    )
    scripts = (ROOT / "app/templates/includes/scripts.html").read_text(
        encoding="utf-8"
    )

    assert "operation_progress" in modal
    assert "data-operation-progress" in component
    assert "operation-progress.js" in scripts


def test_operation_progress_script_uses_real_payload_and_accessible_states():
    script = (ROOT / "app/static/assets/js/operation-progress.js").read_text(
        encoding="utf-8"
    )

    assert "payload.counts" in script
    assert "payload.progress" in script
    assert "aria-valuenow" in script
    assert "aria-busy" in script
    assert "OperationProgress" in script
    assert "setInterval" not in script

