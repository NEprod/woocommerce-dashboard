"""Read-view reuse only: real source files, no remote calls or persistent caches."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from unittest.mock import patch

import pytest

from app import taxonomy_registry as registry
from test_metadata_editor_core import core
from test_taxonomy_registry import document


@pytest.fixture
def navigation(core, document, tmp_path):
    app, client, *_ = core
    root = (tmp_path / "taxonomy").resolve()
    root.mkdir()
    source = root / "registry.json"
    source.write_text(json.dumps(document))
    app.config["TAXONOMY_ROOT"] = str(root)
    return app, client, source, document


@pytest.mark.parametrize("path", ["/", "/api/edit_products"])
def test_page_uses_one_secure_registry_read(navigation, path):
    _, client, source, _ = navigation
    before = source.read_bytes()
    with patch.object(registry, "load_registry", wraps=registry.load_registry) as loads:
        response = client.get(path)
    assert response.status_code == 200
    assert loads.call_count == 1
    assert source.read_bytes() == before


def test_next_page_observes_changed_and_invalid_registry(navigation):
    _, client, source, data = navigation
    snapshots = []
    original = registry.load_registry

    def record(*args, **kwargs):
        result = original(*args, **kwargs)
        snapshots.append(result)
        return result

    with patch.object(registry, "load_registry", side_effect=record):
        assert client.get("/api/edit_products").status_code == 200
        data["tags"][0]["name"] = "Updated seasonal name"
        source.write_text(json.dumps(data))
        assert client.get("/api/edit_products").status_code == 200
        source.write_text("{")
        assert client.get("/api/edit_products").status_code == 200
    assert len(snapshots) == 3
    assert snapshots[0].snapshot.digest != snapshots[1].snapshot.digest
    assert snapshots[2].status == "invalid" and snapshots[2].snapshot is None


@pytest.mark.parametrize("path", ["/", "/api/edit_products"])
def test_registry_failure_is_reused_not_replaced_with_old_success(navigation, path):
    _, client, source, _ = navigation
    source.write_text("{")
    with patch.object(registry, "load_registry", wraps=registry.load_registry) as loads:
        assert client.get(path).status_code == 200
    assert loads.call_count == 1


def test_view_snapshot_is_immutable_and_discarded_after_view(navigation):
    app, _, source, _ = navigation

    @registry.registry_read_view
    def view():
        first = registry.load_configured_registry()
        with pytest.raises(TypeError):
            first.snapshot.data["tags"] = []
        source.write_text("{")
        assert registry.load_configured_registry() is first

    with app.test_request_context("/"):
        view()
        assert registry.load_configured_registry().status == "invalid"


@pytest.mark.parametrize("method", ["POST", "GET"])
def test_other_loads_and_writes_are_fresh(navigation, method):
    app, _, source, _ = navigation

    def read_twice():
        assert registry.load_configured_registry().available
        source.write_text("{")
        assert registry.load_configured_registry().status == "invalid"

    # Only an explicitly decorated GET builder may reuse a read result.
    function = registry.registry_read_view(read_twice) if method == "POST" else read_twice
    with app.test_request_context("/edit-products", method=method):
        function()


def test_exception_cleans_up_snapshot_and_nested_views_reuse(navigation):
    app, _, source, _ = navigation

    @registry.registry_read_view
    def inner():
        return registry.load_configured_registry()

    @registry.registry_read_view
    def outer():
        assert registry.load_configured_registry() is inner()
        raise RuntimeError("fixture failure")

    with app.test_request_context("/"):
        with patch.object(registry, "load_registry", wraps=registry.load_registry) as loads:
            with pytest.raises(RuntimeError, match="fixture failure"):
                outer()
            assert loads.call_count == 1
            source.write_text("{")
            assert registry.load_configured_registry().status == "invalid"
            assert loads.call_count == 2


def test_simultaneous_requests_do_not_share_snapshot(navigation):
    app, _, _, _ = navigation
    barrier = Barrier(2)

    @registry.registry_read_view
    def view():
        first = registry.load_configured_registry()
        barrier.wait(timeout=5)
        assert registry.load_configured_registry() is first
        return first

    def request():
        with app.test_request_context("/"):
            return view()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: request(), range(2)))
    assert results[0] is not results[1]
    assert results[0].snapshot.digest == results[1].snapshot.digest


def test_reuse_does_not_change_catalogue_payloads(navigation):
    app, client, _, _ = navigation
    from app.dashboard import build_dashboard_data
    from app.products_browser import build_products_data
    from app import routes

    before = {path: path.read_bytes() for path in core_json_paths(navigation)}
    with patch("app.shopify_drafts.now", return_value=datetime(2026, 10, 25, 12)):
        reused = client.get("/api/edit_products").get_json()
        with patch.object(routes, "build_products_data", build_products_data.__wrapped__):
            uncached = client.get("/api/edit_products").get_json()
    assert reused == uncached
    with patch("app.dashboard.datetime", wraps=datetime) as clock:
        clock.now.return_value = datetime(2026, 10, 25, 12, tzinfo=UTC)
        with app.test_request_context("/"):
            reused_dashboard = build_dashboard_data()
        with app.test_request_context("/"):
            fresh_dashboard = build_dashboard_data.__wrapped__()
    assert reused_dashboard == fresh_dashboard
    assert {path: path.read_bytes() for path in before} == before


def core_json_paths(navigation):
    from app import db
    from app.models import Settings
    from pathlib import Path
    with navigation[0].app_context():
        root = Path(db.session.query(Settings).first().product_folder)
        return [navigation[2], *root.rglob("*.json")]
