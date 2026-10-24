"""Pure editor foundations; no live catalogue, Shopify or Discord activity."""
import copy
import json
from pathlib import Path

import pytest

from app.metadata_edit import MetadataEditConflict, prepare_edit, source_digest
from app.utils.json_utils import merge_product_json


def prepare(source, patch, **kwargs):
    text = json.dumps(source)
    return prepare_edit(text, patch, expected_digest=source_digest(text),
                        kind=kwargs.pop("kind", "override"), **kwargs)


@pytest.mark.parametrize("fixture", ["Simple Collection", "Variable Collection", "Single Variable"])
def test_all_layouts_reuse_existing_schema_without_reallocating_identity(fixture):
    source = json.loads((Path(__file__).parent / "fixtures/catalogue" / fixture / "product_info.json").read_text())
    before = copy.deepcopy(source)
    edit = prepare(source, {"description": "Reviewed local copy"}, kind="collection")
    assert edit.validation.valid and edit.changed
    assert edit.data["sku_prefix"] == source["sku_prefix"]
    assert edit.data["collection_type"] == source["collection_type"]
    for key in ("attributes", "image_attributes", "variation_modifiers"):
        assert edit.data.get(key) == source.get(key)
    assert "sku" not in edit.data and source == before


def test_patch_preserves_unknown_internal_legacy_and_explicit_empty_values():
    source = {"future": {"opaque": [], "empty": "", "nullable": None},
              "upsell_ids": ["OLD-0001"], "tags": [], "live": False,
              "internal_notes": {"path": "designs/card.svg"}, "dimensions": {"width": 12}}
    patch = {"dimensions": {"height": 3}}
    edit = prepare(source, patch)
    assert edit.validation.valid
    assert edit.data == {**source, "dimensions": {"width": 12, "height": 3}}
    assert patch == {"dimensions": {"height": 3}}
    assert source["dimensions"] == {"width": 12}
    value = edit.data
    value["future"]["opaque"].append("not persisted")
    assert edit.data["future"]["opaque"] == []


def test_explicit_override_removal_restores_established_inheritance():
    edit = prepare({"weight": 55, "customisation": {"fields": {"name": {"label": "My label"}}}}, {},
                   remove_paths=[("weight",), ("customisation", "fields", "name")])
    assert edit.validation.valid and "weight" not in edit.data
    shared = {"weight": 50, "customisation": {"fields": {"name": {"type": "text", "label": "Dog's Name"}}}}
    resolved = merge_product_json(shared, edit.data)
    assert resolved["weight"] == 50
    assert resolved["customisation"]["fields"]["name"]["label"] == "Dog's Name"


def test_obsolete_customisation_tombstone_is_rejected_before_save():
    source = {"customisation": {"fields": {"name": {"type": "text", "label": "Name"}}}}
    edit = prepare(source, {"customisation": {"fields": {"name": {"remove": True}}}},
                   remove_paths=[("customisation", "fields", "name", "type"),
                                 ("customisation", "fields", "name", "label")])
    assert not edit.validation.valid
    assert 'customisation.fields.name.remove is obsolete' in edit.validation.errors[0].message
    with pytest.raises(ValueError, match='obsolete'):
        merge_product_json(source, edit.data)


def test_stale_source_is_rejected_even_if_only_formatting_changed():
    with pytest.raises(MetadataEditConflict, match="reload"):
        prepare_edit('{"title": "New"}', {"title": "Edit"},
                     expected_digest=source_digest('{"title":"New"}'), kind="override")


def test_noop_does_not_require_scan_and_invalid_draft_has_errors():
    assert not prepare({"live": False, "tags": []}, {}).changed
    edit = prepare({"price": "5.00"}, {"requires_shipping": "yes"})
    assert not edit.validation.valid
    assert edit.validation.errors[0].path == "$.requires_shipping"


@pytest.mark.parametrize("path", [(), "weight", ("dimensions", 0), ("attributes", "Size", "value")])
def test_ambiguous_removal_is_rejected(path):
    with pytest.raises(ValueError, match="Removal paths"):
        prepare({"attributes": {"Size": ["Large"]}}, {}, remove_paths=[path])


@pytest.mark.parametrize("text", ['[]', '{"x":1,"x":2}', '{"x":NaN}'])
def test_unsafe_source_is_rejected(text):
    with pytest.raises(ValueError):
        prepare_edit(text, {}, expected_digest=source_digest(text), kind="override")


def test_document_limit_and_invalid_kind():
    with pytest.raises(ValueError, match="size"):
        source_digest("x" * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="size"):
        prepare({}, {"future": "x" * (1024 * 1024)})
    with pytest.raises(ValueError, match="collection or override"):
        prepare({}, {}, kind="product")


def test_local_date_definition_now_supported_without_storefront_side_effects():
    edit = prepare({"customisation": {"fields": {}}},
                   {"customisation": {"fields": {"date": {"type": "date", "label": "Date"}}}})
    assert edit.validation.valid  # M3A.9.4 supports local date definitions only
    assert edit.data['customisation']['fields']['date']=={'type':'date','label':'Date'}
