# app/utils/scan_runner.py
import os
import time
import threading
from dataclasses import dataclass
from flask import url_for
from datetime import datetime
from queue import Queue, Empty

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
)
from app.utils.reconciliation import (
    authoritative_scope,
    reconcile_authoritative_products,
)

# In-memory progress store
_runs = {}


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
    q = _runs[run_id]["queue"]

    def log(msg, level="INFO"):
        normalized_level = level.upper()
        if normalized_level == "WARN":
            _runs[run_id]["warnings"] = _runs[run_id].get("warnings", 0) + 1
        elif normalized_level == "ERROR":
            _runs[run_id]["errors"] = _runs[run_id].get("errors", 0) + 1
        prefix = {"INFO": "[ℹ️]", "WARN": "[⚠️]", "ERROR": "[❌]"}.get(
            normalized_level, "[ℹ️]"
        )
        line = f"{time.strftime('%H:%M:%S')} {prefix} {msg}"
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

    _runs[run_id] = {
        "total": 0,
        "done": 0,
        "status": "running",
        "queue": Queue(),
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
    }
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
        thread.start()
    except Exception as error:
        with app.app_context():
            finish_catalogue_operation(operation_id, status="failed", error=error)
        raise
    return operation_id


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
        try:
            log = make_logger(run_id)
            _runs[run_id]["stage"] = "preparing"

            settings = Settings.query.first()
            if not settings:
                _runs[run_id]["status"] = "error"
                operation_error = "No settings found. Please complete setup first."
                log(operation_error, level="ERROR")
                return

            scan_folder = settings.product_folder or ""
            image_folder = settings.output_folder or ""
            url_prefix = settings.url_prefix or ""

            if not (scan_folder and image_folder and url_prefix):
                _runs[run_id]["status"] = "error"
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
            try:
                notify_scan_started(scan_mode, len(folders))
            except Exception as e:
                log(f"⚠️ Discord start notify failed: {e}", level="WARN")

            log(f"Scan mode: {scan_mode}")
            log(f"Using product folder: {scan_folder}")
            log(f"Using image output folder: {image_folder}")
            log(f"Using URL prefix: {url_prefix}")
            log(f"Found {len(folders)} folders to process.")
            log("🔍 Starting scan...")

            all_rows = []
            for idx, name in enumerate(folders, start=1):
                folder_path = plan.collection_paths[idx - 1]
                _runs[run_id]["stage"] = "scanning"
                _runs[run_id]["current_item"] = name
                log(f"📂 Scanning: {folder_path}")
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

            _runs[run_id]["summary"]["new_rows"] = len(all_rows)
            _runs[run_id]["summary"]["products_resolved"] = sum(
                row.get("Type") in ("simple", "variable") for row in all_rows
            )
            _runs[run_id]["summary"]["variations_processed"] = sum(
                row.get("Type") == "variation" for row in all_rows
            )

            # Ingest → DB
            _runs[run_id]["stage"] = "ingesting"
            _runs[run_id]["current_item"] = None
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

            # 🔔 Discord: completed
            elapsed = datetime.utcnow() - start_ts
            mins, secs = divmod(int(elapsed.total_seconds()), 60)
            elapsed_text = f"{mins:02d}:{secs:02d}"
            try:
                notify_scan_completed(scan_mode, _runs[run_id]["summary"], elapsed_text)
            except Exception as e:
                log(f"⚠️ Discord complete notify failed: {e}", level="WARN")

            _runs[run_id]["status"] = "done"
            if products_failed and products_succeeded:
                operation_status = "partial"
            elif products_failed:
                operation_status = "failed"
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
            operation_error = e
            products_attempted = max(products_attempted, products_succeeded + 1)
            products_failed = products_attempted - products_succeeded
            make_logger(run_id)(f"❌ Critical error: {e}", level="ERROR")
            _runs[run_id]["status"] = "error"
            _runs[run_id]["stage"] = "failed"
            _runs[run_id]["current_item"] = None
            # 🔔 Discord: failed
            try:
                notify_scan_failed(scan_mode, str(e))
            except Exception:
                pass
        finally:
            if _runs[run_id]["status"] == "error":
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
                )
            except Exception as finish_error:
                make_logger(run_id)(
                    f"Operation history finalisation failed: {finish_error}",
                    level="ERROR",
                )


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
