"""Safe, copy-first folder structure proposals for prepared Catalogue Intake results."""

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
import unicodedata
from pathlib import Path, PurePosixPath

from flask import current_app

from app.image_preparation import (
    INTAKE_STAGING_DIRECTORY,
    MAX_PREVIEW_FILES,
    PREPARED_DIRECTORY,
    _classify_entry,
    _decode_path,
    _join_rel,
    _ordered,
    _portable_parts,
    _prepared_result,
    _safe_folder_component,
    _safe_root,
    _within,
    browse_intake,
    configured_intake_root,
    intake_readiness,
    normalize_prefix,
    resolve_intake_folder,
)
from app.intake_warnings import bounded_warning_findings
from app.intake_grouping import (
    INTAKE_FOLDER_OPERATION_TYPE,
    GroupingRejected,
    IntakeOperationActive,
    _Progress,
    _cleanup_operation_staging,
    _copy_source_file,
    _operation_marker,
    _promote_prepared_result,
    acquire_intake_operation,
    cleanup_stale_staging,
    finish_intake_operation,
)
from app.intake_working_result import (
    WorkingResultRecoveryRequired,
    replace_working_result,
)
from app.utils.catalogue_paths import is_reserved_directory_name
from app.utils.operation_control import sanitize_operation_error


FOLDER_EDIT_STATUS = "image_renaming_required"
_WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.I)


class FolderProposalRejected(GroupingRejected):
    pass


def _issue(name, message, *, code, state="blocking"):
    return {"name": str(name)[:500], "message": message, "code": code, "state": state}


def _safe_component(value):
    decoded = _decode_path(value)
    component = _safe_folder_component(decoded)
    if component.endswith(".") or _WINDOWS_RESERVED.match(component):
        raise ValueError("Unsafe reserved folder name")
    return component


def _safe_folder_path(value):
    decoded = _decode_path(value)
    parts = _portable_parts(decoded)
    if not parts:
        raise ValueError("Folder path is empty")
    return PurePosixPath(*(_safe_component(part) for part in parts)).as_posix()


def _fold_path(value):
    return tuple(unicodedata.normalize("NFC", part).casefold() for part in PurePosixPath(value).parts)


def _file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_prepared_result(root, relative):
    parts = _portable_parts(relative)
    if len(parts) != 2 or parts[0] != PREPARED_DIRECTORY:
        raise ValueError("Select one completed result directly beneath Prepared")
    folder, selected = resolve_intake_folder(root, relative)
    return folder, selected


def _snapshot_prepared_result(root, relative):
    folder, selected = _resolve_prepared_result(root, relative)
    folders = []
    images = []
    issues = []
    stack = [(folder, ())]
    while stack:
        current, relative_parts = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda entry: _ordered(entry.name))
        except OSError as error:
            raise ValueError("Prepared result cannot be read safely") from error
        children = []
        for entry in entries:
            kind, entry_stat = _classify_entry(entry)
            entry_relative = _join_rel(*relative_parts, entry.name)
            if kind == "directory":
                children.append((entry, (*relative_parts, entry.name)))
                folders.append({"path": entry_relative, "name": entry.name})
                continue
            if kind == "image":
                if len(images) >= MAX_PREVIEW_FILES:
                    raise ValueError("Prepared result exceeds the supported image limit")
                images.append(
                    {
                        "path": entry_relative,
                        "name": entry.name,
                        "folder": _join_rel(*relative_parts),
                        "size": entry_stat.st_size,
                        "mtime_ns": entry_stat.st_mtime_ns,
                        "sha256": _file_hash(entry),
                    }
                )
                continue
            issues.append(
                _issue(
                    entry_relative,
                    "Prepared results may contain only safe folders and valid supported images.",
                    code=f"unsafe_{kind}",
                )
            )
        for child in reversed(children):
            stack.append(child)

    child_counts = {item["path"]: 0 for item in folders}
    image_counts = {item["path"]: 0 for item in folders}
    for item in folders:
        parent = PurePosixPath(item["path"]).parent.as_posix()
        if parent != "." and parent in child_counts:
            child_counts[parent] += 1
    for image in images:
        if image["folder"] in image_counts:
            image_counts[image["folder"]] += 1
        elif not image["folder"]:
            issues.append(_issue(image["path"], "Root-level images require a product folder before scanner validation.", code="root_image"))
    for item in folders:
        item["child_count"] = child_counts[item["path"]]
        item["image_count"] = image_counts[item["path"]]
        item["empty"] = not item["child_count"] and not item["image_count"]
    folders.sort(key=lambda item: tuple(_ordered(part) for part in PurePosixPath(item["path"]).parts))
    images.sort(key=lambda item: tuple(_ordered(part) for part in PurePosixPath(item["path"]).parts))
    return {
        "folder": folder,
        "selected": selected,
        "root_name": folder.name,
        "folders": folders,
        "images": images,
        "issues": issues,
    }


def _snapshot_identity(snapshot):
    payload = {
        "selected": snapshot["selected"],
        "folders": [
            {"path": item["path"], "empty": item["empty"]}
            for item in snapshot["folders"]
        ],
        "images": [
            {key: item[key] for key in ("path", "size", "sha256")}
            for item in snapshot["images"]
        ],
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _canonical_spec(snapshot, spec):
    value = spec if isinstance(spec, dict) else {}
    raw_root = value.get("root_name", snapshot["root_name"])
    issues = []
    normalised = []
    try:
        root_name = _safe_component(raw_root)
    except ValueError:
        root_name = snapshot["root_name"]
        issues.append(_issue(raw_root, "Collection result name is unsafe or empty.", code="unsafe_root"))
    if str(raw_root) != root_name:
        normalised.append({"entered": str(raw_root), "normalised": root_name})

    raw_renames = value.get("renames") if isinstance(value.get("renames"), dict) else {}
    remove_values = value.get("remove_empty") if isinstance(value.get("remove_empty"), list) else []
    remove_set = {str(item) for item in remove_values}
    existing = {item["path"]: item for item in snapshot["folders"]}
    unknown = sorted((set(raw_renames) | remove_set) - set(existing), key=_ordered)
    for path in unknown:
        issues.append(_issue(path, "Folder edit refers to an unknown prepared folder.", code="unknown_folder"))

    renames = {}
    removed = []
    for current, folder in existing.items():
        if current in remove_set:
            if folder["empty"]:
                removed.append(current)
                continue
            issues.append(_issue(current, "Only an explicitly selected empty folder can be removed.", code="non_empty_remove"))
        entered = raw_renames.get(current, current)
        try:
            proposed = _safe_folder_path(entered)
        except ValueError:
            proposed = current
            issues.append(_issue(entered, "Folder path contains an unsafe or empty component.", code="unsafe_folder"))
        if str(entered) != proposed:
            normalised.append({"entered": str(entered), "normalised": proposed})
        renames[current] = proposed

    raw_created = value.get("created") if isinstance(value.get("created"), list) else []
    created = []
    for entered in raw_created[:200]:
        try:
            proposed = _safe_folder_path(entered)
        except ValueError:
            issues.append(_issue(entered, "New folder path contains an unsafe or empty component.", code="unsafe_created"))
            continue
        if str(entered) != proposed:
            normalised.append({"entered": str(entered), "normalised": proposed})
        created.append(proposed)
    created = sorted(dict.fromkeys(created), key=lambda item: tuple(_ordered(part) for part in PurePosixPath(item).parts))

    raw_prefix = str(value.get("preview_prefix") or "preview")[:128]
    try:
        preview_prefix = normalize_prefix(raw_prefix)
    except ValueError:
        preview_prefix = "preview"
        issues.append(_issue(raw_prefix, "Temporary filename preview prefix is unsafe.", code="unsafe_prefix"))
    return {
        "root_name": root_name,
        "entered_root_name": str(raw_root),
        "renames": renames,
        "created": created,
        "remove_empty": sorted(removed, key=_ordered),
        "preview_prefix": preview_prefix,
        "entered_preview_prefix": raw_prefix,
        "normalised": normalised,
    }, issues


def _proposal_issues(snapshot, canonical):
    issues = []
    final_paths = list(canonical["renames"].values()) + list(canonical["created"])
    by_folded = {}
    for path in final_paths:
        by_folded.setdefault(_fold_path(path), []).append(path)
    for paths in by_folded.values():
        if len(paths) > 1:
            issues.append(_issue(", ".join(paths), "Proposed sibling folders collide case-insensitively or after Unicode normalisation.", code="folder_collision"))

    final_keys = {_fold_path(path) for path in final_paths}
    for path in final_paths:
        parent = PurePosixPath(path).parent
        if parent.as_posix() != "." and _fold_path(parent.as_posix()) not in final_keys:
            issues.append(_issue(path, "Every proposed nested folder must have an explicit proposed parent folder.", code="missing_parent"))

    root_parent_variants = [path for path in final_paths if len(PurePosixPath(path).parts) == 1 and is_reserved_directory_name(path)]
    if len(root_parent_variants) > 1:
        issues.append(_issue(", ".join(root_parent_variants), "Only one collection-root Parent case variant is allowed.", code="duplicate_parent"))

    image_folders = [canonical["renames"].get(image["folder"], image["folder"]) for image in snapshot["images"]]
    non_parent_depths = [len(PurePosixPath(path).parts) for path in image_folders if path and not (len(PurePosixPath(path).parts) == 1 and is_reserved_directory_name(path))]
    if any(depth > 2 for depth in non_parent_depths):
        issues.append(_issue("Proposed hierarchy", "Folder depth beyond two levels cannot be confirmed against current scanner rules.", code="unsupported_depth"))
    if any(depth == 2 for depth in non_parent_depths) and not root_parent_variants:
        issues.append(_issue("Parent", "This variable-looking hierarchy has no Parent folder. Metadata is required to determine whether one is needed.", code="parent_missing", state="warning"))
    return issues


def _future_mappings(snapshot, canonical):
    rows = []
    sequence_by_folder = {}
    for image in snapshot["images"]:
        proposed_folder = canonical["renames"].get(image["folder"], image["folder"])
        sequence_by_folder[proposed_folder] = sequence_by_folder.get(proposed_folder, 0) + 1
        sequence = sequence_by_folder[proposed_folder]
        components = PurePosixPath(proposed_folder).parts if proposed_folder else ()
        filename_components = (canonical["root_name"],) if len(components) == 1 and is_reserved_directory_name(components[0]) else components
        safe_components = [re.sub(r"\s+", "_", unicodedata.normalize("NFC", part).strip()).lower() for part in filename_components]
        component = "_".join(safe_components) or "unassigned"
        future = f"{canonical['preview_prefix']}_{component}_{sequence:02d}{Path(image['name']).suffix.lower()}"
        rows.append(
            {
                "source_relpath": image["path"],
                "current_filename": image["name"],
                "current_folder": image["folder"],
                "proposed_folder": proposed_folder,
                "future_filename": future,
                "hierarchy_components": list(components),
                "size": image["size"],
                "mtime_ns": image["mtime_ns"],
                "sha256": image["sha256"],
                "state": "preview",
            }
        )
    collisions = {}
    for row in rows:
        collisions.setdefault(unicodedata.normalize("NFC", row["future_filename"]).casefold(), []).append(row)
    issues = []
    for collision_rows in collisions.values():
        if len(collision_rows) > 1:
            for row in collision_rows:
                row["state"] = "blocking"
            issues.append(_issue(", ".join(row["future_filename"] for row in collision_rows), "Future image filenames would collide when flattened.", code="future_filename_collision"))
    return rows, issues


def _tree_rows(snapshot, canonical):
    current = []
    proposed = []
    image_counts = {item["path"]: item["image_count"] for item in snapshot["folders"]}
    child_counts = {item["path"]: item["child_count"] for item in snapshot["folders"]}
    for item in snapshot["folders"]:
        role = "Parent product imagery" if len(PurePosixPath(item["path"]).parts) == 1 and is_reserved_directory_name(item["path"]) else "Prepared image folder"
        current.append({**item, "role": role})
        if item["path"] not in canonical["remove_empty"]:
            path = canonical["renames"][item["path"]]
            proposed.append({"path": path, "current_path": item["path"], "image_count": image_counts[item["path"]], "child_count": child_counts[item["path"]], "role": "Parent product imagery" if len(PurePosixPath(path).parts) == 1 and is_reserved_directory_name(path) else "Prepared image folder", "created": False})
    for path in canonical["created"]:
        proposed.append({"path": path, "current_path": None, "image_count": 0, "child_count": 0, "role": "Parent product imagery" if len(PurePosixPath(path).parts) == 1 and is_reserved_directory_name(path) else "New empty folder", "created": True})
    proposed.sort(key=lambda item: tuple(_ordered(part) for part in PurePosixPath(item["path"]).parts))
    return current, proposed


def folder_editor_preview(root, relative, spec=None):
    root = _safe_root(Path(root))
    snapshot = _snapshot_prepared_result(root, relative)
    canonical, issues = _canonical_spec(snapshot, spec)
    if canonical["root_name"] != snapshot["root_name"]:
        issues.append(
            _issue(
                canonical["root_name"],
                "The working Prepared result keeps its visible name throughout preparation.",
                code="working_result_identity",
            )
        )
        canonical["root_name"] = snapshot["root_name"]
    issues = [*snapshot["issues"], *issues, *_proposal_issues(snapshot, canonical)]
    mappings, filename_issues = _future_mappings(snapshot, canonical)
    issues.extend(filename_issues)
    current_tree, proposed_tree = _tree_rows(snapshot, canonical)
    result_name = snapshot["root_name"]

    rename_count = sum(1 for current, proposed in canonical["renames"].items() if current != proposed)
    parent_before = [item["path"] for item in current_tree if len(PurePosixPath(item["path"]).parts) == 1 and is_reserved_directory_name(item["path"])]
    parent_after = [item["path"] for item in proposed_tree if len(PurePosixPath(item["path"]).parts) == 1 and is_reserved_directory_name(item["path"])]
    parent_change = (parent_before[0] if parent_before else None) != (parent_after[0] if parent_after else None)
    blocking = [issue for issue in issues if issue["state"] == "blocking"]
    digest_spec = {
        "root_name": canonical["root_name"],
        "renames": canonical["renames"],
        "created": canonical["created"],
        "remove_empty": canonical["remove_empty"],
        "preview_prefix": canonical["preview_prefix"],
    }
    digest_payload = {
        "kind": "folder_edit",
        "selected": snapshot["selected"],
        "result_name": result_name,
        "source_identity": _snapshot_identity(snapshot),
        "source_folders": [{"path": item["path"], "empty": item["empty"]} for item in snapshot["folders"]],
        "source_images": [{key: item[key] for key in ("path", "size", "mtime_ns", "sha256")} for item in snapshot["images"]],
        "canonical": digest_spec,
        "proposed_tree": [{key: item.get(key) for key in ("path", "current_path", "created")} for item in proposed_tree],
        "issues": [{key: item.get(key) for key in ("code", "name", "state")} for item in issues],
    }
    digest = hashlib.sha256(json.dumps(digest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    depth = max((len(PurePosixPath(row["proposed_folder"]).parts) for row in mappings if row["proposed_folder"]), default=0)
    if depth <= 1:
        compatibility = "Structure appears Simple or Variable Collection-compatible based on folders only."
    elif depth == 2:
        compatibility = "Structure appears Single Variable-compatible based on folders only."
    else:
        compatibility = "The proposed hierarchy is ambiguous or deeper than supported folder-only validation."
    return {
        "kind": "folder_edit",
        "preview_only": True,
        "browser": browse_intake(root, snapshot["selected"]),
        "source": snapshot["selected"],
        "source_name": snapshot["root_name"],
        "result_name": result_name,
        "proposed_result": _join_rel(PREPARED_DIRECTORY, result_name),
        "source_identity": _snapshot_identity(snapshot),
        "canonical_spec": canonical,
        "spec_json": json.dumps(digest_spec, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        "current_tree": current_tree,
        "proposed_tree": proposed_tree,
        "mappings": mappings,
        "issues": sorted(issues, key=lambda item: (_ordered(item["name"]), item["code"])),
        "blockers": [issue["message"] for issue in blocking],
        "digest": digest,
        "ready": bool(snapshot["images"]) and not blocking,
        "counts": {"renamed": rename_count + int(canonical["root_name"] != snapshot["root_name"]), "created": len(canonical["created"]), "removed_empty": len(canonical["remove_empty"]), "images": len(snapshot["images"]), "warnings": len(issues) - len(blocking)},
        "parent": {"before": parent_before[0] if parent_before else None, "after": parent_after[0] if parent_after else None, "changed": parent_change},
        "compatibility": {"label": compatibility, "detail": "Final image-attribute validation requires product_info.json metadata; no scanner has been run."},
    }


def parse_folder_spec(value):
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("Folder proposal is invalid") from error
    if not isinstance(parsed, dict):
        raise ValueError("Folder proposal is invalid")
    return parsed


def revalidate_folder_proposal(relative, spec, submitted_digest):
    readiness = intake_readiness()
    if not readiness["readable"]:
        raise FolderProposalRejected("Catalogue Intake is unavailable")
    if not readiness["writable"]:
        raise FolderProposalRejected("Catalogue Intake must be mounted read/write before folder changes can be applied")
    preview = folder_editor_preview(configured_intake_root(), relative, spec)
    if not hmac.compare_digest(preview["digest"], str(submitted_digest or "")):
        raise FolderProposalRejected("The source or folder proposal changed after preview. Review the updated proposal before continuing.")
    if not preview["ready"]:
        raise FolderProposalRejected("Folder changes cannot be applied until all blocking conflicts are corrected.")
    return preview


def _verify_result(stage_result, preview):
    expected_files = {row["proposed_folder"] + "/" + row["current_filename"] if row["proposed_folder"] else row["current_filename"]: row for row in preview["mappings"]}
    expected_folders = {item["path"] for item in preview["proposed_tree"]}
    found_files = set()
    found_folders = set()
    for current, directory_names, filenames in os.walk(stage_result, followlinks=False):
        current_path = Path(current)
        for name in directory_names:
            candidate = current_path / name
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise FolderProposalRejected("The staged folder proposal contains an unsafe directory")
            found_folders.add(candidate.relative_to(stage_result).as_posix())
        for name in filenames:
            candidate = current_path / name
            relative = candidate.relative_to(stage_result).as_posix()
            expected = expected_files.get(relative)
            if expected is None or relative in found_files:
                raise FolderProposalRejected("The staged folder proposal contains an unexpected image")
            kind, info = _classify_entry(candidate)
            if kind != "image" or info.st_size != expected["size"] or _file_hash(candidate) != expected["sha256"]:
                raise FolderProposalRejected("A staged image failed byte-for-byte verification")
            found_files.add(relative)
    if found_files != set(expected_files) or found_folders != expected_folders:
        raise FolderProposalRejected("The staged folder proposal is incomplete")


def _notify_completed(progress, summary, elapsed):
    from app.utils.discord import notify_intake_folder_edit_completed

    try:
        result = notify_intake_folder_edit_completed(
            source_name=PurePosixPath(summary["source_relpath"]).name,
            result_name=summary["result_name"],
            renamed=summary["renamed_folders"],
            created=summary["created_folders"],
            warnings=summary["warnings"],
            elapsed_text=f"{elapsed:.1f}s",
            operation_id=progress.operation_id,
        )
    except Exception:
        result = (False, "delivery failed")
    progress.record_discord("completed", result)


def _notify_failed(progress, relative, error):
    from app.utils.discord import notify_intake_folder_edit_failed

    try:
        result = notify_intake_folder_edit_failed(source_name=PurePosixPath(relative).name, error_text=sanitize_operation_error(error), operation_id=progress.operation_id)
    except Exception:
        result = (False, "delivery failed")
    progress.record_discord("failed", result)


def execute_folder_edit_operation(lease, relative, spec, submitted_digest):
    root = Path(configured_intake_root()).resolve(strict=True)
    progress = _Progress(lease.id, 0)
    started = time.monotonic()
    operation_dir = root / INTAKE_STAGING_DIRECTORY / lease.id
    replacement_complete = False
    preview = None
    summary = None
    try:
        progress.update("revalidating_folder_proposal", "Revalidating folder proposal")
        preview = revalidate_folder_proposal(relative, spec, submitted_digest)
        progress.total = len(preview["mappings"])
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
            raise FolderProposalRejected("Private Catalogue Intake staging is unsafe")
        operation_dir.mkdir(mode=0o700)
        _operation_marker(operation_dir, lease.id)
        stage_source = operation_dir / "source"
        stage_source.mkdir(mode=0o700)

        source_folder, _canonical = _resolve_prepared_result(root, relative)
        progress.update("copying_grouped_result", "Copying grouped result to private staging")
        for folder in preview["current_tree"]:
            (stage_source / folder["path"]).mkdir(parents=True, exist_ok=True)
        source_by_path = {item["path"]: item for item in _snapshot_prepared_result(root, relative)["images"]}
        for mapping in preview["mappings"]:
            expected = source_by_path[mapping["source_relpath"]]
            _copy_source_file(source_folder / mapping["source_relpath"], stage_source / mapping["source_relpath"], expected)
            progress.copied += 1
            progress.message = f"Images copied: {progress.copied} / {progress.total}"
            progress.current_item = mapping["current_folder"]
            progress.persist_copy_progress()
        progress.update("copying_grouped_result", f"Images copied: {progress.copied} / {progress.total}")

        progress.update("applying_folder_changes", "Applying folder changes inside private staging")
        stage_result = operation_dir / "result"
        stage_result.mkdir(mode=0o700)
        for folder in preview["proposed_tree"]:
            (stage_result / folder["path"]).mkdir(parents=True, exist_ok=False)
        for mapping in preview["mappings"]:
            destination = stage_result / mapping["proposed_folder"] / mapping["current_filename"]
            if destination.exists():
                raise FolderProposalRejected("Folder proposal would merge or overwrite an image")
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.rename(stage_source / mapping["source_relpath"], destination)
        shutil.rmtree(stage_source)

        progress.update("verifying_proposed_structure", "Verifying proposed structure and unchanged image bytes")
        _verify_result(stage_result, preview)
        if folder_editor_preview(root, relative, spec)["digest"] != submitted_digest:
            raise FolderProposalRejected("The source or folder proposal changed during preparation")
        progress.update("checking_scanner_compatibility", "Checking folder-only scanner compatibility")

        final_destination = root / PREPARED_DIRECTORY / preview["result_name"]

        def verify_restored(restored):
            del restored
            restored_snapshot = _snapshot_prepared_result(root, relative)
            if _snapshot_identity(restored_snapshot) != preview["source_identity"]:
                raise WorkingResultRecoveryRequired(
                    "The restored Prepared result could not be verified"
                )

        replacement_messages = {
            "moving_current_result_to_rollback": "Moving the current working result to protected rollback storage",
            "promoting_result": "Promoting the verified folder structure under the same working-result name",
            "verifying_promoted_result": "Verifying the promoted folder structure",
            "verifying_restored_result": "Verifying the automatically restored working result",
            "removing_rollback": "Removing rollback after successful promoted-result verification",
        }
        replacement = replace_working_result(
            root=root,
            operation_id=lease.id,
            staged_result=stage_result,
            visible_result=final_destination,
            verify_promoted=lambda promoted: _verify_result(promoted, preview),
            verify_restored=verify_restored,
            failed_result_parent=operation_dir,
            on_stage=lambda stage: progress.update(stage, replacement_messages[stage]),
        )
        replacement_complete = True

        progress.update("cleaning_staging", "Cleaning the completed operation staging wrapper")
        marker = operation_dir / ".operation-owner"
        marker.unlink()
        operation_dir.rmdir()
        summary = {
            "source_relpath": relative,
            "result_name": preview["result_name"],
            "prepared_relpath": _join_rel(PREPARED_DIRECTORY, preview["result_name"]),
            "proposal_digest": preview["digest"],
            "source_images": len(preview["mappings"]),
            "copied_images": progress.copied,
            "failed_images": 0,
            "renamed_folders": preview["counts"]["renamed"],
            "created_folders": preview["counts"]["created"],
            "removed_empty_folders": preview["counts"]["removed_empty"],
            "parent_changed": preview["parent"]["changed"],
            "warnings": progress.warnings,
            "blocking_errors": 0,
            "warning_findings": bounded_warning_findings(preview["issues"]),
            "workflow_status": FOLDER_EDIT_STATUS,
            "source_identity": preview["source_identity"],
            "result_identity": _snapshot_identity(
                _snapshot_prepared_result(root, preview["proposed_result"])
            ),
            **replacement,
            "duration_seconds": round(time.monotonic() - started, 3),
        }
        _notify_completed(progress, summary, time.monotonic() - started)
        progress.stage = "completed_image_renaming_required"
        progress.message = "Folder structure confirmed — image renaming required"
        progress.current_item = summary["prepared_relpath"]
        progress.persist(summary=summary, status="partial" if progress.warnings else "succeeded")
        finish_intake_operation(lease, status="partial" if progress.warnings else "succeeded", summary=summary)
    except Exception as error:
        cleanup_warning = None
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
            cleanup_warning = "Operation staging cleanup requires review"
        progress.failures = 1
        progress.warnings += int(bool(cleanup_warning))
        failed_stage = progress.stage
        if isinstance(error, GroupingRejected):
            safe_failure = str(error)
        elif isinstance(error, PermissionError):
            safe_failure = "Catalogue Intake permissions prevented folder-result promotion."
        elif isinstance(error, OSError):
            safe_failure = "The mounted Catalogue Intake filesystem could not complete the folder operation safely."
        else:
            safe_failure = sanitize_operation_error(error)
        progress.stage = "failed"
        progress.message = safe_failure
        progress.logs.append({"sequence": len(progress.logs) + 1, "severity": "info" if staging_cleanup == "cleaned" else "warning", "line": "Operation-owned staging was cleaned" if staging_cleanup == "cleaned" else "Operation staging was retained for controlled review"})
        _notify_failed(progress, relative, safe_failure)
        recovery_state = (
            "manual_recovery_required"
            if isinstance(error, WorkingResultRecoveryRequired)
            else "restored"
            if failed_stage in {"promoting_result", "verifying_promoted_result", "moving_current_result_to_rollback", "verifying_restored_result"}
            else "not_required"
        )
        failed_summary = {"source_relpath": relative, "prepared_relpath": preview["proposed_result"] if preview else None, "source_images": progress.total, "copied_images": progress.copied, "failed_images": 1, "warnings": progress.warnings, "workflow_status": "failed", "failed_stage": failed_stage, "staging_cleanup": staging_cleanup, "recovery_state": recovery_state}
        progress.persist(summary=failed_summary, status="failed")
        finish_intake_operation(lease, status="failed", error=safe_failure, summary=failed_summary)


def start_folder_edit_operation(app, relative, spec, submitted_digest):
    preview = revalidate_folder_proposal(relative, spec, submitted_digest)
    lease = acquire_intake_operation(
        {"source_relpath": preview["source"], "proposed_result_name": preview["result_name"], "proposal_digest": preview["digest"], "source_images": len(preview["mappings"]), "group_count": len(preview["proposed_tree"])},
        operation_type=INTAKE_FOLDER_OPERATION_TYPE,
    )
    progress = _Progress(lease.id, len(preview["mappings"]))
    progress.stage = "revalidating_folder_proposal"
    progress.message = "Revalidating folder proposal"
    progress.persist()

    def target():
        with app.app_context():
            execute_folder_edit_operation(lease, relative, spec, submitted_digest)

    thread = threading.Thread(target=target, name=f"intake-folders-{lease.id[:8]}", daemon=True)
    try:
        thread.start()
    except Exception as error:
        finish_intake_operation(lease, status="failed", error=error, summary={"source_images": len(preview["mappings"]), "copied_images": 0, "failed_images": 1, "workflow_status": "failed"})
        raise
    return lease.id
