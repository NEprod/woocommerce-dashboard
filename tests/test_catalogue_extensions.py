"""M3A.4 local source contracts only; no API writes or real catalogue files."""
import copy
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from app.catalogue_fields import validate_extensions, shipping_details
from app.commerce import catalogue_snapshot
from app.product_info import validate_product_info
from app.utils.json_utils import merge_product_json
from app.utils.scanner import scan_collection, build_variations
from test_shopify_drafts import world
from test_phase3_woo_publish_preview import preview_app
from test_shopify_foundation import isolated_transport


FIELDS = {"country_of_origin": "GB", "requires_shipping": True,
    "customisation": {"proof_policy": "required", "fields": {
        "name": {"type": "text", "label": "Name", "required": True},
        "photo": {"type": "photo", "label": "Photograph"}}}}


def test_customisation_additive_inheritance_is_non_mutating():
    shared = copy.deepcopy(FIELDS)
    override = {"country_of_origin": "CN", "requires_shipping": False,
        "customisation": {"proof_policy": "optional", "fields": {
            "age": {"type": "number", "label": "Age"}}}}
    before = copy.deepcopy((shared, override))
    result = merge_product_json(shared, override)
    validate_extensions(result)
    assert set(result["customisation"]["fields"]) == {"name", "photo", "age"}
    assert result["customisation"]["fields"]["name"]["required"] is True
    assert result["country_of_origin"] == "CN" and result["requires_shipping"] is False
    assert result["customisation"]["proof_policy"] == "optional"
    result["customisation"]["fields"]["photo"]["label"] = "Changed"
    assert (shared, override) == before


def test_inherited_disable_and_remove_are_rejected_and_empty_inherits():
    for definition in ({'remove': True}, {'enabled': False}):
        with pytest.raises(ValueError):
            merge_product_json(FIELDS, {'customisation': {'fields': {'name': definition}}})
    assert merge_product_json(FIELDS, {"customisation": {}})["customisation"] == FIELDS["customisation"]


@pytest.mark.parametrize("kind", ["text", "number", "multiline", "photo", "choice", "checkbox"])
def test_all_customisation_input_definitions(kind):
    field = {"type": kind, "label": "Input", "required": False}
    if kind == "choice": field["choices"] = ["First", "Second"]
    assert validate_product_info({"customisation": {"fields": {"input": field}}}, "override").valid
    validate_extensions({"customisation": {"fields": {"input": field}}})


@pytest.mark.parametrize("value", [
    {"requires_shipping": "true"}, {"country_of_origin": "UK" * 2},
    {"customisation": {"proof_policy": "sometimes"}},
    {"customisation": {"fields": {"Invalid ID": {"type": "text"}}}},
    {"customisation": {"fields": {"name": {"type": "variant"}}}},
    {"customisation": {"fields": {"name": {"remove": True, "label": "Bad"}}}},
    {"customisation": {"fields": {"age": {"minimum": 10, "maximum": 1}}}}])
def test_invalid_extensions_are_rejected_before_edit(value):
    assert not validate_product_info(value, "override").valid


def test_incomplete_new_field_blocks_resolved_contract():
    patch = {"customisation": {"fields": {"name": {"label": "Name"}}}}
    assert not validate_product_info(patch, "override").valid
    with pytest.raises(ValueError, match="type.*required"):
        validate_extensions(merge_product_json({}, patch))


def test_remove_tombstone_is_obsolete_in_every_authored_layer():
    patch = {"customisation": {"fields": {"name": {"remove": True}}}}
    assert not validate_product_info(patch, "override").valid
    with pytest.raises(ValueError, match="obsolete"):
        validate_extensions(patch)
    with pytest.raises(ValueError, match="obsolete"):
        merge_product_json(FIELDS, patch)


@pytest.mark.parametrize("name", ["Simple Collection", "Variable Collection", "Single Variable"])
def test_three_scanner_types_keep_rows_skus_markers_and_logs(tmp_path, name):
    fixtures = Path(__file__).parent / "fixtures" / "catalogue" / name
    results = []
    for suffix, extend in [("old", False), ("new", True)]:
        collection = tmp_path / suffix / name
        shutil.copytree(fixtures, collection)
        for folder in [collection, *collection.rglob('*')]:
            if folder.is_dir() and (folder.name.casefold() == 'parent'
                    or not any(p.is_dir() for p in folder.iterdir())):
                Image.new('RGB', (3, 3), 'red').save(folder / 'original.png')
        info = collection / "product_info.json"
        if extend:
            info.write_text(json.dumps({**json.loads(info.read_text()), **FIELDS}))
        output = tmp_path / (suffix + "-output")
        output.mkdir()
        logs = []
        rows = scan_collection(str(collection), "https://invalid.example/", str(output),
            log=lambda message, **kw: logs.append(str(message)))
        markers = {str(p.relative_to(collection)): json.loads(p.read_text())
            for p in collection.rglob(".scanned")}
        for marker in markers.values():
            marker.pop("timestamp", None)
            marker.pop("scan_date", None)
        indexes = {str(p.relative_to(collection)): json.loads(p.read_text())
            for p in collection.rglob("sku_index.json")}
        results.append((rows, markers, indexes))
        assert any("Scanning collection" in line for line in logs)
        assert scan_collection(str(collection), "https://invalid.example/", str(output),
            log=lambda *a, **k: None) == []
    assert results[0] == results[1]


def test_customisation_never_multiplies_variations():
    data = {**FIELDS, "attributes": {"Size": ["Small", "Large"]}, "variation_attributes": ["Size"]}
    assert build_variations(data) == [{"Size": "Small"}, {"Size": "Large"}]


def test_new_fields_target_update_reuses_identity_without_touching_sibling(tmp_path):
    collection = tmp_path / "Simple Collection"
    shutil.copytree(Path(__file__).parent / "fixtures" / "catalogue" / "Simple Collection", collection)
    product = collection / "Blue Token"
    sibling = collection / "Untouched"
    sibling.mkdir()
    for folder in (product, sibling):
        Image.new("RGB", (3, 3), "red").save(folder / "original.png")
    output = tmp_path / "output"
    output.mkdir()
    before = scan_collection(collection, "https://invalid.example/", output, log=lambda *a, **k: None)
    sibling_marker = (sibling / ".scanned").read_bytes()
    index = (collection / "sku_index.json").read_bytes()
    info = product / "product_info.json"
    info.write_text(json.dumps({**json.loads(info.read_text()), **FIELDS}))
    (product / ".update").touch()
    logs = []
    after = scan_collection(collection, "https://invalid.example/", output, update_csv=True,
        log=lambda message, **kw: logs.append(str(message)))
    assert len(after) == 1
    assert after[0]["SKU"] == next(row["SKU"] for row in before if "Blue Token" in row["Name"])
    assert not (product / ".update").exists()
    assert (sibling / ".scanned").read_bytes() == sibling_marker
    assert (collection / "sku_index.json").read_bytes() == index
    assert logs


def test_snapshot_shipping_customisation_and_origin_default(world, monkeypatch):
    product, _, shared = world
    authored = {**json.loads(shared.read_text()), **FIELDS, "weight": "50",
        "dimensions": {"length": "155", "width": "155", "height": "3"},
        "meta_title": "SEO", "tags": ["Birthday"], "storefront_collections": ["Cards"]}
    shared.write_text(json.dumps(authored))
    before = shared.read_bytes()
    snapshot = catalogue_snapshot(product)
    assert snapshot.data["shipping"]["weight_g"] == "50"
    assert snapshot.data["shipping"]["country_of_origin"] == "GB"
    assert snapshot.data["shipping"]["origin_source"] == "authored"
    assert snapshot.data["seo"]["title"] == "SEO"
    assert snapshot.data["customisation"]["proof_policy"] == "required"
    with pytest.raises(TypeError): snapshot.data["customisation"]["fields"]["name"]["label"] = "Changed"
    assert shared.read_bytes() == before
    monkeypatch.setenv("DEFAULT_COUNTRY_OF_ORIGIN", "GB")
    assert shipping_details({}, product)["country_of_origin"] == "GB"
    assert shipping_details({"country_of_origin": "CN"}, product)["country_of_origin"] == "CN"
    monkeypatch.delenv("DEFAULT_COUNTRY_OF_ORIGIN")
    assert shipping_details({}, product)["country_of_origin"] is None
    assert shipping_details({}, product)["requires_shipping"] is None
