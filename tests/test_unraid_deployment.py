import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PIL import Image

from app import create_app, db
from app.database import backup_database, migration_head
from app.models import User
from config import Config


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "unraid" / "my-woocommerce-dashboard.xml"
STATIC_ROOT = ROOT / "app" / "static"


@pytest.fixture
def persistent_app_factory(tmp_path):
    instance = tmp_path / "instance"
    instance.mkdir()
    database = instance / "site.db"
    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{database}"
    apps = []

    def factory():
        app = create_app()
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        apps.append(app)
        return app

    try:
        yield factory, instance, database
    finally:
        for app in apps:
            with app.app_context():
                db.session.remove()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


def _configs(root):
    return {node.attrib["Name"]: node for node in root.findall("Config")}


def test_unraid_template_has_safe_supported_contract():
    root = ET.parse(TEMPLATE).getroot()
    assert root.tag == "Container"
    assert root.findtext("Name") == "WooCommerce Dashboard"
    assert root.findtext("Repository") == "neprod/woocommerce-dashboard:0.2.3"
    assert root.findtext("Registry") == (
        "https://hub.docker.com/r/neprod/woocommerce-dashboard"
    )
    assert root.findtext("Network") == "bridge"
    assert root.findtext("WebUI") == "http://[IP]:[PORT:7485]/"
    assert root.findtext("Icon") == (
        "https://raw.githubusercontent.com/NEprod/woocommerce-dashboard/main/"
        "app/static/assets/img/woocommerce-dashboard-icon.png"
    )

    configs = _configs(root)
    assert configs["WebUI Port"].attrib == {
        "Name": "WebUI Port",
        "Target": "7485",
        "Default": "7485",
        "Mode": "tcp",
        "Description": "WooCommerce Dashboard web interface.",
        "Type": "Port",
        "Display": "always",
        "Required": "true",
        "Mask": "false",
    }
    expected_paths = {
        "Application Data": ("/app/instance", "/mnt/user/appdata/woocommerce-dashboard/instance"),
        "Product Catalogue": ("/catalogue", ""),
        "Generated Output": ("/output", ""),
    }
    for name, (target, default) in expected_paths.items():
        node = configs[name]
        assert node.attrib["Type"] == "Path"
        assert node.attrib["Target"] == target
        assert node.attrib["Default"] == default
        assert node.attrib["Mode"] == "rw"
        assert node.attrib["Required"] == "true"

    assert configs["SECRET_KEY"].attrib["Default"] == ""
    assert configs["SECRET_KEY"].attrib["Required"] == "true"
    assert configs["SECRET_KEY"].attrib["Mask"] == "true"
    assert configs["DISCORD_ENABLED"].attrib["Default"] == "false"
    xml_text = TEMPLATE.read_text(encoding="utf-8")
    assert "/Users/" not in xml_text
    assert "/mnt/user/appdata/woocommerce-dashboard/instance" in xml_text
    assert "discord.com/api/webhooks" not in xml_text


def test_all_template_referenced_static_assets_exist_and_respond(
    persistent_app_factory,
):
    factory, _instance, _database = persistent_app_factory
    references = set()
    for template in (ROOT / "app" / "templates").rglob("*.html"):
        text = template.read_text(encoding="utf-8")
        marker = "url_for('static', filename='"
        for suffix in text.split(marker)[1:]:
            references.add(suffix.split("'", 1)[0])

    assert references
    for reference in references:
        assert (STATIC_ROOT / reference).is_file(), reference

    app = factory()
    client = app.test_client()
    for reference in references:
        response = client.get(f"/static/{reference}")
        assert response.status_code == 200, reference

    icon = STATIC_ROOT / "assets" / "img" / "woocommerce-dashboard-icon.png"
    favicon = STATIC_ROOT / "assets" / "img" / "favicon" / "tlc-icon-32.png"
    with Image.open(icon) as image:
        assert image.size == (512, 512)
        assert image.format == "PNG"
    with Image.open(favicon) as image:
        assert image.size == (32, 32)
        assert image.format == "PNG"


def test_instance_database_and_backups_survive_application_replacement(
    persistent_app_factory,
):
    factory, instance, database = persistent_app_factory
    first = factory()
    with first.app_context():
        db.session.add(
            User(
                email="persistent@example.com",
                username="persistent-admin",
                password="fixture-only",
                is_admin=True,
            )
        )
        db.session.commit()
        db.session.remove()

    migration_backup = backup_database(
        database,
        source_revision=migration_head(),
        target_revision=migration_head(),
        purpose="migration",
    )
    reconstruction_backup = backup_database(
        database,
        source_revision=migration_head(),
        target_revision="reconstruction",
        purpose="reconstruction",
    )
    assert migration_backup.parent == instance / "backups"
    assert reconstruction_backup.parent == instance / "backups"

    second = factory()
    with second.app_context():
        assert User.query.filter_by(username="persistent-admin").one().is_admin
        assert second.config["DATABASE_MIGRATION_REPORT"].action == "current"

    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0] == migration_head()
    finally:
        connection.close()
    assert migration_backup.is_file()
    assert reconstruction_backup.is_file()
