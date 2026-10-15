"""Atomic SQLite catalogue-operation reservation and persistent history."""

from __future__ import annotations

import json
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from flask import current_app, has_app_context, has_request_context, g
from app import db
from app.models import CatalogueOperation, Settings
from app.utils.redaction import redact_diagnostic, runtime_redaction_paths


ALLOWED_OPERATION_TYPES = {
    "shopify_product_lifecycle",
    "shopify_native_dimensions",
    "shopify_status_refresh",
    "shopify_taxonomy_batch",
    "shopify_draft_publish",
    "woo_taxonomy_sync",
    "taxonomy_registry_update",
    "append",
    "product_update",
    "shared_collection_update",
    "full",
    "reconstruction",
    "intake_catalogue_handoff",
    "woo_connection_test",
    "product_relationship_update",
    "woo_publish_preview",
    "woo_controlled_publish",
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
    from app.models import ShopifySaleSchedule
    protected.update(row.operation_id for row in ShopifySaleSchedule.query.all() if row.operation_id)

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
        from app.shopify_notifications import KEY, scope_of
        if scope_of(row).get('mode')=='read_only_sale_authorisation':
            continue  # original approved prices/revision are recoverable audit evidence
        if any(e.get("state") != "sent" for e in scope_of(row).get(KEY, {}).values()):
            continue  # pending/failed/uncertain delivery remains recoverable
        if row.operation_type == "shopify_product_lifecycle":
            continue  # Deleted remote/child bindings are audit archives, not routine logs.
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
    if has_app_context():
        row = (
            CatalogueOperation.query.filter(
                CatalogueOperation.operation_type.in_(ALLOWED_OPERATION_TYPES),
                CatalogueOperation.status.in_({"running", "pending"})
            )
            .order_by(CatalogueOperation.started_at.asc())
            .first()
        )
        if row:
            from app.utils.operation_live import persisted_live_state
            live = persisted_live_state(row) or {}
            return {
                "id": row.id,
                "operation_type": row.operation_type,
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "stage": live.get("stage") or "running",
                "heartbeat_at": live.get("heartbeat_at"),
                "current_item": live.get("current_item"),
                "elapsed_seconds": max(0, int((_utcnow() - row.started_at).total_seconds())) if row.started_at else None,
            }
    with _state_lock:
        return dict(_active_operation) if _active_operation else None


def acquire_catalogue_operation(operation_type: str, scope=None, *, transition_id=None) -> OperationLease:
    global _active_operation

    # Confirm has already committed the same authoritative operation before
    # network revalidation. Reuse only its request-scoped lease, never a global
    # lease from another request or a reconciliation operation.
    if operation_type == "shopify_draft_publish" and has_request_context() and g.get("shopify_confirm_lease"):
        return g.shopify_confirm_lease

    if operation_type not in ALLOWED_OPERATION_TYPES:
        raise ValueError(f"Unsupported catalogue operation type: {operation_type}")
    if transition_id is not None and (operation_type != 'shopify_draft_publish' or not __import__('re').fullmatch('[0-9a-f]{32}',transition_id)):
        raise ValueError('Invalid durable transition identity.')
    from app.shopify_capacity import signal_priority
    if operation_type.startswith("shopify_"):
        signal_priority(1 if operation_type == "shopify_status_refresh" else 2)
    if not _catalogue_lock.acquire(blocking=False):
        raise CatalogueOperationActive(
            get_active_operation()
            or {"id": "unknown", "operation_type": "unknown"}
        )

    operation_id = transition_id or uuid.uuid4().hex
    active = {
        "id": operation_id,
        "operation_type": operation_type,
        "started_at": _utcnow().isoformat(),
    }
    try:
        # A persisted preflight makes the authoritative lock visible to another
        # worker even though the production runtime currently uses one worker.
        existing = (
            CatalogueOperation.query.filter(
                CatalogueOperation.operation_type.in_(ALLOWED_OPERATION_TYPES),
                CatalogueOperation.status.in_({"running", "pending"})
            )
            .order_by(CatalogueOperation.started_at.asc())
            .first()
        )
        if existing:
            raise CatalogueOperationActive({
                "id": existing.id,
                "operation_type": existing.operation_type,
                "started_at": existing.started_at.isoformat() if existing.started_at else None,
            })
        if operation_type == "shopify_draft_publish" and has_request_context() and g.get("shopify_review_ticket"):
            scope = {**(scope or {}), "review_ticket":g.shopify_review_ticket}
        # One SQLite write closes the check/insert race across web/worker processes.
        from sqlalchemy import insert, select, literal, exists
        occupied = exists(select(CatalogueOperation.id).where(
            CatalogueOperation.operation_type.in_(ALLOWED_OPERATION_TYPES),
            CatalogueOperation.status.in_({"running", "pending"})))
        values = {"id":operation_id,"operation_type":operation_type,"status":"running",
            "scope":_safe_scope(scope),"started_at":_utcnow(),"marker_state":"not_started","recovery_state":"none",
            **{key:0 for key in ("products_attempted","products_succeeded","products_failed","products_missing","products_restored","variations_missing","variations_restored")}}
        inserted = db.session.execute(insert(CatalogueOperation.__table__).from_select(
            list(values), select(*(literal(value) for value in values.values())).where(~occupied)))
        if inserted.rowcount != 1:
            db.session.rollback()
            raise CatalogueOperationActive(get_active_operation() or {"id":"unknown","operation_type":"unknown"})
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
    notification = None
    publishing_event = None
    try:
        from app.shopify_capacity import guard_owner
        guard_owner()
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
            # Dedicated scan/publish/connection/intake notifiers retain their summaries.
            # Capture before commit: notification delivery must never refresh an ORM row.
            dedicated = {"append", "product_update", "shared_collection_update", "full",
                         "woo_controlled_publish", "woo_connection_test", "intake_catalogue_handoff",
                         "shopify_status_refresh", "shopify_draft_publish"}
            from app.shopify_notifications import enqueue, read_only
            publishing_event = enqueue(row)
            if row.operation_type == "woo_publish_preview" and operation_summary and operation_summary.get("readiness"):
                dedicated.add("woo_publish_preview")
            if row.operation_type not in dedicated and not read_only(row) and (status != "succeeded" or row.recovery_state not in (None, "none") or row.operation_type == "woo_taxonomy_sync"):
                try:
                    saved_summary = json.loads(row.scope or "{}").get("operation_summary", {})
                except (ValueError, AttributeError):
                    saved_summary = {}
                if not isinstance(saved_summary, dict):
                    saved_summary = {}
                notification = (row.operation_type, dict(saved_summary,
                    products_attempted=row.products_attempted, products_failed=row.products_failed,
                    recovery_state=row.recovery_state), row.error)
            from app.shopify_capacity import fence_owner
            fence_owner()
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
    if notification:
        try:
            from app.utils.discord import notify_operation_attention
            notify_operation_attention(notification[0], status, operation_id=operation_id,
                                       summary=notification[1], error=notification[2])
        except Exception:
            if has_app_context():
                current_app.logger.warning("Discord terminal notification failed safely")
    if publishing_event:
        try:
            from app.shopify_notifications import deliver
            deliver(operation_id, publishing_event)
        except Exception:
            db.session.rollback()
            current_app.logger.warning("Discord publishing delivery remains pending; publication result retained")
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
    from app.shopify_notifications import enqueue, read_only, deliver
    notices = [(row.id, row.operation_type) for row in rows if not read_only(row) and row.operation_type != "shopify_draft_publish"][:10]
    publishing_events = []
    for row in rows:
        row.status = "interrupted"
        row.finished_at = finished_at
        row.recovery_state = "review_required"
        row.error = row.error or "Application stopped before operation completion"
        event = enqueue(row)
        if event:
            publishing_events.append((row.id, event))
    db.session.commit()
    for operation_id, event in publishing_events:
        try:
            deliver(operation_id, event)
        except Exception:
            db.session.rollback()
            current_app.logger.warning("Discord interrupted publishing delivery remains pending")
    if not notices:
        return len(rows)
    try:
        from app.utils.discord import notify_operation_attention
        notify_operation_attention(
            "operation_recovery", "interrupted",
            error=f"Application stopped before {len(rows)} operation(s) completed; review interrupted operations and retained item/marker state.",
            summary={"recovery_state": "review_required", "stage": "Application restart",
                     "scope": f"{len(rows)} interrupted operations; first 5 listed",
                     "items": [{"name": f"{kind} · {operation_id}", "status": "interrupted",
                                "reason": "Review recovery before retrying"} for operation_id, kind in notices[:5]]},
        )
    except Exception:
        current_app.logger.warning("Discord interrupted-operation notification failed safely")
    return len(rows)


def reset_operation_control_for_tests() -> None:
    """Reset process-local state; only isolated tests should call this helper."""

    global _active_operation
    with _state_lock:
        _active_operation = None
        _operation_references.clear()
    if _catalogue_lock.locked():
        _catalogue_lock.release()
