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
    assert "changed after preview" in response.get_data(as_text=True).lower()
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
    assert any(message in response.get_data(as_text=True).lower() for message in ('hard-linked', 'symbolic link'))
    assert (source / 'Image1.png').exists() and (source / 'Image2.png').exists()
    assert (source / 'escape.png').is_symlink()
    assert not (intake / "Prepared").exists()


def test_read_only_intake_keeps_preview_and_disables_confirmation(grouping_app, grouping_client, monkeypatch):
    app, intake, *_ = grouping_app
    source = intake / "Cards"
    source.mkdir()
    _image(source / "Card1.png")
    app.config["INTAKE_TEST_MOUNTED"] = True
    monkeypatch.setattr("app.image_preparation.intake_readiness", lambda: {"state": "read_only", "mounted": True, "readable": True, "writable": False, "message": "Catalogue Intake is mounted but read-only", "label": "Mounted but read-only"})
    html = grouping_client.get("/image-preparation/group?path=Cards").get_data(as_text=True)
    assert "Mounted but read-only" in html
    assert 'disabled' in html and "Resolve the displayed issues" in html
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
