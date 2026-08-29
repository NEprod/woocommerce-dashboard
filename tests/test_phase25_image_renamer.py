import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from app import create_app, db
from app.intake_folder_editor import _snapshot_identity, _snapshot_prepared_result
from app.intake_image_renamer import (
    eligible_image_rename_results,
    image_rename_preview,
)
from app.models import CatalogueOperation, User
from config import Config


def _image(path, colour=(41, 121, 151)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (12, 9), colour).save(path)


def _tree(root):
    rows = []
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        rows.append(
            (
                path.relative_to(root).as_posix(),
                path.is_dir(),
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
            )
        )
    return rows


def _summary_scope(summary):
    return json.dumps(
        {
            "source_relpath": summary["source_relpath"],
            "workflow_status": "image_renaming_required",
            "operation_summary": summary,
        },
        separators=(",", ":"),
    )


@pytest.fixture
def rename_app(tmp_path):
    from app.intake_grouping import reset_intake_operation_control_for_tests

    instance = tmp_path / "instance"
    intake = tmp_path / "intake"
    catalogue = tmp_path / "catalogue"
    output = tmp_path / "output"
    for folder in (instance, intake, catalogue, output):
        folder.mkdir()
    loose = intake / "Loose Original"
    _image(loose / "original.png", (1, 2, 3))
    working = intake / "Prepared" / "Working Result"
    _image(working / "Parent" / "parent-main.PNG", (3, 4, 5))
    _image(working / "Holiday Train" / "train-b.JPG", (6, 7, 8))
    _image(working / "Holiday Train" / "train-a.png", (9, 10, 11))
    _image(working / "Holiday Train" / "Ascending" / "variation.webp", (12, 13, 14))
    (working / "Empty").mkdir()

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
        db.session.add(User(email="rename@example.test", username="rename-admin", password="unused"))
        identity = _snapshot_identity(
            _snapshot_prepared_result(intake, "Prepared/Working Result")
        )
        summary = {
            "source_relpath": "Prepared/Working Result",
            "prepared_relpath": "Prepared/Working Result",
            "workflow_status": "image_renaming_required",
            "source_identity": identity,
            "result_identity": identity,
            "source_images": 4,
            "copied_images": 4,
            "failed_images": 0,
        }
        db.session.add(
            CatalogueOperation(
                id="a" * 32,
                operation_type="intake_folder_edit",
                status="succeeded",
                scope=_summary_scope(summary),
                started_at=datetime(2026, 1, 1),
                finished_at=datetime(2026, 1, 1, 0, 1),
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
def rename_client(rename_app):
    app, *_ = rename_app
    client = app.test_client()
    with app.app_context():
        user_id = User.query.one().id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def _wait(app, operation_id, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with app.app_context():
            row = db.session.get(CatalogueOperation, operation_id)
            if row and row.status in {"succeeded", "partial", "failed", "interrupted"}:
                return row.status
        time.sleep(0.02)
    raise AssertionError("image rename operation did not finish")


def _confirm(client, preview, **extra):
    data = {
        "path": preview["source"],
        "prefix": preview["normalised_prefix"],
        "digest": preview["digest"],
        "acknowledge": "yes",
    }
    data.update(extra)
    return client.post("/image-preparation/rename/confirm", data=data)


def test_preview_uses_same_visible_result_final_hierarchy_and_deterministic_names(rename_app):
    app, intake, *_ = rename_app
    with app.app_context():
        first = image_rename_preview(intake, "Prepared/Working Result", " U CCC ")
        second = image_rename_preview(intake, "Prepared/Working Result", " U CCC ")
    assert first["ready"]
    assert first["digest"] == second["digest"]
    assert first["source"] == first["proposed_result"] == "Prepared/Working Result"
    assert first["normalised_prefix"] == "u_ccc"
    names = {row["recommended_filename"] for row in first["mappings"]}
    assert names == {
        "u_ccc_working_result_01.png",
        "u_ccc_holiday_train_01.png",
        "u_ccc_holiday_train_02.jpg",
        "u_ccc_holiday_train_ascending_01.webp",
    }
    assert first["counts"] == {
        "images": 4,
        "parent": 1,
        "variation": 1,
        "other": 2,
        "warnings": 3,
        "collisions": 0,
    }


@pytest.mark.parametrize(
    "prefix",
    ["", "   ", "bad/name", "bad\\name", "C:drive", ".", "..", "CON", "%2fescape", "bad\x00name", "bad\nname"],
)
def test_prefix_rejects_unsafe_values(rename_app, prefix):
    app, intake, *_ = rename_app
    with app.app_context(), pytest.raises(ValueError):
        image_rename_preview(intake, "Prepared/Working Result", prefix)


def test_only_folder_confirmed_direct_prepared_results_are_eligible(rename_app):
    app, intake, *_ = rename_app
    with app.app_context():
        with pytest.raises(ValueError, match="directly beneath Prepared|Folder structure"):
            image_rename_preview(intake, "Loose Original", "SKU")
        (intake / "Prepared" / "Grouping Only").mkdir()
        _image(intake / "Prepared" / "Grouping Only" / "Product" / "image.png")
        with pytest.raises(ValueError, match="Folder structure confirmed"):
            image_rename_preview(intake, "Prepared/Grouping Only", "SKU")
        for value in ("", "Prepared", "../Prepared/Working Result", ".catalogue-intake-staging/x"):
            with pytest.raises(ValueError):
                image_rename_preview(intake, value, "SKU")


def test_routes_require_authentication_csrf_and_acknowledgement(rename_app, rename_client):
    app, intake, *_ = rename_app
    anonymous = app.test_client()
    assert anonymous.get("/image-preparation/rename?path=Prepared/Working%20Result").status_code in {302, 401}
    assert anonymous.post("/image-preparation/rename/preview").status_code in {302, 401}
    app.config["WTF_CSRF_ENABLED"] = True
    assert rename_client.post("/image-preparation/rename/preview", data={"path": "Prepared/Working Result", "prefix": "SKU"}).status_code == 400
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        preview = image_rename_preview(intake, "Prepared/Working Result", "SKU")
    response = rename_client.post(
        "/image-preparation/rename/confirm",
        data={"path": preview["source"], "prefix": "sku", "digest": preview["digest"]},
    )
    assert response.status_code == 302
    with app.app_context():
        assert not CatalogueOperation.query.filter_by(operation_type="intake_image_rename").first()


def test_safe_rename_replaces_same_result_preserves_bytes_and_boundaries(rename_app, rename_client):
    app, intake, catalogue, output = rename_app
    working = intake / "Prepared" / "Working Result"
    loose_before = _tree(intake / "Loose Original")
    catalogue_before = _tree(catalogue)
    output_before = _tree(output)
    source_hashes = sorted(row[2] for row in _tree(working) if row[2])
    with app.app_context():
        preview = image_rename_preview(intake, "Prepared/Working Result", "UCCC")
    response = _confirm(rename_client, preview)
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    status = _wait(app, operation_id)
    with app.app_context():
        operation_error = db.session.get(CatalogueOperation, operation_id).error
    assert status in {"succeeded", "partial"}, operation_error
    assert working.is_dir()
    assert not (intake / "Prepared" / "Working Result (2)").exists()
    assert sorted(row[2] for row in _tree(working) if row[2]) == source_hashes
    assert (working / "Parent" / "uccc_working_result_01.png").exists()
    assert (working / "Holiday Train" / "uccc_holiday_train_01.png").exists()
    assert (working / "Holiday Train" / "uccc_holiday_train_02.jpg").exists()
    assert (working / "Holiday Train" / "Ascending" / "uccc_holiday_train_ascending_01.webp").exists()
    assert not list(working.rglob(".intake-*.tmp"))
    assert not list(working.rglob("product_info.json"))
    assert _tree(intake / "Loose Original") == loose_before
    assert _tree(catalogue) == catalogue_before
    assert _tree(output) == output_before
    with app.app_context():
        row = db.session.get(CatalogueOperation, operation_id)
        summary = json.loads(row.scope)["operation_summary"]
        assert row.operation_type == "intake_image_rename"
        assert summary["workflow_status"] == "metadata_required"
        assert summary["prepared_relpath"] == "Prepared/Working Result"
        assert summary["rollback_state"] == "removed_after_verification"
        assert set(summary["stage_timings_seconds"]) == {
            "preview_revalidation",
            "staging_copy",
            "temporary_rename",
            "final_rename",
            "staged_verification",
            "visible_result_swap",
        }
        assert eligible_image_rename_results(intake) == []
        assert len(row.scope.encode()) < 256 * 1024
        assert str(intake) not in row.scope
    detail = rename_client.get(response.headers["Location"]).get_data(as_text=True)
    assert '<h1 data-operation-heading>Images renamed — metadata required</h1>' in detail
    assert "Images renamed — metadata required" in detail
    assert "Create Product Metadata" in detail
    assert "Run Append Scan" not in detail


def test_stale_digest_and_duplicate_lock_are_rejected(rename_app, monkeypatch):
    from app.intake_grouping import IntakeOperationActive, acquire_intake_operation, finish_intake_operation
    from app.intake_image_renamer import revalidate_rename_proposal

    app, intake, *_ = rename_app
    with app.app_context():
        preview = image_rename_preview(intake, "Prepared/Working Result", "SKU")
        _image(intake / "Prepared" / "Working Result" / "Holiday Train" / "new.png")
        with pytest.raises(Exception, match="changed"):
            revalidate_rename_proposal("Prepared/Working Result", "SKU", preview["digest"])
        lease = acquire_intake_operation({"source_relpath": "Prepared/Working Result"}, operation_type="intake_image_rename")
        try:
            with pytest.raises(IntakeOperationActive):
                acquire_intake_operation({"source_relpath": "Prepared/Working Result"}, operation_type="intake_folder_edit")
        finally:
            finish_intake_operation(lease, status="failed", summary={"source_images": 0, "copied_images": 0, "failed_images": 1})


def test_promotion_failure_restores_original_without_partial_result(rename_app, rename_client, monkeypatch):
    import app.intake_working_result as working_result

    app, intake, *_ = rename_app
    working = intake / "Prepared" / "Working Result"
    before = _tree(working)
    original = working_result._promote_prepared_result
    calls = {"count": 0}
    def fail_once(*args):
        calls["count"] += 1
        if calls["count"] == 1:
            raise PermissionError("fictional hidden host path")
        return original(*args)
    monkeypatch.setattr(working_result, "_promote_prepared_result", fail_once)
    with app.app_context():
        preview = image_rename_preview(intake, "Prepared/Working Result", "SKU")
    response = _confirm(rename_client, preview)
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    assert _wait(app, operation_id) == "failed"
    assert _tree(working) == before
    assert not (intake / "Prepared" / "Working Result (2)").exists()
    assert not (intake / ".catalogue-intake-staging" / operation_id).exists()


def test_post_promotion_verification_failure_restores_original(rename_app, rename_client, monkeypatch):
    import app.intake_image_renamer as renamer

    app, intake, *_ = rename_app
    working = intake / "Prepared" / "Working Result"
    before = _tree(working)
    original_verify = renamer._verify_renamed_result
    calls = {"count": 0}
    def fail_promoted(*args):
        calls["count"] += 1
        if calls["count"] == 2:
            raise renamer.RenameProposalRejected("injected promoted verification failure")
        return original_verify(*args)
    monkeypatch.setattr(renamer, "_verify_renamed_result", fail_promoted)
    with app.app_context():
        preview = image_rename_preview(intake, "Prepared/Working Result", "SKU")
    response = _confirm(rename_client, preview)
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    assert _wait(app, operation_id) == "failed"
    assert _tree(working) == before


def test_restore_failure_records_controlled_recovery_without_exposing_paths(rename_app, rename_client, monkeypatch):
    import app.intake_working_result as working_result

    app, intake, *_ = rename_app
    monkeypatch.setattr(
        working_result,
        "_promote_prepared_result",
        lambda *_args: (_ for _ in ()).throw(PermissionError("fictional /private/host/path")),
    )
    with app.app_context():
        preview = image_rename_preview(intake, "Prepared/Working Result", "SKU")
    response = _confirm(rename_client, preview)
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    assert _wait(app, operation_id) == "failed"
    with app.app_context():
        row = db.session.get(CatalogueOperation, operation_id)
        summary = json.loads(row.scope)["operation_summary"]
        assert summary["recovery_state"] == "manual_recovery_required"
        assert ".catalogue-intake-rollback" not in row.scope
        assert "/private/host/path" not in (row.error or "")


def test_proven_predecessor_cleanup_is_explicit_and_uncertain_legacy_is_preserved(rename_app, rename_client):
    app, intake, *_ = rename_app
    predecessor = intake / "Prepared" / "Grouped Predecessor"
    _image(predecessor / "Holiday Train" / "train-a.png", (77, 88, 99))
    with app.app_context():
        row = db.session.get(CatalogueOperation, "a" * 32)
        summary = json.loads(row.scope)["operation_summary"]
        summary["source_relpath"] = "Prepared/Grouped Predecessor"
        summary["source_identity"] = _snapshot_identity(
            _snapshot_prepared_result(intake, "Prepared/Grouped Predecessor")
        )
        summary["result_identity"] = _snapshot_identity(
            _snapshot_prepared_result(intake, "Prepared/Working Result")
        )
        row.scope = _summary_scope(summary)
        db.session.commit()
        preview = image_rename_preview(
            intake,
            "Prepared/Working Result",
            "SKU",
        )
    assert preview["lineage"]["eligible"]
    assert preview["cleanup_selected"]
    blocked = _confirm(rename_client, preview, remove_predecessor="yes")
    assert blocked.status_code == 302
    with app.app_context():
        assert not CatalogueOperation.query.filter_by(operation_type="intake_image_rename").first()
    response = _confirm(
        rename_client,
        preview,
        remove_predecessor="yes",
        acknowledge_predecessor="yes",
    )
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    assert _wait(app, operation_id) in {"succeeded", "partial"}
    assert not predecessor.exists()
    assert (intake / "Prepared" / "Working Result").exists()


def test_legacy_lineage_without_identities_never_allows_cleanup(rename_app):
    app, intake, *_ = rename_app
    predecessor = intake / "Prepared" / "Working Result (2)"
    _image(predecessor / "Product" / "image.png")
    with app.app_context():
        row = db.session.get(CatalogueOperation, "a" * 32)
        summary = json.loads(row.scope)["operation_summary"]
        summary["source_relpath"] = "Prepared/Working Result (2)"
        summary.pop("source_identity", None)
        summary.pop("result_identity", None)
        row.scope = _summary_scope(summary)
        db.session.commit()
        preview = image_rename_preview(intake, "Prepared/Working Result", "SKU")
        assert preview["lineage"]["state"] == "uncertain"
        assert not preview["lineage"]["eligible"]
        with pytest.raises(ValueError, match="lineage proof"):
            image_rename_preview(intake, "Prepared/Working Result", "SKU", remove_predecessor=True)
    assert predecessor.exists()


def test_cleanup_failure_is_warning_and_preserves_predecessor(rename_app, rename_client, monkeypatch):
    import app.intake_image_renamer as renamer

    app, intake, *_ = rename_app
    predecessor = intake / "Prepared" / "Predecessor"
    _image(predecessor / "Product" / "image.png")
    with app.app_context():
        row = db.session.get(CatalogueOperation, "a" * 32)
        summary = json.loads(row.scope)["operation_summary"]
        summary.update(
            source_relpath="Prepared/Predecessor",
            source_identity=_snapshot_identity(_snapshot_prepared_result(intake, "Prepared/Predecessor")),
            result_identity=_snapshot_identity(_snapshot_prepared_result(intake, "Prepared/Working Result")),
        )
        row.scope = _summary_scope(summary)
        db.session.commit()
        preview = image_rename_preview(intake, "Prepared/Working Result", "SKU", remove_predecessor=True)
    monkeypatch.setattr(renamer, "_remove_predecessor", lambda *_args: (_ for _ in ()).throw(PermissionError("hidden host path")))
    response = _confirm(rename_client, preview, remove_predecessor="yes", acknowledge_predecessor="yes")
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    assert _wait(app, operation_id) == "partial"
    assert predecessor.exists()


def test_discord_is_terminal_bounded_and_nonfatal(rename_app, rename_client, monkeypatch):
    import app.utils.discord as discord

    app, intake, *_ = rename_app
    sent = []
    monkeypatch.setattr(discord, "send_discord_message", lambda *args, **kwargs: sent.append(kwargs) or (True, "sent"))
    discord.notify_intake_image_rename_completed(result_name="Working", prefix="sku", renamed=4, parent=1, variation=1, warnings=0, predecessor="preserved", elapsed_text="1.0s", operation_id="b" * 32)
    assert len(sent) == 1 and sent[0]["channels"] == ["scans_info"]
    assert ".catalogue-intake" not in json.dumps(sent)
    monkeypatch.setattr(discord, "notify_intake_image_rename_completed", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    with app.app_context():
        preview = image_rename_preview(intake, "Prepared/Working Result", "SKU")
    response = _confirm(rename_client, preview)
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    assert _wait(app, operation_id) in {"succeeded", "partial"}


def test_flattened_parent_product_collision_blocks_confirmation(rename_app):
    app, intake, *_ = rename_app
    working = intake / "Prepared" / "Working Result"
    _image(working / "Working Result" / "same.png")
    with app.app_context():
        preview = image_rename_preview(intake, "Prepared/Working Result", "SKU")
    assert not preview["ready"]
    assert {issue["code"] for issue in preview["issues"]} >= {"flattened_collision", "exact_collision"}


def test_read_only_intake_and_force_overwrite_routes_are_blocked(rename_app, rename_client, monkeypatch):
    import app.intake_image_renamer as renamer

    app, intake, *_ = rename_app
    with app.app_context():
        preview = image_rename_preview(intake, "Prepared/Working Result", "SKU")
    monkeypatch.setattr(
        renamer,
        "intake_readiness",
        lambda: {"readable": True, "writable": False},
    )
    response = _confirm(rename_client, preview)
    assert response.status_code == 302
    assert rename_client.post("/image-preparation/rename/force", data={}).status_code == 404
    assert rename_client.post("/image-preparation/rename/overwrite", data={}).status_code == 404
    assert "os.replace" not in Path("app/intake_image_renamer.py").read_text(encoding="utf-8")
    assert "os.replace" not in Path("app/intake_working_result.py").read_text(encoding="utf-8")


def test_500_image_preview_sequence_exceeds_99_and_remains_bounded(rename_app):
    app, intake, *_ = rename_app
    directory = intake / "Prepared" / "Working Result" / "Many"
    for index in range(500):
        _image(directory / f"image-{index:03d}.png", (index % 255, 40, 50))
    with app.app_context():
        started = time.monotonic()
        preview = image_rename_preview(intake, "Prepared/Working Result", "SKU")
        elapsed = time.monotonic() - started
    row = next(item for item in preview["mappings"] if item["sequence"] == 100 and item["folder_parts"] == ("Many",))
    assert row["recommended_filename"] == "sku_many_100.png"
    assert len(json.dumps({"digest": preview["digest"], "counts": preview["counts"]})) < 1024
    assert elapsed < 10
