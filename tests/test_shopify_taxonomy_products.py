"""M3A.8 fictional collection writes, comparison evidence and migration safety."""
import copy
import json
import sqlite3
from datetime import timedelta

import pytest
from alembic import command
from sqlalchemy.exc import IntegrityError

from app import db, shopify_drafts as drafts, shopify_taxonomy as taxonomy
from app.database import _alembic_config, ensure_database
from app.models import ShopifyCollectionIdentity, ShopifyProductComparison
from app.shopify_connection import ShopifyError, ShopifyAdapter, COLLECTION_CREATE, COLLECTION_JOIN, STANDARD_CATEGORY
from app.shopify_products import states_for
from test_shopify_drafts import world
from test_shopify_simple_updates import UpdateAdapter, change
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_foundation import isolated_transport, Session, Response, TOKEN


class TaxonomyAdapter(UpdateAdapter):
    def __init__(self):
        super().__init__()
        self.collections = {"gid://shopify/Collection/800": {"title": "Preview Cards", "handle": "preview-cards", "marker": None, "members": {"gid://shopify/Product/100"}}}
        self.collection_failure, self.join_failure = None, False

    def collection_candidates(self, config, title, handle):
        return {"nodes": [{"id": key, "title": row["title"], "handle": row["handle"]} for key, row in self.collections.items()
            if row["title"].casefold() == title.casefold() or row["handle"] == handle], "pageInfo": {"hasNextPage": False}}

    def collection_state(self, config, remote_id, product_id="gid://shopify/Product/0"):
        row = self.collections.get(remote_id)
        if row is None:
            return None
        return {"id": remote_id, "title": row["title"], "handle": row["handle"], "ruleSet": None, "sources": [],
            "hasProduct": product_id in row["members"], "metafield": {"value": row["marker"]}}

    def create_collection(self, config, data):
        row = ShopifyCollectionIdentity.query.filter_by(state="creating").one()
        assert row.remote_id is None and row.operation_id
        self.calls.append("collection_create")
        self.collections["gid://shopify/Collection/900"] = {"title": data["title"], "handle": data["handle"], "marker": data["metafields"][0]["value"], "members": set()}
        if self.collection_failure:
            raise ShopifyError("Collection response timed out; outcome uncertain.")
        return {"collectionCreate": {"collection": {"id": "gid://shopify/Collection/900"}, "userErrors": []}}

    def join_collection(self, config, remote_id, product_id):
        self.calls.append("collection_join")
        self.collections[remote_id]["members"].add(product_id)
        if self.join_failure:
            raise ShopifyError("Membership response lost; reconcile before retry.")
        return {"collectionAddProducts": {"collection": {"id": remote_id}, "userErrors": []}}

    def standard_category(self, config, gid):
        if gid != "gid://shopify/TaxonomyCategory/aa-1":
            raise ShopifyError("Invalid or archived standard category")
        return {"id": gid, "fullName": "Example standard category", "isArchived": False}

    def update_simple_product(self, config, product):
        result = super().update_simple_product(config, product)
        if "category" in product:
            self.remote["category"] = {"id": product["category"], "fullName": "Example standard category"}
        return result


@pytest.fixture
def registry(world, monkeypatch, tmp_path):
    root = tmp_path / "taxonomy"
    root.mkdir()
    path = root / "registry.json"
    path.write_text(json.dumps({"schema_version": 1,
        "categories": [{"key": "cards", "name": "Cards", "slug": "cards", "parent": None, "state": "active", "order": 1},
            {"key": "birthday", "name": "Birthday Cards", "slug": "birthday-cards", "parent": "cards", "state": "active", "order": 2}],
        "storefront_collections": [{"key": "personalised", "name": "Personalised", "slug": "personalised", "state": "active"}],
        "attributes": [], "tags": [{"key": "birthday-tag", "name": "Birthday", "slug": "birthday", "state": "active"}]}))
    from flask import current_app
    monkeypatch.setitem(current_app.config, "TAXONOMY_ROOT", str(root))
    return path


def created(world, adapter=None):
    adapter = adapter or TaxonomyAdapter()
    _, token = drafts.preview(world[0], adapter)
    row = drafts.confirm(token, True, adapter)
    assert row.state == "verified", drafts.evidence(row).get("error")
    if taxonomy.load_configured_registry().available:
        adapter.collections["gid://shopify/Collection/802"] = {"title": "Cards", "handle": "cards", "marker": None, "members": {row.shopify_product_id}}
        db.session.add(ShopifyCollectionIdentity(store_id=row.store_id, source_key="categories:cards", remote_id="gid://shopify/Collection/802", state="verified", evidence_json='{"version":1}'))
        db.session.commit()
    adapter.calls.clear()
    return row, adapter


def status(product):
    from flask import current_app
    with current_app.test_request_context():
        return states_for([product])[product.id]


def test_legacy_identity_fresh_dimensions_comparison_persists(world):
    row, adapter = created(world)
    data = drafts.evidence(row)
    data["plan"].pop("workspace_source_fingerprint")
    drafts.save(row, data)
    change(world, weight=50, dimensions={"length": 155, "width": 155, "height": 3})
    plan, _ = drafts.preview(world[0], adapter)
    assert plan["classification"] == "update"
    db.session.expire_all()
    state = status(world[0])
    assert state["label"] == "Update Available" and state["change_count"] > 0
    assert state["compared_at"] and state["verified_at"]
    assert any("155" in value for value in state["change_summary"])
    assert "product_update" not in adapter.calls


def test_stale_comparison_and_source_override_invalidate(world):
    row, adapter = created(world)
    assert status(world[0])["label"] == "Up to Date"
    comparison = ShopifyProductComparison.query.one()
    comparison.compared_at -= timedelta(minutes=11)
    data = json.loads(comparison.summary_json)
    data["revision_checked_at"] = comparison.compared_at.isoformat()
    comparison.summary_json = json.dumps(data)
    db.session.commit()
    assert status(world[0])["label"] == "Last check matched"
    drafts.preview(world[0], adapter)
    (world[1] / "product_info.json").write_text(json.dumps({"weight": 51}))
    assert status(world[0])["label"] != "Up to Date"


def test_missing_publication_scope_defers_availability_not_valid_metadata(world):
    row, adapter = created(world)
    change(world, live=True, weight=50)
    plan, token = drafts.preview(world[0], adapter)
    assert token and plan["classification"] == "update"
    assert status(world[0])["label"] == "Verification Unavailable"
    assert plan['publication']['read_complete'] is False
    assert 'read_publications' in plan['publication']['reason']
    assert adapter.calls == ["connection_read", "product_read", "dimension_definitions_read"]


def test_create_mapping_then_multiple_memberships(world, registry):
    row, adapter = created(world)
    change(world, categories=["Cards > Birthday Cards"], storefront_collections=["Personalised"], tags=["Birthday"])
    plan, token = drafts.preview(world[0], adapter)
    assert token and len(plan["collections"]) == 3
    assert sum(c["action"] == "Create & Join" for c in plan["collections"]) == 2
    assert "collection_create" not in adapter.calls
    source = "categories:birthday"
    reviewed, token = taxonomy.review_mapping(source, "create", adapter=adapter)
    assert reviewed["source"]["name"] == "Birthday Cards"
    mapped = taxonomy.confirm_mapping(token, True, adapter)
    assert mapped.state == "verified" and mapped.remote_id == "gid://shopify/Collection/900"
    # A second explicit link selects another remote group; multiple memberships are additive.
    adapter.collections["gid://shopify/Collection/901"] = {"title": "Personalised", "handle": "personalised", "marker": None, "members": set()}
    _, token = taxonomy.review_mapping("storefront_collections:personalised", "link", "gid://shopify/Collection/901", adapter=adapter)
    taxonomy.confirm_mapping(token, True, adapter)
    plan, token = drafts.preview(world[0], adapter)
    assert len([c for c in plan["collections"] if c["action"] == "Join"]) == 2
    result = drafts.confirm(token, True, adapter)
    assert result.state == "verified" and result.shopify_product_id == row.shopify_product_id
    assert adapter.calls.count("collection_join") == 2
    assert status(world[0])["label"] == "Up to Date"
    assert "product_create" not in adapter.calls


def test_duplicate_candidate_requires_explicit_link(world, registry):
    adapter = TaxonomyAdapter()
    adapter.collections["gid://shopify/Collection/999"] = {"title": "Birthday Cards", "handle": "birthday-cards", "marker": None, "members": set()}
    with pytest.raises(ShopifyError, match="candidate"):
        taxonomy.review_mapping("categories:birthday", "create", adapter=adapter)
    _, token = taxonomy.review_mapping("categories:birthday", "link", "gid://shopify/Collection/999", adapter=adapter)
    taxonomy.confirm_mapping(token, True, adapter)
    assert "collection_create" not in adapter.calls


def test_uncertain_collection_create_reserved_and_read_only_recovery(world, registry):
    adapter = TaxonomyAdapter()
    adapter.collection_failure = True
    _, token = taxonomy.review_mapping("categories:birthday", "create", adapter=adapter)
    with pytest.raises(ShopifyError, match="uncertain"):
        taxonomy.confirm_mapping(token, True, adapter)
    mapping = taxonomy.mapping("gid://shopify/Shop/42", "categories:birthday")
    assert mapping.state == "uncertain"
    with pytest.raises(ShopifyError, match="recovery"):
        taxonomy.review_mapping("categories:birthday", "create", adapter=adapter)
    _, token = taxonomy.review_mapping("categories:birthday", "reconcile", "gid://shopify/Collection/900", adapter=adapter)
    taxonomy.confirm_mapping(token, True, adapter)
    assert mapping.state == "verified" and adapter.calls.count("collection_create") == 1


def test_uncertain_membership_readback_recovers_without_duplicate_product(world, registry):
    row, adapter = created(world)
    adapter.collections["gid://shopify/Collection/900"] = {"title": "Birthday Cards", "handle": "birthday-cards", "marker": None, "members": set()}
    _, token = taxonomy.review_mapping("categories:birthday", "link", "gid://shopify/Collection/900", adapter=adapter)
    taxonomy.confirm_mapping(token, True, adapter)
    change(world, categories=["Cards > Birthday Cards"])
    plan, token = drafts.preview(world[0], adapter)
    adapter.join_failure = True
    result = drafts.confirm(token, True, adapter)
    assert result.state == "uncertain" and status(world[0])["label"] == "Recovery Required"
    drafts.reconcile(row, adapter=adapter)
    assert row.state == "verified" and adapter.calls.count("collection_join") == 1


def test_registry_and_mapping_change_invalidates_review(world, registry):
    row, adapter = created(world)
    change(world, categories=["Cards > Birthday Cards"])
    drafts.preview(world[0], adapter)
    doc = json.loads(registry.read_text())
    doc["categories"][1]["order"] = 8
    registry.write_text(json.dumps(doc))
    assert status(world[0])["compared_at"] is None


def test_unspecified_tags_preserved_and_explicit_clear_is_reviewed(world):
    row, adapter = created(world)
    adapter.remote["tags"] = ["Remote only"]
    plan, _ = drafts.preview(world[0], adapter)
    assert "tags" not in plan["managed"]["product"]
    change(world, tags=[])
    plan, token = drafts.preview(world[0], adapter)
    assert plan["managed"]["product"]["tags"] == []
    assert any("removes: Remote only" in w for w in plan["warnings"])
    drafts.confirm(token, True, adapter)
    assert adapter.remote["tags"] == []


def test_explicit_standard_category_validation_and_verification(world, registry):
    row, adapter = created(world)
    adapter.remote["category"] = None
    adapter.collections["gid://shopify/Collection/900"] = {"title": "Birthday Cards", "handle": "birthday-cards", "marker": None, "members": {row.shopify_product_id}}
    with pytest.raises(ShopifyError, match="Invalid"):
        taxonomy.review_mapping("categories:birthday", "link", "gid://shopify/Collection/900", "guessed-name", adapter)
    _, token = taxonomy.review_mapping("categories:birthday", "link", "gid://shopify/Collection/900", "gid://shopify/TaxonomyCategory/aa-1", adapter)
    taxonomy.confirm_mapping(token, True, adapter)
    change(world, categories=["Cards > Birthday Cards"])
    plan, token = drafts.preview(world[0], adapter)
    assert plan["managed"]["product"]["category"] == "gid://shopify/TaxonomyCategory/aa-1"
    result = drafts.confirm(token, True, adapter)
    assert result.state == "verified" and adapter.remote["category"]["id"] == "gid://shopify/TaxonomyCategory/aa-1"


def test_migration_preserves_existing_identities_and_uniqueness(tmp_path):
    path = tmp_path / "upgrade.db"
    config = _alembic_config(f"sqlite:///{path}")
    command.upgrade(config, "0010_shopify_product_identity")
    with sqlite3.connect(path) as con:
        con.execute("insert into shopify_product_identity(source_identity,sku,store_key,store_id,store_domain,state,verification_state,reviewed_digest,media_evidence_json) values('source','SKU','key','store','fictional.myshopify.com','verified','verified','digest','{}')")
        before = con.execute("select * from shopify_product_identity").fetchall()
    from app.database import migration_head
    assert ensure_database(f"sqlite:///{path}").revision == migration_head()
    with sqlite3.connect(path) as con:
        assert before == con.execute("select * from shopify_product_identity").fetchall()
        assert con.execute("select count(*) from shopify_product_comparison").fetchone()[0] == 0
        con.execute("insert into shopify_collection_identity(store_id,source_key,remote_id,state,evidence_json) values('store','categories:x','remote','verified','{}')")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("insert into shopify_collection_identity(store_id,source_key,remote_id,state,evidence_json) values('store','categories:y','remote','verified','{}')")


def test_mapping_signed_source_and_target_review_cannot_be_replayed(world, registry):
    adapter = TaxonomyAdapter()
    _, token = taxonomy.review_mapping("categories:birthday", "create", adapter=adapter)
    doc = json.loads(registry.read_text())
    doc["categories"][1]["name"] = "Renamed Birthday"
    registry.write_text(json.dumps(doc))
    with pytest.raises(ShopifyError, match="changed"):
        taxonomy.confirm_mapping(token, True, adapter)
    assert "collection_create" not in adapter.calls
    assert taxonomy.mapping("gid://shopify/Shop/42", "categories:birthday") is None


def test_link_read_failure_does_not_strand_create_reservation(world, registry):
    adapter = TaxonomyAdapter()
    adapter.collections["gid://shopify/Collection/999"] = {"title": "Birthday Cards", "handle": "birthday-cards", "marker": None, "members": set()}
    _, token = taxonomy.review_mapping("categories:birthday", "link", "gid://shopify/Collection/999", adapter=adapter)
    original = adapter.collection_state
    calls = 0
    def read(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ShopifyError("Unable to refresh collection.")
        return original(*args)
    adapter.collection_state = read
    with pytest.raises(ShopifyError, match="refresh"):
        taxonomy.confirm_mapping(token, True, adapter)
    assert taxonomy.mapping("gid://shopify/Shop/42", "categories:birthday") is None
    assert adapter.calls == ["connection_read", "connection_read"]


def test_collection_sources_and_conflicting_standard_categories_block(world, registry):
    remote = TaxonomyAdapter().collection_state(None, "gid://shopify/Collection/800")
    remote["sources"] = [{"__typename": "CollectionSubCollectionsSource"}]
    with pytest.raises(ShopifyError, match="unsupported"):
        taxonomy.validate_collection(remote, remote["id"])
    remote["sources"] = [{"__typename": "CollectionConditionsSource", "targetType": "PRODUCTS",
        "shareable": False, "inclusion": {"conditions": []}, "exclusion": None}]
    assert taxonomy.validate_collection(remote, remote["id"])
    remote["sources"][0]["exclusion"] = {"conditions": [], "selections": {"nodes": [{"product": {"id": "other"}}]}}
    with pytest.raises(ShopifyError, match="exclusion"):
        taxonomy.validate_collection(remote, remote["id"])


def test_scope_blockers_keep_read_only_comparison_available(world, registry):
    adapter = TaxonomyAdapter()
    adapter.test_connection = lambda config: {"shop_id": "gid://shopify/Shop/42", "scopes": ["read_products"]}
    plan, token = drafts.preview(world[0], adapter)
    assert token is None and any("write_products" in b for b in plan["blockers"])
    assert status(world[0])["label"] == "Needs Creating"
    with pytest.raises(ShopifyError, match="write_products"):
        taxonomy.review_mapping("categories:birthday", "create", adapter=adapter)
    assert "collection_create" not in adapter.calls


def test_new_source_edit_during_comparison_cannot_record_equality(world):
    row, adapter = created(world)
    original = adapter.read_simple
    def read(*args):
        result = original(*args)
        change(world, weight=99)
        return result
    adapter.read_simple = read
    drafts.preview(world[0], adapter)
    assert status(world[0])["label"] == "Checking Changes"
    assert status(world[0])["compared_at"] is None


def test_mapping_ui_offline_get_admin_and_csrf(world, registry, monkeypatch):
    from flask import current_app
    app = current_app._get_current_object()
    client = _client(app)
    monkeypatch.setattr(ShopifyAdapter, "test_connection", lambda *a: pytest.fail("GET used remote API"))
    response = client.get("/taxonomy/shopify")
    assert response.status_code == 200 and b"Birthday Cards" in response.data
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/taxonomy/shopify", data={"action": "confirm", "acknowledge": "yes"}).status_code == 400
    assert ShopifyCollectionIdentity.query.count() == 1  # only the fixture catalogue mapping
    assert b"fictional_admin_token" not in response.data


def test_fixed_collection_transport_and_no_fictitious_product_lookup(world, isolated_transport):
    from app.shopify_connection import COLLECTION_STATE, COLLECTION_LOOKUP
    config = drafts.configuration()
    session = Session(Response({"data": {"collection": {"id": "gid://shopify/Collection/99", "title": "Cards", "ruleSet": None, "sources": []}}}))
    adapter = ShopifyAdapter()
    import unittest.mock
    with unittest.mock.patch("app.shopify_connection.requests.Session", return_value=session):
        remote = adapter.collection_state(config, "gid://shopify/Collection/99")
    assert remote["hasProduct"] is False
    assert session.calls[0][1]["json"]["query"] == COLLECTION_STATE
    assert session.calls[0][1]["json"]["variables"]["check"] is False
    assert "@include(if: $check)" in COLLECTION_STATE and "sources" in COLLECTION_STATE
    # Only fixed documents are accepted; arbitrary mutations do not enter the read boundary.
    with pytest.raises(ShopifyError):
        adapter._read(config, COLLECTION_CREATE, {})


def test_conflicting_explicit_categories_and_registry_rename_preserve_identity(world, registry):
    row, adapter = created(world)
    for key, remote_id, name in [("categories:birthday", "gid://shopify/Collection/900", "Birthday"),
                                  ("storefront_collections:personalised", "gid://shopify/Collection/901", "Personalised")]:
        adapter.collections[remote_id] = {"title": name, "handle": name.lower(), "marker": None, "members": set()}
        _, token = taxonomy.review_mapping(key, "link", remote_id, adapter=adapter)
        mapped = taxonomy.confirm_mapping(token, True, adapter)
        mapped.standard_category_id = "gid://shopify/TaxonomyCategory/" + ("aa-1" if name == "Birthday" else "bb-2")
    db.session.commit()
    change(world, categories=["Cards > Birthday Cards"], storefront_collections=["Personalised"])
    plan, token = drafts.preview(world[0], adapter)
    assert token is None and any("conflict" in b for b in plan["blockers"])
    doc = json.loads(registry.read_text())
    doc["categories"][1].update(name="New birthday name", aliases=["Cards > Birthday Cards"])
    registry.write_text(json.dumps(doc))
    assert taxonomy.mapping(row.store_id, "categories:birthday").remote_id == "gid://shopify/Collection/900"
    assert "collection_create" not in adapter.calls


@pytest.mark.parametrize("kind", ["missing", "transport"])
def test_failed_remote_compare_preserves_last_known_but_never_current_equality(world, kind):
    row, adapter = created(world)
    verified = row.verified_at
    original = adapter.read_simple
    if kind == "missing":
        adapter.read_simple = lambda *a: None
    else:
        def failure(*a):
            raise ShopifyError("Shopify read timed out. Try again later.")
        adapter.read_simple = failure
    with pytest.raises(ShopifyError):
        drafts.preview(world[0], adapter)
    assert row.state == "verified" and row.verified_at == verified
    assert status(world[0])["label"] == "Verification Unavailable"
    assert ShopifyProductComparison.query.one().summary_json
    adapter.read_simple = original
    drafts.preview(world[0], adapter)
    assert status(world[0])["label"] == "Up to Date"


def test_legacy_readback_remains_verified_without_taxonomy_equality_backfill(world):
    row, adapter = created(world)
    data = drafts.evidence(row)
    for key in ("taxonomy_fingerprint", "collections", "taxonomy_review_digest"):
        data["plan"].pop(key, None)
    drafts.save(row, data)
    db.session.delete(ShopifyProductComparison.query.one())
    db.session.commit()
    drafts.reconcile(row, adapter=adapter)
    assert row.state == "verified" and ShopifyProductComparison.query.count() == 0
    assert status(world[0])["label"] == "Checking Changes"


def test_explicit_product_type_is_independent_of_hierarchy(world):
    row, adapter = created(world)
    adapter.remote["productType"] = ""
    change(world, shopify_product_type="Greeting card")
    plan, token = drafts.preview(world[0], adapter)
    assert plan["managed"]["product"]["productType"] == "Greeting card"
    drafts.confirm(token, True, adapter)
    assert adapter.remote["productType"] == "Greeting card" and row.state == "verified"


def test_mapping_state_change_invalidates_cached_equality(world, registry):
    row, adapter = created(world)
    change(world, categories=["Cards"])
    drafts.preview(world[0], adapter)
    mapped = taxonomy.mapping(row.store_id, "categories:cards")
    mapped.state = "uncertain"
    db.session.commit()
    assert status(world[0])["label"] == "Recovery Required"
    plan, token = drafts.preview(world[0], adapter)
    assert token is None and any("recovery required" in b for b in plan["blockers"])


def test_existing_collection_candidate_inspection_never_links_or_creates(world, registry):
    adapter = TaxonomyAdapter()
    adapter.collections["gid://shopify/Collection/999"] = {"title": "Birthday Cards", "handle": "birthday-cards", "marker": None, "members": set()}
    plan, token = taxonomy.review_mapping("categories:birthday", "inspect", adapter=adapter)
    assert token is None and plan["candidates"]["nodes"][0]["id"] == "gid://shopify/Collection/999"
    assert ShopifyCollectionIdentity.query.count() == 1
    assert adapter.calls == ["connection_read"]


def test_mapping_edit_during_remote_compare_cannot_borrow_new_mapping_equality(world, registry):
    row, adapter = created(world)
    change(world, categories=["Cards"])
    original = adapter.read_simple
    def read(*args):
        remote = original(*args)
        mapped = taxonomy.mapping(row.store_id, "categories:cards")
        mapped.standard_category_id = "gid://shopify/TaxonomyCategory/aa-1"
        db.session.commit()
        return remote
    adapter.read_simple = read
    drafts.preview(world[0], adapter)
    assert status(world[0])["label"] != "Up to Date"
