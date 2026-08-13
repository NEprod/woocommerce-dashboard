# app/utils/discorp.py
import os, json, requests
from datetime import datetime

DEFAULT_TIMEOUT = 10

COLORS = {
    "success": 0x2ECC71,
    "info": 0x3498DB,
    "warn": 0xF1C40F,
    "error": 0xE74C3C,
}


def _env(name, default=""):
    return os.environ.get(name) or default


# Multi-webhook config (set in .env)
WEBHOOKS = {
    "scans_info": _env("DISCORD_WEBHOOK_SCANS_INFO"),
    "scans_errors": _env("DISCORD_WEBHOOK_SCANS_ERRORS"),
    "edits": _env("DISCORD_WEBHOOK_EDITS"),
    "overrides": _env("DISCORD_WEBHOOK_OVERRIDES"),
    # NEW: per-product ingest channel
    "ingest": _env("DISCORD_WEBHOOK_INGEST"),
}

ENABLED = _env("DISCORD_ENABLED", "true").lower() == "true"
DEFAULT_USERNAME = _env("DISCORD_DEFAULT_USERNAME", "TLC Bot")
DEFAULT_AVATAR = _env("DISCORD_DEFAULT_AVATAR_URL", "")

MAX_FIELD_CHARS = int(_env("DISCORD_MAX_FIELD_CHARS", "950"))
MAX_EMBED_FIELDS = int(_env("DISCORD_MAX_EMBED_FIELDS", "10"))


def _post(webhook_url: str, payload: dict, timeout=DEFAULT_TIMEOUT):
    if not ENABLED or not webhook_url:
        return False, "disabled or no webhook"
    try:
        resp = requests.post(
            webhook_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        if 200 <= resp.status_code < 300:
            return True, "ok"
        return False, f"{resp.status_code}: {resp.text}"
    except Exception as e:
        return False, str(e)


def send_discord_message(
    content: str = "", *, embeds=None, channels=None, username=None, avatar_url=None
):
    """Backwards-compatible sender + multi-channel routing."""
    if not ENABLED:
        return False, "disabled"
    if channels is None:
        channels = ["scans_info"]
    payload = {
        "content": content[:2000] if content else None,
        "username": username or DEFAULT_USERNAME,
        "avatar_url": avatar_url or DEFAULT_AVATAR,
        "embeds": embeds or [],
    }
    payload = {k: v for k, v in payload.items() if v not in (None, "", [], {})}
    ok_all = True
    last_err = "ok"
    for ch in channels:
        url = WEBHOOKS.get(ch)
        ok, err = _post(url, payload)
        ok_all = ok_all and ok
        last_err = err
    return ok_all, last_err


def _truncate(v: str) -> str:
    v = v or ""
    return v if len(v) <= MAX_FIELD_CHARS else v[: MAX_FIELD_CHARS - 3] + "..."


def build_embed(
    title: str,
    description: str,
    color: int,
    fields: list[dict] = None,
    footer="WooCommerce Dashboard",
):
    return {
        "title": title,
        "description": _truncate(description),
        "color": color,
        "fields": (fields or [])[:MAX_EMBED_FIELDS],
        "footer": {
            "text": f"{footer} • {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
        },
    }


# Convenience wrappers
def notify_scan_started(mode: str, collection_count: int):
    emb = build_embed(
        "Scan Started",
        f"Mode: **{mode}**\nCollections discovered: **{collection_count}**",
        COLORS["info"],
    )
    return send_discord_message(embeds=[emb], channels=["scans_info"])


def notify_scan_completed(mode: str, summary: dict, elapsed_text: str):
    fields = []
    for k in (
        "folders",
        "new_rows",
        "products_created",
        "products_updated",
        "variations_created",
        "variations_updated",
    ):
        if k in summary:
            fields.append(
                {
                    "name": k.replace("_", " ").title(),
                    "value": str(summary[k]),
                    "inline": True,
                }
            )
    emb = build_embed(
        "Scan Completed",
        f"Mode: **{mode}**\nElapsed: **{elapsed_text}**",
        COLORS["success"],
        fields=fields,
    )
    return send_discord_message(embeds=[emb], channels=["scans_info"])


def notify_scan_failed(mode: str, error_text: str):
    emb = build_embed(
        "Scan Failed",
        f"Mode: **{mode}**\nError: `{_truncate(error_text)}`",
        COLORS["error"],
    )
    return send_discord_message(embeds=[emb], channels=["scans_errors"])


def notify_editor_saved(kind: str, sku: str, path: str):
    emb = build_embed(
        "Editor Saved",
        f"Kind: **{kind}**\nSKU: **{sku}**\nPath: `{path}`",
        COLORS["info"],
    )
    return send_discord_message(embeds=[emb], channels=["edits"])


def notify_override_created(sku: str, path: str):
    emb = build_embed(
        "Override Created",
        f"SKU: **{sku}**\nPath: `{path}`",
        COLORS["info"],
    )
    return send_discord_message(embeds=[emb], channels=["overrides", "edits"])


def notify_override_removed(sku: str, path: str):
    emb = build_embed(
        "Override Removed",
        f"SKU: **{sku}**\nRemoved: `{path}`",
        COLORS["warn"],
    )
    return send_discord_message(embeds=[emb], channels=["overrides", "edits"])


# NEW: per-product ingest notification
def notify_ingest_product(
    *,
    sku: str,
    name: str | None,
    product_type: str,  # 'simple' | 'variable'
    images_count: int,
    has_shared: bool,
    has_override: bool,
    folder_path: str | None,
    variations_count: int | None = None,
):
    fields = [
        {"name": "SKU", "value": f"`{sku}`", "inline": True},
        {"name": "Type", "value": product_type or "-", "inline": True},
        {"name": "Images", "value": str(images_count), "inline": True},
        {"name": "Shared JSON", "value": "✅" if has_shared else "—", "inline": True},
        {
            "name": "Override JSON",
            "value": "✅" if has_override else "—",
            "inline": True,
        },
    ]
    if variations_count is not None:
        fields.append(
            {"name": "Variations", "value": str(variations_count), "inline": True}
        )

    desc = f"**Path:** `{folder_path}`" if folder_path else ""
    emb = build_embed(
        f"Ingested Product — {name or sku}",
        desc,
        COLORS["success"] if images_count else COLORS["warn"],
        fields=fields,
    )
    return send_discord_message(embeds=[emb], channels=["ingest"])
