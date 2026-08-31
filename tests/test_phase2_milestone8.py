import re
from pathlib import Path

import pytest

from app import create_app, db
from app.models import Settings, User
from app.utils.discord import WEBHOOK_ENV, configuration_summary
from config import Config


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings_app(tmp_path, monkeypatch):
    instance = tmp_path / "instance"
    catalogue = tmp_path / "fictional-catalogue"
    output = tmp_path / "generated-output"
    for directory in (instance, catalogue, output):
        directory.mkdir()

    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{instance / 'site.db'}"
    monkeypatch.setenv("DISCORD_ENABLED", "true")
    monkeypatch.setenv("DISCORD_DEFAULT_USERNAME", "Catalogue Bot")
    monkeypatch.setenv("DISCORD_DEFAULT_AVATAR_URL", "https://example.invalid/avatar.png")
    for index, variable in enumerate(WEBHOOK_ENV.values(), start=1):
        monkeypatch.setenv(variable, f"https://discord.com/api/webhooks/{index}/private-token-{index}")

    try:
        app = create_app()
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with app.app_context():
            db.session.add(User(email="settings@example.com", username="settings-admin", password="unused", is_admin=True))
            db.session.add(Settings(product_folder=str(catalogue), output_folder=str(output), url_prefix="https://uploads.invalid/"))
            db.session.commit()
        yield app, catalogue, output, instance
    finally:
        with app.app_context():
            db.session.remove()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


@pytest.fixture
def settings_client(settings_app):
    app, *_ = settings_app
    client = app.test_client()
    with app.app_context():
        user_id = User.query.one().id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def test_settings_requires_authentication(settings_app):
    app, *_ = settings_app
    response = app.test_client().get("/settings")
    assert response.status_code in {302, 401}
    if response.status_code == 302:
        assert "/login" in response.headers["Location"]


def test_settings_renders_safe_application_storage_scanner_and_retention_state(settings_client):
    response = settings_client.get("/settings")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    for expected in (
        "Application",
        "Storage and mounts",
        "Scanner",
        "Discord notifications",
        "Retention and safety",
        "Migration head",
        "0007_woo_sync_identity",
        "SQLite integrity",
        "Passed",
        "Catalogue available",
        "Output available",
        "App data available",
        "Database readable",
        "Database writable",
        "Append",
        "Update",
        "Full",
        "Cancellation is not supported",
        "1,000",
        "180 days",
        "365 days",
        "500 lines",
        "256 KiB",
        "10 per source",
        "90 days",
        "Image binaries are not stored",
    ):
        assert expected in html

    assert html.count("<h1") == 1
    assert html.count("<main") == 1
    ids = re.findall(r'\bid="([^"]+)"', html)
    assert len(ids) == len(set(ids))


def test_settings_discord_state_is_boolean_only_and_never_exposes_values(settings_client, settings_app):
    _, catalogue, output, instance = settings_app
    html = settings_client.get("/settings").get_data(as_text=True)

    assert "Discord enabled" in html
    for label in (
        "Scanner updates",
        "Scanner warnings and failures",
        "Metadata updates",
        "Product overrides",
        "Product ingest",
    ):
        assert label in html
    assert html.count("Configured") >= 7
    for forbidden in (
        "discord.com/api/webhooks",
        "private-token",
        "Catalogue Bot",
        "example.invalid/avatar.png",
        str(catalogue),
        str(output),
        str(instance),
        "SECRET_KEY",
        "Authorization",
        ".env",
    ):
        assert forbidden not in html


def test_discord_configuration_summary_contains_only_safe_states(monkeypatch):
    monkeypatch.setenv("DISCORD_ENABLED", "true")
    variables = list(WEBHOOK_ENV.values())
    monkeypatch.setenv(variables[0], "https://discord.com/api/webhooks/123/private")
    monkeypatch.setenv(variables[1], "not-a-webhook")
    for variable in variables[2:]:
        monkeypatch.setenv(variable, "")
    monkeypatch.delenv("DISCORD_DEFAULT_USERNAME", raising=False)
    monkeypatch.delenv("DISCORD_DEFAULT_AVATAR_URL", raising=False)

    summary = configuration_summary()

    assert summary["channel_states"]["scans_info"] == "configured"
    assert summary["channel_states"]["scans_errors"] == "not_configured"
    assert all(summary["channel_states"][key] == "not_configured" for key in ("edits", "overrides", "ingest"))
    assert summary["display_name_state"] == "default"
    assert summary["avatar_state"] == "not_configured"
    assert "private" not in repr(summary)
    assert "not-a-webhook" not in repr(summary)


def test_settings_reports_missing_output_without_showing_path(settings_client, settings_app):
    app, _catalogue, _output, _instance = settings_app
    missing = "/private/fictional/unavailable-output"
    with app.app_context():
        Settings.query.one().output_folder = missing
        db.session.commit()

    html = settings_client.get("/settings").get_data(as_text=True)
    assert "Output unavailable" in html
    assert "Scanner modes requiring output are unavailable" in html
    assert missing not in html


def test_settings_reports_safe_read_only_woo_connection_state(settings_client):
    html = settings_client.get("/settings").get_data(as_text=True)
    assert "WooCommerce connection" in html
    assert "Woo writes" in html
    assert "Disabled for this milestone" in html
    assert "WooCommerce connected" not in html
    assert "Woo credentials" not in html


def test_settings_navigation_is_reachable_and_active_on_desktop_and_mobile(settings_client):
    html = settings_client.get("/settings").get_data(as_text=True)
    assert re.search(r'class="sidebar-link sidebar-child is-active"[^>]+aria-current="page"[^>]*title="Settings"', html)
    assert re.search(r'class="mobile-primary-link is-active"[^>]+aria-controls="appNavigation"', html)
    assert re.search(r'class="mobile-nav-link is-active"[^>]+aria-current="page"[^>]*>.*Settings', html, re.DOTALL)


def test_settings_uses_text_labels_for_every_health_state(settings_client):
    html = settings_client.get("/settings").get_data(as_text=True)
    badges = re.findall(r'<span class="status-badge[^"]*">\s*([^<]+)', html)
    assert badges
    assert all(label.strip() for label in badges)
    assert {"Available", "Passed", "Configured"}.intersection(label.strip() for label in badges)


def test_settings_styles_include_responsive_overflow_touch_and_reduced_motion_contracts():
    stylesheet = (ROOT / "app/static/assets/css/custom.css").read_text(encoding="utf-8")
    assert ".settings-grid" in stylesheet
    assert ".settings-status-list" in stylesheet
    assert "min-width: 0" in stylesheet
    assert "@media (max-width: 767.98px)" in stylesheet
    assert "@media (prefers-reduced-motion: reduce)" in stylesheet
    assert "min-height: 44px" in stylesheet


def test_post_milestone8_relationship_migration_is_the_only_next_revision():
    revisions = sorted(path.name for path in (ROOT / "migrations/versions").glob("*.py"))
    assert any("0004_catalogue_lifecycle" in name for name in revisions)
    assert [name for name in revisions if "0005" in name] == ["0005_product_relationships.py"]


def test_authentication_and_setup_templates_have_one_semantic_h1():
    templates = (
        "app/templates/auth/login.html",
        "app/templates/auth/forgot_password.html",
        "app/templates/auth/reset_password.html",
        "app/templates/setup/setup.html",
        "app/templates/setup/initial_settings.html",
        "app/templates/setup/initial_scan.html",
    )
    for relative in templates:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert source.count("<h1") == 1, relative
        assert "auth-brand-title" in source, relative
