from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image

from app import create_app, db
from app.models import Collection, Product, ProductImage, Settings, User
from config import Config


@pytest.fixture
def catalogue_image_app(tmp_path):
    catalogue = tmp_path / "catalogue"
    instance = tmp_path / "instance"
    product_folder = catalogue / "Fictional Cards" / "Variable Product"
    container_folder = catalogue / "Fictional Cards" / "Container Product"
    product_folder.mkdir(parents=True)
    container_folder.mkdir(parents=True)
    instance.mkdir()

    Image.new("RGB", (24, 18), "lime").save(
        product_folder / "hero image ünicode.png"
    )
    Image.new("RGB", (18, 24), "teal").save(product_folder / "secondary.jpg")
    Image.new("RGB", (20, 20), "navy").save(
        container_folder / "container image.jpg"
    )
    (product_folder / "unsupported.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8"
    )
    (product_folder / "unreadable.jpg").write_text("not an image", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    Image.new("RGB", (20, 20), "red").save(outside / "secret.png")

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
                    output_folder=str(tmp_path / "output"),
                    url_prefix="https://uploads.invalid/catalogue/",
                )
            )
            user = User(
                email="images@example.test",
                username="image-admin",
                password="unused-test-password",
                is_admin=True,
            )
            collection = Collection(
                name="Fictional Cards",
                root_path=str(catalogue / "Fictional Cards"),
                shared_json_path=str(
                    catalogue / "Fictional Cards" / "product_info.json"
                ),
                source_relpath="Fictional Cards",
                sku_prefix="CARD",
            )
            db.session.add_all([user, collection])
            db.session.flush()

            now = datetime.now()
            primary = Product(
                collection_id=collection.id,
                sku="CARD-IMAGE-001",
                title="Unicode Image Product",
                product_type="variable",
                catalogue_status="active",
                source_relpath="Fictional Cards/Variable Product",
                image_url="https://uploads.invalid/not-the-primary.webp",
                local_updated_at=now,
            )
            container = Product(
                collection_id=collection.id,
                sku="CARD-IMAGE-002",
                title="Container Path Product",
                product_type="simple",
                catalogue_status="active",
                source_relpath="Fictional Cards/Container Product",
                image_url=(
                    "/catalogue/Fictional Cards/Container Product/"
                    "container image.jpg"
                ),
                local_updated_at=now - timedelta(minutes=1),
            )
            missing = Product(
                collection_id=collection.id,
                sku="CARD-IMAGE-003",
                title="Missing Image Product",
                product_type="simple",
                catalogue_status="active",
                source_relpath="Fictional Cards/Variable Product",
                image_url="https://uploads.invalid/missing.webp",
                local_updated_at=now - timedelta(minutes=2),
            )
            unsupported = Product(
                collection_id=collection.id,
                sku="CARD-IMAGE-004",
                title="Unsupported Image Product",
                product_type="simple",
                catalogue_status="active",
                source_relpath="Fictional Cards/Variable Product",
                image_url="https://uploads.invalid/unsupported.svg",
                local_updated_at=now - timedelta(minutes=3),
            )
            unreadable = Product(
                collection_id=collection.id,
                sku="CARD-IMAGE-005",
                title="Unreadable Image Product",
                product_type="simple",
                catalogue_status="active",
                source_relpath="Fictional Cards/Variable Product",
                image_url="https://uploads.invalid/unreadable.webp",
                local_updated_at=now - timedelta(minutes=4),
            )
            traversal = Product(
                collection_id=collection.id,
                sku="CARD-IMAGE-006",
                title="Traversal Product",
                product_type="simple",
                catalogue_status="active",
                source_relpath="Fictional Cards/Variable Product",
                image_url="/catalogue/../outside/secret.png",
                local_updated_at=now - timedelta(minutes=5),
            )
            host_path = Product(
                collection_id=collection.id,
                sku="CARD-IMAGE-007",
                title="Host Path Product",
                product_type="simple",
                catalogue_status="active",
                source_relpath="Fictional Cards/Variable Product",
                image_url=str(product_folder / "secondary.jpg"),
                local_updated_at=now - timedelta(minutes=6),
            )
            db.session.add_all(
                [
                    primary,
                    container,
                    missing,
                    unsupported,
                    unreadable,
                    traversal,
                    host_path,
                ]
            )
            db.session.flush()
            db.session.add_all(
                [
                    ProductImage(
                        product_id=primary.id,
                        url=(
                            "https://uploads.invalid/"
                            "hero%20image%20%C3%BCnicode.webp"
                        ),
                        alt_text="Primary fictional catalogue image",
                        position=0,
                    ),
                    ProductImage(
                        product_id=primary.id,
                        url="https://uploads.invalid/secondary.webp",
                        position=1,
                    ),
                ]
            )
            db.session.commit()
            product_ids = {
                product.sku: product.id
                for product in Product.query.order_by(Product.id).all()
            }
        yield app, catalogue, instance, product_ids
    finally:
        with app.app_context():
            db.session.remove()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


@pytest.fixture
def catalogue_image_client(catalogue_image_app):
    app, _catalogue, _instance, _ids = catalogue_image_app
    client = app.test_client()
    with app.app_context():
        user_id = User.query.one().id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def image_route(product_id):
    return f"/catalogue-images/products/{product_id}"


def test_primary_product_image_uses_position_zero_and_special_filename(
    catalogue_image_app, catalogue_image_client
):
    _app, _catalogue, _instance, ids = catalogue_image_app
    response = catalogue_image_client.get(image_route(ids["CARD-IMAGE-001"]))

    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert "private" in response.headers["Cache-Control"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert str(_catalogue) not in str(response.headers)
    assert len(response.data) > 20


def test_products_api_returns_internal_image_route_not_authored_path(
    catalogue_image_app, catalogue_image_client
):
    _app, catalogue, _instance, ids = catalogue_image_app
    payload = catalogue_image_client.get(
        "/api/edit_products?q=Unicode%20Image&status=active"
    ).get_json()
    product = payload["items"][0]

    assert product["thumbnail"] == image_route(ids["CARD-IMAGE-001"])
    assert product["thumbnail_alt"] == "Primary fictional catalogue image"
    assert str(catalogue) not in str(payload)
    assert "uploads.invalid" not in str(payload)


def test_container_style_catalogue_path_resolves_inside_mount(
    catalogue_image_app, catalogue_image_client
):
    _app, _catalogue, _instance, ids = catalogue_image_app
    response = catalogue_image_client.get(image_route(ids["CARD-IMAGE-002"]))
    assert response.status_code == 200
    assert response.mimetype == "image/jpeg"


def test_relative_catalogue_path_resolves_inside_product_folder(
    catalogue_image_app, catalogue_image_client
):
    app, _catalogue, _instance, ids = catalogue_image_app
    with app.app_context():
        product = Product.query.filter_by(sku="CARD-IMAGE-002").one()
        product.image_url = (
            "Fictional Cards/Container Product/container image.jpg"
        )
        db.session.commit()
    response = catalogue_image_client.get(image_route(ids["CARD-IMAGE-002"]))
    assert response.status_code == 200
    assert response.mimetype == "image/jpeg"


def test_host_absolute_path_is_compatible_but_never_exposed(
    catalogue_image_app, catalogue_image_client
):
    _app, catalogue, _instance, ids = catalogue_image_app
    payload = catalogue_image_client.get(
        "/api/edit_products?q=Host%20Path&per_page=25"
    ).get_json()
    assert payload["items"][0]["thumbnail"] == image_route(ids["CARD-IMAGE-007"])
    assert str(catalogue) not in str(payload)
    assert catalogue_image_client.get(
        image_route(ids["CARD-IMAGE-007"])
    ).status_code == 200


@pytest.mark.parametrize(
    "sku",
    ["CARD-IMAGE-003", "CARD-IMAGE-004", "CARD-IMAGE-005", "CARD-IMAGE-006"],
)
def test_missing_unsupported_unreadable_and_traversal_images_return_404(
    catalogue_image_app, catalogue_image_client, sku
):
    _app, _catalogue, _instance, ids = catalogue_image_app
    response = catalogue_image_client.get(image_route(ids[sku]))
    assert response.status_code == 404
    assert b"outside" not in response.data
    assert b"catalogue" not in response.data.lower()


def test_catalogue_image_route_requires_authentication(catalogue_image_app):
    app, _catalogue, _instance, ids = catalogue_image_app
    response = app.test_client().get(image_route(ids["CARD-IMAGE-001"]))
    assert response.status_code == 401


def test_products_thumbnails_survive_filter_and_pagination(
    catalogue_image_app, catalogue_image_client
):
    app, _catalogue, _instance, ids = catalogue_image_app
    with app.app_context():
        collection = Collection.query.one()
        for index in range(25):
            db.session.add(
                Product(
                    collection_id=collection.id,
                    sku=f"PAGE-{index:03d}",
                    title=f"Paged Product {index:03d}",
                    product_type="simple",
                    catalogue_status="active",
                )
            )
        db.session.commit()

    filtered = catalogue_image_client.get(
        "/api/edit_products?q=Unicode&status=active&per_page=25"
    ).get_json()
    assert filtered["items"][0]["thumbnail"] == image_route(ids["CARD-IMAGE-001"])

    second_page = catalogue_image_client.get(
        "/api/edit_products?per_page=25&page=2"
    ).get_json()
    routed = [item for item in second_page["items"] if item["thumbnail"]]
    assert image_route(ids["CARD-IMAGE-001"]) in {
        item["thumbnail"] for item in routed
    }
    assert all(
        item["thumbnail"].startswith("/catalogue-images/products/")
        for item in routed
    )


def test_dashboard_recent_products_uses_same_catalogue_thumbnail_route(
    catalogue_image_app, catalogue_image_client
):
    _app, catalogue, _instance, ids = catalogue_image_app
    html = catalogue_image_client.get("/").get_data(as_text=True)

    assert image_route(ids["CARD-IMAGE-001"]) in html
    assert "dashboard-product-thumbnail" in html
    assert str(catalogue) not in html
    assert "uploads.invalid" not in html


def test_images_remain_references_not_sqlite_blobs_or_instance_files(
    catalogue_image_app,
):
    app, _catalogue, instance, _ids = catalogue_image_app
    with app.app_context():
        columns = db.session.execute(db.text("PRAGMA table_info(product_image)")).all()
        records = ProductImage.query.all()

    assert next(column for column in columns if column[1] == "url")[2] == "VARCHAR(512)"
    assert all(isinstance(record.url, str) for record in records)
    assert not list(instance.rglob("*.png"))
    assert not list(instance.rglob("*.jpg"))
    assert not list(instance.rglob("*.webp"))


def test_dashboard_thumbnail_css_preserves_mobile_layout_contract():
    root = Path(__file__).resolve().parents[1]
    css = (root / "app/static/assets/css/custom.css").read_text(encoding="utf-8")
    javascript = (root / "app/static/assets/js/app-shell.js").read_text(
        encoding="utf-8"
    )
    assert ".dashboard-product-thumbnail" in css
    assert "@media (max-width: 767.98px)" in css
    assert 'querySelectorAll("[data-catalogue-thumbnail]")' in javascript
