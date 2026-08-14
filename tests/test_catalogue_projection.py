import json
import shutil
from datetime import datetime

import pytest

from app import create_app, db
from app.models import (
    Category,
    Collection,
    Product,
    ProductAsset,
    ProductAttribute,
    Settings,
    Tag,
    Variation,
)
from app.utils.ingest import ingest_rows_to_db
from config import Config


def _parent_row():
    return {
        "Type": "variable",
        "SKU": "FIC-P-0001",
        "Name": "Fictional Projection Product",
        "Parent": "",
        "Published": "0",
        "Is featured?": "1",
        "Visibility in catalog": "hidden",
        "Short description": "Synthetic short description",
        "Description": "Synthetic long description",
        "Tax status": "shipping",
        "Tax class": "fixture-tax",
        "In stock?": "1",
        "Stock": "7",
        "Backorders allowed?": "1",
        "Sold individually?": "1",
        "Weight (g)": "40",
        "Length (mm)": "10",
        "Width (mm)": "20",
        "Height (mm)": "3",
        "Allow customer reviews?": "0",
        "Purchase note": "Synthetic purchase note",
        "Sale price": "10.00",
        "Regular price": "12.50",
        "Date sale price starts": "2026-01-01",
        "Date sale price ends": "2026-01-31",
        "Categories": "Fictional Goods, Projection Fixtures",
        "Tags": "projection, synthetic",
        "Shipping class": "",
        "Images": "https://invalid.example/one.webp, https://invalid.example/two.webp",
        "Download limit": "4",
        "Download expiry days": "30",
        "Grouped products": "GROUP-1, GROUP-2",
        "Upsells": "UP-1",
        "Cross-sells": "CROSS-1",
        "External URL": "https://invalid.example/external",
        "Button text": "Fixture button",
        "Position": "5",
        "Meta: _yoast_wpseo_title": "Synthetic SEO title",
        "Meta: _yoast_wpseo_metadesc": "Synthetic SEO description",
        "Attribute 1 name": "Size",
        "Attribute 1 value(s)": "Small, Large",
        "Attribute 1 visible": "1",
        "Attribute 1 global": "1",
    }


def _variation_row():
    row = _parent_row()
    row.update(
        {
            "Type": "variation",
            "SKU": "FIC-P-0001-1",
            "Parent": "FIC-P-0001",
            "Name": "Fictional Projection Product",
            "Regular price": "13.50",
            "Images": "https://invalid.example/variation.webp",
            "Attribute 1 value(s)": "Large",
        }
    )
    return row


@pytest.mark.parametrize(
    ("collection_type", "row_type", "nested_product"),
    [
        ("Simple", "simple", True),
        ("Variable Collection", "variable", True),
        ("Single Variable", "variable", False),
    ],
)
def test_all_collection_types_use_exact_relationship_and_source_semantics(
    tmp_path, quiet_log, collection_type, row_type, nested_product
):
    database = tmp_path / "collection-type.db"
    catalogue = tmp_path / "catalogue"
    collection_folder = catalogue / "Exact Type Collection"
    product_folder = (
        collection_folder / "Nested Product" if nested_product else collection_folder
    )
    product_folder.mkdir(parents=True)
    (collection_folder / "product_info.json").write_text(
        json.dumps(
            {
                "collection_type": collection_type,
                "sku_prefix": "FIC-T-",
            }
        ),
        encoding="utf-8",
    )
    (product_folder / ".scanned").write_text(
        '{"sku":"FIC-T-0001"}', encoding="utf-8"
    )
    row = _parent_row()
    row.update({"Type": row_type, "SKU": "FIC-T-0001"})

    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{database}"
    try:
        app = create_app()
        with app.app_context():
            db.session.add(
                Settings(
                    product_folder=str(catalogue),
                    output_folder=str(tmp_path / "output"),
                    url_prefix="https://invalid.example/",
                )
            )
            db.session.commit()
            ingest_rows_to_db([row], log=quiet_log)

            collection = Collection.query.one()
            product = Product.query.one()
            expected_product_source = "Exact Type Collection"
            if nested_product:
                expected_product_source += "/Nested Product"
            assert collection.collection_type == collection_type
            assert product.collection_id == collection.id
            assert product.collection_type == collection_type
            assert product.source_relpath == expected_product_source
            assert product.shared_json_relpath == (
                "Exact Type Collection/product_info.json"
            )
            assert product.override_json_relpath is None
            assert product.effective_json_relpath == product.shared_json_relpath
    finally:
        Config.SQLALCHEMY_DATABASE_URI = original_uri


def test_ingest_activates_hierarchy_full_projection_and_portable_provenance(
    tmp_path, quiet_log
):
    database = tmp_path / "projection.db"
    catalogue = tmp_path / "mounted-catalogue"
    collection_folder = catalogue / "Fictional Collection"
    product_folder = collection_folder / "Fictional Product"
    product_folder.mkdir(parents=True)
    shared_json = collection_folder / "product_info.json"
    override_json = product_folder / "product_info.json"
    shared_json.write_text(
        json.dumps(
            {
                "collection_type": "Variable Collection",
                "sku_prefix": "FIC-P-",
                "title": "Fictional Collection Title",
            }
        ),
        encoding="utf-8",
    )
    override_json.write_text('{"title": "Synthetic override"}', encoding="utf-8")
    (product_folder / ".scanned").write_text(
        json.dumps({"sku": "FIC-P-0001"}), encoding="utf-8"
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
            existing_product = Product(
                sku="FIC-P-0001",
                title="Old title",
                woo_id=4101,
                woo_synced_at=datetime(2026, 1, 2, 3, 4, 5),
            )
            db.session.add(existing_product)
            db.session.flush()
            existing_variation = Variation(
                product_id=existing_product.id,
                sku="FIC-P-0001-1",
                woo_id=4201,
                woo_synced_at=datetime(2026, 2, 3, 4, 5, 6),
            )
            db.session.add(existing_variation)
            db.session.commit()
            product_id = existing_product.id
            variation_id = existing_variation.id

            parent_row = _parent_row()
            variation_row = _variation_row()
            summary = ingest_rows_to_db(
                [parent_row, variation_row], log=quiet_log
            )

            collection = Collection.query.one()
            product = Product.query.filter_by(sku="FIC-P-0001").one()
            variation = Variation.query.filter_by(sku="FIC-P-0001-1").one()

            assert summary == {
                "products_created": 0,
                "products_updated": 1,
                "variations_created": 0,
                "variations_updated": 1,
            }
            assert collection.collection_type == "Variable Collection"
            assert collection.sku_prefix == "FIC-P-"
            assert collection.source_relpath == "Fictional Collection"
            assert (
                collection.shared_json_relpath
                == "Fictional Collection/product_info.json"
            )
            assert collection.products == [product]

            assert product.id == product_id
            assert product.collection_id == collection.id
            assert product.collection_type == "Variable Collection"
            assert product.source_relpath == "Fictional Collection/Fictional Product"
            assert product.shared_json_relpath == "Fictional Collection/product_info.json"
            assert (
                product.override_json_relpath
                == "Fictional Collection/Fictional Product/product_info.json"
            )
            assert product.effective_json_relpath == product.override_json_relpath
            assert str(tmp_path) not in product.source_relpath
            assert json.loads(product.resolved_row_json) == parent_row
            assert product.woo_id == 4101
            assert product.woo_synced_at == datetime(2026, 1, 2, 3, 4, 5)

            assert product.published is False
            assert product.status == "draft"
            assert product.featured is True
            assert product.catalog_visibility == "hidden"
            assert product.tax_status == "shipping"
            assert product.tax_class == "fixture-tax"
            assert product.in_stock is True
            assert product.manage_stock is True
            assert product.stock_quantity == 7
            assert product.backorders == "notify"
            assert product.sold_individually is True
            assert product.reviews_allowed is False
            assert product.purchase_note == "Synthetic purchase note"
            assert product.download_limit == 4
            assert product.download_expiry_days == 30
            assert product.grouped_products == "GROUP-1, GROUP-2"
            assert product.external_url == "https://invalid.example/external"
            assert product.button_text == "Fixture button"
            assert product.menu_order == 5
            assert product.meta_title == "Synthetic SEO title"
            assert product.meta_description == "Synthetic SEO description"

            assert {category.name for category in product.categories} == {
                "Fictional Goods",
                "Projection Fixtures",
            }
            assert {tag.name for tag in product.tags} == {"projection", "synthetic"}
            assert Category.query.count() == 2
            assert Tag.query.count() == 2
            attribute = ProductAttribute.query.one()
            assert attribute.product_id == product.id
            assert attribute.name == "Size"
            assert attribute.values == "Small, Large"
            assert attribute.visible is True
            assert attribute.is_global is True
            assert attribute.position == 0

            assert variation.id == variation_id
            assert variation in product.variations
            assert variation.source_relpath == product.source_relpath
            assert json.loads(variation.resolved_row_json) == variation_row
            assert variation.woo_id == 4201
            assert variation.woo_synced_at == datetime(2026, 2, 3, 4, 5, 6)
            assert {item.name: item.value for item in variation.attributes} == {
                "Size": "Large"
            }
            assert variation.attributes[0].is_global is True
            assert variation.attributes[0].position == 0

            assets = {
                asset.label: asset for asset in ProductAsset.query.filter_by(
                    product_id=product.id
                )
            }
            assert assets["shared"].source_relpath == product.shared_json_relpath
            assert assets["override"].source_relpath == product.override_json_relpath
            assert assets["shared"].path == str(shared_json)
            assert assets["override"].path == str(override_json)

            collection_id = collection.id
            relocated_catalogue = tmp_path / "different-mount"
            shutil.copytree(catalogue, relocated_catalogue)
            settings = Settings.query.one()
            settings.product_folder = str(relocated_catalogue)
            db.session.commit()

            ingest_rows_to_db([parent_row, variation_row], log=quiet_log)
            db.session.expire_all()

            relocated_collection = Collection.query.one()
            relocated_product = Product.query.filter_by(sku="FIC-P-0001").one()
            assert relocated_collection.id == collection_id
            assert relocated_collection.source_relpath == "Fictional Collection"
            assert relocated_collection.root_path == str(
                relocated_catalogue / "Fictional Collection"
            )
            assert (
                relocated_product.source_relpath
                == "Fictional Collection/Fictional Product"
            )
            assert relocated_product.product_dir == str(
                relocated_catalogue / "Fictional Collection" / "Fictional Product"
            )
    finally:
        Config.SQLALCHEMY_DATABASE_URI = original_uri
