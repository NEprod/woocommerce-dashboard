import hashlib
import json
import os
import re
import time
from pathlib import Path

import pytest
from PIL import Image

from app import create_app, db
from app.intake_structured_import import (
    IMPORT_FINAL,
    IMPORT_REVIEW,
    StructuredImportRejected,
    structured_import_preview,
)
from app.models import CatalogueOperation, User
from app.utils.operation_control import reset_operation_control_for_tests
from config import Config


def _image(path, colour=(42, 124, 88)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (9, 7), colour).save(path)


def _tree(root):
    if not root.exists():
        return []
    rows = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        info = path.lstat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        rows.append((path.relative_to(root).as_posix(), info.st_mode, info.st_size, info.st_mtime_ns, digest))
    return rows


def _fixture(intake, name="Hero Cards", *, metadata=True):
    source = intake / name
    _image(source / "Parent" / "01 Hero.PNG", (1, 2, 3))
    _image(source / "Hero A" / "image-01.jpg", (4, 5, 6))
    _image(source / "Hero B" / "Large" / "image-02.JPEG", (7, 8, 9))
    if metadata:
        (source / "product_info.json").write_text(
            json.dumps({"title": "Fictional Hero Cards", "collection_type": "Single Variable"}),
            encoding="utf-8",
        )
    return source


@pytest.fixture
def structured_app(tmp_path):
    from app.intake_grouping import reset_intake_operation_control_for_tests

    instance = tmp_path / "instance"
    intake = tmp_path / "intake"
    catalogue = tmp_path / "catalogue"
    output = tmp_path / "output"
    for path in (instance, intake, catalogue, output):
        path.mkdir()
    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{instance / 'site.db'}"
    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        INTAKE_ROOT=str(intake),
        INTAKE_TEST_MOUNTED=True,
        INTAKE_MUTATION_LOCK_PATH=str(instance / "intake.lock"),
        CATALOGUE_ROOT=str(catalogue),
        OUTPUT_FOLDER=str(output),
        DISCORD_ENABLED=False,
    )
    with app.app_context():
        db.session.add(User(email="structured@example.com", username="structured-admin", password="unused"))
        db.session.commit()
    reset_operation_control_for_tests()
    reset_intake_operation_control_for_tests()
    try:
        yield app, intake, catalogue, output
    finally:
        with app.app_context():
            db.session.remove()
        reset_operation_control_for_tests()
        reset_intake_operation_control_for_tests()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


@pytest.fixture
def structured_client(structured_app):
    app, *_ = structured_app
    client = app.test_client()
    with app.app_context():
        user_id = User.query.one().id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def _wait(app, operation_id, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with app.app_context():
            row = db.session.get(CatalogueOperation, operation_id)
            if row and row.status in {"succeeded", "partial", "failed", "interrupted"}:
                return row.status
        time.sleep(0.02)
    raise AssertionError("structured import did not finish")


def _confirm(client, source, preview, *, final_ack=True):
    data = {
        "path": source,
        "mode": preview["mode"],
        "digest": preview["digest"],
        "acknowledge": "yes",
    }
    if final_ack:
        data["acknowledge_final"] = "yes"
    return client.post("/image-preparation/import-structured/confirm", data=data)


def test_routes_require_authentication_and_confirmation_requires_csrf(structured_app):
    app, intake, *_ = structured_app
    _fixture(intake)
    anonymous = app.test_client()
    assert anonymous.get("/image-preparation/import-structured").status_code in {302, 401}
    assert anonymous.post("/image-preparation/import-structured/preview").status_code in {302, 401}
    assert anonymous.post("/image-preparation/import-structured/confirm").status_code in {302, 401}

    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    with app.app_context():
        user_id = User.query.one().id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    preview = structured_import_preview(intake, "Hero Cards")
    assert _confirm(client, "Hero Cards", preview).status_code == 400


def test_preview_is_complete_deterministic_and_read_only(structured_app, structured_client):
    _app, intake, catalogue, output = structured_app
    source = _fixture(intake)
    (source / ".DS_Store").write_text("hidden", encoding="utf-8")
    (source / "notes.txt").write_text("unsupported", encoding="utf-8")
    (source / "broken.webp").write_text("corrupt", encoding="utf-8")
    before = _tree(source)
    preview = structured_import_preview(intake, "Hero Cards", IMPORT_REVIEW)
    repeated = structured_import_preview(intake, "Hero Cards", IMPORT_REVIEW)

    assert preview["digest"] == repeated["digest"]
    assert preview["counts"] == {"folders": 4, "images": 3, "metadata": 1, "hidden": 1, "unsupported": 1, "corrupt": 1, "unsafe": 0, "unreadable": 0, "excluded": 3}
    assert preview["parent"] == {"detected": True, "paths": ["Parent"]}
    assert preview["maximum_depth"] == 2
    assert {item["path"] for item in preview["files"]} == {
        "Hero A/image-01.jpg", "Hero B/Large/image-02.JPEG", "Parent/01 Hero.PNG", "product_info.json"
    }
    assert {item["kind"] for item in preview["excluded"]} == {"hidden", "unsupported", "corrupt"}
    assert preview["workflow_status"] == "folder_review_required"
    body = structured_client.post(
        "/image-preparation/import-structured/preview",
        data={"path": "Hero Cards", "mode": "review"},
    ).get_data(as_text=True)
    assert "Preview only" in body and "The complete source directory remains unchanged" in body
    assert "Parent/" in body and "image-02.JPEG" in body and preview["digest"] in body
    assert _tree(source) == before
    assert _tree(catalogue) == [] and _tree(output) == []


@pytest.mark.parametrize("relative", ["", "Prepared/Result", ".catalogue-intake-staging/x", ".catalogue-intake-rollback/x", "../catalogue", "%2e%2e/output", "/absolute"])
def test_private_absolute_and_traversal_sources_are_rejected(tmp_path, relative):
    intake = tmp_path / "intake"
    intake.mkdir()
    for private in ("Prepared/Result", ".catalogue-intake-staging/x", ".catalogue-intake-rollback/x"):
        (intake / private).mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError):
        structured_import_preview(intake, relative)


def test_symlink_hardlink_and_special_entries_block_import(structured_app):
    _app, intake, *_ = structured_app
    source = _fixture(intake)
    outside = intake.parent / "outside"
    outside.mkdir()
    (source / "escape").symlink_to(outside, target_is_directory=True)
    os.link(source / "Hero A" / "image-01.jpg", source / "Hero A" / "hard.jpg")
    if hasattr(os, "mkfifo"):
        os.mkfifo(source / "pipe")
    preview = structured_import_preview(intake, "Hero Cards")
    assert not preview["ready"]
    assert {item["category"] for item in preview["blockers"]} >= {"unsafe"}
    assert all(str(intake) not in item["message"] for item in preview["issues"])

    alias = intake / "Alias"
    alias.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError):
        structured_import_preview(intake, "Alias")


def test_parent_casing_metadata_and_review_warnings_are_preserved(structured_app):
    _app, intake, *_ = structured_app
    source = _fixture(intake, "Unicode Café")
    (source / "product_info.json").write_bytes(b"{malformed but preserved")
    _image(source / "root warning.png")
    preview = structured_import_preview(intake, "Unicode Café", IMPORT_REVIEW)
    assert preview["ready"]
    assert preview["parent"]["paths"] == ["Parent"]
    assert any(item["code"] == "malformed_metadata" for item in preview["issues"])
    assert any(item["code"] == "root_images" and item["state"] == "warning" for item in preview["issues"])
    assert next(item for item in preview["files"] if item["kind"] == "metadata")["valid_json"] is False


def test_final_mode_applies_strict_hierarchy_and_collision_validation(structured_app):
    _app, intake, *_ = structured_app
    source = _fixture(intake)
    _image(source / "root.png")
    review = structured_import_preview(intake, "Hero Cards", IMPORT_REVIEW)
    final = structured_import_preview(intake, "Hero Cards", IMPORT_FINAL)
    assert review["ready"] and not final["ready"]
    assert review["digest"] != final["digest"]
    assert final["workflow_status"] == "image_renaming_required"
    assert any(item["code"] == "root_images" for item in final["blockers"])

    from app.intake_structured_import import _directory_findings

    collision_issues, parents = _directory_findings(
        [
            {"path": "Parent", "name": "Parent", "depth": 1},
            {"path": "parent", "name": "parent", "depth": 1},
            {"path": "Café", "name": "Café", "depth": 1},
            {"path": "Cafe\u0301", "name": "Cafe\u0301", "depth": 1},
        ],
        IMPORT_REVIEW,
    )
    assert len(parents) == 2
    assert {item["code"] for item in collision_issues} >= {"folder_collision", "duplicate_parent"}

    _image(source / "Hero C" / "Large" / "Nested" / "too-deep.png")
    assert any(
        item["code"] == "unsupported_depth"
        for item in structured_import_preview(intake, "Hero Cards", IMPORT_FINAL)["blockers"]
    )


@pytest.mark.parametrize(
    ("mode", "expected_state", "expected_action"),
    [(IMPORT_REVIEW, "folder_review_required", "Review and Rename Folders"), (IMPORT_FINAL, "image_renaming_required", "Rename Images")],
)
def test_copy_first_import_preserves_source_and_creates_actionable_lineage(
    structured_app, structured_client, mode, expected_state, expected_action
):
    app, intake, catalogue, output = structured_app
    source = _fixture(intake, f"Hero {mode}")
    (source / ".hidden").write_text("excluded", encoding="utf-8")
    before = _tree(source)
    metadata_bytes = (source / "product_info.json").read_bytes()
    preview = structured_import_preview(intake, f"Hero {mode}", mode)
    response = _confirm(structured_client, f"Hero {mode}", preview)
    assert response.status_code == 302
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    assert _wait(app, operation_id) in {"succeeded", "partial"}

    result = intake / preview["proposed_result"]
    assert _tree(source) == before
    assert (result / "product_info.json").read_bytes() == metadata_bytes
    assert (result / "Parent" / "01 Hero.PNG").read_bytes() == (source / "Parent" / "01 Hero.PNG").read_bytes()
    assert not (result / ".hidden").exists()
    assert not list(result.rglob(".scanned")) and not list(result.rglob(".update"))
    assert _tree(catalogue) == [] and _tree(output) == []
    assert not (intake / ".catalogue-intake-staging" / operation_id).exists()

    with app.app_context():
        row = db.session.get(CatalogueOperation, operation_id)
        scope = json.loads(row.scope)
        summary = scope["operation_summary"]
        assert row.operation_type == "intake_structured_import"
        assert summary["workflow_status"] == expected_state
        assert summary["import_mode"] == mode and summary["source_preserved"] is True
        assert len(row.scope.encode("utf-8")) < 256 * 1024
        assert str(intake) not in row.scope
    detail = structured_client.get(response.headers["Location"]).get_data(as_text=True)
    assert "Catalogue Intake — Import Structured Folder" in detail
    assert expected_action in detail
    assert "Imported structured source" in detail


def test_existing_result_is_suffixed_without_overwrite_or_merge(structured_app, structured_client):
    app, intake, *_ = structured_app
    _fixture(intake)
    existing = intake / "Prepared" / "Hero Cards"
    existing.mkdir(parents=True)
    (existing / "owner.txt").write_text("unrelated", encoding="utf-8")
    preview = structured_import_preview(intake, "Hero Cards")
    assert preview["proposed_result"] == "Prepared/Hero Cards (2)"
    response = _confirm(structured_client, "Hero Cards", preview)
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    assert _wait(app, operation_id) in {"succeeded", "partial"}
    assert (existing / "owner.txt").read_text(encoding="utf-8") == "unrelated"
    assert (intake / "Prepared" / "Hero Cards (2)" / "Parent" / "01 Hero.PNG").exists()


@pytest.mark.parametrize("change", ["file", "added", "removed", "destination"])
def test_stale_proposal_is_rejected_without_partial_result(structured_app, structured_client, change):
    _app, intake, *_ = structured_app
    source = _fixture(intake)
    preview = structured_import_preview(intake, "Hero Cards")
    if change == "file":
        _image(source / "Hero A" / "image-01.jpg", (90, 80, 70))
    elif change == "added":
        _image(source / "Hero C" / "new.png")
    elif change == "removed":
        (source / "Hero B" / "Large" / "image-02.JPEG").unlink()
    else:
        (intake / preview["proposed_result"]).mkdir(parents=True)
    response = _confirm(structured_client, "Hero Cards", preview)
    assert response.status_code == 302
    assert "/image-preparation/import-structured" in response.headers["Location"]
    assert not (intake / preview["proposed_result"]).exists() or change == "destination"
    assert not list((intake / ".catalogue-intake-staging").glob("*")) if (intake / ".catalogue-intake-staging").exists() else True


def test_copy_failure_cleans_only_owned_staging_and_exposes_no_result(structured_app, structured_client, monkeypatch):
    import app.intake_structured_import as structured

    app, intake, *_ = structured_app
    _fixture(intake)
    unrelated = intake / ".catalogue-intake-staging" / ("f" * 32)
    unrelated.mkdir(parents=True)
    (unrelated / ".operation-owner").write_text("f" * 32, encoding="utf-8")
    preview = structured_import_preview(intake, "Hero Cards")
    monkeypatch.setattr(structured, "_copy_file", lambda *_args, **_kwargs: (_ for _ in ()).throw(StructuredImportRejected("fictional copy failure")))
    response = _confirm(structured_client, "Hero Cards", preview)
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    assert _wait(app, operation_id) == "failed"
    assert not (intake / preview["proposed_result"]).exists()
    assert unrelated.exists()
    assert not (intake / ".catalogue-intake-staging" / operation_id).exists()


def test_overview_import_page_and_responsive_semantics(structured_app, structured_client):
    _app, intake, *_ = structured_app
    _fixture(intake)
    overview = structured_client.get("/image-preparation").get_data(as_text=True)
    assert "Start with loose images" not in overview  # copy is concise, actions remain explicit
    assert "Group loose images" in overview and "Import structured folder" in overview
    page = structured_client.get("/image-preparation/import-structured?path=Hero%20Cards").get_data(as_text=True)
    assert page.count("<h1") == 1
    assert page.count("<main") == 1
    assert 'name="mode" value="review"' in page and 'name="mode" value="final"' in page
    assert "intake-import-mode-form" in page and "intake-choice-card" in page
    assert 'for="structuredImportAcknowledge"' not in page  # confirmation appears only after preview
    css = (Path(__file__).resolve().parents[1] / "app/static/assets/css/custom.css").read_text(encoding="utf-8")
    assert "@media (max-width: 720px)" in css
    assert ".intake-import-mode-form fieldset { grid-template-columns: 1fr; }" in css


def test_bounded_500_image_preview_and_no_scanner_invocation(structured_app, monkeypatch):
    _app, intake, *_ = structured_app
    source = intake / "Bounded Fixture"
    for folder_number in range(20):
        for image_number in range(25):
            _image(source / f"Product {folder_number:02d}" / f"image-{image_number:02d}.png")
    _image(source / "Parent" / "parent.png")
    monkeypatch.setattr("app.utils.scan_runner.start_scan", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("scanner must not run")))
    started = time.monotonic()
    preview = structured_import_preview(intake, "Bounded Fixture", IMPORT_FINAL)
    assert preview["counts"]["images"] == 501
    assert preview["counts"]["folders"] == 21
    assert preview["ready"]
    assert time.monotonic() - started < 15


def test_discord_summary_is_bounded_and_routes_by_terminal_state(monkeypatch):
    from app.utils import discord

    calls = []
    monkeypatch.setattr(discord, "send_discord_message", lambda **kwargs: calls.append(kwargs) or (True, None))
    summary = {
        "source_relpath": "Fictional/Structured Source",
        "result_name": "Structured Source",
        "import_mode": "review",
        "folder_count": 12,
        "source_images": 24,
        "parent_detected": True,
        "warnings": 0,
        "duration_seconds": 1.2,
    }
    discord.notify_intake_structured_import_completed(summary, operation_id="a" * 32)
    assert calls[-1]["channels"] == ["scans_info"]
    summary["warnings"] = 2
    discord.notify_intake_structured_import_completed(summary, operation_id="b" * 32)
    assert calls[-1]["channels"] == ["scans_errors"]
    discord.notify_intake_structured_import_failed("Structured Source", "safe failure", operation_id="c" * 32)
    assert calls[-1]["channels"] == ["scans_errors"]
    assert all(len(json.dumps(call)) < 6000 for call in calls)
