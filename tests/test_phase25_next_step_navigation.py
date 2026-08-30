import json
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from PIL import Image

from app import create_app, db
from app.models import CatalogueOperation, Settings, User
from config import Config


def _image(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (10, 8), (44, 118, 166)).save(path)


def _metadata():
    return {
        "collection_type": "Simple",
        "title": "Fictional Intake Product",
        "sku_prefix": "FIP-",
        "price": "12.00",
        "categories": ["Fictional"],
        "tags": ["intake"],
        "live": False,
    }


def _scope(relative, state, **extra):
    summary = {
        "prepared_relpath": relative,
        "workflow_status": state,
        "warnings": 0,
        **extra,
    }
    return json.dumps(
        {
            "source_relpath": relative,
            "workflow_status": state,
            "operation_summary": summary,
        },
        separators=(",", ":"),
    )


@pytest.fixture
def navigation_app(tmp_path):
    instance = tmp_path / "instance"
    intake = tmp_path / "intake"
    catalogue = tmp_path / "catalogue"
    output = tmp_path / "output"
    for folder in (instance, intake, catalogue, output):
        folder.mkdir()

    states = {
        "Grouped": ("intake_group", "folder_review_required"),
        "Folders Confirmed": ("intake_folder_edit", "image_renaming_required"),
        "Images Renamed": ("intake_image_rename", "metadata_required"),
        "Existing Metadata": ("intake_image_rename", "metadata_required"),
        "Metadata Complete": ("intake_metadata_save", "validation_required"),
        "Handoff Complete": ("intake_catalogue_handoff", "catalogue_handoff_complete"),
        "Failed Result": ("intake_group", "failed"),
        "Recovery Result": ("intake_group", "folder_review_required"),
        "Metadata Warnings": ("intake_metadata_save", "validation_required"),
        "Handoff Warnings": ("intake_catalogue_handoff", "catalogue_handoff_complete"),
        "Blocked Metadata": ("intake_metadata_save", "validation_required"),
        "Live Grouping": ("intake_group", "grouping_running"),
    }
    for name in states:
        prepared = intake / "Prepared" / name
        _image(prepared / "Product" / "image.png")
        if name in {
            "Existing Metadata",
            "Metadata Complete",
            "Handoff Complete",
            "Metadata Warnings",
            "Handoff Warnings",
            "Blocked Metadata",
        }:
            (prepared / "product_info.json").write_text(
                json.dumps(_metadata(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    (catalogue / "Handoff Complete").mkdir()

    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{instance / 'site.db'}"
    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        INTAKE_ROOT=str(intake),
        INTAKE_TEST_MOUNTED=True,
        DISCORD_ENABLED=False,
    )
    started = datetime(2026, 8, 29, 9, 0)
    with app.app_context():
        db.session.add(User(email="navigation@example.test", username="navigation-admin", password="unused"))
        db.session.add(
            Settings(
                product_folder=str(catalogue),
                output_folder=str(output),
                url_prefix="https://example.test/media/",
            )
        )
        for index, (name, (operation_type, state)) in enumerate(states.items(), 1):
            status = (
                "failed"
                if name == "Failed Result"
                else "running"
                if name == "Live Grouping"
                else "partial"
                if name in {"Metadata Warnings", "Handoff Warnings", "Blocked Metadata"}
                else "succeeded"
            )
            extra = {}
            if name in {"Handoff Complete", "Handoff Warnings"}:
                extra = {
                    "catalogue_destination": name,
                    "handoff_action": "created",
                    "completion_time": "2026-08-29T09:10:00",
                    "rollback_state": "not_required",
                    "recovery_state": "none",
                }
            if name in {"Metadata Warnings", "Handoff Warnings"}:
                extra.update(
                    {
                        "warnings": 4 if name == "Metadata Warnings" else 7,
                        "blocking_errors": 0,
                        "warning_findings": [
                            {
                                "state": "warning",
                                "code": "image_fallback_broader",
                                "message": "Large variations reuse Style-level images from Gnome/. Handoff remains allowed.",
                                "path": "Gnome/",
                            },
                            {
                                "state": "warning",
                                "code": "image_fallback_broader",
                                "message": "Large variations reuse Style-level images from Snowman/. Handoff remains allowed.",
                                "path": "Snowman/",
                            },
                            {
                                "state": "warning",
                                "code": "optional_meta_description",
                                "message": "Meta description is missing.",
                                "path": "$.meta_description",
                            },
                        ],
                    }
                )
            if name == "Blocked Metadata":
                extra.update({"warnings": 2, "blocking_errors": 1, "failures": 0})
            db.session.add(
                CatalogueOperation(
                    id=f"{index:x}" * 32,
                    operation_type=operation_type,
                    status=status,
                    recovery_state="manual_recovery_required" if name == "Recovery Result" else "none",
                    scope=_scope(f"Prepared/{name}", state, **extra),
                    started_at=started + timedelta(minutes=index),
                    finished_at=None
                    if name == "Live Grouping"
                    else started + timedelta(minutes=index, seconds=20),
                )
            )
        db.session.add(
            CatalogueOperation(
                id="f" * 32,
                operation_type="intake_group",
                status="succeeded",
                scope=_scope("Prepared/Missing Result", "folder_review_required"),
                started_at=started + timedelta(minutes=20),
                finished_at=started + timedelta(minutes=20, seconds=20),
            )
        )
        db.session.commit()
    try:
        yield app, intake, catalogue, output
    finally:
        with app.app_context():
            db.session.remove()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


@pytest.fixture
def navigation_client(navigation_app):
    app, *_ = navigation_app
    client = app.test_client()
    with app.app_context():
        user_id = User.query.one().id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def _next_href(html):
    match = re.search(r'href="([^"]*/image-preparation/next/[^"]+)"', html)
    assert match
    return match.group(1)


@pytest.mark.parametrize(
    ("name", "label", "destination"),
    [
        ("Grouped", "Review and Rename Folders", "/image-preparation/folders/edit"),
        ("Folders Confirmed", "Rename Images", "/image-preparation/rename"),
        ("Images Renamed", "Create Product Metadata", "/image-preparation/metadata/edit"),
        ("Existing Metadata", "Edit Product Metadata", "/image-preparation/metadata/edit"),
        ("Metadata Complete", "Validate and Copy to Catalogue", "/image-preparation/handoff/review"),
        ("Handoff Complete", "Open Scanner", "/scanner"),
    ],
)
def test_selected_prepared_result_has_one_signed_revalidated_next_action(
    navigation_client, name, label, destination
):
    page = navigation_client.get("/image-preparation", query_string={"path": f"Prepared/{name}"})
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert label in html
    href = _next_href(html)
    assert "Prepared/" not in href

    response = navigation_client.get(href)
    assert response.status_code == 302
    assert response.headers["Location"].startswith(destination)
    if name != "Handoff Complete":
        assert parse_qs(urlparse(response.headers["Location"]).query)["path"] == [
            f"Prepared/{name}"
        ]


def test_handoff_navigation_does_not_start_a_scan_or_emit_discord(navigation_app, navigation_client, monkeypatch):
    sent = []
    monkeypatch.setattr("app.utils.discord.send_discord_message", lambda *args, **kwargs: sent.append((args, kwargs)))
    with navigation_app[0].app_context():
        before = CatalogueOperation.query.count()
    html = navigation_client.get(
        "/image-preparation", query_string={"path": "Prepared/Handoff Complete"}
    ).get_data(as_text=True)
    response = navigation_client.get(_next_href(html))
    with navigation_app[0].app_context():
        assert CatalogueOperation.query.count() == before
    assert response.headers["Location"].endswith("/scanner")
    assert sent == []


def test_prepared_browser_cards_show_only_the_current_primary_action(navigation_client):
    html = navigation_client.get(
        "/image-preparation", query_string={"path": "Prepared"}
    ).get_data(as_text=True)
    for label in (
        "Review and Rename Folders",
        "Rename Images",
        "Create Product Metadata",
        "Edit Product Metadata",
        "Validate and Copy to Catalogue",
        "Open Scanner",
    ):
        assert label in html
    assert "/private/" not in html
    assert "/Users/" not in html


def test_operation_detail_uses_next_action_and_suppresses_failed_recovery_and_missing(navigation_client):
    grouped = navigation_client.get("/operations/" + "1" * 32).get_data(as_text=True)
    assert "Review and Rename Folders" in grouped
    metadata = navigation_client.get("/operations/" + "5" * 32).get_data(as_text=True)
    assert "Validate and Copy to Catalogue" in metadata
    assert metadata.count("/image-preparation/next/") == 1
    failed = navigation_client.get("/operations/" + "7" * 32).get_data(as_text=True)
    recovery = navigation_client.get("/operations/" + "8" * 32).get_data(as_text=True)
    missing = navigation_client.get("/operations/" + "f" * 32).get_data(as_text=True)
    assert "/image-preparation/next/" not in failed
    assert "/image-preparation/next/" not in recovery
    assert "/image-preparation/next/" not in missing
    assert "No next action is available" in failed
    assert "Recovery must be resolved" in recovery
    assert "Prepared result is no longer available" in missing


def test_running_intake_detail_promises_live_action_without_failure_wording(navigation_client):
    html = navigation_client.get("/operations/" + "c" * 32).get_data(as_text=True)
    assert "This operation is still running" in html
    assert "The next action will appear automatically after successful completion" in html
    assert "did not complete successfully" not in html
    assert "/image-preparation/next/" not in html
    assert 'data-intake-result-panel' in html


def test_same_open_operation_exposes_action_after_terminal_transition(
    navigation_app, navigation_client
):
    app, *_ = navigation_app
    operation_id = "c" * 32
    initial = navigation_client.get(f"/operations/{operation_id}").get_data(
        as_text=True
    )
    assert "This operation is still running" in initial
    assert "Review and Rename Folders" not in initial

    with app.app_context():
        operation = db.session.get(CatalogueOperation, operation_id)
        operation.status = "succeeded"
        operation.scope = _scope(
            "Prepared/Live Grouping", "folder_review_required"
        )
        operation.finished_at = datetime(2026, 8, 29, 10, 0)
        db.session.commit()

    status = navigation_client.get(f"/api/operations/{operation_id}/status")
    assert status.get_json()["terminal"] is True
    fragment = navigation_client.get(
        f"/operations/{operation_id}/intake-result"
    ).get_data(as_text=True)
    assert "Review and Rename Folders" in fragment
    assert fragment.count("/image-preparation/next/") == 1
    assert "still running" not in fragment


@pytest.mark.parametrize(
    ("operation_id", "label"),
    [
        ("1" * 32, "Review and Rename Folders"),
        ("2" * 32, "Rename Images"),
        ("3" * 32, "Create Product Metadata"),
        ("5" * 32, "Validate and Copy to Catalogue"),
        ("6" * 32, "Open Scanner"),
    ],
)
def test_live_intake_result_fragment_returns_authoritative_signed_action(
    navigation_client, operation_id, label
):
    response = navigation_client.get(f"/operations/{operation_id}/intake-result")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert response.headers["Cache-Control"].startswith("no-store")
    assert label in html
    assert html.count("/image-preparation/next/") == 1
    assert "Prepared/" not in _next_href(html)
    assert "/Users/" not in html


def test_live_result_fragment_preserves_warning_details_and_action(navigation_client):
    response = navigation_client.get("/operations/" + "9" * 32 + "/intake-result")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Validate and Copy to Catalogue" in html
    assert "Completed with warnings" in html
    assert "Image fallback" in html
    assert "SEO" in html
    assert "You may continue" in html


@pytest.mark.parametrize("operation_id", ["7" * 32, "8" * 32])
def test_live_result_fragment_hides_action_for_failed_and_recovery_states(
    navigation_client, operation_id
):
    html = navigation_client.get(
        f"/operations/{operation_id}/intake-result"
    ).get_data(as_text=True)
    assert "/image-preparation/next/" not in html
    assert "Review and Rename Folders" not in html


def test_interrupted_live_result_hides_action(navigation_app, navigation_client):
    app, *_ = navigation_app
    with app.app_context():
        operation = db.session.get(CatalogueOperation, "c" * 32)
        operation.status = "interrupted"
        operation.finished_at = datetime(2026, 8, 29, 10, 0)
        db.session.commit()
    html = navigation_client.get(
        "/operations/" + "c" * 32 + "/intake-result"
    ).get_data(as_text=True)
    assert "Operation interrupted" in html
    assert "/image-preparation/next/" not in html


def test_live_result_refresh_is_authenticated_and_observational(
    navigation_app, navigation_client, monkeypatch
):
    app, intake, *_ = navigation_app
    sent = []
    monkeypatch.setattr(
        "app.utils.discord.send_discord_message",
        lambda *args, **kwargs: sent.append((args, kwargs)),
    )
    before_files = sorted(
        (path.relative_to(intake).as_posix(), path.stat().st_mtime_ns)
        for path in intake.rglob("*")
        if path.is_file()
    )
    with app.app_context():
        before_operations = CatalogueOperation.query.count()
    assert navigation_client.get(
        "/operations/" + "1" * 32 + "/intake-result"
    ).status_code == 200
    with app.app_context():
        assert CatalogueOperation.query.count() == before_operations
    after_files = sorted(
        (path.relative_to(intake).as_posix(), path.stat().st_mtime_ns)
        for path in intake.rglob("*")
        if path.is_file()
    )
    assert after_files == before_files
    assert sent == []

    anonymous = app.test_client().get(
        "/operations/" + "1" * 32 + "/intake-result"
    )
    assert anonymous.status_code == 401


def test_stale_link_revalidates_and_opens_the_current_valid_stage(navigation_app, navigation_client):
    html = navigation_client.get(
        "/image-preparation", query_string={"path": "Prepared/Grouped"}
    ).get_data(as_text=True)
    stale_href = _next_href(html)
    with navigation_app[0].app_context():
        db.session.add(
            CatalogueOperation(
                    id="d" * 32,
                operation_type="intake_folder_edit",
                status="succeeded",
                scope=_scope("Prepared/Grouped", "image_renaming_required"),
                started_at=datetime(2026, 8, 29, 12, 0),
                finished_at=datetime(2026, 8, 29, 12, 1),
            )
        )
        db.session.commit()
    response = navigation_client.get(stale_href)
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/image-preparation/rename")


def test_invalid_missing_and_ineligible_navigation_is_controlled(navigation_app, navigation_client):
    assert navigation_client.get("/image-preparation/next/not-a-valid-token").status_code == 302
    with navigation_app[0].test_request_context():
        from app.intake_navigation import navigation_token

        missing_token = navigation_token("Prepared/Missing Result", "folder_review_required")
        failed_token = navigation_token("Prepared/Failed Result", "failed")
    assert navigation_client.get(f"/image-preparation/next/{missing_token}").status_code == 302
    assert navigation_client.get(f"/image-preparation/next/{failed_token}").status_code == 302
    assert navigation_client.get(
        "/image-preparation/folders/edit",
        query_string={"path": "Prepared/Metadata Complete"},
    ).status_code == 400
    assert navigation_client.get(
        "/image-preparation/rename",
        query_string={"path": "Prepared/Metadata Complete"},
    ).status_code == 400


def test_navigation_requires_authentication_and_get_is_read_only(navigation_app, navigation_client):
    html = navigation_client.get(
        "/image-preparation", query_string={"path": "Prepared/Metadata Complete"}
    ).get_data(as_text=True)
    href = _next_href(html)
    anonymous = navigation_app[0].test_client()
    assert anonymous.get(href).status_code in {302, 401}

    prepared = navigation_app[1] / "Prepared" / "Metadata Complete"
    before = {path.relative_to(prepared).as_posix(): path.read_bytes() for path in prepared.rglob("*") if path.is_file()}
    with navigation_app[0].app_context():
        operation_count = CatalogueOperation.query.count()
    navigation_client.get(href)
    after = {path.relative_to(prepared).as_posix(): path.read_bytes() for path in prepared.rglob("*") if path.is_file()}
    with navigation_app[0].app_context():
        assert CatalogueOperation.query.count() == operation_count
    assert after == before


def test_navigation_actions_retain_accessible_mobile_touch_targets(navigation_client):
    html = navigation_client.get(
        "/image-preparation", query_string={"path": "Prepared/Grouped"}
    ).get_data(as_text=True)
    assert 'class="btn btn-accent intake-next-action"' in html
    css = navigation_client.get("/static/assets/css/custom.css").get_data(as_text=True)
    assert ".btn { display: inline-flex; min-height: 44px" in css
    assert "@media (max-width: 767px)" in css


def test_completed_with_warnings_allows_navigation_and_blockers_do_not(
    navigation_client,
):
    warning_page = navigation_client.get(
        "/image-preparation", query_string={"path": "Prepared/Metadata Warnings"}
    ).get_data(as_text=True)
    assert "Completed with warnings" in warning_page
    assert "You may continue." in warning_page
    assert "Validate and Copy to Catalogue" in warning_page

    blocked_page = navigation_client.get(
        "/image-preparation", query_string={"path": "Prepared/Blocked Metadata"}
    ).get_data(as_text=True)
    assert "Blocking errors must be fixed before proceeding." in blocked_page
    assert "Validate and Copy to Catalogue" not in blocked_page


def test_warning_details_are_grouped_expandable_and_keep_operation_next_action(
    navigation_client,
):
    operation = navigation_client.get("/operations/" + "9" * 32)
    body = operation.get_data(as_text=True)
    assert operation.status_code == 200
    assert "Review warnings" in body
    assert "Image fallback (2)" in body
    assert "Gnome/" in body and "Snowman/" in body
    assert "SEO (1)" in body
    assert "Safe to continue." in body
    assert "Optional." in body
    assert "Validate and Copy to Catalogue" in body
    assert '<details class="operation-warning-details"' in body


def test_prepared_cards_show_warning_summary_and_safe_next_action(navigation_client):
    page = navigation_client.get(
        "/image-preparation", query_string={"path": "Prepared"}
    ).get_data(as_text=True)
    assert "4 warnings" in page
    assert "Image fallback" in page
    assert "SEO" in page
    assert "Review warnings" in page
    assert "Validate and Copy to Catalogue" in page
