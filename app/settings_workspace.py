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
    setup_complete = User.query.first() is not None and settings is not None

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
