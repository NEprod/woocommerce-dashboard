from pathlib import Path
import re

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
    assert 'class="app-sidebar"' in html
    assert 'class="mobile-bottom-nav"' in html
    assert 'aria-label="Mobile primary navigation"' in html
    assert 'data-sidebar-toggle' in html
    assert 'aria-label="Primary navigation"' in html
    assert "Catalogue" in html
    assert "Operations" in html
    assert "Metadata" in html
    assert "System" in html
    assert "Future" in html
    assert "account-avatar" not in html


@pytest.mark.parametrize(
    ("route", "label"),
    (("/", "Dashboard"), ("/products", "Products"), ("/scanner", "Scanner"), ("/operations", "Operations")),
)
def test_navigation_exposes_active_destination_to_assistive_technology(
    authenticated_client, route, label
):
    html = authenticated_client.get(route).get_data(as_text=True)
    assert re.search(
        rf'<a[^>]+class="[^"]*is-active[^"]*"[^>]+aria-current="page"[^>]*>.*?{label}',
        html,
        re.DOTALL,
    )


def test_login_uses_light_first_split_shell_without_mockup_only_controls(shell_app):
    html = shell_app.test_client().get("/login").get_data(as_text=True)
    assert "auth-brand-panel" in html
    assert "auth-form-panel" in html
    assert "Username" in html
    for unsupported in ("Sign in with Google", "Create account", "Notifications", "Export"):
        assert unsupported not in html


def test_planned_pages_are_professional_and_do_not_claim_live_features(
    authenticated_client,
):
    expected = {
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

    for route, title in {"/scanner": "Scanner workspace", "/operations": "Operation History"}.items():
        html = authenticated_client.get(route).get_data(as_text=True)
        assert title in html
        assert "Planned" not in html

    settings_html = authenticated_client.get("/settings").get_data(as_text=True)
    assert "Storage and mounts" in settings_html
    assert "Planned" not in settings_html


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
        "--color-surface-primary",
        "--color-surface-secondary",
        "--color-surface-elevated",
        "--color-surface-dark",
        "--color-surface-dark-hover",
        "--color-text-primary",
        "--color-text-secondary",
        "--color-text-inverse",
        "--color-lime",
        "--color-lime-soft",
        "--color-lime-ink",
        "--color-teal",
        "--color-warning",
        "--color-error",
        "--color-border",
        "--color-code-surface",
        "--color-code-text",
        "--space-1",
        "--space-12",
        "--radius-card-large",
        "--radius-mobile-nav",
        "--focus-ring",
    ):
        assert token in stylesheet

    assert "color-scheme: light" in stylesheet
    assert "--color-canvas: #F6F5F1" in stylesheet
    assert "--color-surface-dark: #10262D" in stylesheet

    assert "--tlc-" not in stylesheet
    assert ".btn-tlc" not in stylesheet
    assert "Tender Loving Creations" not in templates
    assert "tlcNavbar" not in templates
    assert icon_sprite.is_file()
    assert shell_script.is_file()


def _hex_rgb(value):
    return tuple(int(value[index : index + 2], 16) / 255 for index in (1, 3, 5))


def _relative_luminance(value):
    channels = tuple(
        channel / 12.92
        if channel <= 0.04045
        else ((channel + 0.055) / 1.055) ** 2.4
        for channel in _hex_rgb(value)
    )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast_ratio(first, second):
    lighter, darker = sorted(
        (_relative_luminance(first), _relative_luminance(second)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def test_table_and_code_tokens_have_accessible_contrast():
    stylesheet = (ROOT / "app/static/assets/css/custom.css").read_text(
        encoding="utf-8"
    )
    variables = dict(
        re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6});", stylesheet)
    )

    for background in ("--color-canvas", "--color-surface-primary", "--color-surface-secondary"):
        assert _contrast_ratio(
            variables["--color-text-primary"], variables[background]
        ) >= 7
        assert _contrast_ratio(
            variables["--color-text-secondary"], variables[background]
        ) >= 4.5

    assert _contrast_ratio(
        variables["--color-code-text"], variables["--color-code-surface"]
    ) >= 7


def test_json_editors_and_metadata_examples_use_dedicated_code_classes():
    editor = (ROOT / "app/templates/editor.html").read_text(encoding="utf-8")
    metadata = (ROOT / "app/templates/metadata_reference.html").read_text(
        encoding="utf-8"
    )

    for field in ("attributes", "image_attributes", "variation_modifiers"):
        assert re.search(
            rf'<textarea class="[^"]*code-editor[^"]*" name="{field}"', editor
        )
    assert metadata.count("code-block") >= 2


def test_visual_correction_avoids_broad_background_overrides_and_inline_colors():
    stylesheet = (ROOT / "app/static/assets/css/custom.css").read_text(
        encoding="utf-8"
    )
    assert ".bg-dark, .bg-white, .bg-warning" not in stylesheet

    for relative_path in (
        "app/templates/edit_products.html",
        "app/templates/editor.html",
        "app/templates/metadata_reference.html",
        "app/templates/setup/initial_scan.html",
        "app/templates/setup/initial_settings.html",
    ):
        template = (ROOT / relative_path).read_text(encoding="utf-8")
        assert not re.search(
            r'style="[^"]*(?:color|background)\s*:', template, re.IGNORECASE
        ), relative_path


def test_default_notification_branding_is_application_neutral():
    from app.utils.discord import DEFAULT_USERNAME

    assert DEFAULT_USERNAME == "WooCommerce Dashboard"
