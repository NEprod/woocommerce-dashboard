"""Normal Products use local evidence; only explicit reviewed actions contact Shopify."""
import pytest
from PIL import Image

from app import db, shopify_drafts as drafts
from app.models import ShopifyProductIdentity
from app.shopify_products import states_for
from app.shopify_connection import ShopifyError, ShopifyAdapter, SKU_QUERY
from test_shopify_drafts import world, FakeDraftAdapter
from test_shopify_simple_updates import created, change
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_foundation import isolated_transport


def status(product):
    from flask import current_app
    with current_app.test_request_context():
        return states_for([product])[product.id]


def test_unknown_is_not_proof_of_remote_absence(world):
    product, _, _ = world
    state = status(product)
    assert state["label"] == "Not yet verified" and not state["remote_id"]
    assert "duplicate checks" in state["message"]
    assert state["review_url"] == "/products/1/shopify/review"
    assert ShopifyProductIdentity.query.count() == 0


def test_verified_local_evidence_and_restart(world):
    product, row, adapter = created(world)
    assert status(product)["label"] == "Up to Date"
    assert status(product)["remote_status"] == "Draft (last compared)"
    assert status(product)["publication"] == "Not verified"
    db.session.expire_all()
    assert status(product)["verified_at"] and status(product)["label"] == "Up to Date"
    assert adapter.calls == []


@pytest.mark.parametrize("field,value", [("description", "Genuine change"), ("weight", 12), ("live", True), ("meta_title", "New SEO")])
def test_authored_change_invalidates_workspace_no_change(world, field, value):
    product, _, _ = created(world)
    change(world, **{field: value})
    assert status(product)["label"] == "Checking Changes"


def test_image_edit_invalidates_without_reading_image_bytes(world, monkeypatch):
    product, folder, _ = world
    created(world)
    Image.new("RGB", (7, 7), "blue").save(folder / "original.png")
    monkeypatch.setattr(drafts, "source_bytes", lambda *a: pytest.fail("Workspace hashed original image"))
    assert status(product)["label"] == "Checking Changes"


@pytest.mark.parametrize("value", [None, "false", 0])
def test_intent_is_explicit_and_live_transition_deferred(world, value):
    product, _, _ = world
    change(world, live=value)
    adapter = FakeDraftAdapter()
    with pytest.raises(ShopifyError, match="intent before publication"):
        drafts.preview(product, adapter)
    assert not adapter.calls and ShopifyProductIdentity.query.count() == 0


@pytest.mark.parametrize("state", ["uncertain", "creating", "pending", "updating", "processing"])
def test_unresolved_identities_never_claim_no_change(world, state):
    product, row, _ = created(world)
    row.state = state
    row.verification_state = "unverified"
    db.session.commit()
    assert status(product)["state"] == ("processing" if state == "processing" else "recovery_required")
    # Processing may finish through read-only verification. This deliberately
    # unrelated remote response must still fail identity protection, not be
    # mistaken for a genuinely uncertain publishing attempt.
    message = "Identity remains protected" if state == "processing" else "Recovery required"
    with pytest.raises(ShopifyError, match=message):
        drafts.preview(product, FakeDraftAdapter())


def test_legacy_verified_evidence_is_not_invented_as_current(world):
    product, row, _ = created(world)
    data = drafts.evidence(row)
    del data["plan"]["workspace_source_fingerprint"]
    drafts.save(row, data)
    assert status(product)["label"] == "Verification Unavailable"
    assert status(product)["identity_url"]


def test_normal_pages_render_offline_and_hide_woo(world, monkeypatch):
    product, row, adapter = created(world)
    monkeypatch.setattr(ShopifyAdapter, "test_connection", lambda *a: pytest.fail("GET contacted Shopify"))
    import app.woo_sync_workspace as woo
    monkeypatch.setattr(woo, "overview", lambda: pytest.fail("Normal page uses Woo overview"))
    client = _client(product_session_app())
    for path in ("/products", "/products/1", "/", "/products/1/shopify/review"):
        page = client.get(path)
        assert page.status_code == 200
        assert b'href="/woo-sync"' not in page.data
        assert b'data-woo-refresh' not in page.data
    data = client.get("/api/edit_products").get_json()
    item = next(p for p in data["items"] if p["id"] == product.id)
    assert item["shopify"]["label"] == "Up to Date" and "woo_live" not in item
    assert item["view_url"] == "/products/1"
    assert "collection_edit_url" in item and "edit_url" in item
    assert adapter.calls == []


def product_session_app():
    from flask import current_app
    return current_app._get_current_object()


def test_scoped_review_uses_existing_update_and_no_change_path(world, monkeypatch):
    product, row, adapter = created(world)
    monkeypatch.setattr(drafts, "ShopifyAdapter", lambda: adapter)
    client = _client(product_session_app())
    page = client.post("/products/1/shopify/review", data={"action": "preview", "product_id": 999})
    assert page.status_code == 200 and b'No Change' in page.data
    assert b'Confirm draft creation' not in page.data and "product_create" not in adapter.calls
    change(world, description="Reviewed edit")
    page = client.post("/products/1/shopify/review", data={"action": "preview"})
    assert page.status_code == 200 and b'Confirm reviewed update' in page.data
    assert ShopifyProductIdentity.query.one().shopify_product_id == row.shopify_product_id


def test_duplicate_sku_discovery_blocks_preview_and_confirm(world, monkeypatch):
    product, _, _ = world
    adapter = FakeDraftAdapter()
    _, token = drafts.preview(product, adapter)
    monkeypatch.setattr(adapter, "find_sku", lambda *a: {"nodes": [{"id": "gid://shopify/ProductVariant/9"}], "pageInfo": {"hasNextPage": False}})
    for call in (lambda: drafts.preview(product, adapter), lambda: drafts.confirm(token, True, adapter)):
        with pytest.raises(ShopifyError, match="SKU candidate"):
            call()
    assert ShopifyProductIdentity.query.count() == 0
    assert "product_create" not in adapter.calls


def test_exact_sku_transport_is_bounded_read_only(world, monkeypatch):
    product, _, _ = world
    calls = []
    adapter = ShopifyAdapter()
    monkeypatch.setattr(adapter, "_read", lambda config, query, variables: calls.append((query, variables)) or {"productVariants": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}})
    assert adapter.find_sku(drafts.configuration(), product.sku)["nodes"] == []
    assert calls == [(SKU_QUERY, {"query": 'sku:"' + product.sku + '"'})]


def test_scoped_review_auth_csrf_and_mobile_hooks(world):
    app = product_session_app()
    assert app.test_client().get("/products/1/shopify/review").status_code in (302, 401)
    from flask import g
    g.pop("_login_user", None)
    app.config["WTF_CSRF_ENABLED"] = True
    client = _client(app)
    try:
        assert client.post("/products/1/shopify/review", data={"action": "preview"}).status_code == 400
        html = client.get("/products").get_data(as_text=True)
        assert "data-shopify-review-queue" in html and "bulk execution is not implemented" in html
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_scoped_confirm_rejects_another_product_review_before_reads(world):
    product, _, _ = world
    adapter = FakeDraftAdapter()
    _, token = drafts.preview(product, adapter)
    adapter.calls.clear()
    with pytest.raises(ShopifyError, match="does not match"):
        drafts.confirm(token, True, adapter, expected_product_id=999)
    assert adapter.calls == [] and ShopifyProductIdentity.query.count() == 0


def test_bad_retained_plan_never_breaks_product_list(world):
    product, row, _ = created(world)
    data = drafts.evidence(row)
    data["plan"] = "invalid"
    drafts.save(row, data)
    assert status(product)["label"] == "Verification Unavailable"


def test_unresolved_taxonomy_invalidates_equality_without_inventing_writes(world):
    product, _, adapter = created(world)
    change(world, customisation={"proof_policy": "required"}, categories=["Future category"])
    assert status(product)["label"] == "Checking Changes"
    plan, token = drafts.preview(product, adapter)
    assert plan["classification"] == "no_change" and token is None
    assert any("Unresolved" in b for b in plan["blockers"])
    assert status(product)["label"] == "Needs Attention"
