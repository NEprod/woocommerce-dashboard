"""Recoverable same-name replacement for one visible Prepared working result."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from app.image_preparation import PREPARED_DIRECTORY, _within
from app.intake_grouping import (
    GroupingRejected,
    _operation_marker,
    _promote_prepared_result,
)


INTAKE_ROLLBACK_DIRECTORY = ".catalogue-intake-rollback"


class WorkingResultRecoveryRequired(GroupingRejected):
    """The original result could not be restored automatically."""


def _safe_private_root(root: Path, name: str) -> Path:
    private = root / name
    private.mkdir(mode=0o700, exist_ok=True)
    info = private.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise GroupingRejected("Private Catalogue Intake recovery storage is unsafe")
    return private


def _operation_rollback(root: Path, operation_id: str) -> Path:
    rollback_root = _safe_private_root(root, INTAKE_ROLLBACK_DIRECTORY)
    operation = rollback_root / operation_id
    if operation.exists() or operation.is_symlink():
        raise GroupingRejected("Private Catalogue Intake rollback storage is already in use")
    operation.mkdir(mode=0o700)
    _operation_marker(operation, operation_id)
    return operation


def _remove_owned_rollback(operation: Path, operation_id: str) -> None:
    marker = operation / ".operation-owner"
    try:
        owned = (
            operation.name == operation_id
            and stat.S_ISREG(marker.lstat().st_mode)
            and marker.read_text(encoding="ascii") == operation_id
        )
    except (OSError, UnicodeError):
        owned = False
    if not owned:
        raise GroupingRejected("Private Catalogue Intake rollback ownership could not be verified")
    shutil.rmtree(operation)


def replace_working_result(
    *,
    root,
    operation_id,
    staged_result,
    visible_result,
    verify_promoted,
    verify_restored,
    failed_result_parent,
    on_stage=None,
):
    """Swap a verified staged tree into the same visible identity with rollback.

    Neither promotion nor restoration uses overwrite semantics.  If promoted
    verification fails, the bad promoted tree is moved aside inside the
    operation-owned staging wrapper before the original is restored.
    """

    root = Path(root).resolve(strict=True)
    prepared_root = root / PREPARED_DIRECTORY
    staged_result = Path(staged_result)
    visible_result = Path(visible_result)
    failed_result_parent = Path(failed_result_parent)
    if not _within(visible_result, prepared_root) or visible_result.parent != prepared_root:
        raise GroupingRejected("Visible Prepared result identity is invalid")
    if not visible_result.exists() or visible_result.is_symlink():
        raise GroupingRejected("The selected Prepared working result is unavailable")
    if not staged_result.exists() or staged_result.is_symlink():
        raise GroupingRejected("The verified staged result is unavailable")
    if visible_result.stat().st_dev != staged_result.stat().st_dev:
        raise GroupingRejected("Catalogue Intake staging and Prepared must share one filesystem")

    rollback_operation = _operation_rollback(root, operation_id)
    rollback_result = rollback_operation / "result"
    promoted = False
    stage = on_stage or (lambda _stage: None)
    try:
        stage("moving_current_result_to_rollback")
        os.rename(visible_result, rollback_result)
        stage("promoting_result")
        promotion = _promote_prepared_result(staged_result, visible_result)
        promoted = True
        stage("verifying_promoted_result")
        verify_promoted(visible_result)
    except Exception as original_error:
        try:
            if promoted and visible_result.exists():
                failed = failed_result_parent / "failed-promoted-result"
                if failed.exists() or failed.is_symlink():
                    raise WorkingResultRecoveryRequired(
                        "The failed promoted result could not be isolated for recovery"
                    )
                os.rename(visible_result, failed)
            if not visible_result.exists() and rollback_result.exists():
                _promote_prepared_result(rollback_result, visible_result)
            stage("verifying_restored_result")
            verify_restored(visible_result)
        except Exception as restore_error:
            raise WorkingResultRecoveryRequired(
                "The original Prepared result requires controlled recovery"
            ) from restore_error
        try:
            _remove_owned_rollback(rollback_operation, operation_id)
        except Exception:
            pass
        raise original_error

    stage("removing_rollback")
    _remove_owned_rollback(rollback_operation, operation_id)
    return {
        "promotion_strategy": promotion["strategy"],
        "promotion_fallback_reason": promotion["fallback_reason"],
        "rollback_state": "removed_after_verification",
        "recovery_state": "none",
    }
