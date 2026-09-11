"""Verified registry integration through the existing Preview/publisher, fictional Woo."""
import copy
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app import db
from app.models import (Product, Settings, Variation, VariationAttribute, WooTaxonomyIdentity,
                        WooProductIdentity, WooVariationIdentity, CatalogueOperation)
from app.taxonomy_workspace import empty_registry, validate
from app.woo_taxonomy_sync import persist
from app.woo_product_taxonomy import contract
from app.woo_publish_preview import generate_publish_plan, PreviewError, plan_is_stale, store_identity
from app.woo_controlled_publish import prepare_publish_confirmation, start_publish_operation, _verification_differences
from test_taxonomy_workspace import entry
from test_phase3_woo_publish_preview import preview_app, FakePublisherClient


def world(app, monkeypatch, tmp_path, *, variable=False, driver_spec=None):
    monkeypatch.setattr("app.woo_publish_preview.notify_woo_publish_preview_completed", lambda *a, **k: None)
    monkeypatch.setattr("app.woo_controlled_publish.notify_woo_publish_completed", lambda *a, **k: (True, "sent"))
    root = tmp_path / "registry"; root.mkdir()
    app.config["TAXONOMY_ROOT"] = str(root)
    data = empty_registry()
    data["categories"] = [entry("cards", parent=None)]
    data["storefront_collections"] = [entry("pixel", name="Pixel")]
    driver_spec = driver_spec or [("Size", ["Small", "Large"]), ("Finish", ["Matt", "Gloss"])]
    data["attributes"] = [entry(name.lower().replace(" ", "-"), name=name, navigation=True, visible_default=True,
        terms=[entry(("term-" if v[0].isdigit() else "") + v.lower().replace(" ", "-"), name=v) for v in values]) for name, values in
        [("Occasion", ["Birthday"]), *driver_spec]]
    (root / "registry.json").write_bytes(validate(data))
    publisher = FakePublisherClient()
    pid = 4 if variable else 3
    p = db.session.get(Product, pid); p.tags.clear()
    document = {"collection_type": "Variable Collection" if variable else "Simple", "categories": ["Cards"],
        "storefront_collections": ["Pixel"], "attributes": {a["name"]: [t["name"] for t in a["terms"]] for a in data["attributes"]},
        "variation_attributes": [name for name, _ in driver_spec] if variable else []}
    source = Path(Settings.query.first().product_folder) / "Preview Cards/product_info.json"
    source.parent.mkdir(parents=True, exist_ok=True); source.write_text(json.dumps(document))
    if variable:
        child = p.variations[0]
        child.attributes.clear()
        for i, (size, finish) in enumerate([(s, f) for s in driver_spec[0][1] for f in driver_spec[1][1]]):
            row = child if i == 0 else Variation(product_id=pid, sku=f"VARIABLE-{i}", source_identity=f"child/{i}", catalogue_status="active", regular_price=21+i)
            if i: db.session.add(row)
            row.attributes.extend([VariationAttribute(name=driver_spec[0][0], value=size), VariationAttribute(name=driver_spec[1][0], value=finish)])
    db.session.commit()
    remote = {}
    number = 90
    for kind in ("categories", "storefront_collections", "attributes"):
        for definition in data[kind]:
            number += 1
            observed = {"id": number, "name": definition["name"], "slug": definition["slug"]}
            if kind == "categories": observed["parent"] = 0
            route = f"products/{'brands' if kind == 'storefront_collections' else kind}/{number}"
            remote[route] = observed
            persist({"kind": kind, "scope": "", "key": definition["key"], "local": definition, "remote_scope": 0}, store_identity()["key"], observed)
            if kind == "attributes":
                aid = number
                for term in definition["terms"]:
                    number += 1
                    observed = {"id": number, "name": term["name"], "slug": term["slug"]}
                    remote[f"products/attributes/{aid}/terms/{number}"] = observed
                    persist({"kind": "terms", "scope": definition["key"], "key": term["key"], "local": term, "remote_scope": aid}, store_identity()["key"], observed)
    original = publisher.request_json
    def request(method, url, **kwargs):
        route = urlsplit(url).path.split("/wc/v3/")[-1]
        if route in remote:
            assert method == "GET", "Explicit product publishing cannot mutate definitions"
            publisher.methods.append(method)
            return copy.deepcopy(remote[route]), object()
        payload, response = original(method, url, **kwargs)
        if method == "GET" and route.startswith("products"):
            payload = copy.deepcopy(payload)
            for row in payload if isinstance(payload, list) else [payload]:
                if not isinstance(row, dict): continue
                for attribute in row.get("attributes", []):
                    if "options" in attribute:
                        attribute["options"] = list(reversed(attribute["options"]))
                        attribute["slug"] = "pa_" + attribute["name"].lower()
                    else:
                        definition = remote.get(f"products/attributes/{attribute.get('id')}")
                        if definition: attribute["name"] = definition["name"]
                for brand in row.get("brands", []): brand["name"] = "Woo decorated brand"
        return payload, response
    publisher.request_json = request
    return pid, publisher, source, root, document, remote


@pytest.mark.parametrize("variable", [False, True])
def test_publish_verified_assignments_and_fresh_no_change(preview_app, monkeypatch, tmp_path, variable):
    with preview_app.app_context():
        pid, woo, source, root, _, _ = world(preview_app, monkeypatch, tmp_path, variable=variable)
        before = source.read_bytes(), (root / "registry.json").read_bytes()
        skus = [v.sku for v in db.session.get(Product, pid).variations]
        plan = generate_publish_plan({"kind": "product", "product_id": pid}, client=woo)
        item = plan["products"][0]
        assert item["action"] == "create", item["blockers"]
        assert item["payload"]["categories"] == [{"id": 91}]
        assert item["payload"]["brands"] == [{"id": 92}]
        assert {a["name"]: a["variation"] for a in item["payload"]["attributes"]} == {"Occasion": False, "Size": variable, "Finish": variable}
        assert all(a["id"] > 0 for a in item["payload"]["attributes"])
        assert len(item["variations"]) == (4 if variable else 0)
        confirmation = prepare_publish_confirmation(plan["operation_id"], plan["digest"], [pid], client=woo)
        op = start_publish_operation(confirmation, client=woo, run_async=False)
        operation = db.session.get(CatalogueOperation, op)
        assert operation.status == "succeeded", operation.scope
        assert WooProductIdentity.query.filter_by(product_id=pid).count() == 1
        assert WooVariationIdentity.query.count() == (4 if variable else 0)
        assert [v.sku for v in db.session.get(Product, pid).variations] == skus
        assert all("/categories" not in path and "/attributes" not in path and "/brands" not in path for _, path, _ in woo.writes)
        assert not any(method == "DELETE" for method in woo.methods)
        fresh = generate_publish_plan({"kind": "product", "product_id": pid}, client=woo)
        assert fresh["products"][0]["action"] == "no_change", fresh["products"][0]
        assert (source.read_bytes(), (root / "registry.json").read_bytes()) == before


@pytest.mark.parametrize("kind", ["categories", "storefront_collections", "attributes", "terms"])
def test_unverified_identity_blocks_before_woo(preview_app, monkeypatch, tmp_path, kind):
    with preview_app.app_context():
        pid, woo, *_ = world(preview_app, monkeypatch, tmp_path)
        WooTaxonomyIdentity.query.filter_by(kind=kind).first().state = "uncertain"
        db.session.commit()
        with pytest.raises(PreviewError, match="Taxonomy Sync"):
            generate_publish_plan({"kind": "product", "product_id": pid}, client=woo)
        assert not woo.methods and not woo.writes


@pytest.mark.parametrize("change", ["scope", "store", "registry", "unknown", "empty", "projection"])
def test_contract_safety(preview_app, monkeypatch, tmp_path, change):
    with preview_app.app_context():
        pid, woo, source, root, document, _ = world(preview_app, monkeypatch, tmp_path, variable=True)
        if change == "scope": WooTaxonomyIdentity.query.filter_by(kind="terms").first().remote_scope = 999
        elif change == "store": WooTaxonomyIdentity.query.filter_by(kind="categories").first().store_key = "another-store"
        elif change == "registry":
            data = json.loads((root / "registry.json").read_text()); data["categories"][0]["slug"] = "different"
            (root / "registry.json").write_bytes(validate(data))
        elif change == "unknown": document["attributes"]["Occasion"] = ["Unregistered"]
        elif change == "empty": document["variation_attributes"] = []
        else: db.session.get(Product, pid).variations[0].attributes[0].value = "Wrong"
        source.write_text(json.dumps(document)); db.session.commit()
        with pytest.raises(PreviewError): generate_publish_plan({"kind": "product", "product_id": pid}, client=woo)
        assert not woo.methods


def test_identity_change_invalidates_preview(preview_app, monkeypatch, tmp_path):
    with preview_app.app_context():
        pid, woo, *_ = world(preview_app, monkeypatch, tmp_path)
        plan = generate_publish_plan({"kind": "product", "product_id": pid}, client=woo)
        assert not plan_is_stale(plan)
        WooTaxonomyIdentity.query.filter_by(kind="categories").first().state = "uncertain"
        db.session.commit()
        assert plan_is_stale(plan)


def test_remote_identity_drift_blocks_without_repair(preview_app, monkeypatch, tmp_path):
    with preview_app.app_context():
        pid, woo, _, _, _, remote = world(preview_app, monkeypatch, tmp_path)
        remote["products/categories/91"]["parent"] = 998
        with pytest.raises(PreviewError, match="Taxonomy Sync"):
            generate_publish_plan({"kind": "product", "product_id": pid}, client=woo)
        assert not woo.writes


@pytest.mark.parametrize("field", ["brands", "categories", "attributes"])
def test_semantic_verification_remains_strict(field):
    expected = {"type": "simple", "categories": [{"id": 1}], "brands": [{"id": 2}], "attributes": [
        {"id": 3, "name": "Size", "options": ["Small", "Large"], "visible": True, "variation": False}]}
    remote = copy.deepcopy(expected)
    remote["attributes"][0].update(slug="pa_size", position=0, options=["Large", "Small"])
    assert not _verification_differences(expected, remote)
    if field == "attributes": remote[field][0]["variation"] = True
    else: remote[field][0]["id"] = 999
    assert field in _verification_differences(expected, remote)


@pytest.mark.parametrize("existing", [False, True])
def test_explicit_tags_remain_permissive_reused_or_created(preview_app, monkeypatch, tmp_path, existing):
    from app.models import Tag
    with preview_app.app_context():
        pid, woo, source, _, document, _ = world(preview_app, monkeypatch, tmp_path)
        document["tags"] = ["Loose label"]; source.write_text(json.dumps(document))
        product = db.session.get(Product, pid)
        product.tags.append(Tag(name="Loose label", slug="loose-label"))
        db.session.commit()
        woo.taxonomy["tags"] = [{"id": 71, "name": "Loose label", "slug": "loose-label"}] if existing else []
        plan = generate_publish_plan({"kind": "product", "product_id": pid}, client=woo)
        assert plan["products"][0]["action"] == "create"
        confirmation = prepare_publish_confirmation(plan["operation_id"], plan["digest"], [pid], client=woo)
        op = start_publish_operation(confirmation, client=woo, run_async=False)
        assert db.session.get(CatalogueOperation, op).status == "succeeded"
        creates = [row for row in woo.writes if row[1].endswith("products/tags")]
        assert len(creates) == (0 if existing else 1)
        assert next(iter(woo.products.values()))["tags"]
        assert json.loads(source.read_text())["tags"] == ["Loose label"]


def test_existing_parent_children_update_without_recreation(preview_app, monkeypatch, tmp_path):
    with preview_app.app_context():
        pid, woo, *_ = world(preview_app, monkeypatch, tmp_path, variable=True)
        plan = generate_publish_plan({"kind": "product", "product_id": pid}, client=woo)
        confirmation = prepare_publish_confirmation(plan["operation_id"], plan["digest"], [pid], client=woo)
        op = start_publish_operation(confirmation, client=woo, run_async=False)
        assert db.session.get(CatalogueOperation, op).status == "succeeded"
        identity = WooProductIdentity.query.filter_by(product_id=pid).one()
        child_ids = [(v.variation_id, v.woo_variation_id) for v in WooVariationIdentity.query.all()]
        # Model the pre-adoption M4 parent representation, retaining trusted children.
        remote = woo.products[identity.woo_product_id]
        remote["brands"] = []
        remote["attributes"][0]["variation"] = True
        identity.last_published_digest = None; identity.last_remote_digest = None
        db.session.commit()
        plan = generate_publish_plan({"kind": "product", "product_id": pid}, client=woo)
        assert plan["products"][0]["action"] == "update"
        before = len(woo.writes)
        confirmation = prepare_publish_confirmation(plan["operation_id"], plan["digest"], [pid], client=woo)
        op = start_publish_operation(confirmation, client=woo, run_async=False)
        assert db.session.get(CatalogueOperation, op).status == "succeeded"
        assert all(method != "POST" for method, _, _ in woo.writes[before:])
        assert [(v.variation_id, v.woo_variation_id) for v in WooVariationIdentity.query.all()] == child_ids
        assert generate_publish_plan({"kind": "product", "product_id": pid}, client=woo)["products"][0]["action"] == "no_change"


def test_sparse_override_inherits_controlled_assignments(preview_app, monkeypatch, tmp_path):
    with preview_app.app_context():
        pid, woo, source, _, _, _ = world(preview_app, monkeypatch, tmp_path)
        override = source.parent / "Link/product_info.json"
        override.parent.mkdir(); override.write_text('{"title": "Sparse title"}')
        before = source.read_bytes(), override.read_bytes()
        plan = generate_publish_plan({"kind": "product", "product_id": pid}, client=woo)
        assert plan["products"][0]["payload"]["brands"] == [{"id": 92}]
        assert len(plan["products"][0]["payload"]["attributes"]) == 3
        assert (source.read_bytes(), override.read_bytes()) == before


@pytest.mark.parametrize("field,value", [("categories", ["Unknown"]), ("storefront_collections", ["Unknown"]),
                                          ("attributes", {"Unknown": ["Term"]}), ("categories", "Cards")])
def test_unknown_or_invalid_assignments_do_not_drop_silently(preview_app, monkeypatch, tmp_path, field, value):
    with preview_app.app_context():
        pid, woo, source, _, document, _ = world(preview_app, monkeypatch, tmp_path)
        document[field] = value; source.write_text(json.dumps(document))
        with pytest.raises(PreviewError): generate_publish_plan({"kind": "product", "product_id": pid}, client=woo)
        assert not woo.methods


def test_tag_create_failure_keeps_existing_failure_contract(preview_app, monkeypatch, tmp_path):
    from app.models import Tag
    from app.woocommerce_connection import WooConnectionError
    with preview_app.app_context():
        pid, woo, *_ = world(preview_app, monkeypatch, tmp_path)
        product = db.session.get(Product, pid)
        product.tags.append(Tag(name="Loose", slug="loose")); db.session.commit()
        plan = generate_publish_plan({"kind": "product", "product_id": pid}, client=woo)
        confirmation = prepare_publish_confirmation(plan["operation_id"], plan["digest"], [pid], client=woo)
        original = woo.request_json
        attempts = []
        def request(method, url, **kwargs):
            if method == "POST" and url.endswith("products/tags"):
                attempts.append(url)
                raise WooConnectionError("forbidden", "Tag creation refused", status_code=403)
            return original(method, url, **kwargs)
        woo.request_json = request
        op = start_publish_operation(confirmation, client=woo, run_async=False)
        result = db.session.get(CatalogueOperation, op)
        assert result.status == "failed"
        assert len(attempts) == 1 and not woo.products
        assert "Required taxonomy could not be resolved" in result.scope


@pytest.mark.parametrize("taxonomy_state", ["unknown", "unverified", "ready", "wrong_child"])
def test_fourteen_existing_children_taxonomy_prerequisite(preview_app, monkeypatch, tmp_path, taxonomy_state):
    with preview_app.app_context():
        specs = [("Build Type", ["Flat 3 Layer", "3d 3 Layer"]),
                 ("Design Style", ["Farm", "Fireplace", "Horse", "Street", "Town", "Train", "Trees"])]
        pid, woo, source, root, document, _ = world(
            preview_app, monkeypatch, tmp_path, variable=True, driver_spec=specs)
        product = db.session.get(Product, pid)
        skus = [v.sku for v in product.variations]
        assert len(skus) == 14
        if taxonomy_state == "unknown":
            data = json.loads((root / "registry.json").read_text())
            data["attributes"] = data["attributes"][:1]
            (root / "registry.json").write_bytes(validate(data))
        elif taxonomy_state == "unverified":
            WooTaxonomyIdentity.query.filter_by(kind="attributes", local_key="build-type").first().state = "uncertain"
        elif taxonomy_state == "wrong_child":
            product.variations[0].attributes[0].value = "Not authored"
        db.session.commit()
        before = source.read_bytes(), (root / "registry.json").read_bytes()
        value = contract(product, store_identity()["key"])
        if taxonomy_state in ("unknown", "unverified"):
            reasons = " ".join(value["blockers"])
            assert "Build Type" in reasons and "Taxonomy Sync" in reasons
            assert "Update scan" not in reasons and "does not match" not in reasons
            with pytest.raises(PreviewError):
                generate_publish_plan({"kind": "product", "product_id": pid}, client=woo)
            assert not woo.methods and not woo.writes
        elif taxonomy_state == "wrong_child":
            assert any("unrecognised selection" in reason for reason in value["blockers"])
        else:
            assert not value["blockers"] and len(value["children"]) == 14
            plan = generate_publish_plan({"kind": "product", "product_id": pid}, client=woo)
            confirmation = prepare_publish_confirmation(plan["operation_id"], plan["digest"], [pid], client=woo)
            operation = start_publish_operation(confirmation, client=woo, run_async=False)
            assert db.session.get(CatalogueOperation, operation).status == "succeeded"
            assert WooVariationIdentity.query.count() == 14
            fresh = generate_publish_plan({"kind": "product", "product_id": pid}, client=woo)
            assert fresh["products"][0]["action"] == "no_change"
            assert all("/attributes" not in path for _, path, _ in woo.writes)
        assert [v.sku for v in product.variations] == skus
        assert (source.read_bytes(), (root / "registry.json").read_bytes()) == before


def test_options_preserve_read_only_registry_status(preview_app, monkeypatch, tmp_path):
    from app.taxonomy_registry import load_configured_registry, RegistryResult
    from app.taxonomy_assignments import options
    with preview_app.app_context():
        world(preview_app, monkeypatch, tmp_path)
        loaded = load_configured_registry()
        monkeypatch.setattr("app.taxonomy_registry.load_configured_registry", lambda: RegistryResult("read_only", snapshot=loaded.snapshot))
        assert options()["status"] == "read_only"
