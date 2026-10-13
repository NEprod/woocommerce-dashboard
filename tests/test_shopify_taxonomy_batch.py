"""Reviewed taxonomy stages use fictional remote state and durable existing tables."""
import copy
import json

import pytest

from app import db, shopify_drafts as drafts, shopify_taxonomy as taxonomy, shopify_taxonomy_batch as batch
from app.models import ShopifyCollectionIdentity, CatalogueOperation
from app.shopify_connection import ShopifyError, ShopifyAdapter
from test_shopify_drafts import world
from test_shopify_simple_updates import change
from test_shopify_taxonomy_products import TaxonomyAdapter, registry
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_foundation import isolated_transport


class BatchAdapter(TaxonomyAdapter):
    def create_collection(self, config, values):
        pending = ShopifyCollectionIdentity.query.filter_by(state="creating").one()
        assert pending.operation_id and pending.remote_id is None
        self.calls.append("collection_create")
        remote_id = "gid://shopify/Collection/" + str(900 + self.calls.count("collection_create") - 1)
        self.collections[remote_id] = {"title": values["title"], "handle": values["handle"], "marker": values["metafields"][0]["value"], "members": set()}
        if self.collection_failure == self.calls.count("collection_create"):
            raise ShopifyError("Collection create response lost; uncertain.")
        return {"collectionCreate": {"collection": {"id": remote_id}, "userErrors": []}}


def prepared(world):
    adapter = BatchAdapter()
    _, token = drafts.preview(world[0], adapter)
    identity = drafts.confirm(token, True, adapter)
    adapter.calls.clear()
    change(world, categories=["Cards > Birthday Cards"], storefront_collections=["Personalised"], tags=["Birthday"])
    return identity, adapter


KEYS = ["categories:cards", "categories:birthday", "storefront_collections:personalised"]


def finish(row, adapter):
    token = batch.execution_token(row)
    for _ in range(10):
        row = batch.execute_one(token, adapter)
        if row.status != "approved":
            return row
    pytest.fail("Unbounded batch")


def test_authoritative_parent_memberships_and_no_filesystem_collection(world, registry):
    change(world, categories=["Cards > Birthday Cards"])
    groups, errors = taxonomy.assignments(world[0])
    assert not errors and [g["key"] for g in groups] == ["categories:birthday", "categories:cards"]
    assert not any(s["key"].startswith("collection:") for s in taxonomy.sources())
    assert "storefront_collections:personalised" not in [g["key"] for g in groups]
    original = json.loads(world[2].read_text())
    assert original["categories"] == ["Cards > Birthday Cards"]  # ancestors never written into JSON


def test_review_is_read_only_and_batch_creates_joins_tags_then_verifies(world, registry):
    identity, adapter = prepared(world)
    verified_at = identity.verified_at
    row, plan, token = batch.review(KEYS, adapter)
    assert plan["totals"]["collections_create"] == 3
    assert plan["totals"]["memberships_join"] == 3 and plan["totals"]["tags_change"] == 1
    assert not any("create" in c or "update" in c or "join" in c for c in adapter.calls)
    with pytest.raises(ShopifyError, match="approval"):
        batch.approve(token, False)
    row = batch.approve(token, True)
    finish(row, adapter)
    assert row.status == "partial" and len(json.loads(row.scope)["results"]) == 4
    assert plan["totals"]["excluded_products"] == 2  # other fixture products inherit assignments but lack identities
    assert adapter.calls.count("collection_create") == 3 and adapter.calls.count("collection_join") == 3
    assert adapter.remote["tags"] == ["Birthday"] and identity.state == "verified"
    assert identity.verified_at == verified_at  # subset verification isn't complete-product verification
    assert "product_create" not in adapter.calls
    assert all(identity.shopify_product_id in r["members"] for key, r in adapter.collections.items() if key != "gid://shopify/Collection/800")
    assert drafts.preview(world[0], adapter)[0]["classification"] == "no_change"


def test_candidate_conflict_blocks_only_affected_group_not_unrelated_creates(world, registry):
    identity, adapter = prepared(world)
    adapter.collections["gid://shopify/Collection/999"] = {"title": "Personalised", "handle": "personalised", "marker": None, "members": set()}
    row, plan, token = batch.review(KEYS, adapter)
    assert plan["totals"]["excluded_groups"] == 1 and plan["totals"]["excluded_products"] >= 1
    row = batch.approve(token, True)
    finish(row, adapter)
    assert row.status == "partial" and adapter.calls.count("collection_create") == 2
    assert not taxonomy.mapping(identity.store_id, "storefront_collections:personalised")
    assert "collection_join" not in adapter.calls


def test_partial_collection_create_keeps_success_and_recovers_without_retry(world, registry):
    identity, adapter = prepared(world)
    row, plan, token = batch.review(KEYS, adapter)
    batch.approve(token, True)
    execution = batch.execution_token(row)
    batch.execute_one(execution, adapter)
    adapter.collection_failure = 2
    with pytest.raises(ShopifyError, match="uncertain"):
        batch.execute_one(execution, adapter)
    assert row.status == "partial" and len(json.loads(row.scope)["results"]) == 1
    mapping = taxonomy.mapping(identity.store_id, "categories:birthday")
    assert mapping.state == "uncertain"
    batch.execute_one(execution, adapter)
    assert adapter.calls.count("collection_create") == 2
    _, link = taxonomy.review_mapping("categories:birthday", "reconcile", "gid://shopify/Collection/901", adapter=adapter)
    taxonomy.confirm_mapping(link, True, adapter)
    batch.recover_batch(row, adapter)
    assert row.status == "approved"
    finish(row, adapter)
    assert adapter.calls.count("collection_create") == 3 and row.status == "partial"


def test_uncertain_memberships_require_readback_and_do_not_repeat_writes(world, registry):
    identity, adapter = prepared(world)
    row, plan, token = batch.review(KEYS, adapter)
    batch.approve(token, True)
    execution = batch.execution_token(row)
    for _ in range(3):
        batch.execute_one(execution, adapter)
    adapter.join_failure = True
    with pytest.raises(ShopifyError, match="reconcile"):
        batch.execute_one(execution, adapter)
    assert identity.state == "uncertain" and adapter.remote["tags"] == ["Birthday"]
    with pytest.raises(ShopifyError, match="membership"):
        drafts.reconcile(identity, adapter=adapter)
    # Model the operator explicitly resolving the remaining reviewed memberships in Shopify.
    for remote_id in ["gid://shopify/Collection/900", "gid://shopify/Collection/901", "gid://shopify/Collection/902"]:
        adapter.collections[remote_id]["members"].add(identity.shopify_product_id)
    writes = adapter.calls.count("collection_join")
    batch.recover_batch(row, adapter)
    assert row.status == "partial" and identity.state == "verified"
    assert adapter.calls.count("collection_join") == writes


def test_changed_registry_or_source_after_approval_stops_without_write(world, registry):
    identity, adapter = prepared(world)
    row, plan, token = batch.review(KEYS, adapter)
    batch.approve(token, True)
    doc = json.loads(registry.read_text())
    doc["categories"][0]["name"] = "Renamed Cards"
    registry.write_text(json.dumps(doc))
    with pytest.raises(ShopifyError, match="changed"):
        batch.execute_one(batch.execution_token(row), adapter)
    assert "collection_create" not in adapter.calls and row.status == "partial"


def test_missing_scope_is_explicit_and_no_optional_standard_category_block(world, registry):
    identity, adapter = prepared(world)
    adapter.test_connection = lambda c: {"shop_id": identity.store_id, "scopes": ["read_products"]}
    row, plan, token = batch.review(KEYS, adapter)
    assert all("write_products" in g["blocker"] for g in plan["groups"])
    assert plan["steps"] == []
    assert not any("standard category" in b for b in plan["blockers"])


def test_overview_and_individual_create_wording_are_offline_safe(world, registry, monkeypatch):
    from flask import current_app
    app = current_app._get_current_object()
    client = _client(app)
    monkeypatch.setattr(ShopifyAdapter, "test_connection", lambda *a: pytest.fail("GET used API"))
    html = client.get("/taxonomy/shopify").get_data(as_text=True)
    assert "TLC categories &amp; subcategories" in html and "Authored storefront collections" in html
    assert "Review Taxonomy Sync" in html and "Catalogue collection · Preview Cards" not in html
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/api/shopify/taxonomy/step", json={"execution": "invalid"}).status_code == 400


def test_storefront_membership_spans_folders_and_missing_assignment_stays_none(world, registry):
    from app.models import Product
    change(world, storefront_collections=["Personalised"], categories=[])
    catalogue_root = world[1].parent.parent
    other = catalogue_root / "Other Group"
    (other / "Product").mkdir(parents=True)
    shared = other / "product_info.json"
    shared.write_text(json.dumps({"collection_type": "Simple", "live": False, "storefront_collections": ["Personalised"]}))
    product = Product(id=999, title="Other product", sku="OTHER-999", product_type="simple", catalogue_status="active",
        source_relpath="Other Group/Product", shared_json_relpath="Other Group/product_info.json", shared_json_path=str(shared))
    db.session.add(product)
    db.session.commit()
    first, _ = taxonomy.assignments(world[0])
    second, _ = taxonomy.assignments(product)
    assert [r["key"] for r in first] == [r["key"] for r in second] == ["storefront_collections:personalised"]
    (other / "Product" / "product_info.json").write_text(json.dumps({"storefront_collections": []}))
    assert taxonomy.assignments(product)[0] == []
    assert not any(s["key"].startswith("collection:") for s in taxonomy.sources())


def test_signed_batch_tampering_and_approval_replay_are_rejected(world, registry):
    identity, adapter = prepared(world)
    row, plan, token = batch.review(KEYS, adapter)
    with pytest.raises(ShopifyError, match="expired"):
        batch.approve(token + "tampered", True)
    batch.approve(token, True)
    with pytest.raises(ShopifyError, match="already approved"):
        batch.approve(token, True)
    assert "collection_create" not in adapter.calls


def test_selected_child_proposal_automatically_includes_required_parent(world, registry):
    identity, adapter = prepared(world)
    row, plan, token = batch.review(["categories:birthday"], adapter)
    assert {s["key"] for s in plan["sources"]} == {"categories:birthday", "categories:cards"}
    assert plan["totals"]["collections_create"] == 2
