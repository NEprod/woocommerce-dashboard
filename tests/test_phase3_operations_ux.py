"""Read-only operational overview and scoped handoffs; no live Woo calls."""
import json
from datetime import UTC, datetime, timedelta

import pytest

from app import db
from app.models import CatalogueOperation, Product, WooProductIdentity
from app import woo_sync_workspace as ui
from app.woo_publish_preview import BUILDER_VERSION, cache_plan, store_identity
from test_phase3_woo_publish_preview import preview_app, _client, FakeWooClient, generate_publish_plan


def cached_action(action, *, stale=False):
    summary = {"store_identity": store_identity()["key"], "builder_version": BUILDER_VERSION,
               "generated_at": (datetime.now(UTC) - timedelta(minutes=20 if stale else 0)).isoformat(),
               "preview_digest": "reviewed", "scope": {"kind": "product", "product_id": 1}}
    db.session.add(CatalogueOperation(id="ui-plan", operation_type="woo_publish_preview", status="succeeded",
                                     scope=json.dumps({"operation_summary": summary})))
    db.session.commit()
    cache_plan({"operation_id": "ui-plan", "summary": summary, "products": [
        {"product_id": 1, "action": action, "blockers": ["Verify Material"] if action == "blocked" else []}]})


@pytest.mark.parametrize("action", ["create", "update", "no_change", "blocked", "link_candidate", "recovery_required"])
def test_current_preview_classifications_and_filters(preview_app, monkeypatch, action):
    monkeypatch.setattr(ui, "plan_is_stale", lambda plan: False)
    with preview_app.app_context():
        cached_action(action)
        data = ui.workspace({"status": action})
        assert [r["product"].id for r in data["rows"]] == [1]
        assert data["counts"][action] == 1
    page = _client(preview_app).get("/woo-sync", query_string={"status": action})
    assert page.status_code == 200
    assert b'product_ids' in page.data
    if action == "link_candidate":
        assert b'/woocommerce/preview/link/1?' in page.data
    if action == "blocked":
        assert b'Verify Material' in page.data and b'Review CREATE-1 metadata' in page.data


@pytest.mark.parametrize("expired,local_stale", [(True, False), (False, True)])
def test_old_preview_never_claims_no_change(preview_app, monkeypatch, expired, local_stale):
    monkeypatch.setattr(ui, "plan_is_stale", lambda plan: local_stale)
    with preview_app.app_context():
        cached_action("no_change", stale=expired)
        assert ui.overview()["counts"]["no_change"] == 0
        assert ui.overview()["counts"]["unknown"] == 4


def test_overview_performs_no_woo_discovery_and_scoped_handoffs(preview_app, monkeypatch):
    import requests
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **k: pytest.fail("Remote request from overview"))
    client = _client(preview_app)
    response = client.get("/woo-sync?collection=1&q=Create&type=simple")
    assert response.status_code == 200
    assert b'Review Collection' in response.data
    assert b'name="kind" value="collection"' in response.data
    assert b'name="product_id" value="1"' in response.data
    assert b'/woocommerce/preview/estimate' in response.data
    for scope in [{"kind": "product", "product_id": 1}, {"kind": "collection", "collection_id": 1},
                  {"kind": "selected", "product_ids": [1, 2]}]:
        assert client.post("/woocommerce/preview/estimate", data=scope).status_code == 200
    assert preview_app.test_client().get("/woo-sync").status_code in {302, 401}


def test_real_generated_preview_has_usable_freshness(preview_app):
    with preview_app.app_context():
        generate_publish_plan({"kind": "product", "product_id": 1}, client=FakeWooClient())
        assert ui.overview()["counts"]["create"] == 1
        product = db.session.get(Product, 1)
        product.title = "Changed after review"
        db.session.commit()
        assert ui.overview()["counts"]["create"] == 0


def test_current_store_identity_and_persisted_recovery(preview_app):
    with preview_app.app_context():
        product = db.session.get(Product, 1)
        db.session.add(WooProductIdentity(product_id=1, sku=product.sku, stable_identity=product.source_relpath or "product:1",
            store_key=store_identity()["key"], store_host="shop.example.test", woo_product_id=999, verification_state="verified"))
        db.session.add(CatalogueOperation(id="recover-ui", operation_type="woo_controlled_publish", status="failed",
            recovery_state="review_required", scope=json.dumps({"store_identity": store_identity()["key"], "product_ids": [1]})))
        db.session.commit()
        data = ui.overview()
        assert data["counts"]["linked"] == 1
        assert data["counts"]["recovery_required"] == 1
    assert b'/operations/recover-ui' in _client(preview_app).get("/woo-sync?status=recovery_required").data


def test_taxonomy_load_is_automatic_readonly_preview_and_manual_refresh_remains(preview_app):
    page = _client(preview_app).get("/taxonomy/sync")
    assert page.status_code == 200
    assert b'data-auto-preview' in page.data
    assert b'Refresh Woo data' in page.data
    assert b'action="/taxonomy/sync/preview"' in page.data
    assert b'csrf_token' in page.data


def test_navigation_uses_same_groups_and_nested_active_state(preview_app):
    page = _client(preview_app).get("/woo-sync")
    for name in [b'Catalogue', b'WooCommerce', b'Operations', b'System']:
        assert page.data.count(b'data-navigation-group="' + name + b'"') == 2
    assert page.data.count(b'href="/woo-sync" aria-current="page"') == 2


def test_unavailable_taxonomy_does_not_loop_automatic_preview(preview_app, monkeypatch):
    from app import taxonomy_routes
    def unavailable():
        raise taxonomy_routes.sync.SyncError("Woo unavailable; discovery incomplete")
    monkeypatch.setattr(taxonomy_routes.sync, "plan", unavailable)
    page = _client(preview_app).post("/taxonomy/sync/preview", data={"view": "categories"})
    assert page.status_code == 409
    assert b'Woo unavailable' in page.data and b'Refresh Woo data' in page.data
    assert b'data-auto-preview' not in page.data


def test_terminal_notification_uses_captured_data_without_transaction(preview_app, monkeypatch):
    from app.utils.operation_control import finish_catalogue_operation
    calls = []
    with preview_app.app_context():
        db.session.add(CatalogueOperation(id="notification-test", operation_type="taxonomy_registry_edit",
                                         status="running", scope="{}"))
        db.session.commit()
        def notified(kind, status, **kwargs):
            assert not db.session().in_transaction()
            calls.append((kind, status, kwargs))
        monkeypatch.setattr("app.utils.discord.notify_operation_attention", notified)
        finish_catalogue_operation("notification-test", status="failed", error="Registry changed; review again")
        assert len(calls) == 1
        assert calls[0][2]["operation_id"] == "notification-test"
        assert "Registry changed" in calls[0][2]["error"]


def test_restart_recovery_is_one_bounded_captured_notification(preview_app, monkeypatch):
    from app.utils.operation_control import recover_interrupted_operations
    calls = []
    with preview_app.app_context():
        for index in range(6):
            db.session.add(CatalogueOperation(id=f"interrupted-{index}", operation_type="append", status="running"))
        db.session.commit()
        def notified(kind, status, **kwargs):
            assert not db.session().in_transaction()
            calls.append(kwargs)
        monkeypatch.setattr("app.utils.discord.notify_operation_attention", notified)
        assert recover_interrupted_operations() == 6
        assert len(calls) == 1
        assert len(calls[0]["summary"]["items"]) == 5
        assert "6 interrupted operations" in calls[0]["summary"]["scope"]
        assert CatalogueOperation.query.filter_by(recovery_state="review_required").count() == 6


def test_dashboard_health_is_recorded_and_never_borrowed_from_another_host(preview_app):
    with preview_app.app_context():
        operation = CatalogueOperation(id="new-health", operation_type="woo_connection_test", status="succeeded",
            started_at=datetime.now(UTC) + timedelta(seconds=1), finished_at=datetime.now(UTC),
            scope=json.dumps({"operation_summary": {"state": "connected", "hostname": store_identity()["host"]}}))
        db.session.add(operation)
        db.session.commit()
        assert ui.overview()["health"]["label"] == "connected"
        assert ui.overview()["health"]["checked_at"] is not None
        operation.scope = json.dumps({"operation_summary": {"state": "connected", "hostname": "another.invalid"}})
        db.session.commit()
        assert ui.overview()["health"]["label"] == "review Integration health"
        assert ui.overview()["health"]["checked_at"] is None
