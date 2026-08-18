from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app import create_app, db
from app.models import (
    CatalogueOperation,
    Collection,
    Product,
    ProductImage,
    User,
    Variation,
)
from config import Config


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def dashboard_app(tmp_path):
    database = tmp_path / "instance" / "site.db"
    database.parent.mkdir()
    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{database}"
    try:
        app = create_app()
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with app.app_context():
            user = User(
                email="dashboard@example.com",
                username="dashboard-admin",
                password="unused-test-password",
                is_admin=True,
            )
            first = Collection(
                name="Fictional Cards",
                root_path="/fixture/cards",
                sku_prefix="CARD",
                shared_json_path="/fixture/cards/product_info.json",
                source_relpath="Fictional Cards",
            )
            second = Collection(
                name="Fictional Gifts",
                root_path="/fixture/gifts",
                sku_prefix="GIFT",
                shared_json_path="/fixture/gifts/product_info.json",
                source_relpath="Fictional Gifts",
            )
            db.session.add_all([user, first, second])
            db.session.flush()

            now = datetime.now()
            active = Product(
                collection_id=first.id,
                sku="CARD-001",
                title="Fictional Card",
                product_type="variable",
                catalogue_status="active",
                short_description="A fictional card.",
                description="A complete fictional description.",
                meta_title="Fictional Card",
                meta_description="Fictional metadata.",
                image_url="https://example.invalid/card.webp",
                override_json_path="/fixture/cards/card/product_info.json",
                local_updated_at=now,
            )
            incomplete = Product(
                collection_id=first.id,
                sku="CARD-002",
                title="Incomplete Card",
                product_type="simple",
                catalogue_status="active",
                short_description="",
                description=None,
                meta_title=None,
                meta_description=None,
                image_url=None,
                local_updated_at=now - timedelta(hours=1),
            )
            missing = Product(
                collection_id=second.id,
                sku="GIFT-001",
                title="Missing Gift",
                product_type="simple",
                catalogue_status="missing",
                local_updated_at=now - timedelta(days=1),
            )
            db.session.add_all([active, incomplete, missing])
            db.session.flush()
            db.session.add(ProductImage(product_id=active.id, url=active.image_url))
            db.session.add_all(
                [
                    Variation(
                        product_id=active.id,
                        sku="CARD-001-A",
                        catalogue_status="active",
                    ),
                    Variation(
                        product_id=active.id,
                        sku="CARD-001-B",
                        catalogue_status="active",
                    ),
                    Variation(
                        product_id=active.id,
                        sku="CARD-001-C",
                        catalogue_status="missing",
                    ),
                ]
            )
            db.session.add_all(
                [
                    CatalogueOperation(
                        id="operation-success",
                        operation_type="append",
                        status="succeeded",
                        scope='{"scan_mode":"append"}',
                        started_at=now - timedelta(hours=2),
                        finished_at=now - timedelta(hours=2) + timedelta(seconds=12),
                        products_attempted=2,
                        products_succeeded=2,
                        marker_state="finalized",
                        recovery_state="none",
                    ),
                    CatalogueOperation(
                        id="operation-failed",
                        operation_type="product_update",
                        status="failed",
                        scope='{"sku":"CARD-002"}',
                        started_at=now - timedelta(minutes=30),
                        finished_at=now - timedelta(minutes=29),
                        products_attempted=1,
                        products_failed=1,
                        error="Sanitized fictional failure",
                        marker_state="database_recovery_required",
                        recovery_state="database_recovery_required",
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
def dashboard_client(dashboard_app):
    app, _database = dashboard_app
    client = app.test_client()
    with app.app_context():
        user_id = User.query.one().id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def test_dashboard_data_uses_only_projection_and_operation_records(dashboard_app):
    from app.dashboard import build_dashboard_data

    app, _database = dashboard_app
    with app.app_context():
        data = build_dashboard_data()

    assert data["summary"] == {
        "collections": 2,
        "products": 3,
        "variations": 3,
        "active_products": 2,
        "missing_products": 1,
        "active_variations": 2,
        "missing_variations": 1,
        "overrides": 1,
    }
    assert data["health"]["total_items"] == 6
    assert data["health"]["active_items"] == 4
    assert data["health"]["missing_items"] == 2
    assert data["health"]["availability_percent"] == 67
    assert data["metadata_issues"] == {
        "missing_descriptions": 1,
        "missing_images": 1,
        "missing_seo": 1,
        "total": 3,
    }
    assert data["scanner"]["active"] is None
    assert data["recent_operations"][0]["id"] == "operation-failed"
    assert data["attention"]["failed_operations"] == 1
    assert data["attention"]["recovery_required"] == 1
    assert data["recent_products"][0]["sku"] == "CARD-001"
    assert data["recent_products"][0]["variation_count"] == 3


def test_dashboard_route_renders_real_sections_without_unsupported_claims(
    dashboard_client,
):
    response = dashboard_client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    for required in (
        "Catalogue Health",
        "Scanner Activity",
        "Recent Changes",
        "Needs Attention",
        "Metadata Issues",
        "Recent Products",
        "Fictional Card",
        "CARD-001",
        "67%",
        "Not configured",
    ):
        assert required in html

    for unsupported in (
        "Revenue",
        "Sales",
        "Orders today",
        "WooCommerce connected",
        "Notifications",
        "Export",
        "Add product",
    ):
        assert unsupported not in html

    assert 'aria-labelledby="catalogue-health-title"' in html
    assert 'aria-labelledby="scanner-activity-title"' in html
    assert '<table' in html
    assert '<th scope="col"' in html


def test_empty_dashboard_is_honest_and_actionable(dashboard_app):
    app, _database = dashboard_app
    with app.app_context():
        Variation.query.delete()
        ProductImage.query.delete()
        Product.query.delete()
        Collection.query.delete()
        CatalogueOperation.query.delete()
        db.session.commit()

    client = app.test_client()
    with app.app_context():
        user_id = User.query.one().id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "No catalogue projection yet" in html
    assert "Run Initial Scan" in html
    assert "No operations recorded" in html
    assert "No projected products yet" in html


def test_settings_icon_uses_the_shared_stroked_icon_family():
    sprite = (ROOT / "app/static/assets/img/icons/app-icons.svg").read_text(
        encoding="utf-8"
    )
    symbol = sprite.split('id="icon-settings"', 1)[1].split("</symbol>", 1)[0]
    stylesheet = (ROOT / "app/static/assets/css/custom.css").read_text(
        encoding="utf-8"
    )
    navbar = (ROOT / "app/templates/includes/navbar.html").read_text(
        encoding="utf-8"
    )

    assert 'viewBox="0 0 24 24"' in symbol
    assert "transform=" not in symbol
    assert "<circle" in symbol and "<path" in symbol
    assert ".app-icon { width: 18px; height: 18px;" in stylesheet
    assert "stroke-width: 1.8" in stylesheet
    assert "{{ icon('settings') }}" in navbar


def test_dashboard_styles_define_responsive_feature_and_empty_states():
    stylesheet = (ROOT / "app/static/assets/css/custom.css").read_text(
        encoding="utf-8"
    )

    for selector in (
        ".dashboard-stats",
        ".dashboard-feature-grid",
        ".catalogue-health-panel",
        ".scanner-activity-panel",
        ".dashboard-support-grid",
        ".recent-products",
        ".dashboard-mobile-products",
    ):
        assert selector in stylesheet
    assert "@media (max-width: 767.98px)" in stylesheet
