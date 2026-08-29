"""Final validation and copy-only handoff from Prepared into Catalogue."""

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
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

from flask import current_app
from sqlalchemy import or_

from app import db
from app.image_preparation import PREPARED_DIRECTORY, _classify_entry, _ordered, _within, configured_intake_root, intake_readiness
from app.intake_grouping import (
    GroupingRejected,
    IntakeOperationActive,
    _operation_marker,
    _promote_prepared_result,
    acquire_intake_mutation_guard,
    release_intake_mutation_guard,
)
from app.intake_metadata_builder import METADATA_FILENAME, METADATA_STATUS, metadata_preview
from app.intake_warnings import bounded_warning_findings, warning_presentation
from app.product_info import validate_product_info
from app.models import CatalogueOperation, Settings
from app.utils.catalogue_paths import is_reserved_directory_name
from app.utils.operation_control import (
    CatalogueOperationActive,
    acquire_catalogue_operation,
    finish_catalogue_operation,
    sanitize_operation_error,
)
from app.utils.operation_live import persist_live_state, utcnow_iso


HANDOFF_OPERATION_TYPE = "intake_catalogue_handoff"
HANDOFF_STATUS = "catalogue_handoff_complete"
CATALOGUE_STAGING_DIRECTORY = ".woocommerce-dashboard-staging"
CATALOGUE_ROLLBACK_DIRECTORY = ".woocommerce-dashboard-rollback"
MAX_TREE_ENTRIES = 5000
STALE_PRIVATE_AGE = timedelta(hours=24)


class HandoffRejected(GroupingRejected):
    pass


def _utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


def _scope(row):
    try:
        value = json.loads(row.scope or "{}")
    except (TypeError, ValueError):
        return {}, {}
    if not isinstance(value, dict):
        return {}, {}
    summary = value.get("operation_summary")
    return value, summary if isinstance(summary, dict) else {}


def _latest_workflow(relative):
    rows = (
        CatalogueOperation.query.filter(
            CatalogueOperation.operation_type.in_({"intake_metadata_save", HANDOFF_OPERATION_TYPE})
        )
        .filter(CatalogueOperation.status.in_({"succeeded", "partial", "failed", "interrupted"}))
        .order_by(CatalogueOperation.finished_at.desc(), CatalogueOperation.id.desc())
        .all()
    )
    for row in rows:
        scope, summary = _scope(row)
        if (summary.get("prepared_relpath") or scope.get("source_relpath")) != relative:
            continue
        state = summary.get("workflow_status") or scope.get("workflow_status")
        return row, summary, state
    return None, {}, None


def _resolve_prepared(root, relative):
    root = Path(root).resolve(strict=True)
    parts = PurePosixPath(str(relative or "")).parts
    if len(parts) != 2 or parts[0] != PREPARED_DIRECTORY or parts[1].startswith("."):
        raise ValueError("Select one completed result directly beneath Prepared")
    prepared = root / PREPARED_DIRECTORY
    folder = prepared / parts[1]
    try:
        if folder.is_symlink() or not folder.is_dir() or folder.parent.resolve(strict=True) != prepared.resolve(strict=True):
            raise ValueError("Prepared result is unavailable")
    except OSError as error:
        raise ValueError("Prepared result is unavailable") from error
    return folder, PurePosixPath(*parts).as_posix()


def _catalogue_readiness(*, access_check=os.access):
    settings = Settings.query.first()
    configured = str(settings.product_folder or "").strip() if settings else ""
    if not configured:
        return {"state": "unavailable", "readable": False, "writable": False, "message": "Catalogue mount is not configured", "root": None}
    root = Path(configured)
    try:
        info = root.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise OSError
        resolved = root.resolve(strict=True)
    except OSError:
        return {"state": "unavailable", "readable": False, "writable": False, "message": "Catalogue mount is unavailable", "root": None}
    readable = bool(access_check(resolved, os.R_OK))
    writable = readable and bool(access_check(resolved, os.W_OK))
    return {
        "state": "writable" if writable else "read_only" if readable else "unavailable",
        "readable": readable,
        "writable": writable,
        "message": "Catalogue is available" if writable else "Catalogue mount is read-only" if readable else "Catalogue mount is unreadable",
        "root": resolved,
    }


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_snapshot(folder, *, source=True):
    """Return a deterministic, safe tree identity without exposing host paths."""

    folder = Path(folder)
    directories, files, findings = [], [], []
    normalised = {}
    stack = [(folder, ())]
    while stack:
        current, parts = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda item: _ordered(item.name))
        except OSError as error:
            raise HandoffRejected("A required collection folder cannot be read safely") from error
        children = []
        for entry in entries:
            relative = PurePosixPath(*parts, entry.name).as_posix()
            if len(directories) + len(files) >= MAX_TREE_ENTRIES:
                raise HandoffRejected("The collection exceeds the supported validation limit")
            try:
                info = entry.lstat()
            except OSError as error:
                raise HandoffRejected("A required collection entry cannot be read safely") from error
            if stat.S_ISLNK(info.st_mode):
                findings.append({"state": "blocking", "code": "symlink", "message": f"Unsafe symbolic link: {relative}"})
                continue
            if stat.S_ISDIR(info.st_mode):
                if source and entry.name.startswith("."):
                    findings.append({"state": "blocking", "code": "hidden_directory", "message": f"Unsupported hidden directory: {relative}"})
                    continue
                directories.append(relative)
                children.append((entry, (*parts, entry.name)))
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                if source and (entry.name.startswith(".") or entry.name in {"sku_index.json", ".scanned", ".update"}):
                    findings.append({"state": "blocking", "code": "scanner_or_hidden_file", "message": f"Unsupported scanner or hidden file: {relative}"})
                    continue
                kind, _ = _classify_entry(entry)
                allowed_json = entry.name == METADATA_FILENAME
                if source and kind != "image" and not allowed_json:
                    findings.append({"state": "blocking", "code": "unsupported_file", "message": f"Unsupported file: {relative}"})
                    continue
                files.append({"path": relative, "size": info.st_size, "sha256": _hash(entry), "kind": "image" if kind == "image" else "metadata" if allowed_json else "existing"})
            else:
                findings.append({"state": "blocking", "code": "special_entry", "message": f"Unsupported special entry: {relative}"})
                continue
            key = unicodedata.normalize("NFC", relative).casefold()
            if key in normalised and normalised[key] != relative:
                findings.append({"state": "blocking", "code": "duplicate_normalised_path", "message": f"Ambiguous case or Unicode-normalised paths: {normalised[key]} and {relative}"})
            else:
                normalised[key] = relative
        stack.extend(reversed(children))
    directories.sort(key=lambda value: tuple(_ordered(p) for p in PurePosixPath(value).parts))
    files.sort(key=lambda value: tuple(_ordered(p) for p in PurePosixPath(value["path"]).parts))
    identity_payload = {"directories": directories, "files": [{key: item[key] for key in ("path", "size", "sha256")} for item in files]}
    identity = hashlib.sha256(json.dumps(identity_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**identity_payload, "rows": files, "findings": findings, "identity": identity}


def _destination_snapshot(root, name):
    if not name or name.startswith(".") or PurePosixPath(name).name != name:
        raise HandoffRejected("Catalogue destination is invalid")
    destination = root / name
    if not _within(destination, root):
        raise HandoffRejected("Catalogue destination escapes the configured catalogue root")
    try:
        info = destination.lstat()
    except FileNotFoundError:
        return {"exists": False, "identity": "absent", "files": 0, "folders": 0, "images": 0, "markers": False, "unsafe": False}
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise HandoffRejected("Existing catalogue destination is unsafe")
    snapshot = _tree_snapshot(destination, source=False)
    marker_names = {".scanned", ".update", "sku_index.json"}
    marker_count = 0
    image_count = 0
    # Existing tree inventory deliberately observes markers but never copies them.
    for current, directories, filenames in os.walk(destination, followlinks=False):
        directories[:] = [name for name in directories if not name.startswith(".")]
        marker_count += sum(name in marker_names for name in filenames)
        image_count += sum(Path(name).suffix.casefold() in {".png", ".jpg", ".jpeg", ".webp"} for name in filenames)
    return {"exists": True, "identity": snapshot["identity"], "files": len(snapshot["files"]), "folders": len(snapshot["directories"]), "images": image_count, "markers": bool(marker_count), "unsafe": bool(snapshot["findings"])}


def _metadata_identity(relative, folder, source_snapshot):
    """Reuse the metadata builder, retaining controlled blockers for unsafe trees."""

    try:
        return metadata_preview(configured_intake_root(), relative)
    except ValueError as error:
        metadata_file = folder / METADATA_FILENAME
        document = {}
        findings = [{"state": "blocking", "code": "prepared_tree_invalid", "message": str(error)}]
        metadata_digest = "unavailable"
        try:
            raw = metadata_file.read_bytes()
            metadata_digest = hashlib.sha256(raw).hexdigest()
            document = json.loads(raw.decode("utf-8"))
            if not isinstance(document, dict):
                raise ValueError("product_info.json must contain an object")
            validation = validate_product_info(document, "collection")
            findings.extend({"state": "blocking", "code": item.code, "message": item.message, "path": item.path} for item in validation.errors)
            findings.extend({"state": "warning", "code": item.code, "message": item.message, "path": item.path} for item in validation.warnings)
            if document.get("collection_type") not in {"Simple", "Variable Collection", "Single Variable"}:
                findings.append({"state": "blocking", "code": "unsupported_collection_type", "message": "Collection type is not supported for catalogue handoff"})
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as metadata_error:
            findings.append({"state": "blocking", "code": "invalid_metadata", "message": f"product_info.json is invalid or missing: {metadata_error}"})
        image_count = sum(item["kind"] == "image" for item in source_snapshot["rows"])
        return {
            "document": document,
            "digest": metadata_digest,
            "findings": findings,
            "folder_identity": {"images": [{"path": item["path"], "sha256": item["sha256"]} for item in source_snapshot["rows"] if item["kind"] == "image"]},
            "analysis": {"expected_variations": 0},
            "counts": {
                "products": 1, "categories": len(document.get("categories", [])) if isinstance(document.get("categories"), list) else 0,
                "tags": len(document.get("tags", [])) if isinstance(document.get("tags"), list) else 0,
                "attributes": len(document.get("attributes", {})) if isinstance(document.get("attributes"), dict) else 0,
                "image_attributes": len(document.get("image_attributes", [])) if isinstance(document.get("image_attributes"), list) else 0,
                "modifiers": len(document.get("variation_modifiers", {})) if isinstance(document.get("variation_modifiers"), dict) else 0,
                "images": image_count,
            },
        }


def _image_roles(preview):
    collection_type = preview["document"].get("collection_type")
    parent = product = variation = 0
    for item in preview["folder_identity"]["images"]:
        parts = PurePosixPath(item["path"]).parts
        if collection_type == "Single Variable":
            if parts and is_reserved_directory_name(parts[0]):
                parent += 1
            else:
                variation += 1
        elif collection_type == "Variable Collection":
            product += 1
        else:
            parent += 1
    return parent, product, variation


def eligible_handoff_results(root):
    root = Path(root).resolve(strict=True)
    prepared = root / PREPARED_DIRECTORY
    if not prepared.is_dir() or prepared.is_symlink():
        return []
    results = []
    for entry in sorted(prepared.iterdir(), key=lambda item: _ordered(item.name)):
        if entry.is_symlink() or not entry.is_dir() or entry.name.startswith("."):
            continue
        relative = f"{PREPARED_DIRECTORY}/{entry.name}"
        operation, summary, state = _latest_workflow(relative)
        if operation is None:
            continue
        if operation.operation_type == "intake_metadata_save" and state == METADATA_STATUS and operation.status in {"succeeded", "partial"}:
            action = "Validate and Copy to Catalogue"
        elif operation.operation_type == HANDOFF_OPERATION_TYPE and state == HANDOFF_STATUS and operation.status in {"succeeded", "partial"}:
            action = "Review Handoff"
        else:
            continue
        results.append({"name": entry.name, "path": relative, "workflow_status": state, "operation_id": operation.id, "action": action})
    return results


def handoff_preview(relative, *, fresh_review=False):
    intake = intake_readiness()
    if not intake["readable"]:
        raise HandoffRejected("Catalogue Intake is unavailable")
    folder, canonical = _resolve_prepared(configured_intake_root(), relative)
    operation, previous_summary, state = _latest_workflow(canonical)
    completed = operation is not None and operation.operation_type == HANDOFF_OPERATION_TYPE and state == HANDOFF_STATUS and operation.status in {"succeeded", "partial"}
    if not ((operation and operation.operation_type == "intake_metadata_save" and state == METADATA_STATUS and operation.status in {"succeeded", "partial"}) or (completed and fresh_review)):
        raise HandoffRejected("This Prepared result is not eligible for a fresh catalogue handoff review")
    source = _tree_snapshot(folder)
    metadata = _metadata_identity(canonical, folder, source)
    findings = list(metadata["findings"]) + list(source["findings"])
    image_rows = [item for item in source["rows"] if item["kind"] == "image"]
    if not image_rows and not any(item.get("code") == "missing_image_source" for item in findings):
        findings.append({"state": "blocking", "code": "missing_images", "message": "The Prepared collection contains no usable supported images"})
    flattened = {}
    for item in image_rows:
        key = unicodedata.normalize("NFC", PurePosixPath(item["path"]).name).casefold()
        flattened.setdefault(key, []).append(item["path"])
    for paths in flattened.values():
        if len(paths) > 1:
            findings.append({"state": "blocking", "code": "flattened_filename_collision", "message": "Image filenames collide when scanner output is flattened"})
    if not str(metadata["document"].get("meta_title") or "").strip():
        findings.append({"state": "warning", "code": "optional_meta_title", "message": "Optional SEO meta title is not authored"})
    if not str(metadata["document"].get("meta_description") or "").strip():
        findings.append({"state": "warning", "code": "optional_meta_description", "message": "Optional SEO meta description is not authored"})
    findings.append({"state": "informational", "code": "prepared_preserved", "message": "The Prepared result will remain unchanged"})
    catalogue = _catalogue_readiness()
    if not catalogue["readable"]:
        findings.append({"state": "blocking", "code": "catalogue_unavailable", "message": catalogue["message"]})
        destination = {"exists": False, "identity": "unavailable", "files": 0, "folders": 0, "images": 0, "markers": False, "unsafe": False}
    else:
        destination = _destination_snapshot(catalogue["root"], folder.name)
        if destination["unsafe"]:
            findings.append({"state": "blocking", "code": "unsafe_destination", "message": "Existing catalogue destination contains unsafe entries"})
        if not catalogue["writable"]:
            findings.append({"state": "blocking", "code": "catalogue_read_only", "message": catalogue["message"]})
    if not destination["exists"]:
        findings.append({"state": "warning", "code": "new_destination", "message": "The catalogue destination does not yet exist and will be created."})
    blocking = [item for item in findings if item.get("state") == "blocking"]
    warnings = [item for item in findings if item.get("state") == "warning"]
    parent, product, variation = _image_roles(metadata)
    document = metadata["document"]
    counts = {
        **metadata["counts"],
        "files": len(source["files"]),
        "folders": len(source["directories"]),
        "parent_images": parent,
        "product_images": product,
        "variation_images": variation,
        "warnings": len(warnings),
        "errors": len(blocking),
        "variations": metadata["analysis"].get("expected_variations") or 0,
        "exact_image_variations": (metadata["analysis"].get("image_health") or {}).get("exact", 0),
        "fallback_image_variations": (metadata["analysis"].get("image_health") or {}).get("fallback", 0),
        "missing_image_variations": (metadata["analysis"].get("image_health") or {}).get("missing", 0),
    }
    proposal = {
        "kind": "catalogue_handoff",
        "prepared": canonical,
        "workflow_operation": operation.id,
        "workflow_state": state,
        "source_identity": source["identity"],
        "metadata_digest": metadata["digest"],
        "destination": folder.name,
        "destination_identity": destination["identity"],
        "action": "replace" if destination["exists"] else "create",
        "replacement_acknowledgement_required": bool(destination["exists"]),
        "findings": findings,
    }
    digest = hashlib.sha256(json.dumps(proposal, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    title = str(document.get("title") or folder.name)
    return {
        "source": canonical,
        "result_name": folder.name,
        "workflow_status": state,
        "completed_before": completed,
        "fresh_review": fresh_review,
        "document": document,
        "collection_type": document.get("collection_type"),
        "shared_title": title,
        "sku_prefix": str(document.get("sku_prefix") or ""),
        "publishing_intent": "Published" if document.get("live") is True else "Draft" if document.get("live") is False else "Inherited / not set",
        "source_tree": source,
        "destination": {**destination, "relative": folder.name, "action": proposal["action"]},
        "catalogue": catalogue,
        "findings": findings,
        "blocking": blocking,
        "warnings": warnings,
        "ready": not blocking,
        "counts": counts,
        "digest": digest,
        "metadata_digest": metadata["digest"],
        "previous_summary": previous_summary if completed else None,
    }


def revalidate_handoff(relative, digest, *, fresh_review=False, acknowledge=False, acknowledge_replace=False):
    preview = handoff_preview(relative, fresh_review=fresh_review)
    if not hmac.compare_digest(preview["digest"], str(digest or "")):
        raise HandoffRejected("The Prepared result, validation, or catalogue destination changed. Review a fresh proposal.")
    if not preview["ready"]:
        raise HandoffRejected("Catalogue handoff is blocked until every validation error is corrected.")
    if not acknowledge:
        raise HandoffRejected("Confirm that the Prepared result will be copied into the live catalogue.")
    if preview["destination"]["exists"] and not acknowledge_replace:
        raise HandoffRejected("Confirm that the existing catalogue collection will be replaced after verified staging.")
    return preview


class _Progress:
    def __init__(self, operation_id, total):
        self.operation_id, self.total, self.completed = operation_id, total, 0
        self.stage, self.message, self.logs = "revalidating_prepared", "Revalidating Prepared result", []
        self.warnings = self.failures = 0
        self.discord = {"state": "pending", "label": "Pending", "events": []}

    def update(self, stage, message, *, severity="info", summary=None):
        self.stage, self.message = stage, message
        self.logs.append({"sequence": len(self.logs) + 1, "severity": severity, "line": message})
        self.persist(summary=summary)

    def persist(self, *, summary=None, status="running"):
        persist_live_state(self.operation_id, {
            "stage": self.stage, "current_item": "", "latest_message": self.message, "status": status,
            "progress": {"completed": self.completed, "total": self.total, "percent": round(self.completed / self.total * 100, 2) if self.total else 0, "unit": "files"},
            "counts": {"files": self.completed, "warnings": self.warnings, "failures": self.failures},
            "summary": summary or {}, "discord": self.discord, "heartbeat_at": utcnow_iso(), "next_sequence": len(self.logs) + 1,
        }, self.logs)

    def record_discord(self, event, result):
        ok, message = result if isinstance(result, tuple) and len(result) == 2 else (False, "delivery failed")
        state = "sent" if ok else "disabled" if message == "disabled" else "not_configured" if message == "not configured" else "failed"
        labels = {"sent": "Sent", "disabled": "Discord disabled", "not_configured": "Discord not configured", "failed": "Delivery failed"}
        self.discord = {"state": state, "label": labels[state], "events": [{"event": event, "state": state}]}


def _private_operation(root, directory_name, operation_id):
    private = root / directory_name
    private.mkdir(mode=0o700, exist_ok=True)
    info = private.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise HandoffRejected("Private catalogue operation storage is unsafe")
    operation = private / operation_id
    if operation.exists() or operation.is_symlink():
        raise HandoffRejected("Private catalogue operation storage is already in use")
    operation.mkdir(mode=0o700)
    _operation_marker(operation, operation_id)
    return operation


def _owned_remove(path, operation_id):
    marker = path / ".operation-owner"
    try:
        owned = path.name == operation_id and marker.is_file() and not marker.is_symlink() and marker.read_text(encoding="ascii") == operation_id
    except (OSError, UnicodeError):
        owned = False
    if not owned:
        raise HandoffRejected("Private catalogue operation ownership could not be verified")
    shutil.rmtree(path)


def cleanup_stale_catalogue_operations(root, *, protected_ids=(), now=None):
    """Remove only old, ownership-proven private operation wrappers."""

    current = now or _utcnow()
    protected = set(protected_ids)
    protected.update(
        row.id for row in CatalogueOperation.query.filter_by(operation_type=HANDOFF_OPERATION_TYPE)
        .filter(or_(CatalogueOperation.status.in_({"running", "pending"}), CatalogueOperation.recovery_state != "none")).all()
    )
    removed, warnings = 0, []
    for directory_name in (CATALOGUE_STAGING_DIRECTORY, CATALOGUE_ROLLBACK_DIRECTORY):
        private = root / directory_name
        if not private.exists():
            continue
        try:
            info = private.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                warnings.append("Private catalogue operation storage is unsafe")
                continue
            entries = list(private.iterdir())
        except OSError:
            warnings.append("Private catalogue operation storage could not be inspected")
            continue
        for entry in entries:
            if entry.name in protected or len(entry.name) != 32 or any(character not in "0123456789abcdef" for character in entry.name):
                continue
            marker = entry / ".operation-owner"
            try:
                age = current - datetime.fromtimestamp(entry.lstat().st_mtime, UTC).replace(tzinfo=None)
                if age < STALE_PRIVATE_AGE or not marker.is_file() or marker.is_symlink() or marker.read_text(encoding="ascii") != entry.name:
                    continue
                shutil.rmtree(entry)
                removed += 1
            except (OSError, UnicodeError):
                warnings.append(f"Stale private operation {entry.name[:8]} could not be removed")
    return {"removed": removed, "warnings": warnings[:10]}


def _copy_source(source, stage, snapshot, progress):
    stage.mkdir(mode=0o700)
    for relative in snapshot["directories"]:
        (stage / relative).mkdir(parents=True, exist_ok=True)
    for expected in snapshot["rows"]:
        source_file = source / expected["path"]
        before = source_file.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or _hash(source_file) != expected["sha256"]:
            raise HandoffRejected("The Prepared result changed while it was copied")
        target = stage / expected["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        with source_file.open("rb") as reader, target.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        os.chmod(target, stat.S_IMODE(before.st_mode) & 0o666 or 0o600)
        progress.completed += 1
        if progress.completed == progress.total or progress.completed % max(5, min(25, (progress.total + 19) // 20)) == 0:
            progress.update("copying_prepared_collection", f"Files copied: {progress.completed} / {progress.total}")


def _verify_tree(folder, expected):
    found = _tree_snapshot(folder)
    if found["findings"] or found["identity"] != expected["identity"]:
        raise HandoffRejected("The copied collection failed complete tree verification")
    return found


def _restore_existing(destination, rollback_result, stage_operation, expected):
    if destination.exists():
        failed = stage_operation / "failed-promoted-result"
        if failed.exists():
            raise HandoffRejected("Failed promoted catalogue result requires controlled recovery")
        os.rename(destination, failed)
    if rollback_result.exists():
        _promote_prepared_result(rollback_result, destination)
    restored = _tree_snapshot(destination, source=False)
    if restored["identity"] != expected["identity"]:
        raise HandoffRejected("Original catalogue destination requires controlled recovery")


def _terminal_notify(progress, summary, *, failed=False, error=None):
    from app.utils.discord import notify_intake_handoff_completed, notify_intake_handoff_failed
    try:
        result = notify_intake_handoff_failed(summary.get("result_name"), sanitize_operation_error(error), operation_id=progress.operation_id) if failed else notify_intake_handoff_completed(summary, operation_id=progress.operation_id)
    except Exception:
        result = (False, "delivery failed")
    progress.record_discord("failed" if failed else "completed", result)


def execute_handoff_operation(lease, relative, digest, fresh_review, acknowledge, acknowledge_replace):
    progress = _Progress(lease.id, 0)
    started = time.monotonic()
    guard = None
    stage_operation = rollback_operation = None
    preview = None
    rollback_state = "not_required"
    recovery_state = "none"
    try:
        progress.update("revalidating_prepared", "Revalidating Prepared result")
        preview = revalidate_handoff(relative, digest, fresh_review=fresh_review, acknowledge=acknowledge, acknowledge_replace=acknowledge_replace)
        progress.total = preview["counts"]["files"]
        progress.warnings = preview["counts"]["warnings"]
        progress.update("acquiring_catalogue_mutation_lock", "Acquiring Catalogue Intake mutation lock")
        guard = acquire_intake_mutation_guard()
        # Catalogue operation lock is already owned by lease. This second lock
        # prevents concurrent Prepared mutation. The documented order avoids deadlock.
        fresh = revalidate_handoff(relative, digest, fresh_review=fresh_review, acknowledge=acknowledge, acknowledge_replace=acknowledge_replace)
        root = fresh["catalogue"]["root"]
        cleanup = cleanup_stale_catalogue_operations(root, protected_ids={lease.id})
        for warning in cleanup["warnings"]:
            progress.warnings += 1
            progress.update("cleaning_catalogue_staging", warning, severity="warning")
        source, _ = _resolve_prepared(configured_intake_root(), relative)
        destination = root / fresh["destination"]["relative"]
        progress.update("creating_catalogue_staging", "Creating catalogue staging")
        stage_operation = _private_operation(root, CATALOGUE_STAGING_DIRECTORY, lease.id)
        staged = stage_operation / "result"
        progress.update("copying_prepared_collection", f"Files copied: 0 / {progress.total}")
        _copy_source(source, staged, fresh["source_tree"], progress)
        progress.update("verifying_staged_collection", "Verifying staged collection")
        _verify_tree(staged, fresh["source_tree"])
        if handoff_preview(relative, fresh_review=fresh_review)["digest"] != digest:
            raise HandoffRejected("The Prepared result or destination changed during staging")
        existing_snapshot = _tree_snapshot(destination, source=False) if destination.exists() else None
        if destination.exists():
            progress.update("moving_existing_destination_to_rollback", "Moving existing catalogue destination to rollback")
            rollback_operation = _private_operation(root, CATALOGUE_ROLLBACK_DIRECTORY, lease.id)
            rollback_result = rollback_operation / "result"
            os.rename(destination, rollback_result)
            rollback_state = "retained_until_verification"
        else:
            rollback_result = None
        try:
            progress.update("promoting_staged_collection", "Promoting staged collection")
            promotion = _promote_prepared_result(staged, destination)
            progress.update("verifying_promoted_collection", "Verifying promoted collection")
            _verify_tree(destination, fresh["source_tree"])
        except Exception:
            if rollback_result is not None:
                progress.update("restoring_catalogue_destination", "Restoring original catalogue destination")
                _restore_existing(destination, rollback_result, stage_operation, existing_snapshot)
                _owned_remove(rollback_operation, lease.id)
                rollback_operation = None
                rollback_state, recovery_state = "restored_after_failure", "restored"
            elif destination.exists():
                failed = stage_operation / "failed-promoted-result"
                os.rename(destination, failed)
            raise
        if rollback_operation is not None:
            progress.update("removing_catalogue_rollback", "Removing rollback after successful verification")
            _owned_remove(rollback_operation, lease.id)
            rollback_operation = None
            rollback_state = "removed_after_verification"
        marker = stage_operation / ".operation-owner"
        marker.unlink()
        stage_operation.rmdir()
        stage_operation = None
        progress.update("updating_prepared_handoff_status", "Updating Prepared handoff status")
        duration = round(time.monotonic() - started, 3)
        summary = {
            "source_relpath": relative, "prepared_relpath": relative, "result_name": fresh["result_name"],
            "catalogue_destination": fresh["destination"]["relative"], "handoff_action": fresh["destination"]["action"],
            "collection_type": fresh["collection_type"], "sku_prefix": fresh["sku_prefix"][:128],
            "publishing_intent": fresh["publishing_intent"].casefold().replace(" / ", "_"),
            "product_count": fresh["counts"]["products"], "variation_count": fresh["counts"]["variations"],
            "exact_image_variations": fresh["counts"]["exact_image_variations"],
            "fallback_image_variations": fresh["counts"]["fallback_image_variations"],
            "missing_image_variations": fresh["counts"]["missing_image_variations"],
            "total_images": fresh["counts"]["images"], "parent_images": fresh["counts"]["parent_images"],
            "product_images": fresh["counts"]["product_images"], "variation_images": fresh["counts"]["variation_images"],
            "category_count": fresh["counts"]["categories"], "tag_count": fresh["counts"]["tags"],
            "attribute_count": fresh["counts"]["attributes"], "image_attribute_count": fresh["counts"]["image_attributes"],
            "modifier_count": fresh["counts"]["modifiers"], "warnings": fresh["counts"]["warnings"],
            "blocking_errors": fresh["counts"]["errors"], "warning_findings": bounded_warning_findings(fresh["warnings"]), "failures": 0,
            "proposal_digest": fresh["digest"], "validation_digest": fresh["metadata_digest"], "source_tree_digest": fresh["source_tree"]["identity"],
            "staged_verification": "matched", "promoted_verification": "matched", "rollback_state": rollback_state,
            "recovery_state": recovery_state, "workflow_status": HANDOFF_STATUS, "completion_time": _utcnow().isoformat(),
            "duration_seconds": duration, "source_images": fresh["counts"]["images"], "copied_images": fresh["counts"]["images"], "failed_images": 0,
            "promotion_strategy": promotion["strategy"], "promotion_fallback_reason": promotion["fallback_reason"],
            "next_step": "Run Append Scan",
        }
        _terminal_notify(progress, summary)
        progress.completed = progress.total
        progress.stage, progress.message = "completed_append_scan_required", "Completed — Append Scan required"
        status = "partial" if progress.warnings else "succeeded"
        progress.persist(summary=summary, status=status)
        if guard is not None:
            release_intake_mutation_guard(guard)
            guard = None
        finish_catalogue_operation(lease.id, status=status, products_attempted=progress.total, products_succeeded=progress.total, products_failed=0, recovery_state="none", operation_summary=summary)
    except Exception as error:
        safe = str(error) if isinstance(error, (HandoffRejected, IntakeOperationActive, CatalogueOperationActive)) else sanitize_operation_error(error)
        if rollback_operation is not None:
            recovery_state = "manual_recovery_required"
        for owned in (stage_operation,):
            if owned and owned.exists():
                try:
                    _owned_remove(owned, lease.id)
                except Exception:
                    recovery_state = "manual_recovery_required"
        summary = {
            "source_relpath": relative, "prepared_relpath": relative, "result_name": PurePosixPath(relative).name,
            "catalogue_destination": PurePosixPath(relative).name, "workflow_status": "failed", "failed_stage": progress.stage,
            "rollback_state": rollback_state, "recovery_state": recovery_state, "warnings": progress.warnings, "failures": 1,
            "source_images": preview["counts"]["images"] if preview else 0, "copied_images": 0, "failed_images": 1,
            "next_step": "Review failed handoff",
        }
        progress.failures = 1
        _terminal_notify(progress, summary, failed=True, error=safe)
        progress.stage, progress.message = "failed", safe
        progress.persist(summary=summary, status="failed")
        if guard is not None:
            release_intake_mutation_guard(guard)
            guard = None
        finish_catalogue_operation(lease.id, status="failed", products_attempted=progress.total, products_succeeded=0, products_failed=1, error=safe, recovery_state=recovery_state, operation_summary=summary)
    finally:
        if guard is not None:
            release_intake_mutation_guard(guard)


def start_handoff_operation(app, relative, digest, *, fresh_review=False, acknowledge=False, acknowledge_replace=False):
    preview = revalidate_handoff(relative, digest, fresh_review=fresh_review, acknowledge=acknowledge, acknowledge_replace=acknowledge_replace)
    lease = acquire_catalogue_operation(HANDOFF_OPERATION_TYPE, {
        "source_relpath": preview["source"], "catalogue_destination": preview["destination"]["relative"],
        "proposal_digest": preview["digest"], "workflow_status": "handoff_running",
    })
    progress = _Progress(lease.id, preview["counts"]["files"])
    progress.persist()

    def target():
        with app.app_context():
            execute_handoff_operation(lease, relative, digest, fresh_review, acknowledge, acknowledge_replace)

    thread = threading.Thread(target=target, name=f"catalogue-handoff-{lease.id[:8]}", daemon=True)
    try:
        thread.start()
    except Exception as error:
        finish_catalogue_operation(lease.id, status="failed", error=error, recovery_state="none", operation_summary={"workflow_status": "failed"})
        raise
    return lease.id


def handoff_review(relative):
    _folder, canonical = _resolve_prepared(configured_intake_root(), relative)
    row, summary, state = _latest_workflow(canonical)
    if row is None or row.operation_type != HANDOFF_OPERATION_TYPE or state != HANDOFF_STATUS:
        raise HandoffRejected("No completed catalogue handoff is available for this Prepared result")
    catalogue = _catalogue_readiness()
    destination_exists = bool(catalogue["readable"] and (catalogue["root"] / str(summary.get("catalogue_destination") or "")).is_dir())
    return {
        "operation": row,
        "summary": summary,
        "source": canonical,
        "destination_exists": destination_exists,
        "warning": warning_presentation(summary, status=row.status),
    }
