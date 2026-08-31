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
    return {
        "enabled": ENABLED,
        "state": state,
        "configured_channels": configured,
        "configured_count": len(configured),
        "channel_states": {
            channel: "configured" if channel in configured else "not_configured"
            for channel in WEBHOOK_ENV
        },
        "display_name_state": (
            "configured" if os.environ.get("DISCORD_DEFAULT_USERNAME") else "default"
        ),
        "avatar_state": "configured" if DEFAULT_AVATAR else "not_configured",
    }


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


def _summary_fields(summary):
    fields = []
    labels = (
        ("collections_processed", "Collections processed"),
        ("products_created", "Products created"),
        ("products_updated", "Products updated"),
        ("products_skipped", "Products skipped"),
        ("parent_images", "Parent images"),
        ("variation_images", "Variation images"),
        ("total_images", "Total images"),
        ("output_images_copied", "Output images copied"),
    )
    for key, label in labels:
        if key in summary and summary[key] is not None:
            fields.append({"name": label, "value": str(summary[key]), "inline": True})
    if "variations_created" in summary or "variations_updated" in summary:
        affected = int(summary.get("variations_created", 0) or 0) + int(summary.get("variations_updated", 0) or 0)
        fields.insert(min(4, len(fields)), {"name": "Variations affected", "value": str(affected), "inline": True})
    elif "variations_processed" in summary:
        fields.insert(min(4, len(fields)), {"name": "Variations processed", "value": str(summary["variations_processed"]), "inline": True})
    return fields


def _grouped_warning_text(summary):
    groups = summary.get("warning_summary") or []
    lines = []
    represented = 0
    for group in groups[:5]:
        count = max(0, int(group.get("count", 0) or 0))
        if not count:
            continue
        represented += count
        lines.append(f"{count} {group.get('category') or 'other warnings'}")
    total = max(0, int(summary.get("warnings", 0) or 0))
    if total > represented:
        lines.append(f"and {total - represented} additional warnings")
    return "\n".join(lines) or f"{total} warning(s) recorded"


def notify_scan_completed(mode, summary, elapsed_text, *, operation_id=None):
    warnings = int(summary.get("warnings", 0) or 0)
    fields = _summary_fields(summary)
    if warnings:
        fields.append({"name": "Warning summary", "value": _grouped_warning_text(summary), "inline": False})
    collections = summary.get("collection_summaries") or []
    collection_line = f"\nCollection: **{_truncate(collections[0].get('collection'))}**" if len(collections) == 1 else ""
    operation_line = f"\nOperation: `{_truncate(operation_id)}`" if operation_id else ""
    review_line = "\nReview Operations for full details." if warnings else ""
    embed = build_embed(
        "Scan Completed with Warnings" if warnings else "Scan Completed",
        f"Mode: **{mode}**\nElapsed: **{elapsed_text}**{collection_line}{operation_line}{review_line}",
        COLORS["warn"] if warnings else COLORS["success"], fields,
    )
    return send_discord_message(embeds=[embed], channels=["scans_errors" if warnings else "scans_info"])


def notify_scan_failed(mode, error_text, *, summary=None, elapsed_text=None, operation_id=None):
    summary = summary or {}
    details = [f"Mode: **{mode}**"]
    if elapsed_text:
        details.append(f"Elapsed: **{elapsed_text}**")
    if operation_id:
        details.append(f"Operation: `{_truncate(operation_id)}`")
    details.append(f"Error: `{_truncate(error_text)}`")
    embed = build_embed("Scan Failed", "\n".join(details), COLORS["error"], _summary_fields(summary))
    return send_discord_message(embeds=[embed], channels=["scans_errors"])


def notify_woo_connection_completed(summary, *, operation_id):
    limitations = max(0, int(summary.get("optional_limitations", 0) or 0))
    findings = [item for item in (summary.get("limitation_findings") or []) if isinstance(item, dict)][:5]
    fields = [
        {"name": "Store host", "value": _truncate(summary.get("hostname") or "Unavailable"), "inline": True},
        {"name": "Result", "value": "Connected with limitations" if limitations else "Connected", "inline": True},
        {"name": "WordPress REST", "value": _truncate(summary.get("wordpress_rest") or "Unavailable"), "inline": True},
        {"name": "WooCommerce REST", "value": _truncate(summary.get("woo_rest") or "Unavailable"), "inline": True},
        {"name": "Authentication", "value": _truncate(summary.get("authentication") or "Unavailable"), "inline": True},
        {"name": "API namespace", "value": _truncate(summary.get("selected_namespace") or "Unavailable"), "inline": True},
        {"name": "Required reads", "value": f"{int(summary.get('required_verified', 0) or 0)} / {int(summary.get('required_total', 0) or 0)}", "inline": True},
        {"name": "Limitations", "value": str(limitations), "inline": True},
    ]
    if findings:
        lines = []
        for item in findings:
            status = str(item.get("read_status") or "limited").replace("_", " ").title()
            code = f" ({int(item['http_status'])})" if isinstance(item.get("http_status"), int) else ""
            lines.append(f"**{_truncate(item.get('label') or 'Capability')}** — {status}{code}\n{_truncate(item.get('current_impact') or 'Review the operation for impact.')}")
        omitted = max(0, limitations - len(findings))
        if omitted:
            lines.append(f"{omitted} additional bounded limitation{'s' if omitted != 1 else ''} retained in Operations.")
        fields.append({"name": "Limitation details", "value": _truncate("\n".join(lines)), "inline": False})
    embed = build_embed(
        "WooCommerce Connection Verified with Limitations" if limitations else "WooCommerce Connection Verified",
        f"Operation: `{_truncate(operation_id)}`\nRead-only API discovery completed. No write was attempted.",
        COLORS["warn"] if limitations else COLORS["success"], fields,
    )
    return send_discord_message(embeds=[embed], channels=["scans_info"])


def notify_woo_connection_failed(summary, *, operation_id):
    fields = [
        {"name": "Category", "value": _truncate(summary.get("failure_category") or "connection_failed"), "inline": True},
        {"name": "Operation", "value": f"`{_truncate(operation_id)}`", "inline": True},
    ]
    embed = build_embed(
        "WooCommerce Connection Failed",
        f"Read-only discovery failed safely.\nReason: `{_truncate(summary.get('failure_reason') or 'Connection failed')}`",
        COLORS["error"], fields,
    )
    return send_discord_message(embeds=[embed], channels=["scans_errors"])


def notify_product_relationships_completed(summary, *, operation_id):
    """Send one bounded terminal summary without target lists or media."""

    fields = [
        {"name": "Product", "value": _truncate(summary.get("product") or "Product family"), "inline": True},
        {"name": "Cross-sells", "value": str(max(0, int(summary.get("cross_sell_count", 0) or 0))), "inline": True},
        {"name": "Upsells", "value": str(max(0, int(summary.get("upsell_count", 0) or 0))), "inline": True},
    ]
    if int(summary.get("product_count", 0) or 0) > 1:
        fields = [
            {"name": "Operation", "value": "Mutual cross-sell family", "inline": True},
            {"name": "Selected products", "value": str(int(summary.get("product_count", 0) or 0)), "inline": True},
            {"name": "New directed edges", "value": str(int(summary.get("new_relationship_count", 0) or 0)), "inline": True},
            {"name": "Existing preserved", "value": str(int(summary.get("existing_relationship_count", 0) or 0)), "inline": True},
            {"name": "Warnings", "value": str(int(summary.get("warning_count", 0) or 0)), "inline": True},
            {"name": "Duration", "value": f"{int(summary.get('duration_ms', 0) or 0)} ms", "inline": True},
        ]
    embed = build_embed(
        "Product Relationships Updated",
        f"Operation: `{_truncate(operation_id)}`\nLocal relationship data was updated. No WooCommerce request was made.",
        COLORS["success"],
        fields,
    )
    return send_discord_message(embeds=[embed], channels=["scans_info"])


def notify_intake_grouping_completed(
    *, source_name, result_name, groups, copied_images, warnings, elapsed_text, operation_id
):
    """Send one bounded terminal summary for confirmed copy-first grouping."""

    warning_count = max(0, int(warnings or 0))
    fields = [
        {"name": "Source", "value": _truncate(source_name), "inline": True},
        {"name": "Prepared result", "value": _truncate(result_name), "inline": True},
        {"name": "Groups created", "value": str(max(0, int(groups or 0))), "inline": True},
        {"name": "Images copied", "value": str(max(0, int(copied_images or 0))), "inline": True},
        {"name": "Warnings", "value": str(warning_count), "inline": True},
        {"name": "Duration", "value": _truncate(elapsed_text), "inline": True},
        {"name": "Status", "value": "Folder review required", "inline": False},
    ]
    embed = build_embed(
        "Catalogue Intake Grouping Completed with Warnings" if warning_count else "Catalogue Intake Grouping Completed",
        f"Operation: `{_truncate(operation_id)}`\nGrouped copies are provisional and require folder review.",
        COLORS["warn"] if warning_count else COLORS["success"],
        fields,
    )
    return send_discord_message(
        embeds=[embed],
        channels=["scans_errors" if warning_count else "scans_info"],
    )


def notify_intake_grouping_failed(*, source_name, error_text, operation_id):
    fields = [
        {"name": "Source", "value": _truncate(source_name), "inline": True},
        {"name": "Operation", "value": f"`{_truncate(operation_id)}`", "inline": True},
    ]
    embed = build_embed(
        "Catalogue Intake Grouping Failed",
        f"No completed Prepared result was exposed.\nError: `{_truncate(error_text)}`",
        COLORS["error"],
        fields,
    )
    return send_discord_message(embeds=[embed], channels=["scans_errors"])


def notify_intake_structured_import_completed(summary, *, operation_id):
    """Send one bounded terminal summary for a structured-folder import."""

    warning_count = max(0, int(summary.get("warnings", 0) or 0))
    mode = "Review folder structure" if summary.get("import_mode") == "review" else "Final folder structure"
    fields = [
        {"name": "Source", "value": _truncate(str(summary.get("source_relpath") or "Structured source").rsplit("/", 1)[-1]), "inline": True},
        {"name": "Prepared result", "value": _truncate(summary.get("result_name")), "inline": True},
        {"name": "Import mode", "value": mode, "inline": True},
        {"name": "Folders", "value": str(max(0, int(summary.get("folder_count", 0) or 0))), "inline": True},
        {"name": "Images", "value": str(max(0, int(summary.get("source_images", 0) or 0))), "inline": True},
        {"name": "Parent detected", "value": "Yes" if summary.get("parent_detected") else "No", "inline": True},
        {"name": "Warnings", "value": str(warning_count), "inline": True},
        {"name": "Duration", "value": f"{float(summary.get('duration_seconds', 0) or 0):.1f}s", "inline": True},
        {"name": "Next step", "value": "Review and rename folders" if summary.get("import_mode") == "review" else "Rename images", "inline": False},
    ]
    embed = build_embed(
        "Structured Folder Imported with Warnings" if warning_count else "Structured Folder Imported",
        f"Operation: `{_truncate(operation_id)}`\nThe source folder was preserved and a verified Prepared result was created.",
        COLORS["warn"] if warning_count else COLORS["success"],
        fields,
    )
    return send_discord_message(embeds=[embed], channels=["scans_errors" if warning_count else "scans_info"])


def notify_intake_structured_import_failed(source_name, error_text, *, operation_id):
    fields = [
        {"name": "Source", "value": _truncate(source_name), "inline": True},
        {"name": "Operation", "value": f"`{_truncate(operation_id)}`", "inline": True},
    ]
    embed = build_embed(
        "Structured Folder Import Failed",
        f"No unverified Prepared result was exposed.\nError: `{_truncate(error_text)}`",
        COLORS["error"],
        fields,
    )
    return send_discord_message(embeds=[embed], channels=["scans_errors"])


def notify_intake_folder_edit_completed(
    *, source_name, result_name, renamed, created, warnings, elapsed_text, operation_id
):
    """Send one bounded terminal summary for a confirmed folder-edit result."""

    warning_count = max(0, int(warnings or 0))
    fields = [
        {"name": "Source result", "value": _truncate(source_name), "inline": True},
        {"name": "Working result", "value": _truncate(result_name), "inline": True},
        {"name": "Folders renamed", "value": str(max(0, int(renamed or 0))), "inline": True},
        {"name": "Folders created", "value": str(max(0, int(created or 0))), "inline": True},
        {"name": "Warnings", "value": str(warning_count), "inline": True},
        {"name": "Duration", "value": _truncate(elapsed_text), "inline": True},
        {"name": "Next step", "value": "Rename images using final folder names", "inline": False},
    ]
    embed = build_embed(
        "Catalogue Intake Folder Structure Completed with Warnings" if warning_count else "Catalogue Intake Folder Structure Completed",
        f"Operation: `{_truncate(operation_id)}`\nThe same Prepared working result was safely updated after rollback-protected verification.",
        COLORS["warn"] if warning_count else COLORS["success"],
        fields,
    )
    return send_discord_message(embeds=[embed], channels=["scans_errors" if warning_count else "scans_info"])


def notify_intake_folder_edit_failed(*, source_name, error_text, operation_id):
    fields = [
        {"name": "Source result", "value": _truncate(source_name), "inline": True},
        {"name": "Operation", "value": f"`{_truncate(operation_id)}`", "inline": True},
    ]
    embed = build_embed(
        "Catalogue Intake Folder Structure Failed",
        f"No folder-edited Prepared result was exposed.\nError: `{_truncate(error_text)}`",
        COLORS["error"],
        fields,
    )
    return send_discord_message(embeds=[embed], channels=["scans_errors"])


def notify_intake_image_rename_completed(
    *, result_name, prefix, renamed, parent, variation, warnings,
    predecessor, elapsed_text, operation_id
):
    """Send one bounded terminal image-renaming summary."""

    warning_count = max(0, int(warnings or 0))
    fields = [
        {"name": "Working result", "value": _truncate(result_name), "inline": True},
        {"name": "Prefix", "value": _truncate(prefix), "inline": True},
        {"name": "Images renamed", "value": str(max(0, int(renamed or 0))), "inline": True},
        {"name": "Parent images", "value": str(max(0, int(parent or 0))), "inline": True},
        {"name": "Variation images", "value": str(max(0, int(variation or 0))), "inline": True},
        {"name": "Warnings", "value": str(warning_count), "inline": True},
        {"name": "Predecessor", "value": _truncate(predecessor or "preserved"), "inline": True},
        {"name": "Duration", "value": _truncate(elapsed_text), "inline": True},
        {"name": "Next step", "value": "Create product metadata", "inline": False},
    ]
    embed = build_embed(
        "Catalogue Intake Image Renaming Completed with Warnings" if warning_count else "Catalogue Intake Image Renaming Completed",
        f"Operation: `{_truncate(operation_id)}`\nImages were renamed in the same rollback-protected Prepared working result.",
        COLORS["warn"] if warning_count else COLORS["success"],
        fields,
    )
    return send_discord_message(
        embeds=[embed],
        channels=["scans_errors" if warning_count else "scans_info"],
    )


def notify_intake_image_rename_failed(*, result_name, error_text, operation_id):
    fields = [
        {"name": "Working result", "value": _truncate(result_name), "inline": True},
        {"name": "Operation", "value": f"`{_truncate(operation_id)}`", "inline": True},
    ]
    embed = build_embed(
        "Catalogue Intake Image Renaming Failed",
        f"No partial Prepared result was exposed.\nError: `{_truncate(error_text)}`",
        COLORS["error"],
        fields,
    )
    return send_discord_message(embeds=[embed], channels=["scans_errors"])


def notify_intake_metadata_completed(summary, *, operation_id):
    """Send one bounded terminal Prepared metadata summary."""

    warning_count = max(0, int(summary.get("warnings", 0) or 0))
    fields = [
        {"name": "Prepared result", "value": _truncate(summary.get("result_name")), "inline": True},
        {"name": "Action", "value": _truncate(summary.get("metadata_action")), "inline": True},
        {"name": "Collection type", "value": _truncate(summary.get("collection_type")), "inline": True},
        {"name": "SKU prefix", "value": _truncate(summary.get("sku_prefix")), "inline": True},
        {"name": "Publishing intent", "value": _truncate(summary.get("publishing_intent")), "inline": True},
        {"name": "Attributes", "value": str(max(0, int(summary.get("attribute_count", 0) or 0))), "inline": True},
        {"name": "Warnings", "value": str(warning_count), "inline": True},
        {"name": "Duration", "value": f"{float(summary.get('duration_seconds', 0) or 0):.1f}s", "inline": True},
        {"name": "Next step", "value": "Validate prepared collection", "inline": False},
    ]
    embed = build_embed(
        "Catalogue Intake Metadata Completed with Warnings" if warning_count else "Catalogue Intake Metadata Completed",
        f"Operation: `{_truncate(operation_id)}`\nThe same Prepared result was safely updated and remains outside Catalogue.",
        COLORS["warn"] if warning_count else COLORS["success"],
        fields,
    )
    return send_discord_message(embeds=[embed], channels=["scans_errors" if warning_count else "scans_info"])


def notify_intake_metadata_failed(result_name, error_text, *, operation_id):
    fields = [
        {"name": "Prepared result", "value": _truncate(result_name), "inline": True},
        {"name": "Operation", "value": f"`{_truncate(operation_id)}`", "inline": True},
    ]
    embed = build_embed(
        "Catalogue Intake Metadata Failed",
        f"No partial Prepared result was exposed.\nError: `{_truncate(error_text)}`",
        COLORS["error"],
        fields,
    )
    return send_discord_message(embeds=[embed], channels=["scans_errors"])


def notify_intake_handoff_completed(summary, *, operation_id):
    """Send one bounded terminal catalogue handoff summary."""

    warning_count = max(0, int(summary.get("warnings", 0) or 0))
    fields = [
        {"name": "Prepared result", "value": _truncate(summary.get("result_name")), "inline": True},
        {"name": "Catalogue destination", "value": _truncate(summary.get("catalogue_destination")), "inline": True},
        {"name": "Action", "value": _truncate(summary.get("handoff_action")), "inline": True},
        {"name": "Collection type", "value": _truncate(summary.get("collection_type")), "inline": True},
        {"name": "Products / variations", "value": f"{int(summary.get('product_count', 0) or 0)} / {int(summary.get('variation_count', 0) or 0)}", "inline": True},
        {"name": "Images", "value": str(max(0, int(summary.get("total_images", 0) or 0))), "inline": True},
        {"name": "Exact / fallback / missing", "value": f"{int(summary.get('exact_image_variations', 0) or 0)} / {int(summary.get('fallback_image_variations', 0) or 0)} / {int(summary.get('missing_image_variations', 0) or 0)}", "inline": True},
        {"name": "Warnings", "value": str(warning_count), "inline": True},
        {"name": "Duration", "value": f"{float(summary.get('duration_seconds', 0) or 0):.1f}s", "inline": True},
        {"name": "Next step", "value": "Run Append Scan", "inline": False},
    ]
    embed = build_embed(
        "Catalogue Handoff Completed with Warnings" if warning_count else "Catalogue Handoff Completed",
        f"Operation: `{_truncate(operation_id)}`\nThe verified Prepared collection was copied safely. No scan was started.",
        COLORS["warn"] if warning_count else COLORS["success"],
        fields,
    )
    return send_discord_message(embeds=[embed], channels=["scans_errors" if warning_count else "scans_info"])


def notify_intake_handoff_failed(result_name, error_text, *, operation_id):
    fields = [
        {"name": "Prepared result", "value": _truncate(result_name), "inline": True},
        {"name": "Operation", "value": f"`{_truncate(operation_id)}`", "inline": True},
    ]
    embed = build_embed(
        "Catalogue Handoff Failed",
        f"No unverified partial destination was retained.\nError: `{_truncate(error_text)}`",
        COLORS["error"],
        fields,
    )
    return send_discord_message(embeds=[embed], channels=["scans_errors"])


def notify_editor_saved(kind, sku, path=None, *, collection=None, affected=None):
    fields = [{"name": "Kind", "value": kind, "inline": True}, {"name": "SKU", "value": sku, "inline": True}]
    if collection:
        fields.append({"name": "Collection", "value": collection, "inline": True})
    if affected is not None:
        fields.append({"name": "Affected products", "value": str(affected), "inline": True})
    embed = build_embed("Metadata Updated", "Catalogue metadata was saved successfully.", COLORS["info"], fields)
    return send_discord_message(embeds=[embed], channels=["edits"])


def notify_woo_publish_preview_completed(summary, *, operation_id):
    """Send one bounded, secret-free terminal preview summary."""
    counts = summary.get("product_counts") or {}
    fields = [
        {"name": "Scope", "value": _truncate((summary.get("scope") or {}).get("kind")), "inline": True},
        {"name": "Create / update / unchanged", "value": f"{int(counts.get('create', 0))} / {int(counts.get('update', 0))} / {int(counts.get('no_change', 0))}", "inline": True},
        {"name": "Blocked / recovery", "value": f"{int(counts.get('blocked', 0))} / {int(counts.get('recovery_required', 0))}", "inline": True},
        {"name": "Pending Pass 2", "value": str(int((summary.get("relationship_counts") or {}).get("pending_pass_2", 0))), "inline": True},
        {"name": "Taxonomy dependencies", "value": str(int((summary.get("taxonomy_counts") or {}).get("create_required", 0))), "inline": True},
        {"name": "Media dependencies", "value": str(int((summary.get("media_counts") or {}).get("missing_url", 0))), "inline": True},
        {"name": "Warnings / blockers", "value": f"{int(summary.get('warning_count', 0))} / {int(summary.get('blocker_count', 0))}", "inline": True},
        {"name": "Readiness", "value": _truncate(summary.get("readiness")), "inline": True},
        {"name": "Duration", "value": f"{int(summary.get('duration_ms', 0))} ms", "inline": True},
    ]
    embed = build_embed(
        "WooCommerce Publish Preview Completed",
        f"Operation: `{_truncate(operation_id)}`\nRead-only plan generated. No WooCommerce write was sent.",
        COLORS["warn"] if summary.get("blocker_count") or summary.get("warning_count") else COLORS["success"],
        fields,
    )
    return send_discord_message(embeds=[embed], channels=["scans_errors" if summary.get("blocker_count") else "scans_info"])


def notify_woo_publish_completed(summary, *, operation_id):
    """Send one bounded terminal summary for a controlled Woo mutation."""

    fields = [
        {"name": "Selected", "value": str(int(summary.get("selected_products", 0))), "inline": True},
        {"name": "Created / updated", "value": f"{int(summary.get('created', 0))} / {int(summary.get('updated', 0))}", "inline": True},
        {"name": "Verified", "value": str(int(summary.get("verified_products", 0))), "inline": True},
        {"name": "Variations", "value": str(int((summary.get("counts") or {}).get("variations_verified", 0))), "inline": True},
        {"name": "Taxonomy created", "value": str(int((summary.get("taxonomy") or {}).get("created", 0))), "inline": True},
        {"name": "Relationships", "value": str(int((summary.get("counts") or {}).get("relationships_applied", 0))), "inline": True},
        {"name": "Pending relationships", "value": str(int(summary.get("pending_relationship_count", 0))), "inline": True},
        {"name": "Failures / recovery", "value": f"{int(summary.get('failed_products', 0))} / {int(bool(summary.get('recovery_required')))}", "inline": True},
        {"name": "Duration", "value": f"{int(summary.get('duration_ms', 0))} ms", "inline": True},
    ]
    has_attention = bool(summary.get("failed_products") or summary.get("recovery_required") or summary.get("pending_relationship_count"))
    embed = build_embed(
        "WooCommerce Controlled Publish Completed with Attention" if has_attention else "WooCommerce Controlled Publish Completed",
        f"Operation: `{_truncate(operation_id)}`\nVerified two-pass publication finished. Review the operation before any retry.",
        COLORS["warn"] if has_attention else COLORS["success"],
        fields,
    )
    return send_discord_message(embeds=[embed], channels=["scans_errors" if has_attention else "scans_info"])


def notify_override_created(sku, path=None, *, product=None, collection=None):
    fields = [{"name": "SKU", "value": sku, "inline": True}]
    if product:
        fields.append({"name": "Product", "value": product, "inline": True})
    if collection:
        fields.append({"name": "Collection", "value": collection, "inline": True})
    embed = build_embed("Product Override Saved", "One product override was saved.", COLORS["info"], fields)
    return send_discord_message(embeds=[embed], channels=["overrides"])


def notify_override_removed(sku, path=None, *, product=None, collection=None):
    fields = [{"name": "SKU", "value": sku, "inline": True}]
    if product:
        fields.append({"name": "Product", "value": product, "inline": True})
    if collection:
        fields.append({"name": "Collection", "value": collection, "inline": True})
    embed = build_embed("Product Override Removed", "One product override was removed.", COLORS["warn"], fields)
    return send_discord_message(embeds=[embed], channels=["overrides"])


def notify_ingest_product(
    *, sku, name, product_type, has_shared, has_override, folder_path,
    images_count=None, parent_images_count=None, variation_images_count=None,
    total_images_count=None, output_images_copied=None, variations_count=None,
):
    if total_images_count is None and images_count is not None:
        total_images_count = images_count
    fields = [
        {"name": "SKU", "value": f"`{sku}`", "inline": True},
        {"name": "Type", "value": product_type or "-", "inline": True},
        {"name": "Shared JSON", "value": "✅" if has_shared else "—", "inline": True},
        {"name": "Override JSON", "value": "✅" if has_override else "—", "inline": True},
    ]
    for label, value in (
        ("Parent images", parent_images_count),
        ("Variation images", variation_images_count),
        ("Total images", total_images_count),
        ("Output images copied", output_images_copied),
    ):
        if value is not None:
            fields.append({"name": label, "value": str(value), "inline": True})
    if variations_count is not None:
        fields.append({"name": "Variations", "value": str(variations_count), "inline": True})
    description = f"**Source:** `{_truncate(folder_path)}`" if folder_path else ""
    embed = build_embed(f"Ingested Product — {name or sku}", description, COLORS["success"] if total_images_count else COLORS["warn"], fields)
    return send_discord_message(embeds=[embed], channels=["ingest"])
