"""Focused presentation and real-schema metadata resolution regressions; mocked remote only."""
import json

from PIL import Image

from app import db, shopify_drafts as drafts
from app.commerce import catalogue_snapshot
from app.shopify_simple_fields import review_rows
from app.shopify_products import states_for
from app.models import ProductImage
from test_shopify_drafts import world, FakeDraftAdapter
from test_shopify_simple_updates import created, change
from test_phase3_woo_publish_preview import preview_app, _client


def test_create_null_compare_at_is_not_change(world):
    plan, _ = drafts.preview(world[0], FakeDraftAdapter())
    field = next(row for row in plan["fields"] if row["field"] == "compareAtPrice")
    assert field == {"field": "compareAtPrice", "before": None, "after": None, "changed": False}
    shown = next(row for row in review_rows(plan) if row["label"] == "Compare-at price")
    assert shown["action"] == "Already matching" and shown["after"] == "Not set"


def test_shared_description_sparse_override_and_seo_are_independent(world):
    product, folder, _ = world
    change(world, description="<p>Inherited main description</p>", meta_description="SEO only",
        tags=["Birthday"], weight=50, dimensions={"length": 110, "width": 100, "height": 6},
        requires_shipping=True, country_of_origin="GB")
    (folder / "product_info.json").write_text(json.dumps({"meta_description": "Product SEO", "tags": ["Personalised"]}))
    # Simulate an older projection: the reviewed plan must still use resolved authored JSON.
    product.description = None
    db.session.commit()
    snapshot = catalogue_snapshot(product)
    plan = drafts.local_plan(product)
    assert snapshot.data["authored"]["description"] == "<p>Inherited main description</p>"
    assert plan["managed"]["product"]["descriptionHtml"] == "<p>Inherited main description</p>"
    assert plan["managed"]["product"]["seo"]["description"] == "Product SEO"
    assert plan["managed"]["product"]["tags"] == ["Birthday", "Personalised"]
    inventory = plan["managed"]["variant"]["inventoryItem"]
    assert inventory["measurement"]["weight"] == {"value": 50.0, "unit": "GRAMS"}
    assert inventory["requiresShipping"] is True and inventory["countryCodeOfOrigin"] == "GB"
    assert len(plan["managed"]["product"]["metafields"]) == 3
    (folder / "product_info.json").write_text(json.dumps({"description": "Explicit main override"}))
    assert drafts.local_plan(product)["description"] == "Explicit main override"


def test_missing_metadata_is_not_invented(world, monkeypatch):
    monkeypatch.delenv("DEFAULT_COUNTRY_OF_ORIGIN", raising=False)
    change(world, description="", meta_description="SEO is not main copy")
    world[0].tags.clear()
    db.session.commit()
    plan = drafts.local_plan(world[0])
    assert plan["description"] == ""
    assert "tags" not in plan["managed"]["product"]
    inventory = plan["managed"]["variant"]["inventoryItem"]
    assert "requiresShipping" not in inventory and "countryCodeOfOrigin" not in inventory
    assert any("Main product description is empty" in w for w in plan["warnings"])
    assert any("Tags are unspecified" in w for w in plan["warnings"])


def test_original_webp_order_and_bytes_are_preserved(world):
    product, folder, _ = world
    for image in list(product.images):
        db.session.delete(image)
    product.images.clear()
    for position, filename in enumerate(["third.webp", "first.webp", "second.webp"]):
        Image.new("RGB", (5, 5), "red").save(folder / filename, "WEBP")
        product.images.append(ProductImage(url=filename, position=position, alt_text=f"Alt {position}"))
    db.session.commit()
    original = {p.name: p.read_bytes() for p in folder.glob("*.webp")}
    plan = drafts.local_plan(product)
    # The established resolver also appends unprojected originals still in the folder.
    assert [i["filename"] for i in plan["images"][:3]] == ["third.webp", "first.webp", "second.webp"]
    assert {i["filename"] for i in plan["images"][3:]} == {"original.png", "second.jpg"}
    assert all(i["mime"] == "image/webp" and i["extension"] == ".webp" for i in plan["images"][:3])
    assert original == {p.name: p.read_bytes() for p in folder.glob("*.webp")}


def test_compact_card_cached_evidence_and_responsive_review(world, monkeypatch):
    from flask import current_app
    product, row, adapter = created(world)
    monkeypatch.setattr("app.shopify_connection.ShopifyAdapter.test_connection", lambda *a: (_ for _ in ()).throw(AssertionError("Page performed remote read")))
    client = _client(current_app._get_current_object())
    html = client.get(f"/products/{product.id}").get_data(as_text=True)
    assert "Review &amp; Sync" in html and "Cached verification" in html and "Up to Date" in html
    assert "Refresh / History" in html and "View on Shopify" in html
    assert "WooCommerce Dashboard" not in html
    plan, token = drafts.preview(product, adapter)
    from flask import render_template
    from flask_wtf import FlaskForm
    with current_app.test_request_context():
        html = render_template("shopify_drafts.html", form=FlaskForm(), plan=plan, token=token,
            error=None, scoped_product=product, products=[product], identities=[row], review_rows=review_rows(plan))
    assert "Advanced Details" in html and 'data-label="Reviewed"' in html
    assert "Already matching" in html and "No Change" in html


def test_live_intent_remains_blocked_without_mutating_source(world):
    product, row, adapter = created(world)
    change(world, live=True)
    before = world[2].read_bytes()
    import pytest
    from app.shopify_connection import ShopifyError
    with pytest.raises(ShopifyError, match="Live intent"):
        drafts.preview(product, adapter)
    assert world[2].read_bytes() == before and adapter.calls == []
    from flask import current_app
    with current_app.test_request_context():
        state = states_for([product])[product.id]
    assert state["state"] != "no_change" and state["publication"] == "Not verified"
