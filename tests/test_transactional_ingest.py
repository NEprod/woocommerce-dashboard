import json
from datetime import datetime

import pytest

from app import create_app, db
from app.models import (
    CatalogueOperation,
    CatalogueOperationItem,
    Product,
    ProductAsset,
    Settings,
    Variation,
)
from app.utils.ingest import ingest_rows_to_db
from config import Config


SIGNIFICANT_STAGES = (
    "collection",
    "parent",
    "product_images",
    "assets",
    "categories_tags",
    "product_attributes",
    "variations",
    "variation_attributes",
    "variation_images",
    "operation_item",
)


@pytest.fixture
def transactional_app(tmp_path):
    database = tmp_path / "transactional.db"
    catalogue = tmp_path / "catalogue"
    collection = catalogue / "Transactional Collection"
    collection.mkdir(parents=True)
    (collection / "product_info.json").write_text(
        json.dumps(
            {
                "collection_type": "Variable Collection",
                "sku_prefix": "FIC-TX-",
            }
        ),
        encoding="utf-8",
    )

    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{database}"
    try:
        app = create_app()
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with app.app_context():
            db.session.add(
                Settings(
                    product_folder=str(catalogue),
                    output_folder=str(tmp_path / "output"),
                    url_prefix="https://invalid.example/",
                )
            )
            db.session.commit()
        yield app, catalogue
    finally:
        with app.app_context():
            db.session.remove()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


def _write_product(catalogue, name, sku):
    folder = catalogue / "Transactional Collection" / name
    folder.mkdir()
    (folder / "product_info.json").write_text(
        json.dumps({"title": f"{name} override"}), encoding="utf-8"
    )
    (folder / ".scanned").write_text(json.dumps({"sku": sku}), encoding="utf-8")
    return folder


def _rows(sku, title="Original", image_suffix="original", price="10.00"):
    parent = {
        "Type": "variable",
        "SKU": sku,
        "Name": title,
        "Published": "1",
        "Is featured?": "0",
        "Visibility in catalog": "visible",
        "Regular price": price,
        "Sale price": "",
        "Date sale price starts": "",
        "Date sale price ends": "",
        "Weight (g)": "40",
        "Length (mm)": "10",
        "Width (mm)": "20",
        "Height (mm)": "3",
        "Shipping class": "",
        "Short description": f"{title} short",
        "Description": f"{title} description",
        "Tax status": "taxable",
        "Tax class": "",
        "In stock?": "1",
        "Stock": "5",
        "Backorders allowed?": "0",
        "Sold individually?": "0",
        "Allow customer reviews?": "1",
        "Purchase note": "",
        "Categories": "Transactional Category",
        "Tags": "transactional-tag",
        "Images": f"https://invalid.example/{image_suffix}.webp",
        "Download limit": "",
        "Download expiry days": "",
        "Grouped products": "",
        "Upsells": "",
        "Cross-sells": "",
        "External URL": "",
        "Button text": "",
        "Position": "0",
        "Meta: _yoast_wpseo_title": f"{title} SEO",
        "Meta: _yoast_wpseo_metadesc": f"{title} SEO description",
        "Attribute 1 name": "Size",
        "Attribute 1 value(s)": "Large",
        "Attribute 1 visible": "1",
        "Attribute 1 global": "1",
    }
    variation = dict(parent)
    variation.update(
        {
            "Type": "variation",
            "SKU": f"{sku}-1",
            "Parent": sku,
            "Regular price": str(float(price) + 2),
            "Images": f"https://invalid.example/{image_suffix}-variation.webp",
            "Attribute 1 value(s)": "Large",
        }
    )
    return [parent, variation]


def _add_operation(operation_id):
    db.session.add(
        CatalogueOperation(
            id=operation_id,
            operation_type="append",
            status="running",
            scope="{}",
        )
    )
    db.session.commit()


def _catalogue_snapshot(sku):
    db.session.expire_all()
    product = Product.query.filter_by(sku=sku).one()
    return {
        "collection": (
            product.collection.id,
            product.collection.name,
            product.collection.collection_type,
            product.collection.source_relpath,
            product.collection.updated_at,
        ),
        "product": (
            product.id,
            product.title,
            str(product.regular_price),
            product.created_at,
            product.local_updated_at,
            product.woo_id,
            product.woo_synced_at,
            product.resolved_row_json,
        ),
        "images": [
            (image.id, image.url, image.position, image.woo_id)
            for image in product.images
        ],
        "assets": [
            (asset.id, asset.label, asset.path, asset.source_relpath, asset.created_at)
            for asset in sorted(product.assets, key=lambda item: item.label)
        ],
        "categories": [
            (item.id, item.name, item.woo_id)
            for item in sorted(product.categories, key=lambda item: item.name)
        ],
        "tags": [
            (item.id, item.name, item.woo_id)
            for item in sorted(product.tags, key=lambda item: item.name)
        ],
        "attributes": [
            (
                item.id,
                item.name,
                item.values,
                item.visible,
                item.is_global,
                item.position,
            )
            for item in product.attributes
        ],
        "variations": [
            (
                variation.id,
                variation.sku,
                str(variation.regular_price),
                variation.local_updated_at,
                variation.woo_id,
                variation.woo_synced_at,
                variation.resolved_row_json,
                tuple(
                    (
                        item.id,
                        item.name,
                        item.value,
                        item.visible,
                        item.is_global,
                        item.position,
                    )
                    for item in variation.attributes
                ),
                tuple(
                    (image.id, image.url, image.position)
                    for image in variation.images
                ),
            )
            for variation in sorted(product.variations, key=lambda item: item.sku)
        ],
    }


def _seed_identity_placeholders(catalogue, sku):
    _write_product(catalogue, "Seed Product", sku)
    ingest_rows_to_db(_rows(sku), log=lambda *args, **kwargs: None)
    product = Product.query.filter_by(sku=sku).one()
    variation = Variation.query.filter_by(sku=f"{sku}-1").one()
    product.woo_id = 5101
    product.woo_synced_at = datetime(2026, 1, 2, 3, 4, 5)
    product.images[0].woo_id = 5201
    product.categories[0].woo_id = 5301
    product.tags[0].woo_id = 5401
    variation.woo_id = 5501
    variation.woo_synced_at = datetime(2026, 2, 3, 4, 5, 6)
    db.session.commit()


@pytest.mark.parametrize("failing_stage", SIGNIFICANT_STAGES)
def test_failure_at_each_parent_stage_rolls_back_the_complete_existing_parent(
    transactional_app, failing_stage
):
    app, catalogue = transactional_app
    sku = "FIC-TX-0001"
    with app.app_context():
        _seed_identity_placeholders(catalogue, sku)
        product = Product.query.filter_by(sku=sku).one()
        db.session.add(
            ProductAsset(
                product_id=product.id,
                path="/fictional/obsolete.json",
                kind="info",
                label="obsolete",
            )
        )
        db.session.commit()
        before = _catalogue_snapshot(sku)
        (catalogue / "Transactional Collection" / "product_info.json").write_text(
            json.dumps(
                {
                    "collection_type": "Changed Variable Collection",
                    "sku_prefix": "FIC-TX-",
                }
            ),
            encoding="utf-8",
        )
        changed_rows = _rows(
            sku, title="Changed", image_suffix="changed", price="20.00"
        )
        changed_rows[0]["Categories"] = "Changed Category"
        changed_rows[0]["Tags"] = "changed-tag"
        changed_rows[0]["Attribute 1 name"] = "Changed Size"
        changed_rows[0]["Attribute 1 value(s)"] = "Changed Large"
        changed_rows[1]["Attribute 1 name"] = "Changed Size"
        changed_rows[1]["Attribute 1 value(s)"] = "Changed Large"
        operation_id = f"failure-{failing_stage}"
        _add_operation(operation_id)

        def inject(stage, affected_sku):
            if stage == failing_stage:
                raise RuntimeError(
                    f"fixture failure at {stage} token=do-not-store"
                )

        summary = ingest_rows_to_db(
            changed_rows,
            log=lambda *args, **kwargs: None,
            operation_id=operation_id,
            failure_injector=inject,
        )

        assert summary["products_failed"] == 1
        assert _catalogue_snapshot(sku) == before
        item = CatalogueOperationItem.query.filter_by(
            operation_id=operation_id
        ).one()
        assert item.sku == sku
        assert item.status == "failed"
        assert item.database_state == "rolled_back"
        assert item.source_path == "Transactional Collection/Seed Product"
        assert failing_stage in item.error
        assert "do-not-store" not in item.error
        assert "[REDACTED]" in item.error


def test_late_parent_failure_does_not_remove_an_unrelated_committed_parent(
    transactional_app,
):
    app, catalogue = transactional_app
    _write_product(catalogue, "First Product", "FIC-TX-0001")
    _write_product(catalogue, "Second Product", "FIC-TX-0002")
    with app.app_context():
        operation_id = "two-parent-operation"
        _add_operation(operation_id)

        def inject(stage, affected_sku):
            if stage == "variation_images" and affected_sku == "FIC-TX-0002":
                raise RuntimeError("second parent failure")

        summary = ingest_rows_to_db(
            _rows("FIC-TX-0001") + _rows("FIC-TX-0002"),
            log=lambda *args, **kwargs: None,
            operation_id=operation_id,
            failure_injector=inject,
        )

        assert summary == {
            "products_created": 1,
            "products_updated": 0,
            "products_failed": 1,
            "variations_created": 1,
            "variations_updated": 0,
        }
        assert Product.query.filter_by(sku="FIC-TX-0001").one().variations
        assert Product.query.filter_by(sku="FIC-TX-0002").first() is None
        items = {
            item.sku: (item.status, item.database_state)
            for item in CatalogueOperationItem.query.filter_by(
                operation_id=operation_id
            )
        }
        assert items == {
            "FIC-TX-0001": ("succeeded", "committed"),
            "FIC-TX-0002": ("failed", "rolled_back"),
        }


def test_successful_update_preserves_internal_ids_timestamps_and_woo_placeholders(
    transactional_app,
):
    app, catalogue = transactional_app
    sku = "FIC-TX-0001"
    with app.app_context():
        _seed_identity_placeholders(catalogue, sku)
        before = _catalogue_snapshot(sku)
        operation_id = "successful-update"
        _add_operation(operation_id)

        summary = ingest_rows_to_db(
            _rows(sku, title="Changed", price="20.00"),
            log=lambda *args, **kwargs: None,
            operation_id=operation_id,
        )
        after = _catalogue_snapshot(sku)

        assert summary["products_updated"] == 1
        assert summary["products_failed"] == 0
        assert after["product"][0] == before["product"][0]
        assert after["product"][3] == before["product"][3]
        assert after["product"][5:7] == before["product"][5:7]
        assert after["collection"][0] == before["collection"][0]
        assert after["images"][0][0] == before["images"][0][0]
        assert after["images"][0][3] == 5201
        assert after["assets"][0][0] == before["assets"][0][0]
        assert after["assets"][0][4] == before["assets"][0][4]
        assert after["categories"][0] == before["categories"][0]
        assert after["tags"][0] == before["tags"][0]
        assert after["attributes"][0][0] == before["attributes"][0][0]
        assert after["variations"][0][0] == before["variations"][0][0]
        assert after["variations"][0][4:6] == before["variations"][0][4:6]
        assert after["variations"][0][7][0][0] == before["variations"][0][7][0][0]
        assert after["variations"][0][8][0][0] == before["variations"][0][8][0][0]
        item = CatalogueOperationItem.query.filter_by(
            operation_id=operation_id
        ).one()
        assert item.status == "succeeded"
        assert item.database_state == "committed"
        assert item.source_path == "Transactional Collection/Seed Product"


def test_missing_variation_parent_is_a_clear_failed_operation_item(
    transactional_app,
):
    app, _ = transactional_app
    orphan = _rows("FIC-TX-MISSING")[1]
    with app.app_context():
        operation_id = "missing-parent"
        _add_operation(operation_id)

        summary = ingest_rows_to_db(
            [orphan],
            log=lambda *args, **kwargs: None,
            operation_id=operation_id,
        )

        assert summary["products_failed"] == 1
        assert Product.query.count() == 0
        assert Variation.query.count() == 0
        item = CatalogueOperationItem.query.filter_by(
            operation_id=operation_id
        ).one()
        assert item.sku == "FIC-TX-MISSING"
        assert item.status == "failed"
        assert item.database_state == "rolled_back"
        assert "missing parent row" in item.error.lower()
