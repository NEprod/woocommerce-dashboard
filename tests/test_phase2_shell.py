from pathlib import Path

import pytest

from app import create_app, db
from app.models import User
from config import Config


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def shell_app(tmp_path):
    database = tmp_path / "instance" / "site.db"
    database.parent.mkdir()
    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{database}"
    try:
        app = create_app()
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with app.app_context():
            db.session.add(
                User(
                    email="shell@example.com",
                    username="shell-admin",
                    password="unused-test-password",
                    is_admin=True,
                )
            )
            db.session.commit()
        yield app
    finally:
        with app.app_context():
            db.session.remove()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


@pytest.fixture
def authenticated_client(shell_app):
    client = shell_app.test_client()
    with shell_app.app_context():
        user_id = User.query.one().id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def test_visible_authenticated_routes_never_return_server_error(authenticated_client):
    routes = (
        "/",
        "/products",
        "/edit_products",
        "/collections",
        "/scanner",
        "/operations",
        "/metadata-reference",
        "/settings",
        "/woo-sync",
        "/orders",
        "/website-automation",
        "/analytics",
        "/sync",
        "/web-sync",
        "/pos",
        "/tools",
        "/site",
    )

    for route in routes:
        response = authenticated_client.get(route, follow_redirects=True)
        assert response.status_code == 200, route
        assert b"TemplateNotFound" not in response.data, route


def test_shell_uses_neutral_branding_local_assets_and_accessible_navigation(
    authenticated_client,
):
    response = authenticated_client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "WooCommerce Dashboard" in html
    assert "Tender Loving Creations" not in html
    assert "tlcNavbar" not in html
    assert "cdn.jsdelivr.net" not in html
    assert "cdnjs.cloudflare.com" not in html
    assert "code.jquery.com" not in html
    assert "/static/assets/vendor/bootstrap/dist/css/bootstrap.min.css" in html
    assert "/static/assets/vendor/bootstrap/dist/js/bootstrap.bundle.min.js" in html
    assert "/static/assets/js/app-shell.js" in html
    assert 'class="skip-link"' in html
    assert 'id="appNavigation"' in html
    assert 'aria-controls="appNavigation"' in html
    assert 'aria-label="Primary navigation"' in html
    assert "Catalogue" in html
    assert "Operations" in html
    assert "Metadata" in html
    assert "System" in html
    assert "Future" in html


def test_planned_pages_are_professional_and_do_not_claim_live_features(
    authenticated_client,
):
    expected = {
        "/scanner": "Scanner workspace",
        "/operations": "Operation History",
        "/collections": "Collections",
        "/settings": "Settings",
        "/woo-sync": "Woo Sync",
        "/orders": "Orders",
        "/website-automation": "Website Automation",
        "/analytics": "Analytics",
    }

    for route, title in expected.items():
        response = authenticated_client.get(route)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert title in html
        assert "Planned" in html
        assert "not available in this release" in html


def test_folder_picker_requires_authentication_and_still_works_for_admin(
    shell_app, authenticated_client, tmp_path
):
    anonymous = shell_app.test_client().get(
        "/folder-picker", query_string={"path": str(tmp_path)}
    )
    assert anonymous.status_code == 401

    authenticated = authenticated_client.get(
        "/folder-picker", query_string={"path": str(tmp_path)}
    )
    assert authenticated.status_code == 200
    assert authenticated.get_json()["current_path"] == str(tmp_path)


def test_legacy_signup_route_resolves_safely(shell_app):
    response = shell_app.test_client().get("/signup", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_design_tokens_and_project_owned_icon_sprite_are_centralized():
    stylesheet = (ROOT / "app/static/assets/css/custom.css").read_text(
        encoding="utf-8"
    )
    templates = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "app/templates").rglob("*.html")
    )
    icon_sprite = ROOT / "app/static/assets/img/icons/app-icons.svg"
    shell_script = ROOT / "app/static/assets/js/app-shell.js"

    for token in (
        "--color-canvas",
        "--color-surface",
        "--color-surface-raised",
        "--color-primary",
        "--color-accent",
        "--color-text",
        "--space-1",
        "--radius-card",
        "--focus-ring",
    ):
        assert token in stylesheet

    assert "--tlc-" not in stylesheet
    assert ".btn-tlc" not in stylesheet
    assert "Tender Loving Creations" not in templates
    assert "tlcNavbar" not in templates
    assert icon_sprite.is_file()
    assert shell_script.is_file()


def test_default_notification_branding_is_application_neutral():
    from app.utils.discord import DEFAULT_USERNAME

    assert DEFAULT_USERNAME == "WooCommerce Dashboard"
