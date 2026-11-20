"""Safe read-only presentation for runtime and deployment settings."""

from __future__ import annotations

import os
import platform
import re
from pathlib import Path

from flask import current_app
from sqlalchemy import text

from app import db
from app.database import migration_head
from app.models import Settings, User
from app.operations_workspace import SCAN_MODES, scanner_readiness
from app.utils.backup_retention import METADATA_AGE, METADATA_COUNT
from app.utils.discord import configuration_summary
from app.utils.operation_control import (
    FAILURE_OPERATION_AGE,
    ROUTINE_OPERATION_AGE,
    ROUTINE_OPERATION_COUNT,
)
from app.utils.operation_live import PERSISTED_LOG_BYTE_LIMIT, PERSISTED_LOG_LINE_LIMIT


_SAFE_BUILD = re.compile(r"^[A-Za-z0-9._+-]{1,64}$")
_CHANNEL_LABELS = {
    "scans_info": "Scanner updates",
    "scans_errors": "Scanner warnings and failures",
    "edits": "Metadata updates",
    "overrides": "Product overrides",
    "ingest": "Product ingest",
}


def _safe_build_identifier():
    value = os.environ.get("APP_VERSION") or os.environ.get("APP_BUILD") or ""
    return value if _SAFE_BUILD.fullmatch(value) else "Not supplied"


def _environment_label():
    if current_app.testing:
        return "Testing"
    value = str(current_app.config.get("ENV") or os.environ.get("FLASK_ENV") or "production").lower()
    return {"development": "Development", "testing": "Testing"}.get(value, "Production")


def _platform_label():
    system = re.sub(r"[^A-Za-z0-9._+-]", "", platform.system())[:24]
    machine = re.sub(r"[^A-Za-z0-9._+-]", "", platform.machine())[:24]
    return " · ".join(value for value in (system, machine) if value) or "Unavailable"


def _directory_state(path, *, writable=False):
    if not path or not os.path.isdir(path):
        return False
    mode = os.R_OK | (os.W_OK if writable else 0)
    return os.access(path, mode)


def _database_state():
    readable = False
    integrity = "unavailable"
    writable = False
    try:
        db.session.execute(text("SELECT 1"))
        readable = True
        if db.engine.dialect.name == "sqlite":
            integrity = db.session.execute(text("PRAGMA quick_check")).scalar() or "unavailable"
            database_name = db.engine.url.database
            if database_name and database_name != ":memory:":
                database_path = Path(database_name)
                writable = database_path.exists() and os.access(database_path, os.W_OK) and os.access(database_path.parent, os.W_OK)
            else:
                writable = True
        else:
            integrity = "available"
            writable = True
    except Exception:
        db.session.rollback()
    return {"readable": readable, "writable": writable, "integrity": integrity}


def _status(label, ok, *, available="Available", unavailable="Unavailable", detail="", summary=None):
    state = available if ok else unavailable
    return {
        "label": label,
        "ok": bool(ok),
        "state": state,
        "summary": summary or f"{label} {state.lower()}",
        "detail": detail,
    }


def build_settings_workspace():
    """Return labels and booleans only; never return configured values or paths."""

    settings = Settings.query.first()
    readiness = scanner_readiness()
    database = _database_state()
    discord = configuration_summary()
    from app.woocommerce_connection import build_woocommerce_workspace
    woo = build_woocommerce_workspace()
    catalogue_ok = _directory_state(settings.product_folder if settings else None)
    output_ok = _directory_state(settings.output_folder if settings else None, writable=True)
    app_data_ok = _directory_state(current_app.instance_path, writable=True)
    from app.onboarding import pending
    setup_complete = User.query.first() is not None and settings is not None and not pending()

    active = readiness.get("active")
    active_label = "No operation active"
    if active:
        active_label = f"{str(active.get('operation_type') or 'Catalogue operation').replace('_', ' ').title()} active"

    channel_states = [
        {
            "label": label,
            "configured": discord["channel_states"].get(channel) == "configured",
        }
        for channel, label in _CHANNEL_LABELS.items()
    ]

    return {
        "application": {
            "build": _safe_build_identifier(),
            "environment": _environment_label(),
            "platform": _platform_label(),
            "database_available": database["readable"],
            "migration_head": migration_head(),
            "integrity_ok": database["integrity"] in {"ok", "available"},
            "setup_complete": setup_complete,
        },
        "storage": [
            _status(
                "Catalogue",
                catalogue_ok,
                detail="Scanner source is readable." if catalogue_ok else "Catalogue source cannot be read; scanner operations are unavailable.",
            ),
            _status(
                "Output",
                output_ok,
                detail="Generated output can be written." if output_ok else "Scanner modes requiring output are unavailable.",
            ),
            _status(
                "App data",
                app_data_ok,
                detail="Persistent application storage is writable." if app_data_ok else "Persistent application state cannot be written.",
            ),
            _status("Database", database["readable"], available="Readable", unavailable="Unavailable", summary="Database readable" if database["readable"] else "Database unavailable"),
            _status("Database", database["writable"], available="Writable", unavailable="Read only", summary="Database writable" if database["writable"] else "Database read only"),
        ],
        "scanner": {
            "modes": [mode["label"] for mode in SCAN_MODES],
            "active": bool(active),
            "active_label": active_label,
            "lock_label": "Occupied" if active else "Available",
            "mounts_ready": readiness["mounts_ready"],
        },
        "discord": {
            "enabled": discord["enabled"],
            "state": discord["state"],
            "channels": channel_states,
            "display_name_state": discord["display_name_state"],
            "avatar_state": discord["avatar_state"],
        },
        "woocommerce": {
            "configured": woo["configuration"]["configured"],
            "store_url_configured": woo["configuration"]["store_url_configured"],
            "consumer_key_configured": woo["configuration"]["consumer_key_configured"],
            "consumer_secret_configured": woo["configuration"]["consumer_secret_configured"],
            "configuration_source": woo["configuration"]["configuration_source"],
            "last_result": woo["health"]["state"],
            "selected_namespace": woo["health"]["latest"].get("selected_namespace") or "Not tested",
        },
        "retention": {
            "routine_count": ROUTINE_OPERATION_COUNT,
            "routine_days": ROUTINE_OPERATION_AGE.days,
            "failure_days": FAILURE_OPERATION_AGE.days,
            "log_lines": PERSISTED_LOG_LINE_LIMIT,
            "log_kib": PERSISTED_LOG_BYTE_LIMIT // 1024,
            "metadata_count": METADATA_COUNT,
            "metadata_days": METADATA_AGE.days,
        },
    }


def configuration_inventory(*, show_paths=False):
    """Explicit safe allowlist, not a process-environment dump or editable override."""
    settings = Settings.query.first()
    proof = current_app.config.get("TLC_DEPLOYMENT_PROOF")
    managed = bool(proof)
    role = proof[0].environment if managed else "TLCDashboard"
    unraid = f"Unraid → Docker → {role} container → Edit; recreate/restart container"
    local = "Existing application setup; save through its established form"
    groups = []

    def group(label, rows):
        groups.append({"label": label, "rows": rows})

    def row(label, value, source, *, status="Configured", where=unraid):
        return {"label": label, "value": value, "source": source, "status": status, "where": where}

    def path_value(value):
        return str(value) if show_paths and value else "Configured path" if value else "Not configured"

    def effective_path(key, saved):
        value = current_app.config.get(key)
        if value is not None:
            return row(key, path_value(value), "Docker environment" if key in os.environ else "Application default",
                       status="Configured" if value else "Missing")
        return row(key, path_value(saved), "Saved application settings" if saved else "Unset",
                   status="Configured" if saved else "Missing", where=local)

    group("Application and runtime", [
        row("Build revision", os.environ["APP_GIT_REVISION"] if _SAFE_BUILD.fullmatch(os.environ.get("APP_GIT_REVISION", "")) else "Not embedded",
            "Image metadata" if os.environ.get("APP_GIT_REVISION") else "Environment/default",
            status="Configured" if _SAFE_BUILD.fullmatch(os.environ.get("APP_GIT_REVISION", "")) else "Not available"),
        row("Deployment identity", proof[0].environment.title() if managed else "Not pinned", "Verified deployment marker" if managed else "Legacy installation",
            status="Verified" if managed else "Not verified"),
        row("Application data", path_value(current_app.instance_path), "Container instance path"),
        row("Database", path_value(db.engine.url.database) if db.engine.dialect.name == "sqlite" else "Database connection details hidden",
            "Application instance" if db.engine.dialect.name == "sqlite" else "Database configuration"),
        row("URL_PREFIX", current_app.config.get("URL_PREFIX") or "/", "Docker environment" if "URL_PREFIX" in os.environ else "Default"),
    ])
    group("Catalogue and source directories", [
        effective_path("PRODUCT_FOLDER", settings._product_folder if settings else None),
        effective_path("OUTPUT_FOLDER", settings._output_folder if settings else None),
        effective_path("INTAKE_ROOT", None), effective_path("TAXONOMY_ROOT", None),
    ])
    group("Image and file handling", [
        row("Product image maximum", "19,000,000 bytes per image", "Application validation", where="Application code; no runtime edit"),
        row("Customer photo service", "Configured" if all(current_app.config.get(key) for key in
            ("TLC_PHOTO_WORKER_URL", "TLC_PHOTO_WORKER_TOKEN", "TLC_PHOTO_PROXY_PATH")) else "Not fully configured",
            "Docker environment", status="Configured" if all(current_app.config.get(key) for key in
            ("TLC_PHOTO_WORKER_URL", "TLC_PHOTO_WORKER_TOKEN", "TLC_PHOTO_PROXY_PATH")) else "Optional / unavailable"),
    ])
    from app import commerce_config
    try:
        shopify = commerce_config.shopify_settings()
        shopify_source = "Docker environment" if commerce_config.deployment_owned() else "Private instance settings"
    except commerce_config.CommerceConfigError:
        shopify, shopify_source = {}, "Invalid private settings"
    group("Shopify configuration references", [
        row("SHOPIFY_STORE_DOMAIN", shopify.get("domain") or "Not configured", shopify_source,
            status="Configured" if shopify.get("domain") else "Missing", where=unraid if commerce_config.deployment_owned() else "Settings → Connections"),
        row("SHOPIFY_API_VERSION", shopify.get("api_version") or "Not configured", shopify_source,
            status="Configured" if shopify.get("api_version") else "Missing"),
        row("Product app credentials", "Configured" if shopify.get("client_id") and shopify.get("client_secret") else
            "Manual token configured" if shopify.get("token") else "Missing", shopify_source,
            status="Configured" if (shopify.get("client_id") and shopify.get("client_secret")) or shopify.get("token") else "Missing"),
        row("TLC_PHOTO_WORKER_TOKEN", "Configured" if current_app.config.get("TLC_PHOTO_WORKER_TOKEN") else "Missing",
            "Docker environment", status="Configured" if current_app.config.get("TLC_PHOTO_WORKER_TOKEN") else "Optional / missing"),
        row("TLC_PHOTO_WORKER_URL", "Configured (endpoint hidden)" if current_app.config.get("TLC_PHOTO_WORKER_URL") else "Not configured", "Docker environment",
            status="Configured" if current_app.config.get("TLC_PHOTO_WORKER_URL") else "Optional / missing"),
        row("TLC_PHOTO_PROXY_PATH", current_app.config.get("TLC_PHOTO_PROXY_PATH") or "Not configured", "Docker environment",
            status="Configured" if current_app.config.get("TLC_PHOTO_PROXY_PATH") else "Optional / missing"),
    ])
    group("Scheduled processing and integrations", [
        row("SHOPIFY_ORDERS_STARTUP_ENABLED", "Enabled" if current_app.config.get("SHOPIFY_ORDERS_STARTUP_ENABLED") else "Disabled",
            "Docker environment" if "SHOPIFY_ORDERS_STARTUP_ENABLED" in os.environ else "Application/deployment default"),
        row("Task intervals", "Saved per task", "Scheduled task database", where="Settings → Scheduled Tasks"),
        row("Automatic publisher authority", "Separate store-bound approval required", "Publishing policy", where="Settings → Scheduled Tasks → publishing authority"),
        row("DISCORD_ENABLED", "Enabled" if current_app.config.get("DISCORD_ENABLED") else "Disabled", "Docker environment",
            status="Configured" if current_app.config.get("DISCORD_ENABLED") else "Optional / disabled"),
    ])
    return groups


def system_status_workspace(*, show_paths=False):
    """Verified local facts only; no host Docker socket or external request."""
    import sys
    from app.models import ScheduledTask
    paths = []
    settings = Settings.query.first()
    values = [("Product catalogue", settings.product_folder if settings else None),
              ("Image output", settings.output_folder if settings else None),
              ("Intake", current_app.config.get("INTAKE_ROOT")),
              ("Taxonomy", current_app.config.get("TAXONOMY_ROOT")),
              ("Application data", current_app.instance_path),
              ("Database", db.engine.url.database if db.engine.dialect.name == "sqlite" else None)]
    seen = set()
    for label, raw in values:
        path = Path(raw) if raw else None
        exists = bool(path and path.exists())
        readable = bool(exists and os.access(path, os.R_OK))
        writable = bool(exists and os.access(path, os.W_OK))
        device, free = None, None
        if exists:
            try:
                device = os.stat(path).st_dev
                if device not in seen:
                    stat = os.statvfs(path)
                    free = stat.f_bavail * stat.f_frsize
                    seen.add(device)
            except OSError:
                pass
        paths.append({"label": label, "value": str(path) if show_paths and path else "Configured path" if path else "Not configured",
                      "exists": exists, "readable": readable, "writable": writable,
                      "filesystem": str(device) if device is not None else "Unavailable",
                      "free": free})
    proof = current_app.config.get("TLC_DEPLOYMENT_PROOF")
    revision = os.environ.get("APP_GIT_REVISION", "")
    return {"revision": revision if _SAFE_BUILD.fullmatch(revision) else "Not embedded",
            "image_tag": "Unavailable inside container; no Docker socket mounted",
            "runtime": f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "environment": proof[0].environment.title() if proof else "Not verified (legacy installation)",
            "application": "Serving this authenticated page", "paths": paths,
            "scheduler": "Configured tasks retained" if ScheduledTask.query.first() else "No scheduled task records",
            "scanner": scanner_readiness(),
            "shopify": "Not verified by page load; use Settings → Connections",
            "workers": "Process liveness not exposed to the web process; inspect task checkpoints",
            "photo": "Configured, not probed" if all(current_app.config.get(key) for key in
                ("TLC_PHOTO_WORKER_URL", "TLC_PHOTO_WORKER_TOKEN", "TLC_PHOTO_PROXY_PATH")) else "Optional / not configured"}
