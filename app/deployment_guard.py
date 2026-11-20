"""Opt-in deployment fence. Legacy installations do not acquire new authority."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sqlite3


MARKER = ".tlc-deployment.json"
DEV_DOMAIN = "tlc-development.myshopify.com"
DEV_SHOP = "gid://shopify/Shop/112056795527"
LIVE_SHOP = "gid://shopify/Shop/103940129151"
LIVE_APP = "gid://shopify/App/434537660417"
LIVE_CLIENT = "f9498b5a7e0393d9cddbb49defb4cacd"
PROMOTIONS_APP = "gid://shopify/App/447730089985"
PROMOTIONS_CLIENT = "1d6fc7fb70b92cce8113e7ef4a9809d1"
DEV_MARKET = "gid://shopify/Market/125965795719"
PREFIX = "TLC_DEPLOYMENT_"
KEYS = {"ENV", "STORAGE_ID", "SHOP_DOMAIN", "SHOP_ID", "PRODUCT_APP_ID",
        "ALLOW_WRITES", "ALLOW_AUTOMATION"}
STORE_COLUMNS = {
    "shopify_product_identity": "store_id", "shopify_product_comparison": "store_id",
    "shopify_collection_identity": "store_id", "shopify_attribute_identity": "store_id",
    "shopify_sale_schedule": "store_id", "packaging_link": "store_id",
    "shopify_order": "store_id", "shopify_order_sync": "store_id",
    "shopify_order_event": "store_id", "automatic_publishing": "store_id",
}


class DeploymentError(RuntimeError):
    pass


@dataclass(frozen=True)
class Identity:
    environment: str
    storage_id: str
    domain: str
    shop: str
    app: str
    writes: bool = False
    automation: bool = False

    def marker(self, role):
        return dict(version=1, environment=self.environment, storage_id=self.storage_id, role=role)


def identity(env):
    values = {k[len(PREFIX):]: v for k, v in env.items() if k.startswith(PREFIX)}
    if not values:
        return None
    role, storage, domain, shop, app = (values.get(k, "") for k in
        ("ENV", "STORAGE_ID", "SHOP_DOMAIN", "SHOP_ID", "PRODUCT_APP_ID"))
    if (set(values) - KEYS or role not in {"development", "production"}
            or not re.fullmatch(r"[0-9a-f]{32}", storage)
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.myshopify\.com", domain)
            or not re.fullmatch(r"gid://shopify/App/[0-9]{1,30}", app)
            or app == PROMOTIONS_APP
            or (role == "development" and (domain != DEV_DOMAIN or shop != DEV_SHOP or app == LIVE_APP))
            or (role == "production" and (domain == DEV_DOMAIN or shop != LIVE_SHOP or app != LIVE_APP))
            or any(values.get(k, "false") not in {"true", "false"} for k in ("ALLOW_WRITES", "ALLOW_AUTOMATION"))):
        raise DeploymentError("Deployment identity is incomplete or conflicts with the pinned environment.")
    writes = values.get("ALLOW_WRITES") == "true"
    automation = values.get("ALLOW_AUTOMATION") == "true"
    if automation and not writes:
        raise DeploymentError("Automation requires deployment write authority.")
    return Identity(role, storage, domain, shop, app, writes, automation)


def roots_for(instance, env):
    roots = {"instance": instance, "catalogue": env.get("PRODUCT_FOLDER"),
             "output": env.get("OUTPUT_FOLDER")}
    for role, key in (("intake", "INTAKE_ROOT"), ("taxonomy", "TAXONOMY_ROOT")):
        raw = env.get(key, "/" + role)
        if raw and (Path(raw).exists() or raw != "/" + role):
            roots[role] = raw
    return {role: Path(raw) if raw else None for role, raw in roots.items()}


def _ancestor(a, b):
    return a == b or a in b.parents


def _mount_aliases(paths):
    """Detect nested/aliased Linux mounts even when their visible paths differ."""
    info = Path("/proc/self/mountinfo")
    if not info.is_file():
        return []
    def unescape(s):
        return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), s)
    mounts = []
    for row in info.read_text().splitlines():
        parts = row.split()
        mounts.append((Path(unescape(parts[4])), parts[2], Path(unescape(parts[3]))))
    aliases = []
    for path in paths:
        if any(path != m[0] and _ancestor(path, m[0]) for m in mounts):
            raise DeploymentError("Nested mounts under a managed storage root are not allowed.")
        points = [m for m in mounts if _ancestor(m[0], path)]
        if points:
            point, device, source = max(points, key=lambda m: len(m[0].parts))
            aliases.append((device, source / path.relative_to(point)))
    return aliases


def validate_storage(owner, roots):
    if not owner:
        if any(p and os.path.lexists(p / MARKER) for p in roots.values()):
            raise DeploymentError("Marked storage requires explicit deployment identity; legacy fallback refused.")
        return
    paths, seen = [], set()
    for role, path in roots.items():
        if not path or not path.is_absolute() or not path.is_dir() or path.resolve() != path:
            raise DeploymentError("Managed storage requires separate existing absolute directories without symlinks.")
        st = path.stat()
        alias = (st.st_dev, st.st_ino)
        if alias in seen or any(_ancestor(path, other) or _ancestor(other, path) for other in paths):
            raise DeploymentError("Managed storage roots overlap or alias each other.")
        seen.add(alias)
        paths.append(path)
        marker = path / MARKER
        try:
            if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 1024:
                raise ValueError()
            if json.loads(marker.read_bytes()) != owner.marker(role):
                raise ValueError()
        except (OSError, ValueError):
            raise DeploymentError("A storage identity marker is missing or belongs to another environment or role.") from None
        if role == "instance":
            for name in ("site.db", "site.db-wal", "site.db-shm", "site.db-journal"):
                db = path / name
                if os.path.lexists(db) and (db.is_symlink() or not db.is_file() or db.stat().st_nlink != 1):
                    raise DeploymentError("Managed SQLite storage must be a regular non-aliased file.")
    aliases = _mount_aliases(paths)
    for i, (device, path) in enumerate(aliases):
        if any(device == d and (_ancestor(path, p) or _ancestor(p, path)) for d, p in aliases[:i]):
            raise DeploymentError("Managed storage resolves to overlapping host mounts.")


def _local_comparison_failure(key, raw):
    """A status-read failure is local retry state, not a remote identity."""
    match = re.fullmatch(r"unverified:([0-9a-f]{64})", key) if isinstance(key, str) else None
    if not match or not isinstance(raw, str) or len(raw) > 32768:
        return False
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        return False
    if not isinstance(data, dict) or set(data) != {
        "version", "config", "classification", "failures", "retry_after", "refresh_failure", "failure_local"
    }:
        return False  # Never exempt a row carrying remote state or comparison evidence.
    failure = data["refresh_failure"]
    local = data["failure_local"]
    return (data["version"] == 1 and data["config"] == match[1] and data["classification"] == "unavailable"
        and type(data["failures"]) is int and 1 <= data["failures"] <= 6
        and isinstance(data["retry_after"], str)
        and (local is None or isinstance(local, str) and re.fullmatch(r"[0-9a-f]{64}", local) is not None)
        and isinstance(failure, dict) and {"at", "message"} <= failure.keys() <= {"at", "message", "kind"}
        and all(isinstance(value, str) for value in failure.values()))


def audit_database(owner, path):
    if not path.exists():
        return
    try:
        from contextlib import closing
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as con:
            con.execute("PRAGMA trusted_schema=OFF")
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
            for table, column in STORE_COLUMNS.items():
                if table not in tables:
                    continue
                outside_store = f"FROM {table} WHERE {column} IS NOT NULL AND {column} != ?"
                if table == "shopify_product_comparison":
                    mismatch = any(not _local_comparison_failure(key, summary) for key, summary in con.execute(
                        f"SELECT {column}, summary_json {outside_store}", (owner.shop,)))
                else:
                    mismatch = con.execute(f"SELECT 1 {outside_store} LIMIT 1", (owner.shop,)).fetchone()
                if mismatch:
                    raise DeploymentError(f"Database contains Shopify references from another store in {table}.{column}; migration is required.")
    except sqlite3.Error:
        raise DeploymentError("Managed database identity could not be checked before startup.") from None


def verify_startup(instance, env, *, verify_product=None, verify_promotions=None):
    owner = identity(env)
    roots = roots_for(instance, env)
    validate_storage(owner, roots)
    if not owner:
        return None
    saved = roots["instance"] / "commerce.json"
    if os.path.lexists(saved):
        try:
            if (saved.is_symlink() or not saved.is_file() or saved.stat().st_size > 16384
                    or json.loads(saved.read_bytes()) != {"version": 1, "active_platform": "shopify", "shopify": {}}):
                raise ValueError()
        except (ValueError, OSError):
            raise DeploymentError("Managed storage contains legacy commerce settings; stage a clean copy.") from None
    # Keep the Phase B fence around copied ancillary service settings. Photos
    # are a separate opt-in: only the real dashboard fields are allowed, and
    # the photo client independently verifies the expected shop before use.
    if any(v for k, v in env.items() if k.startswith((
            "SHOPIFY_ORDERS_WEBHOOK_", "WOO_", "DISCORD_WEBHOOK"))
            or k == "SHOPIFY_ONLINE_STORE_PUBLICATION_ID"):
        raise DeploymentError("Managed startup requires legacy webhook, Woo, Discord and publication overrides unset.")
    from app.customer_photos import ENV_KEYS as PHOTO_KEYS, parse_settings as photo_settings
    if (any(v for k, v in env.items() if k.startswith("TLC_PHOTO_") and k not in PHOTO_KEYS)
            or any(env.get(k) for k in PHOTO_KEYS) and not photo_settings(env)[0]):
        raise DeploymentError("Managed customer photos require a valid Worker HTTPS origin, token and Shopify app-proxy path, or all three unset.")
    from app.shopify_connection import ShopifyAdapter, ShopifyConfiguration, ShopifyError
    try:
        cfg = ShopifyConfiguration(env.get("SHOPIFY_STORE_DOMAIN", ""),
            env.get("SHOPIFY_ADMIN_ACCESS_TOKEN", ""), env.get("SHOPIFY_API_VERSION") or "2026-10",
            env.get("SHOPIFY_CLIENT_ID", ""), env.get("SHOPIFY_CLIENT_SECRET", "")).validated()
    except ShopifyError:
        raise DeploymentError("Managed startup requires valid environment-owned product app credentials.") from None
    if (cfg.domain != owner.domain or cfg.client_id == PROMOTIONS_CLIENT
            or owner.environment == "development" and cfg.client_id == LIVE_CLIENT
            or owner.environment == "production" and cfg.client_id and cfg.client_id != LIVE_CLIENT):
        raise DeploymentError("Product Shopify configuration does not match the deployment identity.")
    # Promotions is now a local planner. Preserve its historical configuration
    # fields, but never require its separate Shopify app to start TLCDashboard.
    promo = None
    audit_database(owner, roots["instance"] / "site.db")
    try:
        data = (verify_product or ShopifyAdapter().deployment_identity)(cfg)
    except ShopifyError:
        raise DeploymentError("Product app authentication or Shopify identity read failed.") from None
    if data != (owner.domain, owner.shop, owner.app, owner.environment == "development"):
        raise DeploymentError("Authenticated Shopify store or product app does not match the deployment identity.")
    return owner, cfg.fingerprint(), promo


def promotions_config(owner, env):
    from app.promotions.shopify import Configuration, ShopifyPromotionError
    prefix = "TLC_PROMOTIONS_DEV_"
    if not any(v for k, v in env.items() if k.startswith(prefix)):
        return None
    cfg = Configuration(*(env.get(prefix + k, "").strip() for k in
        ("DOMAIN", "STORE_ID", "APP_ID", "CLIENT_ID", "KEY_ID", "PRIVATE_KEY_PATH", "UK_MARKET_ID")))
    if (owner.environment != "development" or (cfg.domain, cfg.store, cfg.app, cfg.client) !=
            (DEV_DOMAIN, DEV_SHOP, PROMOTIONS_APP, PROMOTIONS_CLIENT) or
            cfg.uk_market not in {"", DEV_MARKET}):
        raise DeploymentError("Promotions credentials do not match the existing development installation.")
    try:
        return cfg.validate()
    except ShopifyPromotionError:
        raise DeploymentError("Promotions development settings are incomplete.") from None


def product_transport(cfg, *, writing):
    from flask import current_app, has_app_context
    proof = current_app.config.get("TLC_DEPLOYMENT_PROOF") if has_app_context() else None
    if proof and (cfg.validated().fingerprint() != proof[1] or writing and not proof[0].writes):
        from app.shopify_connection import ShopifyError
        raise ShopifyError("Shopify operation refused by the deployment identity or write policy.")


def promotion_transport(cfg, *, writing=False):
    from flask import current_app, has_app_context
    proof = current_app.config.get("TLC_DEPLOYMENT_PROOF") if has_app_context() else None
    if proof and (proof[2] != cfg or writing and not proof[0].writes):
        from app.promotions.shopify import ShopifyPromotionError
        raise ShopifyPromotionError("Promotions operation refused by the deployment identity or write policy.")


if __name__ == "__main__":
    try:
        validate_storage(identity(os.environ), roots_for("/app/instance", os.environ))
    except DeploymentError as exc:
        raise SystemExit(str(exc)) from None
