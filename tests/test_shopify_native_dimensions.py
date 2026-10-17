import copy
import json
from decimal import Decimal

import pytest
from flask import current_app
from app import db, shopify_native_dimensions as native, shopify_drafts as drafts
from app.models import CatalogueOperation
from app.shopify_products import states_for
from app.shopify_connection import ShopifyAdapter, ShopifyConfiguration, ShopifyError, NATIVE_CAPABILITY, NATIVE_READ, NATIVE_WRITE, SIMPLE_QUERY
from test_shopify_drafts import world
from test_shopify_simple_updates import UpdateAdapter, change
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_foundation import Response, Session, TOKEN, isolated_transport


class NativeAdapter(UpdateAdapter):
    def __init__(self):
        super().__init__()
        self.packed = {"length": 0, "width": 0, "height": 0, "unit": "CENTIMETERS"}
        self.permission = True
        self.capable = True
        self.native_error = None
        self.writes = []

    def test_connection(self, config):
        result = super().test_connection(config)
        if self.permission:
            result["scopes"].append("write_inventory")
        return result

    def packed_capability(self, config):
        assert config.api_version == "2026-10"
        self.calls.append("native_schema_read")
        return {"measurement": {"fields": [{"name": "packedDimensions"}] if self.capable else []},
            "measurementInput": {"inputFields": [{"name": "packedDimensions"}]},
            "dimensionsInput": {"inputFields": [{"name": k} for k in ("length", "width", "height", "unit")]},
            "units": {"enumValues": [{"name": k} for k in native.FACTORS]}}

    def read_packed(self, config, product_id):
        self.calls.append("native_read")
        item = self.remote["simpleVariants"]["nodes"][0]
        return {"id": product_id, "status": self.remote["status"], "metafield": self.remote["metafield"],
            "variants": {"nodes": [{"id": item["id"], "sku": item["sku"], "inventoryItem": {
                "id": item["inventoryItem"]["id"], "measurement": {"packedDimensions": copy.deepcopy(self.packed)}}}],
                "pageInfo": {"hasNextPage": False}}}

    def write_packed(self, config, inventory_id, dimensions):
        row = drafts.ShopifyProductIdentity.query.one()
        assert drafts.evidence(row)["native_dimensions"]["state"] == "pending"
        assert inventory_id == "gid://shopify/InventoryItem/250"
        self.calls.append("native_write")
        self.writes.append(copy.deepcopy(dimensions))
        if self.native_error == "rejected":
            return {"inventoryItemUpdate": {"inventoryItem": None, "userErrors": [{"message": "rejected"}]}}
        if self.native_error != "before":
            self.packed = copy.deepcopy(dimensions)
        if self.native_error in {"before", "after"}:
            raise ShopifyError("Native write response timed out; reconcile before retry.")
        return {"inventoryItemUpdate": {"inventoryItem": {"id": inventory_id}, "userErrors": []}}


def enable(monkeypatch):
    monkeypatch.setenv("SHOPIFY_NATIVE_DIMENSIONS_ENABLED", "true")
    monkeypatch.setenv("SHOPIFY_PACKED_DIMENSIONS_USE_PRODUCT_DIMENSIONS", "true")


def product(world, monkeypatch):
    change(world, dimensions={"length": "155", "width": "155", "height": "3"}, weight="50",
        packaging={'packed_dimensions_cm':{'length':'15.5','width':'15.5','height':'.3'},'measured':True})
    adapter = NativeAdapter()
    _, token = drafts.preview(world[0], adapter)
    row = drafts.confirm(token, True, adapter)
    assert row.state == "verified"
    enable(monkeypatch)
    adapter.calls.clear()
    return row, adapter


def test_existing_native_only_update_preserves_every_other_field(world, monkeypatch):
    row, adapter = product(world, monkeypatch)
    before, stamp = copy.deepcopy(adapter.remote), row.verified_at
    plan, token = native.review(world[0], adapter=adapter)
    assert plan["classification"] == "update" and plan["inventory_id"] == "gid://shopify/InventoryItem/250"
    assert plan["desired"] == {"length": 15.5, "width": 15.5, "height": 0.3, "unit": "CENTIMETERS"}
    assert "native_write" not in adapter.calls
    with pytest.raises(ShopifyError, match="Explicit approval"):
        native.confirm(token, False, adapter)
    native.confirm(token, True, adapter)
    assert adapter.remote == before and row.verified_at == stamp
    assert adapter.calls.count("native_write") == 1
    assert drafts.evidence(row)["native_dimensions"]["state"] == "verified"
    assert native.review(world[0], adapter=adapter)[0]["classification"] == "no_change"
    assert "product_create" not in adapter.calls and "binary_upload" not in adapter.calls


def test_native_only_success_removes_only_native_comparison_changes(world, monkeypatch):
    row, adapter = product(world, monkeypatch)
    plan, _ = drafts.preview(world[0], adapter)
    assert plan["classification"] == "update"
    _, token = native.review(world[0], adapter=adapter)
    native.confirm(token, True, adapter)
    with current_app.test_request_context():
        assert states_for([world[0]])[world[0].id]["label"] == "Up to Date"


def test_new_create_has_independently_verified_native_stage(world, monkeypatch):
    enable(monkeypatch)
    change(world, dimensions={"length": 155, "width": 155, "height": 3}, weight=50,
        packaging={'packed_dimensions_cm':{'length':'15.5','width':'15.5','height':'.3'},'measured':True})
    adapter = NativeAdapter()
    plan, token = drafts.preview(world[0], adapter)
    assert plan["native"]["classification"] == "create_stage"
    row = drafts.confirm(token, True, adapter)
    assert row.state == "verified" and drafts.evidence(row)["native_dimensions"]["state"] == "verified"
    assert db.session.get(CatalogueOperation, row.operation_id).status == "succeeded"
    assert adapter.calls.count("product_create") == adapter.calls.count("native_write") == 1
    assert adapter.packed["height"] == 0.3


def test_missing_permission_retains_created_product_with_blocked_not_uncertain_native(world, monkeypatch):
    enable(monkeypatch)
    change(world, dimensions={"length": 155, "width": 155, "height": 3},
        packaging={'packed_dimensions_cm':{'length':'15.5','width':'15.5','height':'.3'},'measured':True})
    adapter = NativeAdapter()
    adapter.permission = False
    plan, token = drafts.preview(world[0], adapter)
    assert token and "write_inventory" in plan["native"]["reason"]
    row = drafts.confirm(token, True, adapter)
    assert row.state == "verified" and row.shopify_product_id
    assert drafts.evidence(row)["native_dimensions"]["state"] == "blocked"
    assert db.session.get(CatalogueOperation, row.operation_id).status == "partial"
    assert "native_write" not in adapter.calls
    with current_app.test_request_context():
        state = states_for([world[0]])[world[0].id]
        assert state["label"] == "Needs Attention" and state["native"]["state"] == "blocked"


def test_matching_read_restores_native_evidence_without_changing_metadata_proof(world, monkeypatch):
    row, adapter = product(world, monkeypatch)
    stamp = row.verified_at
    adapter.packed = native.desired(world[0])["desired"]
    plan, token = native.check(world[0], adapter)
    assert plan["classification"] == "no_change" and token is None
    assert native.local_status(world[0], row)["state"] == "verified"
    assert row.verified_at == stamp and "native_write" not in adapter.calls
    proof = drafts.evidence(row)["native_dimensions"]
    operation = db.session.get(CatalogueOperation, proof["operation_id"])
    assert operation.status == "succeeded" and proof["mode"] == "read_only_comparison"


@pytest.mark.parametrize("mode", ["create", "update"])
def test_native_uncertainty_remains_visible_in_complete_product_operation(world, monkeypatch, mode):
    if mode == "update":
        row, adapter = product(world, monkeypatch)
    else:
        enable(monkeypatch)
        change(world, dimensions={"length": 155, "width": 155, "height": 3},
            packaging={'packed_dimensions_cm':{'length':'15.5','width':'15.5','height':'.3'},'measured':True})
        adapter = NativeAdapter()
    adapter.native_error = "after"
    _, token = drafts.preview(world[0], adapter)
    row = drafts.confirm(token, True, adapter)
    operation = db.session.get(CatalogueOperation, row.operation_id)
    assert row.state == "verified" and row.verification_state == "verified"
    assert operation.status == "partial" and operation.recovery_state == "review_required"
    assert drafts.evidence(row)["native_dimensions"]["state"] == "uncertain"
    assert adapter.calls.count("native_write") == 1


@pytest.mark.parametrize("failure", ["before", "after"])
def test_uncertain_native_never_retries_and_reconciles_only_with_equality(world, monkeypatch, failure):
    row, adapter = product(world, monkeypatch)
    _, token = native.review(world[0], adapter=adapter)
    adapter.native_error = failure
    native.confirm(token, True, adapter)
    assert row.state == "verified" and drafts.evidence(row)["native_dimensions"]["state"] == "uncertain"
    with pytest.raises(ShopifyError, match="changed"):
        native.confirm(token, True, adapter)
    if failure == "before":
        with pytest.raises(ShopifyError, match="unresolved"):
            native.reconcile(row, adapter)
        adapter.packed = native.desired(world[0])["desired"]  # model explicit operator resolution
    native.reconcile(row, adapter)
    assert drafts.evidence(row)["native_dimensions"]["state"] == "verified"
    assert adapter.calls.count("native_write") == 1


def test_unsupported_schema_blocks_only_native_stage(world, monkeypatch):
    row, adapter = product(world, monkeypatch)
    adapter.capable = False
    plan, token = native.review(world[0], adapter=adapter)
    assert token is None and "unavailable" in plan["reason"]
    assert row.state == "verified" and "native_write" not in adapter.calls


def test_incomplete_capability_response_is_a_known_blocker_not_recovery(world, monkeypatch):
    row, adapter = product(world, monkeypatch)
    adapter.packed_capability = lambda config: None
    plan, token = native.check(world[0], adapter)
    assert token is None and "incomplete" in plan["reason"]
    assert row.state == "verified" and "native_write" not in adapter.calls


def test_product_dimensions_are_not_silently_assumed_packed(world, monkeypatch):
    row, adapter = product(world, monkeypatch)
    change(world,packaging={})
    monkeypatch.setenv("SHOPIFY_PACKED_DIMENSIONS_USE_PRODUCT_DIMENSIONS", "false")
    plan, token = native.review(world[0], adapter=adapter)
    assert token is None and "unconfigured" in plan["reason"]
    assert plan["capable"] is True and plan["desired"] is None


@pytest.mark.parametrize("unit,factor", list(native.FACTORS.items()))
def test_units_normalize_deterministically(unit, factor):
    values = {"length": float(Decimal(155)/factor), "width": float(Decimal(155)/factor), "height": float(Decimal(3)/factor), "unit": unit}
    assert native.equal(values, {"length": 15.5, "width": 15.5, "height": 0.3, "unit": "CENTIMETERS"})


def test_native_uses_scoped_version_and_same_protected_transport(isolated_transport):
    adapter = ShopifyAdapter()
    settings = ShopifyConfiguration("fictional.myshopify.com", TOKEN)
    response = Response({"data": {"measurement": {}}})
    response.headers = {"X-Shopify-API-Version": "2027-01"}
    session = Session(response)
    adapter._transport(settings, NATIVE_CAPABILITY, session=session, api_version="2027-01")
    url, options = session.calls[0]
    assert url.endswith("/2027-01/graphql.json") and settings.api_version == "2026-10"
    assert options["timeout"] == (5, 15) and options["allow_redirects"] is False
    with pytest.raises(ShopifyError, match="Only packed"):
        adapter._transport(settings, SIMPLE_QUERY, session=session, api_version="2027-01")
    response.headers = {"X-Shopify-API-Version": "2026-10"}
    with pytest.raises(ShopifyError, match="different API version"):
        adapter._transport(settings, NATIVE_CAPABILITY, session=session, api_version="2027-01")


def test_native_review_signature_source_and_scope_are_protected(world, monkeypatch):
    row, adapter = product(world, monkeypatch)
    _, token = native.review(world[0], adapter=adapter)
    with pytest.raises(ShopifyError, match="expired"):
        native.confirm(token + "invalid", True, adapter)
    with pytest.raises(ShopifyError, match="invalid"):
        native.confirm(token, True, adapter, expected_product_id=999)
    change(world, dimensions={"length": 160, "width": 155, "height": 3})
    with pytest.raises(ShopifyError, match="changed"):
        native.confirm(token, True, adapter)
    assert "native_write" not in adapter.calls


def test_native_get_is_offline_and_confirmation_requires_csrf(world, monkeypatch):
    monkeypatch.setattr(ShopifyAdapter, "test_connection", lambda *a: pytest.fail("GET used Shopify"))
    app = current_app._get_current_object()
    response = _client(app).get(f"/products/{world[0].id}/shopify/native-dimensions")
    assert response.status_code == 200 and b"release candidate" in response.data
    app.config["WTF_CSRF_ENABLED"] = True
    assert _client(app).post(f"/products/{world[0].id}/shopify/native-dimensions", data={"action":"confirm"}).status_code == 400


def test_normal_review_can_apply_native_only_without_product_payload_mutation(world, monkeypatch):
    row, adapter = product(world, monkeypatch)
    before = copy.deepcopy(adapter.remote)
    plan, token = drafts.preview(world[0], adapter)
    assert plan["classification"] == "update"
    assert [f["field"] for f in plan["fields"] if f["changed"]] == ["native.packed_dimensions"]
    drafts.confirm(token, True, adapter)
    assert adapter.remote == before and adapter.calls.count("native_write") == 1
    assert "product_update" not in adapter.calls and "variant_write" not in adapter.calls
    assert drafts.preview(world[0], adapter)[0]["classification"] == "no_change"


def test_mutation_payload_is_only_packed_measurements(isolated_transport):
    import requests
    adapter = ShopifyAdapter()
    config = ShopifyConfiguration("fictional.myshopify.com", TOKEN)
    response = Response({"data": {"inventoryItemUpdate": {"inventoryItem": {"id": "gid://shopify/InventoryItem/250"}, "userErrors": []}}})
    response.headers = {"X-Shopify-API-Version": "2027-01"}
    session = Session(response)
    original_session = requests.Session
    requests.Session = lambda: session
    try:
        dims = {"length":15.5,"width":15.5,"height":0.3,"unit":"CENTIMETERS"}
        adapter.write_packed(config, "gid://shopify/InventoryItem/250", dims)
        _, options = session.calls[0]
        assert options["json"]["variables"] == {"id":"gid://shopify/InventoryItem/250", "input":{"measurement":{"packedDimensions":dims}}}
        assert options["json"]["query"] == NATIVE_WRITE
    finally:
        requests.Session = original_session


def test_inventory_identity_mismatch_prevents_write(world, monkeypatch):
    row, adapter = product(world, monkeypatch)
    reader = adapter.read_packed
    def wrong(*args):
        remote = reader(*args)
        remote["variants"]["nodes"][0]["inventoryItem"]["id"] = "gid://shopify/InventoryItem/999"
        return remote
    adapter.read_packed = wrong
    plan, token = native.review(world[0], adapter=adapter)
    assert not token and "untrusted" in plan["reason"]
    assert "native_write" not in adapter.calls


def test_native_freshness_is_independent_of_product_revision(world, monkeypatch):
    from datetime import timedelta
    row, adapter = product(world, monkeypatch)
    _, token = native.review(world[0], adapter=adapter)
    native.confirm(token, True, adapter)
    proof = drafts.evidence(row)["native_dimensions"]
    proof["checked_at"] = (drafts.now() - timedelta(minutes=11)).isoformat()
    native.persist(row, proof)
    assert native.local_status(world[0], row)["state"] == "stale"
    with current_app.test_request_context():
        assert states_for([world[0]])[world[0].id]["label"] != "Up to Date"


def test_http_permission_rejection_is_not_an_uncertain_native_write(isolated_transport):
    from app.shopify_connection import ShopifyNativeWriteRejected
    session = Session(Response(status=403))
    with pytest.raises(ShopifyNativeWriteRejected, match="before execution"):
        ShopifyAdapter()._transport(ShopifyConfiguration("fictional.myshopify.com", TOKEN), NATIVE_WRITE,
            {"id":"gid://shopify/InventoryItem/250", "input":{}}, session=session, api_version="2027-01")
