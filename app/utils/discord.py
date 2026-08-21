"""Optional, bounded Discord notifications for catalogue operations."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from urllib.parse import urlparse

import requests

from app.utils.redaction import redact_diagnostic

CONNECT_TIMEOUT = 3.05
READ_TIMEOUT = 5
MAX_ATTEMPTS = 2
MAX_RATE_LIMIT_WAIT = 1.0
COLORS = {"success": 0x2ECC71, "info": 0x3498DB, "warn": 0xF1C40F, "error": 0xE74C3C}


def _env(name, default=""):
    return os.environ.get(name) or default


WEBHOOK_ENV = {
    "scans_info": "DISCORD_WEBHOOK_SCANS_INFO",
    "scans_errors": "DISCORD_WEBHOOK_SCANS_ERRORS",
    "edits": "DISCORD_WEBHOOK_EDITS",
    "overrides": "DISCORD_WEBHOOK_OVERRIDES",
    "ingest": "DISCORD_WEBHOOK_INGEST",
}
WEBHOOKS = {channel: _env(variable) for channel, variable in WEBHOOK_ENV.items()}
ENABLED = _env("DISCORD_ENABLED", "false").lower() == "true"
DEFAULT_USERNAME = _env("DISCORD_DEFAULT_USERNAME", "WooCommerce Dashboard")
DEFAULT_AVATAR = _env("DISCORD_DEFAULT_AVATAR_URL", "")
MAX_FIELD_CHARS = int(_env("DISCORD_MAX_FIELD_CHARS", "950"))
MAX_EMBED_FIELDS = int(_env("DISCORD_MAX_EMBED_FIELDS", "10"))


def _refresh_configuration():
    global ENABLED, DEFAULT_USERNAME, DEFAULT_AVATAR
    ENABLED = _env("DISCORD_ENABLED", "false").lower() == "true"
    DEFAULT_USERNAME = _env("DISCORD_DEFAULT_USERNAME", "WooCommerce Dashboard")
    DEFAULT_AVATAR = _env("DISCORD_DEFAULT_AVATAR_URL", "")
    for channel, variable in WEBHOOK_ENV.items():
        value = os.environ.get(variable)
        if value is not None:
            WEBHOOKS[channel] = value


def _valid_webhook(value):
    try:
        parsed = urlparse(value)
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname in {"discord.com", "discordapp.com"}
        and parsed.path.startswith("/api/webhooks/")
        and len([part for part in parsed.path.split("/") if part]) >= 4
        and not parsed.username
        and not parsed.password
    )


def configuration_summary():
    _refresh_configuration()
    configured = [channel for channel, value in WEBHOOKS.items() if _valid_webhook(value)]
    state = "disabled" if not ENABLED else "configured" if configured else "not_configured"
    return {"enabled": ENABLED, "state": state, "configured_channels": configured, "configured_count": len(configured)}


def _post(webhook_url, payload):
    _refresh_configuration()
    if not ENABLED:
        return False, "disabled"
    if not webhook_url:
        return False, "not configured"
    if not _valid_webhook(webhook_url):
        return False, "invalid webhook configuration"
    final_error = "delivery failed"
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = requests.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
            if 200 <= response.status_code < 300:
                return True, "sent"
            if response.status_code == 429:
                final_error = "rate limited"
                if attempt + 1 < MAX_ATTEMPTS:
                    try:
                        delay = min(MAX_RATE_LIMIT_WAIT, max(0.0, float(response.headers.get("Retry-After", "0"))))
                    except (TypeError, ValueError):
                        delay = 0
                    if delay:
                        time.sleep(delay)
                    continue
            elif 500 <= response.status_code < 600 and attempt + 1 < MAX_ATTEMPTS:
                final_error = f"Discord service returned HTTP {response.status_code}"
                continue
            else:
                final_error = f"Discord returned HTTP {response.status_code}"
            break
        except (requests.Timeout, requests.ConnectionError) as error:
            final_error = error.__class__.__name__.replace("Error", " failure").lower()
            if attempt + 1 < MAX_ATTEMPTS:
                continue
        except requests.RequestException:
            final_error = "request failure"
            break
        except Exception:
            final_error = "unexpected delivery failure"
            break
    return False, redact_diagnostic(final_error, limit=240)


def send_discord_message(content="", *, embeds=None, channels=None, username=None, avatar_url=None):
    _refresh_configuration()
    if not ENABLED:
        return False, "disabled"
    channels = channels or ["scans_info"]
    payload = {
        "content": content[:2000] if content else None,
        "username": username or DEFAULT_USERNAME,
        "avatar_url": avatar_url or DEFAULT_AVATAR,
        "embeds": embeds or [],
    }
    payload = {key: value for key, value in payload.items() if value not in (None, "", [], {})}
    sent = 0
    failures = []
    for channel in channels:
        ok, result = _post(WEBHOOKS.get(channel, ""), payload)
        if ok:
            sent += 1
        else:
            failures.append(result)
    if sent == len(channels):
        return True, "sent"
    if sent:
        return False, "partially sent"
    return False, failures[-1] if failures else "not configured"


def _truncate(value):
    value = redact_diagnostic(value or "", limit=MAX_FIELD_CHARS)
    return value if len(value) <= MAX_FIELD_CHARS else value[: MAX_FIELD_CHARS - 3] + "..."


def build_embed(title, description, color, fields=None, footer="WooCommerce Dashboard"):
    safe_fields = []
    for field in (fields or [])[:MAX_EMBED_FIELDS]:
        safe_fields.append({
            "name": _truncate(str(field.get("name", "")))[:256],
            "value": _truncate(str(field.get("value", ""))),
            "inline": bool(field.get("inline")),
        })
    return {
        "title": _truncate(title)[:256], "description": _truncate(description), "color": color,
        "fields": safe_fields,
        "footer": {"text": f"{footer} • {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')} UTC"},
    }


def notify_scan_started(mode, collection_count):
    embed = build_embed("Scan Started", f"Mode: **{mode}**\nCollections discovered: **{collection_count}**", COLORS["info"])
    return send_discord_message(embeds=[embed], channels=["scans_info"])


def notify_scan_completed(mode, summary, elapsed_text):
    warnings = int(summary.get("warnings", 0) or 0)
    fields = []
    for key in ("folders", "new_rows", "products_created", "products_updated", "variations_created", "variations_updated", "images_copied"):
        if key in summary:
            fields.append({"name": key.replace("_", " ").title(), "value": str(summary[key]), "inline": True})
    if warnings:
        fields.append({"name": "Warnings", "value": str(warnings), "inline": True})
    embed = build_embed(
        "Scan Completed with Warnings" if warnings else "Scan Completed",
        f"Mode: **{mode}**\nElapsed: **{elapsed_text}**",
        COLORS["warn"] if warnings else COLORS["success"], fields,
    )
    return send_discord_message(embeds=[embed], channels=["scans_info"])


def notify_scan_failed(mode, error_text):
    embed = build_embed("Scan Failed", f"Mode: **{mode}**\nError: `{_truncate(error_text)}`", COLORS["error"])
    return send_discord_message(embeds=[embed], channels=["scans_errors"])


def notify_editor_saved(kind, sku, path=None, *, collection=None, affected=None):
    fields = [{"name": "Kind", "value": kind, "inline": True}, {"name": "SKU", "value": sku, "inline": True}]
    if collection:
        fields.append({"name": "Collection", "value": collection, "inline": True})
    if affected is not None:
        fields.append({"name": "Affected products", "value": str(affected), "inline": True})
    embed = build_embed("Metadata Updated", "Catalogue metadata was saved successfully.", COLORS["info"], fields)
    return send_discord_message(embeds=[embed], channels=["edits"])


def notify_override_created(sku, path=None, *, product=None, collection=None):
    fields = [{"name": "SKU", "value": sku, "inline": True}]
    if product:
        fields.append({"name": "Product", "value": product, "inline": True})
    if collection:
        fields.append({"name": "Collection", "value": collection, "inline": True})
    embed = build_embed("Product Override Updated", "One product override was created.", COLORS["info"], fields)
    return send_discord_message(embeds=[embed], channels=["overrides", "edits"])


def notify_override_removed(sku, path=None, *, product=None, collection=None):
    fields = [{"name": "SKU", "value": sku, "inline": True}]
    if product:
        fields.append({"name": "Product", "value": product, "inline": True})
    if collection:
        fields.append({"name": "Collection", "value": collection, "inline": True})
    embed = build_embed("Product Override Removed", "One product override was removed.", COLORS["warn"], fields)
    return send_discord_message(embeds=[embed], channels=["overrides", "edits"])


def notify_ingest_product(*, sku, name, product_type, images_count, has_shared, has_override, folder_path, variations_count=None):
    fields = [
        {"name": "SKU", "value": f"`{sku}`", "inline": True},
        {"name": "Type", "value": product_type or "-", "inline": True},
        {"name": "Images", "value": str(images_count), "inline": True},
        {"name": "Shared JSON", "value": "✅" if has_shared else "—", "inline": True},
        {"name": "Override JSON", "value": "✅" if has_override else "—", "inline": True},
    ]
    if variations_count is not None:
        fields.append({"name": "Variations", "value": str(variations_count), "inline": True})
    description = f"**Source:** `{_truncate(folder_path)}`" if folder_path else ""
    embed = build_embed(f"Ingested Product — {name or sku}", description, COLORS["success"] if images_count else COLORS["warn"], fields)
    return send_discord_message(embeds=[embed], channels=["ingest"])
