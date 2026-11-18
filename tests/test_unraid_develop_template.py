"""The repository templates track real settings, not installed Unraid XML."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from app import commerce_config, customer_photos, deployment_guard as guard
from app.product_defaults import DEFAULTS
from app.utils.discord import WEBHOOK_ENV
from config import Config

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "unraid"
FILES = {
    "development": "my-tlcdashboard-develop.xml",
    "production": "my-tlcdashboard-production.xml",
}
DEV_PATHS = {
    "/app/instance": "/mnt/cache/appdata/woocommerce-dashboard-dev/instance",
    **{target: "/mnt/user/data/tlc/09_Development_Products/" + folder + "/" for target, folder in (
        ("/catalogue", "products"), ("/output", "uploads"), ("/intake", "intake"), ("/taxonomy", "taxonomy")
    )},
}
PROMO = {f"TLC_PROMOTIONS_DEV_{name}" for name in (
    "DOMAIN", "STORE_ID", "APP_ID", "CLIENT_ID", "KEY_ID", "PRIVATE_KEY_PATH", "UK_MARKET_ID"
)}
PHOTO = set(customer_photos.ENV_KEYS)
LEGACY = set(WEBHOOK_ENV.values()) | {
    "WOO_STORE_URL", "WOO_CONSUMER_KEY", "WOO_CONSUMER_SECRET", "SHOPIFY_ONLINE_STORE_PUBLICATION_ID",
    *(key for key in vars(Config) if key.startswith("SHOPIFY_ORDERS_WEBHOOK_")),
}
RUNTIME_VARIABLES = {
    *(f"TLC_DEPLOYMENT_{key}" for key in guard.KEYS),
    *commerce_config.ENV_KEYS,
    *(value[0] for value in DEFAULTS.values()),
    *(key for key in vars(Config) if key.startswith(("SHOPIFY_ORDERS_", "TLC_PHOTO_"))),
    *LEGACY,
    "PRODUCT_FOLDER", "OUTPUT_FOLDER", "INTAKE_ROOT", "TAXONOMY_ROOT", "URL_PREFIX",
    "SECRET_KEY", "PUID", "PGID", "UMASK", "TZ",
    "SHOPIFY_NATIVE_DIMENSIONS_ENABLED", "SHOPIFY_PRODUCT_DIMENSIONS_NAMESPACE",
    "DISCORD_ENABLED", "DISCORD_DEFAULT_USERNAME", "DISCORD_DEFAULT_AVATAR_URL",
    "DISCORD_MAX_FIELD_CHARS", "DISCORD_MAX_EMBED_FIELDS",
}


def template(role):
    root = ET.parse(DIRECTORY / FILES[role]).getroot()
    return root, {node.attrib["Target"]: node for node in root.findall("Config")}


def environment(configs):
    return {key: node.get("Default") for key, node in configs.items() if node.get("Type") == "Variable"}


def test_only_two_repository_container_templates():
    assert {path.name for path in DIRECTORY.glob("*.xml")} == set(FILES.values())


@pytest.mark.parametrize("role", FILES)
def test_supported_environment_contract_and_no_duplicate_fields(role):
    root, configs = template(role)
    names = [node.get("Name") for node in root.findall("Config")]
    assert len(configs) == len(names) == len(set(names))
    env = environment(configs)
    assert set(env) == RUNTIME_VARIABLES | (PROMO if role == "development" else set())
    assert all((configs[k].text or "") == default for k, default in env.items())
    assert (env["PRODUCT_FOLDER"], env["OUTPUT_FOLDER"], env["INTAKE_ROOT"], env["TAXONOMY_ROOT"]) == (
        "/catalogue", "/output", "/intake", "/taxonomy")
    assert env["SHOPIFY_PRODUCT_DIMENSIONS_NAMESPACE"] == "tlc_dashboard"
    assert env["TLC_DEPLOYMENT_ALLOW_AUTOMATION"] == env["TLC_DEPLOYMENT_ALLOW_WRITES"] == "false"
    assert env["SHOPIFY_NATIVE_DIMENSIONS_ENABLED"] == env["DISCORD_ENABLED"] == "false"
    assert env["SHOPIFY_API_VERSION"] == ""
    assert "2026-10" in configs["SHOPIFY_API_VERSION"].get("Description")
    assert "Do not set 2027-01" in configs["SHOPIFY_API_VERSION"].get("Description")
    assert root.findtext("Network") == "bridge"
    assert root.findtext("Privileged") == "false"
    assert configs["7485"].get("Mode") == "tcp"
    assert "--log-driver=local" in root.findtext("ExtraParams")
    assert not root.findtext("PostArgs")


def test_separate_channels_identities_and_mount_defaults():
    dev, dc = template("development")
    prod, pc = template("production")
    assert dev.findtext("Name") == "WooCommerceDashboard-dev"
    assert prod.findtext("Name") == "TLCDashboard-Production"
    assert dev.findtext("Repository") == "neprod/woocommerce-dashboard:develop"
    assert prod.findtext("Repository") == "neprod/woocommerce-dashboard:latest"
    for target, host in DEV_PATHS.items():
        assert dc[target].get("Default") == dc[target].text == host
        assert pc[target].get("Default") == "" and not pc[target].text
        for cfg in (dc, pc):
            assert cfg[target].get("Type") == "Path" and cfg[target].get("Mode") == "rw"
            assert cfg[target].get("Required") == ("false" if target in {"/taxonomy", "/intake"} else "true")
    assert dc["/run/secrets/tlc-promotions.pem"].get("Mode") == "ro"
    assert dc["/run/secrets/tlc-promotions.pem"].get("Default") == ""
    assert "/run/secrets/tlc-promotions.pem" not in pc
    assert dc["SHOPIFY_STORE_DOMAIN"].text == dc["TLC_DEPLOYMENT_SHOP_DOMAIN"].text == guard.DEV_DOMAIN
    assert not pc["SHOPIFY_STORE_DOMAIN"].text and not pc["TLC_DEPLOYMENT_SHOP_DOMAIN"].text
    assert dc["TLC_DEPLOYMENT_SHOP_ID"].text == guard.DEV_SHOP
    assert pc["TLC_DEPLOYMENT_SHOP_ID"].text == guard.LIVE_SHOP
    assert pc["TLC_DEPLOYMENT_PRODUCT_APP_ID"].text == guard.LIVE_APP
    assert dc["TLC_DEPLOYMENT_PRODUCT_APP_ID"].text not in {guard.LIVE_APP, guard.PROMOTIONS_APP}
    assert guard.DEV_SHOP not in (DIRECTORY / FILES["production"]).read_text()


@pytest.mark.parametrize("role", FILES)
def test_blank_masked_secrets_and_legacy_limits_are_explicit(role):
    _, configs = template(role)
    for name, node in configs.items():
        if node.get("Mask") == "true":
            assert node.get("Default") == "" and not node.text, name
    for required_secret in (
        "SECRET_KEY", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET", "SHOPIFY_ADMIN_ACCESS_TOKEN",
        "TLC_PHOTO_WORKER_TOKEN", "SHOPIFY_ORDERS_WEBHOOK_TOKEN", "SHOPIFY_ORDERS_WEBHOOK_KEY",
        "SHOPIFY_ORDERS_WEBHOOK_SECRET", *WEBHOOK_ENV.values(),
    ):
        assert configs[required_secret].get("Mask") == "true"
    for name in LEGACY:
        node = configs[name]
        assert node.get("Default") == "" and node.get("Required") == "false"
        assert node.get("Name").startswith("Legacy only:")
        assert "startup guard rejects" in node.get("Description")
    assert PHOTO <= configs.keys()
    assert all(configs[key].get("Required") == "false" and not configs[key].text for key in PHOTO)
    assert all(not configs[key].get("Name").startswith("Legacy only:") for key in PHOTO)
    assert all(configs[key].get("Default") == "" for key in PROMO if key in configs)


@pytest.fixture(params=FILES)
def guarded_environment(request, tmp_path):
    tmp_path = tmp_path.resolve()
    role = request.param
    _, configs = template(role)
    env = environment(configs)
    env.update(TLC_DEPLOYMENT_STORAGE_ID="a" * 32 if role == "development" else "b" * 32,
               SHOPIFY_ADMIN_ACCESS_TOKEN="synthetic-fixture-token")
    # The production XML cannot select a real destination without operator input.
    if role == "production":
        env["TLC_DEPLOYMENT_SHOP_DOMAIN"] = env["SHOPIFY_STORE_DOMAIN"] = "fictional-live.myshopify.com"
    instance = tmp_path / "instance"
    for mount_role, key in (("instance", None), ("catalogue", "PRODUCT_FOLDER"),
                            ("output", "OUTPUT_FOLDER"), ("intake", "INTAKE_ROOT"), ("taxonomy", "TAXONOMY_ROOT")):
        path = tmp_path / mount_role
        path.mkdir()
        (path / guard.MARKER).write_text(json.dumps(guard.identity(env).marker(mount_role)))
        if key:
            env[key] = str(path)
    return role, instance, env


def test_managed_defaults_use_real_guard_without_optional_integrations(guarded_environment):
    role, instance, env = guarded_environment
    seen = []

    def product(config):
        seen.append(config.domain)
        return (config.domain, env["TLC_DEPLOYMENT_SHOP_ID"], env["TLC_DEPLOYMENT_PRODUCT_APP_ID"], role == "development")

    owner, _, promotion = guard.verify_startup(instance, env, verify_product=product)
    assert seen == [env["TLC_DEPLOYMENT_SHOP_DOMAIN"]]
    assert owner.environment == role and not owner.writes and not owner.automation
    assert promotion is None


def test_enablement_accepts_valid_photo_config_in_either_mode(guarded_environment):
    role, instance, env = guarded_environment
    env.update(TLC_PHOTO_WORKER_URL="https://photos.example.invalid",
               TLC_PHOTO_WORKER_TOKEN="synthetic-worker-token-not-for-a-live-service",
               TLC_PHOTO_PROXY_PATH="/apps/tlc-customer-uploads")
    proof = guard.verify_startup(instance, env, verify_product=lambda config: (
        config.domain, env["TLC_DEPLOYMENT_SHOP_ID"], env["TLC_DEPLOYMENT_PRODUCT_APP_ID"], role == "development"))
    assert proof[0].environment == role and not proof[0].automation


@pytest.mark.parametrize("bad", [
    {"TLC_PHOTO_WORKER_URL": "https://photos.example.invalid"},
    {"TLC_PHOTO_WORKER_URL": "http://photos.example.invalid",
     "TLC_PHOTO_WORKER_TOKEN": "synthetic-worker-token-not-for-a-live-service",
     "TLC_PHOTO_PROXY_PATH": "/apps/tlc-customer-uploads"},
    {"TLC_PHOTO_BUCKET": "raw-r2-settings-are-not-dashboard-fields"},
])
def test_invalid_managed_photo_fields_refuse_startup_before_remote_access(guarded_environment, bad):
    _, instance, env = guarded_environment
    env.update(bad)
    with pytest.raises(guard.DeploymentError, match="Managed customer photos") as raised:
        guard.verify_startup(instance, env, verify_product=lambda _: pytest.fail("Must reject before Shopify access"))
    assert all(value not in str(raised.value) for value in bad.values())


@pytest.mark.parametrize("enabled", [False, True])
def test_managed_flask_startup_loads_only_current_photo_environment(guarded_environment, monkeypatch, enabled):
    import app as dashboard
    from flask import Flask
    from app.shopify_connection import ShopifyAdapter

    role, instance, env = guarded_environment
    dev_photo = dict(TLC_PHOTO_WORKER_URL=f"https://{role}-fixture.example.invalid",
                     TLC_PHOTO_WORKER_TOKEN=f"synthetic-{role}-dashboard-worker-token",
                     TLC_PHOTO_PROXY_PATH="/apps/tlc-customer-uploads")
    env["SECRET_KEY"] = "unit-test-session-value-not-for-deployment"
    # Model an earlier app import with a different configuration in memory.
    for key in PHOTO:
        monkeypatch.setattr(Config, key, "earlier-environment-value")
    for key, value in (env | (dev_photo if enabled else {})).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", "sqlite:///" + str(instance / "site.db"))
    monkeypatch.setattr(dashboard, "Flask", lambda *args, **kwargs: Flask(
        "managed_template_test", instance_path=str(instance), **kwargs))
    monkeypatch.setattr(ShopifyAdapter, "deployment_identity", lambda _, config: (
        config.domain, env["TLC_DEPLOYMENT_SHOP_ID"], env["TLC_DEPLOYMENT_PRODUCT_APP_ID"], role == "development"))
    monkeypatch.setattr(customer_photos, "_request", lambda *_: pytest.fail("No Worker traffic on startup"))
    flask_app = dashboard.create_app(recover=False)
    with flask_app.app_context():
        public, token = customer_photos.settings()
        if enabled:
            assert public == {"origin": dev_photo["TLC_PHOTO_WORKER_URL"], "path": dev_photo["TLC_PHOTO_PROXY_PATH"]}
            assert token == dev_photo["TLC_PHOTO_WORKER_TOKEN"]
        else:
            assert public is token is None
