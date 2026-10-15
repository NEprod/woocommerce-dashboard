import copy
import json
from pathlib import Path

import pytest
from flask import current_app
from app import db, shopify_drafts as drafts, shopify_status as status
from app.models import CatalogueOperation, ProductAsset
from app.shopify_connection import ShopifyError
from app.shopify_products import states_for
from test_shopify_drafts import world, FakeDraftAdapter
from test_shopify_simple_updates import UpdateAdapter
from test_phase3_woo_publish_preview import preview_app, _client
from test_phase2_milestone3_hotfix import override_workflow


def test_completed_async_create_finalizes_on_refresh_without_new_write(world):
    adapter = FakeDraftAdapter(processing=True)
    _, token = drafts.preview(world[0], adapter)
    row = drafts.confirm(token, True, adapter)
    assert row.state == "processing" and row.verified_at is None
    original_id, operation_id = row.shopify_product_id, row.operation_id
    for m in adapter.remote["media"]["nodes"]:
        m.update(status="READY", image={"url": "https://cdn.shopify.com/original.png"})
    adapter.calls.clear()
    status.refresh([world[0]], adapter=adapter)
    assert row.state == row.verification_state == "verified" and row.verified_at
    with current_app.test_request_context():
        assert states_for([world[0]])[world[0].id]["label"] == "Up to Date"
    db.session.expire_all()
    plan, token = drafts.preview(world[0], adapter)
    assert plan["classification"] == "no_change" and token is None
    assert row.shopify_product_id == original_id
    operation = db.session.get(CatalogueOperation, operation_id)
    assert operation.status == "succeeded"
    assert json.loads(operation.scope)["resolved_previous_outcome"]["status"] == "partial"
    assert not any(c in adapter.calls for c in ("product_create", "binary_upload", "variant_write"))


def test_processing_review_explains_pending_media_not_false_recovery(world):
    adapter = FakeDraftAdapter(processing=True)
    _, token = drafts.preview(world[0], adapter)
    row = drafts.confirm(token, True, adapter)
    with pytest.raises(ShopifyError, match="still processing") as error:
        drafts.preview(world[0], adapter)
    assert "Recovery required" not in str(error.value)
    assert row.state == "processing"


def test_genuine_uncertainty_is_not_automatically_cleared(world):
    adapter = FakeDraftAdapter("create")
    _, token = drafts.preview(world[0], adapter)
    row = drafts.confirm(token, True, adapter)
    adapter.calls.clear()
    status.refresh([world[0]], adapter=adapter)
    assert row.state == "uncertain" and "product_read" not in adapter.calls


def test_processing_checks_are_deduplicated_but_explicit_review_is_fresh(world):
    adapter = FakeDraftAdapter(processing=True)
    _, token = drafts.preview(world[0], adapter)
    row = drafts.confirm(token, True, adapter)
    adapter.calls.clear()
    status.refresh([world[0]], adapter=adapter)
    reads = adapter.calls.count("product_read")
    status.refresh([world[0]], adapter=adapter)
    assert adapter.calls.count("product_read") == reads
    with pytest.raises(ShopifyError, match="still processing"):
        drafts.preview(world[0], adapter)
    assert adapter.calls.count("product_read") == reads + 1 and row.state == "processing"


def test_create_override_navigation_and_cancel_are_read_only(override_workflow):
    wf = override_workflow
    product_id = wf["product_ids"][0]
    target = wf["collection_root"] / "Product 1" / "product_info.json"
    page = wf["client"].get(f"/edit_products/{product_id}/edit/override")
    assert page.status_code == 200 and b"Edit Product Override" in page.data
    assert not target.exists() and not wf["calls"]
    assert not (target.parent / ".update").exists()
    wf["client"].get(f"/products/{product_id}")
    assert not target.exists() and not wf["calls"]


def test_genuine_override_save_only_then_scans_and_unchanged_save_is_noop(override_workflow):
    wf = override_workflow
    product_id = wf["product_ids"][0]
    with wf["app"].app_context():
        from app.models import Product
        sku = db.session.get(Product, product_id).sku
    endpoint = f"/api/products/{sku}/save-json"
    # Resolve existing route by endpoint name, preserving its actual convention.
    with wf["app"].test_request_context():
        from flask import url_for
        endpoint = url_for("main.product_save_json", sku=sku)
    from app.metadata_save import review_token
    with wf["app"].app_context():revision = review_token(db.session.get(Product, product_id), "override")
    result = wf["client"].post(endpoint, json={"kind": "override", "data": {"description": "Actual authored change"}, "revision": revision})
    assert result.status_code == 200, result.get_json()
    target = wf["collection_root"] / "Product 1" / "product_info.json"
    assert json.loads(target.read_text()) == {"description": "Actual authored change"}
    assert (target.parent / ".update").exists() and len(wf["calls"]) == 1
    with wf["app"].app_context():
        from app.utils.operation_control import finish_catalogue_operation
        operation = CatalogueOperation.query.filter_by(status="running").one()
        finish_catalogue_operation(operation.id, status="succeeded")
    result = wf["client"].post(endpoint, json={"kind": "override", "data": {"description": "Actual authored change"}, "revision": result.json["revision"]})
    assert result.status_code == 200 and result.get_json()["unchanged"] is True
    assert len(wf["calls"]) == 1
