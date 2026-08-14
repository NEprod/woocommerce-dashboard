import json
import shutil
from pathlib import Path

from PIL import Image

from app.utils.csv_writer import (
    build_common_fields,
    build_variable_parent,
    build_variation_row,
)
from app.utils.json_utils import apply_variation_modifiers, merge_product_json
from app.utils import scanner as scanner_module
from app.utils import file_markers as marker_module
from app.utils.scanner import scan_collection


FIXTURES = Path(__file__).parent / "fixtures" / "catalogue"


def _simple_collection(tmp_path):
    collection = tmp_path / "catalogue" / "Simple Collection"
    shutil.copytree(FIXTURES / "Simple Collection", collection)
    product = collection / "Blue Token"
    Image.new("RGB", (2, 3), "#336699").save(product / "fictional-blue.png")
    output = tmp_path / "output"
    output.mkdir()
    return collection, product, output


def test_append_update_and_full_preserve_current_selection_and_sku_rules(
    tmp_path, quiet_log
):
    collection, product, output = _simple_collection(tmp_path)

    appended = scan_collection(
        collection, "https://invalid.example/assets/", output, log=quiet_log
    )
    assert [row["SKU"] for row in appended] == ["FIC-S-0001"]
    assert scan_collection(
        collection, "https://invalid.example/assets/", output, log=quiet_log
    ) == []

    (product / ".update").write_text("fixture", encoding="utf-8")
    updated = scan_collection(
        collection,
        "https://invalid.example/assets/",
        output,
        update_csv=True,
        log=quiet_log,
    )
    assert [row["SKU"] for row in updated] == ["FIC-S-0001"]
    assert not (product / ".update").exists()

    marker = json.loads((product / ".scanned").read_text(encoding="utf-8"))
    marker["sku"] = "FIC-S-0042"
    (product / ".scanned").write_text(json.dumps(marker), encoding="utf-8")
    (collection / "sku_index.json").write_text(
        json.dumps({"counter": 42}), encoding="utf-8"
    )

    full = scan_collection(
        collection,
        "https://invalid.example/assets/",
        output,
        force_update=True,
        update_csv=False,
        log=quiet_log,
    )
    assert [row["SKU"] for row in full] == ["FIC-S-0001"]


def test_scanned_marker_payload_is_the_current_durable_identity_contract(
    tmp_path, quiet_log
):
    collection, product, output = _simple_collection(tmp_path)
    scan_collection(collection, "https://invalid.example/assets/", output, log=quiet_log)

    marker = json.loads((product / ".scanned").read_text(encoding="utf-8"))
    assert set(marker) == {"sku", "title", "images_used", "scan_date"}
    assert marker["sku"] == "FIC-S-0001"
    assert marker["title"] == "Blue Token - Fictional Desk Token"
    assert marker["images_used"] == ["fictional-blue.png"]


def test_scanner_filesystem_side_effect_order_is_sku_images_then_marker(
    tmp_path, quiet_log, monkeypatch
):
    collection, _, output = _simple_collection(tmp_path)
    events = []
    original_generate = scanner_module.generate_sku
    original_process = scanner_module.process_images
    original_write = scanner_module.write_scanned

    def generate(*args, **kwargs):
        events.append("sku_index")
        return original_generate(*args, **kwargs)

    def process(*args, **kwargs):
        events.append("images")
        return original_process(*args, **kwargs)

    def write(*args, **kwargs):
        events.append("scanned")
        return original_write(*args, **kwargs)

    monkeypatch.setattr(scanner_module, "generate_sku", generate)
    monkeypatch.setattr(scanner_module, "process_images", process)
    monkeypatch.setattr(scanner_module, "write_scanned", write)

    scan_collection(collection, "https://invalid.example/assets/", output, log=quiet_log)
    assert events == ["sku_index", "images", "scanned"]


def test_phase0_pipeline_order_removes_update_before_database_ingestion(
    tmp_path, quiet_log, monkeypatch
):
    collection, product, output = _simple_collection(tmp_path)
    (product / ".update").write_text("fixture", encoding="utf-8")
    events = []
    original_generate = scanner_module.generate_sku
    original_process = scanner_module.process_images
    original_write = scanner_module.write_scanned
    original_remove = marker_module.os.remove

    def generate(*args, **kwargs):
        events.append("sku_index")
        return original_generate(*args, **kwargs)

    def process(*args, **kwargs):
        events.append("images")
        return original_process(*args, **kwargs)

    def write(*args, **kwargs):
        events.append("scanned")
        return original_write(*args, **kwargs)

    def remove(path):
        if str(path).endswith(".update"):
            events.append("update_removed")
        return original_remove(path)

    monkeypatch.setattr(scanner_module, "generate_sku", generate)
    monkeypatch.setattr(scanner_module, "process_images", process)
    monkeypatch.setattr(scanner_module, "write_scanned", write)
    monkeypatch.setattr(marker_module.os, "remove", remove)

    scan_collection(collection, "https://invalid.example/assets/", output, log=quiet_log)
    events.append("database_ingestion")

    assert events == [
        "sku_index",
        "images",
        "scanned",
        "update_removed",
        "database_ingestion",
    ]


def test_phase0_database_failure_leaves_retry_skipped_after_marker_was_written(
    tmp_path, quiet_log
):
    collection, product, output = _simple_collection(tmp_path)

    rows = scan_collection(
        collection, "https://invalid.example/assets/", output, log=quiet_log
    )
    assert [row["SKU"] for row in rows] == ["FIC-S-0001"]
    # Characterize the old ordering: pretend DB ingestion failed after scanning.
    assert (product / ".scanned").exists()
    assert scan_collection(
        collection, "https://invalid.example/assets/", output, log=quiet_log
    ) == []


def test_unknown_collection_type_is_accepted_by_validation_but_emits_no_rows(
    tmp_path, quiet_log
):
    collection = tmp_path / "catalogue" / "Unknown Collection"
    (collection / "Product").mkdir(parents=True)
    (collection / "product_info.json").write_text(
        json.dumps(
            {
                "collection_type": "Unknown Fixture Type",
                "sku_prefix": "FIC-U-",
            }
        ),
        encoding="utf-8",
    )

    assert scan_collection(
        collection,
        "https://invalid.example/assets/",
        tmp_path / "output",
        log=quiet_log,
    ) == []


def test_resolved_variation_sale_price_is_not_emitted_by_row_builder(quiet_log):
    base = {
        "sku": "FIC-V-0001",
        "price": "10.00",
        "sale_price": "9.00",
        "variation_modifiers": {
            "Size=Large": {"price": "12.00", "sale_price": "8.00"}
        },
    }
    attrs = {"Size": "Large"}
    modifiers = apply_variation_modifiers(base, attrs, log=quiet_log)
    row = build_variation_row(
        base,
        {"attributes": attrs, "modifiers": modifiers},
        [],
        1,
        override_sku="FIC-V-0001-1",
    )

    assert modifiers["sale_price"] == "8.00"
    assert row["Regular price"] == "12.00"
    assert row["Sale price"] == "9.00"


def test_shipping_class_is_authored_but_current_row_output_is_blank():
    row = build_common_fields(
        {"sku": "FIC-S-0001", "shipping_class": "fixture-class"}, []
    )
    assert row["Shipping class"] == ""


def test_editor_style_relationship_keys_do_not_feed_current_row_aliases():
    row = build_common_fields(
        {
            "sku": "FIC-S-0001",
            "upsell_ids": ["UP-1"],
            "cross_sell_ids": ["CROSS-1"],
        },
        [],
    )
    assert row["Upsells"] == ""
    assert row["Cross-sells"] == ""


def test_woo_parent_row_emits_only_first_five_attributes():
    attributes = {f"Attribute {index}": [str(index)] for index in range(1, 7)}
    row = build_variable_parent(
        {"sku": "FIC-V-0001", "attributes": attributes}, []
    )
    assert row["Attribute 5 name"] == "Attribute 5"
    assert "Attribute 6 name" not in row


def test_list_inheritance_is_additive_but_order_is_not_a_contract():
    merged = merge_product_json(
        {"sku_prefix": "FIC-", "tags": ["shared", "common"]},
        {"tags": ["override", "shared"]},
    )
    assert set(merged["tags"]) == {"shared", "common", "override"}
