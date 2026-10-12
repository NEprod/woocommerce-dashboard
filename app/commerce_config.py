"""Versioned installation settings, separate from catalogue and remote identities.

Secrets use protected instance storage (0600), not authored JSON or browser state.
Environment-owned Shopify settings take precedence as one configuration unit.
"""
import fcntl
import hashlib
import json
import os
import stat
from pathlib import Path

from flask import current_app
from app.utils.atomic_files import atomic_write_json


class CommerceConfigError(ValueError):
    pass


def _path():
    return Path(current_app.instance_path) / "commerce.json"


def document():
    try:
        fd = os.open(_path(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise CommerceConfigError("Commerce settings require a private regular file (0600).")
            raw = source.read(16385)
        if len(raw) > 16384:
            raise ValueError()
        data = json.loads(raw)
        if (data.get("version") != 1 or data.get("active_platform") not in {"woocommerce", "shopify"}
                or not isinstance(data.get("shopify", {}), dict)):
            raise ValueError()
        if any(not isinstance(value, str) for value in data.get("shopify", {}).values()):
            raise ValueError()
        return data
    except FileNotFoundError:
        return {"version": 1, "active_platform": "woocommerce", "shopify": {}}
    except (OSError, ValueError, AttributeError):
        raise CommerceConfigError("Commerce settings are unreadable or invalid. Restore protected instance settings; do not switch platforms.") from None


def active_platform():
    return document()["active_platform"]


def revision():
    return hashlib.sha256(json.dumps(document(), sort_keys=True).encode()).hexdigest()


def _save(transform):
    # Lock the existing instance directory; stage/replace uses the established helper.
    fd = None
    try:
        fd = os.open(current_app.instance_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fcntl.flock(fd, fcntl.LOCK_EX)
        data = transform(document())
        atomic_write_json(_path(), data)  # mkstemp supplies 0600, including replacement.
    except OSError:
        raise CommerceConfigError("Commerce settings could not be saved. Check instance storage permissions.") from None
    finally:
        if fd is not None:
            os.close(fd)


def initialize_platform(platform):
    """Only first administrator creation may choose a platform; retries cannot switch."""
    from app.models import User
    if platform not in {"woocommerce", "shopify"} or User.query.first() is not None:
        raise CommerceConfigError("An established installation cannot switch platforms here.")
    def select(data):
        if os.path.lexists(_path()) and data["active_platform"] != platform:
            raise CommerceConfigError("The installation platform is already selected. Restore/review setup before continuing.")
        return dict(data, active_platform=platform)
    _save(select)


ENV_KEYS = ("SHOPIFY_STORE_DOMAIN", "SHOPIFY_ADMIN_ACCESS_TOKEN", "SHOPIFY_API_VERSION",
            "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET")


def deployment_owned():
    return any(os.environ.get(k) for k in ENV_KEYS)


def shopify_settings():
    if deployment_owned():
        settings = dict(domain=os.environ.get(ENV_KEYS[0], ""), token=os.environ.get(ENV_KEYS[1], ""),
                        api_version=os.environ.get(ENV_KEYS[2]) or "2026-10")
        # Preserve the original manual settings shape for existing consumers.
        if os.environ.get("SHOPIFY_CLIENT_ID") or os.environ.get("SHOPIFY_CLIENT_SECRET"):
            settings.update(client_id=os.environ.get("SHOPIFY_CLIENT_ID", ""),
                            client_secret=os.environ.get("SHOPIFY_CLIENT_SECRET", ""))
        return settings
    return document().get("shopify", {})


def save_shopify(settings, expected_revision):
    if deployment_owned():
        raise CommerceConfigError("Shopify configuration is deployment-owned; update it in your environment.")
    def replace(data):
        digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        if digest != expected_revision:
            raise CommerceConfigError("Settings changed during review. Reload and test again.")
        return dict(data, shopify=settings)
    _save(replace)
