from pathlib import Path

from app import create_app, db
from app.models import Product, Settings, Variation
from app.utils.ingest import ingest_rows_to_db
from config import Config


def _row(row_type, sku, **overrides):
    row = {
        "Type": row_type,
        "SKU": sku,
        "Name": "Fictional Ingest Product",
        "Regular price": "12.50",
        "Sale price": "10.00",
        "Date sale price starts": "2026-01-01",
        "Date sale price ends": "2026-01-31",
        "Weight (g)": "40",
        "Length (mm)": "10",
        "Width (mm)": "20",
        "Height (mm)": "3",
        "Short description": "Synthetic",
        "Description": "Synthetic database fixture",
        "Images": "https://invalid.example/one.webp, https://invalid.example/two.webp",
        "Categories": "Ignored Category",
        "Tags": "ignored-tag",
        "Meta: _yoast_wpseo_title": "Ignored SEO title",
    }
    row.update(overrides)
    return row


def test_ingest_maps_current_supported_subset_to_temporary_database(tmp_path, quiet_log):
    database = tmp_path / "test.db"
    catalogue = tmp_path / "catalogue"
    product_folder = catalogue / "Fictional Collection" / "Fictional Product"
    product_folder.mkdir(parents=True)
    (product_folder / ".scanned").write_text('{"sku":"FIC-0001"}')
    (product_folder.parent / "product_info.json").write_text(
        '{"collection_type":"Variable Collection","sku_prefix":"FIC-"}'
    )

    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{database}"
    try:
        app = create_app()
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with app.app_context():
            db.session.add(Settings(product_folder=str(catalogue), output_folder=str(tmp_path / "out"), url_prefix="https://invalid.example/"))
            db.session.commit()
            rows = [
                _row("variable", "FIC-0001"),
                _row(
                    "variation",
                    "FIC-0001-1",
                    Parent="FIC-0001",
                    **{"Attribute 1 name": "Size", "Attribute 1 value(s)": "Large"},
                ),
            ]
            summary = ingest_rows_to_db(rows, log=quiet_log)

            product = Product.query.filter_by(sku="FIC-0001").one()
            variation = Variation.query.filter_by(sku="FIC-0001-1").one()
            assert summary == {
                "products_created": 1,
                "products_updated": 0,
                "products_failed": 0,
                "variations_created": 1,
                "variations_updated": 0,
            }
            assert str(product.regular_price) == "12.50"
            assert len(product.images) == 2
            assert variation.product_id == product.id
            assert {a.name: a.value for a in variation.attributes} == {"Size": "Large"}
            assert {category.name for category in product.categories} == {
                "Ignored Category"
            }
            assert {tag.name for tag in product.tags} == {"ignored-tag"}
            assert product.collection_id is not None
            assert product.collection_type == "Variable Collection"
            assert product.meta_title == "Ignored SEO title"
    finally:
        Config.SQLALCHEMY_DATABASE_URI = original_uri
