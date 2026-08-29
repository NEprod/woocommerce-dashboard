import json
from pathlib import Path

import pytest
from PIL import Image

from app import create_app, db
from app.models import Product, ProductAsset, Settings, Variation
from app.utils.image_resolution import resolve_single_variable_image_layout
from app.utils.ingest import ingest_rows_to_db
from app.utils.scanner import scan_collection
from config import Config


def _document(image_attributes=("Style",)):
    return {
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


def _layout(document, folders, images):
    return resolve_single_variable_image_layout(document, folders, images)


def test_one_image_attribute_ignores_non_image_attribute_depth():
    result = _layout(
        _document(),
        ["Gnome", "Santa and his Elves", "Snowman"],
        [
            "Gnome/gnome.jpg",
            "Santa and his Elves/santa.jpg",
            "Snowman/snowman.jpg",
        ],
    )
    assert result["expected_variations"] == 12
    assert result["image_health"] == {"exact": 12, "fallback": 0, "missing": 0}
    assert not result["findings"]
    assert {row["resolved_path"] for row in result["resolutions"]} == {
        "Gnome/", "Santa and his Elves/", "Snowman/"
    }


def test_two_image_attributes_allow_non_image_size_without_third_level():
    document = _document(("Style", "Direction"))
    folders = [
        "Gnome", "Gnome/Ascending", "Gnome/Descending",
        "Santa and his Elves", "Santa and his Elves/Ascending", "Santa and his Elves/Descending",
        "Snowman", "Snowman/Ascending", "Snowman/Descending",
    ]
    images = [f"{style}/{direction}/image.jpg" for style in document["attributes"]["Style"] for direction in document["attributes"]["Direction"]]
    result = _layout(document, folders, images)
    assert result["expected_variations"] == 12
    assert result["image_health"] == {"exact": 12, "fallback": 0, "missing": 0}
    assert not result["findings"]


def test_scanner_supported_broader_source_is_warning_not_blocking():
    result = _layout(
        _document(("Style", "Direction")),
        ["Gnome", "Santa and his Elves", "Snowman"],
        [
            "Gnome/gnome.jpg",
            "Santa and his Elves/santa.jpg",
            "Snowman/snowman.jpg",
        ],
    )
    assert result["image_health"] == {"exact": 0, "fallback": 12, "missing": 0}
    assert {row["resolution_type"] for row in result["resolutions"]} == {"broader"}
    warning = next(item for item in result["findings"] if item["code"] == "image_fallback_broader")
    assert warning["state"] == "warning"
    assert "Gnome/" in warning["message"]
    assert not any(item["state"] == "blocking" for item in result["findings"])


def test_parent_preview_fallback_is_warning_and_remains_parent_owned():
    result = _layout(
        _document(),
        ["Parent", "Gnome", "Santa and his Elves", "Snowman"],
        ["Parent/parent.jpg"],
    )
    assert result["image_health"] == {"exact": 0, "fallback": 12, "missing": 0}
    assert {row["resolution_type"] for row in result["resolutions"]} == {"parent"}
    assert {row["owner_type"] for row in result["resolutions"]} == {"parent"}
    warning = next(item for item in result["findings"] if item["code"] == "image_fallback_parent")
    assert warning["state"] == "warning"
    assert "Parent/" in warning["message"]


def test_no_resolvable_source_blocks():
    result = _layout(
        _document(),
        ["Gnome", "Santa and his Elves", "Snowman"],
        [],
    )
    assert result["image_health"] == {"exact": 0, "fallback": 0, "missing": 12}
    assert any(item["code"] == "missing_image_source" and item["state"] == "blocking" for item in result["findings"])


def test_missing_first_image_attribute_folder_blocks_even_with_parent():
    result = _layout(
        _document(),
        ["Parent", "Gnome", "Snowman"],
        ["Parent/parent.jpg", "Gnome/gnome.jpg", "Snowman/snowman.jpg"],
    )
    assert any(item["code"] == "missing_image_owner_folder" and item["state"] == "blocking" for item in result["findings"])


def test_too_deep_unknown_and_root_images_are_filtered_diagnostics():
    result = _layout(
        _document(),
        ["Gnome", "Gnome/Unexpected", "Unknown", "Santa and his Elves", "Snowman"],
        [
            "Gnome/gnome.jpg", "Gnome/Unexpected/deep.jpg", "Unknown/unknown.jpg",
            "Santa and his Elves/santa.jpg", "Snowman/snowman.jpg", "root.jpg",
        ],
    )
    codes = {item["code"] for item in result["findings"]}
    assert {"unsupported_depth", "unexplained_folders", "unresolved_image_owner"} <= codes
    messages = " ".join(item["message"] for item in result["findings"])
    assert "product_info.json" not in messages
    assert "gnome.jpg" not in messages


@pytest.mark.parametrize("parent_name", ["parent", "Parent", "PARENT", "PaReNt"])
def test_parent_name_is_case_insensitive_and_casing_is_preserved(parent_name):
    result = _layout(
        _document(),
        [parent_name, "Gnome", "Santa and his Elves", "Snowman"],
        [f"{parent_name}/parent.jpg"],
    )
    assert result["parent_folders"] == [parent_name]
    assert {row["resolved_path"] for row in result["resolutions"]} == {f"{parent_name}/"}


def test_duplicate_parent_case_variants_block():
    result = _layout(
        _document(),
        ["Parent", "parent", "Gnome", "Santa and his Elves", "Snowman"],
        ["Parent/one.jpg", "parent/two.jpg"],
    )
    assert any(item["code"] == "duplicate_parent" and item["state"] == "blocking" for item in result["findings"])


def test_nested_parent_is_not_reserved_and_must_match_metadata():
    result = _layout(
        _document(("Style", "Direction")),
        ["Gnome", "Gnome/Parent", "Santa and his Elves", "Snowman"],
        ["Gnome/Parent/image.jpg"],
    )
    assert any(item["code"] == "unexplained_folders" and item["state"] == "blocking" for item in result["findings"])
    assert result["parent_folders"] == []


def _image(path, colour=(30, 120, 170)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (12, 9), colour).save(path)


@pytest.mark.parametrize("image_attributes", [("Style",), ("Style", "Direction")])
def test_scanner_rows_and_ingestion_reuse_style_images_without_duplicates(tmp_path, quiet_log, image_attributes):
    catalogue = tmp_path / "catalogue"
    collection = catalogue / "Fictional Winter Cards"
    output = tmp_path / "output"
    collection.mkdir(parents=True)
    output.mkdir()
    document = _document(image_attributes)
    (collection / "product_info.json").write_text(json.dumps(document), encoding="utf-8")
    for index, style in enumerate(document["attributes"]["Style"]):
        _image(collection / style / f"style-{index}.jpg", (index * 40, 80, 120))

    rows = scan_collection(collection, "https://invalid.example/images/", output, log=quiet_log)
    parent = next(row for row in rows if row["Type"] == "variable")
    variations = [row for row in rows if row["Type"] == "variation"]
    assert len(variations) == 12
    for row in variations:
        attributes = {row[f"Attribute {index} name"]: row[f"Attribute {index} value(s)"] for index in range(1, 4)}
        style_index = document["attributes"]["Style"].index(attributes["Style"])
        assert row["Images"].endswith(f"style-{style_index}.webp")

    database = tmp_path / "projection.db"
    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{database}"
    try:
        app = create_app()
        with app.app_context():
            db.session.add(Settings(product_folder=str(catalogue), output_folder=str(output), url_prefix="https://invalid.example/images/"))
            db.session.commit()
            result = ingest_rows_to_db(rows, log=quiet_log)
            assert result["products_failed"] == 0
            product = Product.query.one()
            assert product.sku == parent["SKU"]
            assert Variation.query.count() == 12
            for variation in Variation.query.all():
                assets = ProductAsset.query.filter_by(product_id=product.id, variation_id=variation.id, kind="image").all()
                assert len(assets) == 1
                assert assets[0].source_relpath.startswith("Fictional Winter Cards/")
                assert assets[0].source_relpath.split("/")[1] in document["attributes"]["Style"]
    finally:
        Config.SQLALCHEMY_DATABASE_URI = original_uri
