import json
from datetime import datetime
from decimal import Decimal

import pytest
from PIL import Image
from sqlalchemy import event

from app import create_app, db
from app.models import (
    CatalogueOperation,
    CatalogueOperationItem,
    Collection,
    Product,
    ProductAsset,
    ProductAttribute,
    ProductImage,
    Settings,
    User,
    Variation,
    VariationAttribute,
    VariationImage,
)
from config import Config
from app.utils.operation_control import finish_catalogue_operation


@pytest.fixture
def milestone5_app(tmp_path):
    catalogue = tmp_path / "catalogue"
    collection_folder = catalogue / "Fictional Prints"
    variable_folder = collection_folder / "Aurora Set"
    simple_folder = collection_folder / "Solo Print"
    variable_folder.mkdir(parents=True)
    simple_folder.mkdir(parents=True)

    shared_path = collection_folder / "product_info.json"
    override_path = variable_folder / "product_info.json"
    shared = {
        "collection_type": "Variable Collection",
        "sku_prefix": "PRINT",
        "title": "Fictional Print",
        "price": "12.00",
        "categories": ["Wall art"],
        "tags": ["fictional"],
        "live": True,
        "attributes": {"Size": ["Small", "Large"]},
        "image_attributes": ["Size"],
        "variation_modifiers": {
            "Size=Large": {
                "price": "18.00",
                "weight": 120,
                "dimensions": {"length": 420, "width": 297, "height": 2},
            }
        },
        "meta_title": "Fictional print collection",
    }
    override = {"title": "Aurora", "tags": ["aurora"], "live": False}
    shared_path.write_text(json.dumps(shared), encoding="utf-8")
    override_path.write_text(json.dumps(override), encoding="utf-8")

    Image.new("RGB", (32, 24), "navy").save(variable_folder / "parent hero.png")
    Image.new("RGB", (24, 32), "teal").save(variable_folder / "parent detail.jpg")
    small_folder = variable_folder / "Small"
    large_folder = variable_folder / "Large"
    small_folder.mkdir()
    large_folder.mkdir()
    Image.new("RGB", (20, 20), "lime").save(small_folder / "small view.jpeg")
    Image.new("RGB", (20, 20), "orange").save(large_folder / "large view.webp")
    Image.new("RGB", (30, 30), "white").save(simple_folder / "solo.png")

    database = tmp_path / "instance" / "site.db"
    database.parent.mkdir()
    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{database}"
    app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        db.session.add(
            Settings(
                product_folder=str(catalogue),
                output_folder=str(tmp_path / "output"),
                url_prefix="https://uploads.invalid/catalogue/",
            )
        )
        user = User(
            email="metadata@example.test",
            username="metadata-admin",
            password="unused-test-password",
            is_admin=True,
        )
        collection = Collection(
            name="Fictional Prints",
            root_path=str(collection_folder),
            shared_json_path=str(shared_path),
            source_relpath="Fictional Prints",
            shared_json_relpath="Fictional Prints/product_info.json",
            sku_prefix="PRINT",
            collection_type="Variable Collection",
        )
        db.session.add_all([user, collection])
        db.session.flush()
        variable = Product(
            collection_id=collection.id,
            sku="PRINT-001",
            title="Aurora - Fictional Print",
            product_type="variable",
            collection_type="Variable Collection",
            catalogue_status="active",
            source_relpath="Fictional Prints/Aurora Set",
            shared_json_relpath="Fictional Prints/product_info.json",
            override_json_relpath="Fictional Prints/Aurora Set/product_info.json",
            shared_json_path=str(shared_path),
            override_json_path=str(override_path),
            image_url="https://uploads.invalid/catalogue/PRINT-001.webp",
            regular_price=Decimal("12.00"),
            published=False,
            meta_title="Fictional print collection",
            local_updated_at=datetime(2026, 8, 20, 12, 0),
        )
        simple = Product(
            collection_id=collection.id,
            sku="PRINT-002",
            title="Solo Print - Fictional Print",
            product_type="simple",
            collection_type="Variable Collection",
            catalogue_status="active",
            source_relpath="Fictional Prints/Solo Print",
            shared_json_relpath="Fictional Prints/product_info.json",
            shared_json_path=str(shared_path),
            image_url="https://uploads.invalid/catalogue/PRINT-002.webp",
            regular_price=Decimal("12.00"),
            local_updated_at=datetime(2026, 8, 20, 11, 0),
        )
        db.session.add_all([variable, simple])
        db.session.flush()
        db.session.add_all(
            [
                ProductAsset(
                    product_id=variable.id,
                    path=str(shared_path),
                    source_relpath="Fictional Prints/product_info.json",
                    kind="info",
                    label="shared",
                ),
                ProductAsset(
                    product_id=variable.id,
                    path=str(override_path),
                    source_relpath="Fictional Prints/Aurora Set/product_info.json",
                    kind="info",
                    label="override",
                ),
                ProductAsset(
                    product_id=simple.id,
                    path=str(shared_path),
                    source_relpath="Fictional Prints/product_info.json",
                    kind="info",
                    label="shared",
                ),
                ProductImage(
                    product_id=variable.id,
                    url="https://uploads.invalid/catalogue/PRINT-001.webp",
                    alt_text="Aurora parent primary",
                    position=0,
                ),
                ProductImage(
                    product_id=variable.id,
                    url="https://uploads.invalid/catalogue/PRINT-001-2.webp",
                    alt_text="Aurora parent detail",
                    position=1,
                ),
                ProductImage(
                    product_id=simple.id,
                    url="https://uploads.invalid/catalogue/PRINT-002.webp",
                    alt_text="Solo print",
                    position=0,
                ),
                ProductAttribute(
                    product_id=variable.id,
                    name="Size",
                    values="Small, Large",
                    position=0,
                ),
            ]
        )
        small = Variation(
            product_id=variable.id,
            sku="PRINT-001-SMALL",
            source_relpath="Fictional Prints/Aurora Set/Small",
            regular_price=Decimal("12.00"),
            stock_quantity=4,
            catalogue_status="active",
            menu_order=0,
            local_updated_at=datetime(2026, 8, 20, 12, 0),
            image_url="https://uploads.invalid/catalogue/PRINT-001-SMALL.webp",
        )
        large = Variation(
            product_id=variable.id,
            sku="PRINT-001-LARGE",
            source_relpath="Fictional Prints/Aurora Set/Large",
            regular_price=Decimal("18.00"),
            stock_quantity=2,
            catalogue_status="active",
            menu_order=1,
            local_updated_at=datetime(2026, 8, 20, 12, 1),
            image_url="https://uploads.invalid/catalogue/PRINT-001-LARGE.webp",
        )
        db.session.add_all([small, large])
        db.session.flush()
        db.session.add_all(
            [
                VariationAttribute(variation_id=small.id, name="Size", value="Small", position=0),
                VariationAttribute(variation_id=large.id, name="Size", value="Large", position=0),
                VariationImage(
                    variation_id=small.id,
                    url="https://uploads.invalid/catalogue/PRINT-001-SMALL.webp",
                    alt_text="Small variation",
                    position=0,
                ),
                VariationImage(
                    variation_id=large.id,
                    url="https://uploads.invalid/catalogue/PRINT-001-LARGE.webp",
                    alt_text="Large variation",
                    position=0,
                ),
            ]
        )
        operation = CatalogueOperation(
            id="a" * 32,
            operation_type="product_update",
            status="succeeded",
            scope=json.dumps({"sku": variable.sku}),
            products_attempted=1,
            products_succeeded=1,
        )
        db.session.add(operation)
        db.session.flush()
        db.session.add(
            CatalogueOperationItem(
                operation_id=operation.id,
                sku=variable.sku,
                source_path="Fictional Prints/Aurora Set",
                status="succeeded",
                database_state="committed",
                marker_state="finalized",
            )
        )
        db.session.commit()
        ids = {"variable": variable.id, "simple": simple.id, "collection": collection.id}
    try:
        yield app, catalogue, database, ids, shared_path, override_path
    finally:
        with app.app_context():
            db.session.remove()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


@pytest.fixture
def milestone5_client(milestone5_app):
    app, *_rest = milestone5_app
    client = app.test_client()
    with app.app_context():
        user_id = User.query.one().id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def test_product_detail_requires_authentication_and_has_controlled_404(milestone5_app):
    app, *_rest = milestone5_app
    anonymous = app.test_client()
    assert anonymous.get("/products/1").status_code in {302, 401}
    client = app.test_client()
    with app.app_context():
        user_id = User.query.one().id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    assert client.get("/products/999999").status_code == 404


def test_variable_product_detail_is_resolved_and_parent_first(milestone5_client, milestone5_app):
    _app, catalogue, _database, ids, *_paths = milestone5_app
    response = milestone5_client.get(f"/products/{ids['variable']}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert html.count("<h1") == 1
    assert "Aurora - Fictional Print" in html
    assert "Collection metadata" in html
    assert "Product override" in html
    assert "Inherited value" in html
    assert "Override value" in html
    assert "Resolved value" in html
    assert "PRINT-001-SMALL" in html
    assert html.index("Parent product") < html.index("PRINT-001-SMALL")
    assert str(catalogue) not in html
    assert "/output" not in html


def test_simple_product_detail_has_no_meaningless_variation_controls(milestone5_client, milestone5_app):
    _app, _catalogue, _database, ids, *_paths = milestone5_app
    html = milestone5_client.get(f"/products/{ids['simple']}").get_data(as_text=True)
    assert "Parent product" in html
    assert "No variations are generated for this Simple product." in html
    assert "data-variation-toggle" not in html


def test_gallery_routes_are_ordered_private_and_preserve_ownership(milestone5_client, milestone5_app):
    _app, _catalogue, _database, ids, *_paths = milestone5_app
    detail = milestone5_client.get(f"/products/{ids['variable']}").get_data(as_text=True)
    assert f"/catalogue-images/products/{ids['variable']}/gallery/0" in detail
    assert f"/catalogue-images/products/{ids['variable']}/gallery/1" in detail
    assert "https://uploads.invalid/catalogue/PRINT-001.webp" in detail
    first = milestone5_client.get(f"/catalogue-images/products/{ids['variable']}/gallery/0")
    second = milestone5_client.get(f"/catalogue-images/products/{ids['variable']}/gallery/1")
    assert first.status_code == second.status_code == 200
    assert first.headers["X-Content-Type-Options"] == "nosniff"
    assert "private" in first.headers["Cache-Control"]


def test_product_detail_identifies_confined_but_corrupt_source_image(
    milestone5_client, milestone5_app
):
    _app, catalogue, _database, ids, *_paths = milestone5_app
    (catalogue / "Fictional Prints" / "Solo Print" / "solo.png").write_bytes(
        b"not-an-image"
    )

    html = milestone5_client.get(f"/products/{ids['simple']}").get_data(as_text=True)

    assert "Source Corrupt" in html
    assert "Fictional Prints/Solo Print/solo.png" in html
    assert f"/catalogue-images/products/{ids['simple']}/gallery/0" not in html


def test_collection_editor_identifies_shared_scope_and_affected_products(milestone5_client, milestone5_app):
    _app, catalogue, _database, ids, *_paths = milestone5_app
    response = milestone5_client.get(f"/collections/{ids['collection']}/metadata")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Edit Collection Metadata" in html
    assert "may affect every product that inherits from it" in html
    assert "2 products affected" in html
    assert "Aurora - Fictional Print" in html
    assert "Advanced JSON" in html
    assert str(catalogue) not in html


def test_affected_product_preview_is_paginated_and_authorized(milestone5_client, milestone5_app):
    app, *_rest, ids, _shared, _override = milestone5_app
    response = milestone5_client.get(f"/api/collections/{ids['collection']}/affected-products?per_page=1")
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["pagination"]["total"] == 2
    assert len(payload["items"]) == 1
    assert "thumbnail" in payload["items"][0]
    assert app.test_client().get(f"/api/collections/{ids['collection']}/affected-products").status_code in {302, 401}


def test_override_editor_distinguishes_authored_inherited_and_resolved_values(milestone5_client, milestone5_app):
    _app, catalogue, _database, ids, *_paths = milestone5_app
    html = milestone5_client.get(f"/edit_products/{ids['variable']}/edit/override").get_data(as_text=True)
    assert "Edit Product Override" in html
    assert "affects only this product" in html
    assert 'data-override-field="title"' in html
    assert "Fictional Print" in html
    assert "Aurora" in html
    assert "Aurora - Fictional Print" in html
    assert "Product Image Context" in html
    assert "PRINT-001-SMALL" in html
    assert "https://uploads.invalid/catalogue/PRINT-001-SMALL.webp" in html
    assert str(catalogue) not in html


def test_advanced_mode_bootstrap_identifies_exact_source_without_host_path(milestone5_client, milestone5_app):
    _app, catalogue, _database, ids, *_paths = milestone5_app
    html = milestone5_client.get(
        f"/edit_products/{ids['variable']}/edit/shared", follow_redirects=True
    ).get_data(as_text=True)
    assert 'data-editor-mode="advanced"' in html
    assert "Collection metadata source" in html
    assert "Fictional Prints/product_info.json" in html
    assert "Search JSON" in html
    assert "Format JSON" in html
    assert "Return to Guided editing" in html
    assert str(catalogue) not in html


def test_replace_save_keeps_override_partial_and_collection_unchanged(
    milestone5_client, milestone5_app, monkeypatch
):
    _app, _catalogue, _database, ids, shared_path, override_path = milestone5_app
    monkeypatch.setattr(
        "app.routes.start_scan",
        lambda *_args, **kwargs: finish_catalogue_operation(
            kwargs["operation_id"], status="succeeded"
        ),
    )
    response = milestone5_client.post(
        "/edit_products/PRINT-001/save",
        json={"kind": "override", "data": {"price": "19.00"}, "replace": True},
    )
    assert response.status_code == 200
    assert json.loads(override_path.read_text(encoding="utf-8")) == {"price": "19.00"}
    shared = json.loads(shared_path.read_text(encoding="utf-8"))
    assert shared["title"] == "Fictional Print"
    assert "collection_type" in shared


def test_collection_guided_payload_preserves_supported_structures_and_scope(
    milestone5_client, milestone5_app, monkeypatch
):
    _app, _catalogue, _database, _ids, shared_path, override_path = milestone5_app
    previous_override = override_path.read_text(encoding="utf-8")
    starts = []

    def complete_scan(*args, **kwargs):
        starts.append((args, kwargs))
        finish_catalogue_operation(kwargs["operation_id"], status="succeeded")

    monkeypatch.setattr("app.routes.start_scan", complete_scan)
    authored = {
        "collection_type": "Variable Collection",
        "sku_prefix": "PRINT",
        "title": "Revised fictional prints",
        "price": "14.00",
        "sale_price": "12.00",
        "weight": "90",
        "dimensions": {"length": "300", "width": "200", "height": "4"},
        "categories": ["Wall art", "Fictional"],
        "tags": ["ordered-first", "ordered-second"],
        "attributes": {"Size": ["Small", "Large"], "Finish": ["Matt", "Gloss"]},
        "image_attributes": ["Size", "Finish"],
        "variation_modifiers": {
            "Size=Large": {"price": "18.00", "weight": "120"},
            "Size=Large|Finish=Gloss": {
                "price": "20.00",
                "dimensions": {"length": "420", "width": "297", "height": "2"},
            },
        },
        "live": True,
    }

    response = milestone5_client.post(
        "/edit_products/PRINT-001/save",
        json={"kind": "shared", "data": authored, "replace": True},
    )

    assert response.status_code == 200
    assert json.loads(shared_path.read_text(encoding="utf-8")) == authored
    assert override_path.read_text(encoding="utf-8") == previous_override
    assert starts[0][1]["scan_mode"] == "shared_collection"
    assert starts[0][1]["scope"]["scope_kind"] == "collection"
    assert starts[0][1]["scope"]["exhaustive"] is True
    assert list(shared_path.parent.glob("product_info.json.bak.*"))
    assert not (shared_path.parent / ".update").exists()


def test_override_replace_removes_disabled_fields_without_flattening_inheritance(
    milestone5_client, milestone5_app, monkeypatch
):
    _app, _catalogue, _database, _ids, shared_path, override_path = milestone5_app
    previous_shared = shared_path.read_text(encoding="utf-8")
    starts = []

    def complete_scan(*args, **kwargs):
        starts.append((args, kwargs))
        finish_catalogue_operation(kwargs["operation_id"], status="succeeded")

    monkeypatch.setattr("app.routes.start_scan", complete_scan)
    response = milestone5_client.post(
        "/edit_products/PRINT-001/save",
        json={"kind": "override", "data": {"tags": ["product-only"]}, "replace": True},
    )

    assert response.status_code == 200
    assert json.loads(override_path.read_text(encoding="utf-8")) == {
        "tags": ["product-only"]
    }
    assert shared_path.read_text(encoding="utf-8") == previous_shared
    assert starts[0][1]["scan_mode"] == "update"
    assert (override_path.parent / ".update").read_text(encoding="utf-8") == "1"


def test_products_api_points_to_detail_and_correct_source_editor(milestone5_client, milestone5_app):
    _app, *_rest, ids, _shared, _override = milestone5_app
    payload = milestone5_client.get("/api/edit_products?q=Aurora").get_json()["items"][0]
    assert payload["view_url"] == f"/products/{ids['variable']}"
    assert payload["edit_url"] == f"/edit_products/{ids['variable']}/edit/override"
    assert payload["collection_edit_url"] == f"/collections/{ids['collection']}/metadata"


def test_milestone5_pages_do_not_advertise_unsupported_actions(milestone5_client, milestone5_app):
    _app, *_rest, ids, _shared, _override = milestone5_app
    pages = [
        milestone5_client.get(f"/products/{ids['variable']}").get_data(as_text=True),
        milestone5_client.get(f"/collections/{ids['collection']}/metadata").get_data(as_text=True),
    ]
    for html in pages:
        assert "Upload image" not in html
        assert "Convert image" not in html
        assert "Sync to WooCommerce" not in html
        assert "Revenue" not in html


def test_advanced_editor_and_guided_editor_expose_required_client_safety_hooks():
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "app/static/assets/js/metadata-editor.js"
    ).read_text(encoding="utf-8")
    assert "beforeunload" in source
    assert "replace: true" in source
    assert "data-override-toggle" in source
    assert "setBusy(true)" in source
    assert "JSON.parse" in source
    assert "line" in source and "column" in source
    assert "window.confirm" in source
    assert "textContent" in source
    assert "template_url_tpl" in source


def test_minimal_override_remains_valid_and_does_not_flatten_collection(
    milestone5_client, milestone5_app, monkeypatch
):
    _app, _catalogue, _database, _ids, shared_path, override_path = milestone5_app
    monkeypatch.setattr(
        "app.routes.start_scan",
        lambda *_args, **kwargs: finish_catalogue_operation(
            kwargs["operation_id"], status="succeeded"
        ),
    )
    response = milestone5_client.post(
        "/edit_products/PRINT-001/save",
        json={"kind": "override", "data": {}, "replace": True},
    )
    assert response.status_code == 200
    assert json.loads(override_path.read_text(encoding="utf-8")) == {}
    assert json.loads(shared_path.read_text(encoding="utf-8"))["sku_prefix"] == "PRINT"


def test_collection_preview_query_count_is_bounded_for_large_collection(
    milestone5_app, milestone5_client
):
    app, catalogue, _database, ids, shared_path, _override_path = milestone5_app
    with app.app_context():
        collection = db.session.get(Collection, ids["collection"])
        for index in range(40):
            folder = catalogue / "Fictional Prints" / f"Bulk {index:02d}"
            folder.mkdir()
            db.session.add(
                Product(
                    collection_id=collection.id,
                    sku=f"PRINT-BULK-{index:02d}",
                    title=f"Bulk Fictional Product {index:02d}",
                    product_type="simple",
                    catalogue_status="active",
                    source_relpath=f"Fictional Prints/Bulk {index:02d}",
                    shared_json_relpath="Fictional Prints/product_info.json",
                    shared_json_path=str(shared_path),
                )
            )
        db.session.commit()

        statements = []

        def before_cursor_execute(*_args):
            statements.append(_args[2])

        event.listen(db.engine, "before_cursor_execute", before_cursor_execute)
        try:
            response = milestone5_client.get(
                f"/api/collections/{ids['collection']}/affected-products?per_page=24"
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", before_cursor_execute)

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["pagination"]["total"] == 42
    assert len(payload["items"]) == 24
    assert len(statements) <= 12


def test_product_detail_variations_are_bounded_and_lazily_paginated(
    milestone5_app, milestone5_client
):
    app, _catalogue, _database, ids, _shared_path, _override_path = milestone5_app
    with app.app_context():
        for index in range(2, 30):
            db.session.add(
                Variation(
                    product_id=ids["variable"],
                    sku=f"PRINT-001-{index:02d}",
                    regular_price=Decimal("12.00"),
                    catalogue_status="active",
                    menu_order=index,
                )
            )
        db.session.commit()

    html = milestone5_client.get(f"/products/{ids['variable']}").get_data(as_text=True)
    assert "30 total" in html
    assert "PRINT-001-23" in html
    assert "PRINT-001-24" not in html
    assert "Load more variations" in html

    payload = milestone5_client.get(
        f"/api/products/{ids['variable']}/detail-variations?page=2"
    ).get_json()
    assert payload["pagination"] == {"page": 2, "pages": 2, "per_page": 24, "total": 30}
    assert [item["sku"] for item in payload["items"]] == [
        f"PRINT-001-{index:02d}" for index in range(24, 30)
    ]
