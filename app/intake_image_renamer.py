"""Safe same-name image renaming for a folder-confirmed Prepared result."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import threading
import time
from pathlib import Path, PurePosixPath

from app import db
from app.image_preparation import (
    INTAKE_STAGING_DIRECTORY,
    PREPARED_DIRECTORY,
    _classify_entry,
    _join_rel,
    _ordered,
    _portable_parts,
    configured_intake_root,
    intake_readiness,
    rename_preview,
)
from app.intake_folder_editor import (
    _file_hash,
    _resolve_prepared_result,
    _snapshot_identity,
    _snapshot_prepared_result,
)
from app.intake_grouping import (
    INTAKE_RENAME_OPERATION_TYPE,
    GroupingRejected,
    _Progress,
    _cleanup_operation_staging,
    _copy_source_file,
    _operation_marker,
    acquire_intake_operation,
    cleanup_stale_staging,
    finish_intake_operation,
)
from app.intake_warnings import bounded_warning_findings
from app.intake_working_result import (
    WorkingResultRecoveryRequired,
    replace_working_result,
)
from app.models import CatalogueOperation
from app.utils.operation_control import sanitize_operation_error


RENAME_STATUS = "metadata_required"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class RenameProposalRejected(GroupingRejected):
    pass


def _row_scope(row):
    try:
        value = json.loads(row.scope or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}, {}
    if not isinstance(value, dict):
        return {}, {}
    summary = value.get("operation_summary")
    return value, summary if isinstance(summary, dict) else {}


def _eligible_folder_operation(selected):
    rows = (
        CatalogueOperation.query.filter(
            CatalogueOperation.operation_type.in_(
                {"intake_folder_edit", INTAKE_RENAME_OPERATION_TYPE}
            )
        )
        .filter(CatalogueOperation.status.in_({"succeeded", "partial"}))
        .order_by(CatalogueOperation.finished_at.desc(), CatalogueOperation.id.desc())
        .all()
    )
    for row in rows:
        _scope, summary = _row_scope(row)
        if summary.get("prepared_relpath") != selected:
            continue
        if (
            row.operation_type == "intake_folder_edit"
            and summary.get("workflow_status") == "image_renaming_required"
        ):
            return row, summary
        return None, None
    return None, None


def eligible_image_rename_results(root):
    """Return only currently folder-confirmed direct Prepared working results."""

    root = Path(root).resolve(strict=True)
    prepared = root / PREPARED_DIRECTORY
    if not prepared.is_dir() or prepared.is_symlink():
        return []
    results = []
    for entry in sorted(prepared.iterdir(), key=lambda value: _ordered(value.name)):
        try:
            info = entry.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            continue
        selected = _join_rel(PREPARED_DIRECTORY, entry.name)
        operation, summary = _eligible_folder_operation(selected)
        if operation is not None:
            results.append(
                {
                    "name": entry.name,
                    "path": selected,
                    "operation_id": operation.id,
                    "workflow_status": summary.get("workflow_status"),
                }
            )
    return results


def _later_dependency_exists(predecessor, ancestor_operation):
    rows = CatalogueOperation.query.filter(
        CatalogueOperation.started_at >= ancestor_operation.started_at
    ).all()
    for row in rows:
        if row.id == ancestor_operation.id:
            continue
        scope, summary = _row_scope(row)
        if scope.get("source_relpath") == predecessor or summary.get("source_relpath") == predecessor:
            return True
    return False


def _lineage(root, selected, operation, summary, current_identity):
    predecessor = str(summary.get("source_relpath") or "")
    result_identity = str(summary.get("result_identity") or "")
    source_identity = str(summary.get("source_identity") or "")
    base = {
        "state": "unavailable",
        "predecessor": None,
        "eligible": False,
        "reason": "No independently proven superseded predecessor is available.",
        "proof": None,
    }
    if not predecessor or predecessor == selected:
        return base
    try:
        parts = _portable_parts(predecessor)
    except ValueError:
        return base
    if len(parts) != 2 or parts[0] != PREPARED_DIRECTORY:
        return base
    if not (_DIGEST.fullmatch(result_identity) and _DIGEST.fullmatch(source_identity)):
        return {
            **base,
            "state": "uncertain",
            "predecessor": predecessor,
            "reason": "Direct ancestry exists, but legacy operation metadata cannot prove both trees are unchanged.",
        }
    if not hmac.compare_digest(result_identity, current_identity):
        return {
            **base,
            "state": "uncertain",
            "predecessor": predecessor,
            "reason": "The working result changed after the recorded folder operation.",
        }
    try:
        predecessor_snapshot = _snapshot_prepared_result(root, predecessor)
    except ValueError:
        return base
    if not hmac.compare_digest(source_identity, _snapshot_identity(predecessor_snapshot)):
        return {
            **base,
            "state": "uncertain",
            "predecessor": predecessor,
            "reason": "The recorded predecessor changed after the folder operation.",
        }
    if _later_dependency_exists(predecessor, operation):
        return {
            **base,
            "state": "protected",
            "predecessor": predecessor,
            "reason": "A later preparation operation still references the predecessor.",
        }
    proof = hashlib.sha256(
        json.dumps(
            {
                "operation": operation.id,
                "predecessor": predecessor,
                "source_identity": source_identity,
                "result_identity": result_identity,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "state": "proven",
        "predecessor": predecessor,
        "eligible": True,
        "reason": "Direct ancestry and both unchanged tree identities are proven.",
        "proof": proof,
        "source_identity": source_identity,
        "ancestor_operation": operation.id,
    }


def image_rename_preview(root, relative, prefix, *, remove_predecessor=None):
    root = Path(root).resolve(strict=True)
    folder, selected = _resolve_prepared_result(root, relative)
    operation, folder_summary = _eligible_folder_operation(selected)
    if operation is None:
        raise ValueError(
            "Only a result with status ‘Folder structure confirmed — image renaming required’ can be renamed."
        )
    base = rename_preview(root, selected, prefix)
    snapshot = _snapshot_prepared_result(root, selected)
    source_identity = _snapshot_identity(snapshot)
    snapshot_images = {item["path"]: item for item in snapshot["images"]}
    for mapping in base["mappings"]:
        local_path = PurePosixPath(
            *mapping["folder_parts"], mapping["source_filename"]
        ).as_posix()
        mapping["sha256"] = snapshot_images[local_path]["sha256"]
    lineage = _lineage(root, selected, operation, folder_summary, source_identity)
    cleanup_selected = bool(
        lineage["eligible"]
        and (remove_predecessor is None or remove_predecessor)
    )
    if remove_predecessor is True and not lineage["eligible"]:
        raise ValueError("The superseded predecessor cannot be removed without complete lineage proof")

    current_tree = [
        {
            "path": item["path"],
            "image_count": item["image_count"],
            "child_count": item["child_count"],
        }
        for item in snapshot["folders"]
    ]
    proposed_tree = list(current_tree)
    counts = {"parent": 0, "variation": 0, "other": 0}
    for mapping in base["mappings"]:
        kind = mapping["hierarchy_type"]
        counts[kind if kind in counts else "other"] += 1
    blocking = [issue for issue in base["issues"] if issue["state"] == "blocking"]
    mapping_warnings = sum(
        1 for mapping in base["mappings"] if mapping.get("state") == "warning"
    )
    digest_payload = {
        "kind": "image_rename",
        "selected": selected,
        "visible_name": folder.name,
        "workflow_operation": operation.id,
        "workflow_state": folder_summary.get("workflow_status"),
        "source_identity": source_identity,
        "prefix": base["normalised_prefix"],
        "base_digest": base["digest"],
        "mappings": [
            {
                key: row.get(key)
                for key in (
                    "source_relpath",
                    "size",
                    "mtime_ns",
                    "hierarchy_type",
                    "hierarchy_components",
                    "sequence",
                    "recommended_filename",
                    "destination_relpath",
                    "state",
                )
            }
            for row in base["mappings"]
        ],
        "lineage": {
            "state": lineage["state"],
            "predecessor": lineage["predecessor"],
            "proof": lineage["proof"],
            "cleanup_selected": cleanup_selected,
        },
    }
    digest = hashlib.sha256(
        json.dumps(
            digest_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        **base,
        "source": selected,
        "result_name": folder.name,
        "proposed_result": selected,
        "visible_result": selected,
        "source_identity": source_identity,
        "workflow_operation": operation.id,
        "workflow_status": folder_summary.get("workflow_status"),
        "current_tree": current_tree,
        "proposed_tree": proposed_tree,
        "lineage": lineage,
        "cleanup_selected": cleanup_selected,
        "counts": {
            "images": len(base["mappings"]),
            "parent": counts["parent"],
            "variation": counts["variation"],
            "other": counts["other"],
            "warnings": len(base["issues"]) - len(blocking) + mapping_warnings,
            "collisions": len(blocking),
        },
        "digest": digest,
        "ready": bool(base["mappings"]) and not blocking,
    }


def revalidate_rename_proposal(relative, prefix, digest, *, remove_predecessor=False):
    readiness = intake_readiness()
    if not readiness["readable"]:
        raise RenameProposalRejected("Catalogue Intake is unavailable")
    if not readiness["writable"]:
        raise RenameProposalRejected(
            "Catalogue Intake must be mounted read/write before images can be renamed"
        )
    preview = image_rename_preview(
        configured_intake_root(),
        relative,
        prefix,
        remove_predecessor=remove_predecessor,
    )
    if not hmac.compare_digest(preview["digest"], str(digest or "")):
        raise RenameProposalRejected(
            "The working result, prefix, or lineage changed after preview. Review a fresh proposal."
        )
    if not preview["ready"]:
        raise RenameProposalRejected(
            "Image renaming cannot begin until every blocking conflict is corrected."
        )
    return preview


def _verify_renamed_result(folder, preview):
    folder = Path(folder)
    expected = {}
    for row in preview["mappings"]:
        relative = PurePosixPath(*row["folder_parts"], row["recommended_filename"]).as_posix()
        expected[relative] = row
    expected_folders = {item["path"] for item in preview["proposed_tree"]}
    found = set()
    found_folders = set()
    for current, directory_names, filenames in os.walk(folder, followlinks=False):
        current_path = Path(current)
        for name in directory_names:
            candidate = current_path / name
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise RenameProposalRejected("The renamed result contains an unsafe directory")
            found_folders.add(candidate.relative_to(folder).as_posix())
        for name in filenames:
            candidate = current_path / name
            relative = candidate.relative_to(folder).as_posix()
            row = expected.get(relative)
            if row is None or relative in found:
                raise RenameProposalRejected("The renamed result contains an unexpected file")
            kind, info = _classify_entry(candidate)
            if (
                kind != "image"
                or info.st_size != row["size"]
                or _file_hash(candidate) != row["sha256"]
            ):
                raise RenameProposalRejected("A renamed image failed byte verification")
            found.add(relative)
    if found != set(expected) or found_folders != expected_folders:
        raise RenameProposalRejected("The renamed result does not match the approved preview")


def _remove_predecessor(root, preview):
    lineage = preview["lineage"]
    if not (preview["cleanup_selected"] and lineage["eligible"]):
        return "preserved"
    ancestor = db.session.get(CatalogueOperation, lineage.get("ancestor_operation"))
    if ancestor is None or _later_dependency_exists(lineage["predecessor"], ancestor):
        raise RenameProposalRejected("Predecessor lineage changed before cleanup")
    predecessor, _selected = _resolve_prepared_result(root, lineage["predecessor"])
    predecessor_snapshot = _snapshot_prepared_result(root, lineage["predecessor"])
    if not hmac.compare_digest(
        str(lineage.get("source_identity") or ""),
        _snapshot_identity(predecessor_snapshot),
    ):
        raise RenameProposalRejected("The superseded predecessor changed before cleanup")
    shutil.rmtree(predecessor)
    return "removed"


def _notify_completed(progress, summary, elapsed):
    from app.utils.discord import notify_intake_image_rename_completed

    try:
        result = notify_intake_image_rename_completed(
            result_name=summary["result_name"],
            prefix=summary["prefix"],
            renamed=summary["renamed_images"],
            parent=summary["parent_images"],
            variation=summary["variation_images"],
            warnings=summary["warnings"],
            predecessor=summary["predecessor_cleanup"],
            elapsed_text=f"{elapsed:.1f}s",
            operation_id=progress.operation_id,
        )
    except Exception:
        result = (False, "delivery failed")
    progress.record_discord("completed", result)


def _notify_failed(progress, relative, error):
    from app.utils.discord import notify_intake_image_rename_failed

    try:
        result = notify_intake_image_rename_failed(
            result_name=PurePosixPath(relative).name,
            error_text=sanitize_operation_error(error),
            operation_id=progress.operation_id,
        )
    except Exception:
        result = (False, "delivery failed")
    progress.record_discord("failed", result)


def execute_image_rename_operation(
    lease,
    relative,
    prefix,
    submitted_digest,
    *,
    remove_predecessor=False,
):
    root = Path(configured_intake_root()).resolve(strict=True)
    progress = _Progress(lease.id, 0)
    started = time.monotonic()
    operation_dir = root / INTAKE_STAGING_DIRECTORY / lease.id
    replacement_complete = False
    preview = None
    timings = {}
    try:
        stage_started = time.monotonic()
        progress.update("revalidating_rename_proposal", "Revalidating rename proposal")
        preview = revalidate_rename_proposal(
            relative,
            prefix,
            submitted_digest,
            remove_predecessor=remove_predecessor,
        )
        timings["preview_revalidation"] = round(time.monotonic() - stage_started, 4)
        progress.total = preview["counts"]["images"]
        progress.warnings = preview["counts"]["warnings"]
        progress.update("acquiring_intake_lock", "Dedicated Catalogue Intake mutation lock acquired")
        cleanup = cleanup_stale_staging(root, protected_ids={lease.id})
        for warning in cleanup["warnings"]:
            progress.warnings += 1
            progress.update("cleaning_staging", warning, severity="warning")

        staging_root = root / INTAKE_STAGING_DIRECTORY
        staging_root.mkdir(mode=0o700, exist_ok=True)
        staging_info = staging_root.lstat()
        if stat.S_ISLNK(staging_info.st_mode) or not stat.S_ISDIR(staging_info.st_mode):
            raise RenameProposalRejected("Private Catalogue Intake staging is unsafe")
        operation_dir.mkdir(mode=0o700)
        _operation_marker(operation_dir, lease.id)
        stage_result = operation_dir / "result"
        stage_result.mkdir(mode=0o700)

        source_folder, _selected = _resolve_prepared_result(root, relative)
        snapshot = _snapshot_prepared_result(root, relative)
        if _snapshot_identity(snapshot) != preview["source_identity"]:
            raise RenameProposalRejected("The working result changed before staging")
        progress.update("copying_working_result", "Copying working result to private staging")
        stage_started = time.monotonic()
        for item in snapshot["folders"]:
            (stage_result / item["path"]).mkdir(parents=True, exist_ok=True)
        by_path = {item["path"]: item for item in snapshot["images"]}
        for row in preview["mappings"]:
            source_relative = PurePosixPath(*row["folder_parts"], row["source_filename"]).as_posix()
            expected = by_path[source_relative]
            _copy_source_file(
                source_folder / source_relative,
                stage_result / source_relative,
                expected,
            )
            progress.copied += 1
            progress.message = f"Images copied: {progress.copied} / {progress.total}"
            progress.persist_copy_progress()
        timings["staging_copy"] = round(time.monotonic() - stage_started, 4)

        progress.update("assigning_temporary_filenames", "Assigning operation-owned temporary filenames")
        stage_started = time.monotonic()
        temporary = []
        for index, row in enumerate(preview["mappings"], start=1):
            directory = stage_result.joinpath(*row["folder_parts"])
            source = directory / row["source_filename"]
            target = directory / f".intake-{lease.id}-{index:06d}.tmp"
            if target.exists() or target.is_symlink():
                raise RenameProposalRejected("A temporary rename destination already exists")
            os.rename(source, target)
            temporary.append((target, directory / row["recommended_filename"]))
        timings["temporary_rename"] = round(time.monotonic() - stage_started, 4)

        progress.update("applying_final_filenames", "Applying final approved image filenames")
        stage_started = time.monotonic()
        for source, destination in temporary:
            if destination.exists() or destination.is_symlink():
                raise RenameProposalRejected("A final image destination already exists")
            os.rename(source, destination)
        timings["final_rename"] = round(time.monotonic() - stage_started, 4)

        progress.update("verifying_staged_result", "Verifying renamed staging tree and unchanged image bytes")
        stage_started = time.monotonic()
        _verify_renamed_result(stage_result, preview)
        if image_rename_preview(
            root,
            relative,
            prefix,
            remove_predecessor=remove_predecessor,
        )["digest"] != submitted_digest:
            raise RenameProposalRejected("The rename proposal changed during staging")
        timings["staged_verification"] = round(time.monotonic() - stage_started, 4)

        visible = root / relative

        def verify_restored(restored):
            del restored
            if _snapshot_identity(_snapshot_prepared_result(root, relative)) != preview["source_identity"]:
                raise WorkingResultRecoveryRequired("The restored working result could not be verified")

        replacement_messages = {
            "moving_current_result_to_rollback": "Moving current working result to protected rollback storage",
            "promoting_result": "Promoting renamed result under the same visible name",
            "verifying_promoted_result": "Verifying promoted renamed result",
            "verifying_restored_result": "Verifying the automatically restored working result",
            "removing_rollback": "Removing rollback after successful promoted-result verification",
        }
        stage_started = time.monotonic()
        replacement = replace_working_result(
            root=root,
            operation_id=lease.id,
            staged_result=stage_result,
            visible_result=visible,
            verify_promoted=lambda promoted: _verify_renamed_result(promoted, preview),
            verify_restored=verify_restored,
            failed_result_parent=operation_dir,
            on_stage=lambda stage: progress.update(stage, replacement_messages[stage]),
        )
        timings["visible_result_swap"] = round(time.monotonic() - stage_started, 4)
        replacement_complete = True

        predecessor_cleanup = "preserved"
        if remove_predecessor:
            stage_started = time.monotonic()
            progress.update("removing_superseded_predecessor", "Removing the explicitly approved proven predecessor")
            try:
                predecessor_cleanup = _remove_predecessor(root, preview)
            except Exception:
                predecessor_cleanup = "preserved_after_cleanup_warning"
                progress.warnings += 1
                progress.update(
                    "removing_superseded_predecessor",
                    "The renamed result is complete; its predecessor was preserved after a cleanup warning",
                    severity="warning",
                )
            timings["predecessor_cleanup"] = round(time.monotonic() - stage_started, 4)

        marker = operation_dir / ".operation-owner"
        marker.unlink()
        operation_dir.rmdir()
        summary = {
            "source_relpath": relative,
            "prepared_relpath": relative,
            "result_name": preview["result_name"],
            "prefix": preview["normalised_prefix"],
            "proposal_digest": preview["digest"],
            "source_images": progress.total,
            "copied_images": progress.copied,
            "renamed_images": progress.total,
            "failed_images": 0,
            "parent_images": preview["counts"]["parent"],
            "variation_images": preview["counts"]["variation"],
            "other_images": preview["counts"]["other"],
            "warnings": progress.warnings,
            "blocking_errors": 0,
            "warning_findings": bounded_warning_findings(preview["issues"]),
            "workflow_status": RENAME_STATUS,
            "source_identity": preview["source_identity"],
            "result_identity": _snapshot_identity(_snapshot_prepared_result(root, relative)),
            "predecessor_relpath": preview["lineage"]["predecessor"],
            "predecessor_cleanup_requested": bool(remove_predecessor),
            "predecessor_cleanup": predecessor_cleanup,
            "stage_timings_seconds": timings,
            **replacement,
            "duration_seconds": round(time.monotonic() - started, 3),
        }
        _notify_completed(progress, summary, time.monotonic() - started)
        progress.stage = "completed_metadata_required"
        progress.message = "Images renamed — metadata required"
        progress.current_item = relative
        status = "partial" if progress.warnings else "succeeded"
        progress.persist(summary=summary, status=status)
        finish_intake_operation(lease, status=status, summary=summary)
    except Exception as error:
        failed_stage = progress.stage
        try:
            staging_cleanup = (
                "not_found"
                if replacement_complete
                else "cleaned"
                if _cleanup_operation_staging(root, lease.id)
                else "not_found"
            )
        except OSError:
            staging_cleanup = "retained_for_review"
            progress.warnings += 1
        safe_failure = (
            str(error)
            if isinstance(error, GroupingRejected)
            else "The mounted Catalogue Intake filesystem could not complete image renaming safely."
            if isinstance(error, OSError)
            else sanitize_operation_error(error)
        )
        recovery_state = (
            "manual_recovery_required"
            if isinstance(error, WorkingResultRecoveryRequired)
            else "restored"
            if failed_stage in {"promoting_result", "verifying_promoted_result", "moving_current_result_to_rollback", "verifying_restored_result"}
            else "not_required"
        )
        progress.failures = 1
        progress.stage = "failed"
        progress.message = safe_failure
        progress.logs.append(
            {
                "sequence": len(progress.logs) + 1,
                "severity": "error",
                "line": "Image renaming failed without exposing a partial working result",
            }
        )
        _notify_failed(progress, relative, safe_failure)
        summary = {
            "source_relpath": relative,
            "prepared_relpath": relative,
            "source_images": progress.total,
            "copied_images": progress.copied,
            "failed_images": 1,
            "warnings": progress.warnings,
            "workflow_status": "failed",
            "failed_stage": failed_stage,
            "staging_cleanup": staging_cleanup,
            "recovery_state": recovery_state,
        }
        progress.persist(summary=summary, status="failed")
        finish_intake_operation(
            lease,
            status="failed",
            error=safe_failure,
            summary=summary,
        )


def start_image_rename_operation(
    app,
    relative,
    prefix,
    submitted_digest,
    *,
    remove_predecessor=False,
):
    preview = revalidate_rename_proposal(
        relative,
        prefix,
        submitted_digest,
        remove_predecessor=remove_predecessor,
    )
    lease = acquire_intake_operation(
        {
            "source_relpath": preview["source"],
            "proposed_result_name": preview["result_name"],
            "proposal_digest": preview["digest"],
            "source_images": preview["counts"]["images"],
            "group_count": len(preview["proposed_tree"]),
        },
        operation_type=INTAKE_RENAME_OPERATION_TYPE,
    )
    progress = _Progress(lease.id, preview["counts"]["images"])
    progress.stage = "revalidating_rename_proposal"
    progress.message = "Revalidating rename proposal"
    progress.persist()

    def target():
        with app.app_context():
            execute_image_rename_operation(
                lease,
                relative,
                prefix,
                submitted_digest,
                remove_predecessor=remove_predecessor,
            )

    thread = threading.Thread(
        target=target,
        name=f"intake-rename-{lease.id[:8]}",
        daemon=True,
    )
    try:
        thread.start()
    except Exception as error:
        finish_intake_operation(
            lease,
            status="failed",
            error=error,
            summary={
                "source_images": preview["counts"]["images"],
                "copied_images": 0,
                "failed_images": 1,
                "workflow_status": "failed",
            },
        )
        raise
    return lease.id
