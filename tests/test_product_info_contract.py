import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import create_app, db
from app.models import Product, ProductAsset, Settings
from app.product_info import (
    CANONICAL_FIELDS,
    EXAMPLE_NAMES,
    FIELD_INVENTORY,
    SCHEMA_NAMES,
    TEMPLATE_NAMES,
    load_example,
    load_schema,
    load_template,
    validate_product_info,
)
from app.utils.json_utils import merge_product_json
from config import Config


def _codes(issues):
    return {issue.code for issue in issues}


def test_schemas_are_valid_and_accept_all_intended_examples():
    from jsonschema.validators import validator_for

    for name in SCHEMA_NAMES:
        schema = load_schema(name)
        validator_for(schema).check_schema(schema)

    for name in EXAMPLE_NAMES:
        kind = "override" if name == "override" else "collection"
        result = validate_product_info(load_example(name), kind)
        assert result.valid, (name, result.errors)


def test_collection_requires_type_and_prefix_while_override_is_partial():
    missing = validate_product_info({"title": "Fictional item"}, "collection")
    assert not missing.valid
    assert {issue.path for issue in missing.errors} == {
        "$.collection_type",
        "$.sku_prefix",
    }

    minimal = load_template("minimal-override")
    assert minimal == {}
    assert validate_product_info(minimal, "override").valid


def test_existing_inheritance_and_partial_override_remain_valid():
    shared = load_example("variable-collection")
    override = {"title": "Fictional override", "tags": ["override"]}
    assert validate_product_info(override, "override").valid
    merged = merge_product_json(shared, override, path="Fictional Folder")
    assert validate_product_info(merged, "collection").valid
    assert merged["title"].startswith("Fictional override - ")
    assert set(merged["tags"]) == {"fictional", "variable", "override"}


def test_alias_unknown_ignored_and_attribute_limit_warnings_are_explicit():
    result = validate_product_info(
        {
            "title": "Partial override",
            "upsell_ids": ["FIC-UP-1"],
            "cross_sell_ids": ["FIC-CROSS-1"],
            "shipping_class": "fictional-class",
            "grouped_ids": ["FIC-GROUP-1"],
            "future_field": True,
            "attributes": {f"Option {index}": ["A"] for index in range(6)},
        },
        "override",
    )
    assert result.valid
    assert {
        "accepted_alias",
        "currently_ignored",
        "editor_only",
        "unknown_field",
        "woo_attribute_limit",
    } <= _codes(result.warnings)

    unknown_type = validate_product_info(
        {"collection_type": "Future Fictional Type", "sku_prefix": "FIC-U-"},
        "collection",
    )
    assert unknown_type.valid
    assert "unknown_collection_type" in _codes(unknown_type.warnings)


@pytest.mark.parametrize(
    "data, expected_path",
    [
        ({"attributes": ["Size"]}, "$.attributes"),
        ({"attributes": {"Size": "Large"}}, "$.attributes.Size"),
        ({"image_attributes": {"Size": True}}, "$.image_attributes"),
        ({"variation_modifiers": []}, "$.variation_modifiers"),
        (
            {"variation_modifiers": {"Size=Large": "12.00"}},
            "$.variation_modifiers.Size=Large",
        ),
        (
            {
                "variation_modifiers": {
                    "Size=Large": {"dimensions": [10, 20, 30]}
                }
            },
            "$.variation_modifiers.Size=Large.dimensions",
        ),
    ],
)
def test_unsafe_attribute_and_modifier_structures_are_blocking(data, expected_path):
    result = validate_product_info(data, "override")
    assert not result.valid
    assert expected_path in {issue.path for issue in result.errors}


def test_complete_example_and_templates_cover_the_contract():
    complete = load_example("complete")
    assert CANONICAL_FIELDS <= complete.keys()
    assert {entry["key"] for entry in FIELD_INVENTORY} <= complete.keys()
    assert {
        "minimal-collection",
        "minimal-override",
        "complete",
        "simple",
        "variable-collection",
        "single-variable",
    } == set(TEMPLATE_NAMES)
    for name in TEMPLATE_NAMES:
        template = load_template(name)
        kind = "override" if name == "minimal-override" else "collection"
        assert validate_product_info(template, kind).valid, name


def test_every_inventory_field_has_the_required_contract_classification():
    required_facets = {
        "key",
        "aliases",
        "type",
        "units",
        "required_collection",
        "required_override",
        "collection_allowed",
        "override_allowed",
        "inheritance",
        "merge",
        "parent_effect",
        "variation_effect",
        "woo_field",
        "sqlite_destination",
        "classification",
        "implementation_status",
        "example",
        "warning_error",
    }
    classifications = {
        "canonical and active",
        "accepted alias",
        "supported but currently ignored",
        "editor-only",
        "Woo CSV-only",
        "deprecated/legacy",
        "planned but not operational",
    }
    assert len({field["key"] for field in FIELD_INVENTORY}) == len(FIELD_INVENTORY)
    for field in FIELD_INVENTORY:
        assert required_facets <= field.keys(), field["key"]
        assert field["classification"] in classifications
    inventory_keys = {field["key"] for field in FIELD_INVENTORY}
    for schema_name in SCHEMA_NAMES:
        assert inventory_keys <= load_schema(schema_name)["properties"].keys()


@pytest.fixture
def editor_app(tmp_path):
    instance = tmp_path / "instance"
    instance.mkdir()
    database = instance / "site.db"
    catalogue = tmp_path / "catalogue"
    output = tmp_path / "output"
    product_folder = catalogue / "Fictional Collection" / "Fictional Product"
    product_folder.mkdir(parents=True)
    output.mkdir()
    target = product_folder / "product_info.json"
    target.write_text(
        json.dumps({"title": "Original override", "price": "10.00"}, indent=2),
        encoding="utf-8",
    )

    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{database}"
    try:
        app = create_app()
        app.config.update(TESTING=True, LOGIN_DISABLED=True, WTF_CSRF_ENABLED=False)
        with app.app_context():
            product = Product(sku="FIC-EDITOR-0001", title="Fictional Product")
            db.session.add_all(
                [
                    product,
                    Settings(
                        product_folder=str(catalogue),
                        output_folder=str(output),
                        url_prefix="https://invalid.example/",
                    ),
                ]
            )
            db.session.flush()
            db.session.add(
                ProductAsset(
                    product_id=product.id,
                    path=str(target),
                    kind="info",
                    label="override",
                )
            )
            db.session.commit()
        yield app, target
    finally:
        with app.app_context():
            db.session.remove()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


@pytest.mark.parametrize(
    "body, content_type",
    [
        ('{"kind":"override","data":', "application/json"),
        (json.dumps({"kind": "override", "data": []}), "application/json"),
        (
            json.dumps(
                {"kind": "override", "data": {"attributes": {"Size": "A3"}}}
            ),
            "application/json",
        ),
        (
            json.dumps(
                {
                    "kind": "override",
                    "data": {"variation_modifiers": {"Size=A3": []}},
                }
            ),
            "application/json",
        ),
    ],
)
def test_invalid_editor_save_has_no_filesystem_or_scan_side_effects(
    editor_app, monkeypatch, body, content_type
):
    app, target = editor_app
    before = target.read_bytes()
    monkeypatch.setattr(
        "app.routes.acquire_catalogue_operation",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("invalid save acquired operation lock")
        ),
    )
    monkeypatch.setattr(
        "app.routes.start_scan",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("invalid save started scan")
        ),
    )

    response = app.test_client().post(
        "/edit_products/FIC-EDITOR-0001/save",
        data=body,
        content_type=content_type,
    )

    assert response.status_code == 400
    payload = response.get_json()
    if body.endswith(":"):
        assert payload["submitted_content"] == body
    else:
        assert payload["submitted_data"] == json.loads(body)["data"]
    assert target.read_bytes() == before
    assert not list(target.parent.glob("product_info.json.bak.*"))
    assert not (target.parent / ".update").exists()


def test_valid_editor_save_retains_backup_marker_scan_and_warning_behavior(
    editor_app, monkeypatch
):
    app, target = editor_app
    calls = []
    monkeypatch.setattr(
        "app.routes.acquire_catalogue_operation",
        lambda *args, **kwargs: SimpleNamespace(id="fixture-operation"),
    )
    monkeypatch.setattr(
        "app.routes.start_scan",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    response = app.test_client().post(
        "/edit_products/FIC-EDITOR-0001/save",
        json={
            "kind": "override",
            "data": {
                "title": "Updated override",
                "upsell_ids": ["FIC-UP-1"],
                "future_field": "preserved",
            },
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert {warning["code"] for warning in payload["warnings"]} >= {
        "accepted_alias",
        "unknown_field",
    }
    assert json.loads(target.read_text(encoding="utf-8"))["future_field"] == "preserved"
    assert len(list(target.parent.glob("product_info.json.bak.*"))) == 1
    assert (target.parent / ".update").exists()
    assert calls[0][1]["scan_mode"] == "update"


def test_in_app_reference_and_templates_are_available(editor_app):
    app, _target = editor_app
    page = app.test_client().get("/metadata-reference")
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    assert "product_info.json Metadata Reference" in text
    assert "Inherited / override behavior" in text
    assert "variation modifier sale_price" in text

    template = app.test_client().get("/api/metadata-reference/template/minimal-override")
    assert template.status_code == 200
    assert template.get_json() == {"name": "minimal-override", "data": {}}

    schema = app.test_client().get("/api/metadata-reference/schema/collection")
    assert schema.status_code == 200
    assert schema.get_json()["required"] == ["collection_type", "sku_prefix"]

    editor = app.test_client().get("/edit_products/1/edit/override")
    assert editor.status_code == 200
    editor_text = editor.get_data(as_text=True)
    assert "Metadata reference" in editor_text
    assert 'id="templateSelect"' in editor_text
    assert 'id="validationMessages"' in editor_text


def test_runtime_resources_are_in_image_context_and_tests_are_not_copied():
    root = Path(__file__).parents[1]
    assert (root / "app/resources/product_info/schemas/collection.schema.json").is_file()
    assert (root / "app/resources/product_info/schemas/override.schema.json").is_file()
    assert (root / "app/resources/product_info/field_inventory.json").is_file()
    assert "COPY --chown=app:app app ./app" in (root / "Dockerfile").read_text()
    assert "COPY --chown=app:app tests" not in (root / "Dockerfile").read_text()
