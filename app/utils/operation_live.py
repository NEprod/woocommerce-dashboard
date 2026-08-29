"""Bounded persisted scanner progress and logs shared by every web worker."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app import db
from app.models import CatalogueOperation
from app.utils.redaction import redact_diagnostic


LIVE_STATE_KEY = "live_state"
PERSISTED_LOG_LINE_LIMIT = 500
PERSISTED_LOG_BYTE_LIMIT = 256 * 1024
LIVE_SCHEMA_VERSION = 1


def utcnow_iso():
    return datetime.now(UTC).isoformat()


def scope_dict(row):
    try:
        value = json.loads(row.scope or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def persisted_live_state(row):
    value = scope_dict(row).get(LIVE_STATE_KEY)
    return value if isinstance(value, dict) else None


def _bounded_logs(entries):
    bounded = []
    retained_bytes = 0
    for entry in reversed(entries):
        text = redact_diagnostic(entry.get("line", ""), limit=4000)
        size = len(text.encode("utf-8"))
        if bounded and (
            len(bounded) >= PERSISTED_LOG_LINE_LIMIT
            or retained_bytes + size > PERSISTED_LOG_BYTE_LIMIT
        ):
            break
        bounded.append({
            "sequence": max(1, int(entry.get("sequence", 1))),
            "severity": entry.get("severity") if entry.get("severity") in {"info", "warning", "error"} else "info",
            "line": text,
        })
        retained_bytes += size
    return list(reversed(bounded))


def persist_live_state(operation_id, state, log_entries=()):
    """Replace one operation's bounded live snapshot in a short transaction."""

    row = db.session.get(CatalogueOperation, operation_id)
    if row is None:
        return False
    scope = scope_dict(row)
    logs = _bounded_logs(log_entries)
    safe = {
        "version": LIVE_SCHEMA_VERSION,
        "stage": str(state.get("stage") or "queued")[:80],
        "current_item": redact_diagnostic(state.get("current_item") or "", limit=240),
        "latest_message": redact_diagnostic(state.get("latest_message") or "", limit=500),
        "status": str(state.get("status") or "running")[:32],
        "progress": state.get("progress") if isinstance(state.get("progress"), dict) else {},
        "counts": state.get("counts") if isinstance(state.get("counts"), dict) else {},
        "summary": state.get("summary") if isinstance(state.get("summary"), dict) else {},
        "discord": state.get("discord") if isinstance(state.get("discord"), dict) else {},
        "heartbeat_at": state.get("heartbeat_at") or utcnow_iso(),
        "updated_at": utcnow_iso(),
        "logs": logs,
        "oldest_sequence": logs[0]["sequence"] if logs else int(state.get("next_sequence", 1)),
        "next_sequence": int(state.get("next_sequence", 1)),
        "retention": {
            "max_lines": PERSISTED_LOG_LINE_LIMIT,
            "max_bytes": PERSISTED_LOG_BYTE_LIMIT,
        },
    }
    scope[LIVE_STATE_KEY] = safe
    row.scope = json.dumps(scope, ensure_ascii=False, separators=(",", ":"))
    db.session.commit()
    return True


def persisted_log_page(row, *, page=1, per_page=50, severity="", search="", after=None):
    live = persisted_live_state(row) or {}
    entries = live.get("logs") if isinstance(live.get("logs"), list) else []
    entries = [entry for entry in entries if isinstance(entry, dict)]
    oldest = int(live.get("oldest_sequence") or (entries[0].get("sequence") if entries else 1))
    next_sequence = int(live.get("next_sequence") or (entries[-1].get("sequence", 0) + 1 if entries else oldest))
    if after is not None:
        cursor = max(0, int(after))
        gap = bool(entries and cursor and cursor < oldest - 1)
        if cursor > max(oldest - 1, next_sequence - 1):
            cursor = oldest - 1
            gap = bool(entries)
        selected = [entry for entry in entries if int(entry.get("sequence", 0)) > cursor][:100]
        next_cursor = int(selected[-1]["sequence"]) if selected else max(cursor, oldest - 1)
        return {
            "entries": selected,
            "gap": gap,
            "oldest_sequence": oldest,
            "next_cursor": next_cursor,
            "retained": bool(entries),
            "persisted": True,
            "terminal": row.status in {"succeeded", "partial", "failed", "interrupted"},
        }
    marker = {"info": "info", "warning": "warning", "error": "error"}.get(severity)
    needle = (search or "").casefold()[:100]
    filtered = [
        entry for entry in entries
        if (not marker or entry.get("severity") == marker)
        and (not needle or needle in str(entry.get("line", "")).casefold())
    ]
    page = max(1, int(page))
    per_page = min(100, max(1, int(per_page)))
    start = (page - 1) * per_page
    return {
        "items": [entry.get("line", "") for entry in filtered[start:start + per_page]],
        "page": page,
        "per_page": per_page,
        "total": len(filtered),
        "pages": max(1, (len(filtered) + per_page - 1) // per_page),
        "retained": bool(entries),
        "persisted": True,
    }
