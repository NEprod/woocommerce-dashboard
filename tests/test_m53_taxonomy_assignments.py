"""Local assignments and opt-in publication boundary; fictional filesystem data only."""
import copy
import json
from pathlib import Path

import pytest

from app import db
from app.models import Product, ProductAttribute, CatalogueOperation
from app.taxonomy_assignments import resolve, options, variation_drivers
from app.utils.json_utils import merge_product_json
from app.utils.scanner import build_variations, scan_collection
from app.utils.ingest import ingest_rows_to_db
from app.utils.operation_control import finish_catalogue_operation
from test_taxonomy_workspace import registry_app, entry, proposal, confirm, field
from test_onboarding_deployment import configured
from test_marker_recovery import _simple_collection, _variable_collection
from test_phase2_milestone5 import milestone5_app, milestone5_client
from test_phase3_woo_publish_preview import (
    preview_app, FakeWooClient, _eligible_create_confirmation,
    generate_publish_plan, PreviewError, start_publish_operation,
)


def test_registry_matches_and_legacy_values_remain_visible(registry_app):
    app, client, root, _ = registry_app
    before = (root / "registry.json").read_bytes()
    with app.app_context():
        result = resolve({"collection_type": "Simple", "categories": ["Cards > Notelets", "Old range"],
                          "attributes": {"Finish": ["Matte", "Speckled"], "Unknown": ["Original"]}, "variation_attributes": []})
        assert result["categories"][0]["definition"]["key"] == "notelets"
        assert result["categories"][1]["definition"] is None
        assert result["attributes"][0]["terms"][0]["definition"]["key"] == "matte"
        assert result["attributes"][0]["terms"][1]["definition"] is None
        assert result["attributes"][1]["definition"] is None
        assert not any(a["variation"] for a in result["attributes"])
    assert (root / "registry.json").read_bytes() == before
    assert client.get("/taxonomy/options").json["categories"][1]["value"] == "Cards > Notelets"
    assert app.test_client().get("/taxonomy/options").status_code in (302, 401)


@pytest.mark.parametrize("explicit, count", [(None, 4), (["Size"], 2), ([], 0)])
def test_legacy_and_explicit_variation_combinations(explicit, count):
    data = {"collection_type": "Variable Collection", "attributes": {"Size": ["Small", "Large"], "Occasion": ["Birthday", "Party"]}}
    if explicit is not None:
        data["variation_attributes"] = explicit
    before = copy.deepcopy(data)
    combinations = build_variations(data)
    assert len(combinations) == count
    if explicit:
        assert all(set(row) == {"Size"} for row in combinations)
    assert data == before


@pytest.mark.parametrize("drivers", [["Missing"], ["Size", "Size"], None, "Size"])
def test_invalid_explicit_drivers_rejected(drivers):
    with pytest.raises(ValueError):
        variation_drivers({"attributes": {"Size": ["Small"]}, "variation_attributes": drivers})


def test_sparse_inheritance_and_explicit_empty():
    shared = {"categories": ["Cards"], "attributes": {"Size": ["Small", "Large"]}, "variation_attributes": ["Size"]}
    resolved = merge_product_json(shared, {"title": "Example"})
    assert resolved["categories"] == shared["categories"]
    assert resolved["variation_attributes"] == ["Size"]
    resolved = merge_product_json(shared, {"categories": ["Legacy"], "variation_attributes": []})
    assert set(resolved["categories"]) == {"Cards", "Legacy"}
    assert resolved["variation_attributes"] == []
    assert merge_product_json(shared, {"attributes": {"Finish": ["Matte"]}})["attributes"] == {"Finish": ["Matte"]}


def test_explicit_driver_limit_prevents_truncated_child_selections():
    attributes = {f"Axis {i}": ["Option"] for i in range(6)}
    with pytest.raises(ValueError, match="at most five"):
        variation_drivers({"attributes": attributes, "variation_attributes": list(attributes)})
    assert len(variation_drivers({"attributes": attributes})) == 6  # no new legacy block
    assert variation_drivers({"attributes": attributes, "variation_attributes": ["Axis 5"]}) == ["Axis 5"]


@pytest.mark.parametrize("simple,explicit", [(True, False), (True, True), (False, True)])
def test_real_scan_preserves_sources_and_projects_local_attributes(configured, simple, explicit):
    app, _, root, catalogue, output = configured
    collection, _, _ = (_simple_collection if simple else _variable_collection)(catalogue.parent)
    source = collection / "product_info.json"
    data = json.loads(source.read_text())
    data["categories"] = ["Unregistered legacy category"]
    data["attributes"] = {"Size": ["Small", "Large"], "Occasion": ["Birthday", "Party"]}
    if explicit:
        data["variation_attributes"] = [] if simple else ["Size"]
    source.write_text(json.dumps(data))
    before = source.read_bytes(), (root / "registry.json").read_bytes()
    with app.app_context():
        rows = scan_collection(str(collection), "https://fictional.invalid/", str(output), log=lambda *a, **k: None)
        db.session.remove()
        result = ingest_rows_to_db(rows, log=lambda *a, **k: None)
        assert result["products_failed"] == 0, result
        product = Product.query.one()
        assert len(product.variations) == (0 if simple else 2)
        if explicit:
            assert {a.name for a in product.attributes} == {"Size", "Occasion"}
        if not simple:
            assert all({a.name for a in v.attributes} == {"Size"} for v in product.variations)
    assert (source.read_bytes(), (root / "registry.json").read_bytes()) == before


@pytest.mark.parametrize("drivers", [[], ["Size"]])
def test_actual_metadata_save_preserves_explicit_contract_and_sparse_override(milestone5_app, milestone5_client, monkeypatch, drivers):
    app, _, _, ids, shared, override = milestone5_app
    before = shared.read_bytes()
    monkeypatch.setattr("app.routes.start_scan", lambda *a, **k: finish_catalogue_operation(k["operation_id"], status="succeeded"))
    document = {"title": "Edited", "variation_attributes": drivers, "categories": ["Legacy value"]}
    response = milestone5_client.post("/edit_products/PRINT-001/save", json={"kind": "override", "replace": True, "data": document})
    assert response.status_code == 200, response.json
    assert json.loads(override.read_text()) == document
    assert shared.read_bytes() == before
    page = milestone5_client.get(f"/products/{ids['variable']}")
    assert b"Legacy value" in page.data and b"requires verified controlled taxonomy identities" in page.data
    editor = milestone5_client.get(f"/edit_products/{ids['variable']}/edit/override")
    assert editor.status_code == 200
    assert b"Refresh registry choices" in editor.data and b"Add new category to registry" in editor.data
    assert b"Add from taxonomy registry" in editor.data
    assert b'data-list-editor="variation_attributes"' not in editor.data
    assert b'id="image-attributes-title"' in editor.data


def test_invalid_resolved_driver_leaves_metadata_unchanged(milestone5_app, milestone5_client):
    _, _, _, _, _, override = milestone5_app
    before = override.read_bytes()
    response = milestone5_client.post("/edit_products/PRINT-001/save", json={"kind": "override", "replace": True, "data": {"variation_attributes": ["Absent"]}})
    assert response.status_code == 400
    assert override.read_bytes() == before


def test_advanced_unknown_assignments_preserved_and_not_imported(milestone5_app, milestone5_client, monkeypatch):
    app, _, _, ids, shared, override = milestone5_app
    before = shared.read_bytes()
    monkeypatch.setattr("app.routes.start_scan", lambda *a, **k: finish_catalogue_operation(k["operation_id"], status="succeeded"))
    document = {"categories": ["Old handmade range"], "attributes": {"Legacy Design": ["Train, old style"]}}
    response = milestone5_client.post("/edit_products/PRINT-001/save", json={"kind": "override", "replace": True, "data": document})
    assert response.status_code == 200
    assert json.loads(override.read_text()) == document
    assert shared.read_bytes() == before
    page = milestone5_client.get(f"/products/{ids['variable']}").get_data(as_text=True)
    assert "Legacy Design" in page and "Train, old style" in page and "Not in taxonomy registry" in page
    assert "variation_attributes" not in json.loads(override.read_text())


@pytest.mark.parametrize("kind", ["categories", "attributes", "terms"])
def test_explicit_reviewed_adoption_uses_existing_writer(registry_app, kind):
    app, client, root, data = registry_app
    before = (root / "registry.json").read_bytes()
    name = "New fictional value"
    query = f"?name={name}" + ("&attribute=finish" if kind == "terms" else "")
    editor = client.get(f"/taxonomy/edit/{kind}{query}")
    assert editor.status_code == 200 and name.encode() in editor.data
    proposed = copy.deepcopy(data)
    if kind == "categories":
        proposed[kind].append(entry("new-category", name=name, parent="cards"))
    elif kind == "attributes":
        proposed[kind].append(entry("new-attribute", name=name, navigation=True, visible_default=True, terms=[]))
    else:
        proposed["attributes"][0]["terms"].append(entry("new-term", name=name))
    review = proposal(client, proposed)
    assert (root / "registry.json").read_bytes() == before
    assert confirm(client, review).status_code == 200
    with app.app_context():
        choices = options()
        rows = choices["categories"] if kind == "categories" else choices["attributes"] if kind == "attributes" else choices["attributes"][0]["terms"]
        assert any(name in r["value"] for r in rows)
    # There is deliberately no product mutation in the registry save transaction.
    with app.app_context():
        assert Product.query.count() == 0


def test_grouped_taxonomy_and_scoped_terms(registry_app):
    _, client, _, _ = registry_app
    page = client.get("/taxonomy").get_data(as_text=True)
    assert page.index("<h3>Cards</h3>") < page.index("<h3>Notelets</h3>")
    assert "taxonomy-child" in page and "Cards &gt; Notelets" in page
    page = client.get("/taxonomy?kind=attributes").get_data(as_text=True)
    assert "<h3>Finish</h3>" in page and '<details open>' in page and 'taxonomy-term-list' in page


@pytest.mark.parametrize("failure", ["registry", "metadata"])
def test_registry_first_separate_metadata_save_failure_is_non_destructive(milestone5_app, milestone5_client, tmp_path, monkeypatch, failure):
    from app import taxonomy_workspace as service
    app, _, _, _, _, override = milestone5_app
    root = tmp_path / "taxonomy"
    root.mkdir()
    app.config["TAXONOMY_ROOT"] = str(root)
    data = service.empty_registry()
    (root / "registry.json").write_bytes(service.validate(data))
    before = override.read_bytes()
    editor = milestone5_client.get("/taxonomy/edit/categories?name=Legacy")
    review = milestone5_client.post("/taxonomy/edit/categories", data={"base": field(editor, "base"),
        "key": "legacy", "name": "Legacy", "slug": "legacy", "parent": "", "state": "active", "action": "save"})
    assert review.status_code == 200
    if failure == "registry":
        def denied(*a, **k):
            raise service.RegistryEditError("Read-only registry")
        monkeypatch.setattr(service, "save_reviewed", denied)
    result = confirm(milestone5_client, review)
    assert override.read_bytes() == before
    if failure == "registry":
        assert result.status_code == 409
        assert json.loads((root / "registry.json").read_bytes())["categories"] == []
    else:
        assert result.status_code == 200
        # Deliberate invalid assignment: no fake transaction across authored files.
        result = milestone5_client.post("/edit_products/PRINT-001/save", json={"kind": "override", "replace": True,
            "data": {"categories": ["Legacy"], "variation_attributes": ["Invalid"]}})
        assert result.status_code == 400
        assert override.read_bytes() == before
        assert json.loads((root / "registry.json").read_bytes())["categories"][0]["key"] == "legacy"


@pytest.mark.parametrize("drivers", [[], ["Size"]])
def test_new_contract_preview_blocks_before_any_woo_read(preview_app, drivers):
    from app.models import Settings
    with preview_app.app_context():
        root = Path(Settings.query.first().product_folder)
        source = root / "Preview Cards" / "product_info.json"
        source.parent.mkdir(parents=True)
        source.write_text(json.dumps({"attributes": {"Size": ["A5"]}, "variation_attributes": drivers}))
        client = FakeWooClient()
        with pytest.raises(PreviewError, match="Taxonomy registry is not ready"):
            generate_publish_plan({"kind": "product", "product_id": 4}, client=client)
        assert client.methods == []
    from test_phase3_woo_publish_preview import _client
    response = _client(preview_app).post("/woocommerce/preview/generate", data={"scope_kind": "product", "product_id": "4"}, follow_redirects=True)
    assert b"Taxonomy registry is not ready" in response.data


def test_execution_guard_rechecks_new_opt_in_after_legacy_confirmation(preview_app, monkeypatch):
    from app.models import Settings
    publisher, _, confirmation = _eligible_create_confirmation(preview_app, monkeypatch)
    with preview_app.app_context():
        source = Path(Settings.query.first().product_folder) / "Preview Cards" / "product_info.json"
        source.parent.mkdir(parents=True)
        source.write_text(json.dumps({"variation_attributes": []}))
        before = list(publisher.methods)
        operation_id = start_publish_operation(confirmation, client=publisher, run_async=False)
        assert publisher.methods == before
        operation = db.session.get(CatalogueOperation, operation_id)
        assert operation.status == "failed"
        assert "Explicit taxonomy assignments or verified identities changed" in operation.scope
