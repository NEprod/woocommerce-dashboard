import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import event

from app import create_app, db
from app.models import (
    CatalogueOperation,
    Collection,
    Product,
    ProductAsset,
    ProductImage,
    Settings,
    User,
    Variation,
)
from config import Config


def _image(path, colour="teal"):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (24, 18), colour).save(path)


@pytest.fixture
def collections_app(tmp_path):
    database = tmp_path / "instance" / "site.db"
    database.parent.mkdir()
    catalogue = tmp_path / "catalogue"
    output = tmp_path / "output"
    output.mkdir()
    cards_root = catalogue / "Fictional Cards"
    prints_root = catalogue / "Fictional Prints"
    art_root = catalogue / "Capital Parent Art"
    for root in (cards_root, prints_root, art_root):
        root.mkdir(parents=True)
    cards_metadata = {
        "collection_type": "Simple",
        "sku_prefix": "CARD-",
        "title": "Fictional Greeting Cards",
        "short_description": "Shared fictional card defaults.",
        "meta_title": "Fictional cards",
        "meta_description": "Fictional card metadata.",
        "categories": ["Cards"],
        "tags": ["fictional", "paper"],
        "live": True,
    }
    art_metadata = {
        "collection_type": "Single Variable",
        "sku_prefix": "ART-",
        "title": "Capital Parent Art",
        "short_description": "Fictional shared art defaults.",
        "meta_title": "Fictional art",
        "meta_description": "Fictional art metadata.",
        "attributes": {"Style": ["Hero A"], "Size": ["A5"]},
        "image_attributes": ["Style", "Size"],
        "variation_modifiers": {"Style=Hero A": {"price": "21.00"}},
        "live": False,
    }
    (cards_root / "product_info.json").write_text(json.dumps(cards_metadata), encoding="utf-8")
    (prints_root / "product_info.json").write_text("{malformed", encoding="utf-8")
    (art_root / "product_info.json").write_text(json.dumps(art_metadata), encoding="utf-8")
    card_image = cards_root / "Birthday" / "card.png"
    art_parent = art_root / "Parent" / "01-parent.PNG"
    art_variation = art_root / "Hero A" / "A5" / "variation.jpg"
    _image(card_image, "lime")
    _image(art_parent, "navy")
    _image(art_variation, "orange")

    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{database}"
    try:
        app = create_app()
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with app.app_context():
            db.session.add_all(
                [
                    User(
                        email="collections@example.test",
                        username="collections-admin",
                        password="unused-test-password",
                        is_admin=True,
                    ),
                    Settings(
                        product_folder=str(catalogue),
                        output_folder=str(output),
                        url_prefix="https://uploads.invalid/",
                    ),
                ]
            )
            collections = [
                Collection(
                    name="Fictional Cards",
                    root_path=str(cards_root),
                    sku_prefix="CARD-",
                    shared_json_path=str(cards_root / "product_info.json"),
                    collection_type="Simple",
                    source_relpath="Fictional Cards",
                    shared_json_relpath="Fictional Cards/product_info.json",
                ),
                Collection(
                    name="Fictional Prints",
                    root_path=str(prints_root),
                    sku_prefix="PRINT-",
                    shared_json_path=str(prints_root / "product_info.json"),
                    collection_type="Variable Collection",
                    source_relpath="Fictional Prints",
                    shared_json_relpath="Fictional Prints/product_info.json",
                ),
                Collection(
                    name="Capital Parent Art",
                    root_path=str(art_root),
                    sku_prefix="ART-",
                    shared_json_path=str(art_root / "product_info.json"),
                    collection_type="Single Variable",
                    source_relpath="Capital Parent Art",
                    shared_json_relpath="Capital Parent Art/product_info.json",
                ),
            ]
            db.session.add_all(collections)
            db.session.flush()
            now = datetime.now()
            products = [
                Product(
                    collection_id=collections[0].id,
                    sku="CARD-001",
                    title="Birthday Card",
                    product_type="simple",
                    collection_type="Simple",
                    catalogue_status="active",
                    published=True,
                    short_description="Complete card.",
                    meta_title="Birthday card",
                    meta_description="Birthday card metadata.",
                    image_url="https://uploads.invalid/card.webp",
                    source_relpath="Fictional Cards/Birthday",
                    shared_json_relpath="Fictional Cards/product_info.json",
                    local_updated_at=now,
                ),
                Product(
                    collection_id=collections[0].id,
                    sku="CARD-002",
                    title="Override Card",
                    product_type="variable",
                    collection_type="Simple",
                    catalogue_status="active",
                    published=False,
                    override_json_path=str(cards_root / "Override" / "product_info.json"),
                    override_json_relpath="Fictional Cards/Override/product_info.json",
                    source_relpath="Fictional Cards/Override",
                    shared_json_relpath="Fictional Cards/product_info.json",
                    local_updated_at=now - timedelta(hours=1),
                ),
                Product(
                    collection_id=collections[1].id,
                    sku="PRINT-001",
                    title="Historical Print",
                    product_type="variable",
                    collection_type="Variable Collection",
                    catalogue_status="missing",
                    published=True,
                    source_relpath="Fictional Prints/Historical Print",
                    shared_json_relpath="Fictional Prints/product_info.json",
                    local_updated_at=now - timedelta(days=1),
                ),
                Product(
                    collection_id=collections[2].id,
                    sku="ART-001",
                    title="Hero Artwork",
                    product_type="variable",
                    collection_type="Single Variable",
                    catalogue_status="active",
                    published=False,
                    short_description="Complete art.",
                    meta_title="Hero artwork",
                    meta_description="Hero artwork metadata.",
                    image_url="https://uploads.invalid/01-parent.webp",
                    source_relpath="Capital Parent Art",
                    shared_json_relpath="Capital Parent Art/product_info.json",
                    local_updated_at=now - timedelta(minutes=30),
                ),
            ]
            db.session.add_all(products)
            db.session.flush()
            db.session.add_all(
                [
                    ProductImage(product_id=products[0].id, url=products[0].image_url, position=0),
                    ProductImage(product_id=products[3].id, url=products[3].image_url, position=0),
                    ProductAsset(
                        product_id=products[0].id,
                        path=str(card_image),
                        source_relpath="Fictional Cards/Birthday/card.png",
                        kind="image",
                        label="parent:0000",
                        is_primary=True,
                    ),
                    ProductAsset(
                        product_id=products[3].id,
                        path=str(art_parent),
                        source_relpath="Capital Parent Art/Parent/01-parent.PNG",
                        kind="image",
                        label="parent:0000",
                        is_primary=True,
                    ),
                ]
            )
            variations = [
                Variation(product_id=products[1].id, sku="CARD-002-S", catalogue_status="active"),
                Variation(product_id=products[1].id, sku="CARD-002-L", catalogue_status="active"),
                Variation(product_id=products[2].id, sku="PRINT-001-A", catalogue_status="missing"),
                Variation(product_id=products[3].id, sku="ART-001-A5", catalogue_status="active"),
            ]
            db.session.add_all(variations)
            db.session.flush()
            db.session.add(
                ProductAsset(
                    product_id=products[3].id,
                    variation_id=variations[3].id,
                    path=str(art_variation),
                    source_relpath="Capital Parent Art/Hero A/A5/variation.jpg",
                    kind="image",
                    label="variation:0000",
                    is_primary=True,
                )
            )
            db.session.add_all(
                [
                    CatalogueOperation(
                        id="collection-refresh",
                        operation_type="shared_collection_update",
                        status="succeeded",
                        scope=json.dumps(
                            {"scope_kind": "collection", "collection_relpath": "Fictional Cards"}
                        ),
                        started_at=now - timedelta(minutes=10),
                        finished_at=now - timedelta(minutes=9),
                        products_attempted=2,
                        products_succeeded=2,
                    ),
                    CatalogueOperation(
                        id="unrelated-refresh",
                        operation_type="shared_collection_update",
                        status="failed",
                        scope=json.dumps(
                            {"scope_kind": "collection", "collection_relpath": "Other"}
                        ),
                        started_at=now - timedelta(minutes=5),
                    ),
                ]
            )
            db.session.commit()
            ids = {collection.name: collection.id for collection in collections}
        yield app, database, catalogue, ids
    finally:
        with app.app_context():
            db.session.remove()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


@pytest.fixture
def collections_client(collections_app):
    app, _database, _catalogue, _ids = collections_app
    client = app.test_client()
    with app.app_context():
        user_id = User.query.one().id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def test_collections_routes_require_authentication(collections_app):
    app, _database, _catalogue, ids = collections_app
    anonymous = app.test_client()
    assert anonymous.get("/collections").status_code in {302, 401}
    assert anonymous.get(f"/collections/{ids['Fictional Cards']}").status_code in {302, 401}


def test_collections_browser_renders_genuine_aggregates_and_safe_identity(
    collections_client, collections_app
):
    _app, _database, catalogue, _ids = collections_app
    response = collections_client.get("/collections")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert html.count('data-collection-card') == 3
    assert "Fictional Cards" in html
    assert "Variable Collection" in html
    assert "Single Variable" in html
    assert "2 products" in html
    assert "2 variations" in html
    assert "1 Published intent" in html
    assert "1 Draft intent" in html
    assert "1 product override" in html
    assert "Invalid metadata" in html
    assert "Capital Parent Art/Parent/01-parent.PNG" not in html
    assert str(catalogue) not in html
    assert "WooCommerce collection" not in html
    assert "Create Collection" not in html


@pytest.mark.parametrize(
    ("query", "present", "absent"),
    [
        ("q=Greeting", "Fictional Cards", "Fictional Prints"),
        ("type=Single+Variable", "Capital Parent Art", "Fictional Cards"),
        ("health=invalid", "Fictional Prints", "Fictional Cards"),
        ("intent=mixed", "Fictional Cards", "Capital Parent Art"),
        ("overrides=yes", "Fictional Cards", "Fictional Prints"),
        ("lifecycle=missing", "Fictional Prints", "Capital Parent Art"),
        ("images=missing", "Fictional Prints", "Capital Parent Art"),
    ],
)
def test_collection_search_and_filters_are_server_backed(
    collections_client, query, present, absent
):
    html = collections_client.get(f"/collections?{query}").get_data(as_text=True)
    assert present in html
    assert absent not in html
    assert f'action="/collections"' in html


def test_collection_sort_pagination_and_query_state(collections_client):
    html = collections_client.get(
        "/collections?sort=products&order=desc&per_page=25&page=1&type=Simple"
    ).get_data(as_text=True)
    assert 'option value="products" selected' in html
    assert 'option value="desc" selected' in html
    assert 'option value="25" selected' in html
    assert 'name="type"' in html
    assert 'value="Simple" selected' in html
    assert 'aria-label="Collection pages"' in html


def test_collection_order_is_deterministic_and_identity_is_not_duplicated(
    collections_client,
):
    html = collections_client.get("/collections").get_data(as_text=True)
    names = ["Capital Parent Art", "Fictional Greeting Cards", "Fictional Prints"]
    assert html.count('data-collection-card') == 3
    assert [html.index(name) for name in names] == sorted(html.index(name) for name in names)


def test_missing_and_unsupported_collection_metadata_are_controlled(
    collections_client, collections_app
):
    _app, _database, catalogue, ids = collections_app
    cards_source = catalogue / "Fictional Cards" / "product_info.json"
    cards_source.unlink()
    missing = collections_client.get(
        f"/collections/{ids['Fictional Cards']}"
    ).get_data(as_text=True)
    assert "Missing metadata" in missing
    assert str(catalogue) not in missing

    cards_source.write_text(
        json.dumps({"collection_type": "Unsupported Fictional Type"}),
        encoding="utf-8",
    )
    unsupported = collections_client.get(
        f"/collections/{ids['Fictional Cards']}"
    ).get_data(as_text=True)
    assert "Invalid metadata" in unsupported
    assert "Unsupported Fictional Type" in unsupported
    assert "Traceback" not in unsupported


def test_collection_detail_summarizes_metadata_products_images_and_activity(
    collections_client, collections_app
):
    _app, _database, _catalogue, ids = collections_app
    response = collections_client.get(f"/collections/{ids['Fictional Cards']}")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert html.count("<h1") == 1
    assert "Fictional Greeting Cards" in html
    assert "Fictional Cards/product_info.json" in html
    assert "Metadata health" in html
    assert "Valid metadata" in html
    assert "Collection default" in html
    assert "Published intent" in html
    assert "Resolved products" in html
    assert "1 Published intent" in html
    assert "1 Draft intent" in html
    assert "1 of 2 products" in html
    assert "Birthday Card" in html
    assert "Override Card" in html
    assert f'/products/' in html
    assert f'/collections/{ids["Fictional Cards"]}/metadata' in html
    assert "Collection refresh" in html
    assert "unrelated-refresh" not in html
    assert "current remote publication" not in html.lower()


def test_collection_detail_handles_malformed_source_without_raw_error(
    collections_client, collections_app
):
    _app, _database, catalogue, ids = collections_app
    response = collections_client.get(f"/collections/{ids['Fictional Prints']}")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Invalid metadata" in html
    assert "The collection metadata could not be parsed safely" in html
    assert "JSONDecodeError" not in html
    assert "{malformed" not in html
    assert str(catalogue) not in html


def test_collection_detail_preserves_capital_parent_image_contract(
    collections_client, collections_app
):
    _app, _database, _catalogue, ids = collections_app
    html = collections_client.get(
        f"/collections/{ids['Capital Parent Art']}"
    ).get_data(as_text=True)
    assert "Capital Parent Art" in html
    assert "1 product with genuine parent imagery" in html
    assert "1 variation image source" in html
    assert f'/catalogue-images/products/' in html
    assert "Draft intent" in html
    assert "Parent/" not in html


def test_collection_image_issue_filter_counts_before_pagination(
    collections_client, collections_app
):
    _app, _database, _catalogue, ids = collections_app
    html = collections_client.get(
        f"/collections/{ids['Fictional Cards']}?product_issue=images"
    ).get_data(as_text=True)
    assert "Override Card" in html
    assert "<span>Birthday Card</span>" not in html
    assert "Showing 1–1 of 1 products" in html


def test_unknown_collection_is_controlled_404(collections_client):
    response = collections_client.get("/collections/999999")
    assert response.status_code == 404
    assert b"Traceback" not in response.data


def test_product_and_products_views_link_to_collection_detail(
    collections_client, collections_app
):
    app, _database, _catalogue, ids = collections_app
    with app.app_context():
        product_id = Product.query.filter_by(sku="CARD-001").one().id
    detail = collections_client.get(f"/products/{product_id}").get_data(as_text=True)
    products_payload = collections_client.get("/api/edit_products").get_json()
    expected = f"/collections/{ids['Fictional Cards']}"
    assert expected in detail
    assert any(item["collection_url"] == expected for item in products_payload["items"])


def test_collection_affected_products_paginate_without_loading_every_variation(
    collections_client, collections_app
):
    app, _database, _catalogue, ids = collections_app
    with app.app_context():
        collection = db.session.get(Collection, ids["Fictional Cards"])
        for index in range(30):
            product = Product(
                collection_id=collection.id,
                sku=f"CARD-BULK-{index:03d}",
                title=f"Bulk Card {index:03d}",
                product_type="variable",
                catalogue_status="active",
                published=index % 2 == 0,
                source_relpath=f"Fictional Cards/Bulk {index:03d}",
            )
            db.session.add(product)
            db.session.flush()
            db.session.add_all(
                Variation(product_id=product.id, sku=f"CARD-BULK-{index:03d}-{variant:02d}")
                for variant in range(20)
            )
        db.session.commit()
        statements = []

        def before_cursor_execute(*args):
            statements.append(args[2])

        event.listen(db.engine, "before_cursor_execute", before_cursor_execute)
        try:
            response = collections_client.get(
                f"/collections/{collection.id}?products_page=2&products_per_page=12"
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", before_cursor_execute)
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Showing 13–24 of 32 products" in html
    assert "Bulk Card 011" in html
    assert len(statements) <= 22
    assert not any("variation_attribute" in statement.lower() for statement in statements)


def test_large_collection_browser_is_bounded_and_deterministic(collections_app):
    app, _database, catalogue, _ids = collections_app
    with app.app_context():
        for collection_index in range(50):
            name = f"Scale Collection {collection_index:02d}"
            root = catalogue / name
            root.mkdir()
            (root / "product_info.json").write_text(
                json.dumps(
                    {
                        "collection_type": ["Simple", "Variable Collection", "Single Variable"][collection_index % 3],
                        "sku_prefix": f"S{collection_index:02d}-",
                        "title": name,
                        "short_description": "Bounded fictional fixture.",
                        "meta_title": name,
                        "meta_description": "Bounded fictional fixture metadata.",
                        "live": collection_index % 2 == 0,
                    }
                ),
                encoding="utf-8",
            )
            collection = Collection(
                name=name,
                root_path=str(root),
                sku_prefix=f"S{collection_index:02d}-",
                shared_json_path=str(root / "product_info.json"),
                collection_type=["Simple", "Variable Collection", "Single Variable"][collection_index % 3],
                source_relpath=name,
                shared_json_relpath=f"{name}/product_info.json",
            )
            db.session.add(collection)
            db.session.flush()
            for product_index in range(6):
                product = Product(
                    collection_id=collection.id,
                    sku=f"S{collection_index:02d}-{product_index:03d}",
                    title=f"Scale Product {collection_index:02d}-{product_index:03d}",
                    product_type="variable",
                    catalogue_status="active",
                    published=(collection_index + product_index) % 2 == 0,
                    source_relpath=f"{name}/Product {product_index:03d}",
                )
                db.session.add(product)
                db.session.flush()
                db.session.add_all(
                    Variation(
                        product_id=product.id,
                        sku=f"S{collection_index:02d}-{product_index:03d}-{variation_index:02d}",
                    )
                    for variation_index in range(7)
                )
        db.session.commit()
        client = app.test_client()
        user_id = User.query.one().id
        with client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
        statements = []

        def before_cursor_execute(*args):
            statements.append(args[2])

        event.listen(db.engine, "before_cursor_execute", before_cursor_execute)
        try:
            response = client.get("/collections?per_page=25&page=2")
        finally:
            event.remove(db.engine, "before_cursor_execute", before_cursor_execute)
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert html.count('data-collection-card') == 25
    assert "Showing 26–50 of 53 collections" in html
    assert len(statements) <= 18
    assert not any("variation_attribute" in statement.lower() for statement in statements)
    assert not any("variation_image" in statement.lower() for statement in statements)
