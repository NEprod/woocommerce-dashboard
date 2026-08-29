import errno
import hashlib
import json
import os
import re
import time
from pathlib import Path

import pytest
from PIL import Image

from app import create_app, db
from app.image_preparation import grouping_preview
from app.models import CatalogueOperation, User
from app.utils.operation_control import (
    acquire_catalogue_operation,
    finish_catalogue_operation,
    reset_operation_control_for_tests,
)
from config import Config


def _image(path, colour=(42, 124, 88)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (12, 10), colour).save(path)


def _tree(root):
    if not root.exists():
        return []
    rows = []
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        info = path.lstat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        rows.append((path.relative_to(root).as_posix(), info.st_mode, info.st_size, info.st_mtime_ns, digest))
    return rows


@pytest.fixture
def grouping_app(tmp_path):
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
        DISCORD_ENABLED=False,
    )
    with app.app_context():
        db.session.add(User(email="grouping@example.com", username="grouping-admin", password="unused"))
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
def grouping_client(grouping_app):
    app, *_ = grouping_app
    client = app.test_client()
    with app.app_context():
        user_id = User.query.one().id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def _wait_for_operation(app, operation_id, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with app.app_context():
            row = db.session.get(CatalogueOperation, operation_id)
            if row and row.status in {"succeeded", "partial", "failed", "interrupted"}:
                return row.status
        time.sleep(0.02)
    raise AssertionError("intake grouping operation did not finish")


def _confirm(client, source, preview, *, follow=False):
    return client.post(
        "/image-preparation/group/confirm",
        data={"path": source, "digest": preview["digest"], "acknowledge": "yes"},
        follow_redirects=follow,
    )


def test_confirm_requires_authentication_and_csrf(grouping_app):
    app, intake, *_ = grouping_app
    source = intake / "Cards"
    source.mkdir()
    _image(source / "Card1.png")
    preview = grouping_preview(intake, "Cards")
    assert app.test_client().post("/image-preparation/group/confirm", data={}).status_code in {302, 401}

    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    with app.app_context():
        user_id = User.query.one().id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    assert _confirm(client, "Cards", preview).status_code == 400


def test_valid_csrf_confirmation_is_accepted(grouping_app):
    app, intake, *_ = grouping_app
    source = intake / "Cards"
    source.mkdir()
    _image(source / "Card1.png")
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    with app.app_context():
        user_id = User.query.one().id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    page = client.get("/image-preparation/group?path=Cards")
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.get_data(as_text=True)).group(1)
    preview = grouping_preview(intake, "Cards")
    response = client.post(
        "/image-preparation/group/confirm",
        data={"csrf_token": token, "path": "Cards", "digest": preview["digest"], "acknowledge": "yes"},
    )
    assert response.status_code == 302
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    assert _wait_for_operation(app, operation_id) in {"succeeded", "partial"}


def test_valid_confirmation_copies_first_and_records_bounded_result(grouping_app, grouping_client):
    app, intake, catalogue, output = grouping_app
    source = intake / "Christmas Countdown"
    source.mkdir()
    _image(source / "Train1.png", (1, 2, 3))
    _image(source / "Train2.JPG", (4, 5, 6))
    _image(source / "Solo1.webp", (7, 8, 9))
    _image(source / "Parent1.png", (10, 11, 12))
    (source / ".DS_Store").write_text("hidden", encoding="utf-8")
    (source / "notes.txt").write_text("unsupported", encoding="utf-8")
    (source / "broken.png").write_text("corrupt", encoding="utf-8")
    source_before = _tree(source)
    catalogue_before = _tree(catalogue)
    output_before = _tree(output)
    preview = grouping_preview(intake, "Christmas Countdown")

    response = _confirm(grouping_client, "Christmas Countdown", preview)
    assert response.status_code == 302
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    assert _wait_for_operation(app, operation_id) in {"succeeded", "partial"}

    result = intake / "Prepared" / "Christmas Countdown"
    assert (result / "Train" / "Train1.png").read_bytes() == (source / "Train1.png").read_bytes()
    assert (result / "Train" / "Train2.JPG").read_bytes() == (source / "Train2.JPG").read_bytes()
    assert (result / "Solo" / "Solo1.webp").exists()
    assert (result / "Parent" / "Parent1.png").exists()
    assert not (result / ".DS_Store").exists()
    assert not (result / "notes.txt").exists()
    assert not (result / "broken.png").exists()
    assert not list(result.rglob("product_info.json"))
    assert _tree(source) == source_before
    assert _tree(catalogue) == catalogue_before
    assert _tree(output) == output_before
    assert not (intake / ".catalogue-intake-staging" / operation_id).exists()

    with app.app_context():
        row = db.session.get(CatalogueOperation, operation_id)
        scope = json.loads(row.scope)
        summary = scope["operation_summary"]
        assert row.operation_type == "intake_group"
        assert summary["workflow_status"] == "folder_review_required"
        assert summary["copied_images"] == 4
        assert summary["ignored_entries"] == 3
        assert summary["prepared_relpath"] == "Prepared/Christmas Countdown"
        assert len(row.scope.encode("utf-8")) < 256 * 1024
        assert str(intake) not in row.scope
        assert CatalogueOperation.query.count() == 1
        assert not row.items


def test_copy_progress_is_batched_instead_of_committing_per_file(grouping_app, grouping_client, monkeypatch):
    import app.intake_grouping as grouping

    app, intake, *_ = grouping_app
    source = intake / "Batched Progress"
    source.mkdir()
    for number in range(1, 13):
        _image(source / f"Card{number}.png")
    preview = grouping_preview(intake, "Batched Progress")
    persisted_copy_counts = []
    original_persist = grouping.persist_live_state

    def record_persist(operation_id, state, logs=()):
        if state.get("stage") == "copying_grouped_images" and state.get("progress", {}).get("completed", 0):
            persisted_copy_counts.append(state["progress"]["completed"])
        return original_persist(operation_id, state, logs)

    monkeypatch.setattr(grouping, "persist_live_state", record_persist)
    response = _confirm(grouping_client, "Batched Progress", preview)
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    assert _wait_for_operation(app, operation_id) in {"succeeded", "partial"}
    assert persisted_copy_counts == [5, 10, 12]


@pytest.mark.parametrize("change", ["size", "mtime", "new", "removed"])
def test_stale_source_or_digest_is_rejected_without_partial_copy(grouping_app, grouping_client, change):
    _app, intake, *_ = grouping_app
    source = intake / "Cards"
    source.mkdir()
    image = source / "Card1.png"
    _image(image)
    preview = grouping_preview(intake, "Cards")
    before = _tree(source)
    if change == "size":
        image.write_bytes(image.read_bytes() + b"changed")
    elif change == "mtime":
        os.utime(image, ns=(image.stat().st_atime_ns, image.stat().st_mtime_ns + 2_000_000_000))
    elif change == "new":
        _image(source / "Card2.png")
    else:
        image.unlink()
    changed = _tree(source)
    response = _confirm(grouping_client, "Cards", preview, follow=True)
    assert response.status_code == 200
    assert "source folder changed after preview" in response.get_data(as_text=True).lower()
    assert not (intake / "Prepared").exists()
    assert _tree(source) == changed
    assert changed != before


def test_blocking_ambiguity_and_unsafe_entries_prevent_confirmation(grouping_app, grouping_client):
    _app, intake, *_ = grouping_app
    source = intake / "Ambiguous"
    source.mkdir()
    _image(source / "Train1.png")
    _image(source / "Train 2.png")
    preview = grouping_preview(intake, "Ambiguous")
    html = grouping_client.get("/image-preparation/group?path=Ambiguous").get_data(as_text=True)
    assert "data-bs-target=\"#confirmGroupingDialog\"" not in html
    assert "Create grouped copies" not in html
    assert _confirm(grouping_client, "Ambiguous", preview, follow=True).status_code == 200
    assert not (intake / "Prepared").exists()


def test_duplicate_safe_destinations_and_duplicate_submission(grouping_app, grouping_client):
    app, intake, *_ = grouping_app
    source = intake / "Cards"
    source.mkdir()
    _image(source / "Card1.png")
    (intake / "Prepared" / "Cards").mkdir(parents=True)
    (intake / "Prepared" / "cards (2)").mkdir()
    preview = grouping_preview(intake, "Cards")
    first = _confirm(grouping_client, "Cards", preview)
    operation_id = first.headers["Location"].rstrip("/").split("/")[-1]
    assert _wait_for_operation(app, operation_id) in {"succeeded", "partial"}
    assert (intake / "Prepared" / "Cards (3)" / "Card" / "Card1.png").exists()
    second = _confirm(grouping_client, "Cards", preview, follow=True)
    assert "source folder changed after preview" in second.get_data(as_text=True).lower()
    assert not (intake / "Prepared" / "Cards (4)").exists()


def test_dedicated_intake_lock_is_exclusive_and_scanner_lock_is_separate(grouping_app):
    from app.intake_grouping import IntakeOperationActive, acquire_intake_operation, finish_intake_operation

    app, _intake, *_ = grouping_app
    with app.app_context():
        lease = acquire_intake_operation({"source_relpath": "Cards", "proposal_digest": "a" * 64})
        with pytest.raises(IntakeOperationActive):
            acquire_intake_operation({"source_relpath": "Other", "proposal_digest": "b" * 64})
        scanner = acquire_catalogue_operation("append", {"scan_mode": "append"})
        finish_catalogue_operation(scanner.id, status="succeeded")
        finish_intake_operation(lease, status="failed", error="fictional test cleanup")


def test_rapid_duplicate_submission_returns_existing_operation(grouping_app, grouping_client, monkeypatch):
    import threading
    import app.intake_grouping as grouping

    app, intake, *_ = grouping_app
    source = intake / "Slow Cards"
    source.mkdir()
    _image(source / "Card1.png")
    preview = grouping_preview(intake, "Slow Cards")
    entered = threading.Event()
    release = threading.Event()
    original = grouping._copy_source_file

    def slow_copy(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(grouping, "_copy_source_file", slow_copy)
    first = _confirm(grouping_client, "Slow Cards", preview)
    first_id = first.headers["Location"].rstrip("/").split("/")[-1]
    assert entered.wait(3)
    second = _confirm(grouping_client, "Slow Cards", preview)
    assert second.status_code == 302
    assert second.headers["Location"].endswith(first_id)
    release.set()
    assert _wait_for_operation(app, first_id) in {"succeeded", "partial"}
    assert len(list((intake / "Prepared").glob("Slow Cards*"))) == 1


@pytest.mark.parametrize("failure_point", ["copy", "verify"])
def test_failure_exposes_no_final_result_and_cleans_only_owned_staging(grouping_app, grouping_client, monkeypatch, failure_point):
    import app.intake_grouping as grouping

    app, intake, *_ = grouping_app
    source = intake / "Failure Fixture"
    source.mkdir()
    _image(source / "Image1.png")
    unrelated = intake / ".catalogue-intake-staging" / "user-created"
    unrelated.mkdir(parents=True)
    (unrelated / "keep.txt").write_text("keep", encoding="utf-8")
    preview = grouping_preview(intake, "Failure Fixture")
    target = "_copy_source_file" if failure_point == "copy" else "_verify_staged_result"
    monkeypatch.setattr(grouping, target, lambda *args, **kwargs: (_ for _ in ()).throw(OSError(f"injected {failure_point} failure")))
    response = _confirm(grouping_client, "Failure Fixture", preview)
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    assert _wait_for_operation(app, operation_id) == "failed"
    assert not (intake / "Prepared" / "Failure Fixture").exists()
    assert (source / "Image1.png").exists()
    assert (unrelated / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert not (intake / ".catalogue-intake-staging" / operation_id).exists()


def test_cleanup_failure_is_bounded_and_preserves_owned_staging_for_review(grouping_app, grouping_client, monkeypatch):
    import app.intake_grouping as grouping

    app, intake, *_ = grouping_app
    source = intake / "Cleanup Failure"
    source.mkdir()
    _image(source / "Image1.png")
    preview = grouping_preview(intake, "Cleanup Failure")
    monkeypatch.setattr(grouping, "_verify_staged_result", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected verification failure")))
    monkeypatch.setattr(grouping, "_cleanup_operation_staging", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected cleanup failure")))
    response = _confirm(grouping_client, "Cleanup Failure", preview)
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    assert _wait_for_operation(app, operation_id) == "failed"
    assert not (intake / "Prepared" / "Cleanup Failure").exists()
    assert (intake / ".catalogue-intake-staging" / operation_id / ".operation-owner").exists()
    with app.app_context():
        row = db.session.get(CatalogueOperation, operation_id)
        summary = json.loads(row.scope)["operation_summary"]
        assert summary["cleanup_warning"] == "Operation staging cleanup requires review"
        assert str(intake) not in row.scope


def test_hard_link_and_symlink_are_blocking_for_mutation(grouping_app, grouping_client):
    _app, intake, *_ = grouping_app
    source = intake / "Unsafe"
    source.mkdir()
    _image(source / "Image1.png")
    os.link(source / "Image1.png", source / "Image2.png")
    outside = intake.parent / "outside.png"
    _image(outside)
    (source / "escape.png").symlink_to(outside)
    preview = grouping_preview(intake, "Unsafe")
    response = _confirm(grouping_client, "Unsafe", preview, follow=True)
    assert response.status_code == 200
    assert "cannot be confirmed" in response.get_data(as_text=True).lower()
    assert not (intake / "Prepared").exists()


def test_read_only_intake_keeps_preview_and_disables_confirmation(grouping_app, grouping_client, monkeypatch):
    app, intake, *_ = grouping_app
    source = intake / "Cards"
    source.mkdir()
    _image(source / "Card1.png")
    app.config["INTAKE_TEST_MOUNTED"] = True
    monkeypatch.setattr("app.routes.intake_readiness", lambda: {"state": "read_only", "mounted": True, "readable": True, "writable": False, "message": "Catalogue Intake is mounted but read-only", "label": "Mounted but read-only"})
    html = grouping_client.get("/image-preparation/group?path=Cards").get_data(as_text=True)
    assert "Mounted but read-only" in html
    assert "Confirmation unavailable" in html
    assert "Create grouped copies" not in html


def test_private_staging_is_hidden_and_stale_cleanup_is_conservative(grouping_app):
    from datetime import datetime, timedelta
    from app.intake_grouping import cleanup_stale_staging

    app, intake, *_ = grouping_app
    staging = intake / ".catalogue-intake-staging"
    stale_id = "a" * 32
    active_id = "b" * 32
    recent_id = "c" * 32
    for operation_id in (stale_id, active_id, recent_id):
        owned = staging / operation_id
        owned.mkdir(parents=True)
        (owned / ".operation-owner").write_text(operation_id, encoding="ascii")
    user = staging / "user-created"
    user.mkdir()
    (user / "keep.txt").write_text("keep", encoding="utf-8")
    (intake / "Prepared" / "Completed").mkdir(parents=True)
    old = time.time() - 48 * 60 * 60
    os.utime(staging / stale_id, (old, old))
    os.utime(staging / active_id, (old, old))
    with app.app_context():
        db.session.add(CatalogueOperation(id=active_id, operation_type="intake_group", status="running", scope="{}"))
        db.session.commit()
        result = cleanup_stale_staging(intake, now=datetime.now(), protected_ids={active_id})
    assert result["removed"] == 1
    assert not (staging / stale_id).exists()
    assert (staging / active_id).exists()
    assert (staging / recent_id).exists()
    assert (user / "keep.txt").exists()
    assert (intake / "Prepared" / "Completed").exists()
    from app.image_preparation import browse_intake
    browser = browse_intake(intake)
    assert ".catalogue-intake-staging" not in json.dumps(browser)


def test_destination_race_never_overwrites_or_merges(grouping_app, grouping_client, monkeypatch):
    import app.intake_grouping as grouping

    app, intake, *_ = grouping_app
    source = intake / "Race"
    source.mkdir()
    _image(source / "Card1.png")
    preview = grouping_preview(intake, "Race")

    def race(_source, destination):
        destination.mkdir()
        (destination / "external.txt").write_text("external", encoding="utf-8")
        raise FileExistsError(str(destination))

    monkeypatch.setattr(grouping, "_promote_prepared_result", race)
    response = _confirm(grouping_client, "Race", preview)
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    assert _wait_for_operation(app, operation_id) == "failed"
    assert (intake / "Prepared" / "Race" / "external.txt").read_text(encoding="utf-8") == "external"
    assert not (intake / "Prepared" / "Race" / "Card").exists()
    assert (source / "Card1.png").exists()


def test_specialised_no_replace_promotion_is_used_when_supported(tmp_path, monkeypatch):
    import app.intake_grouping as grouping

    source = tmp_path / "staging"
    destination = tmp_path / "Prepared" / "Result"
    source.mkdir()
    destination.parent.mkdir()
    (source / "image.png").write_bytes(b"fictional")
    called = []

    def supported(current, final):
        called.append((current, final))
        os.rename(current, final)

    monkeypatch.setattr(grouping, "_specialised_promote_noreplace", supported)
    result = grouping._promote_prepared_result(source, destination)
    assert result == {"strategy": "specialised_no_replace", "fallback_reason": None}
    assert called == [(source, destination)]
    assert (destination / "image.png").exists()


def test_einval_uses_safe_same_filesystem_fallback(tmp_path, monkeypatch):
    import app.intake_grouping as grouping

    source = tmp_path / "staging"
    destination = tmp_path / "Prepared" / "Result"
    source.mkdir()
    destination.parent.mkdir()
    (source / "image.png").write_bytes(b"fictional")
    original_rename = os.rename
    ordinary_calls = []

    def unsupported(*_args):
        raise OSError(errno.EINVAL, "filesystem does not support RENAME_NOREPLACE")

    def ordinary(current, final):
        ordinary_calls.append((current, final))
        return original_rename(current, final)

    monkeypatch.setattr(grouping, "_specialised_promote_noreplace", unsupported)
    monkeypatch.setattr(grouping.os, "rename", ordinary)
    result = grouping._promote_prepared_result(source, destination)
    assert result == {"strategy": "ordinary_same_filesystem_rename", "fallback_reason": "specialised_unsupported_einval"}
    assert ordinary_calls == [(source, destination)]
    assert (destination / "image.png").exists()


def test_fallback_cannot_overwrite_existing_destination(tmp_path, monkeypatch):
    import app.intake_grouping as grouping

    source = tmp_path / "staging"
    destination = tmp_path / "Prepared" / "Result"
    source.mkdir()
    destination.mkdir(parents=True)
    (source / "new.png").write_bytes(b"new")
    (destination / "existing.png").write_bytes(b"existing")
    monkeypatch.setattr(grouping, "_specialised_promote_noreplace", lambda *_args: (_ for _ in ()).throw(OSError(errno.EINVAL, "unsupported")))
    with pytest.raises(grouping.PromotionRejected, match="destination is no longer available"):
        grouping._promote_prepared_result(source, destination)
    assert (destination / "existing.png").read_bytes() == b"existing"
    assert (source / "new.png").exists()


def test_fallback_destination_race_is_controlled(tmp_path, monkeypatch):
    import app.intake_grouping as grouping

    source = tmp_path / "staging"
    destination = tmp_path / "Prepared" / "Result"
    source.mkdir()
    destination.parent.mkdir()
    (source / "new.png").write_bytes(b"new")
    monkeypatch.setattr(grouping, "_specialised_promote_noreplace", lambda *_args: (_ for _ in ()).throw(OSError(errno.EINVAL, "unsupported")))

    def race(_current, final):
        final.mkdir()
        (final / "external.png").write_bytes(b"external")
        raise FileExistsError(errno.EEXIST, "race")

    monkeypatch.setattr(grouping.os, "rename", race)
    with pytest.raises(grouping.PromotionRejected, match="destination is no longer available"):
        grouping._promote_prepared_result(source, destination)
    assert (destination / "external.png").read_bytes() == b"external"
    assert (source / "new.png").exists()


def test_cross_filesystem_promotion_fails_before_rename(tmp_path, monkeypatch):
    import app.intake_grouping as grouping

    source = tmp_path / "staging"
    destination = tmp_path / "Prepared" / "Result"
    source.mkdir()
    destination.parent.mkdir()
    monkeypatch.setattr(grouping, "_filesystem_device", lambda path: 1 if path == source else 2)
    monkeypatch.setattr(grouping, "_specialised_promote_noreplace", lambda *_args: pytest.fail("rename attempted"))
    with pytest.raises(grouping.PromotionRejected, match="same mounted filesystem"):
        grouping._promote_prepared_result(source, destination)


@pytest.mark.parametrize(
    ("error_number", "message"),
    [
        (errno.EROFS, "read-only"),
        (errno.EACCES, "permissions"),
        (errno.EINVAL, "not valid"),
    ],
)
def test_fallback_filesystem_errors_are_controlled(tmp_path, monkeypatch, error_number, message):
    import app.intake_grouping as grouping

    source = tmp_path / "staging"
    destination = tmp_path / "Prepared" / "Result"
    source.mkdir()
    destination.parent.mkdir()
    monkeypatch.setattr(grouping, "_specialised_promote_noreplace", lambda *_args: (_ for _ in ()).throw(OSError(errno.EINVAL, "unsupported")))
    monkeypatch.setattr(grouping.os, "rename", lambda *_args: (_ for _ in ()).throw(OSError(error_number, "raw fixture detail")))
    with pytest.raises(grouping.PromotionRejected, match=message):
        grouping._promote_prepared_result(source, destination)


def test_einval_fallback_flushes_full_progress_before_verification(grouping_app, grouping_client, monkeypatch):
    import app.intake_grouping as grouping

    app, intake, *_ = grouping_app
    source = intake / "Unraid Fixture"
    source.mkdir()
    for number in range(1, 27):
        _image(source / f"Card{number}.png")
    preview = grouping_preview(intake, "Unraid Fixture")
    monkeypatch.setattr(grouping, "_specialised_promote_noreplace", lambda *_args: (_ for _ in ()).throw(OSError(errno.EINVAL, "unsupported")))
    response = _confirm(grouping_client, "Unraid Fixture", preview)
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    assert _wait_for_operation(app, operation_id) in {"succeeded", "partial"}
    result = intake / "Prepared" / "Unraid Fixture"
    assert len(list(result.rglob("*.png"))) == 26
    with app.app_context():
        row = db.session.get(CatalogueOperation, operation_id)
        scope = json.loads(row.scope)
        summary = scope["operation_summary"]
        logs = scope["live_state"]["logs"]
        assert summary["copied_images"] == 26
        assert summary["promotion_strategy"] == "ordinary_same_filesystem_rename"
        assert any(entry["line"] == "Images copied: 26 / 26" for entry in logs)
        copy_line = next(index for index, entry in enumerate(logs) if entry["line"] == "Images copied: 26 / 26")
        verify_line = next(index for index, entry in enumerate(logs) if "Verifying" in entry["line"])
        assert copy_line < verify_line


def test_failed_promotion_records_safe_stage_cleanup_and_retry_succeeds(grouping_app, grouping_client, monkeypatch):
    import app.intake_grouping as grouping

    app, intake, *_ = grouping_app
    source = intake / "Retry Fixture"
    source.mkdir()
    _image(source / "Card1.png")
    source_before = _tree(source)
    preview = grouping_preview(intake, "Retry Fixture")
    original_specialised = grouping._specialised_promote_noreplace
    monkeypatch.setattr(grouping, "_specialised_promote_noreplace", lambda *_args: (_ for _ in ()).throw(OSError(errno.EINVAL, "unsupported")))
    monkeypatch.setattr(grouping.os, "rename", lambda *_args: (_ for _ in ()).throw(OSError(errno.EACCES, "host detail")))
    first = _confirm(grouping_client, "Retry Fixture", preview)
    first_id = first.headers["Location"].rstrip("/").split("/")[-1]
    assert _wait_for_operation(app, first_id) == "failed"
    assert not (intake / "Prepared" / "Retry Fixture").exists()
    assert not (intake / ".catalogue-intake-staging" / first_id).exists()
    assert _tree(source) == source_before
    with app.app_context():
        failed = db.session.get(CatalogueOperation, first_id)
        scope = json.loads(failed.scope)
        assert scope["operation_summary"]["failed_stage"] == "promoting_prepared_result"
        assert scope["operation_summary"]["staging_cleanup"] == "cleaned"
        assert str(intake) not in failed.scope
        assert "host detail" not in failed.scope
    detail = grouping_client.get(f"/operations/{first_id}").get_data(as_text=True)
    assert "Catalogue Intake permissions prevented prepared-result promotion" in detail
    assert "Failed stage" in detail
    assert "Promoting Prepared Result" in detail
    assert "Private staging" in detail
    assert "Cleaned" in detail
    assert "Review fresh grouping proposal" in detail
    assert "host detail" not in detail

    monkeypatch.setattr(grouping, "_specialised_promote_noreplace", original_specialised)
    monkeypatch.undo()
    retry_preview = grouping_preview(intake, "Retry Fixture")
    second = _confirm(grouping_client, "Retry Fixture", retry_preview)
    second_id = second.headers["Location"].rstrip("/").split("/")[-1]
    assert _wait_for_operation(app, second_id) in {"succeeded", "partial"}
    assert (intake / "Prepared" / "Retry Fixture" / "Card" / "Card1.png").exists()


def test_discord_exception_does_not_fail_completed_grouping(grouping_app, grouping_client, monkeypatch):
    from app.utils import discord

    app, intake, *_ = grouping_app
    source = intake / "Cards"
    source.mkdir()
    _image(source / "Card1.png")
    preview = grouping_preview(intake, "Cards")
    monkeypatch.setattr(discord, "notify_intake_grouping_completed", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("fictional delivery failure")))
    response = _confirm(grouping_client, "Cards", preview)
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    assert _wait_for_operation(app, operation_id) in {"succeeded", "partial"}
    assert (intake / "Prepared" / "Cards" / "Card" / "Card1.png").exists()
    with app.app_context():
        row = db.session.get(CatalogueOperation, operation_id)
        live = json.loads(row.scope)["live_state"]
        assert live["discord"]["state"] == "failed"
        assert live["stage"] == "completed_folder_review_required"
        assert len(live["logs"]) < 50


def test_operation_detail_and_prepared_browser_use_provisional_language(grouping_app, grouping_client):
    app, intake, *_ = grouping_app
    source = intake / "Cards"
    source.mkdir()
    _image(source / "Card1.png")
    preview = grouping_preview(intake, "Cards")
    response = _confirm(grouping_client, "Cards", preview)
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    assert _wait_for_operation(app, operation_id) in {"succeeded", "partial"}
    html = grouping_client.get(f"/operations/{operation_id}").get_data(as_text=True)
    assert "Grouping complete — folder review required" in html
    assert "Review and Rename Folders" in html
    assert "Open Prepared Result" in html
    assert "Ready for Catalogue" not in html
    assert "Rename Images" not in html
    assert "Create Metadata" not in html
    assert "Copy to Catalogue" not in html
    prepared = grouping_client.get("/image-preparation?path=Prepared/Cards")
    assert prepared.status_code == 200
    assert "Prepared/Cards" in prepared.get_data(as_text=True)


def test_confirmation_dialog_has_accessible_acknowledgement(grouping_app, grouping_client):
    _app, intake, *_ = grouping_app
    source = intake / "Cards"
    source.mkdir()
    _image(source / "Card1.png")
    html = grouping_client.get("/image-preparation/group?path=Cards").get_data(as_text=True)
    assert 'id="confirmGroupingDialog"' in html
    assert 'aria-labelledby="confirmGroupingDialogTitle"' in html
    assert 'id="confirmGroupingDialogTitle"' in html
    assert 'id="confirmGroupingAcknowledge"' in html
    assert 'for="confirmGroupingAcknowledge"' in html
    assert "my source files will remain unchanged" in html
    assert "folder names are provisional" in html


def test_discord_grouping_helpers_are_bounded_and_failure_is_nonfatal(monkeypatch):
    from app.utils import discord

    sent = []
    monkeypatch.setattr(discord, "send_discord_message", lambda **kwargs: sent.append(kwargs) or (False, "delivery failed"))
    result = discord.notify_intake_grouping_completed(
        source_name="Fictional Cards",
        result_name="Fictional Cards (2)",
        groups=3,
        copied_images=12,
        warnings=1,
        elapsed_text="2s",
        operation_id="a" * 32,
    )
    assert result == (False, "delivery failed")
    assert sent[0]["channels"] == ["scans_errors"]
    payload = json.dumps(sent[0])
    assert "/intake" not in payload
    assert ".png" not in payload
    sent.clear()
    discord.notify_intake_grouping_completed(
        source_name="Fictional Cards",
        result_name="Fictional Cards",
        groups=3,
        copied_images=12,
        warnings=0,
        elapsed_text="2s",
        operation_id="c" * 32,
    )
    assert sent[0]["channels"] == ["scans_info"]
    sent.clear()
    discord.notify_intake_grouping_failed(source_name="Fictional Cards", error_text="safe failure", operation_id="b" * 32)
    assert sent[0]["channels"] == ["scans_errors"]
    assert "/intake" not in json.dumps(sent[0])
    assert "/Users/" not in json.dumps(sent[0])


def test_rename_preview_is_read_only_and_invalid_confirmation_does_not_mutate(grouping_app, grouping_client):
    _app, intake, *_ = grouping_app
    source = intake / "Prepared Fixture"
    _image(source / "Group" / "Image.png")
    before = _tree(intake)
    assert grouping_client.get("/image-preparation/rename?path=Prepared%20Fixture&prefix=Test").status_code == 200
    assert grouping_client.post("/image-preparation/rename/confirm", data={}).status_code == 302
    assert _tree(intake) == before
