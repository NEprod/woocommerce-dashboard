import hashlib
import json
import os
import errno
import shutil
import time
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from app import create_app, db
from app.intake_handoff import (
    HANDOFF_OPERATION_TYPE,
    HANDOFF_STATUS,
    HandoffRejected,
    eligible_handoff_results,
    handoff_preview,
    revalidate_handoff,
)
from app.models import CatalogueOperation, Settings, User
from app.utils.operation_control import reset_operation_control_for_tests
from config import Config


def _image(path, colour=(30, 120, 170)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (12, 9), colour).save(path)


def _metadata(collection_type="Single Variable"):
    value = {
        "collection_type": collection_type,
        "title": "Fictional Holiday Cards",
        "sku_prefix": "FHC-",
        "price": "12.00",
        "categories": ["Cards"],
        "tags": ["fictional"],
        "live": False,
    }
    if collection_type == "Single Variable":
        value.update({
            "attributes": {"Style": ["Hero A", "Hero B"], "Size": ["A5", "A4"]},
            "image_attributes": ["Style", "Size"],
            "variation_modifiers": {"Size=A5": {"price": "14.00"}},
        })
    return value


def _operation_scope(relative, state="validation_required", operation_type="intake_metadata_save"):
    summary = {"prepared_relpath": relative, "workflow_status": state, "warnings": 0}
    return json.dumps({"source_relpath": relative, "workflow_status": state, "operation_summary": summary}, separators=(",", ":"))


@pytest.fixture
def handoff_app(tmp_path):
    from app.intake_grouping import reset_intake_operation_control_for_tests

    instance, intake, catalogue, output = (tmp_path / name for name in ("instance", "intake", "catalogue", "output"))
    for folder in (instance, intake, catalogue, output):
        folder.mkdir()
    prepared = intake / "Prepared" / "Holiday Cards"
    _image(prepared / "Parent" / "FHC_parent_01.PNG")
    _image(prepared / "Hero A" / "A5" / "FHC_hero_a_a5_01.jpg")
    _image(prepared / "Hero B" / "A4" / "FHC_hero_b_a4_01.webp")
    (prepared / "product_info.json").write_text(json.dumps(_metadata(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    before = {path.relative_to(prepared).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in prepared.rglob("*") if path.is_file()}

    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{instance / 'site.db'}"
    app = create_app()
    app.config.update(
        TESTING=True, WTF_CSRF_ENABLED=False, INTAKE_ROOT=str(intake), INTAKE_TEST_MOUNTED=True,
        INTAKE_MUTATION_LOCK_PATH=str(instance / "intake.lock"), DISCORD_ENABLED=False,
    )
    with app.app_context():
        db.session.add(User(email="handoff@example.test", username="handoff-admin", password="unused"))
        db.session.add(Settings(product_folder=str(catalogue), output_folder=str(output), url_prefix="https://example.test/media/"))
        db.session.add(CatalogueOperation(
            id="1" * 32, operation_type="intake_metadata_save", status="succeeded",
            scope=_operation_scope("Prepared/Holiday Cards"), started_at=datetime(2026, 1, 1), finished_at=datetime(2026, 1, 1, 0, 1),
        ))
        db.session.commit()
    reset_intake_operation_control_for_tests()
    reset_operation_control_for_tests()
    try:
        yield app, intake, catalogue, output, prepared, before
    finally:
        with app.app_context():
            db.session.remove()
        reset_intake_operation_control_for_tests()
        reset_operation_control_for_tests()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


@pytest.fixture
def handoff_client(handoff_app):
    app, *_ = handoff_app
    client = app.test_client()
    with app.app_context():
        user_id = User.query.one().id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def _wait(app, operation_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with app.app_context():
            row = db.session.get(CatalogueOperation, operation_id)
            if row and row.status in {"succeeded", "partial", "failed", "interrupted"}:
                return row
        time.sleep(0.02)
    raise AssertionError("handoff operation did not finish")


def _bytes(folder):
    return {path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in folder.rglob("*") if path.is_file()}


def test_access_eligibility_and_safe_source_boundaries(handoff_app):
    app, intake, *_ = handoff_app
    anonymous = app.test_client()
    assert anonymous.get("/image-preparation/handoff").status_code in {302, 401}
    with app.app_context():
        results = eligible_handoff_results(intake)
        assert [(item["path"], item["action"]) for item in results] == [("Prepared/Holiday Cards", "Validate and Copy to Catalogue")]
        for path in ("Holiday Cards", "Prepared", "../Prepared/Holiday Cards", ".catalogue-intake-staging/x", "/output", "/catalogue"):
            with pytest.raises((ValueError, HandoffRejected)):
                handoff_preview(path)


def test_final_validation_counts_destination_and_warning_severity(handoff_app):
    app, _intake, _catalogue, *_ = handoff_app
    with app.app_context():
        preview = handoff_preview("Prepared/Holiday Cards")
    assert preview["ready"]
    assert preview["destination"]["relative"] == "Holiday Cards"
    assert preview["destination"]["action"] == "create"
    assert preview["counts"]["products"] == 1
    assert preview["counts"]["variations"] == 4
    assert preview["counts"]["parent_images"] == 1
    assert preview["counts"]["variation_images"] == 2
    assert preview["publishing_intent"] == "Draft"
    assert any(item["code"] == "new_destination" and item["state"] == "warning" for item in preview["findings"])


def test_blocking_validation_and_unsupported_entries(handoff_app):
    app, _intake, _catalogue, _output, prepared, _before = handoff_app
    (prepared / "unsupported.txt").write_text("no", encoding="utf-8")
    with app.app_context():
        preview = handoff_preview("Prepared/Holiday Cards")
        assert not preview["ready"]
        assert any(item["code"] == "unsupported_file" for item in preview["blocking"])
        with pytest.raises(HandoffRejected, match="blocked"):
            revalidate_handoff("Prepared/Holiday Cards", preview["digest"], acknowledge=True)


def test_symlink_and_parent_case_ambiguity_block(handoff_app):
    app, _intake, _catalogue, _output, prepared, _before = handoff_app
    os.symlink(prepared / "Parent" / "FHC_parent_01.PNG", prepared / "unsafe.png")
    collision_created = True
    try:
        (prepared / "parent").mkdir()
    except FileExistsError:
        collision_created = False
        # The macOS development filesystem is case-insensitive. The shared
        # scanner-aware folder validator is exercised directly with the Linux
        # collision shape that Unraid can represent.
        from app.intake_metadata_builder import _folder_analysis
        analysis = _folder_analysis(
            {"folder": prepared, "folders": ["Parent", "parent"], "images": [], "auxiliary_json": []},
            _metadata(),
        )
        assert any(item["code"] == "duplicate_parent" for item in analysis["findings"])
    with app.app_context():
        preview = handoff_preview("Prepared/Holiday Cards")
    codes = {item["code"] for item in preview["blocking"]}
    assert "symlink" in codes
    if collision_created:
        assert "duplicate_parent" in codes


def test_digest_changes_for_source_metadata_and_destination(handoff_app):
    app, _intake, catalogue, _output, prepared, _before = handoff_app
    with app.app_context():
        first = handoff_preview("Prepared/Holiday Cards")
        (prepared / "Hero A" / "A5" / "FHC_hero_a_a5_01.jpg").write_bytes((prepared / "Parent" / "FHC_parent_01.PNG").read_bytes())
        second = handoff_preview("Prepared/Holiday Cards")
        assert first["digest"] != second["digest"]
        with pytest.raises(HandoffRejected, match="changed"):
            revalidate_handoff("Prepared/Holiday Cards", first["digest"], acknowledge=True)
        destination = catalogue / "Holiday Cards"
        destination.mkdir()
        (destination / "old.txt").write_text("old", encoding="utf-8")
        third = handoff_preview("Prepared/Holiday Cards")
        assert second["digest"] != third["digest"]


def test_confirm_requires_acknowledgements(handoff_app):
    app, *_ = handoff_app
    with app.app_context():
        preview = handoff_preview("Prepared/Holiday Cards")
        with pytest.raises(HandoffRejected, match="Confirm"):
            revalidate_handoff("Prepared/Holiday Cards", preview["digest"])












def test_operation_type_is_bounded_and_no_schema_change(handoff_app):
    assert len(HANDOFF_OPERATION_TYPE) <= 32
    assert HANDOFF_OPERATION_TYPE == "intake_catalogue_handoff"








def test_catalogue_unavailable_or_read_only_blocks_confirmation(handoff_app, monkeypatch):
    app, *_ = handoff_app
    import app.intake_handoff as handoff
    with app.app_context():
        monkeypatch.setattr(handoff, "_catalogue_readiness", lambda **_kwargs: {"state": "read_only", "readable": True, "writable": False, "message": "Catalogue mount is read-only", "root": Path(Settings.query.one().product_folder)})
        preview = handoff_preview("Prepared/Holiday Cards")
        assert any(item["code"] == "catalogue_read_only" for item in preview["blocking"])
        with pytest.raises(HandoffRejected):
            revalidate_handoff(preview["source"], preview["digest"], acknowledge=True)


def test_csrf_is_required_when_enabled(handoff_app):
    app, *_ = handoff_app
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    with app.app_context():
        user_id = User.query.one().id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    assert client.post("/image-preparation/handoff/preview", data={"path": "Prepared/Holiday Cards"}).status_code == 400


@pytest.mark.parametrize("collection_type", ["Simple", "Variable Collection", "Single Variable"])
def test_supported_collection_types_validate_without_projection(handoff_app, collection_type):
    app, _intake, _catalogue, _output, prepared, _before = handoff_app
    document = _metadata(collection_type)
    (prepared / "product_info.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    with app.app_context():
        preview = handoff_preview("Prepared/Holiday Cards")
    assert preview["collection_type"] == collection_type
    assert not any(item["code"] == "unsupported_collection_type" for item in preview["blocking"])


def test_five_hundred_file_validation_is_bounded(handoff_app):
    app, _intake, _catalogue, _output, prepared, _before = handoff_app
    source = prepared / "Parent" / "FHC_parent_01.PNG"
    bulk = prepared / "Bulk"
    bulk.mkdir()
    for index in range(497):
        shutil.copyfile(source, bulk / f"FHC_bulk_{index:04d}.png")
    started = time.monotonic()
    with app.app_context():
        preview = handoff_preview("Prepared/Holiday Cards")
    elapsed = time.monotonic() - started
    assert preview["counts"]["files"] == 501
    assert len(json.dumps({"digest": preview["digest"], "counts": preview["counts"]})) < 2000
    assert elapsed < 15


def test_discord_handoff_summary_is_single_bounded_and_uses_existing_channels(monkeypatch):
    import app.utils.discord as discord
    deliveries = []
    monkeypatch.setattr(discord, "send_discord_message", lambda **kwargs: deliveries.append(kwargs) or (True, "sent"))
    summary = {
        "result_name": "Holiday Cards", "catalogue_destination": "Holiday Cards", "handoff_action": "create",
        "collection_type": "Single Variable", "product_count": 1, "variation_count": 4,
        "total_images": 3, "warnings": 0, "duration_seconds": 1.2,
        "exact_image_variations": 2, "fallback_image_variations": 2, "missing_image_variations": 0,
    }
    assert discord.notify_intake_handoff_completed(summary, operation_id="a" * 32) == (True, "sent")
    assert len(deliveries) == 1
    assert deliveries[0]["channels"] == ["scans_info"]
    assert any(field["name"] == "Exact / fallback / missing" and field["value"] == "2 / 2 / 0" for field in deliveries[0]["embeds"][0]["fields"])
    encoded = json.dumps(deliveries[0])
    assert "/private/" not in encoded and "product_info.json" not in encoded
    deliveries.clear()
    summary["warnings"] = 2
    discord.notify_intake_handoff_completed(summary, operation_id="b" * 32)
    discord.notify_intake_handoff_failed("Holiday Cards", "controlled failure", operation_id="c" * 32)
    assert [item["channels"] for item in deliveries] == [["scans_errors"], ["scans_errors"]]


def _replace_with_winter_fixture(prepared, *, image_attributes=("Style",), parent=False, images=True):
    for entry in list(prepared.iterdir()):
        if entry.is_dir():
            shutil.rmtree(entry)
    document = {
        "collection_type": "Single Variable",
        "title": "Fictional Winter Cards",
        "sku_prefix": "FWC-",
        "price": "12.00",
        "categories": ["Cards"],
        "tags": ["fictional"],
        "live": False,
        "attributes": {
            "Style": ["Gnome", "Santa and his Elves", "Snowman"],
            "Size": ["Small", "Large"],
            "Direction": ["Ascending", "Descending"],
        },
        "image_attributes": list(image_attributes),
    }
    (prepared / "product_info.json").write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for index, style in enumerate(document["attributes"]["Style"]):
        (prepared / style).mkdir(parents=True, exist_ok=True)
        if images:
            _image(prepared / style / f"winter-{index}.jpg", (20 + index, 80, 120))
    if parent:
        _image(prepared / "Parent" / "parent.jpg", (30, 40, 50))
    return document


def test_gnome_style_only_handoff_is_ready_and_diagnostics_are_filtered(handoff_app):
    app, intake, _catalogue, _output, prepared, _before = handoff_app
    _replace_with_winter_fixture(prepared)
    from app.intake_metadata_builder import metadata_preview
    from app.image_preparation import rename_preview
    with app.app_context():
        metadata = metadata_preview(intake, "Prepared/Holiday Cards")
        handoff = handoff_preview("Prepared/Holiday Cards")
        compatibility = rename_preview(intake, "Prepared/Holiday Cards", "FWC")
    assert metadata["ready"] and handoff["ready"] and compatibility["ready"]
    assert metadata["analysis"]["image_health"] == {"exact": 12, "fallback": 0, "missing": 0}
    assert handoff["counts"]["exact_image_variations"] == 12
    assert compatibility["compatibility"]["image_health"] == metadata["analysis"]["image_health"]
    assert {item["code"] for item in metadata["findings"]} == {
        item["code"] for item in handoff["findings"] if item["code"] not in {"optional_meta_title", "optional_meta_description", "prepared_preserved", "new_destination"}
    }
    diagnostics = " ".join(item["message"] for item in handoff["findings"])
    assert "product_info.json" not in diagnostics
    assert "winter-0.jpg" not in diagnostics


def test_style_level_scanner_fallback_allows_handoff_with_named_warning(handoff_app):
    app, _intake, _catalogue, _output, prepared, _before = handoff_app
    _replace_with_winter_fixture(prepared, image_attributes=("Style", "Direction"))
    with app.app_context():
        preview = handoff_preview("Prepared/Holiday Cards")
    assert preview["ready"]
    assert preview["counts"]["fallback_image_variations"] == 12
    warning = next(item for item in preview["warnings"] if item["code"] == "image_fallback_broader")
    assert "Gnome/" in warning["message"]
    assert "Handoff remains allowed" in warning["message"]
    assert not any(item["code"] == "unsupported_depth" for item in preview["blocking"])


def test_parent_preview_fallback_allows_handoff_without_changing_ownership(handoff_app):
    app, _intake, _catalogue, _output, prepared, _before = handoff_app
    _replace_with_winter_fixture(prepared, parent=True, images=False)
    with app.app_context():
        preview = handoff_preview("Prepared/Holiday Cards")
    assert preview["ready"]
    assert preview["counts"]["parent_images"] == 1
    assert preview["counts"]["variation_images"] == 0
    assert preview["counts"]["fallback_image_variations"] == 12
    assert any(item["code"] == "image_fallback_parent" for item in preview["warnings"])
