"""Fictional shops only; no real credentials, catalogue or network calls."""
import json
from pathlib import Path

import pytest
import requests
from sqlalchemy import event

from app import db
from app.models import User, Product
from app import commerce_config as config
from app.commerce import adapter, catalogue_snapshot
from app.shopify_connection import ShopifyAdapter, ShopifyConfiguration, ShopifyError, QUERY
from test_setup_flow import setup_app
from test_phase3_woo_publish_preview import preview_app
from test_m56_product_taxonomy import world

TOKEN = "fictional_admin_token_for_tests"


class Response:
    status_code = 200
    headers = {"X-Shopify-API-Version": "2026-10"}
    def __init__(self, payload=None, status=200):
        self.status_code = status
        self.payload = payload if payload is not None else {"data": {
            "shop": {"id": "gid://shopify/Shop/42", "myshopifyDomain": "fictional.myshopify.com"},
            "currentAppInstallation": {"accessScopes": [{"handle": "read_products"}]}}}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def iter_content(self, size): yield json.dumps(self.payload).encode()


class Session:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response or Response(), error, []
    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error: raise self.error
        return self.response
    def close(self): pass


@pytest.fixture(autouse=True)
def isolated_transport(monkeypatch):
    for key in config.ENV_KEYS: monkeypatch.delenv(key, raising=False)
    def forbidden(*a, **kw): raise AssertionError("Unexpected network call")
    monkeypatch.setattr(requests.Session, "request", forbidden)


def admin_client(app):
    with app.app_context():
        db.session.add(User(id=1, email="admin@example.test", username="admin", password="unused", is_admin=True))
        db.session.commit()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "1"; session["_fresh"] = True
    return client


def test_existing_installation_defaults_without_rewrite(setup_app):
    app, _ = setup_app
    client = admin_client(app)
    with app.app_context():
        assert config.active_platform() == "woocommerce"
        assert adapter().platform == "woocommerce"
        assert not (Path(app.instance_path) / "commerce.json").exists()
        with pytest.raises(config.CommerceConfigError): config.initialize_platform("shopify")
    assert client.get("/settings/commerce").status_code == 200


@pytest.mark.parametrize("platform", ["woocommerce", "shopify"])
def test_first_setup_selection_persists(setup_app, platform):
    app, _ = setup_app
    response = app.test_client().post("/setup", data={"email": "setup@example.com", "username": "admin",
        "password": "fictional-password", "commerce_platform": platform})
    assert response.status_code == 302
    with app.app_context():
        assert config.active_platform() == platform
        assert adapter().platform == platform
        assert config.shopify_settings() == {}  # No credentials needed to boot/select.
    # A separate app context reads persisted storage rather than process state.
    from flask import Flask
    restarted = Flask("restarted", instance_path=app.instance_path)
    with restarted.app_context(): assert config.active_platform() == platform
    assert (Path(app.instance_path) / "commerce.json").stat().st_mode & 0o777 == 0o600


def test_invalid_setup_platform_does_not_create_admin(setup_app):
    app, _ = setup_app
    app.test_client().post("/setup", data={"email": "setup@example.com", "username": "admin",
        "password": "fictional-password", "commerce_platform": "other"})
    with app.app_context(): assert User.query.count() == 0
    assert not (Path(app.instance_path) / "commerce.json").exists()


def test_shopify_does_not_execute_woo_routes(setup_app):
    app, _ = setup_app
    with app.app_context(): config.initialize_platform("shopify")
    client = admin_client(app)
    assert client.get("/woo-sync").location.endswith("/settings/commerce")
    assert client.post("/woo-sync/state/1").status_code == 409
    assert client.post("/taxonomy/sync/confirm").status_code == 409
    assert client.get("/settings/commerce").status_code == 200


def test_connection_only_fixed_query_and_secret_not_in_repr():
    session = Session()
    settings = ShopifyConfiguration("fictional.myshopify.com", TOKEN)
    result = ShopifyAdapter().test_connection(settings, session=session)
    assert TOKEN not in repr(settings) and TOKEN not in repr(result)
    assert result["products_readable"] and result["shop_id"] == "gid://shopify/Shop/42"
    assert len(session.calls) == 1
    url, kwargs = session.calls[0]
    assert url == "https://fictional.myshopify.com/admin/api/2026-10/graphql.json"
    assert kwargs["json"] == {"query": QUERY} and "mutation" not in QUERY
    assert kwargs["timeout"] == (5, 15) and kwargs["allow_redirects"] is False
    assert kwargs["headers"]["X-Shopify-Access-Token"] == TOKEN
    assert ShopifyAdapter.can_publish is False


@pytest.mark.parametrize("domain", ["localhost", "127.0.0.1", "https://fictional.myshopify.com", "fictional.myshopify.com:443",
    "fictional.myshopify.com.evil.test", "fictional.myshopify.com/path", "x@fictional.myshopify.com", "", None])
def test_invalid_domain_never_sends_credentials(domain):
    session = Session()
    with pytest.raises(ShopifyError): ShopifyAdapter().test_connection(ShopifyConfiguration(domain, TOKEN), session=session)
    assert not session.calls


@pytest.mark.parametrize("token,version", [("", "2026-10"), ("bad\r\nheader", "2026-10"), (TOKEN, "unstable"), (TOKEN, "2020-01")])
def test_invalid_credentials_version(token, version):
    with pytest.raises(ShopifyError): ShopifyConfiguration("fictional.myshopify.com", token, version).validated()


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_http_failures_are_safe_and_not_retried(status):
    session = Session(Response({"secret": TOKEN}, status))
    with pytest.raises(ShopifyError) as error:
        ShopifyAdapter().test_connection(ShopifyConfiguration("fictional.myshopify.com", TOKEN), session=session)
    assert TOKEN not in str(error.value) and len(session.calls) == 1


@pytest.mark.parametrize("failure", [requests.Timeout(TOKEN), requests.ConnectionError(TOKEN)])
def test_transport_errors_redacted(failure):
    with pytest.raises(ShopifyError) as error:
        ShopifyAdapter().test_connection(ShopifyConfiguration("fictional.myshopify.com", TOKEN), session=Session(error=failure))
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize("payload", [{"errors": [{"message": TOKEN}]}, {"data": None}, [], {"data": {"shop": {}}}])
def test_incomplete_graphql_evidence_never_succeeds(payload):
    with pytest.raises(ShopifyError) as error:
        ShopifyAdapter().test_connection(ShopifyConfiguration("fictional.myshopify.com", TOKEN), session=Session(Response(payload)))
    assert TOKEN not in str(error.value)


def test_version_and_identity_mismatch():
    response = Response(); response.headers = {"X-Shopify-API-Version": "2026-07"}
    with pytest.raises(ShopifyError):
        ShopifyAdapter().test_connection(ShopifyConfiguration("fictional.myshopify.com", TOKEN), session=Session(response))
    response = Response(); response.payload["data"]["shop"]["myshopifyDomain"] = "different.myshopify.com"
    with pytest.raises(ShopifyError):
        ShopifyAdapter().test_connection(ShopifyConfiguration("fictional.myshopify.com", TOKEN), session=Session(response))


def test_ui_test_save_secret_protection_and_no_activation(setup_app, monkeypatch):
    app, _ = setup_app
    client = admin_client(app)
    session = Session()
    monkeypatch.setattr("app.shopify_connection.requests.Session", lambda: session)
    with app.app_context(): revision = config.revision()
    data = {"domain": "fictional.myshopify.com", "token": TOKEN, "api_version": "2026-10", "revision": revision, "action": "test"}
    response = client.post("/settings/commerce", data=data)
    assert response.status_code == 200 and TOKEN.encode() not in response.data
    assert not (Path(app.instance_path) / "commerce.json").exists()
    data["action"] = "save"
    response = client.post("/settings/commerce", data=data)
    assert response.status_code == 200 and TOKEN.encode() not in response.data
    with app.app_context():
        assert config.active_platform() == "woocommerce"
        assert config.shopify_settings()["token"] == TOKEN
        fresh = config.revision()
    assert client.get("/settings/commerce").headers["Cache-Control"] == "no-store"
    assert TOKEN.encode() not in client.get("/settings/commerce").data
    before = (Path(app.instance_path) / "commerce.json").read_bytes()
    assert client.post("/settings/commerce", data=data).status_code == 400  # stale revision
    data.update(revision=fresh, active_platform="shopify")
    assert client.post("/settings/commerce", data=data).status_code == 400
    assert (Path(app.instance_path) / "commerce.json").read_bytes() == before


def test_failed_save_keeps_config_and_secret_private(setup_app, monkeypatch):
    app, _ = setup_app; client = admin_client(app)
    monkeypatch.setattr("app.shopify_connection.requests.Session", lambda: Session(Response(status=401)))
    with app.app_context(): revision = config.revision()
    result = client.post("/settings/commerce", data={"domain": "fictional.myshopify.com", "token": TOKEN,
        "api_version": "2026-10", "revision": revision, "action": "save"})
    assert result.status_code == 400 and TOKEN.encode() not in result.data
    assert not (Path(app.instance_path) / "commerce.json").exists()


def test_auth_admin_and_csrf(setup_app):
    app, _ = setup_app
    assert app.test_client().get("/settings/commerce").status_code == 401
    client = admin_client(app)
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/settings/commerce", data={"action": "save"}).status_code == 400
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        db.session.get(User, 1).is_admin = False; db.session.commit()
    assert client.get("/settings/commerce").status_code == 403


def test_environment_settings_are_not_overwritten(setup_app, monkeypatch):
    app, _ = setup_app; client = admin_client(app)
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "fictional.myshopify.com")
    monkeypatch.setenv("SHOPIFY_ADMIN_ACCESS_TOKEN", TOKEN)
    with app.app_context(): revision = config.revision()
    response = client.post("/settings/commerce", data={"revision": revision, "action": "save", "api_version": "2026-10"})
    assert response.status_code == 400 and TOKEN.encode() not in response.data
    assert not (Path(app.instance_path) / "commerce.json").exists()


def test_docker_configuration_precedence_redaction_and_fallback(setup_app, monkeypatch):
    app, _ = setup_app
    client = admin_client(app)
    saved = {"domain": "saved.myshopify.com", "token": "fictional_saved_token", "api_version": "2026-07"}
    with app.app_context():
        config.save_shopify(saved, config.revision())
        revision = config.revision()
    path = Path(app.instance_path) / "commerce.json"
    original = path.read_bytes()
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "fictional.myshopify.com")
    monkeypatch.setenv("SHOPIFY_ADMIN_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("SHOPIFY_API_VERSION", "2026-10")
    session = Session()
    monkeypatch.setattr("app.shopify_connection.requests.Session", lambda: session)
    with app.app_context():
        assert config.shopify_settings() == {"domain": "fictional.myshopify.com", "token": TOKEN, "api_version": "2026-10"}
        assert config.active_platform() == "woocommerce"
    page = client.get("/settings/commerce")
    assert page.status_code == 200
    for value in (TOKEN, "fictional.myshopify.com", saved["token"]): assert value.encode() not in page.data
    response = client.post("/settings/commerce", data={"action": "test", "revision": revision, "api_version": "2026-10"})
    assert response.status_code == 200 and b"deployment-configured store" in response.data
    assert TOKEN.encode() not in response.data and b"fictional.myshopify.com" not in response.data
    assert session.calls[0][1]["headers"]["X-Shopify-Access-Token"] == TOKEN
    discovery = client.post("/shopify/discovery", data={"action": "test"})
    assert discovery.status_code == 200
    assert TOKEN.encode() not in discovery.data and b"fictional.myshopify.com" not in discovery.data
    response = client.post("/settings/commerce", data={"action": "save", "revision": revision, "api_version": "2026-10"})
    assert response.status_code == 400 and path.read_bytes() == original and len(session.calls) == 2
    # Partial deployment configuration must never mix with saved credentials.
    monkeypatch.delenv("SHOPIFY_ADMIN_ACCESS_TOKEN")
    with app.app_context(): assert config.shopify_settings()["token"] == ""
    for key in config.ENV_KEYS: monkeypatch.setenv(key, "")
    with app.app_context():
        assert config.shopify_settings() == saved and config.active_platform() == "woocommerce"
    assert path.read_bytes() == original


def test_docker_examples_expose_optional_shopify_configuration():
    import xml.etree.ElementTree as ET
    root = Path(__file__).resolve().parents[1]
    compose = (root / "compose.yaml").read_text()
    env = (root / ".env.example").read_text()
    template = ET.parse(root / "unraid/my-woocommerce-dashboard.xml").getroot()
    entries = {entry.get("Target"): entry for entry in template.findall("Config")}
    for key in config.ENV_KEYS:
        assert f'{key}: "${{{key}:-}}"' in compose and f"{key}=" in env
        assert key in entries and entries[key].get("Default") == ""
    assert entries["SHOPIFY_ADMIN_ACCESS_TOKEN"].get("Mask") == "true"
    assert entries["SHOPIFY_CLIENT_SECRET"].get("Mask") == "true"
    assert entries["SHOPIFY_CLIENT_ID"].get("Mask") == "true"


def test_storage_failure_is_actionable_and_does_not_change_selection(setup_app, monkeypatch):
    app, _ = setup_app
    with app.app_context():
        revision = config.revision()
        def denied(*args, **kwargs):
            raise PermissionError("fictional storage failure")
        monkeypatch.setattr(config, "atomic_write_json", denied)
        with pytest.raises(config.CommerceConfigError, match="storage permissions"):
            config.save_shopify({"domain": "fictional.myshopify.com", "token": TOKEN}, revision)
        assert config.active_platform() == "woocommerce"
    assert not (Path(app.instance_path) / "commerce.json").exists()


@pytest.mark.parametrize("unsafe", ["symlink", "corrupt", "permissions", "invalid_fields"])
def test_unsafe_config_never_falls_back_or_overwrites(setup_app, unsafe):
    app, _ = setup_app
    path = Path(app.instance_path) / "commerce.json"
    if unsafe == "symlink":
        target = path.parent / "other.json"; target.write_text("unchanged"); path.symlink_to(target)
    else:
        path.write_text("bad" if unsafe == "corrupt" else json.dumps({"version": 1, "active_platform": "shopify",
            "shopify": {"domain": []} if unsafe == "invalid_fields" else {}}))
        path.chmod(0o644 if unsafe == "permissions" else 0o600)
    with app.app_context():
        with pytest.raises(config.CommerceConfigError): config.active_platform()
    if unsafe == "symlink": assert target.read_text() == "unchanged"


@pytest.mark.parametrize("variable", [False, True])
def test_snapshot_is_deeply_immutable_local_and_no_writes(preview_app, monkeypatch, tmp_path, variable):
    with preview_app.app_context():
        pid, _, source, *_ = world(preview_app, monkeypatch, tmp_path, variable=variable)
        product = db.session.get(Product, pid)
        before = source.read_bytes()
        identities = [(v.id, v.sku) for v in product.variations]
        statements = []
        def observe(conn, cursor, statement, *args): statements.append(statement)
        event.listen(db.engine, "before_cursor_execute", observe)
        try:
            snapshot = catalogue_snapshot(product)
            assert snapshot == catalogue_snapshot(product)
            assert snapshot.sku == product.sku
            assert ("variation_attributes" in snapshot.data["authored"])
            assert bool(snapshot.data["authored"]["variation_attributes"]) == variable
            with pytest.raises(TypeError): snapshot.data["title"] = "changed"
            with pytest.raises(TypeError): snapshot.data["authored"]["attributes"]["Occasion"] = ()
        finally: event.remove(db.engine, "before_cursor_execute", observe)
        assert not any(s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in statements)
        assert source.read_bytes() == before and identities == [(v.id, v.sku) for v in product.variations]
        assert "woo_id" not in repr(snapshot.data)
