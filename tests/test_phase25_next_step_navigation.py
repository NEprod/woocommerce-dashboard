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
    }
    for name in states:
        prepared = intake / "Prepared" / name
        _image(prepared / "Product" / "image.png")
        if name in {"Existing Metadata", "Metadata Complete", "Handoff Complete"}:
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
            status = "failed" if name == "Failed Result" else "succeeded"
            extra = {}
            if name == "Handoff Complete":
                extra = {
                    "catalogue_destination": name,
                    "handoff_action": "created",
                    "completion_time": "2026-08-29T09:10:00",
                    "rollback_state": "not_required",
                    "recovery_state": "none",
                }
            db.session.add(
                CatalogueOperation(
                    id=f"{index:x}" * 32,
                    operation_type=operation_type,
                    status=status,
                    recovery_state="manual_recovery_required" if name == "Recovery Result" else "none",
                    scope=_scope(f"Prepared/{name}", state, **extra),
                    started_at=started + timedelta(minutes=index),
                    finished_at=started + timedelta(minutes=index, seconds=20),
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


def test_stale_link_revalidates_and_opens_the_current_valid_stage(navigation_app, navigation_client):
    html = navigation_client.get(
        "/image-preparation", query_string={"path": "Prepared/Grouped"}
    ).get_data(as_text=True)
    stale_href = _next_href(html)
    with navigation_app[0].app_context():
        db.session.add(
            CatalogueOperation(
                id="a" * 32,
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
