"""Fictional GraphQL revision checks; refresh must never mutate Shopify."""
import json
from datetime import timedelta

import pytest

from app import db, shopify_drafts as drafts, shopify_status as status_service
from app.models import ShopifyProductComparison
from app.shopify_connection import ShopifyAdapter, ShopifyError, REVISIONS_QUERY
from app.shopify_products import states_for
from test_shopify_drafts import world
from test_shopify_taxonomy_products import TaxonomyAdapter
from test_shopify_simple_updates import change
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_foundation import isolated_transport, Session, Response


class RevisionAdapter(TaxonomyAdapter):
    def lifecycle_product(self, config, product_id):
        # A missing revision alone cannot prove absence: simulate unavailable
        # independent existence evidence, which must keep the identity protected.
        raise ShopifyError("Exact existence read unavailable; absence not established.")

    def product_revisions(self, config, ids):
        self.calls.append("revision_read")
        return {i: {"id": i, "updatedAt": self.remote.get("updatedAt", "2026-10-12T00:00:00Z"), "status": self.remote["status"]} if self.remote else None for i in ids}


def created(world):
    adapter = RevisionAdapter()
    _, token = drafts.preview(world[0], adapter)
    row = drafts.confirm(token, True, adapter)
    assert row.state == "verified"
    adapter.calls.clear()
    return row, adapter


def age(minutes, *fields):
    row = ShopifyProductComparison.query.one()
    data = json.loads(row.summary_json)
    for field in fields:
        data[field] = (drafts.now() - timedelta(minutes=minutes)).isoformat()
    row.summary_json = json.dumps(data)
    db.session.commit()


def displayed(product):
    from flask import current_app
    with current_app.test_request_context():
        return states_for([product])[product.id]


def test_cached_page_navigation_performs_no_duplicate_read(world):
    row, adapter = created(world)
    for _ in range(3):
        result = status_service.refresh([world[0]], adapter=adapter)
        assert result["full_comparisons"] == 0
    assert adapter.calls == [] and displayed(world[0])["label"] == "Up to Date"


def test_unchanged_batched_revision_renews_without_full_fetch(world):
    row, adapter = created(world)
    original = ShopifyProductComparison.query.one().compared_at
    age(11, "revision_checked_at", "related_checked_at")
    result = status_service.refresh([world[0]], adapter=adapter)
    assert result["full_comparisons"] == 0 and adapter.calls == ["connection_read", "revision_read"]
    assert displayed(world[0])["label"] == "Up to Date"
    assert ShopifyProductComparison.query.one().compared_at == original


@pytest.mark.parametrize("kind", ["local", "remote", "both", "media_interval"])
def test_affected_product_gets_canonical_full_comparison(world, kind):
    row, adapter = created(world)
    age(11, "revision_checked_at")
    if kind in {"local", "both"}:
        change(world, weight=51)
        assert displayed(world[0])["label"] == "Local Changes Detected"
    if kind in {"remote", "both"}:
        adapter.remote["updatedAt"] = "2026-10-13T02:00:00Z"
        adapter.remote["title"] = "Changed remotely"
    if kind == "media_interval":
        age(31, "full_compared_at")
    result = status_service.refresh([world[0]], adapter=adapter)
    assert result["full_comparisons"] == 1 and "product_read" in adapter.calls
    assert not any(c in adapter.calls for c in ["product_create", "product_update", "collection_join", "collection_create"])


def test_missing_remote_is_not_create_and_failures_back_off(world):
    row, adapter = created(world)
    age(11, "revision_checked_at")
    adapter.remote = None
    result = status_service.refresh([world[0]], adapter=adapter)
    assert result["errors"] and row.state == "verified"
    assert displayed(world[0])["label"] == "Verification Unavailable"
    calls = adapter.calls[:]
    status_service.refresh([world[0]], adapter=adapter)
    assert adapter.calls == calls


def test_verified_absence_is_cached_but_create_still_rechecks(world):
    adapter = RevisionAdapter()
    result = status_service.refresh([world[0]], adapter=adapter)
    assert result["full_comparisons"] == 1 and displayed(world[0])["label"] == "Needs Creating"
    calls = adapter.calls[:]
    status_service.refresh([world[0]], adapter=adapter)
    assert adapter.calls == calls
    age(31, "absence_checked_at")
    assert displayed(world[0])["label"] == "Not yet verified"
    status_service.refresh([world[0]], adapter=adapter)
    assert len(adapter.calls) > len(calls)


def test_manual_force_and_genuine_recovery_precedence(world):
    row, adapter = created(world)
    assert status_service.refresh([world[0]], force=True, adapter=adapter)["full_comparisons"] == 1
    row.state, row.verification_state = "uncertain", "unverified"
    db.session.commit()
    adapter.calls.clear()
    status_service.refresh([world[0]], force=True, adapter=adapter)
    assert adapter.calls == [] and displayed(world[0])["label"] == "Recovery Required"


def test_batched_revision_transport_bound_and_response_identity(world, isolated_transport):
    config = drafts.configuration()
    session = Session(Response({"data": {"nodes": [{"id": "gid://shopify/Product/100", "updatedAt": "2026-10-13T02:00:00Z", "status": "DRAFT"}]}}))
    import unittest.mock
    with unittest.mock.patch("app.shopify_connection.requests.Session", return_value=session):
        assert ShopifyAdapter().product_revisions(config, ["gid://shopify/Product/100"])
    assert session.calls[0][1]["json"]["query"] == REVISIONS_QUERY
    assert session.calls[0][1]["timeout"] == (5, 15)
    with pytest.raises(ShopifyError):
        ShopifyAdapter().product_revisions(config, ["gid://shopify/Product/100"] * 26)


def test_refresh_route_auth_csrf_and_limits(world, monkeypatch):
    from flask import current_app
    client = _client(current_app._get_current_object())
    monkeypatch.setattr(status_service, "refresh", lambda products, **kw: {"checked": [], "pending": [], "errors": []})
    assert client.post("/api/shopify/status", json={"ids": [world[0].id]}).status_code == 200
    assert client.post("/api/shopify/status", json={"ids": list(range(1, 27))}).status_code == 400
    current_app.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/api/shopify/status", json={"ids": [world[0].id]}).status_code == 400


def test_related_membership_is_checked_independently_of_product_revision(world, monkeypatch):
    row, adapter = created(world)
    age(11, "revision_checked_at", "related_checked_at")
    calls = []
    def membership(*args):
        calls.append(True)
        return ([{"key": "categories:cards", "name": "Cards", "action": "Join", "mapping_fingerprint": None}], [], None)
    monkeypatch.setattr("app.shopify_taxonomy.membership_plan", membership)
    result = status_service.refresh([world[0]], adapter=adapter)
    assert calls and result["full_comparisons"] == 0
    assert json.loads(ShopifyProductComparison.query.one().summary_json)["classification"] == "update"
    assert "product_read" not in adapter.calls


def test_step_budget_and_unknown_product_backoff(world, monkeypatch):
    from app.models import Product
    # Budget is exercised independently of expensive canonical comparison fixtures.
    products = [Product(id=i, product_type="simple", catalogue_status="active", source_relpath=f"source-{i}", sku=f"SKU-{i}") for i in range(100, 105)]
    calls = []
    monkeypatch.setattr(drafts, "preview", lambda p, *a, **kw: calls.append(p.id))
    result = status_service.refresh(products, adapter=RevisionAdapter())
    assert calls == [100, 101] and result["pending"] == [102, 103, 104]


def test_revision_failure_backs_off_and_preserves_equality_timestamp(world):
    row, adapter = created(world)
    age(11, "revision_checked_at")
    before = json.loads(ShopifyProductComparison.query.one().summary_json)["equality_at"]
    def failure(*args):
        raise ShopifyError("Shopify is rate limiting reads. Wait before retrying.")
    adapter.product_revisions = failure
    status_service.refresh([world[0]], adapter=adapter)
    current = json.loads(ShopifyProductComparison.query.one().summary_json)
    assert current["equality_at"] == before and current["retry_after"]
    previous_calls = adapter.calls[:]
    status_service.refresh([world[0]], adapter=adapter)
    assert adapter.calls == previous_calls


def test_cached_revision_never_gets_a_manufactured_new_check_timestamp(world):
    row, adapter = created(world)
    age(11, "related_checked_at")
    age(9, "revision_checked_at")
    saved = ShopifyProductComparison.query.one()
    data = json.loads(saved.summary_json)
    observed_at = data["revision_checked_at"]
    data.update(observed_revision_at=observed_at, observed_status="DRAFT", observed_updated_at=data["remote_updated_at"])
    saved.summary_json = json.dumps(data)
    db.session.commit()
    status_service.refresh([world[0]], adapter=adapter)
    assert adapter.calls == ["connection_read"]
    assert json.loads(saved.summary_json)["revision_checked_at"] == observed_at
