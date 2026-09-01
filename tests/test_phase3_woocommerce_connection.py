import json
from pathlib import Path

import pytest
import requests

from app import create_app, db
from app.models import CatalogueOperation, Settings, User
from app.utils.operation_control import reset_operation_control_for_tests
from app.utils.redaction import redact_diagnostic
from app.woocommerce_connection import (
    MAX_DISCOVERY_INDEX_BYTES,
    MAX_RESPONSE_BYTES,
    PublisherWooClient,
    ReadOnlyWooClient,
    STREAM_CHUNK_BYTES,
    WooConfiguration,
    WooConnectionError,
    execute_connection_test,
    normalize_store_url,
    run_connection_discovery,
)
from config import Config


ROOT = Path(__file__).resolve().parents[1]
KEY = "ck_" + "k" * 40
SECRET = "cs_" + "s" * 40


class FakeResponse:
    def __init__(self, status=200, payload=None, *, headers=None, raw=None):
        self.status_code = status
        self.headers = headers or {}
        self.encoding = "utf-8"
        self._raw = raw if raw is not None else json.dumps(payload).encode()
        self.closed = False
        self.raw = type("RawCounter", (), {"tell": lambda counter: int(self.headers.get("X-Compressed-Bytes", len(self._raw)))})()

    def iter_content(self, chunk_size=65536):
        for offset in range(0, len(self._raw), chunk_size):
            yield self._raw[offset:offset + chunk_size]

    def close(self):
        self.closed = True


def _rest_index():
    route = lambda methods: {"endpoints": [{"methods": methods, "args": {"consumer_secret": {"description": "must never render"}}}]}
    return {
        "name": "Fictional Shop", "home": "https://shop.example.test/",
        "namespaces": ["wp/v2", "wc/v1", "wc/v3", "unrelated/v9"],
        "routes": {
            "/wc/v3/products": route(["GET", "POST"]),
            "/wc/v3/products/(?P<product_id>[\\d]+)/variations": route(["GET", "POST", "PUT", "DELETE"]),
            "/wc/v3/products/categories": route(["GET", "POST"]),
            "/wc/v3/products/tags": route(["GET", "POST"]),
            "/wc/v3/products/attributes": route(["GET", "POST"]),
            "/wc/v3/products/attributes/(?P<attribute_id>[\\d]+)/terms": route(["GET", "POST"]),
            "/wp/v2/media": route(["GET", "POST", "DELETE"]),
            "/wc/v3/orders": route(["GET", "POST"]),
            "/wc/v3/customers": route(["GET", "POST"]),
            "/wc/v3/system_status": route(["GET"]),
        },
    }


def _large_index_bytes(size, *, include_routes=True):
    document = _rest_index() if include_routes else {"namespaces": ["wc/v3"], "routes": {}}
    document["routes"]["/plugin-heavy/v1/private/(?P<token>.*)"] = {
        "endpoints": [{"methods": ["GET", "POST"], "args": {"secret": "must-not-persist"}}]
    }
    document["plugin_padding"] = ""
    baseline = json.dumps(document, separators=(",", ":")).encode()
    document["plugin_padding"] = "x" * max(0, size - len(baseline))
    raw = json.dumps(document, separators=(",", ":")).encode()
    assert len(raw) == size
    return raw


class FakeSession:
    def __init__(self, *, optional_forbidden=False, optional_statuses=None, required_status=None, index=None):
        self.calls = []
        self.optional_forbidden = optional_forbidden
        self.optional_statuses = optional_statuses or {}
        self.required_status = required_status
        self.index = index

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("/wp-json/"):
            return FakeResponse(payload=self.index if self.index is not None else _rest_index())
        if self.required_status and any(value in url for value in ("/products?", "/products/categories?", "/products/tags?", "/products/attributes?")):
            return FakeResponse(status=self.required_status, payload={"code": "controlled"})
        if self.optional_forbidden and any(value in url for value in ("/orders?", "/customers?")):
            return FakeResponse(status=403, payload={"code": "forbidden"})
        for resource, status in self.optional_statuses.items():
            if f"/{resource}?" in url:
                return FakeResponse(status=status, payload={"code": "controlled", "secret": SECRET})
        if "/products?" in url:
            return FakeResponse(payload=[{"id": 44, "name": "Do not persist me"}])
        if "/products/attributes?" in url:
            return FakeResponse(payload=[{"id": 8}])
        if "/system_status?" in url:
            return FakeResponse(payload={"environment": {"version": "9.9", "wp_version": "6.8", "site_timezone": "Europe/London"}, "settings": {"currency": "GBP"}})
        return FakeResponse(payload=[])


@pytest.fixture
def woo_app(tmp_path, monkeypatch):
    instance = tmp_path / "instance"
    catalogue = tmp_path / "catalogue"
    output = tmp_path / "output"
    for path in (instance, catalogue, output):
        path.mkdir()
    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{instance / 'site.db'}"
    monkeypatch.setenv("WOO_STORE_URL", "https://shop.example.test/wp-json/")
    monkeypatch.setenv("WOO_CONSUMER_KEY", KEY)
    monkeypatch.setenv("WOO_CONSUMER_SECRET", SECRET)
    try:
        app = create_app()
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with app.app_context():
            db.session.add(User(email="woo@example.test", username="woo-admin", password="unused"))
            db.session.add(Settings(product_folder=str(catalogue), output_folder=str(output), url_prefix="https://uploads.invalid/"))
            db.session.commit()
        yield app
    finally:
        with app.app_context():
            db.session.remove()
        reset_operation_control_for_tests()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


def _client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True
    return client


@pytest.mark.parametrize("value,expected", [
    ("https://shop.example.test/", "https://shop.example.test"),
    ("https://SHOP.example.test/base/wp-json/wc/v3", "https://shop.example.test/base"),
])
def test_store_url_normalisation(value, expected):
    assert normalize_store_url(value) == expected


@pytest.mark.parametrize("value,category", [
    ("http://shop.example.test", "invalid_scheme"),
    ("ftp://shop.example.test", "invalid_scheme"),
    ("https://user:password@shop.example.test", "embedded_credentials"),
    ("https://", "invalid_url"),
    ("https://shop.example.test/?consumer_secret=value", "invalid_url"),
])
def test_store_url_rejects_unsafe_values(value, category):
    with pytest.raises(WooConnectionError) as caught:
        normalize_store_url(value)
    assert caught.value.category == category


def test_read_only_client_rejects_mutating_methods():
    client = ReadOnlyWooClient(WooConfiguration("https://shop.example.test", KEY, SECRET), session=FakeSession())
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        with pytest.raises(WooConnectionError, match="read-only"):
            client.request_json(method, "https://shop.example.test/wp-json/wc/v3/products", authenticated=True)
    assert client.request_count == 0


def test_cross_origin_redirect_is_blocked_without_forwarding_credentials():
    class RedirectSession:
        def __init__(self): self.calls = []
        def request(self, method, url, **kwargs):
            self.calls.append((url, kwargs))
            return FakeResponse(302, {}, headers={"Location": "https://attacker.invalid/steal"})
    session = RedirectSession()
    client = ReadOnlyWooClient(WooConfiguration("https://shop.example.test", KEY, SECRET), session=session)
    with pytest.raises(WooConnectionError) as caught:
        client.request_json("GET", "https://shop.example.test/private", authenticated=True)
    assert caught.value.category == "cross_origin_redirect"
    assert len(session.calls) == 1


def test_same_origin_redirect_is_followed_with_a_strict_limit():
    class RedirectSession:
        def __init__(self): self.count = 0
        def request(self, method, url, **kwargs):
            self.count += 1
            return FakeResponse(302, {}, headers={"Location": "/next"}) if self.count == 1 else FakeResponse(payload={"ok": True})
    session = RedirectSession()
    client = ReadOnlyWooClient(WooConfiguration("https://shop.example.test", KEY, SECRET), session=session)
    assert client.request_json("GET", "https://shop.example.test/start", authenticated=True)[0] == {"ok": True}
    assert session.count == 2


def test_publisher_refuses_mutating_redirect_without_replaying_credentials():
    class RedirectSession:
        def __init__(self): self.calls = []
        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return FakeResponse(307, {}, headers={"Location": "/wp-json/wc/v3/products/9"})

    session = RedirectSession()
    client = PublisherWooClient(WooConfiguration("https://shop.example.test", KEY, SECRET), session=session)
    with pytest.raises(WooConnectionError) as caught:
        client.request_json(
            "POST",
            "https://shop.example.test/wp-json/wc/v3/products",
            authenticated=True,
            json_body={"sku": "FICTIONAL-1"},
        )
    assert caught.value.category == "write_redirect_refused"
    assert len(session.calls) == 1


def test_response_size_and_malformed_json_are_controlled():
    class ResponseSession:
        def __init__(self, response): self.response = response
        def request(self, *args, **kwargs): return self.response
    config = WooConfiguration("https://shop.example.test", KEY, SECRET)
    with pytest.raises(WooConnectionError) as large:
        ReadOnlyWooClient(config, session=ResponseSession(FakeResponse(raw=b"x" * (MAX_RESPONSE_BYTES + 1)))).request_json("GET", "https://shop.example.test/data")
    assert large.value.category == "response_too_large"
    with pytest.raises(WooConnectionError) as malformed:
        ReadOnlyWooClient(config, session=ResponseSession(FakeResponse(raw=b"not-json"))).request_json("GET", "https://shop.example.test/data")
    assert malformed.value.category == "malformed_json"


def test_publisher_retains_only_bounded_structured_woo_error_fields():
    class ResponseSession:
        def __init__(self, response): self.response = response
        def request(self, *args, **kwargs): return self.response

    payload = {
        "code": "woocommerce_rest_invalid_product",
        "message": "Invalid parameter(s): regular_price",
        "data": {
            "status": 400,
            "params": {"regular_price": "regular_price must be numeric"},
            "details": {"regular_price": {"code": "rest_invalid_param", "message": "Expected a decimal string"}},
            "raw": {"secret": SECRET},
        },
        "consumer_secret": SECRET,
        "response": {"unbounded": "must not persist"},
    }
    client = PublisherWooClient(
        WooConfiguration("https://shop.example.test", KEY, SECRET),
        session=ResponseSession(FakeResponse(status=400, payload=payload)),
    )
    with pytest.raises(WooConnectionError) as caught:
        client.request_json(
            "POST", "https://shop.example.test/wp-json/wc/v3/products",
            authenticated=True, json_body={"sku": "SAFE-1"},
        )
    error = caught.value
    assert error.category == "bad_request" and error.status_code == 400
    assert error.remote_error == {
        "code": "woocommerce_rest_invalid_product",
        "message": "Invalid parameter(s): regular_price",
        "status": 400,
        "params": {"regular_price": "regular_price must be numeric"},
        "details": {"regular_price": "Expected a decimal string"},
    }
    encoded = json.dumps(error.remote_error)
    assert SECRET not in encoded and "consumer_secret" not in encoded and "response" not in encoded


@pytest.mark.parametrize("raw", [b"not-json", b"x" * (MAX_RESPONSE_BYTES + 1)])
def test_http_400_without_usable_bounded_json_remains_confirmed_not_uncertain(raw):
    class ResponseSession:
        def request(self, *args, **kwargs): return FakeResponse(status=400, raw=raw)
    client = PublisherWooClient(
        WooConfiguration("https://shop.example.test", KEY, SECRET), session=ResponseSession()
    )
    with pytest.raises(WooConnectionError) as caught:
        client.request_json(
            "POST", "https://shop.example.test/wp-json/wc/v3/products",
            authenticated=True, json_body={"sku": "SAFE-1"},
        )
    assert caught.value.category == "bad_request"
    assert caught.value.status_code == 400
    assert caught.value.remote_error == {}


def test_plugin_heavy_index_uses_larger_endpoint_specific_limit():
    raw = _large_index_bytes(MAX_RESPONSE_BYTES + 512 * 1024)
    response = FakeResponse(raw=raw, headers={"Content-Encoding": "gzip", "X-Compressed-Bytes": "145000"})
    class ResponseSession:
        def request(self, *args, **kwargs): return response
    client = ReadOnlyWooClient(WooConfiguration("https://shop.example.test", KEY, SECRET), session=ResponseSession())
    payload, _ = client.request_json(
        "GET", "https://shop.example.test/wp-json/",
        response_limit=MAX_DISCOVERY_INDEX_BYTES,
        endpoint_category="wordpress_rest_index",
    )
    assert payload["namespaces"][-1] == "unrelated/v9"
    assert client.last_transfer["decompressed_bytes"] == len(raw)
    assert client.last_transfer["compressed_bytes"] == 145000
    assert client.last_transfer["content_encoding"] == "gzip"
    assert response.closed is True


def test_index_just_below_limit_succeeds_and_above_limit_closes_cleanly():
    class ResponseSession:
        def __init__(self, response): self.response = response
        def request(self, *args, **kwargs): return self.response
    config = WooConfiguration("https://shop.example.test", KEY, SECRET)
    permitted = FakeResponse(raw=_large_index_bytes(MAX_DISCOVERY_INDEX_BYTES - 256))
    ReadOnlyWooClient(config, session=ResponseSession(permitted)).request_json(
        "GET", "https://shop.example.test/wp-json/",
        response_limit=MAX_DISCOVERY_INDEX_BYTES,
        endpoint_category="wordpress_rest_index",
    )
    assert permitted.closed is True
    oversized = FakeResponse(raw=_large_index_bytes(MAX_DISCOVERY_INDEX_BYTES + 1))
    with pytest.raises(WooConnectionError) as caught:
        ReadOnlyWooClient(config, session=ResponseSession(oversized)).request_json(
            "GET", "https://shop.example.test/wp-json/",
            response_limit=MAX_DISCOVERY_INDEX_BYTES,
            endpoint_category="wordpress_rest_index",
        )
    assert caught.value.category == "discovery_index_too_large"
    assert "WordPress REST API index" in caught.value.message
    assert "credentials" in caught.value.message
    assert caught.value.diagnostics["configured_limit"] == MAX_DISCOVERY_INDEX_BYTES
    assert caught.value.diagnostics["decompressed_bytes"] > MAX_DISCOVERY_INDEX_BYTES
    assert caught.value.diagnostics["failure_phase"] == "streaming"
    assert oversized.closed is True


@pytest.mark.parametrize("headers", [
    {},
    {"Content-Length": "12"},
    {"Transfer-Encoding": "chunked"},
    {"Content-Encoding": "gzip", "Content-Length": "8192", "X-Compressed-Bytes": "8192"},
])
def test_decompressed_index_bytes_cannot_bypass_limit(headers):
    class ResponseSession:
        def __init__(self, response): self.response = response
        def request(self, *args, **kwargs): return self.response
    response = FakeResponse(raw=_large_index_bytes(MAX_DISCOVERY_INDEX_BYTES + 65536), headers=headers)
    client = ReadOnlyWooClient(WooConfiguration("https://shop.example.test", KEY, SECRET), session=ResponseSession(response))
    with pytest.raises(WooConnectionError) as caught:
        client.request_json(
            "GET", "https://shop.example.test/wp-json/",
            response_limit=MAX_DISCOVERY_INDEX_BYTES,
            endpoint_category="wordpress_rest_index",
        )
    assert caught.value.category == "discovery_index_too_large"
    assert caught.value.category != "authentication_rejected"
    assert response.closed is True


def test_ordinary_api_response_keeps_one_mib_limit():
    class ResponseSession:
        def __init__(self, response): self.response = response
        def request(self, *args, **kwargs): return self.response
    response = FakeResponse(raw=json.dumps({"payload": "x" * (MAX_RESPONSE_BYTES + 1)}).encode())
    client = ReadOnlyWooClient(WooConfiguration("https://shop.example.test", KEY, SECRET), session=ResponseSession(response))
    with pytest.raises(WooConnectionError) as caught:
        client.request_json("GET", "https://shop.example.test/wp-json/wc/v3/products?per_page=1", authenticated=True)
    assert caught.value.category == "response_too_large"
    assert caught.value.diagnostics["configured_limit"] == MAX_RESPONSE_BYTES


def test_large_permitted_index_persists_only_bounded_relevant_summary():
    session = FakeSession(index=json.loads(_large_index_bytes(MAX_RESPONSE_BYTES + 256 * 1024)))
    result = run_connection_discovery(WooConfiguration("https://shop.example.test", KEY, SECRET), session=session)
    serialized = json.dumps(result)
    assert result["selected_namespace"] == "wc/v3"
    assert len(result["capabilities"]) == 10
    assert "plugin-heavy" not in serialized
    assert "must-not-persist" not in serialized
    assert "plugin_padding" not in serialized
    assert result["discovery_transfer"]["configured_limit"] == MAX_DISCOVERY_INDEX_BYTES


def test_oversized_index_operation_reports_safe_discovery_diagnostics(woo_app, monkeypatch):
    from app import woocommerce_connection
    response = FakeResponse(
        raw=_large_index_bytes(MAX_DISCOVERY_INDEX_BYTES + STREAM_CHUNK_BYTES),
        headers={"Content-Encoding": "gzip", "Content-Length": "4096", "X-Compressed-Bytes": "4096"},
    )
    class OversizedSession:
        def __init__(self): self.calls = []
        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return response
    session = OversizedSession()
    discord = []
    monkeypatch.setattr(woocommerce_connection, "notify_woo_connection_failed", lambda summary, **kwargs: discord.append(summary) or (True, "sent"))
    with woo_app.app_context():
        operation_id = execute_connection_test(session=session)
        row = db.session.get(CatalogueOperation, operation_id)
        assert row.status == "failed"
        assert "WordPress REST API index" in row.error
        assert "invalid credential" not in row.error.lower()
        assert "plugin_padding" not in row.scope
        assert KEY not in row.scope and SECRET not in row.scope
        summary = json.loads(row.scope)["operation_summary"]
        assert summary["failure_category"] == "discovery_index_too_large"
        assert summary["transfer_diagnostics"]["endpoint_category"] == "wordpress_rest_index"
        assert summary["transfer_diagnostics"]["content_encoding"] == "gzip"
    html = _client(woo_app).get(f"/operations/{operation_id}").get_data(as_text=True)
    assert "safe discovery limit" in html
    assert "No credentials were exposed" in html
    assert KEY not in html and SECRET not in html
    assert len(session.calls) == 1 and session.calls[0][0] == "GET"
    assert len(discord) == 1
    assert KEY not in json.dumps(discord) and SECRET not in json.dumps(discord)


def test_discovery_verifies_required_reads_and_never_mutates():
    session = FakeSession()
    result = run_connection_discovery(WooConfiguration("https://shop.example.test", KEY, SECRET), session=session)
    assert result["state"] in {"connected", "connected_with_limitations"}
    assert result["selected_namespace"] == "wc/v3"
    assert result["required_verified"] == result["required_total"] == 4
    assert result["wordpress_version"] == "6.8"
    assert result["woocommerce_version"] == "9.9"
    products = next(item for item in result["capabilities"] if item["name"] == "Products")
    assert products["read_state"] == "Read access verified"
    assert "POST" in products["advertised_write_methods"]
    assert products["write_permission"] == "Not verified"
    assert {method for method, _, _ in session.calls} == {"GET"}
    assert all(call[2]["auth"] == (KEY, SECRET) for call in session.calls[1:])
    assert session.calls[0][2]["auth"] is None


def test_optional_forbidden_resources_are_limitations_not_required_failure():
    result = run_connection_discovery(WooConfiguration("https://shop.example.test", KEY, SECRET), session=FakeSession(optional_forbidden=True))
    assert result["state"] == "connected_with_limitations"
    assert result["optional_limitations"] >= 2
    assert next(item for item in result["capabilities"] if item["name"] == "Orders")["read_state"] == "Forbidden"


def test_one_optional_forbidden_capability_creates_bounded_structured_finding():
    session = FakeSession(optional_statuses={"customers": 403})
    result = run_connection_discovery(WooConfiguration("https://shop.example.test", KEY, SECRET), session=session)
    assert result["state"] == "connected_with_limitations"
    assert result["required_verified"] == result["required_total"] == 4
    assert result["optional_limitations"] == len(result["limitation_findings"]) == 1
    finding = result["limitation_findings"][0]
    assert finding == {
        "key": "customers",
        "label": "Customers",
        "requirement": "future_optional",
        "route_discovered": True,
        "read_status": "forbidden",
        "http_status": 403,
        "severity": "warning",
        "continuation_allowed": True,
        "current_impact": "Does not affect product publishing.",
        "future_impact": "Required only for future customer features.",
        "recommendation": "No action required for current Phase 3 publishing work.",
        "explanation": "Read access returned HTTP 403.",
    }
    serialized = json.dumps(result)
    assert SECRET not in serialized
    assert "controlled" not in serialized
    assert "headers" not in serialized.lower()


@pytest.mark.parametrize("resource,status,read_status", [
    ("customers", 404, "not_exposed"),
    ("orders", 503, "unavailable"),
])
def test_optional_unavailable_capability_has_accurate_future_impact(resource, status, read_status):
    result = run_connection_discovery(
        WooConfiguration("https://shop.example.test", KEY, SECRET),
        session=FakeSession(optional_statuses={resource: status}),
    )
    finding = result["limitation_findings"][0]
    assert finding["read_status"] == read_status
    assert finding["continuation_allowed"] is True
    assert "Does not affect product publishing." == finding["current_impact"]
    assert f"future {resource[:-1]} features" in finding["future_impact"]


def test_successful_capabilities_have_classification_without_warning_findings():
    result = run_connection_discovery(WooConfiguration("https://shop.example.test", KEY, SECRET), session=FakeSession())
    products = next(item for item in result["capabilities"] if item["key"] == "products")
    variations = next(item for item in result["capabilities"] if item["key"] == "product_variations")
    assert products["requirement"] == "required_product_publishing"
    assert products["read_status"] == "verified"
    assert variations["requirement"] == "later_variation_publishing"
    assert result["limitation_findings"] == []


def test_capability_findings_and_logs_persist_and_render_without_generic_fallback(woo_app, monkeypatch):
    from app import woocommerce_connection
    notifications = []
    monkeypatch.setattr(
        woocommerce_connection,
        "notify_woo_connection_completed",
        lambda summary, **kwargs: notifications.append(summary) or (True, "sent"),
    )
    with woo_app.app_context():
        operation_id = execute_connection_test(session=FakeSession(optional_statuses={"customers": 403}))
        row = db.session.get(CatalogueOperation, operation_id)
        persisted = json.loads(row.scope)["operation_summary"]
        assert row.status == "partial"
        assert persisted["optional_limitations"] == 1
        assert persisted["limitation_findings"][0]["label"] == "Customers"
        assert persisted["limitation_findings_truncated"] == 0
        assert SECRET not in row.scope
    client = _client(woo_app)
    operation_html = client.get(f"/operations/{operation_id}").get_data(as_text=True)
    workspace_html = client.get("/woocommerce").get_data(as_text=True)
    combined = operation_html + workspace_html
    assert "Completed with limitations" in operation_html
    assert "Customers" in combined and "Forbidden (403)" in combined
    assert "Does not affect product publishing." in combined
    assert "Required only for future customer features." in combined
    assert "No action required for current Phase 3 publishing work." in combined
    assert "Additional bounded operation warnings" not in operation_html
    assert "Detailed capability findings were not retained" not in operation_html
    logs = client.get(f"/api/operations/{operation_id}/logs?after=0").get_json()
    assert any("Optional capability limited: Customers" in entry["line"] for entry in logs["entries"])
    assert len(notifications) == 1
    assert notifications[0]["limitation_findings"][0]["label"] == "Customers"
    assert KEY not in combined and SECRET not in combined


def test_historical_woo_warning_uses_controlled_detail_fallback(woo_app):
    with woo_app.app_context():
        row = CatalogueOperation(
            id="b" * 32,
            operation_type="woo_connection_test",
            status="partial",
            scope=json.dumps({"operation_summary": {"state": "connected_with_limitations", "optional_limitations": 1}}),
        )
        db.session.add(row)
        db.session.commit()
        operation_id = row.id
    html = _client(woo_app).get(f"/operations/{operation_id}").get_data(as_text=True)
    assert "Detailed capability findings were not retained for this earlier operation." in html
    assert "Additional bounded operation warnings" not in html


def test_multiple_limitations_are_bounded_with_accurate_truncation(monkeypatch):
    from app import woocommerce_connection
    monkeypatch.setattr(woocommerce_connection, "MAX_LIMITATION_FINDINGS", 2)
    result = run_connection_discovery(
        WooConfiguration("https://shop.example.test", KEY, SECRET),
        session=FakeSession(optional_statuses={"orders": 403, "customers": 403, "system_status": 404}),
    )
    assert result["optional_limitations"] == 3
    assert len(result["limitation_findings"]) == 2
    assert result["limitation_findings_truncated"] == 1


def test_discord_uses_one_bounded_grouped_limitation_summary(monkeypatch):
    from app import woocommerce_connection
    from app.utils import discord
    sent = []
    monkeypatch.setattr(discord, "send_discord_message", lambda **payload: sent.append(payload) or (True, "sent"))
    result = run_connection_discovery(
        WooConfiguration("https://shop.example.test", KEY, SECRET),
        session=FakeSession(optional_statuses={"customers": 403}),
    )
    assert discord.notify_woo_connection_completed(result, operation_id="fictional-operation") == (True, "sent")
    assert len(sent) == 1
    serialized = json.dumps(sent)
    assert "Customers" in serialized and "Forbidden (403)" in serialized
    assert "Does not affect product publishing." in serialized
    assert KEY not in serialized and SECRET not in serialized


def test_light_detail_cards_reset_inherited_dark_panel_foreground():
    css = (ROOT / "app" / "static" / "assets" / "css" / "custom.css").read_text()
    rule = next(line for line in css.splitlines() if line.startswith(".collection-card-metrics > div, .detail-definition-grid > div"))
    assert "color: var(--color-text-primary)" in rule


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 502, 503, 504])
def test_required_resource_http_failures_are_controlled(status):
    with pytest.raises(WooConnectionError) as caught:
        run_connection_discovery(WooConfiguration("https://shop.example.test", KEY, SECRET), session=FakeSession(required_status=status))
    assert caught.value.category == "required_capability_failed"
    assert KEY not in caught.value.message and SECRET not in caught.value.message


def test_wordpress_without_woo_namespace_is_controlled():
    index = _rest_index()
    index["namespaces"] = ["wp/v2", "custom/v1"]
    with pytest.raises(WooConnectionError) as caught:
        run_connection_discovery(WooConfiguration("https://shop.example.test", KEY, SECRET), session=FakeSession(index=index))
    assert caught.value.category == "woo_namespace_absent"


@pytest.mark.parametrize("exception,category", [
    (requests.ConnectTimeout(), "connect_timeout"),
    (requests.ReadTimeout(), "read_timeout"),
    (requests.exceptions.SSLError(), "tls_failure"),
    (requests.ConnectionError("DNS name resolution failed"), "dns_failure"),
    (requests.ConnectionError("connection refused"), "connection_failed"),
])
def test_transport_failures_are_controlled(exception, category):
    class FailingSession:
        def request(self, *args, **kwargs): raise exception
    client = ReadOnlyWooClient(WooConfiguration("https://shop.example.test", KEY, SECRET), session=FailingSession())
    with pytest.raises(WooConnectionError) as caught:
        client.request_json("GET", "https://shop.example.test/wp-json/")
    assert caught.value.category == category


def test_workspace_requires_authentication_and_page_view_never_contacts_store(woo_app, monkeypatch):
    monkeypatch.setattr(requests, "Session", lambda: (_ for _ in ()).throw(AssertionError("page view contacted store")))
    assert woo_app.test_client().get("/woocommerce").status_code in {302, 401}
    html = _client(woo_app).get("/woocommerce").get_data(as_text=True)
    assert "WooCommerce Connection" in html
    assert "Test Connection" in html
    assert "Read-only discovery" in html
    assert KEY not in html and SECRET not in html
    assert 'aria-label="Primary navigation"' in html
    assert 'aria-current="page"' in html
    assert "API credentials" in html


def test_not_configured_workspace_gives_environment_setup_guidance(woo_app, monkeypatch):
    for name in ("WOO_STORE_URL", "WOO_CONSUMER_KEY", "WOO_CONSUMER_SECRET"):
        monkeypatch.delenv(name, raising=False)
    html = _client(woo_app).get("/woocommerce").get_data(as_text=True)
    assert "Not configured" in html
    assert "Configure the runtime environment" in html
    assert all(name in html for name in ("WOO_STORE_URL", "WOO_CONSUMER_KEY", "WOO_CONSUMER_SECRET"))
    assert 'disabled aria-disabled="true"' in html


def test_connection_action_records_one_bounded_operation_and_safe_history(woo_app, monkeypatch):
    from app import woocommerce_connection
    session = FakeSession(optional_forbidden=True)
    monkeypatch.setattr(woocommerce_connection.requests, "Session", lambda: session)
    response = _client(woo_app).post("/woocommerce/test", follow_redirects=False)
    assert response.status_code == 302
    with woo_app.app_context():
        rows = CatalogueOperation.query.filter_by(operation_type="woo_connection_test").all()
        assert len(rows) == 1
        row = rows[0]
        assert row.status == "partial"
        assert response.headers["Location"].endswith(f"/operations/{row.id}")
        persisted = row.scope
        assert KEY not in persisted and SECRET not in persisted
        assert "routes" not in persisted and "consumer_secret" not in persisted
    html = _client(woo_app).get(response.headers["Location"]).get_data(as_text=True)
    assert "Connection health" in html
    assert "No mutating request was issued" in html
    assert KEY not in html and SECRET not in html
    status_json = _client(woo_app).get(f"/api/operations/{row.id}/status").get_data(as_text=True)
    assert KEY not in status_json and SECRET not in status_json


def test_existing_running_operation_blocks_duplicate_connection_test(woo_app):
    with woo_app.app_context():
        existing = CatalogueOperation(id="a" * 32, operation_type="woo_connection_test", status="running", scope="{}")
        db.session.add(existing)
        db.session.commit()
    response = _client(woo_app).post("/woocommerce/test", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/operations/" + "a" * 32)
    with woo_app.app_context():
        assert CatalogueOperation.query.filter_by(operation_type="woo_connection_test").count() == 1


def test_success_and_failure_operation_details_are_safe(woo_app, monkeypatch):
    from app import woocommerce_connection
    notifications = []
    monkeypatch.setattr(woocommerce_connection, "notify_woo_connection_completed", lambda summary, **kwargs: notifications.append(("success", summary)) or (True, "sent"))
    monkeypatch.setattr(woocommerce_connection, "notify_woo_connection_failed", lambda summary, **kwargs: notifications.append(("failure", summary)) or (True, "sent"))
    with woo_app.app_context():
        success_id = execute_connection_test(session=FakeSession())
        failure_id = execute_connection_test(session=FakeSession(required_status=401))
        assert db.session.get(CatalogueOperation, success_id).status == "succeeded"
        assert db.session.get(CatalogueOperation, failure_id).status == "failed"
    success_html = _client(woo_app).get(f"/operations/{success_id}").get_data(as_text=True)
    failure_html = _client(woo_app).get(f"/operations/{failure_id}").get_data(as_text=True)
    assert "Connection health" in success_html and "Read access" not in success_html
    assert "Required Capability Failed" in failure_html
    assert "No mutating request was issued" in success_html
    assert KEY not in success_html + failure_html and SECRET not in success_html + failure_html
    assert [kind for kind, _ in notifications] == ["success", "failure"]
    assert KEY not in json.dumps(notifications) and SECRET not in json.dumps(notifications)


def test_capability_workspace_is_bounded_expandable_and_contains_no_raw_index(woo_app):
    with woo_app.app_context():
        execute_connection_test(session=FakeSession())
    html = _client(woo_app).get("/woocommerce").get_data(as_text=True)
    assert "API capabilities" in html
    assert "Read access verified" in html
    assert "Credential write permission" in html and "Not verified" in html
    assert "Write methods advertised" not in html
    assert "Advertised methods" in html
    assert "consumer_secret" not in html and "must never render" not in html
    assert html.count("<tr>") <= 12


def test_transport_policy_uses_tls_bounded_timeouts_and_minimal_pages():
    session = FakeSession()
    run_connection_discovery(WooConfiguration("https://shop.example.test", KEY, SECRET), session=session)
    assert len(session.calls) <= 11
    for method, url, kwargs in session.calls:
        assert method == "GET"
        assert kwargs["verify"] is True
        assert kwargs["timeout"] == (3.05, 8)
        assert kwargs["allow_redirects"] is False
        if not url.endswith("/wp-json/"):
            assert "per_page=1" in url


def test_woo_workspace_responsive_and_accessible_structure_is_shared(woo_app):
    html = _client(woo_app).get("/woocommerce").get_data(as_text=True)
    css = (ROOT / "app" / "static" / "assets" / "css" / "custom.css").read_text()
    assert html.count("<h1") == 1
    assert html.count("<main") == 1
    assert 'aria-label="WooCommerce connection overview"' in html
    assert 'aria-label="Primary navigation"' in html
    assert 'aria-label="Mobile primary navigation"' in html
    assert "@media (max-width: 640px)" in css
    assert "overflow-x: auto" in css


def test_missing_configuration_starts_application_and_records_controlled_failure(woo_app, monkeypatch):
    monkeypatch.delenv("WOO_CONSUMER_SECRET")
    operation_id = None
    with woo_app.app_context():
        operation_id = execute_connection_test()
        row = db.session.get(CatalogueOperation, operation_id)
        assert row.status == "failed"
        assert "must be configured" in row.error


def test_woocommerce_credentials_are_redacted_from_diagnostics():
    value = f"Authorization: Basic abc Consumer_key={KEY} https://x.test/?consumer_secret={SECRET}&oauth_signature=signed ck_1234567890 cs_1234567890"
    redacted = redact_diagnostic(value)
    assert KEY not in redacted and SECRET not in redacted
    assert "Basic abc" not in redacted
    assert "oauth_signature=signed" not in redacted
    assert "ck_1234567890" not in redacted and "cs_1234567890" not in redacted


def test_settings_and_unraid_are_safe_and_environment_only(woo_app):
    html = _client(woo_app).get("/settings").get_data(as_text=True)
    assert "WooCommerce connection" in html
    assert "Runtime environment" in html
    assert "Woo writes" in html and "Disabled for this milestone" in html
    assert KEY not in html and SECRET not in html
    xml = (ROOT / "unraid" / "my-woocommerce-dashboard.xml").read_text()
    for variable in ("WOO_STORE_URL", "WOO_CONSUMER_KEY", "WOO_CONSUMER_SECRET"):
        assert f'Target="{variable}"' in xml
    assert xml.count('Mask="true"') >= 3
    assert KEY not in xml and SECRET not in xml
