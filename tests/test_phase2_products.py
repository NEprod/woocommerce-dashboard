from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app, db
from app.models import (
    Collection,
    Product,
    ProductAsset,
    ProductImage,
    User,
    Variation,
    VariationAttribute,
)
from config import Config


@pytest.fixture
def products_app(tmp_path):
    database = tmp_path / "instance" / "site.db"
    database.parent.mkdir()
    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{database}"
    try:
        app = create_app()
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with app.app_context():
            user = User(
                email="catalogue@example.test",
                username="catalogue-admin",
                password="unused-test-password",
                is_admin=True,
            )
            cards = Collection(
                name="Fictional Cards",
                root_path="/fixture/cards",
                sku_prefix="CARD",
                shared_json_path="/fixture/cards/product_info.json",
                source_relpath="Fictional Cards",
            )
            gifts = Collection(
                name="Fictional Gifts",
                root_path="/fixture/gifts",
                sku_prefix="GIFT",
                shared_json_path="/fixture/gifts/product_info.json",
                source_relpath="Fictional Gifts",
            )
            db.session.add_all([user, cards, gifts])
            db.session.flush()

            now = datetime.now()
            variable = Product(
                collection_id=cards.id,
                sku="CARD-VAR-001",
                title="Fictional Variable Birthday Card",
                product_type="variable",
                catalogue_status="active",
                regular_price=Decimal("12.00"),
                short_description="A complete fictional product.",
                description="Fictional description.",
                meta_title="Fictional metadata",
                meta_description="Fictional metadata description.",
                image_url="https://example.invalid/fictional-card.webp",
                override_json_path="/fixture/cards/variable/product_info.json",
                local_updated_at=now,
            )
            simple = Product(
                collection_id=cards.id,
                sku="CARD-SIMPLE-001",
                title="Simple Fictional Card",
                product_type="simple",
                catalogue_status="active",
                regular_price=Decimal("7.50"),
                short_description="",
                description=None,
                meta_title=None,
                meta_description=None,
                local_updated_at=now - timedelta(hours=1),
            )
            missing = Product(
                collection_id=gifts.id,
                sku="GIFT-MISSING-001",
                title="Missing Fictional Gift",
                product_type="simple",
                catalogue_status="missing",
                local_updated_at=now - timedelta(days=1),
            )
            db.session.add_all([variable, simple, missing])
            db.session.flush()
            db.session.add_all(
                [
                    ProductAsset(
                        product_id=variable.id,
                        path="/fixture/cards/product_info.json",
                        kind="info",
                        label="shared",
                    ),
                    ProductAsset(
                        product_id=variable.id,
                        path="/fixture/cards/variable/product_info.json",
                        kind="info",
                        label="override",
                    ),
                    ProductAsset(
                        product_id=simple.id,
                        path="/fixture/cards/product_info.json",
                        kind="info",
                        label="shared",
                    ),
                ]
            )
            db.session.add(ProductImage(product_id=variable.id, url=variable.image_url))
            variations = [
                Variation(
                    product_id=variable.id,
                    sku="CARD-VAR-001-S",
                    catalogue_status="active",
                    regular_price=Decimal("10.00"),
                    stock_quantity=8,
                    local_updated_at=now,
                ),
                Variation(
                    product_id=variable.id,
                    sku="CARD-VAR-001-L",
                    catalogue_status="missing",
                    regular_price=Decimal("15.00"),
                    stock_quantity=0,
                    local_updated_at=now - timedelta(minutes=10),
                ),
            ]
            db.session.add_all(variations)
            db.session.flush()
            db.session.add_all(
                [
                    VariationAttribute(
                        variation_id=variations[0].id,
                        name="Size",
                        value="Small",
                        position=0,
                    ),
                    VariationAttribute(
                        variation_id=variations[0].id,
                        name="Finish",
                        value="Matte",
                        position=1,
                    ),
                    VariationAttribute(
                        variation_id=variations[1].id,
                        name="Size",
                        value="Large",
                        position=0,
                    ),
                ]
            )
            db.session.commit()
        yield app, database
    finally:
        with app.app_context():
            db.session.remove()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


@pytest.fixture
def products_client(products_app):
    app, _database = products_app
    client = app.test_client()
    with app.app_context():
        user_id = User.query.one().id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def test_products_page_renders_grouped_accessible_shell(products_client):
    response = products_client.get("/products")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="products-browser"' in html
    assert 'aria-label="Product catalogue filters"' in html
    assert 'data-products-loading' in html
    assert 'data-products-error' in html
    assert 'data-products-empty' in html
    assert 'data-products-filtered-empty' in html
    assert "/static/assets/js/products-browser.js" in html
    assert "Export" not in html
    assert "Add product" not in html


def test_products_javascript_defines_mobile_and_accessible_expansion_hooks():
    source = (Path(__file__).resolve().parents[1] / "app/static/assets/js/products-browser.js").read_text()
    assert "mobileProductCard" in source
    assert 'setAttribute("aria-expanded", "false")' in source
    assert 'event.key !== "Escape"' in source
    assert "loadVariations" in source


def test_products_api_groups_collections_and_reports_genuine_counts(products_client):
    response = products_client.get("/api/edit_products")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["summary"] == {
        "collections": 2,
        "products": 3,
        "variations": 2,
        "active": 2,
        "missing": 1,
        "metadata_issues": 1,
    }
    assert payload["pagination"]["total"] == 3
    assert [group["name"] for group in payload["groups"]] == [
        "Fictional Cards",
        "Fictional Gifts",
    ]
    cards = payload["groups"][0]
    assert cards["product_count"] == 2
    assert cards["variation_count"] == 2
    assert cards["active_count"] == 2
    assert cards["missing_count"] == 0
    variable = next(item for item in cards["products"] if item["type"] == "variable")
    assert variable["variation_count"] == 2
    assert variable["price"] == {"minimum": "10.00", "maximum": "15.00"}
    assert variable["metadata_source"] == "override"
    assert variable["thumbnail"] == f"/catalogue-images/products/{variable['id']}"
    assert "variations" not in variable


@pytest.mark.parametrize(
    ("query", "expected_skus"),
    [
        ("q=variable", {"CARD-VAR-001"}),
        ("q=card-simple-001", {"CARD-SIMPLE-001"}),
        ("collection=2", {"GIFT-MISSING-001"}),
        ("type=variable", {"CARD-VAR-001"}),
        ("status=missing", {"GIFT-MISSING-001"}),
        ("source=override", {"CARD-VAR-001"}),
        ("source=shared", {"CARD-SIMPLE-001"}),
        ("issue=missing_image", {"CARD-SIMPLE-001"}),
    ],
)
def test_products_api_supported_filters(products_client, query, expected_skus):
    payload = products_client.get(f"/api/edit_products?{query}").get_json()
    skus = {
        product["sku"]
        for group in payload["groups"]
        for product in group["products"]
    }
    assert skus == expected_skus


def test_products_api_rejects_unsupported_filters(products_client):
    assert products_client.get("/api/edit_products?status=published").status_code == 400
    assert products_client.get("/api/edit_products?type=external").status_code == 400
    assert products_client.get("/api/edit_products?source=invented").status_code == 400


def test_variations_are_loaded_on_demand_with_attributes(products_client, products_app):
    app, _database = products_app
    with app.app_context():
        product_id = Product.query.filter_by(sku="CARD-VAR-001").one().id

    payload = products_client.get(f"/api/products/{product_id}/variations").get_json()
    assert payload["total"] == 2
    assert payload["truncated"] is False
    assert [item["sku"] for item in payload["items"]] == [
        "CARD-VAR-001-L",
        "CARD-VAR-001-S",
    ]
    assert payload["items"][1]["attributes"] == [
        {"name": "Size", "value": "Small"},
        {"name": "Finish", "value": "Matte"},
    ]
    assert payload["items"][1]["stock_quantity"] == 8
    assert payload["items"][0]["catalogue_status"] == "missing"


def test_variation_endpoint_is_authorized_and_rejects_simple_product(
    products_app, products_client
):
    app, _database = products_app
    anonymous = app.test_client()
    with app.app_context():
        variable_id = Product.query.filter_by(sku="CARD-VAR-001").one().id
        simple_id = Product.query.filter_by(sku="CARD-SIMPLE-001").one().id

    assert anonymous.get(f"/api/products/{variable_id}/variations").status_code == 401
    assert products_client.get(f"/api/products/{simple_id}/variations").status_code == 404


def test_dashboard_issue_query_remains_visible_and_composable(products_client):
    response = products_client.get(
        "/products?issue=missing_image&q=simple&status=active"
    )
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Missing images" in html
    assert 'value="simple"' in html
    assert 'value="active"' in html


def test_products_api_paginates_large_catalogue(products_app, products_client):
    app, _database = products_app
    with app.app_context():
        collection = Collection.query.filter_by(name="Fictional Cards").one()
        for index in range(30):
            db.session.add(
                Product(
                    collection_id=collection.id,
                    sku=f"BULK-{index:03d}",
                    title=f"Bulk fictional product {index:03d}",
                    product_type="simple",
                    catalogue_status="active",
                )
            )
        db.session.commit()

    first = products_client.get("/api/edit_products?per_page=25").get_json()
    second = products_client.get("/api/edit_products?per_page=25&page=2").get_json()
    assert first["pagination"]["total"] == 33
    assert first["pagination"]["pages"] == 2
    assert sum(len(group["products"]) for group in first["groups"]) == 25
    assert sum(len(group["products"]) for group in second["groups"]) == 8


def test_products_empty_and_filtered_empty_contract(products_app, products_client):
    filtered = products_client.get("/api/edit_products?q=does-not-exist").get_json()
    assert filtered["pagination"]["total"] == 0
    assert filtered["empty_reason"] == "filtered"

    app, _database = products_app
    with app.app_context():
        VariationAttribute.query.delete()
        Variation.query.delete()
        ProductAsset.query.delete()
        ProductImage.query.delete()
        Product.query.delete()
        Collection.query.delete()
        db.session.commit()
    empty = products_client.get("/api/edit_products").get_json()
    assert empty["pagination"]["total"] == 0
    assert empty["empty_reason"] == "catalogue"
