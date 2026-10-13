"""Live acceptance shapes reproduced locally; no Shopify network or source writes."""
import json

import pytest
from flask import current_app

from app import shopify_drafts as drafts, shopify_taxonomy_batch as batch
from app.shopify_connection import ShopifyError
from app.shopify_simple_fields import dimension_mm, DIMENSION_TOLERANCE_MM
from test_shopify_drafts import world
from test_shopify_simple_updates import created, change
from test_shopify_taxonomy_products import registry
from test_shopify_taxonomy_batch import prepared, KEYS
from test_phase3_woo_publish_preview import preview_app, _client


def test_canonical_dimension_readback_and_legacy_plan_recovery(world):
    product, identity, adapter = created(world)
    change(world, weight="50", dimensions={"length": "155", "width": "155", "height": "3"})
    _, token = drafts.preview(product, adapter)
    original_read = adapter.read_simple
    def canonical_read(*args):
        remote = original_read(*args)
        for key in ("length", "width", "height"):
            if remote.get(key):
                value = json.loads(remote[key]["value"])
                value["unit"] = "millimeters"
                remote[key]["value"] = json.dumps(value)
        return remote
    adapter.read_simple = canonical_read
    drafts.confirm(token, True, adapter)
    assert identity.state == "verified"
    data = drafts.evidence(identity)
    # Existing M3A.5/8 reviewed receipts used short units. Do not rewrite their plan.
    for item in data["plan"]["managed"]["product"]["metafields"]:
        value = json.loads(item["value"])
        value["unit"] = "mm"
        item["value"] = json.dumps(value)
    data.update(stage="collection_membership", error="Remote dimension evidence is invalid.")
    drafts.save(identity, data, "uncertain")
    previous_id = identity.shopify_product_id
    adapter.calls.clear()
    drafts.reconcile(identity, adapter=adapter)
    assert identity.state == "verified" and identity.shopify_product_id == previous_id
    assert not any(c in adapter.calls for c in ("product_create", "product_update", "stage_write", "collection_join"))
    assert drafts.preview(product, adapter)[0]["classification"] == "no_change"


def test_real_batch_post_renders_retained_review(world, registry, monkeypatch):
    _, adapter = prepared(world)
    original_review = batch.review
    monkeypatch.setattr(batch, "review", lambda keys, **kw: original_review(keys, adapter, **kw))
    client = _client(current_app._get_current_object())
    from werkzeug.datastructures import MultiDict
    response = client.post("/taxonomy/shopify", data=MultiDict([
        ("action", "batch_review"), ("after", "0"), *(("sources", key) for key in KEYS)]))
    assert response.status_code == 200
    assert b"Confirm reviewed taxonomy stages" in response.data
    assert "collection_create" not in adapter.calls


def test_legacy_verified_product_in_checkbox_batch_is_excluded_not_500(world, registry, monkeypatch):
    identity, adapter = prepared(world)
    data = drafts.evidence(identity)
    data["plan"].pop("managed")  # original verified M3A receipt, still protected
    drafts.save(identity, data)
    original_review = batch.review
    monkeypatch.setattr(batch, "review", lambda keys, **kw: original_review(keys, adapter, **kw))
    response = _client(current_app._get_current_object()).post("/taxonomy/shopify",
        data={"action": "batch_review", "sources": KEYS})
    assert response.status_code == 200
    assert b"individual product Review" in response.data


@pytest.mark.parametrize("unit,value", [("mm", 155), ("millimeters", 155),
    ("MILLIMETERS", 155), ("centimeters", 15.5), ("cm", 15.5), ("meters", 0.155),
    ("inches", 155 / 25.4), ("feet", 155 / 304.8), ("yards", 155 / 914.4)])
def test_dimension_unit_normalization(unit, value):
    from decimal import Decimal
    assert abs(dimension_mm(json.dumps({"unit": unit, "value": value})) - Decimal(155)) <= DIMENSION_TOLERANCE_MM


@pytest.mark.parametrize("value", ["not JSON", "null", "[]", '{"unit":"parsecs","value":155}',
    '{"unit":"millimeters","value":true}', '{"unit":"millimeters","value":"NaN"}',
    '{"unit":"millimeters","value":-1}', '{"unit":"millimeters","value":0}'])
def test_invalid_dimension_does_not_manufacture_equality(value):
    with pytest.raises(ShopifyError, match="dimension evidence is invalid"):
        dimension_mm(value)


def test_failed_field_verification_retains_media_and_id_then_recovers_without_write(world):
    product, identity, adapter = created(world)
    change(world, dimensions={"length": "155"}, weight="50")
    _, token = drafts.preview(product, adapter)
    read = adapter.read_simple
    corrupt = [True]
    def invalid_read(*args):
        remote = read(*args)
        if remote.get("length") and corrupt[0] and any(c in adapter.calls for c in ("product_update", "variant_write")):
            remote["length"]["value"] = '{"unit":"unknown","value":155}'
        return remote
    adapter.read_simple = invalid_read
    drafts.confirm(token, True, adapter)
    assert identity.state == "uncertain"
    data = drafts.evidence(identity)
    assert data["stage"] == "managed_field_verification"
    assert data["verification"]["identity"] == "verified" and data["verification"]["fields"] == "unverified"
    assert len(data["observed_media"]) == len(data["created_media"]) == 2
    old_id = identity.shopify_product_id
    adapter.calls.clear()
    with pytest.raises(ShopifyError, match="Recovery required"):
        drafts.preview(product, adapter)
    corrupt[0] = False
    drafts.reconcile(identity, adapter=adapter)
    assert identity.state == "verified" and identity.shopify_product_id == old_id
    assert drafts.evidence(identity)["verification"]["media"] == "verified"
    assert not any(c in adapter.calls for c in ("product_create", "product_update", "stage_write"))


def test_creation_long_unit_readback_is_supported_not_failed_create(world):
    from test_shopify_simple_updates import UpdateAdapter
    change(world, dimensions={"length": "155", "height": "3"}, weight="50")
    adapter = UpdateAdapter()
    plan, token = drafts.preview(world[0], adapter)
    assert all(json.loads(m["value"])["unit"] == "millimeters" for m in plan["managed"]["product"]["metafields"])
    assert "length" not in plan["managed"]["variant"]["inventoryItem"]  # no unsupported native dimensions
    identity = drafts.confirm(token, True, adapter)
    assert identity.state == "verified"
    assert drafts.evidence(identity)["verification"]["fields"] == "verified"
    assert adapter.remote["simpleVariants"]["nodes"][0]["inventoryItem"]["measurement"]["weight"]["value"] == 50


@pytest.mark.parametrize("fields", [{"sources": []}, {"sources": ["categories:not-real"]},
    {"sources": KEYS, "after": "-1"}, {"sources": KEYS, "after": "invalid"}])
def test_invalid_checkbox_inputs_are_validation_not_remote_requests(world, registry, monkeypatch, fields):
    from app.shopify_connection import ShopifyAdapter
    monkeypatch.setattr(ShopifyAdapter, "test_connection", lambda *a: pytest.fail("Invalid selection contacted Shopify"))
    response = _client(current_app._get_current_object()).post("/taxonomy/shopify", data={"action": "batch_review", **fields})
    assert response.status_code == 400
    assert b"Internal Server Error" not in response.data


def test_unified_workspace_retains_tree_mappings_and_offline_navigation(world, registry, monkeypatch):
    from app import db
    from app.models import ShopifyCollectionIdentity
    from app.shopify_connection import ShopifyAdapter
    db.session.add(ShopifyCollectionIdentity(store_id="gid://shopify/Shop/42", source_key="categories:cards",
        remote_id="gid://shopify/Collection/902", state="verified", evidence_json='{"version":1}'))
    db.session.commit()
    monkeypatch.setattr(ShopifyAdapter, "test_connection", lambda *a: pytest.fail("Workspace contacted Shopify"))
    client = _client(current_app._get_current_object())
    for path in ("/taxonomy", "/taxonomy/shopify", "/taxonomy?kind=attributes"):
        response = client.get(path)
        assert response.status_code == 200
        for text in (b"Categories", b"Storefront Collections", b"Attributes &amp; Tags", b"Shopify Sync",
            b"Shopify verified", b"Select Parent", b"data-taxonomy-panel"):
            if text == b"Select Parent":
                assert b"Select parent and descendants" in response.data
            else:
                assert text in response.data
        assert b"Woo Taxonomy Sync" not in response.data
        assert b'value="categories:cards"' in response.data and b'value="categories:birthday"' in response.data
        assert b'data-taxonomy-selection-count' in response.data
    assert ShopifyCollectionIdentity.query.filter_by(source_key="categories:cards").one().remote_id == "gid://shopify/Collection/902"


@pytest.mark.parametrize("body", [None, [], "invalid", {"execution": 1}, {"execution": "x" * 5000}])
def test_invalid_execution_body_is_validation_not_500(world, body):
    response = _client(current_app._get_current_object()).post("/api/shopify/taxonomy/step", json=body)
    assert response.status_code == 400
    assert "no write was attempted" in response.get_json()["error"]


def test_missing_media_receipt_recovery_reviews_fresh_local_digest_not_old_formatter(world):
    from test_shopify_drafts import FakeDraftAdapter
    adapter = FakeDraftAdapter("create")
    product = world[0]
    _, token = drafts.preview(product, adapter)
    identity = drafts.confirm(token, True, adapter)
    adapter.remote["simpleVariants"]["nodes"][0].update(sku=product.sku, price="3.50")
    adapter.remote["simpleVariants"]["nodes"][0]["inventoryItem"]["sku"] = product.sku
    drafts.reconcile(identity, adapter=adapter)
    # Reviewed provenance may use an old unit/warning formatter. Preserve it;
    # mapping approval instead signs fresh local bytes plus retained remote evidence.
    identity.reviewed_digest = "old-formatter-digest"
    from app import db
    db.session.commit()
    approval = drafts.recovery_token(identity)
    assert approval
    old_id = identity.shopify_product_id
    drafts.reconcile(identity, adapter=adapter, approval=approval)
    assert identity.state == "verified" and identity.shopify_product_id == old_id
    assert identity.reviewed_digest == "old-formatter-digest"
    assert adapter.calls.count("product_create") == 1 and adapter.calls.count("binary_upload") == 2


def test_partial_collection_membership_preserves_field_media_evidence_and_reconciles(world, registry):
    from test_shopify_taxonomy_products import created as taxonomy_created
    identity, adapter = taxonomy_created(world)
    change(world, categories=["Cards"], weight="50", dimensions={"length": "155"})
    remote_collection = adapter.collections["gid://shopify/Collection/802"]
    remote_collection["members"].clear()
    _, token = drafts.preview(world[0], adapter)
    adapter.join_failure = True  # write actually succeeds but response is lost
    drafts.confirm(token, True, adapter)
    assert identity.state == "uncertain"
    original_id = identity.shopify_product_id
    media = [m["id"] for m in adapter.remote["media"]["nodes"]]
    writes = adapter.calls.count("collection_join")
    drafts.reconcile(identity, adapter=adapter)
    assert identity.state == "verified" and identity.shopify_product_id == original_id
    assert drafts.evidence(identity)["verification"]["memberships"] == "verified"
    assert [m["id"] for m in adapter.remote["media"]["nodes"]] == media
    assert adapter.calls.count("collection_join") == writes
