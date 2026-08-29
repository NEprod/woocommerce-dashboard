"""Safe copy-first import for an existing structured Catalogue Intake folder."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import stat
import threading
import time
import unicodedata
from pathlib import Path, PurePosixPath

from app.image_preparation import (
    INTAKE_STAGING_DIRECTORY,
    MAX_PREVIEW_FILES,
    PREPARED_DIRECTORY,
    _classify_entry,
    _join_rel,
    _ordered,
    _prepared_result,
    configured_intake_root,
    intake_readiness,
    resolve_intake_folder,
)
from app.intake_folder_editor import _safe_component
from app.intake_grouping import (
    INTAKE_STRUCTURED_IMPORT_OPERATION_TYPE,
    GroupingRejected,
    _Progress,
    _cleanup_operation_staging,
    _operation_marker,
    _promote_prepared_result,
    acquire_intake_operation,
    cleanup_stale_staging,
    finish_intake_operation,
)
from app.intake_warnings import bounded_warning_findings
from app.intake_working_result import INTAKE_ROLLBACK_DIRECTORY
from app.utils.catalogue_paths import is_reserved_directory_name
from app.utils.operation_control import sanitize_operation_error


IMPORT_REVIEW = "review"
IMPORT_FINAL = "final"
IMPORT_MODES = {IMPORT_REVIEW, IMPORT_FINAL}
METADATA_NAME = "product_info.json"
PRIVATE_SOURCE_ROOTS = {
    PREPARED_DIRECTORY.casefold(),
    INTAKE_STAGING_DIRECTORY.casefold(),
    INTAKE_ROLLBACK_DIRECTORY.casefold(),
}


class StructuredImportRejected(GroupingRejected):
    pass


def _issue(name, message, *, code, category="structure", state="warning"):
    return {
        "name": str(name or "Selected source")[:500],
        "message": str(message)[:500],
        "code": str(code)[:80],
        "category": str(category)[:80],
        "state": state,
    }


def _hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_folder(root, relative):
    folder, selected = resolve_intake_folder(root, relative)
    parts = PurePosixPath(selected).parts if selected else ()
    if not parts:
        raise ValueError("Select one existing structured folder beneath Catalogue Intake")
    if parts[0].casefold() in PRIVATE_SOURCE_ROOTS or parts[0].startswith("."):
        raise ValueError("Prepared and private Catalogue Intake folders cannot be imported")
    return folder, selected


def _identity(info):
    return {
        "device": int(info.st_dev),
        "inode": int(info.st_ino),
        "size": int(info.st_size),
        "mtime_ns": int(info.st_mtime_ns),
    }


def _directory_findings(directories, mode):
    issues = []
    sibling_names = {}
    for directory in directories:
        path = PurePosixPath(directory["path"])
        parent = path.parent.as_posix()
        key = ("" if parent == "." else parent, unicodedata.normalize("NFC", path.name).casefold())
        sibling_names.setdefault(key, []).append(directory["path"])
    for paths in sibling_names.values():
        if len(paths) > 1:
            issues.append(_issue(", ".join(paths), "Sibling folders collide case-insensitively or after Unicode normalisation.", code="folder_collision", category="ambiguity", state="blocking"))

    root_parent = [item for item in directories if item["depth"] == 1 and is_reserved_directory_name(item["name"])]
    nested_parent = [item for item in directories if item["depth"] > 1 and is_reserved_directory_name(item["name"])]
    if len(root_parent) > 1:
        issues.append(_issue(", ".join(item["path"] for item in root_parent), "Multiple Parent case variants are ambiguous.", code="duplicate_parent", category="ambiguity", state="blocking"))
    for item in nested_parent:
        issues.append(_issue(item["path"], "Parent is scanner-reserved only at the collection root.", code="nested_parent", category="hierarchy", state="blocking" if mode == IMPORT_FINAL else "warning"))
    return issues, root_parent


def _snapshot(root, relative, mode):
    if mode not in IMPORT_MODES:
        raise ValueError("Select a supported structured-folder import mode")
    root = Path(root).resolve(strict=True)
    folder, selected = _source_folder(root, relative)
    try:
        root_info = folder.lstat()
        _safe_component(folder.name)
    except (OSError, ValueError) as error:
        raise ValueError("The selected structured-folder name is unsafe") from error

    directories = []
    files = []
    excluded = []
    issues = []
    metadata = []
    stack = [(folder, ())]
    inspected = 0
    while stack:
        current, relative_parts = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda item: _ordered(item.name))
        except OSError:
            issues.append(_issue(_join_rel(*relative_parts), "Directory could not be read.", code="unreadable_directory", category="unreadable", state="blocking"))
            continue
        children = []
        for entry in entries:
            inspected += 1
            if inspected > MAX_PREVIEW_FILES:
                raise ValueError("Structured folder exceeds the bounded preview limit")
            relative_path = _join_rel(*relative_parts, entry.name)
            kind, info = _classify_entry(entry)
            if kind == "directory":
                try:
                    _safe_component(entry.name)
                except ValueError:
                    issues.append(_issue(relative_path, "Folder name is unsafe for a Prepared result.", code="unsafe_folder_name", category="unsafe", state="blocking"))
                    continue
                record = {
                    "path": relative_path,
                    "name": entry.name,
                    "depth": len(relative_parts) + 1,
                    "identity": _identity(info),
                }
                directories.append(record)
                children.append((entry, (*relative_parts, entry.name)))
                continue
            if kind == "image":
                digest = _hash_file(entry)
                files.append({
                    "path": relative_path,
                    "name": entry.name,
                    "kind": "image",
                    "size": info.st_size,
                    "mtime_ns": info.st_mtime_ns,
                    "inode": info.st_ino,
                    "device": info.st_dev,
                    "sha256": digest,
                })
                continue
            if entry.name == METADATA_NAME and kind == "unsupported" and info is not None:
                try:
                    raw = entry.read_bytes()
                    parsed = json.loads(raw.decode("utf-8"))
                    valid = isinstance(parsed, dict)
                except (OSError, UnicodeError, ValueError):
                    try:
                        raw = entry.read_bytes()
                    except OSError:
                        raw = b""
                        issues.append(_issue(relative_path, "Existing product_info.json could not be read.", code="unreadable_metadata", category="unreadable", state="blocking"))
                    valid = False
                files.append({
                    "path": relative_path,
                    "name": entry.name,
                    "kind": "metadata",
                    "size": info.st_size,
                    "mtime_ns": info.st_mtime_ns,
                    "inode": info.st_ino,
                    "device": info.st_dev,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "valid_json": valid,
                })
                metadata.append({"path": relative_path, "valid": valid})
                if not valid:
                    issues.append(_issue(relative_path, "Existing product_info.json is malformed and will be preserved unchanged for later correction.", code="malformed_metadata", category="metadata"))
                continue
            excluded.append({"path": relative_path, "kind": kind})
            if kind in {"unsafe", "unreadable", "changed"}:
                issues.append(_issue(relative_path, "Unsafe or unreadable entries cannot be imported.", code=f"unsafe_{kind}", category=kind, state="blocking"))
            elif kind == "corrupt":
                issues.append(_issue(relative_path, "Corrupt image is excluded from the Prepared result.", code="corrupt_image", category="corrupt"))
            elif kind == "hidden":
                issues.append(_issue(relative_path, "Hidden or system entry is excluded from the Prepared result.", code="hidden_entry", category="hidden"))
            else:
                issues.append(_issue(relative_path, "Unsupported file is excluded from the Prepared result.", code="unsupported_entry", category="unsupported"))
        for child in reversed(children):
            stack.append(child)

    directories.sort(key=lambda item: tuple(_ordered(part) for part in PurePosixPath(item["path"]).parts))
    files.sort(key=lambda item: tuple(_ordered(part) for part in PurePosixPath(item["path"]).parts))
    excluded.sort(key=lambda item: tuple(_ordered(part) for part in PurePosixPath(item["path"]).parts))

    directory_issues, root_parent = _directory_findings(directories, mode)
    issues.extend(directory_issues)

    copied_paths = [item["path"] for item in directories] + [item["path"] for item in files]
    for directory in directories:
        prefix = f"{directory['path']}/"
        if not any(path.startswith(prefix) for path in copied_paths if path != directory["path"]):
            issues.append(_issue(directory["path"], "Empty folder is preserved for later structure review.", code="empty_directory", category="hierarchy"))
        parts = PurePosixPath(directory["path"]).parts
        under_root_parent = bool(parts and is_reserved_directory_name(parts[0]))
        if directory["depth"] > 2 and not under_root_parent:
            issues.append(_issue(directory["path"], "Folder depth beyond two levels cannot be confirmed against current scanner rules.", code="unsupported_depth", category="hierarchy", state="blocking" if mode == IMPORT_FINAL else "warning"))

    image_names = {}
    for item in files:
        if item["kind"] != "image":
            continue
        key = unicodedata.normalize("NFC", item["name"]).casefold()
        image_names.setdefault(key, []).append(item["path"])
    for paths in image_names.values():
        if len(paths) > 1:
            issues.append(_issue(", ".join(paths[:5]), "Repeated source filenames across folders will be resolved by the later image-renaming stage.", code="potential_flattened_collision", category="filename"))

    root_images = [item for item in files if item["kind"] == "image" and len(PurePosixPath(item["path"]).parts) == 1]
    if root_images:
        issues.append(_issue(", ".join(item["path"] for item in root_images[:5]), "Root-level images have ambiguous product ownership.", code="root_images", category="hierarchy", state="blocking" if mode == IMPORT_FINAL else "warning"))

    image_count = sum(item["kind"] == "image" for item in files)
    if not image_count:
        issues.append(_issue(selected, "No valid supported images were found in the selected structured folder.", code="no_images", category="content", state="blocking"))
    result_name, conflict = _prepared_result(root, folder.name)
    if conflict:
        issues.append(_issue(result_name, f"An unrelated Prepared result exists; this import will use {result_name}.", code="destination_suffix", category="destination"))

    counts = {
        "folders": len(directories),
        "images": image_count,
        "metadata": len(metadata),
        "hidden": sum(item["kind"] == "hidden" for item in excluded),
        "unsupported": sum(item["kind"] == "unsupported" for item in excluded),
        "corrupt": sum(item["kind"] == "corrupt" for item in excluded),
        "unsafe": sum(item["kind"] in {"unsafe", "changed"} for item in excluded),
        "unreadable": sum(item["kind"] == "unreadable" for item in excluded),
        "excluded": len(excluded),
    }
    maximum_depth = max((item["depth"] for item in directories), default=0)
    workflow_status = "folder_review_required" if mode == IMPORT_REVIEW else "image_renaming_required"
    payload = {
        "kind": "structured_import",
        "source": selected,
        "source_identity": _identity(root_info),
        "mode": mode,
        "result_name": result_name,
        "directories": [{"path": item["path"], "identity": item["identity"]} for item in directories],
        "files": [{key: item.get(key) for key in ("path", "kind", "size", "mtime_ns", "inode", "device", "sha256", "valid_json")} for item in files],
        "excluded": excluded,
        "issues": [{key: item.get(key) for key in ("code", "name", "state", "message")} for item in issues],
        "workflow_status": workflow_status,
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    blockers = [item for item in issues if item["state"] == "blocking"]
    return {
        "preview_only": True,
        "source": selected,
        "source_display": f"Catalogue Intake/{selected}",
        "source_folder": folder,
        "source_identity": _identity(root_info),
        "mode": mode,
        "mode_label": "Import and Review Folder Structure" if mode == IMPORT_REVIEW else "Import as Final Folder Structure",
        "next_action": "Review and Rename Folders" if mode == IMPORT_REVIEW else "Rename Images",
        "workflow_status": workflow_status,
        "result_name": result_name,
        "proposed_result": _join_rel(PREPARED_DIRECTORY, result_name),
        "directories": directories,
        "files": files,
        "excluded": excluded,
        "metadata": metadata,
        "issues": sorted(issues, key=lambda item: (_ordered(item["name"]), item["code"])),
        "blockers": blockers,
        "counts": counts,
        "parent": {"detected": bool(root_parent), "paths": [item["path"] for item in root_parent]},
        "maximum_depth": maximum_depth,
        "digest": digest,
        "ready": not blockers and bool(image_count),
        "compatibility": (
            "Structure appears suitable for direct image renaming; metadata is required for full scanner validation."
            if mode == IMPORT_FINAL and not blockers
            else "Structure ready for review; metadata is required for full scanner validation."
            if not blockers
            else "Blocking structural or filesystem issues must be corrected before import."
        ),
    }


def structured_import_preview(root, relative, mode=IMPORT_REVIEW):
    return _snapshot(root, relative, mode)


def revalidate_structured_import(relative, mode, submitted_digest):
    readiness = intake_readiness()
    if not readiness["readable"]:
        raise StructuredImportRejected("Catalogue Intake is unavailable")
    if not readiness["writable"]:
        raise StructuredImportRejected("Catalogue Intake must be mounted read/write before a structured folder can be imported")
    preview = structured_import_preview(configured_intake_root(), relative, mode)
    if not hmac.compare_digest(preview["digest"], str(submitted_digest or "")):
        raise StructuredImportRejected("The source folder changed after preview. Review the updated import proposal before continuing.")
    if not preview["ready"]:
        raise StructuredImportRejected("This structured-folder proposal has blocking issues and cannot be imported")
    return preview


def _copy_file(source, destination, expected):
    before = source.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink > 1:
        raise StructuredImportRejected("A required source file is no longer a safe regular file")
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (expected["device"], expected["inode"], expected["size"], expected["mtime_ns"]):
        raise StructuredImportRejected("The source folder changed after preview. Review the updated import proposal before continuing.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_file, destination.open("xb") as output_file:
        shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
        output_file.flush()
        os.fsync(output_file.fileno())
    if _hash_file(destination) != expected["sha256"]:
        raise StructuredImportRejected("A staged structured-folder file failed verification")


def _verify_tree(folder, preview):
    expected_dirs = {item["path"] for item in preview["directories"]}
    expected_files = {item["path"]: item for item in preview["files"]}
    found_dirs = set()
    found_files = set()
    for current, directories, filenames in os.walk(folder, followlinks=False):
        current_path = Path(current)
        for name in directories:
            candidate = current_path / name
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise StructuredImportRejected("The staged result contains an unsafe directory")
            found_dirs.add(candidate.relative_to(folder).as_posix())
        for name in filenames:
            candidate = current_path / name
            relative = candidate.relative_to(folder).as_posix()
            expected = expected_files.get(relative)
            if expected is None or relative in found_files:
                raise StructuredImportRejected("The staged result contains an unexpected file")
            info = candidate.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size != expected["size"] or _hash_file(candidate) != expected["sha256"]:
                raise StructuredImportRejected("A staged structured-folder file failed verification")
            if expected["kind"] == "image" and _classify_entry(candidate)[0] != "image":
                raise StructuredImportRejected("A staged image failed readability verification")
            found_files.add(relative)
    if found_dirs != expected_dirs or found_files != set(expected_files):
        raise StructuredImportRejected("The staged structured-folder result is incomplete")
    return True


def _warning_summary(preview):
    warnings = [item for item in preview["issues"] if item["state"] != "blocking"]
    counts = {}
    for item in warnings:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    return (
        [{"category": key, "count": value, "samples": []} for key, value in sorted(counts.items())],
        [item["message"][:240] for item in warnings[:10]],
    )


def _notify_completed(progress, summary):
    from app.utils.discord import notify_intake_structured_import_completed
    try:
        result = notify_intake_structured_import_completed(summary, operation_id=progress.operation_id)
    except Exception:
        result = (False, "delivery failed")
    progress.record_discord("completed", result)


def _notify_failed(progress, relative, error):
    from app.utils.discord import notify_intake_structured_import_failed
    try:
        result = notify_intake_structured_import_failed(PurePosixPath(relative).name, sanitize_operation_error(error), operation_id=progress.operation_id)
    except Exception:
        result = (False, "delivery failed")
    progress.record_discord("failed", result)


def execute_structured_import(lease, relative, mode, submitted_digest):
    root = Path(configured_intake_root()).resolve(strict=True)
    progress = _Progress(lease.id, 0)
    started = time.monotonic()
    operation_dir = root / INTAKE_STAGING_DIRECTORY / lease.id
    preview = None
    promoted = False
    try:
        progress.update("revalidating_structured_folder", "Revalidating structured-folder proposal")
        preview = revalidate_structured_import(relative, mode, submitted_digest)
        progress.total = len(preview["files"])
        progress.warnings = len([item for item in preview["issues"] if item["state"] != "blocking"])
        progress.update("acquiring_intake_lock", "Dedicated Catalogue Intake mutation lock acquired")
        cleanup = cleanup_stale_staging(root, protected_ids={lease.id})
        for warning in cleanup["warnings"]:
            progress.warnings += 1
            progress.update("cleaning_staging", warning, severity="warning")

        progress.update("creating_private_staging", "Creating private operation-owned staging")
        staging_root = root / INTAKE_STAGING_DIRECTORY
        staging_root.mkdir(mode=0o700, exist_ok=True)
        staging_info = staging_root.lstat()
        if stat.S_ISLNK(staging_info.st_mode) or not stat.S_ISDIR(staging_info.st_mode):
            raise StructuredImportRejected("Private Catalogue Intake staging is unsafe")
        operation_dir.mkdir(mode=0o700)
        _operation_marker(operation_dir, lease.id)
        stage_result = operation_dir / "result"
        stage_result.mkdir(mode=0o700)
        for directory in preview["directories"]:
            (stage_result / PurePosixPath(directory["path"])).mkdir(parents=True, exist_ok=True)

        source, _selected = _source_folder(root, relative)
        progress.update("copying_structured_folder", f"Files copied: 0 / {progress.total}")
        for expected in preview["files"]:
            _copy_file(source / PurePosixPath(expected["path"]), stage_result / PurePosixPath(expected["path"]), expected)
            progress.copied += 1
            progress.message = f"Files copied: {progress.copied} / {progress.total}"
            progress.current_item = expected["path"]
            progress.persist_copy_progress()

        progress.update("verifying_staged_result", f"Images verified: {preview['counts']['images']} / {preview['counts']['images']}")
        _verify_tree(stage_result, preview)
        if structured_import_preview(root, relative, mode)["digest"] != submitted_digest:
            raise StructuredImportRejected("The source folder changed while files were copied")

        progress.update("promoting_prepared_result", "Promoting verified structured folder into Prepared")
        prepared_root = root / PREPARED_DIRECTORY
        prepared_root.mkdir(mode=0o755, exist_ok=True)
        prepared_info = prepared_root.lstat()
        if stat.S_ISLNK(prepared_info.st_mode) or not stat.S_ISDIR(prepared_info.st_mode):
            raise StructuredImportRejected("The Prepared result root is unsafe")
        destination = prepared_root / preview["result_name"]
        if destination.exists() or destination.is_symlink():
            raise StructuredImportRejected("The prepared destination changed after preview")
        promotion = _promote_prepared_result(stage_result, destination)
        promoted = True

        progress.update("recording_workflow_state", "Recording imported Prepared-result workflow state")
        warning_summary, warning_entries = _warning_summary(preview)
        elapsed = round(time.monotonic() - started, 3)
        summary = {
            "source_relpath": preview["source"],
            "prepared_relpath": preview["proposed_result"],
            "result_name": preview["result_name"],
            "import_mode": mode,
            "source_preserved": True,
            "folder_count": preview["counts"]["folders"],
            "source_images": preview["counts"]["images"],
            "copied_images": preview["counts"]["images"],
            "copied_files": progress.copied,
            "failed_images": 0,
            "parent_detected": preview["parent"]["detected"],
            "maximum_depth": preview["maximum_depth"],
            "ignored_entries": preview["counts"]["excluded"],
            "warnings": progress.warnings,
            "blocking_errors": 0,
            "proposal_digest": preview["digest"],
            "workflow_status": preview["workflow_status"],
            "warning_findings": bounded_warning_findings(preview["issues"]),
            "warning_summary": warning_summary,
            "warning_entries": warning_entries,
            "promotion_strategy": promotion["strategy"],
            "promotion_fallback_reason": promotion["fallback_reason"],
            "duration_seconds": elapsed,
        }
        _notify_completed(progress, summary)
        progress.update("cleaning_staging", "Cleaning completed operation staging wrapper")
        try:
            (operation_dir / ".operation-owner").unlink()
            operation_dir.rmdir()
        except OSError:
            progress.warnings += 1
            summary["warnings"] = progress.warnings
            summary["warning_entries"] = [*summary["warning_entries"], "Empty staging wrapper requires later cleanup"][:10]
        progress.stage = "completed_folder_review_required" if mode == IMPORT_REVIEW else "completed_image_renaming_required"
        progress.message = "Structured folder imported — folder review required" if mode == IMPORT_REVIEW else "Folder structure confirmed — image renaming required"
        progress.current_item = preview["proposed_result"]
        status = "partial" if progress.warnings else "succeeded"
        progress.persist(summary=summary, status=status)
        finish_intake_operation(lease, status=status, summary=summary)
    except Exception as error:
        if promoted:
            progress.warnings += 1
            summary = {
                "source_relpath": relative,
                "prepared_relpath": preview["proposed_result"] if preview else None,
                "import_mode": mode,
                "source_preserved": True,
                "source_images": (preview or {"counts": {"images": 0}})["counts"]["images"],
                "copied_images": progress.copied,
                "warnings": progress.warnings,
                "blocking_errors": 0,
                "workflow_status": preview["workflow_status"] if preview else "folder_review_required",
                "warning_findings": [{"state": "warning", "code": "post_promotion_bookkeeping", "message": "Post-promotion bookkeeping requires review."}],
            }
            progress.persist(summary=summary, status="partial")
            finish_intake_operation(lease, status="partial", summary=summary)
            return
        try:
            staging_cleanup = "cleaned" if _cleanup_operation_staging(root, lease.id) else "not_found"
        except OSError:
            staging_cleanup = "retained_for_review"
        safe_error = str(error) if isinstance(error, StructuredImportRejected) else sanitize_operation_error(error)
        _notify_failed(progress, relative, safe_error)
        progress.failures = 1
        failed = {
            "source_relpath": relative,
            "prepared_relpath": preview["proposed_result"] if preview else None,
            "import_mode": mode,
            "source_preserved": True,
            "source_images": (preview or {"counts": {"images": 0}})["counts"]["images"],
            "copied_images": progress.copied,
            "failed_images": 1,
            "warnings": progress.warnings,
            "blocking_errors": 1,
            "workflow_status": "failed",
            "failed_stage": progress.stage,
            "staging_cleanup": staging_cleanup,
        }
        progress.stage = "failed"
        progress.message = safe_error
        progress.persist(summary=failed, status="failed")
        finish_intake_operation(lease, status="failed", error=safe_error, summary=failed)


def start_structured_import(app, relative, mode, submitted_digest):
    preview = revalidate_structured_import(relative, mode, submitted_digest)
    lease = acquire_intake_operation(
        {
            "source_relpath": preview["source"],
            "proposed_result_name": preview["result_name"],
            "proposal_digest": preview["digest"],
            "source_images": preview["counts"]["images"],
            "group_count": preview["counts"]["folders"],
            "workflow_status": preview["workflow_status"],
            "import_mode": mode,
        },
        operation_type=INTAKE_STRUCTURED_IMPORT_OPERATION_TYPE,
    )
    progress = _Progress(lease.id, len(preview["files"]))
    progress.persist()

    def target():
        with app.app_context():
            execute_structured_import(lease, relative, mode, submitted_digest)

    thread = threading.Thread(target=target, name=f"intake-import-{lease.id[:8]}", daemon=True)
    try:
        thread.start()
    except Exception as error:
        finish_intake_operation(lease, status="failed", error=error, summary={"source_images": preview["counts"]["images"], "copied_images": 0, "failed_images": 1, "workflow_status": "failed"})
        raise
    return lease.id
