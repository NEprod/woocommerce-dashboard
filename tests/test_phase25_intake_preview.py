import hashlib
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PIL import Image

from app import create_app, db
from app.models import User
from app.image_preparation import (
    browse_intake,
    grouping_preview,
    intake_readiness,
    normalize_prefix,
    rename_preview,
)
from config import Config


ROOT = Path(__file__).resolve().parents[1]
UNRAID = ROOT / "unraid" / "my-woocommerce-dashboard.xml"


def _image(path, colour=(40, 130, 90)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (12, 10), colour).save(path)


def _tree_digest(root):
    rows = []
    if not root.exists():
        return rows
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        stat = path.lstat()
        rows.append((path.relative_to(root).as_posix(), stat.st_mode, stat.st_size, stat.st_mtime_ns))
    return rows


@pytest.fixture
def intake_app(tmp_path):
    instance = tmp_path / "instance"
    intake = tmp_path / "intake"
    instance.mkdir()
    intake.mkdir()
    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{instance / 'site.db'}"
    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        INTAKE_ROOT=str(intake),
        INTAKE_TEST_MOUNTED=True,
    )
    with app.app_context():
        db.session.add(User(email="intake@example.com", username="intake-admin", password="unused"))
        db.session.commit()
    try:
        yield app, intake, instance
    finally:
        with app.app_context():
            db.session.remove()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


@pytest.fixture
def intake_client(intake_app):
    app, *_ = intake_app
    client = app.test_client()
    with app.app_context():
        user_id = User.query.one().id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def test_unraid_template_adds_optional_intake_without_changing_existing_contract():
    root = ET.parse(UNRAID).getroot()
    configs = {node.attrib["Name"]: node for node in root.findall("Config")}
    intake = configs["Catalogue Intake"].attrib
    assert intake["Target"] == "/intake"
    assert intake["Default"] == ""
    assert intake["Mode"] == "rw"
    assert intake["Required"] == "false"
    assert "loose and prepared product images" in intake["Description"]
    assert configs["Application Data"].attrib["Target"] == "/app/instance"
    assert configs["Product Catalogue"].attrib["Target"] == "/catalogue"
    assert configs["Generated Output"].attrib["Target"] == "/output"
    assert configs["WebUI Port"].attrib["Target"] == "7485"


def test_readiness_distinguishes_missing_read_only_writable_and_unsafe(tmp_path):
    missing = tmp_path / "missing"
    assert intake_readiness(missing, mounted=False)["state"] == "unavailable"
    root = tmp_path / "intake"
    root.mkdir()
    assert intake_readiness(root, mounted=True, access_check=lambda _path, mode: mode == os.R_OK)["state"] == "read_only"
    assert intake_readiness(root, mounted=True, access_check=lambda _path, _mode: True)["state"] == "writable"
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    assert intake_readiness(alias, mounted=True)["state"] == "unsafe"
    assert not missing.exists()


def test_workspace_requires_authentication_and_missing_mount_is_controlled(intake_app):
    app, intake, _instance = intake_app
    response = app.test_client().get("/image-preparation")
    assert response.status_code in {302, 401}
    if response.status_code == 302:
        assert "/login" in response.headers["Location"]
    app.config["INTAKE_TEST_MOUNTED"] = False
    client = app.test_client()
    with app.app_context():
        user_id = User.query.one().id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    html = client.get("/image-preparation").get_data(as_text=True)
    assert "Catalogue Intake is not mounted" in html
    assert str(intake) not in html
    assert "Preview only" in html


@pytest.mark.parametrize("value", ["../outside", "%2e%2e/outside", "%252e%252e/outside", "/absolute", "C:/drive", "bad\\path", "bad\x00path"])
def test_browser_rejects_traversal_absolute_and_encoded_paths(tmp_path, value):
    root = tmp_path / "intake"
    root.mkdir()
    with pytest.raises(ValueError):
        browse_intake(root, value)


def test_browser_classifies_entries_and_never_follows_unsafe_links(tmp_path):
    root = tmp_path / "intake"
    selected = root / "Christmas Intake"
    selected.mkdir(parents=True)
    (selected / "Beta").mkdir()
    (selected / "alpha").mkdir()
    _image(selected / "valid.PNG")
    (selected / "corrupt.jpg").write_text("not an image", encoding="utf-8")
    (selected / "notes.txt").write_text("notes", encoding="utf-8")
    (selected / ".DS_Store").write_text("hidden", encoding="utf-8")
    (selected / "._valid.png").write_text("hidden", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (selected / "escape").symlink_to(outside, target_is_directory=True)
    hard = selected / "hard.png"
    os.link(selected / "valid.PNG", hard)
    if hasattr(os, "mkfifo"):
        os.mkfifo(selected / "pipe")

    browser = browse_intake(root, "Christmas Intake")
    assert [item["name"] for item in browser["directories"]] == ["alpha", "Beta"]
    assert browser["counts"]["supported_images"] == 0  # both valid names share a hard-link identity
    assert browser["counts"]["corrupt_images"] == 1
    assert browser["counts"]["unsupported_entries"] == 1
    assert browser["counts"]["hidden_system"] == 2
    assert browser["counts"]["unsafe_entries"] >= 2
    assert browser["breadcrumbs"][-1]["label"] == "Christmas Intake"
    assert all(not item.get("target") for item in browser["issues"])


def test_grouping_preview_matches_legacy_rule_and_modern_safety(tmp_path):
    root = tmp_path / "intake"
    source = root / "Christmas Countdown"
    source.mkdir(parents=True)
    for name in (
        "Train1.png", "Train2.png", "Train 1.png", "Train-01.png", "Train_02.png",
        "train3.png", "TRAIN4.png", "Design.png", "Design01.jpg", "Parent1.png",
        "Solo.webp", "123.png",
    ):
        _image(source / name)
    _image(source / ".hidden.png")
    (source / "corrupt.jpeg").write_text("broken", encoding="utf-8")
    (source / "notes.txt").write_text("ignored", encoding="utf-8")

    before = _tree_digest(root)
    preview = grouping_preview(root, "Christmas Countdown")
    after = _tree_digest(root)

    by_source = {item["source_name"]: item for item in preview["mappings"]}
    assert by_source["Train1.png"]["legacy_base"] == "Train"
    assert by_source["Train 1.png"]["legacy_base"] == "Train "
    assert by_source["Train 1.png"]["proposed_group"] == "Train"
    assert by_source["Train-01.png"]["proposed_group"] == "Train-"
    assert by_source["Train_02.png"]["proposed_group"] == "Train_"
    assert by_source["Design.png"]["proposed_group"] == "Design"
    assert by_source["Design01.jpg"]["proposed_group"] == "Design"
    assert by_source["Parent1.png"]["reserved_parent"] is True
    assert by_source["Solo.webp"]["single_image"] is True
    assert by_source["123.png"]["state"] == "blocking"
    assert any(issue["code"] == "case_ambiguity" for issue in preview["issues"])
    assert any(issue["code"] == "reserved_parent" for issue in preview["issues"])
    assert "Prepared/Christmas Countdown" in preview["proposed_result"]
    assert preview["preview_only"] is True
    assert preview["digest"] == grouping_preview(root, "Christmas Countdown")["digest"]
    assert before == after

    _image(source / "Changed9.png")
    assert preview["digest"] != grouping_preview(root, "Christmas Countdown")["digest"]


def test_grouping_preview_uses_duplicate_safe_result_name_and_reports_existing(tmp_path):
    root = tmp_path / "intake"
    source = root / "Cards"
    source.mkdir(parents=True)
    _image(source / "Card1.png")
    (root / "Prepared" / "Cards").mkdir(parents=True)
    (root / "Prepared" / "cards (2)").mkdir()
    preview = grouping_preview(root, "Cards")
    assert preview["result_name"] == "Cards (3)"
    assert any(issue["code"] == "existing_result" for issue in preview["issues"])


@pytest.mark.parametrize("value", ["", "   ", ".", "..", "bad/name", "bad\\name", "/absolute", "C:drive", "bad\x01name"])
def test_prefix_validation_rejects_unsafe_values(value):
    with pytest.raises(ValueError):
        normalize_prefix(value)


def test_rename_preview_shows_one_two_level_parent_and_complete_paths(tmp_path):
    root = tmp_path / "intake"
    source = root / "Ultimate Christmas Countdown"
    _image(source / "Holiday Express Train" / "original-b.JPG")
    _image(source / "Holiday Express Train" / "original-a.PNG")
    _image(source / "Holiday Express Train" / "Ascending" / "first.PNG")
    _image(source / "Parent" / "parent-b.jpg")
    _image(source / "Parent" / "parent-a.png")
    _image(source / "Other" / "Parent" / "nested.png")

    before = _tree_digest(root)
    preview = rename_preview(root, "Ultimate Christmas Countdown", " U CCC ")
    by_source = {item["source_relpath"]: item for item in preview["mappings"]}

    assert preview["normalised_prefix"] == "u_ccc"
    assert by_source["Ultimate Christmas Countdown/Holiday Express Train/original-a.PNG"]["recommended_filename"] == "u_ccc_holiday_express_train_01.png"
    assert by_source["Ultimate Christmas Countdown/Holiday Express Train/original-b.JPG"]["recommended_filename"] == "u_ccc_holiday_express_train_02.jpg"
    two = by_source["Ultimate Christmas Countdown/Holiday Express Train/Ascending/first.PNG"]
    assert two["recommended_filename"] == "u_ccc_holiday_express_train_ascending_01.png"
    assert two["hierarchy_components"] == ["Holiday Express Train", "Ascending"]
    parent = by_source["Ultimate Christmas Countdown/Parent/parent-a.png"]
    assert parent["hierarchy_type"] == "parent"
    assert parent["recommended_filename"] == "u_ccc_ultimate_christmas_countdown_01.png"
    nested = by_source["Ultimate Christmas Countdown/Other/Parent/nested.png"]
    assert nested["hierarchy_type"] != "parent"
    assert nested["recommended_filename"] == "u_ccc_other_parent_01.png"
    assert parent["destination_relpath"].endswith("Parent/u_ccc_ultimate_christmas_countdown_01.png")
    assert preview["digest"] == rename_preview(root, "Ultimate Christmas Countdown", " U CCC ")["digest"]
    assert before == _tree_digest(root)
    assert not (root / "Prepared").exists()


def test_rename_sequence_over_99_extension_case_and_collisions(tmp_path):
    root = tmp_path / "intake"
    source = root / "Many"
    for index in range(100):
        _image(source / "Group" / f"source-{index:03d}.PNG")
    _image(source / "A_B" / "C" / "one.png")
    _image(source / "A" / "B_C" / "two.png")

    preview = rename_preview(root, "Many", "SKU")
    filenames = [item["recommended_filename"] for item in preview["mappings"] if "/Group/" in item["source_relpath"]]
    assert filenames[0] == "sku_group_01.png"
    assert filenames[-1] == "sku_group_100.png"
    assert any(issue["code"] == "flattened_collision" for issue in preview["issues"])
    assert preview["ready"] is False


def test_single_variable_metadata_confirms_parent_and_attribute_hierarchy(tmp_path):
    root = tmp_path / "intake"
    source = root / "Single Variable Fixture"
    source.mkdir(parents=True)
    (source / "product_info.json").write_text(
        json.dumps(
            {
                "collection_type": "Single Variable",
                "sku_prefix": "SVF",
                "image_attributes": ["Style", "Size"],
            }
        ),
        encoding="utf-8",
    )
    _image(source / "PaReNt" / "parent.PNG")
    _image(source / "Hero A" / "A5" / "variation.JPG")
    preview = rename_preview(root, "Single Variable Fixture", "SVF")
    rows = {item["source_filename"]: item for item in preview["mappings"]}
    assert rows["parent.PNG"]["hierarchy_type"] == "parent"
    assert rows["variation.JPG"]["hierarchy_type"] == "variation"
    assert rows["variation.JPG"]["hierarchy_components"] == ["Hero A", "A5"]
    assert preview["compatibility"]["image_attributes"] == ["Style", "Size"]
    assert preview["ready"] is True


def test_bounded_500_image_preview_is_deterministic_and_read_only(tmp_path):
    root = tmp_path / "intake"
    source = root / "Bounded Fixture"
    for directory in range(20):
        for image in range(25):
            extension = ("png", "jpg", "webp")[image % 3]
            _image(source / f"Group {directory:02d}" / f"image-{image:03d}.{extension}")
    (source / ".DS_Store").write_text("hidden", encoding="utf-8")
    (source / "notes.txt").write_text("unsupported", encoding="utf-8")
    (source / "broken.png").write_text("corrupt", encoding="utf-8")
    before = _tree_digest(root)
    started = time.monotonic()
    preview = rename_preview(root, "Bounded Fixture", "BND")
    elapsed = time.monotonic() - started
    assert len(preview["mappings"]) == 500
    assert len(preview["digest"]) == 64
    assert preview["digest"] == rename_preview(root, "Bounded Fixture", "BND")["digest"]
    assert elapsed < 10
    assert _tree_digest(root) == before


def test_unicode_normalisation_and_parent_case_ambiguity_are_blocking(tmp_path, monkeypatch):
    root = tmp_path / "intake"
    source = root / "Unicode"
    source.mkdir(parents=True)
    rows = [
        ("Parent", "one.png"),
        ("PARENT", "two.png"),
        ("Caf\u00e9", "one.png"),
        ("Cafe\u0301", "two.png"),
    ]
    images = [
        {
            "name": name,
            "source_relpath": f"Unicode/{folder}/{name}",
            "folder_parts": (folder,),
            "size": 100,
            "mtime_ns": 1,
            "extension": ".png",
            "thumbnail_token": None,
        }
        for folder, name in rows
    ]
    monkeypatch.setattr(
        "app.image_preparation._walk_intake_images",
        lambda *_args: (images, [], ["Parent", "PARENT", "Caf\u00e9", "Cafe\u0301"]),
    )
    preview = rename_preview(root, "Unicode", "SKU")
    codes = {issue["code"] for issue in preview["issues"]}
    assert "duplicate_parent" in codes
    assert "unicode_collision" in codes or "flattened_collision" in codes
    assert preview["ready"] is False


def test_routes_render_navigation_visibility_and_preview_only_contract(intake_client, intake_app):
    _app, intake, _instance = intake_app
    source = intake / "Loose Images"
    source.mkdir()
    _image(source / "Train1.png")
    _image(source / "Train2.jpg")

    overview = intake_client.get("/image-preparation")
    assert overview.status_code == 200
    html = overview.get_data(as_text=True)
    assert "Catalogue Intake" in html
    assert "Group loose images" in html
    assert "Preview image renaming" in html
    assert html.count("<h1") == 1
    assert html.count("<main") == 1

    grouped = intake_client.get("/image-preparation/group", query_string={"path": "Loose Images"})
    text = grouped.get_data(as_text=True)
    assert grouped.status_code == 200
    assert "Preview only — no files or folders have been created." in text
    assert "Prepared/Loose Images/Train/Train1.png" in text
    assert "Source file" in text and "Proposed destination" in text

    renamed = intake_client.get("/image-preparation/rename", query_string={"path": "Loose Images", "prefix": "UCCC"})
    text = renamed.get_data(as_text=True)
    assert renamed.status_code == 200
    assert "uccc_loose_images_01.png" not in text  # root-level images are not scanner-owned product folders
    assert "This filename prefix is independent from scanner-generated product SKUs" in text
    assert "Preview only — no files or folders have been created." in text
    assert "Catalogue Intake" in html
    assert str(intake) not in grouped.get_data(as_text=True)


def test_thumbnail_is_authenticated_private_and_rejects_invalid_or_symlink(intake_client, intake_app):
    app, intake, _instance = intake_app
    _image(intake / "Images" / "valid.png")
    (intake / "Images" / "invalid.png").write_text("invalid", encoding="utf-8")
    outside = intake.parent / "outside.png"
    _image(outside)
    (intake / "Images" / "escape.png").symlink_to(outside)

    from app.image_preparation import intake_image_token

    with app.app_context():
        valid = intake_image_token("Images/valid.png")
        invalid = intake_image_token("Images/invalid.png")
        escape = intake_image_token("Images/escape.png")
    anonymous = app.test_client().get(f"/intake-images/{valid}")
    assert anonymous.status_code in {302, 401}
    response = intake_client.get(f"/intake-images/{valid}")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.cache_control.private
    assert intake_client.get(f"/intake-images/{invalid}").status_code == 404
    assert intake_client.get(f"/intake-images/{escape}").status_code == 404
    assert intake_client.get("/intake-images/not-a-valid-token").status_code == 404


def test_preview_is_request_scoped_and_does_not_touch_database_or_scanner_state(intake_client, intake_app):
    _app, intake, instance = intake_app
    source = intake / "Read Only"
    source.mkdir()
    _image(source / "Image1.png")
    before = _tree_digest(intake)
    database_before = hashlib.sha256((instance / "site.db").read_bytes()).hexdigest()
    assert intake_client.get("/image-preparation/group", query_string={"path": "Read Only"}).status_code == 200
    assert intake_client.get("/image-preparation/rename", query_string={"path": "Read Only", "prefix": "SKU"}).status_code == 200
    assert before == _tree_digest(intake)
    assert database_before == hashlib.sha256((instance / "site.db").read_bytes()).hexdigest()
    assert not list(intake.rglob(".scanned"))
    assert not list(intake.rglob("sku_index.json"))
    assert not (intake / "Prepared").exists()
