"""Client Credentials uses fictional stores and mocked HTTP only."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from time import sleep

import pytest
import requests

from app import commerce_config, shopify_connection as connection, shopify_drafts as drafts
from app.models import ShopifyProductIdentity
from test_shopify_foundation import Response, Session, isolated_transport, setup_app, admin_client
from test_shopify_drafts import world, FakeDraftAdapter
from test_phase3_woo_publish_preview import preview_app

CLIENT = "fictional_client_id_123456"
SECRET = "fictional_client_secret_123456"
ACCESS = "fictional_short_lived_token_123456"


def config():
    return connection.ShopifyConfiguration("fictional.myshopify.com", client_id=CLIENT, client_secret=SECRET).validated()


def token_response(expiry=86399):
    return Response({"access_token": ACCESS, "expires_in": expiry, "scope": "read_products,write_products"})


@pytest.fixture(autouse=True)
def clean_cache():
    connection._tokens.clear()
    yield
    connection._tokens.clear()


def test_acquisition_endpoint_form_cache_and_expiry(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(connection, "monotonic", lambda: clock[0])
    session = Session(token_response())
    monkeypatch.setattr(connection.requests, "Session", lambda: session)
    settings = config()
    assert connection._access_token(settings) == ACCESS
    url, kwargs = session.calls[0]
    assert url == "https://fictional.myshopify.com/admin/oauth/access_token"
    assert kwargs["data"] == {"grant_type": "client_credentials", "client_id": CLIENT, "client_secret": SECRET}
    assert kwargs["timeout"] == (5, 15) and kwargs["allow_redirects"] is False
    assert session.trust_env is False and "X-Shopify-Access-Token" not in kwargs["headers"]
    assert SECRET not in repr(settings) and CLIENT not in repr(settings)
    clock[0] += 86338
    assert connection._access_token(settings) == ACCESS and len(session.calls) == 1
    clock[0] += 1  # Renew sixty seconds before returned expiry.
    assert connection._access_token(settings) == ACCESS and len(session.calls) == 2


def test_concurrent_acquisitions_are_coalesced(monkeypatch):
    class SlowSession(Session):
        def post(self, *args, **kwargs):
            sleep(0.02)
            return super().post(*args, **kwargs)
    session = SlowSession(token_response())
    monkeypatch.setattr(connection.requests, "Session", lambda: session)
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(lambda _: connection._access_token(config()), range(8))) == [ACCESS] * 8
    assert len(session.calls) == 1


@pytest.mark.parametrize("failure", [401, 403, 429, 500, requests.Timeout(SECRET), requests.ConnectionError(SECRET)])
def test_failures_are_redacted_not_retried_and_cooled_down(monkeypatch, failure):
    session = Session(Response({"error_description": SECRET}, failure)) if isinstance(failure, int) else Session(error=failure)
    monkeypatch.setattr(connection.requests, "Session", lambda: session)
    for _ in range(2):
        with pytest.raises(connection.ShopifyError) as error:
            connection._access_token(config())
        assert SECRET not in str(error.value) and CLIENT not in str(error.value)
    assert len(session.calls) == 1


@pytest.mark.parametrize("response", [
    {}, {"access_token": ACCESS, "scope": "read_products"},
    {"access_token": ACCESS, "expires_in": 0, "scope": "read_products"},
    {"access_token": ACCESS, "expires_in": True, "scope": "read_products"},
    {"access_token": ACCESS, "expires_in": "86399", "scope": "read_products"},
    {"access_token": SECRET + "\n", "expires_in": 86399, "scope": "read_products"},
])
def test_incomplete_token_evidence_never_cached(monkeypatch, response):
    monkeypatch.setattr(connection.requests, "Session", lambda: Session(Response(response)))
    with pytest.raises(connection.ShopifyError): connection._access_token(config())
    assert all("token" not in value for value in connection._tokens.values())


@pytest.mark.parametrize("changes", [
    {"client_secret": ""}, {"client_id": ""}, {"token": ACCESS},
    {"domain": "https://evil.example"}, {"client_secret": SECRET + "\r\n"},
])
def test_conflicting_incomplete_or_invalid_credentials_refused(changes):
    from dataclasses import replace
    with pytest.raises(connection.ShopifyError): replace(config(), **changes).validated()


def test_manual_token_bypasses_oauth_and_preserves_fingerprint(monkeypatch):
    def forbidden(): raise AssertionError("OAuth must not run")
    monkeypatch.setattr(connection.requests, "Session", forbidden)
    settings = connection.ShopifyConfiguration("fictional.myshopify.com", ACCESS).validated()
    assert connection._access_token(settings) == ACCESS
    assert settings.fingerprint() == drafts.digest({"domain": settings.domain, "token": ACCESS, "version": settings.api_version})


def test_cache_is_store_and_credential_scoped_and_bounded(monkeypatch):
    from dataclasses import replace
    session = Session(token_response())
    monkeypatch.setattr(connection.requests, "Session", lambda: session)
    for i in range(18):
        connection._access_token(replace(config(), domain=f"fictional-{i}.myshopify.com"))
    assert len(connection._tokens) == 16 and len(session.calls) == 18
    connection._access_token(replace(config(), client_secret=SECRET + "rotated"))
    assert len(session.calls) == 19


def test_configuration_never_mixes_saved_and_deployment_auth(setup_app, monkeypatch):
    app, _ = setup_app
    with app.app_context():
        commerce_config.save_shopify({"domain": "saved.myshopify.com", "token": ACCESS}, commerce_config.revision())
        monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "fictional.myshopify.com")
        monkeypatch.setenv("SHOPIFY_CLIENT_ID", CLIENT)
        settings = commerce_config.shopify_settings()
        assert settings["token"] == "" and settings["client_secret"] == ""
        with pytest.raises(connection.ShopifyError): connection.ShopifyConfiguration.from_settings(settings).validated()
        monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", SECRET)
        assert connection.ShopifyConfiguration.from_settings(commerce_config.shopify_settings()).validated().client_id == CLIENT
        monkeypatch.setenv("SHOPIFY_ADMIN_ACCESS_TOKEN", ACCESS)
        with pytest.raises(connection.ShopifyError, match="Conflicting"):
            connection.ShopifyConfiguration.from_settings(commerce_config.shopify_settings()).validated()
        for key in commerce_config.ENV_KEYS: monkeypatch.delenv(key, raising=False)
        assert commerce_config.shopify_settings()["token"] == ACCESS


def test_oversized_token_response_redacted_and_not_retained(monkeypatch):
    class HugeResponse(Response):
        def iter_content(self, size): yield SECRET.encode() * 5000
    session = Session(HugeResponse())
    monkeypatch.setattr(connection.requests, "Session", lambda: session)
    with pytest.raises(connection.ShopifyError) as exc: connection._access_token(config())
    assert SECRET not in str(exc.value) and len(session.calls) == 1
    assert all("token" not in value for value in connection._tokens.values())


def test_expired_cache_loss_and_revocation_do_not_retry_mutations(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(connection, "monotonic", lambda: clock[0])
    session = Session(token_response())
    monkeypatch.setattr(connection.requests, "Session", lambda: session)
    connection._access_token(config())
    session.response = Response(status=401)
    with pytest.raises(connection.ShopifyError): connection.ShopifyAdapter().create_draft(config(), {}, [])
    assert len(session.calls) == 2
    session.response = token_response()
    with pytest.raises(connection.ShopifyError): connection._access_token(config())
    assert len(session.calls) == 2
    clock[0] += 31
    assert connection._access_token(config()) == ACCESS and len(session.calls) == 3
    connection._tokens.clear()  # Restart only discards cached tokens, not credentials.
    connection._access_token(config())
    assert len(session.calls) == 4


def test_environment_ui_uses_client_pair_without_redisplay_or_save(setup_app, monkeypatch):
    app, _ = setup_app
    client = admin_client(app)
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "fictional.myshopify.com")
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", CLIENT)
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", SECRET)
    calls = []
    class RoutedSession(Session):
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return token_response() if url.endswith("access_token") else Response()
    monkeypatch.setattr(connection.requests, "Session", RoutedSession)
    with app.app_context(): revision = commerce_config.revision()
    page = client.get("/settings/commerce")
    assert page.status_code == 200 and calls == []
    assert b"Client Credentials configured" in page.data
    page = client.post("/settings/commerce", data={"action": "test", "revision": revision, "api_version": "2026-10"})
    assert page.status_code == 200 and len(calls) == 2
    for value in (CLIENT, SECRET, ACCESS): assert value.encode() not in page.data
    page = client.post("/settings/commerce", data={"action": "save", "revision": revision, "api_version": "2026-10"})
    assert page.status_code == 400 and len(calls) == 2
    with app.app_context(): assert commerce_config.active_platform() == "woocommerce"


def test_client_authentication_covers_all_graphql_and_binary_boundaries(monkeypatch):
    calls = []
    class RoutedSession(Session):
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return token_response() if url.endswith("access_token") else Response({"data": {}})
    monkeypatch.setattr(connection.requests, "Session", RoutedSession)
    adapter = connection.ShopifyAdapter()
    for query in connection.READ_QUERIES | connection.WRITE_QUERIES:
        adapter._transport(config(), query, {})
    assert sum(url.endswith("access_token") for url, _ in calls) == 1
    for url, kwargs in calls:
        if url.endswith("graphql.json"): assert kwargs["headers"]["X-Shopify-Access-Token"] == ACCESS
    adapter.upload_image(config(), {"url": "https://storage.googleapis.com/upload",
        "resourceUrl": "https://storage.googleapis.com/source", "parameters": []},
        {"filename": "original.png", "mime": "image/png"}, b"original")
    assert "headers" not in calls[-1][1] and calls[-1][1]["files"]["file"][1] == b"original"


def test_token_rotation_during_review_preserves_full_draft_safety(world, monkeypatch):
    product, _, _ = world
    monkeypatch.delenv("SHOPIFY_ADMIN_ACCESS_TOKEN")
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", CLIENT)
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", SECRET)
    clock, oauth = [100.0], []
    monkeypatch.setattr(connection, "monotonic", lambda: clock[0])
    fake = FakeDraftAdapter()
    settings = config()
    class RoutedSession(Session):
        def post(self, url, **kwargs):
            if url.endswith("access_token"):
                oauth.append(url)
                payload = token_response(120)
                payload.payload["access_token"] = ACCESS + str(len(oauth))
                return payload
            if "files" in kwargs: return Response(status=204)
            query, variables = kwargs["json"]["query"], kwargs["json"].get("variables", {})
            if query == connection.QUERY:
                data = {"shop": {"id": "gid://shopify/Shop/42", "myshopifyDomain": settings.domain},
                        "currentAppInstallation": {"accessScopes": [{"handle": "read_products"}, {"handle": "write_products"}]}}
            elif query == connection.STAGE_QUERY:
                images = [dict(filename=i["filename"], mime=i["mimeType"], size=int(i["fileSize"])) for i in variables["input"]]
                data = fake.stage_images(settings, images)
            elif query == connection.CREATE_QUERY:
                data = fake.create_draft(settings, variables["product"], variables["media"])
            elif query == connection.PRICE_QUERY:
                variant = variables["variants"][0]
                data = fake.set_simple_variant(settings, variables["id"], variant["id"], variant["inventoryItem"]["sku"], variant["price"])
            else: data = {"product": copy.deepcopy(fake.remote)}
            return Response({"data": data})
    monkeypatch.setattr(connection.requests, "Session", RoutedSession)
    _, reviewed = drafts.preview(product)
    clock[0] += 109
    row = drafts.confirm(reviewed, True)
    assert row.state == "verified" and len(oauth) == 2
    assert fake.calls.count("product_create") == 1
    for value in (CLIENT, SECRET, ACCESS): assert value not in row.media_evidence_json
    assert ShopifyProductIdentity.query.count() == 1 and product.woo_id is None
    drafts.reconcile(row)
    assert row.state == "verified" and len(oauth) == 2
