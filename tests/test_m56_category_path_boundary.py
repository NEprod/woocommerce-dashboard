"""Readable category paths require the existing explicit opt-in, not a new flag."""
import json

import pytest

from app import db
from app.models import Category, Product, ProductRelationship, CatalogueOperation
from app.taxonomy_workspace import validate
from app.utils.ingest import _slugify
from app.woo_taxonomy_sync import persist
from app.woo_publish_preview import generate_publish_plan, store_identity, PreviewError
from app.woo_controlled_publish import prepare_publish_confirmation, start_publish_operation
from test_m56_product_taxonomy import world
from test_phase3_woo_publish_preview import preview_app
from test_taxonomy_workspace import entry


@pytest.mark.parametrize("explicit,wrong_parent", [(True, False), (False, False), (True, True)])
def test_two_products_shared_path_explicit_consumption_vs_legacy_creation(preview_app, monkeypatch, tmp_path, explicit, wrong_parent):
    with preview_app.app_context():
        first, woo, source, root, document, remote = world(preview_app, monkeypatch, tmp_path)
        definition = entry("birthday-cards", name="Birthday Cards", parent="cards")
        data = json.loads((root / "registry.json").read_text())
        data["categories"].append(definition)
        (root / "registry.json").write_bytes(validate(data))
        child = {"id": 201, "name": "Birthday Cards", "slug": "birthday-cards", "parent": 91}
        remote["products/categories/201"] = child
        persist({"kind": "categories", "scope": "", "key": definition["key"], "local": definition, "remote_scope": 0}, store_identity()["key"], child)
        path = "Cards > Birthday Cards"
        document["categories"] = [path]
        if not explicit: document.pop("variation_attributes")
        source.write_text(json.dumps(document))
        second = db.session.get(Product, 2)
        second.tags.clear()
        override = source.parent / "Existing/product_info.json"
        override.parent.mkdir()
        override.write_text('{"storefront_collections": []}')
        category = Category(name=path, slug=_slugify(path))
        db.session.add(category)
        for pid in (first, second.id):
            product = db.session.get(Product, pid)
            product.categories[:] = [category]
        db.session.add_all([
            ProductRelationship(source_product_id=first, target_sku=second.sku, resolved_target_product_id=second.id, relationship_type="cross_sell", position=0),
            ProductRelationship(source_product_id=second.id, target_sku=db.session.get(Product, first).sku, resolved_target_product_id=first, relationship_type="cross_sell", position=0),
        ])
        db.session.commit()
        woo.taxonomy["categories"] = [remote["products/categories/91"], child]
        original = woo.request_json
        def request(method, url, **kwargs):
            result, response = original(method, url, **kwargs)
            # Model Woo's entity-encoded category name, not an echo-only fake.
            if method == "GET" and "/products/categories/" in url and isinstance(result, dict) and result.get("name") == path:
                result = {**result, "name": "Cards &gt; Birthday Cards"}
            return result, response
        woo.request_json = request
        before = source.read_bytes(), override.read_bytes(), (root / "registry.json").read_bytes()
        if wrong_parent:
            child["parent"] = 999
            with pytest.raises(PreviewError, match="Taxonomy Sync"):
                generate_publish_plan({"kind": "selected", "product_ids": [first, second.id]}, client=woo)
            assert not woo.writes
            return
        plan = generate_publish_plan({"kind": "selected", "product_ids": [first, second.id]}, client=woo)
        if explicit:
            assert all(p["payload"]["categories"] == [{"id": 201}] for p in plan["products"])
            assert all(not p["taxonomy"]["categories"] for p in plan["products"])
        else:
            assert all(p["explicit_taxonomy"] is None for p in plan["products"])
            assert all(p["taxonomy"]["categories"][0]["name"] == path for p in plan["products"])
        confirmation = prepare_publish_confirmation(plan["operation_id"], plan["digest"], [first, second.id], client=woo)
        operation_id = start_publish_operation(confirmation, client=woo, run_async=False)
        operation = db.session.get(CatalogueOperation, operation_id)
        definition_writes = [w for w in woo.writes if "/products/categories" in w[1]]
        if explicit:
            assert operation.status == "succeeded", operation.scope
            assert not definition_writes
            products = {p["sku"]: p for p in woo.products.values()}
            first_remote = products[db.session.get(Product, first).sku]
            second_remote = products[second.sku]
            assert first_remote["brands"] == [{"id": 92}] and second_remote["brands"] == []
            assert first_remote["cross_sell_ids"] == [second_remote["id"]]
            assert second_remote["cross_sell_ids"] == [first_remote["id"]]
            assert all(not a["variation"] for p in products.values() for a in p["attributes"])
            fresh = generate_publish_plan({"kind": "selected", "product_ids": [first, second.id]}, client=woo)
            assert all(p["action"] == "no_change" for p in fresh["products"])
        else:
            assert len(woo.writes) == len(definition_writes) == 1
            assert definition_writes[0][0] == "POST"
            assert definition_writes[0][2] == {"name": path, "slug": _slugify(path)}
            assert "Created categorie conflicts with the reviewed managed identity" in operation.scope
            assert not woo.products  # No parent or relationship write after failed dependency.
        assert (source.read_bytes(), override.read_bytes(), (root / "registry.json").read_bytes()) == before
