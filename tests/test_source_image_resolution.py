import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pytest
from PIL import Image

from app import create_app, db
from app.catalogue_images import (
    resolve_product_catalogue_image,
    resolve_variation_catalogue_image,
)
from app.models import (
    Collection,
    Product,
    ProductImage,
    Settings,
    User,
    Variation,
    VariationAttribute,
    VariationImage,
)
from config import Config


def _image(path, image_format=None, colour="lime"):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (30, 22), colour).save(path, format=image_format)


def _marker(folder, images):
    (folder / ".scanned").write_text(
        json.dumps({"sku": "FIXTURE", "title": "Fixture", "images_used": images}),
        encoding="utf-8",
    )


@pytest.fixture
def source_image_matrix(tmp_path):
    catalogue = tmp_path / "catalogue"
    instance = tmp_path / "instance"
    output = tmp_path / "output"
    instance.mkdir()
    output.mkdir()

    simple_root = catalogue / "Simple Sources"
    simple_root.mkdir(parents=True)
    (simple_root / "product_info.json").write_text(
        json.dumps({"collection_type": "Simple", "sku_prefix": "SRC"}),
        encoding="utf-8",
    )

    outside = tmp_path / "outside.png"
    _image(outside, colour="red")

    database = instance / "site.db"
    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{database}"
    try:
        app = create_app()
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with app.app_context():
            db.session.add(
                Settings(
                    product_folder=str(catalogue),
                    output_folder=str(output),
                    url_prefix="https://uploads.invalid/catalogue/",
                )
            )
            user = User(
                email="source-images@example.test",
                username="source-image-admin",
                password="unused-test-password",
                is_admin=True,
            )
            simple_collection = Collection(
                name="Simple Sources",
                root_path=str(simple_root),
                source_relpath="Simple Sources",
                shared_json_path=str(simple_root / "product_info.json"),
                collection_type="Simple",
                sku_prefix="SRC",
            )
            db.session.add_all([user, simple_collection])
            db.session.flush()

            now = datetime.now()
            ids = {}

            def simple_product(sku, folder_name, filename=None, emitted=None, marker=None):
                folder = simple_root / folder_name
                folder.mkdir()
                if filename:
                    _image(folder / filename)
                if marker is not None:
                    _marker(folder, marker)
                product = Product(
                    collection_id=simple_collection.id,
                    sku=sku,
                    title=folder_name,
                    product_type="simple",
                    collection_type="Simple",
                    catalogue_status="active",
                    source_relpath=f"Simple Sources/{folder_name}",
                    image_url=emitted,
                    local_updated_at=now - timedelta(minutes=len(ids)),
                )
                db.session.add(product)
                db.session.flush()
                if emitted:
                    db.session.add(ProductImage(product_id=product.id, url=emitted, position=0))
                ids[sku] = product.id
                return product, folder

            simple_product("SRC-PNG", "PNG Product", "Primary Image.png", "https://uploads.invalid/Primary%20Image.webp")
            simple_product("SRC-JPG", "JPG Product", "photo.jpg", "https://uploads.invalid/photo.webp")
            simple_product("SRC-JPEG", "JPEG Product", "photo.jpeg", "https://uploads.invalid/photo.webp")
            simple_product("SRC-WEBP", "WebP Product", "native.webp", "https://uploads.invalid/native.webp")
            simple_product("SRC-CASE", "Mixed Case Product", "MiXeD.JpG", "https://uploads.invalid/MiXeD.webp")
            simple_product(
                "SRC-UNICODE",
                "Unicode Product",
                "café card ü.jpg",
                f"https://uploads.invalid/{quote('café card ü')}.webp",
            )
            simple_product(
                "SRC-MISMATCH",
                "Stem Mismatch",
                "scanner chosen source.png",
                "https://uploads.invalid/upload-renamed.webp",
                ["scanner chosen source.png"],
            )
            multi, multi_folder = simple_product(
                "SRC-MULTI", "Multiple Extensions", "same.png", "https://uploads.invalid/same.webp"
            )
            _image(multi_folder / "same.jpg", colour="teal")
            _marker(multi_folder, ["same.jpg", "same.png"])
            db.session.add(ProductImage(product_id=multi.id, url=multi.image_url, position=1))
            simple_product("SRC-NO-URL", "No Emitted URL", "discovered.png", None, ["discovered.png"])
            corrupt, corrupt_folder = simple_product(
                "SRC-CORRUPT", "Corrupt Product", None, "https://uploads.invalid/broken.webp"
            )
            (corrupt_folder / "broken.png").write_text("not an image", encoding="utf-8")
            traversal, traversal_folder = simple_product(
                "SRC-TRAVERSAL", "Traversal Product", None, "https://uploads.invalid/escape.webp"
            )
            (traversal_folder / "escape.png").symlink_to(outside)
            simple_product("SRC-MISSING", "Genuinely Missing")

            single_root = catalogue / "Single Parent And Variations"
            (single_root / "parent").mkdir(parents=True)
            (single_root / "Blue").mkdir()
            (single_root / "Red").mkdir()
            (single_root / "product_info.json").write_text(
                json.dumps(
                    {
                        "collection_type": "Single Variable",
                        "sku_prefix": "SVP",
                        "attributes": {"Theme": ["Blue", "Red"]},
                        "image_attributes": ["Theme"],
                    }
                ),
                encoding="utf-8",
            )
            _image(single_root / "parent" / "parent hero.png", colour="navy")
            _image(single_root / "Blue" / "blue choice.jpg", colour="blue")
            _image(single_root / "Red" / "red choice.webp", colour="red")
            _marker(single_root, ["parent hero.png"])
            single_collection = Collection(
                name="Single Parent And Variations",
                root_path=str(single_root),
                source_relpath="Single Parent And Variations",
                shared_json_path=str(single_root / "product_info.json"),
                collection_type="Single Variable",
                sku_prefix="SVP",
            )
            db.session.add(single_collection)
            db.session.flush()
            variable = Product(
                collection_id=single_collection.id,
                sku="SVP-001",
                title="Parent And Variation Images",
                product_type="variable",
                collection_type="Single Variable",
                catalogue_status="active",
                source_relpath="Single Parent And Variations",
                image_url="https://uploads.invalid/parent%20hero.webp",
                local_updated_at=now + timedelta(minutes=1),
            )
            db.session.add(variable)
            db.session.flush()
            db.session.add(ProductImage(product_id=variable.id, url=variable.image_url, position=0, alt_text="Parent source"))
            ids["SVP-001"] = variable.id
            variation_ids = {}
            for position, (value, filename, emitted) in enumerate(
                (
                    ("Blue", "blue choice.jpg", "https://uploads.invalid/blue%20choice.webp"),
                    ("Red", "red choice.webp", "https://uploads.invalid/red%20choice.webp"),
                )
            ):
                variation = Variation(
                    product_id=variable.id,
                    sku=f"SVP-001-{position + 1}",
                    source_relpath="Single Parent And Variations",
                    catalogue_status="active",
                    image_url=emitted,
                    menu_order=position,
                )
                db.session.add(variation)
                db.session.flush()
                db.session.add_all(
                    [
                        VariationImage(variation_id=variation.id, url=emitted, position=0),
                        VariationAttribute(variation_id=variation.id, name="Theme", value=value, position=0),
                    ]
                )
                variation_ids[value] = variation.id

            fallback_root = catalogue / "Variation Only"
            (fallback_root / "Blue").mkdir(parents=True)
            (fallback_root / "product_info.json").write_text(
                json.dumps(
                    {
                        "collection_type": "Single Variable",
                        "sku_prefix": "SVO",
                        "attributes": {"Theme": ["Blue"]},
                        "image_attributes": ["Theme"],
                    }
                ),
                encoding="utf-8",
            )
            _image(fallback_root / "Blue" / "only variation.jpeg", colour="purple")
            fallback_collection = Collection(
                name="Variation Only",
                root_path=str(fallback_root),
                source_relpath="Variation Only",
                shared_json_path=str(fallback_root / "product_info.json"),
                collection_type="Single Variable",
                sku_prefix="SVO",
            )
            db.session.add(fallback_collection)
            db.session.flush()
            fallback_product = Product(
                collection_id=fallback_collection.id,
                sku="SVO-001",
                title="Variation Image Fallback",
                product_type="variable",
                collection_type="Single Variable",
                catalogue_status="active",
                source_relpath="Variation Only",
                local_updated_at=now + timedelta(minutes=2),
            )
            db.session.add(fallback_product)
            db.session.flush()
            ids["SVO-001"] = fallback_product.id
            fallback_variation = Variation(
                product_id=fallback_product.id,
                sku="SVO-001-1",
                source_relpath="Variation Only",
                catalogue_status="active",
                image_url="https://uploads.invalid/only%20variation.webp",
            )
            db.session.add(fallback_variation)
            db.session.flush()
            db.session.add_all(
                [
                    VariationImage(variation_id=fallback_variation.id, url=fallback_variation.image_url, position=0),
                    VariationAttribute(variation_id=fallback_variation.id, name="Theme", value="Blue", position=0),
                ]
            )
            variation_ids["Fallback"] = fallback_variation.id

            variable_collection_root = catalogue / "Variable Collection"
            variable_product_root = variable_collection_root / "Green Product"
            variable_product_root.mkdir(parents=True)
            (variable_collection_root / "product_info.json").write_text(
                json.dumps(
                    {
                        "collection_type": "Variable Collection",
                        "sku_prefix": "VAR",
                        "attributes": {"Size": ["Small", "Large"]},
                    }
                ),
                encoding="utf-8",
            )
            _image(variable_product_root / "shared parent.jpg", colour="green")
            _marker(variable_product_root, ["shared parent.jpg"])
            variable_collection = Collection(
                name="Variable Collection",
                root_path=str(variable_collection_root),
                source_relpath="Variable Collection",
                shared_json_path=str(variable_collection_root / "product_info.json"),
                collection_type="Variable Collection",
                sku_prefix="VAR",
            )
            db.session.add(variable_collection)
            db.session.flush()
            variable_collection_product = Product(
                collection_id=variable_collection.id,
                sku="VAR-001",
                title="Variable Collection Product",
                product_type="variable",
                collection_type="Variable Collection",
                catalogue_status="active",
                source_relpath="Variable Collection/Green Product",
                image_url="https://uploads.invalid/shared%20parent.webp",
                local_updated_at=now + timedelta(minutes=3),
            )
            db.session.add(variable_collection_product)
            db.session.flush()
            db.session.add(
                ProductImage(
                    product_id=variable_collection_product.id,
                    url=variable_collection_product.image_url,
                    position=0,
                )
            )
            ids["VAR-001"] = variable_collection_product.id
            for position, size in enumerate(("Small", "Large")):
                variation = Variation(
                    product_id=variable_collection_product.id,
                    sku=f"VAR-001-{position + 1}",
                    source_relpath="Variable Collection/Green Product",
                    catalogue_status="active",
                    image_url=variable_collection_product.image_url,
                    menu_order=position,
                )
                db.session.add(variation)
                db.session.flush()
                db.session.add_all(
                    [
                        VariationImage(
                            variation_id=variation.id,
                            url=variable_collection_product.image_url,
                            position=0,
                        ),
                        VariationAttribute(
                            variation_id=variation.id,
                            name="Size",
                            value=size,
                            position=0,
                        ),
                    ]
                )
            db.session.commit()

        client = app.test_client()
        with app.app_context():
            user_id = User.query.one().id
        with client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
        yield {
            "app": app,
            "client": client,
            "catalogue": catalogue,
            "instance": instance,
            "output": output,
            "ids": ids,
            "variation_ids": variation_ids,
            "database": database,
        }
    finally:
        with app.app_context():
            db.session.remove()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


@pytest.mark.parametrize(
    ("sku", "filename", "mimetype"),
    [
        ("SRC-PNG", "Primary Image.png", "image/png"),
        ("SRC-JPG", "photo.jpg", "image/jpeg"),
        ("SRC-JPEG", "photo.jpeg", "image/jpeg"),
        ("SRC-WEBP", "native.webp", "image/webp"),
        ("SRC-CASE", "MiXeD.JpG", "image/jpeg"),
        ("SRC-UNICODE", "café card ü.jpg", "image/jpeg"),
        ("SRC-MISMATCH", "scanner chosen source.png", "image/png"),
        ("SRC-NO-URL", "discovered.png", "image/png"),
    ],
)
def test_simple_source_formats_and_scanner_metadata_resolve(
    source_image_matrix, sku, filename, mimetype
):
    app = source_image_matrix["app"]
    with app.app_context():
        product = Product.query.filter_by(sku=sku).one()
        selected = resolve_product_catalogue_image(product)
    assert selected.name == filename
    response = source_image_matrix["client"].get(f"/catalogue-images/products/{source_image_matrix['ids'][sku]}")
    assert response.status_code == 200
    assert response.mimetype == mimetype


def test_multiple_source_extensions_follow_recorded_scanner_order(source_image_matrix):
    app = source_image_matrix["app"]
    with app.app_context():
        selected = resolve_product_catalogue_image(Product.query.filter_by(sku="SRC-MULTI").one())
    assert selected.name == "same.jpg"


def test_variable_parent_and_variation_images_keep_distinct_identity(source_image_matrix):
    app = source_image_matrix["app"]
    with app.app_context():
        product = Product.query.filter_by(sku="SVP-001").one()
        blue = Variation.query.get(source_image_matrix["variation_ids"]["Blue"])
        assert resolve_product_catalogue_image(product).name == "parent hero.png"
        assert resolve_variation_catalogue_image(blue).name == "blue choice.jpg"

    parent = source_image_matrix["client"].get(f"/catalogue-images/products/{source_image_matrix['ids']['SVP-001']}")
    variation = source_image_matrix["client"].get(f"/catalogue-images/variations/{source_image_matrix['variation_ids']['Blue']}")
    assert (parent.status_code, parent.mimetype) == (200, "image/png")
    assert (variation.status_code, variation.mimetype) == (200, "image/jpeg")


def test_parent_thumbnail_falls_back_to_first_valid_variation(source_image_matrix):
    app = source_image_matrix["app"]
    with app.app_context():
        product = Product.query.filter_by(sku="SVO-001").one()
        assert resolve_product_catalogue_image(product).name == "only variation.jpeg"
    response = source_image_matrix["client"].get(f"/catalogue-images/products/{source_image_matrix['ids']['SVO-001']}")
    assert (response.status_code, response.mimetype) == (200, "image/jpeg")


def test_variable_collection_uses_its_scanner_shared_parent_image(source_image_matrix):
    app = source_image_matrix["app"]
    with app.app_context():
        product = Product.query.filter_by(sku="VAR-001").one()
        variation = Variation.query.filter_by(sku="VAR-001-1").one()
        assert resolve_product_catalogue_image(product).name == "shared parent.jpg"
        assert resolve_variation_catalogue_image(variation).name == "shared parent.jpg"


def test_variation_preview_exposes_own_authenticated_thumbnail(source_image_matrix):
    payload = source_image_matrix["client"].get(
        f"/api/products/{source_image_matrix['ids']['SVP-001']}/variations"
    ).get_json()
    blue = next(item for item in payload["items"] if item["sku"] == "SVP-001-1")
    assert blue["thumbnail"] == f"/catalogue-images/variations/{source_image_matrix['variation_ids']['Blue']}"


def test_variation_source_route_requires_authentication(source_image_matrix):
    response = source_image_matrix["app"].test_client().get(
        f"/catalogue-images/variations/{source_image_matrix['variation_ids']['Blue']}"
    )
    assert response.status_code == 401


@pytest.mark.parametrize("sku", ["SRC-CORRUPT", "SRC-TRAVERSAL", "SRC-MISSING"])
def test_invalid_escaped_and_missing_sources_fall_back_safely(source_image_matrix, sku):
    response = source_image_matrix["client"].get(
        f"/catalogue-images/products/{source_image_matrix['ids'][sku]}"
    )
    assert response.status_code == 404


def test_missing_image_issue_uses_resolvable_product_or_variation_source(source_image_matrix):
    dashboard = source_image_matrix["client"].get("/").get_data(as_text=True)
    products = source_image_matrix["client"].get("/api/edit_products?issue=missing_image").get_json()
    assert 'View 3 products with missing images' in dashboard
    assert {item["sku"] for item in products["items"]} == {
        "SRC-CORRUPT",
        "SRC-TRAVERSAL",
        "SRC-MISSING",
    }


def test_images_stay_out_of_sqlite_instance_and_output(source_image_matrix):
    app = source_image_matrix["app"]
    with app.app_context():
        product_columns = db.session.execute(db.text("PRAGMA table_info(product_image)")).all()
        variation_columns = db.session.execute(db.text("PRAGMA table_info(variation_image)")).all()
    assert next(column for column in product_columns if column[1] == "url")[2] == "VARCHAR(512)"
    assert next(column for column in variation_columns if column[1] == "url")[2] == "VARCHAR(512)"
    assert not list(source_image_matrix["instance"].rglob("*.png"))
    assert not list(source_image_matrix["instance"].rglob("*.jpg"))
    assert not list(source_image_matrix["instance"].rglob("*.jpeg"))
    assert not list(source_image_matrix["instance"].rglob("*.webp"))
    assert not list(source_image_matrix["output"].rglob("*"))
