import hashlib
import json
import re
import time
from pathlib import Path

import pytest
from PIL import Image

from app import create_app, db
from app.intake_folder_editor import folder_editor_preview
from app.models import CatalogueOperation, User
from config import Config


def _image(path, colour=(52, 132, 92)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (14, 11), colour).save(path)


def _tree(root):
    if not root.exists():
        return []
    rows = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        rows.append(
            (
                path.relative_to(root).as_posix(),
                path.is_dir(),
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
            )
        )
    return rows


@pytest.fixture
def folder_app(tmp_path):
    from app.intake_grouping import reset_intake_operation_control_for_tests

    instance = tmp_path / "instance"
    intake = tmp_path / "intake"
    catalogue = tmp_path / "catalogue"
    output = tmp_path / "output"
    for path in (instance, intake, catalogue, output):
        path.mkdir()
    prepared = intake / "Prepared" / "Grouped Result"
    _image(prepared / "Train" / "Train1.png", (1, 2, 3))
    _image(prepared / "Train" / "Ascending" / "Train2.JPG", (4, 5, 6))
    _image(prepared / "Parent" / "Parent1.webp", (7, 8, 9))
    (prepared / "Empty").mkdir()
    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{instance / 'site.db'}"
    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        INTAKE_ROOT=str(intake),
        INTAKE_TEST_MOUNTED=True,
        INTAKE_MUTATION_LOCK_PATH=str(instance / "intake.lock"),
        DISCORD_ENABLED=False,
    )
    with app.app_context():
        db.session.add(User(email="folders@example.test", username="folders-admin", password="unused"))
        db.session.add(
            CatalogueOperation(
                id="0" * 32,
                operation_type="intake_group",
                status="succeeded",
                scope=json.dumps(
                    {
                        "source_relpath": "Prepared/Grouped Result",
                        "workflow_status": "folder_review_required",
                        "operation_summary": {
                            "prepared_relpath": "Prepared/Grouped Result",
                            "workflow_status": "folder_review_required",
                            "warnings": 0,
                        },
                    },
                    separators=(",", ":"),
                ),
            )
        )
        db.session.commit()
    reset_intake_operation_control_for_tests()
    try:
        yield app, intake, catalogue, output
    finally:
        with app.app_context():
            db.session.remove()
        reset_intake_operation_control_for_tests()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


@pytest.fixture
def folder_client(folder_app):
    app, *_ = folder_app
    client = app.test_client()
    with app.app_context():
        user_id = User.query.one().id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def _spec(**changes):
    value = {
        "root_name": "Grouped Result",
        "renames": {
            "Empty": "Empty",
            "Parent": "PaReNt",
            "Train": "Holiday Express Train",
            "Train/Ascending": "Holiday Express Train/Ascending",
        },
        "created": ["New Empty"],
        "remove_empty": ["Empty"],
        "preview_prefix": " UCC ",
    }
    value.update(changes)
    return value


def _wait(app, operation_id, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with app.app_context():
            row = db.session.get(CatalogueOperation, operation_id)
            if row and row.status in {"succeeded", "partial", "failed", "interrupted"}:
                return row.status
        time.sleep(0.02)
    raise AssertionError("folder edit did not finish")


def _confirm(client, preview):
    return client.post(
        "/image-preparation/folders/confirm",
        data={"path": preview["source"], "digest": preview["digest"], "proposal_spec": preview["spec_json"], "acknowledge": "yes"},
    )


def test_folder_editor_routes_require_authentication_and_csrf(folder_app):
    app, intake, *_ = folder_app
    anonymous = app.test_client()
    for route in ("/image-preparation/folders", "/image-preparation/folders/edit?path=Prepared/Grouped%20Result"):
        assert anonymous.get(route).status_code in {302, 401}
    assert anonymous.post("/image-preparation/folders/preview", data={}).status_code in {302, 401}
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    with app.app_context():
        user_id = User.query.one().id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    preview = folder_editor_preview(intake, "Prepared/Grouped Result", _spec())
    assert _confirm(client, preview).status_code == 400


@pytest.mark.parametrize(
    "relative",
    ["", "Prepared", "Grouped Result", "../Prepared/Grouped Result", "Catalogue/Thing", "Output/Thing"],
)
def test_only_direct_prepared_results_are_selectable(folder_app, relative):
    _app, intake, *_ = folder_app
    with pytest.raises(ValueError, match="Prepared|Invalid|Select"):
        folder_editor_preview(intake, relative)


def test_symlink_and_special_entries_are_rejected(folder_app, tmp_path):
    _app, intake, *_ = folder_app
    escape = tmp_path / "escape"
    escape.mkdir()
    (intake / "Prepared" / "Alias").symlink_to(escape, target_is_directory=True)
    with pytest.raises(ValueError, match="Unsafe"):
        folder_editor_preview(intake, "Prepared/Alias")
    source = intake / "Prepared" / "Grouped Result"
    (source / "Train" / "notes.txt").write_text("not an image", encoding="utf-8")
    preview = folder_editor_preview(intake, "Prepared/Grouped Result")
    assert not preview["ready"]
    assert any(issue["code"] == "unsafe_unsupported" for issue in preview["issues"])


def test_complete_current_and_proposed_tree_parent_and_future_preview(folder_app):
    _app, intake, *_ = folder_app
    preview = folder_editor_preview(intake, "Prepared/Grouped Result", _spec())
    assert preview["ready"]
    assert preview["source"] == "Prepared/Grouped Result"
    assert preview["proposed_result"] == "Prepared/Grouped Result"
    assert {item["path"] for item in preview["current_tree"]} == {"Empty", "Parent", "Train", "Train/Ascending"}
    assert {item["path"] for item in preview["proposed_tree"]} == {"PaReNt", "Holiday Express Train", "Holiday Express Train/Ascending", "New Empty"}
    assert preview["parent"] == {"before": "Parent", "after": "PaReNt", "changed": True}
    assert preview["counts"] == {"renamed": 3, "created": 1, "removed_empty": 1, "images": 3, "warnings": 0}
    assert all(row["current_filename"] in {"Train1.png", "Train2.JPG", "Parent1.webp"} for row in preview["mappings"])
    assert all(row["future_filename"].startswith("ucc_") for row in preview["mappings"])
    assert "Single Variable-compatible" in preview["compatibility"]["label"]


@pytest.mark.parametrize("name", [" /bad", "..", ".", "C:drive", "bad\\path", "%2e%2e", "CON"])
def test_unsafe_folder_names_are_blocking(folder_app, name):
    _app, intake, *_ = folder_app
    spec = _spec(renames={**_spec()["renames"], "Train": name})
    preview = folder_editor_preview(intake, "Prepared/Grouped Result", spec)
    assert not preview["ready"]
    assert any(issue["code"] == "unsafe_folder" for issue in preview["issues"])


def test_whitespace_unicode_apostrophes_and_case_are_preserved_visibly(folder_app):
    _app, intake, *_ = folder_app
    spec = _spec(
        renames={**_spec()["renames"], "Train": "  L'Express Été  ", "Train/Ascending": "L'Express Été/Grand Format"},
    )
    preview = folder_editor_preview(intake, "Prepared/Grouped Result", spec)
    assert preview["canonical_spec"]["root_name"] == "Grouped Result"
    assert preview["canonical_spec"]["renames"]["Train"] == "L'Express Été"
    assert {item["normalised"] for item in preview["canonical_spec"]["normalised"]} >= {"L'Express Été"}


@pytest.mark.parametrize(
    "renames,code",
    [
        ({"Train": "Gnome", "Parent": "gnome", "Train/Ascending": "Gnome/Ascending", "Empty": "Empty"}, "folder_collision"),
        ({"Train": "Café", "Parent": "Cafe\u0301", "Train/Ascending": "Café/Ascending", "Empty": "Empty"}, "folder_collision"),
        ({"Train": "Parent", "Parent": "PARENT", "Train/Ascending": "Parent/Ascending", "Empty": "Empty"}, "folder_collision"),
    ],
)
def test_case_unicode_and_parent_collisions_are_blocking(folder_app, renames, code):
    _app, intake, *_ = folder_app
    preview = folder_editor_preview(intake, "Prepared/Grouped Result", _spec(renames=renames))
    assert not preview["ready"]
    assert any(issue["code"] == code for issue in preview["issues"])


def test_nested_parent_is_not_reserved_and_missing_parent_warning_is_cautious(folder_app):
    _app, intake, *_ = folder_app
    renames = {"Train": "Hero", "Train/Ascending": "Hero/Parent", "Parent": "Gallery", "Empty": "Empty"}
    preview = folder_editor_preview(intake, "Prepared/Grouped Result", _spec(renames=renames))
    assert preview["parent"]["after"] is None
    assert any(issue["code"] == "parent_missing" and issue["state"] == "warning" for issue in preview["issues"])
    assert not any(item["path"] == "Hero/Parent" and item["role"] == "Parent product imagery" for item in preview["proposed_tree"])


def test_digest_is_deterministic_and_source_or_proposal_change_invalidates_it(folder_app):
    _app, intake, *_ = folder_app
    first = folder_editor_preview(intake, "Prepared/Grouped Result", _spec())
    second = folder_editor_preview(intake, "Prepared/Grouped Result", _spec())
    assert first["digest"] == second["digest"]
    changed_spec = folder_editor_preview(intake, "Prepared/Grouped Result", _spec(root_name="Different"))
    assert first["digest"] != changed_spec["digest"]
    _image(intake / "Prepared" / "Grouped Result" / "Train" / "Train1.png", (90, 80, 70))
    changed_source = folder_editor_preview(intake, "Prepared/Grouped Result", _spec())
    assert first["digest"] != changed_source["digest"]


def test_non_empty_removal_and_missing_explicit_parent_are_blocking(folder_app):
    _app, intake, *_ = folder_app
    preview = folder_editor_preview(intake, "Prepared/Grouped Result", _spec(remove_empty=["Train"], created=["New/Nested"]))
    assert not preview["ready"]
    assert {issue["code"] for issue in preview["issues"]} >= {"non_empty_remove", "missing_parent"}










def test_lock_blocks_duplicate_folder_operations_and_scanner_lock_is_unchanged(folder_app):
    from app.intake_grouping import IntakeOperationActive, acquire_intake_operation, finish_intake_operation
    from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation

    app, *_ = folder_app
    with app.app_context():
        lease = acquire_intake_operation({"source_relpath": "Prepared/Grouped Result"}, operation_type="intake_folder_edit")
        with pytest.raises(IntakeOperationActive):
            acquire_intake_operation({"source_relpath": "Prepared/Grouped Result"}, operation_type="intake_folder_edit")
        finish_intake_operation(lease, status="failed", summary={"source_images": 0, "copied_images": 0, "failed_images": 1})
        scanner_lease = acquire_catalogue_operation("append", {})
        finish_catalogue_operation(scanner_lease.id, status="failed", error="test")


def test_preview_is_read_only_and_confirmation_requires_matching_digest(folder_app, folder_client):
    _app, intake, catalogue, output = folder_app
    before = _tree(intake)
    preview = folder_editor_preview(intake, "Prepared/Grouped Result", _spec())
    assert _tree(intake) == before
    assert not _tree(catalogue)
    assert not _tree(output)
    response = folder_client.post("/image-preparation/folders/confirm", data={"path": preview["source"], "digest": "0" * 64, "proposal_spec": preview["spec_json"], "acknowledge": "yes"}, follow_redirects=True)
    assert "changed after preview" in response.get_data(as_text=True)
    assert _tree(intake) == before




def test_discord_folder_edit_helpers_use_one_bounded_terminal_channel(monkeypatch):
    from app.utils import discord

    sent = []
    monkeypatch.setattr(discord, "send_discord_message", lambda **kwargs: sent.append(kwargs) or (True, "sent"))
    discord.notify_intake_folder_edit_completed(source_name="Grouped Result", result_name="Final Result", renamed=3, created=1, warnings=0, elapsed_text="1.0s", operation_id="a" * 32)
    assert len(sent) == 1 and sent[0]["channels"] == ["scans_info"]
    sent.clear()
    discord.notify_intake_folder_edit_completed(source_name="Grouped Result", result_name="Final Result", renamed=3, created=1, warnings=2, elapsed_text="1.0s", operation_id="a" * 32)
    assert len(sent) == 1 and sent[0]["channels"] == ["scans_errors"]
    sent.clear()
    discord.notify_intake_folder_edit_failed(source_name="Grouped Result", error_text="safe failure", operation_id="b" * 32)
    assert len(sent) == 1 and sent[0]["channels"] == ["scans_errors"]
    assert "filename" not in json.dumps(sent).lower()




def test_read_only_mount_keeps_preview_and_disables_apply(folder_app, folder_client, monkeypatch):
    import app.routes as routes

    _app, _intake, *_ = folder_app
    monkeypatch.setattr('app.image_preparation.intake_readiness', lambda: {"state": "read_only", "mounted": True, "readable": True, "writable": False, "message": "Catalogue Intake is mounted but read-only", "label": "Mounted but read-only"})
    response = folder_client.get("/image-preparation/folders/edit?path=Prepared/Grouped%20Result")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Mounted but read-only" in html
    assert 'disabled' in html and "Resolve the displayed issues" in html
