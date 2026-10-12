"""Bounded fictional Shopify reads; never use real credentials or HTTP."""
from pathlib import Path

import pytest
import requests
from sqlalchemy import event

from app import db
from app import commerce_config as config
from app.shopify_connection import (
    ShopifyAdapter, ShopifyConfiguration, ShopifyError, READ_QUERIES,
    PRODUCTS_QUERY, PRODUCT_QUERY, VARIANT_QUERY, COLLECTIONS_QUERY, COLLECTION_QUERY,
)
from test_shopify_foundation import setup_app, isolated_transport, admin_client, Response, Session, TOKEN

SETTINGS = ShopifyConfiguration("fictional.myshopify.com", TOKEN)
PID = "gid://shopify/Product/101"
VID = "gid://shopify/ProductVariant/102"
CID = "gid://shopify/Collection/103"
MID = "gid://shopify/MediaImage/104"


def page(nodes, more=False, end=None):
    return {"nodes": nodes, "pageInfo": {"hasNextPage": more, "endCursor": end}}


def media_page(more=False, end=None):
    return {"edges": [{"cursor": "media-1", "node": {
        "id": MID, "mediaContentType": "IMAGE", "status": "READY", "fileStatus": "READY",
        "alt": "Birthday card", "mimeType": "image/png",
        "originalSource": {"url": "https://cdn.shopify.com/original/Card%20Photo.png?expires=123"},
        "image": {"id": "gid://shopify/ImageSource/105", "url": "https://cdn.shopify.com/files/CardPhoto.png?v=456",
                  "width": 1550, "height": 1550, "altText": "Printed card"}}}],
        "pageInfo": {"hasNextPage": more, "endCursor": end}}


def product():
    return {"id": PID, "title": "Birthday Card", "handle": "birthday-card", "status": "DRAFT",
        "description": "Personalised card", "descriptionHtml": "<script>unsafe()</script>",
        "options": [{"id": "gid://shopify/ProductOption/106", "name": "Finish", "position": 1, "values": ["Matt"]}],
        "variants": page([{"id": VID, "title": "Matt", "sku": "TLC-0001-1", "price": "3.50",
            "compareAtPrice": None, "position": 1, "selectedOptions": [{"name": "Finish", "value": "Matt"}],
            "media": page([{"id": MID, "status": "READY", "mediaContentType": "IMAGE"}], True, "assoc-1")}]),
        "media": media_page()}


def session_for(key, result):
    return Session(Response({"data": {key: result}}))


def test_product_page_cursor_and_fixed_query():
    adapter = ShopifyAdapter()
    first = session_for("products", page([{"id": PID, "title": "Card"}], True, "p1"))
    result = adapter.discover_products(SETTINGS, session=first)
    second = session_for("products", page([]))
    assert adapter.discover_products(SETTINGS, result["pageInfo"]["endCursor"], session=second)["nodes"] == []
    assert len(first.calls) == len(second.calls) == 1
    assert first.calls[0][1]["json"] == {"query": PRODUCTS_QUERY, "variables": {"after": None}}
    assert second.calls[0][1]["json"]["variables"] == {"after": "p1"}
    assert "first: 20" in PRODUCTS_QUERY


def test_product_variants_options_and_actual_image_identity():
    response = product()
    session = session_for("product", response)
    result = ShopifyAdapter().inspect_product(SETTINGS, PID, "v1", "m1", session=session)
    variant = result["variants"]["nodes"][0]
    assert (variant["id"], variant["sku"], variant["price"], variant["selectedOptions"]) == (
        VID, "TLC-0001-1", "3.50", [{"name": "Finish", "value": "Matt"}])
    image = result["media"]["edges"][0]["node"]
    assert image["id"] == MID and image["image"]["width"] == 1550
    assert image["image"]["url"] == "https://cdn.shopify.com/files/CardPhoto.png?v=456"
    assert image["filename_hint"] == "Card Photo.png"
    assert image["local_filename"] is None and image["local_path"] is None
    assert image["page_order"] == 1 and image["order_cursor"] == "media-1"
    assert variant["media"]["nodes"][0]["id"] == image["id"]
    assert session.calls[0][1]["json"]["variables"] == {"id": PID, "variants": "v1", "media": "m1"}


def test_variant_association_continuation_and_unprocessed_media():
    media = media_page(True, "m2")
    node = media["edges"][0]["node"]
    node.update(status="PROCESSING", image=None, originalSource=None)
    session = session_for("productVariant", {"id": VID, "title": "Matt", "sku": "TLC-0001-1", "media": media})
    result = ShopifyAdapter().inspect_variant(SETTINGS, VID, "m1", session=session)
    assert result["media"]["pageInfo"]["hasNextPage"]
    observed = result["media"]["edges"][0]["node"]
    assert observed["filename_hint"] is None and observed["image"] is None
    assert session.calls[0][1]["json"]["query"] == VARIANT_QUERY


def test_collection_list_and_member_pagination():
    adapter = ShopifyAdapter()
    collection = {"id": CID, "title": "Cards", "handle": "cards", "description": "Cards", "sortOrder": "MANUAL",
                  "ruleSet": {"appliedDisjunctively": False}}
    assert adapter.discover_collections(SETTINGS, session=session_for("collections", page([collection])))["nodes"] == [collection]
    collection["products"] = page([{"id": PID, "title": "Card", "handle": "card", "status": "ACTIVE"}], True, "member2")
    session = session_for("collection", collection)
    result = adapter.inspect_collection(SETTINGS, CID, "member1", session=session)
    assert result["products"]["nodes"][0]["id"] == PID
    assert session.calls[0][1]["json"]["variables"] == {"id": CID, "after": "member1"}
    assert "parent" not in COLLECTION_QUERY


@pytest.mark.parametrize("method,key", [("discover_products", "products"), ("discover_collections", "collections")])
def test_empty_store_is_not_an_error(method, key):
    assert getattr(ShopifyAdapter(), method)(SETTINGS, session=session_for(key, page([]))) == page([])


@pytest.mark.parametrize("payload", [None, {}, {"nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": "next"}},
    page([{}] * 21), page([{}], True, "same"), {"nodes": [], "pageInfo": {"hasNextPage": "false"}}])
def test_incomplete_or_nonadvancing_pages_never_look_complete(payload):
    with pytest.raises(ShopifyError):
        ShopifyAdapter().discover_products(SETTINGS, "same", session=session_for("products", payload))


@pytest.mark.parametrize("method,resource", [("inspect_product", PID), ("inspect_variant", VID), ("inspect_collection", CID)])
def test_missing_resources_report_safely(method, resource):
    with pytest.raises(ShopifyError, match="missing or unavailable"):
        getattr(ShopifyAdapter(), method)(SETTINGS, resource, session=Session(Response({"data": {}})))


@pytest.mark.parametrize("payload,status,match", [
    ({"errors": [{"message": TOKEN, "extensions": {"code": "THROTTLED"}}]}, 200, "budget"),
    ({"secret": TOKEN}, 429, "rate limiting"),
    ({"errors": [{"message": TOKEN}]}, 200, "permissions"),
    ({}, 403, "authentication"), ({}, 500, "availability"), ({}, 302, "availability"),
])
def test_api_errors_and_throttling_are_redacted_no_retry(payload, status, match):
    session = Session(Response(payload, status))
    with pytest.raises(ShopifyError, match=match) as exc:
        ShopifyAdapter().discover_products(SETTINGS, session=session)
    assert TOKEN not in str(exc.value) and len(session.calls) == 1


@pytest.mark.parametrize("failure", [requests.Timeout(TOKEN), requests.ConnectionError(TOKEN)])
def test_discovery_transport_failure_redacted(failure):
    with pytest.raises(ShopifyError) as exc:
        ShopifyAdapter().discover_collections(SETTINGS, session=Session(error=failure))
    assert TOKEN not in str(exc.value)


def test_response_byte_and_time_limits(monkeypatch):
    oversized = Response()
    oversized.iter_content = lambda size: iter([b"x" * 1048577])
    with pytest.raises(ShopifyError, match="oversized"):
        ShopifyAdapter().discover_products(SETTINGS, session=Session(oversized))
    ticks = iter([0, 21])
    monkeypatch.setattr("app.shopify_connection.monotonic", lambda: next(ticks))
    with pytest.raises(ShopifyError, match="incomplete"):
        ShopifyAdapter().discover_products(SETTINGS, session=session_for("products", page([])))


def test_read_only_allowlist_and_invalid_inputs_never_make_requests():
    session = Session()
    adapter = ShopifyAdapter()
    for query in READ_QUERIES:
        assert query.startswith("query ") and "mutation" not in query
    with pytest.raises(ShopifyError): adapter._read(SETTINGS, "mutation { productDelete { id } }", session=session)
    with pytest.raises(ShopifyError): adapter.inspect_product(SETTINGS, "https://evil.test", session=session)
    with pytest.raises(ShopifyError): adapter.discover_products(SETTINGS, "x" * 1025, session=session)
    assert session.calls == []


def configure(app):
    with app.app_context():
        config.save_shopify({"domain": SETTINGS.domain, "token": TOKEN, "api_version": SETTINGS.api_version}, config.revision())


def test_ui_is_explicit_protected_and_isolated_from_woo(setup_app, monkeypatch):
    app, _ = setup_app
    assert app.test_client().get("/shopify/discovery").status_code == 401
    client = admin_client(app)
    configure(app)
    before = (Path(app.instance_path) / "commerce.json").read_bytes()
    session = session_for("products", page([]))
    monkeypatch.setattr("app.shopify_connection.requests.Session", lambda: session)
    assert client.get("/shopify/discovery").status_code == 200 and session.calls == []
    statements = []
    def observe(conn, cursor, statement, *args): statements.append(statement)
    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", observe)
        try:
            response = client.post("/shopify/discovery", data={"action": "products"})
        finally:
            event.remove(db.engine, "before_cursor_execute", observe)
        assert config.active_platform() == "woocommerce"
    assert response.status_code == 200 and b"No products found" in response.data
    assert response.headers["Cache-Control"] == "no-store" and TOKEN.encode() not in response.data
    assert (Path(app.instance_path) / "commerce.json").read_bytes() == before
    assert not any(s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in statements)
    assert len(session.calls) == 1
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/shopify/discovery", data={"action": "products"}).status_code == 400
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        from app.models import User
        db.session.get(User, 1).is_admin = False
        db.session.commit()
    assert client.get("/shopify/discovery").status_code == 403


@pytest.mark.parametrize("action,key,result", [("product", "product", product()),
    ("variant", "productVariant", {"id": VID, "title": "Matt", "sku": "TLC-0001-1", "media": media_page()}),
    ("collection", "collection", {"id": CID, "title": "Cards", "handle": "cards", "sortOrder": "MANUAL",
                                    "ruleSet": None, "products": page([])}),
    ("collections", "collections", page([]))])
def test_inspection_pages_render_and_escape_remote_html(setup_app, monkeypatch, action, key, result):
    app, _ = setup_app
    client = admin_client(app)
    configure(app)
    monkeypatch.setattr("app.shopify_connection.requests.Session", lambda: session_for(key, result))
    response = client.post("/shopify/discovery", data={"action": action, "resource_id": {"product": PID, "variant": VID, "collection": CID}.get(action)})
    assert response.status_code == 200 and TOKEN.encode() not in response.data
    if action == "product":
        assert b"&lt;script&gt;unsafe()&lt;/script&gt;" in response.data
        assert b"https://cdn.shopify.com/files/CardPhoto.png?v=456" in response.data
        assert b"More associations available" in response.data
