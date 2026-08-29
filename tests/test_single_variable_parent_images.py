import json
from pathlib import Path

import pytest
from PIL import Image

from app import create_app, db
from app.catalogue_images import product_image_diagnostics, resolve_product_catalogue_image
from app.models import (
    Product,
    ProductAsset,
    ProductImage,
    Settings,
    User,
    Variation,
)
from app.utils.ingest import ingest_reconstruction_rows, ingest_rows_to_db
from app.utils.catalogue_paths import (
    AmbiguousReservedDirectoryError,
    find_reserved_directory,
)
from app.utils.scanner import scan_collection
from config import Config


URL_PREFIX = "https://uploads.invalid/single-variable/"


def _image(path: Path, image_format=None, colour="lime"):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (31, 23), colour).save(path, format=image_format)


@pytest.fixture
def single_variable_projection(tmp_path):
    catalogue = tmp_path / "catalogue"
    collection = catalogue / "SingleVariableFixture"
    output = tmp_path / "output"
    instance = tmp_path / "instance"
    output.mkdir()
    instance.mkdir()

    metadata = {
        "collection_type": "Single Variable",
        "sku_prefix": "SVF-",
        "title": "Fictional Art Prints",
        "price": "19.00",
        "attributes": {
            "Style": ["Hero A", "Hero B"],
            "Size": ["A5", "A4"],
        },
        "image_attributes": ["Style", "Size"],
        "live": False,
    }
    collection.mkdir(parents=True)
    (collection / "product_info.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    # Deliberately create gallery first: scanner order must not depend on creation order.
    _image(collection / "Parent" / "parent-gallery.jpeg", "JPEG", "teal")
    _image(collection / "Parent" / "parent-primary.PNG", "PNG", "navy")
    _image(collection / "Hero A" / "A5" / "hero-a-a5-secondary.JPG", "JPEG", "blue")
    _image(collection / "Hero A" / "A5" / "hero-a-a5.png", "PNG", "cyan")
    _image(collection / "Hero A" / "A4" / "hero-a-a4.webp", "WEBP", "purple")
    _image(collection / "Hero B" / "A5" / "hero-b-a5.png", "PNG", "orange")

    rows = scan_collection(
        str(collection), URL_PREFIX, str(output), log=lambda *_a, **_k: None
    )
    marker = json.loads((collection / ".scanned").read_text(encoding="utf-8"))

    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{instance / 'site.db'}"
    try:
        app = create_app()
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with app.app_context():
            db.session.add(
                Settings(
                    product_folder=str(catalogue),
                    output_folder=str(output),
                    url_prefix=URL_PREFIX,
                )
            )
            db.session.add(
                User(
                    email="parent-images@example.test",
                    username="parent-images-admin",
                    password="fixture-password",
                    is_admin=True,
                )
            )
            db.session.commit()
            summary = ingest_rows_to_db(rows, log=lambda *_a, **_k: None)
            product = Product.query.one()
            yield {
                "app": app,
                "catalogue": catalogue,
                "collection": collection,
                "output": output,
                "rows": rows,
                "marker": marker,
                "summary": summary,
                "product_id": product.id,
                "sku": product.sku,
            }
    finally:
        Config.SQLALCHEMY_DATABASE_URI = original_uri


def test_single_variable_parent_folder_is_ordered_and_parent_owned(single_variable_projection):
    state = single_variable_projection
    parent = state["rows"][0]
    variations = state["rows"][1:]

    assert parent["Type"] == "variable"
    assert parent["Images"].split(", ") == [
        f"{URL_PREFIX}parent-gallery.webp",
        f"{URL_PREFIX}parent-primary.webp",
    ]
    assert state["marker"]["images_used"] == [
        "parent-gallery.jpeg",
        "parent-primary.PNG",
    ]
    assert all("parent-" not in row["Images"] for row in variations)
    assert {row["Attribute 1 value(s)"] for row in variations} == {
        "Hero A",
        "Hero B",
    }
    assert {row["Attribute 2 value(s)"] for row in variations} == {"A5", "A4"}
    assert all(
        entry.get("images_used") is not None
        for entry in state["marker"]["variations"]
    )


def test_parent_urls_and_portable_sources_are_projected(single_variable_projection):
    state = single_variable_projection
    app = state["app"]
    with app.app_context():
        product = db.session.get(Product, state["product_id"])
        assert [image.url for image in product.images] == [
            f"{URL_PREFIX}parent-gallery.webp",
            f"{URL_PREFIX}parent-primary.webp",
        ]
        assert [image.position for image in product.images] == [0, 1]
        parent_assets = ProductAsset.query.filter_by(
            product_id=product.id, variation_id=None, kind="image"
        ).order_by(ProductAsset.label).all()
        assert [asset.source_relpath for asset in parent_assets] == [
            "SingleVariableFixture/Parent/parent-gallery.jpeg",
            "SingleVariableFixture/Parent/parent-primary.PNG",
        ]
        assert [asset.is_primary for asset in parent_assets] == [True, False]
        assert all(str(state["output"]) not in asset.path for asset in parent_assets)
        assert resolve_product_catalogue_image(product).name == "parent-gallery.jpeg"
        variation_assets = ProductAsset.query.filter(
            ProductAsset.product_id == product.id,
            ProductAsset.variation_id.isnot(None),
            ProductAsset.kind == "image",
        ).all()
        assert {asset.source_relpath for asset in variation_assets} == {
            "SingleVariableFixture/Hero A/A5/hero-a-a5-secondary.JPG",
            "SingleVariableFixture/Hero A/A5/hero-a-a5.png",
            "SingleVariableFixture/Hero A/A4/hero-a-a4.webp",
            "SingleVariableFixture/Hero B/A5/hero-b-a5.png",
        }


def test_reconstruction_recreates_parent_and_variation_image_projection(single_variable_projection):
    state = single_variable_projection
    app = state["app"]
    with app.app_context():
        original_parent = [
            (image.url, image.position)
            for image in ProductImage.query.order_by(ProductImage.position).all()
        ]
        original_variations = {
            variation.sku: [(image.url, image.position) for image in variation.images]
            for variation in Variation.query.all()
        }
        Product.query.delete()
        db.session.commit()
        result = ingest_reconstruction_rows(
            state["rows"], operation_id=None, log=lambda *_a, **_k: None
        )
        rebuilt = Product.query.one()
        assert result["products_created"] == 1
        assert [(image.url, image.position) for image in rebuilt.images] == original_parent
        assert {
            variation.sku: [(image.url, image.position) for image in variation.images]
            for variation in rebuilt.variations
        } == original_variations
        assert ProductAsset.query.filter_by(
            product_id=rebuilt.id, variation_id=None, kind="image"
        ).count() == 2


def test_product_detail_products_and_dashboard_use_parent_primary(single_variable_projection):
    state = single_variable_projection
    app = state["app"]
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True

    detail = client.get(f"/products/{state['product_id']}")
    products = client.get("/products")
    products_payload = client.get("/api/edit_products").get_json()
    dashboard = client.get("/")
    assert detail.status_code == products.status_code == dashboard.status_code == 200
    assert b"Parent image gallery" in detail.data
    assert b"parent-gallery.webp" in detail.data
    assert b"Parent preview fallback" not in detail.data.split(b"Parent image gallery", 1)[0]
    thumbnail_path = f"/catalogue-images/products/{state['product_id']}".encode()
    assert products.status_code == 200
    product_row = next(
        item
        for group in products_payload["groups"]
        for item in group["products"]
        if item["id"] == state["product_id"]
    )
    assert product_row["thumbnail"] == thumbnail_path.decode()
    assert thumbnail_path in dashboard.data
    preview = client.get(thumbnail_path.decode())
    assert preview.status_code == 200
    assert preview.headers["Content-Type"] == "image/jpeg"
    assert preview.headers["X-Content-Type-Options"] == "nosniff"


def test_no_image_binary_or_output_path_is_persisted(single_variable_projection):
    state = single_variable_projection
    database = Path(state["app"].config["SQLALCHEMY_DATABASE_URI"].removeprefix("sqlite:///"))
    payload = database.read_bytes()
    assert b"parent-gallery.webp" in payload
    assert b"\x89PNG\r\n\x1a\n" not in payload
    with state["app"].app_context():
        image_assets = ProductAsset.query.filter_by(kind="image").all()
        assert all(str(state["output"]) not in asset.path for asset in image_assets)
        assert all(
            str(state["output"]) not in (asset.source_relpath or "")
            for asset in image_assets
        )


def test_parent_corruption_is_diagnostic_and_next_parent_remains_preferred(
    single_variable_projection,
):
    state = single_variable_projection
    corrupt = state["collection"] / "Parent" / "parent-gallery.jpeg"
    corrupt.write_text("not an image", encoding="utf-8")
    with state["app"].app_context():
        product = db.session.get(Product, state["product_id"])
        diagnostics = product_image_diagnostics(product)
        assert diagnostics[0]["state"] == "source_corrupt"
        assert diagnostics[0]["source_reference"].endswith("parent-gallery.jpeg")
        assert resolve_product_catalogue_image(product).name == "parent-primary.PNG"


def test_variation_fallback_is_used_only_after_all_parent_sources_are_unusable(
    single_variable_projection,
):
    state = single_variable_projection
    for source in (state["collection"] / "Parent").iterdir():
        source.unlink()
    with state["app"].app_context():
        product = db.session.get(Product, state["product_id"])
        selected = resolve_product_catalogue_image(product)
        assert "parent" not in selected.parts
        assert selected.name in {
            "hero-a-a5-secondary.JPG",
            "hero-a-a5.png",
            "hero-a-a4.webp",
            "hero-b-a5.png",
        }
    client = state["app"].test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True
    detail = client.get(f"/products/{state['product_id']}")
    assert detail.status_code == 200
    assert b"Parent fallback from variation" in detail.data


@pytest.mark.parametrize("directory_name", ["parent", "Parent", "PARENT", "PaReNt"])
def test_reserved_parent_directory_is_case_insensitive_and_preserves_source_case(
    tmp_path, directory_name
):
    collection = tmp_path / "catalogue" / "CaseFixture"
    output = tmp_path / "output"
    output.mkdir(parents=True)
    collection.mkdir(parents=True)
    (collection / "product_info.json").write_text(
        json.dumps(
            {
                "collection_type": "Single Variable",
                "sku_prefix": "CASE-",
                "title": "Case Fixture",
                "price": "12.00",
                "attributes": {"Style": ["Hero A"], "Size": ["A5"]},
                "image_attributes": ["Style", "Size"],
            }
        ),
        encoding="utf-8",
    )
    _image(collection / directory_name / "01-parent.png", "PNG", "navy")
    _image(collection / directory_name / "02-parent.jpg", "JPEG", "teal")
    _image(collection / "Hero A" / "A5" / "variation.png", "PNG", "orange")

    rows = scan_collection(
        str(collection), URL_PREFIX, str(output), log=lambda *_a, **_k: None
    )
    marker = json.loads((collection / ".scanned").read_text(encoding="utf-8"))
    selected = find_reserved_directory(collection, "parent")

    assert selected == collection / directory_name
    assert selected.name == directory_name
    assert rows[0]["Images"].split(", ") == [
        f"{URL_PREFIX}01-parent.webp",
        f"{URL_PREFIX}02-parent.webp",
    ]
    assert marker["images_used"] == ["01-parent.png", "02-parent.jpg"]
    assert rows[1]["Attribute 1 value(s)"] == "Hero A"
    assert rows[1]["Attribute 2 value(s)"] == "A5"
    assert all(
        row["Attribute 1 value(s)"].casefold() != "parent" for row in rows[1:]
    )


def test_duplicate_reserved_parent_directories_are_rejected_without_host_paths(
    tmp_path, monkeypatch
):
    collection = tmp_path / "catalogue" / "CollisionFixture"
    collection.mkdir(parents=True)
    physical = collection / "Parent"
    physical.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    (collection / "product_info.json").write_text(
        json.dumps(
            {
                "collection_type": "Single Variable",
                "sku_prefix": "COLLISION-",
                "title": "Collision Fixture",
                "price": "12.00",
                "attributes": {"Style": ["Hero A"], "Size": ["A5"]},
                "image_attributes": ["Style", "Size"],
            }
        ),
        encoding="utf-8",
    )
    entries = [physical, collection / "parent"]
    original_iterdir = Path.iterdir

    def collision_iterdir(path):
        if path == collection:
            return iter(entries)
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", collision_iterdir)

    with pytest.raises(AmbiguousReservedDirectoryError) as raised:
        find_reserved_directory(collection, "parent")

    message = str(raised.value)
    assert "Parent" in message and "parent" in message
    assert str(tmp_path) not in message

    with pytest.raises(AmbiguousReservedDirectoryError) as scan_raised:
        scan_collection(
            str(collection), URL_PREFIX, str(output), log=lambda *_a, **_k: None
        )
    assert "Parent" in str(scan_raised.value)
    assert "parent" in str(scan_raised.value)
    assert str(tmp_path) not in str(scan_raised.value)


@pytest.mark.parametrize("reserved_value", ["parent", "Parent", "PARENT", "PaReNt"])
def test_reserved_parent_name_cannot_be_an_image_attribute_directory(
    tmp_path, reserved_value
):
    collection = tmp_path / "catalogue" / "ReservedAttributeFixture"
    output = tmp_path / "output"
    output.mkdir(parents=True)
    collection.mkdir(parents=True)
    (collection / "product_info.json").write_text(
        json.dumps(
            {
                "collection_type": "Single Variable",
                "sku_prefix": "RESERVED-",
                "title": "Reserved Attribute Fixture",
                "price": "12.00",
                "attributes": {"Style": [reserved_value], "Size": ["A5"]},
                "image_attributes": ["Style", "Size"],
            }
        ),
        encoding="utf-8",
    )
    _image(collection / reserved_value / "parent-owned.png", "PNG", "navy")

    with pytest.raises(ValueError, match="reserved 'parent' directory name"):
        scan_collection(
            str(collection), URL_PREFIX, str(output), log=lambda *_a, **_k: None
        )
