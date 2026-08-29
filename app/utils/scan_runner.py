# app/utils/scan_runner.py
import os
import time
import threading
from collections import deque
from dataclasses import dataclass
from flask import url_for
from datetime import datetime
from itertools import count
from queue import Empty

from app.models import Settings
from app.utils.json_utils import load_json, validate_json
from app.utils.scanner import scan_collection
from app.utils.discord import (  # UPDATED import
    notify_scan_started,
    notify_scan_completed,
    notify_scan_failed,
)
from app.utils.ingest import ingest_rows_to_db
from app.utils.marker_recovery import (
    finalize_ingested_markers,
    mark_pending_database_recovery,
    recover_committed_markers,
)
from app.utils.operation_control import (
    acquire_catalogue_operation,
    finish_catalogue_operation,
    register_operation_reference,
    unregister_operation_reference,
)
from app.utils.operation_live import persist_live_state, utcnow_iso
from app.utils.redaction import redact_diagnostic, runtime_redaction_paths
from app.utils.reconciliation import (
    authoritative_scope,
    reconcile_authoritative_products,
)

# In-memory progress store
_runs = {}
_runs_lock = threading.RLock()
_run_sequence = count()
COMPLETED_RUN_LIMIT = 20
LOG_LINE_LIMIT = 2000
LOG_BYTE_LIMIT = 2 * 1024 * 1024
LOG_TRUNCATION_MARKER = "[⚠️] Earlier log output was truncated to retain the newest entries."
WARNING_SAMPLE_LIMIT = 20
COLLECTION_SUMMARY_LIMIT = 25
LIVE_HEARTBEAT_SECONDS = 5


def _row_images(row):
    return [value.strip() for value in str(row.get("Images") or "").split(",") if value.strip()]


def image_ownership_summary(rows):
    """Count unique emitted image ownership records without counting fallbacks twice."""

    parent_refs = []
    variation_refs = []
    seen = set()
    for row in rows:
        target = variation_refs if row.get("Type") == "variation" else parent_refs
        for reference in _row_images(row):
            identity = reference.casefold()
            if identity in seen:
                continue
            seen.add(identity)
            target.append(reference)
    parent_count = len(parent_refs)
    variation_count = len(variation_refs)
    total = parent_count + variation_count
    return {
        "parent_images": parent_count,
        "variation_images": variation_count,
        "total_images": total,
        # Emitted image references originate only from successful process_images calls.
        "output_images_copied": total,
    }


def _warning_category(message):
    lowered = message.casefold()
    categories = (
        (("missing source image", "no source image", "image missing"), "missing source images"),
        (("url-only", "url only"), "URL-only images"),
        (("source-only", "source only"), "source-only images"),
        (("corrupt", "error processing image", "invalid image"), "corrupt images"),
        (("metadata", "product_info", "json"), "malformed metadata"),
        (("skipped", "skip "), "skipped products"),
        (("recovery", "recover"), "marker recovery"),
        (("output", "copy"), "output-copy warnings"),
    )
    for needles, label in categories:
        if any(needle in lowered for needle in needles):
            return label
    return "other warnings"


def warning_summary(run):
    lines = [
        line for line in run["queue"].snapshot()
        if "[⚠️]" in line and LOG_TRUNCATION_MARKER not in line
    ]
    grouped = {}
    entries = []
    for line in lines:
        safe = redact_diagnostic(line, paths=run.get("redaction_paths"), limit=240)
        message = safe.split("[⚠️]", 1)[-1].strip()
        category = _warning_category(message)
        bucket = grouped.setdefault(category, {"category": category, "count": 0, "samples": []})
        bucket["count"] += 1
        if len(bucket["samples"]) < 2:
            bucket["samples"].append(message[:160])
        if len(entries) < WARNING_SAMPLE_LIMIT:
            entries.append(message)
    return {
        "warning_summary": sorted(grouped.values(), key=lambda item: (-item["count"], item["category"])),
        "warning_entries": entries,
    }


def _persistable_operation_summary(summary):
    allowed = {
        "collections_processed", "products_created", "products_updated", "products_failed",
        "products_skipped", "variations_created", "variations_updated", "variations_processed",
        "parent_images", "variation_images", "total_images", "output_images_copied",
        "warnings", "warning_summary", "warning_entries", "collection_summaries",
    }
    return {key: summary[key] for key in allowed if key in summary}


class BoundedLogQueue:
    """A Queue-compatible newest-first bounded transport for SSE log lines."""

    def __init__(self, *, max_lines=LOG_LINE_LIMIT, max_bytes=LOG_BYTE_LIMIT):
        self.max_lines = max_lines
        self.max_bytes = max_bytes
        self._items = deque()
        self._bytes = 0
        self._history = deque()
        self._history_bytes = 0
        self._sequence_history = deque()
        self._next_sequence = 1
        self._condition = threading.Condition()

    @staticmethod
    def _size(value):
        return len(str(value).encode("utf-8"))

    def put(self, value):
        item = str(value)
        marker_size = self._size(LOG_TRUNCATION_MARKER)
        if self._size(item) + marker_size > self.max_bytes:
            allowance = max(0, self.max_bytes - marker_size)
            item = item.encode("utf-8")[-allowance:].decode("utf-8", errors="ignore")
        with self._condition:
            if self._items and self._items[0] == LOG_TRUNCATION_MARKER:
                self._items.popleft()
                self._bytes -= marker_size
            self._items.append(item)
            self._bytes += self._size(item)
            truncated = False
            while self._items and (
                len(self._items) > max(1, self.max_lines - 1)
                or self._bytes + marker_size > self.max_bytes
            ):
                removed = self._items.popleft()
                self._bytes -= self._size(removed)
                truncated = True
            if truncated:
                self._items.appendleft(LOG_TRUNCATION_MARKER)
                self._bytes += marker_size
            if self._history and self._history[0] == LOG_TRUNCATION_MARKER:
                self._history.popleft()
                self._history_bytes -= marker_size
            self._history.append(item)
            sequence = self._next_sequence
            self._next_sequence += 1
            self._sequence_history.append((sequence, item))
            self._history_bytes += self._size(item)
            history_truncated = False
            while self._history and (
                len(self._history) > max(1, self.max_lines - 1)
                or self._history_bytes + marker_size > self.max_bytes
            ):
                removed = self._history.popleft()
                self._history_bytes -= self._size(removed)
                if self._sequence_history:
                    self._sequence_history.popleft()
                history_truncated = True
            if history_truncated:
                self._history.appendleft(LOG_TRUNCATION_MARKER)
                self._history_bytes += marker_size
            self._condition.notify()

    def get(self, timeout=None):
        with self._condition:
            if timeout is None:
                while not self._items:
                    self._condition.wait()
            else:
                deadline = time.monotonic() + timeout
                while not self._items:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise Empty
                    self._condition.wait(remaining)
            item = self._items.popleft()
            self._bytes -= self._size(item)
            return item

    def get_nowait(self):
        return self.get(timeout=0)

    def empty(self):
        with self._condition:
            return not self._items

    def qsize(self):
        with self._condition:
            return len(self._items)

    def snapshot(self):
        """Return the bounded chronological history without draining SSE output."""
        with self._condition:
            return list(self._history)

    def sequenced_snapshot(self):
        with self._condition:
            entries = []
            for sequence, line in self._sequence_history:
                severity = "warning" if "[⚠️]" in line else "error" if "[❌]" in line else "info"
                entries.append({"sequence": sequence, "severity": severity, "line": line})
            return entries, self._next_sequence


def _run_is_protected(run):
    if run.get("status") not in {"done", "error"}:
        return True
    return run.get("recovery_state") not in (None, "none")


def _prune_completed_runs():
    removed_operation_ids = []
    with _runs_lock:
        completed = [
            (run_id, run)
            for run_id, run in _runs.items()
            if not _run_is_protected(run)
        ]
        completed.sort(key=lambda item: (item[1].get("sequence", 0), item[0]))
        for run_id, run in completed[:-COMPLETED_RUN_LIMIT]:
            _runs.pop(run_id, None)
            if run.get("operation_id"):
                removed_operation_ids.append(run["operation_id"])
    for operation_id in removed_operation_ids:
        unregister_operation_reference(operation_id)
    return len(removed_operation_ids)


@dataclass(frozen=True)
class ScanScopePlan:
    collection_paths: tuple[str, ...]
    seen_source_relpaths: frozenset[str]
    expected_parent_counts: dict[str, int]
    authoritative: bool
    complete: bool
    collection_relpath: str | None = None
    error: str | None = None


def _safe_collection_path(scan_folder, collection_relpath):
    root = os.path.realpath(scan_folder)
    candidate = os.path.realpath(os.path.join(root, collection_relpath))
    try:
        if os.path.commonpath([root, candidate]) != root:
            return None
    except ValueError:
        return None
    return candidate


def build_scan_scope(scan_folder, scan_mode, *, collection_relpath=None):
    """Preflight an exhaustive scan without changing scanner selection behavior."""

    authoritative = scan_mode in {"full", "shared_collection"}
    if not os.path.isdir(scan_folder):
        return ScanScopePlan(
            (),
            frozenset(),
            {},
            False,
            False,
            error="catalogue root unavailable",
        )

    try:
        if scan_mode == "shared_collection":
            target = _safe_collection_path(scan_folder, collection_relpath or "")
            if not target or not os.path.isdir(target):
                return ScanScopePlan(
                    (),
                    frozenset(),
                    {},
                    False,
                    False,
                    collection_relpath,
                    "collection unavailable",
                )
            collection_paths = (target,)
        else:
            collection_paths = tuple(
                os.path.join(scan_folder, name)
                for name in sorted(os.listdir(scan_folder))
                if os.path.isdir(os.path.join(scan_folder, name))
                and not name.startswith("_")
            )
    except OSError as error:
        return ScanScopePlan(
            (), frozenset(), {}, False, False, collection_relpath, str(error)
        )

    if not authoritative:
        return ScanScopePlan(collection_paths, frozenset(), {}, False, True)
    if scan_mode == "full" and not collection_paths:
        return ScanScopePlan(
            (),
            frozenset(),
            {},
            False,
            False,
            error="catalogue has no resolvable collections",
        )

    seen = set()
    expected = {}
    try:
        for path in collection_paths:
            shared = validate_json(
                load_json(os.path.join(path, "product_info.json")),
                is_collection=True,
            )
            relative = os.path.relpath(path, scan_folder).replace(os.sep, "/")
            collection_type = shared.get("collection_type")
            if collection_type == "Single Variable":
                sources = {relative}
            elif collection_type in {"Simple", "Variable Collection"}:
                sources = {
                    f"{relative}/{name}"
                    for name in sorted(os.listdir(path))
                    if os.path.isdir(os.path.join(path, name))
                    and not name.startswith(".")
                }
            else:
                return ScanScopePlan(
                    collection_paths,
                    frozenset(),
                    {},
                    False,
                    False,
                    collection_relpath,
                    "collection type is not exhaustive",
                )
            seen.update(sources)
            expected[path] = len(sources)
    except (OSError, ValueError, TypeError, KeyError) as error:
        return ScanScopePlan(
            collection_paths,
            frozenset(),
            {},
            False,
            False,
            collection_relpath,
            str(error),
        )

    return ScanScopePlan(
        collection_paths,
        frozenset(seen),
        expected,
        True,
        True,
        collection_relpath,
    )


def make_logger(run_id):
    with _runs_lock:
        q = _runs[run_id]["queue"]

    def log(msg, level="INFO"):
        normalized_level = level.upper()
        with _runs_lock:
            run = _runs[run_id]
            if normalized_level == "WARN":
                run["warnings"] = run.get("warnings", 0) + 1
            elif normalized_level == "ERROR":
                run["errors"] = run.get("errors", 0) + 1
            paths = run.get("redaction_paths")
        prefix = {"INFO": "[ℹ️]", "WARN": "[⚠️]", "ERROR": "[❌]"}.get(
            normalized_level, "[ℹ️]"
        )
        safe_message = redact_diagnostic(msg, paths=paths)
        line = f"{time.strftime('%H:%M:%S')} {prefix} {safe_message}"
        q.put(line)

    return log


def _operation_type_for_scan(scan_mode):
    if scan_mode == "full":
        return "full"
    if scan_mode == "update":
        return "product_update"
    if scan_mode == "shared_collection":
        return "shared_collection_update"
    return "append"


def start_scan(
    app,
    run_id,
    scan_mode="append",
    *,
    operation_id=None,
    operation_type=None,
    scope=None,
    collection_relpath=None,
):
    operation_scope = dict(scope or {"scan_mode": scan_mode})
    if scan_mode == "full":
        operation_scope.setdefault("scope_kind", "catalogue")
        operation_scope.setdefault("exhaustive", True)
    elif scan_mode == "shared_collection":
        operation_scope.setdefault("scope_kind", "collection")
        operation_scope.setdefault("collection_relpath", collection_relpath)
        operation_scope.setdefault("exhaustive", True)
    if operation_id is None:
        with app.app_context():
            lease = acquire_catalogue_operation(
                operation_type or _operation_type_for_scan(scan_mode),
                operation_scope,
            )
        operation_id = lease.id

    with _runs_lock:
        persist_stop = threading.Event()
        _runs[run_id] = {
            "total": 0,
            "done": 0,
            "status": "running",
            "queue": BoundedLogQueue(),
            "summary": {
                "new_rows": 0,
                "folders": 0,
                "started_at": datetime.utcnow().isoformat(),
                "finished_at": None,
            },
            "operation_id": operation_id,
            "operation_type": operation_type or _operation_type_for_scan(scan_mode),
            "scope": operation_scope,
            "stage": "queued",
            "current_item": None,
            "warnings": 0,
            "errors": 0,
            "sequence": next(_run_sequence),
            "recovery_state": "none",
            "discord": {"state": "pending", "label": "Pending", "events": []},
            "notification_events": set(),
            "persist_stop": persist_stop,
        }
    register_operation_reference(operation_id)
    _prune_completed_runs()
    with app.app_context():
        _persist_run_snapshot(run_id)
    heartbeat = threading.Thread(
        target=_heartbeat_loop,
        args=(app, run_id, persist_stop),
        daemon=True,
    )
    with _runs_lock:
        _runs[run_id]["heartbeat_thread"] = heartbeat
    thread = threading.Thread(
        target=_scan_thread,
        args=(
            app,
            run_id,
            scan_mode,
            operation_id,
            operation_scope,
            collection_relpath,
        ),
        daemon=True,
    )
    try:
        heartbeat.start()
        thread.start()
    except Exception as error:
        persist_stop.set()
        if heartbeat.is_alive():
            heartbeat.join(timeout=LIVE_HEARTBEAT_SECONDS + 1)
        unregister_operation_reference(operation_id)
        with _runs_lock:
            _runs.pop(run_id, None)
        with app.app_context():
            finish_catalogue_operation(operation_id, status="failed", error=error)
        raise
    return operation_id


def _record_notification(run_id, event, result):
    ok, message = result if isinstance(result, tuple) and len(result) == 2 else (False, "delivery failed")
    state = "sent" if ok else "disabled" if message == "disabled" else "not_configured" if message == "not configured" else "failed"
    with _runs_lock:
        notification = _runs[run_id]["discord"]
        notification["state"] = state
        notification["label"] = {
            "sent": "Sent", "disabled": "Discord disabled", "not_configured": "Discord not configured", "failed": "Delivery failed",
        }[state]
        notification["events"].append({"event": event, "state": state, "result": message, "attempted_at": datetime.utcnow().isoformat()})


def _notify_once(run_id, event, callback):
    with _runs_lock:
        sent = _runs[run_id]["notification_events"]
        if event in sent:
            return False, "duplicate skipped"
        sent.add(event)
    try:
        result = callback()
    except Exception as error:
        result = (False, redact_diagnostic(error, paths=_runs[run_id].get("redaction_paths"), limit=240))
    _record_notification(run_id, event, result)
    return result


def operation_run_snapshot(operation_id):
    """Return safe process-local progress for an operation, when still retained."""
    with _runs_lock:
        for run_id, run in _runs.items():
            if run.get("operation_id") != operation_id:
                continue
            progress = get_progress(run_id)
            return {
                "run_id": run_id, "stage": run.get("stage"), "current_item": run.get("current_item"),
                "status": run.get("status"), "progress": progress["progress"], "counts": progress["counts"],
                "summary": dict(run.get("summary", {})), "discord": dict(run.get("discord", {})),
            }
    return None


def operation_log_page(operation_id, *, page=1, per_page=50, severity="", search=""):
    """Return one bounded page from retained, already-redacted process logs."""
    page = max(1, int(page))
    per_page = min(100, max(1, int(per_page)))
    run = None
    with _runs_lock:
        for candidate in _runs.values():
            if candidate.get("operation_id") == operation_id:
                run = candidate
                break
        lines = (
            [
                redact_diagnostic(line, paths=run.get("redaction_paths"), limit=4000)
                for line in run["queue"].snapshot()
            ]
            if run
            else []
        )
    severity_markers = {"info": "[ℹ️]", "warning": "[⚠️]", "error": "[❌]"}
    marker = severity_markers.get(severity)
    needle = (search or "").casefold()[:100]
    filtered = [line for line in lines if (not marker or marker in line) and (not needle or needle in line.casefold())]
    start = (page - 1) * per_page
    return {"items": filtered[start:start + per_page], "page": page, "per_page": per_page,
            "total": len(filtered), "pages": max(1, (len(filtered) + per_page - 1) // per_page),
            "retained": bool(run)}


def _persist_run_snapshot(run_id):
    """Persist one bounded live snapshot for cross-worker readers."""

    with _runs_lock:
        run = _runs.get(run_id)
        if not run:
            return False
        entries, next_sequence = run["queue"].sequenced_snapshot()
        progress = get_progress(run_id)
        latest_message = entries[-1]["line"] if entries else ""
        state = {
            "stage": run.get("stage"),
            "current_item": run.get("current_item"),
            "latest_message": latest_message,
            "status": run.get("status"),
            "progress": progress.get("progress", {}),
            "counts": progress.get("counts", {}),
            "summary": _persistable_operation_summary(run.get("summary", {})),
            "discord": dict(run.get("discord", {})),
            "heartbeat_at": utcnow_iso(),
            "next_sequence": next_sequence,
        }
        operation_id = run.get("operation_id")
    return persist_live_state(operation_id, state, entries)


def _heartbeat_loop(app, run_id, stop_event):
    while not stop_event.wait(LIVE_HEARTBEAT_SECONDS):
        try:
            with app.app_context():
                _persist_run_snapshot(run_id)
        except Exception:
            # Browser observability must never interrupt scanner execution.
            with app.app_context():
                from app import db
                db.session.rollback()


def _scan_thread(
    app,
    run_id,
    scan_mode,
    operation_id,
    operation_scope,
    collection_relpath,
):
    with app.app_context():
        collection_relpath = collection_relpath or operation_scope.get(
            "collection_relpath"
        )
        start_ts = datetime.utcnow()
        operation_status = "failed"
        operation_error = None
        products_attempted = 0
        products_succeeded = 0
        products_failed = 0
        operation_marker_state = None
        operation_recovery_state = None
        products_missing = 0
        products_restored = 0
        variations_missing = 0
        variations_restored = 0
        scanner_execution_error = False
        try:
            log = make_logger(run_id)
            _runs[run_id]["stage"] = "preparing"
            _persist_run_snapshot(run_id)

            settings = Settings.query.first()
            if not settings:
                scanner_execution_error = True
                _runs[run_id]["stage"] = "failed"
                operation_error = "No settings found. Please complete setup first."
                log(operation_error, level="ERROR")
                return

            scan_folder = settings.product_folder or ""
            image_folder = settings.output_folder or ""
            url_prefix = settings.url_prefix or ""
            with _runs_lock:
                _runs[run_id]["redaction_paths"] = runtime_redaction_paths(
                    catalogue=scan_folder,
                    output=image_folder,
                    instance=app.instance_path,
                )

            if not (scan_folder and image_folder and url_prefix):
                scanner_execution_error = True
                _runs[run_id]["stage"] = "failed"
                operation_error = "Missing scan settings (folders or URL prefix)."
                log(operation_error, level="ERROR")
                return

            force = scan_mode in {"full", "shared_collection"}
            update = scan_mode in {"update", "shared_collection"}

            recovered = recover_committed_markers(scan_folder, log=log)
            if recovered["recovered"]:
                log(
                    f"🩹 Recovered {recovered['recovered']} committed marker(s)",
                    level="INFO",
                )

            plan = build_scan_scope(
                scan_folder,
                scan_mode,
                collection_relpath=collection_relpath,
            )
            folders = [os.path.basename(path) for path in plan.collection_paths]
            scan_complete = plan.complete
            _runs[run_id]["total"] = len(folders)
            _runs[run_id]["summary"]["folders"] = len(folders)

            # 🔔 Discord: started
            result = _notify_once(run_id, "scanner_started", lambda: notify_scan_started(scan_mode, len(folders)))
            if not result[0] and result[1] not in {"disabled", "not configured"}:
                # Notification delivery is operational context, not a scanner warning.
                log(f"Discord start notification: {result[1]}", level="INFO")

            log(f"Scan mode: {scan_mode}")
            log(f"Using product folder: {scan_folder}")
            log(f"Using image output folder: {image_folder}")
            log(f"Using URL prefix: {url_prefix}")
            log(f"Found {len(folders)} folders to process.")
            log("🔍 Starting scan...")

            all_rows = []
            collection_summaries = []
            for idx, name in enumerate(folders, start=1):
                folder_path = plan.collection_paths[idx - 1]
                _runs[run_id]["stage"] = "scanning"
                _runs[run_id]["current_item"] = name
                log(f"📂 Scanning: {folder_path}")
                _persist_run_snapshot(run_id)
                try:
                    rows = scan_collection(
                        folder_path,
                        url_prefix,
                        image_folder,
                        force_update=force,
                        update_csv=update,
                        log=log,
                        defer_markers=True,
                        operation_id=operation_id,
                    )
                    all_rows.extend(rows)
                    image_counts = image_ownership_summary(rows)
                    if len(collection_summaries) < COLLECTION_SUMMARY_LIMIT:
                        collection_summaries.append({
                            "collection": name,
                            "products": sum(row.get("Type") in ("simple", "variable") for row in rows),
                            "variations": sum(row.get("Type") == "variation" for row in rows),
                            **image_counts,
                        })
                    if plan.authoritative:
                        parent_count = sum(
                            row.get("Type") in ("simple", "variable") for row in rows
                        )
                        if parent_count != plan.expected_parent_counts.get(
                            folder_path
                        ):
                            scan_complete = False
                            log(
                                f"Authoritative scope incomplete for {name}: expected "
                                f"{plan.expected_parent_counts.get(folder_path)} "
                                f"parent(s), got {parent_count}",
                                level="ERROR",
                            )
                    log(f"✅ {name} → {len(rows)} rows processed.")
                except Exception as e:
                    if plan.authoritative:
                        scan_complete = False
                    log(f"❌ Error in {name}: {e}", level="ERROR")
                _runs[run_id]["done"] = idx
                _runs[run_id]["summary"]["collections_processed"] = idx
                _persist_run_snapshot(run_id)

            _runs[run_id]["summary"]["new_rows"] = len(all_rows)
            _runs[run_id]["summary"]["products_resolved"] = sum(
                row.get("Type") in ("simple", "variable") for row in all_rows
            )
            _runs[run_id]["summary"]["variations_processed"] = sum(
                row.get("Type") == "variation" for row in all_rows
            )
            _runs[run_id]["summary"].update(image_ownership_summary(all_rows))
            _runs[run_id]["summary"]["collection_summaries"] = collection_summaries

            # Ingest → DB
            _runs[run_id]["stage"] = "ingesting"
            _runs[run_id]["current_item"] = None
            _persist_run_snapshot(run_id)
            try:
                summary = ingest_rows_to_db(
                    all_rows, log=log, operation_id=operation_id
                )
            except Exception as database_error:
                recovery = mark_pending_database_recovery(
                    scan_folder,
                    operation_id,
                    database_error,
                    log=log,
                )
                products_failed = recovery["database_recovery_required"]
                products_attempted = products_failed
                operation_marker_state = "database_recovery_required"
                operation_recovery_state = "database_recovery_required"
                raise
            _runs[run_id]["summary"].update(summary)
            database_succeeded = summary.get("products_created", 0) + summary.get(
                "products_updated", 0
            )
            _runs[run_id]["stage"] = "finalizing"
            _persist_run_snapshot(run_id)
            marker_outcome = finalize_ingested_markers(
                scan_folder, operation_id, log=log
            )
            marker_failed = marker_outcome["marker_recovery_required"]
            products_succeeded = max(0, database_succeeded - marker_failed)
            products_failed = summary.get("products_failed", 0) + marker_failed
            products_attempted = products_succeeded + products_failed
            products_restored = summary.get("products_restored", 0)
            variations_missing = summary.get("variations_missing", 0)
            variations_restored = summary.get("variations_restored", 0)

            scope_type = (
                "shared_collection_update"
                if scan_mode == "shared_collection"
                else scan_mode
            )
            if plan.authoritative and not scan_complete:
                products_failed = max(1, products_failed)
                products_attempted = max(
                    products_attempted, products_succeeded + products_failed
                )
                operation_error = "Authoritative catalogue scope did not resolve completely"
            if (
                plan.authoritative
                and scan_complete
                and products_failed == 0
                and marker_failed == 0
                and marker_outcome["database_recovery_required"] == 0
            ):
                lifecycle = reconcile_authoritative_products(
                    authoritative_scope(
                        scope_type,
                        seen_source_relpaths=plan.seen_source_relpaths,
                        collection_source_relpath=collection_relpath,
                    ),
                    operation_id=operation_id,
                )
                products_missing = lifecycle["products_missing"]
                _runs[run_id]["summary"].update(lifecycle)
            if marker_outcome["database_recovery_required"] and marker_failed:
                operation_marker_state = "partial"
                operation_recovery_state = "multiple_recovery_required"
            elif marker_outcome["database_recovery_required"]:
                operation_marker_state = "database_recovery_required"
                operation_recovery_state = "database_recovery_required"
            elif marker_failed:
                operation_marker_state = "marker_recovery_required"
                operation_recovery_state = "marker_recovery_required"
            else:
                operation_marker_state = "finalized"
                operation_recovery_state = "none"
            log(f"🗄️ DB summary: {summary}")
            log(f"📦 Total rows prepared: {len(all_rows)}")
            log("✅ Scan complete.")
            _runs[run_id]["summary"]["warnings"] = _runs[run_id].get("warnings", 0)
            if products_failed and products_succeeded:
                operation_status = "partial"
            elif products_failed:
                operation_status = "failed"
            elif _runs[run_id]["summary"]["warnings"]:
                operation_status = "partial"
            else:
                operation_status = "succeeded"
            _runs[run_id]["stage"] = (
                "completed" if operation_status == "succeeded" else operation_status
            )
            if products_failed:
                if operation_recovery_state not in (None, "none"):
                    operation_error = (
                        f"{products_failed} catalogue product(s) require recovery"
                    )
                else:
                    operation_error = (
                        f"{products_failed} parent projection(s) failed and rolled back"
                    )

        except Exception as e:
            scanner_execution_error = True
            operation_error = e
            products_attempted = max(products_attempted, products_succeeded + 1)
            products_failed = products_attempted - products_succeeded
            make_logger(run_id)(f"❌ Critical error: {e}", level="ERROR")
            _runs[run_id]["stage"] = "failed"
            _runs[run_id]["current_item"] = None
        finally:
            _runs[run_id]["persist_stop"].set()
            heartbeat = _runs[run_id].get("heartbeat_thread")
            if heartbeat and heartbeat is not threading.current_thread():
                heartbeat.join(timeout=LIVE_HEARTBEAT_SECONDS + 1)
            if _runs[run_id]["stage"] == "failed":
                _runs[run_id]["stage"] = "failed"
                _runs[run_id]["current_item"] = None
            _runs[run_id]["summary"]["finished_at"] = datetime.utcnow().isoformat()
            _runs[run_id]["summary"].update(
                {
                    "products_attempted": products_attempted,
                    "products_succeeded": products_succeeded,
                    "products_failed": products_failed,
                }
            )
            _runs[run_id]["summary"].setdefault("warnings", _runs[run_id].get("warnings", 0))
            _runs[run_id]["summary"].update(warning_summary(_runs[run_id]))
            _runs[run_id]["recovery_state"] = operation_recovery_state or "none"
            try:
                _persist_run_snapshot(run_id)
            except Exception:
                from app import db
                db.session.rollback()
            try:
                finish_catalogue_operation(
                    operation_id,
                    status=operation_status,
                    products_attempted=products_attempted,
                    products_succeeded=products_succeeded,
                    products_failed=products_failed,
                    error=operation_error,
                    marker_state=operation_marker_state,
                    recovery_state=operation_recovery_state,
                    products_missing=products_missing,
                    products_restored=products_restored,
                    variations_missing=variations_missing,
                    variations_restored=variations_restored,
                    operation_summary=_persistable_operation_summary(_runs[run_id]["summary"]),
                )
            except Exception as finish_error:
                make_logger(run_id)(
                    f"Operation history finalisation failed: {finish_error}",
                    level="ERROR",
                )
            _runs[run_id]["status"] = "error" if scanner_execution_error else "done"
            elapsed = datetime.utcnow() - start_ts
            mins, secs = divmod(int(elapsed.total_seconds()), 60)
            elapsed_text = f"{mins:02d}:{secs:02d}"
            if operation_status == "failed":
                result = _notify_once(
                    run_id,
                    "scanner_failed",
                    lambda: notify_scan_failed(
                        scan_mode,
                        str(operation_error or "Scanner operation failed"),
                        summary=_runs[run_id]["summary"],
                        elapsed_text=elapsed_text,
                        operation_id=operation_id,
                    ),
                )
            else:
                result = _notify_once(
                    run_id,
                    "scanner_completed",
                    lambda: notify_scan_completed(
                        scan_mode,
                        _runs[run_id]["summary"],
                        elapsed_text,
                        operation_id=operation_id,
                    ),
                )
            if not result[0] and result[1] not in {"disabled", "not configured"}:
                # Delivery failures are reported separately and never reclassify scan output.
                make_logger(run_id)(f"Discord terminal notification: {result[1]}", level="INFO")
            try:
                _persist_run_snapshot(run_id)
            except Exception:
                from app import db
                db.session.rollback()
            _prune_completed_runs()


def stream_lines(run_id, timeout=15):
    q = _runs[run_id]["queue"]
    while True:
        try:
            line = q.get(timeout=timeout)
            yield f"data: {line}\n\n"
        except Empty:
            yield ":\n\n"
            if _runs[run_id]["status"] in ("done", "error") and q.empty():
                break


def get_progress(run_id):
    run = _runs[run_id]
    total = run["total"]
    done = run["done"]
    summary = run["summary"]
    products = summary.get("products_attempted", summary.get("products_resolved", 0))
    failures = summary.get("products_failed", run.get("errors", 0))
    started_at = summary.get("started_at")
    finished_at = summary.get("finished_at")
    elapsed_seconds = 0
    if started_at:
        try:
            started = datetime.fromisoformat(started_at)
            finished = (
                datetime.fromisoformat(finished_at) if finished_at else datetime.now()
            )
            elapsed_seconds = max(0, int((finished - started).total_seconds()))
        except (TypeError, ValueError):
            elapsed_seconds = 0
    return {
        # Frozen compatibility keys used by existing scan clients.
        "total": total,
        "done": done,
        "status": run["status"],
        "summary": summary,
        # Normalized, observational presentation contract for Phase 2 clients.
        "operation": {
            "id": run.get("operation_id"),
            "type": run.get("operation_type"),
            "status": run["status"],
            "stage": run.get("stage", "working"),
            "current_item": run.get("current_item"),
            "scope": run.get("scope", {}),
        },
        "progress": {
            "completed": done,
            "total": total,
            "percent": round((done / total) * 100) if total else 0,
            "unit": "collections",
        },
        "timing": {
            "started_at": started_at,
            "finished_at": finished_at,
            "elapsed_seconds": elapsed_seconds,
        },
        "counts": {
            "collections": summary.get("collections_processed", done),
            "products": products,
            "variations": summary.get("variations_processed", 0),
            "warnings": run.get("warnings", 0),
            "failures": failures,
        },
    }
