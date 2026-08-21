"""Single-process catalogue-operation locking and persistent history."""

from __future__ import annotations

import json
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from flask import current_app, has_app_context
from app import db
from app.models import CatalogueOperation, Settings
from app.utils.redaction import redact_diagnostic, runtime_redaction_paths


ALLOWED_OPERATION_TYPES = {
    "append",
    "product_update",
    "shared_collection_update",
    "full",
    "reconstruction",
}
FINAL_STATUSES = {"succeeded", "partial", "failed", "interrupted"}

_catalogue_lock = threading.Lock()
_state_lock = threading.Lock()
_active_operation: dict | None = None
_operation_references: set[str] = set()

ROUTINE_OPERATION_COUNT = 1000
ROUTINE_OPERATION_AGE = timedelta(days=180)
FAILURE_OPERATION_AGE = timedelta(days=365)


class CatalogueOperationActive(RuntimeError):
    def __init__(self, active: dict):
        super().__init__(
            f"Catalogue operation {active['operation_type']} ({active['id']}) is active"
        )
        self.active = active


@dataclass(frozen=True)
class OperationLease:
    id: str
    operation_type: str


def _utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


def _safe_scope(scope) -> str:
    def clean(value, key=""):
        if any(word in key.lower() for word in ("secret", "password", "token", "webhook")):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {str(k)[:100]: clean(v, str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(item, key) for item in value[:20]]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return sanitize_operation_error(value)[:500]

    return json.dumps(clean(scope or {}), sort_keys=True)[:4000]


def _redaction_paths():
    if not has_app_context():
        return runtime_redaction_paths()
    try:
        settings = Settings.query.first()
        return runtime_redaction_paths(
            catalogue=settings.product_folder if settings else None,
            output=settings.output_folder if settings else None,
            instance=current_app.instance_path,
        )
    except Exception:
        return runtime_redaction_paths(instance=current_app.instance_path)


def sanitize_operation_error(error) -> str:
    return redact_diagnostic(error, paths=_redaction_paths(), limit=1000)


def register_operation_reference(operation_id: str) -> None:
    with _state_lock:
        _operation_references.add(operation_id)


def unregister_operation_reference(operation_id: str) -> None:
    with _state_lock:
        _operation_references.discard(operation_id)


def prune_operation_history(*, now=None, protected_ids=None):
    """Apply bounded completed-operation retention without touching active recovery."""

    current = (now or datetime.now(UTC)).replace(tzinfo=None)
    routine_cutoff = current - ROUTINE_OPERATION_AGE
    failure_cutoff = current - FAILURE_OPERATION_AGE
    with _state_lock:
        protected = set(_operation_references)
        if _active_operation:
            protected.add(_active_operation["id"])
    protected.update(protected_ids or ())

    completed = CatalogueOperation.query.filter(
        CatalogueOperation.finished_at.isnot(None),
        ~CatalogueOperation.status.in_({"running", "pending"}),
    ).all()
    completed.sort(
        key=lambda row: (row.finished_at or row.started_at, row.id), reverse=True
    )
    newest_by_type = {}
    for row in completed:
        newest_by_type.setdefault(row.operation_type, row.id)
    protected.update(newest_by_type.values())

    routine = [
        row
        for row in completed
        if row.status == "succeeded" and row.recovery_state in (None, "none")
    ]
    protected.update(row.id for row in routine[:ROUTINE_OPERATION_COUNT])

    candidates = []
    for row in completed:
        if row.id in protected:
            continue
        if row.status == "succeeded" and row.recovery_state in (None, "none"):
            if row.finished_at < routine_cutoff:
                candidates.append(row)
            continue
        if row.recovery_state not in (None, "none"):
            continue
        if row.finished_at < failure_cutoff:
            candidates.append(row)

    candidates.sort(key=lambda row: (row.finished_at, row.id))
    for row in candidates:
        db.session.delete(row)
    if candidates:
        db.session.commit()
    return {"deleted": len(candidates), "retained": len(completed) - len(candidates)}


def get_active_operation() -> dict | None:
    with _state_lock:
        return dict(_active_operation) if _active_operation else None


def acquire_catalogue_operation(operation_type: str, scope=None) -> OperationLease:
    global _active_operation

    if operation_type not in ALLOWED_OPERATION_TYPES:
        raise ValueError(f"Unsupported catalogue operation type: {operation_type}")
    if not _catalogue_lock.acquire(blocking=False):
        raise CatalogueOperationActive(
            get_active_operation()
            or {"id": "unknown", "operation_type": "unknown"}
        )

    operation_id = uuid.uuid4().hex
    active = {
        "id": operation_id,
        "operation_type": operation_type,
        "started_at": _utcnow().isoformat(),
    }
    try:
        row = CatalogueOperation(
            id=operation_id,
            operation_type=operation_type,
            status="running",
            scope=_safe_scope(scope),
            started_at=_utcnow(),
        )
        db.session.add(row)
        db.session.commit()
        with _state_lock:
            _active_operation = active
        return OperationLease(operation_id, operation_type)
    except Exception:
        db.session.rollback()
        _catalogue_lock.release()
        raise


def finish_catalogue_operation(
    operation_id: str,
    *,
    status: str,
    products_attempted: int | None = None,
    products_succeeded: int | None = None,
    products_failed: int | None = None,
    error=None,
    marker_state: str | None = None,
    recovery_state: str | None = None,
    products_missing: int | None = None,
    products_restored: int | None = None,
    variations_missing: int | None = None,
    variations_restored: int | None = None,
    operation_summary: dict | None = None,
) -> None:
    global _active_operation

    if status not in FINAL_STATUSES:
        raise ValueError(f"Unsupported final operation status: {status}")
    try:
        row = db.session.get(CatalogueOperation, operation_id)
        if row:
            row.status = status
            row.finished_at = _utcnow()
            if products_attempted is not None:
                row.products_attempted = max(0, products_attempted)
            if products_succeeded is not None:
                row.products_succeeded = max(0, products_succeeded)
            if products_failed is not None:
                row.products_failed = max(0, products_failed)
            if products_missing is not None:
                row.products_missing = max(0, products_missing)
            if products_restored is not None:
                row.products_restored = max(0, products_restored)
            if variations_missing is not None:
                row.variations_missing = max(0, variations_missing)
            if variations_restored is not None:
                row.variations_restored = max(0, variations_restored)
            if error is not None:
                row.error = sanitize_operation_error(error)
            if marker_state is not None:
                row.marker_state = marker_state
            if recovery_state is not None:
                row.recovery_state = recovery_state
            if operation_summary is not None:
                try:
                    scope = json.loads(row.scope or "{}")
                except (TypeError, ValueError):
                    scope = {}
                if not isinstance(scope, dict):
                    scope = {}
                scope["operation_summary"] = operation_summary
                row.scope = json.dumps(scope, ensure_ascii=False, separators=(",", ":"))
            db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    finally:
        with _state_lock:
            matches = _active_operation and _active_operation["id"] == operation_id
            if matches:
                _active_operation = None
        if matches and _catalogue_lock.locked():
            _catalogue_lock.release()
    try:
        prune_operation_history()
    except Exception as cleanup_error:
        db.session.rollback()
        if has_app_context():
            current_app.logger.warning(
                "Operation retention cleanup failed: %s",
                sanitize_operation_error(cleanup_error),
            )


@contextmanager
def operation_context(operation_type: str, scope=None):
    lease = acquire_catalogue_operation(operation_type, scope)
    try:
        yield lease
    except Exception as error:
        finish_catalogue_operation(lease.id, status="failed", error=error)
        raise
    else:
        finish_catalogue_operation(lease.id, status="succeeded")


def recover_interrupted_operations() -> int:
    rows = CatalogueOperation.query.filter_by(status="running").all()
    if not rows:
        return 0
    finished_at = _utcnow()
    for row in rows:
        row.status = "interrupted"
        row.finished_at = finished_at
        row.recovery_state = "review_required"
        row.error = row.error or "Application stopped before operation completion"
    db.session.commit()
    return len(rows)


def reset_operation_control_for_tests() -> None:
    """Reset process-local state; only isolated tests should call this helper."""

    global _active_operation
    with _state_lock:
        _active_operation = None
        _operation_references.clear()
    if _catalogue_lock.locked():
        _catalogue_lock.release()
