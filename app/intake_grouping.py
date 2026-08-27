"""Copy-first Catalogue Intake grouping with isolated locking and atomic promotion."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import hmac
import json
import os
import re
import shutil
import stat
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

from flask import current_app

from app import db
from app.image_preparation import (
    INTAKE_STAGING_DIRECTORY,
    PREPARED_DIRECTORY,
    _classify_entry,
    _within,
    configured_intake_root,
    grouping_confirmation_blockers,
    grouping_preview,
    intake_readiness,
    resolve_intake_folder,
)
from app.models import CatalogueOperation
from app.utils.operation_control import finish_catalogue_operation, sanitize_operation_error
from app.utils.operation_live import persist_live_state, utcnow_iso


INTAKE_OPERATION_TYPE = "intake_group"
STALE_STAGING_AGE = timedelta(hours=24)
_OPERATION_ID = re.compile(r"^[0-9a-f]{32}$")
_process_lock = threading.Lock()
_state_lock = threading.Lock()
_active_lease = None


class GroupingRejected(RuntimeError):
    pass


class IntakeOperationActive(RuntimeError):
    def __init__(self, active):
        super().__init__("A Catalogue Intake preparation operation is already running")
        self.active = active


@dataclass(frozen=True)
class IntakeOperationLease:
    id: str
    lock_fd: int


def _utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


def _lock_path():
    configured = current_app.config.get("INTAKE_MUTATION_LOCK_PATH")
    return Path(configured) if configured else Path(current_app.instance_path) / "catalogue-intake-mutation.lock"


def _safe_scope(scope):
    safe = {
        "source_relpath": str(scope.get("source_relpath") or "")[:1024],
        "proposed_result_name": str(scope.get("proposed_result_name") or "")[:255],
        "proposal_digest": str(scope.get("proposal_digest") or "")[:64],
        "source_images": max(0, int(scope.get("source_images") or 0)),
        "group_count": max(0, int(scope.get("group_count") or 0)),
        "workflow_status": "folder_review_required",
    }
    return json.dumps(safe, ensure_ascii=False, separators=(",", ":"))


def get_active_intake_operation():
    row = (
        CatalogueOperation.query.filter_by(operation_type=INTAKE_OPERATION_TYPE)
        .filter(CatalogueOperation.status.in_({"running", "pending"}))
        .order_by(CatalogueOperation.started_at.asc())
        .first()
    )
    if row:
        return {"id": row.id, "operation_type": row.operation_type, "started_at": row.started_at.isoformat() if row.started_at else None}
    with _state_lock:
        return {"id": _active_lease.id, "operation_type": INTAKE_OPERATION_TYPE} if _active_lease else None


def acquire_intake_operation(scope) -> IntakeOperationLease:
    global _active_lease

    if not _process_lock.acquire(blocking=False):
        raise IntakeOperationActive(get_active_intake_operation() or {"id": "unknown"})
    fd = None
    try:
        path = _lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise IntakeOperationActive(get_active_intake_operation() or {"id": "unknown"}) from error
        existing = (
            CatalogueOperation.query.filter_by(operation_type=INTAKE_OPERATION_TYPE)
            .filter(CatalogueOperation.status.in_({"running", "pending"}))
            .order_by(CatalogueOperation.started_at.asc())
            .first()
        )
        if existing:
            raise IntakeOperationActive({"id": existing.id, "operation_type": existing.operation_type})
        operation_id = uuid.uuid4().hex
        row = CatalogueOperation(
            id=operation_id,
            operation_type=INTAKE_OPERATION_TYPE,
            status="running",
            scope=_safe_scope(scope),
            started_at=_utcnow(),
        )
        db.session.add(row)
        db.session.commit()
        lease = IntakeOperationLease(operation_id, fd)
        with _state_lock:
            _active_lease = lease
        return lease
    except Exception:
        db.session.rollback()
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)
        _process_lock.release()
        raise


def finish_intake_operation(lease, *, status, error=None, summary=None):
    global _active_lease

    try:
        finish_catalogue_operation(
            lease.id,
            status=status,
            products_attempted=(summary or {}).get("source_images", 0),
            products_succeeded=(summary or {}).get("copied_images", 0),
            products_failed=(summary or {}).get("failed_images", int(status == "failed")),
            error=error,
            operation_summary=summary,
        )
    finally:
        with _state_lock:
            if _active_lease and _active_lease.id == lease.id:
                _active_lease = None
        try:
            fcntl.flock(lease.lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lease.lock_fd)
            if _process_lock.locked():
                _process_lock.release()


def reset_intake_operation_control_for_tests():
    global _active_lease

    with _state_lock:
        lease = _active_lease
        _active_lease = None
    if lease:
        try:
            fcntl.flock(lease.lock_fd, fcntl.LOCK_UN)
            os.close(lease.lock_fd)
        except OSError:
            pass
    if _process_lock.locked():
        _process_lock.release()


def revalidate_grouping(relative, submitted_digest):
    readiness = intake_readiness()
    if not readiness["readable"]:
        raise GroupingRejected("Catalogue Intake is unavailable")
    if not readiness["writable"]:
        raise GroupingRejected("Catalogue Intake must be mounted read/write before grouping can be confirmed")
    preview = grouping_preview(configured_intake_root(), relative)
    if not hmac.compare_digest(str(preview["digest"]), str(submitted_digest or "")):
        raise GroupingRejected("The source folder changed after preview. Review the updated grouping proposal before continuing.")
    blockers = grouping_confirmation_blockers(preview)
    if blockers:
        raise GroupingRejected("This grouping proposal cannot be confirmed until its blocking conflicts are corrected.")
    selected = PurePosixPath(preview["browser"]["path"])
    if selected.parts and selected.parts[0] in {PREPARED_DIRECTORY, INTAKE_STAGING_DIRECTORY}:
        raise GroupingRejected("Prepared and application staging folders cannot be used as loose-image sources")
    return preview


class _Progress:
    def __init__(self, operation_id, total):
        self.operation_id = operation_id
        self.total = total
        self.copied = 0
        self.groups = 0
        self.warnings = 0
        self.failures = 0
        self.logs = []
        self.stage = "revalidating_preview"
        self.message = "Revalidating the grouping proposal"
        self.current_item = ""
        self.discord = {"state": "pending", "label": "Pending", "events": []}
        self._last_persisted_copy_count = 0

    def update(self, stage, message, *, severity="info", current_item=""):
        self.stage = stage
        self.message = message
        self.current_item = current_item
        self.logs.append({"sequence": len(self.logs) + 1, "severity": severity, "line": message})
        self.persist()

    def persist(self, *, summary=None, status="running"):
        percent = round((self.copied / self.total * 100), 2) if self.total else 0
        persist_live_state(
            self.operation_id,
            {
                "stage": self.stage,
                "current_item": self.current_item,
                "latest_message": self.message,
                "status": status,
                "progress": {"completed": self.copied, "total": self.total, "percent": percent, "unit": "images"},
                "counts": {"images": self.copied, "groups": self.groups, "warnings": self.warnings, "failures": self.failures},
                "summary": summary or {},
                "discord": self.discord,
                "heartbeat_at": utcnow_iso(),
                "next_sequence": len(self.logs) + 1,
            },
            self.logs,
        )
        self._last_persisted_copy_count = self.copied

    def persist_copy_progress(self):
        """Persist useful live progress without one SQLite transaction per file."""

        batch_size = max(5, min(25, (self.total + 19) // 20))
        if self.copied >= self.total or self.copied - self._last_persisted_copy_count >= batch_size:
            self.persist()

    def record_discord(self, event, result):
        ok, message = result if isinstance(result, tuple) and len(result) == 2 else (False, "delivery failed")
        state = "sent" if ok else "disabled" if message == "disabled" else "not_configured" if message == "not configured" else "failed"
        self.discord = {"state": state, "label": {"sent": "Sent", "disabled": "Discord disabled", "not_configured": "Discord not configured", "failed": "Delivery failed"}[state], "events": [{"event": event, "state": state}]}


def _operation_marker(operation_dir, operation_id):
    marker = operation_dir / ".operation-owner"
    marker.write_text(operation_id, encoding="ascii")
    os.chmod(marker, 0o600)
    return marker


def _owned_operation_directory(path, operation_id):
    if not _OPERATION_ID.fullmatch(operation_id) or path.name != operation_id:
        return False
    marker = path / ".operation-owner"
    try:
        return stat.S_ISREG(marker.lstat().st_mode) and marker.read_text(encoding="ascii") == operation_id
    except (OSError, UnicodeError):
        return False


def _cleanup_operation_staging(root, operation_id):
    operation_dir = root / INTAKE_STAGING_DIRECTORY / operation_id
    if not _within(operation_dir, root / INTAKE_STAGING_DIRECTORY) or not _owned_operation_directory(operation_dir, operation_id):
        return False
    shutil.rmtree(operation_dir)
    return True


def cleanup_stale_staging(root, *, now=None, protected_ids=None):
    root = Path(root).resolve(strict=True)
    staging = root / INTAKE_STAGING_DIRECTORY
    if not staging.exists():
        return {"removed": 0, "warnings": []}
    try:
        staging_stat = staging.lstat()
    except OSError:
        return {"removed": 0, "warnings": ["Private staging could not be inspected"]}
    if stat.S_ISLNK(staging_stat.st_mode) or not stat.S_ISDIR(staging_stat.st_mode):
        return {"removed": 0, "warnings": ["Private staging is unsafe"]}
    current = now or _utcnow()
    protected = set(protected_ids or ())
    protected.update(
        row.id
        for row in CatalogueOperation.query.filter_by(operation_type=INTAKE_OPERATION_TYPE)
        .filter(CatalogueOperation.status.in_({"running", "pending"}))
        .all()
    )
    removed = 0
    warnings = []
    for entry in sorted(staging.iterdir(), key=lambda value: (value.name.casefold(), value.name)):
        if entry.name in protected or not _OPERATION_ID.fullmatch(entry.name):
            continue
        try:
            age = current - datetime.fromtimestamp(entry.lstat().st_mtime, UTC).replace(tzinfo=None)
            if age < STALE_STAGING_AGE or not _owned_operation_directory(entry, entry.name):
                continue
            shutil.rmtree(entry)
            removed += 1
        except OSError:
            warnings.append(f"Stale staging cleanup could not remove operation {entry.name[:8]}")
    return {"removed": removed, "warnings": warnings[:10]}


def _copy_source_file(source, destination, expected):
    before = source.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink > 1:
        raise GroupingRejected("A required source image is no longer a safe regular file")
    if before.st_size != expected["size"] or before.st_mtime_ns != expected["mtime_ns"]:
        raise GroupingRejected("The source folder changed after preview. Review the updated grouping proposal before continuing.")
    kind, _info = _classify_entry(source)
    if kind != "image":
        raise GroupingRejected("A required source image is no longer valid")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_file, destination.open("xb") as output_file:
        shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
        output_file.flush()
        os.fsync(output_file.fileno())
    after = source.lstat()
    if (after.st_size, after.st_mtime_ns, after.st_ino) != (before.st_size, before.st_mtime_ns, before.st_ino):
        raise GroupingRejected("The source folder changed while images were copied")


def _expected_stage_path(stage_result, mapping):
    return stage_result / mapping["proposed_group"] / mapping["source_name"]


def _verify_staged_result(stage_result, preview):
    expected = {
        (mapping["proposed_group"], mapping["source_name"]): mapping
        for mapping in preview["mappings"]
    }
    found = {}
    for current, directories, filenames in os.walk(stage_result, followlinks=False):
        current_path = Path(current)
        for name in directories:
            info = (current_path / name).lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise GroupingRejected("The staged result contains an unsafe directory")
        for name in filenames:
            candidate = current_path / name
            relative = candidate.relative_to(stage_result)
            if len(relative.parts) != 2:
                raise GroupingRejected("The staged result does not match the grouping proposal")
            key = tuple(relative.parts)
            if key not in expected or key in found:
                raise GroupingRejected("The staged result contains an unexpected file")
            kind, info = _classify_entry(candidate)
            if kind != "image" or info.st_size != expected[key]["size"]:
                raise GroupingRejected("A staged image failed verification")
            found[key] = candidate
    if set(found) != set(expected):
        raise GroupingRejected("The staged result is incomplete")
    return True


def _atomic_promote_noreplace(source, destination):
    source_value = os.fsencode(source)
    destination_value = os.fsencode(destination)
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        result = libc.renameat2(-100, source_value, -100, destination_value, 1)
    elif sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        result = libc.renamex_np(source_value, destination_value, 0x00000004)
    else:
        if destination.exists():
            raise FileExistsError(str(destination))
        os.rename(source, destination)
        return
    if result != 0:
        code = ctypes.get_errno()
        if code in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError(str(destination))
        raise OSError(code, os.strerror(code))


def _warning_summary(preview):
    counts = {}
    entries = []
    for issue in preview.get("issues") or ():
        if issue.get("state") == "blocking":
            continue
        category = str(issue.get("category") or "warning")[:80]
        counts[category] = counts.get(category, 0) + 1
        if len(entries) < 10:
            entries.append(str(issue.get("message") or category)[:240])
    return ([{"category": key, "count": value, "samples": []} for key, value in sorted(counts.items())], entries)


def _notify_completed(progress, summary, elapsed):
    from app.utils.discord import notify_intake_grouping_completed

    try:
        result = notify_intake_grouping_completed(
            source_name=PurePosixPath(summary["source_relpath"]).name,
            result_name=summary["result_name"],
            groups=summary["group_count"],
            copied_images=summary["copied_images"],
            warnings=summary["warnings"],
            elapsed_text=f"{elapsed:.1f}s",
            operation_id=progress.operation_id,
        )
    except Exception:
        result = (False, "delivery failed")
    progress.record_discord("completed", result)


def _notify_failed(progress, relative, error):
    from app.utils.discord import notify_intake_grouping_failed

    try:
        result = notify_intake_grouping_failed(
            source_name=PurePosixPath(relative).name,
            error_text=sanitize_operation_error(error),
            operation_id=progress.operation_id,
        )
    except Exception:
        result = (False, "delivery failed")
    progress.record_discord("failed", result)


def execute_grouping_operation(lease, relative, submitted_digest):
    root = Path(configured_intake_root()).resolve(strict=True)
    progress = _Progress(lease.id, 0)
    started = time.monotonic()
    operation_dir = root / INTAKE_STAGING_DIRECTORY / lease.id
    summary = None
    promoted = False
    preview = None
    canonical = relative
    try:
        progress.update("revalidating_preview", "Revalidating the grouping proposal")
        preview = revalidate_grouping(relative, submitted_digest)
        progress.total = len(preview["mappings"])
        progress.warnings = len([issue for issue in preview["issues"] if issue.get("state") != "blocking"])
        progress.update("acquiring_intake_lock", "Dedicated Catalogue Intake mutation lock acquired")

        cleanup = cleanup_stale_staging(root, protected_ids={lease.id})
        for warning in cleanup["warnings"]:
            progress.warnings += 1
            progress.update("cleaning_staging", warning, severity="warning")

        progress.update("creating_staging_tree", "Creating the private operation-owned staging tree")
        staging_root = root / INTAKE_STAGING_DIRECTORY
        staging_root.mkdir(mode=0o700, exist_ok=True)
        staging_info = staging_root.lstat()
        if stat.S_ISLNK(staging_info.st_mode) or not stat.S_ISDIR(staging_info.st_mode):
            raise GroupingRejected("Private Catalogue Intake staging is unsafe")
        operation_dir.mkdir(mode=0o700)
        _operation_marker(operation_dir, lease.id)
        stage_result = operation_dir / "result"
        stage_result.mkdir(mode=0o700)

        source_folder, canonical = resolve_intake_folder(root, relative)
        completed_groups = set()
        progress.update("copying_grouped_images", f"Copying 0 of {progress.total} grouped images")
        for mapping in preview["mappings"]:
            source = source_folder / mapping["source_name"]
            destination = _expected_stage_path(stage_result, mapping)
            _copy_source_file(source, destination, mapping)
            progress.copied += 1
            completed_groups.add(mapping["proposed_group"])
            progress.groups = len(completed_groups)
            progress.message = f"Images copied: {progress.copied} / {progress.total}"
            progress.current_item = mapping["proposed_group"]
            progress.persist_copy_progress()

        progress.update("verifying_staged_result", "Verifying the complete staged grouping result")
        _verify_staged_result(stage_result, preview)
        if grouping_preview(root, canonical)["digest"] != submitted_digest:
            raise GroupingRejected("The source folder changed while images were copied")

        progress.update("promoting_prepared_result", "Promoting the verified grouped result atomically")
        prepared_root = root / PREPARED_DIRECTORY
        prepared_root.mkdir(mode=0o755, exist_ok=True)
        prepared_info = prepared_root.lstat()
        if stat.S_ISLNK(prepared_info.st_mode) or not stat.S_ISDIR(prepared_info.st_mode):
            raise GroupingRejected("The Prepared result root is unsafe")
        final_destination = prepared_root / preview["result_name"]
        if final_destination.exists() or final_destination.is_symlink():
            raise GroupingRejected("The prepared destination changed after preview")
        _atomic_promote_noreplace(stage_result, final_destination)
        promoted = True

        progress.update("cleaning_staging", "Cleaning the completed operation staging wrapper")
        marker = operation_dir / ".operation-owner"
        try:
            marker.unlink()
            operation_dir.rmdir()
        except OSError:
            progress.warnings += 1
            progress.update("cleaning_staging", "Prepared result is complete; its empty staging wrapper requires later cleanup", severity="warning")

        warning_summary, warning_entries = _warning_summary(preview)
        counts = preview["browser"]["counts"]
        ignored = counts["hidden_system"] + counts["unsupported_entries"] + counts["corrupt_images"] + counts["child_directories"]
        summary = {
            "source_relpath": canonical,
            "result_name": preview["result_name"],
            "prepared_relpath": f"{PREPARED_DIRECTORY}/{preview['result_name']}",
            "proposal_digest": preview["digest"],
            "source_images": len(preview["mappings"]),
            "copied_images": progress.copied,
            "failed_images": 0,
            "group_count": len(preview["groups"]),
            "single_image_groups": len([group for group in preview["groups"] if group["single_image"]]),
            "ignored_entries": ignored,
            "hidden_ignored": counts["hidden_system"],
            "unsupported_ignored": counts["unsupported_entries"],
            "corrupt_images": counts["corrupt_images"],
            "warnings": progress.warnings,
            "warning_summary": warning_summary,
            "warning_entries": warning_entries,
            "workflow_status": "folder_review_required",
            "duration_seconds": round(time.monotonic() - started, 3),
        }
        _notify_completed(progress, summary, time.monotonic() - started)
        progress.stage = "completed_folder_review_required"
        progress.message = "Grouping complete — folder review required"
        progress.current_item = summary["prepared_relpath"]
        progress.persist(summary=summary, status="partial" if progress.warnings else "succeeded")
        finish_intake_operation(lease, status="partial" if progress.warnings else "succeeded", summary=summary)
    except Exception as error:
        if promoted:
            progress.warnings += 1
            summary = summary or {
                "source_relpath": canonical,
                "result_name": preview["result_name"] if preview else "Prepared result",
                "prepared_relpath": f"{PREPARED_DIRECTORY}/{preview['result_name']}" if preview else None,
                "proposal_digest": submitted_digest,
                "source_images": progress.total,
                "copied_images": progress.copied,
                "failed_images": 0,
                "group_count": len(preview["groups"]) if preview else progress.groups,
                "ignored_entries": 0,
                "warnings": progress.warnings,
                "warning_summary": [{"category": "post-promotion", "count": 1, "samples": []}],
                "warning_entries": ["A post-promotion bookkeeping step requires review"],
                "workflow_status": "folder_review_required",
            }
            _notify_completed(progress, summary, time.monotonic() - started)
            progress.stage = "completed_folder_review_required"
            progress.message = "Grouping complete — folder review required"
            progress.persist(summary=summary, status="partial")
            finish_intake_operation(lease, status="partial", summary=summary)
            return
        cleanup_warning = None
        try:
            _cleanup_operation_staging(root, lease.id)
        except OSError:
            cleanup_warning = "Operation staging cleanup requires review"
        progress.failures = 1
        progress.warnings += int(bool(cleanup_warning))
        progress.stage = "failed"
        progress.message = sanitize_operation_error(error)
        if cleanup_warning:
            progress.logs.append({"sequence": len(progress.logs) + 1, "severity": "warning", "line": cleanup_warning})
        _notify_failed(progress, relative, error)
        failed_summary = {
            "source_relpath": relative,
            "source_images": progress.total,
            "copied_images": progress.copied,
            "failed_images": 1,
            "warnings": progress.warnings,
            "workflow_status": "failed",
            "cleanup_warning": cleanup_warning,
        }
        progress.persist(summary=failed_summary, status="failed")
        finish_intake_operation(lease, status="failed", error=error, summary=failed_summary)


def start_grouping_operation(app, relative, submitted_digest):
    preview = revalidate_grouping(relative, submitted_digest)
    lease = acquire_intake_operation({
        "source_relpath": preview["browser"]["path"],
        "proposed_result_name": preview["result_name"],
        "proposal_digest": preview["digest"],
        "source_images": len(preview["mappings"]),
        "group_count": len(preview["groups"]),
    })
    progress = _Progress(lease.id, len(preview["mappings"]))
    progress.persist()

    def target():
        with app.app_context():
            execute_grouping_operation(lease, relative, submitted_digest)

    thread = threading.Thread(target=target, name=f"intake-group-{lease.id[:8]}", daemon=True)
    try:
        thread.start()
    except Exception as error:
        finish_intake_operation(lease, status="failed", error=error, summary={"source_images": len(preview["mappings"]), "copied_images": 0, "failed_images": 1, "workflow_status": "failed"})
        raise
    return lease.id
