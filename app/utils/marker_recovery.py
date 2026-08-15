"""Recoverable coordination between committed catalogue rows and scan markers."""

import os
from datetime import UTC, datetime

from app import db
from app.models import CatalogueOperation, CatalogueOperationItem
from app.utils.file_markers import (
    apply_pending_scanned,
    clear_pending_scanned,
    ensure_update,
    iter_pending_scanned,
    load_pending_scanned,
    set_pending_state,
)
from app.utils.operation_control import sanitize_operation_error


def _utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


def _source_path(folder, catalogue_root):
    try:
        relative = os.path.relpath(folder, catalogue_root)
    except ValueError:
        return None
    if relative == ".." or relative.startswith(f"..{os.sep}"):
        return None
    return relative.replace(os.sep, "/")


def _pending_for_operation(catalogue_root, operation_id):
    matches = []
    for folder in iter_pending_scanned(catalogue_root):
        pending = load_pending_scanned(folder, log=lambda *args, **kwargs: None)
        if pending.get("operation_id") == operation_id:
            matches.append((folder, pending))
    return matches


def _set_operation_state(operation_id, marker_state, recovery_state, error=None):
    operation = db.session.get(CatalogueOperation, operation_id)
    if not operation:
        return
    operation.marker_state = marker_state
    operation.recovery_state = recovery_state
    if error is not None:
        operation.error = sanitize_operation_error(error)


def _record_database_recovery(
    catalogue_root, operation_id, folder, pending, error, database_state
):
    sku = pending["marker"]["sku"]
    item = CatalogueOperationItem.query.filter_by(
        operation_id=operation_id, sku=sku
    ).first()
    if not item:
        item = CatalogueOperationItem(
            operation_id=operation_id,
            source_path=_source_path(folder, catalogue_root),
            sku=sku,
            started_at=_utcnow(),
        )
        db.session.add(item)
    item.status = "failed"
    item.database_state = database_state
    item.marker_state = "database_recovery_required"
    item.error = sanitize_operation_error(error)
    item.finished_at = _utcnow()
    _set_operation_state(
        operation_id,
        "database_recovery_required",
        "database_recovery_required",
        error,
    )


def mark_pending_database_recovery(
    catalogue_root, operation_id, error, log=print
):
    outcome = {
        "finalized": 0,
        "database_recovery_required": 0,
        "marker_recovery_required": 0,
        "errors": [],
    }
    safe_error = sanitize_operation_error(error)
    for folder, pending in _pending_for_operation(catalogue_root, operation_id):
        try:
            ensure_update(folder)
        except Exception as update_error:
            safe_error = sanitize_operation_error(
                f"{safe_error}; update retention failed: {update_error}"
            )
        _record_database_recovery(
            catalogue_root,
            operation_id,
            folder,
            pending,
            safe_error,
            "not_started",
        )
        outcome["database_recovery_required"] += 1
    db.session.commit()
    if outcome["database_recovery_required"]:
        outcome["errors"].append(safe_error)
        log(
            f"⚠️ {outcome['database_recovery_required']} product(s) require database recovery",
            level="WARN",
        )
    return outcome


def _finalize_committed_item(
    folder, pending, item, log=print, failure_injector=None
):
    sku = pending["marker"]["sku"]
    item.marker_state = "pending_finalization"
    db.session.commit()
    try:
        set_pending_state(folder, "pending_marker", log=log)
        apply_pending_scanned(
            folder, log=log, failure_injector=failure_injector
        )
        item = db.session.get(CatalogueOperationItem, item.id)
        item.status = "succeeded"
        item.marker_state = "finalized"
        item.error = None
        item.finished_at = _utcnow()
        db.session.commit()
        clear_pending_scanned(folder, failure_injector=failure_injector)
        log(f"✅ Finalized committed marker for {sku}", level="INFO")
        return None
    except Exception as error:
        db.session.rollback()
        item = db.session.get(CatalogueOperationItem, item.id)
        item.status = "recovery_required"
        item.marker_state = "marker_recovery_required"
        item.error = sanitize_operation_error(error)
        item.finished_at = _utcnow()
        _set_operation_state(
            item.operation_id,
            "marker_recovery_required",
            "marker_recovery_required",
            error,
        )
        db.session.commit()
        return error


def finalize_ingested_markers(
    catalogue_root,
    operation_id,
    log=print,
    failure_injector=None,
):
    outcome = {
        "finalized": 0,
        "database_recovery_required": 0,
        "marker_recovery_required": 0,
        "errors": [],
    }
    for folder, pending in _pending_for_operation(catalogue_root, operation_id):
        sku = pending["marker"]["sku"]
        item = CatalogueOperationItem.query.filter_by(
            operation_id=operation_id, sku=sku
        ).first()
        if item and item.database_state == "committed":
            error = _finalize_committed_item(
                folder,
                pending,
                item,
                log=log,
                failure_injector=failure_injector,
            )
            if error:
                outcome["marker_recovery_required"] += 1
                outcome["errors"].append(sanitize_operation_error(error))
            else:
                outcome["finalized"] += 1
            continue

        database_error = item.error if item and item.error else "Database ingestion did not commit"
        try:
            ensure_update(folder)
        except Exception as update_error:
            database_error = (
                f"{database_error}; update retention failed: {update_error}"
            )
        _record_database_recovery(
            catalogue_root,
            operation_id,
            folder,
            pending,
            database_error,
            item.database_state if item else "not_started",
        )
        db.session.commit()
        outcome["database_recovery_required"] += 1

    if outcome["marker_recovery_required"] and outcome["database_recovery_required"]:
        _set_operation_state(
            operation_id,
            "partial",
            "multiple_recovery_required",
            "; ".join(outcome["errors"]),
        )
    elif outcome["marker_recovery_required"]:
        _set_operation_state(
            operation_id,
            "marker_recovery_required",
            "marker_recovery_required",
            "; ".join(outcome["errors"]),
        )
    elif outcome["database_recovery_required"]:
        _set_operation_state(
            operation_id,
            "database_recovery_required",
            "database_recovery_required",
        )
    elif outcome["finalized"]:
        _set_operation_state(operation_id, "finalized", "none")
    db.session.commit()
    return outcome


def recover_committed_markers(catalogue_root, log=print):
    outcome = {"recovered": 0, "still_pending": 0, "errors": []}
    for folder in iter_pending_scanned(catalogue_root):
        pending = load_pending_scanned(folder, log=log)
        if not pending:
            outcome["still_pending"] += 1
            continue
        operation_id = pending.get("operation_id")
        sku = pending["marker"]["sku"]
        item = CatalogueOperationItem.query.filter_by(
            operation_id=operation_id, sku=sku, database_state="committed"
        ).first()
        if not item:
            outcome["still_pending"] += 1
            continue
        error = _finalize_committed_item(folder, pending, item, log=log)
        if error:
            outcome["still_pending"] += 1
            outcome["errors"].append(sanitize_operation_error(error))
        else:
            outcome["recovered"] += 1
            _set_operation_state(operation_id, "finalized", "recovered")
            db.session.commit()
    return outcome
