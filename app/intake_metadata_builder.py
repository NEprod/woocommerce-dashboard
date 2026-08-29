"""Scanner-aware product_info.json authoring for one Prepared working result."""

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
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath

from app import db
from app.image_preparation import (
    INTAKE_STAGING_DIRECTORY,
    PREPARED_DIRECTORY,
    _classify_entry,
    _join_rel,
    _ordered,
    configured_intake_root,
    intake_readiness,
)
from app.intake_folder_editor import _file_hash, _resolve_prepared_result
from app.intake_grouping import (
    INTAKE_METADATA_OPERATION_TYPE,
    INTAKE_RENAME_OPERATION_TYPE,
    GroupingRejected,
    _Progress,
    _cleanup_operation_staging,
    _operation_marker,
    acquire_intake_operation,
    cleanup_stale_staging,
    finish_intake_operation,
)
from app.intake_warnings import bounded_warning_findings
from app.intake_working_result import WorkingResultRecoveryRequired, replace_working_result
from app.models import CatalogueOperation
from app.product_info import FIELD_INVENTORY, validate_product_info
from app.utils.catalogue_paths import is_reserved_directory_name
from app.utils.image_resolution import resolve_single_variable_image_layout
from app.utils.operation_control import sanitize_operation_error


METADATA_FILENAME = "product_info.json"
METADATA_STATUS = "validation_required"
MAX_METADATA_BYTES = 1024 * 1024
COLLECTION_TYPES = ("Simple", "Variable Collection", "Single Variable")
SUPPORTED_FIELDS = tuple(field["key"] for field in FIELD_INVENTORY if field["collection_allowed"])
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_UNSAFE_SKU = re.compile(r"[\\/\x00-\x1f\x7f]")


class MetadataProposalRejected(GroupingRejected):
    pass


def _row_scope(row):
    try:
        scope = json.loads(row.scope or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}, {}
    if not isinstance(scope, dict):
        return {}, {}
    summary = scope.get("operation_summary")
    return scope, summary if isinstance(summary, dict) else {}


def _eligible_operation(selected):
    rows = (
        CatalogueOperation.query.filter(
            CatalogueOperation.operation_type.in_(
                {INTAKE_RENAME_OPERATION_TYPE, INTAKE_METADATA_OPERATION_TYPE}
            )
        )
        .filter(CatalogueOperation.status.in_({"succeeded", "partial"}))
        .order_by(CatalogueOperation.finished_at.desc(), CatalogueOperation.id.desc())
        .all()
    )
    for row in rows:
        scope, summary = _row_scope(row)
        if (summary.get("prepared_relpath") or scope.get("source_relpath")) != selected:
            continue
        state = summary.get("workflow_status") or scope.get("workflow_status")
        if row.operation_type == INTAKE_RENAME_OPERATION_TYPE and state == "metadata_required":
            return row, summary, "metadata_required"
        if row.operation_type == INTAKE_METADATA_OPERATION_TYPE and state == METADATA_STATUS:
            return row, summary, METADATA_STATUS
        return None, None, None
    return None, None, None


def eligible_metadata_results(root):
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
        operation, _summary, state = _eligible_operation(selected)
        if operation:
            results.append(
                {
                    "name": entry.name,
                    "path": selected,
                    "operation_id": operation.id,
                    "workflow_status": state,
                    "action": "Edit Product Metadata" if state == METADATA_STATUS else "Create Product Metadata",
                }
            )
    return results


def _snapshot(root, relative):
    folder, selected = _resolve_prepared_result(root, relative)
    operation, operation_summary, workflow_state = _eligible_operation(selected)
    if operation is None:
        raise ValueError(
            "Only an image-renamed or metadata-complete Prepared result can use the metadata builder."
        )
    folders, images, auxiliary_json, metadata = [], [], [], None
    stack = [(folder, ())]
    while stack:
        current, parts = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda value: _ordered(value.name))
        except OSError as error:
            raise ValueError("Prepared result cannot be read safely") from error
        children = []
        for entry in entries:
            kind, info = _classify_entry(entry)
            relative_path = _join_rel(*parts, entry.name)
            if kind == "directory":
                folders.append(relative_path)
                children.append((entry, (*parts, entry.name)))
            elif kind == "image":
                images.append(
                    {
                        "path": relative_path,
                        "size": info.st_size,
                        "mtime_ns": info.st_mtime_ns,
                        "sha256": _file_hash(entry),
                    }
                )
            elif not parts and entry.name == METADATA_FILENAME:
                if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1 or info.st_size > MAX_METADATA_BYTES:
                    raise ValueError("Existing product_info.json is unsafe or too large")
                metadata = {
                    "path": entry,
                    "size": info.st_size,
                    "mtime_ns": info.st_mtime_ns,
                    "sha256": _file_hash(entry),
                }
            elif parts and entry.name == METADATA_FILENAME:
                if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1 or info.st_size > MAX_METADATA_BYTES:
                    raise ValueError("A product-level product_info.json is unsafe or too large")
                auxiliary_json.append(
                    {
                        "path": relative_path,
                        "file": entry,
                        "size": info.st_size,
                        "mtime_ns": info.st_mtime_ns,
                        "sha256": _file_hash(entry),
                    }
                )
            else:
                raise ValueError("Prepared result contains an unsupported or unsafe entry")
        stack.extend(reversed(children))
    folders.sort(key=lambda value: tuple(_ordered(part) for part in PurePosixPath(value).parts))
    images.sort(key=lambda value: tuple(_ordered(part) for part in PurePosixPath(value["path"]).parts))
    auxiliary_json.sort(key=lambda value: tuple(_ordered(part) for part in PurePosixPath(value["path"]).parts))
    payload = {
        "selected": selected,
        "folders": folders,
        "images": [{key: row[key] for key in ("path", "size", "sha256")} for row in images],
        "metadata": ({key: metadata[key] for key in ("size", "sha256")} if metadata else None),
        "auxiliary_json": [{key: row[key] for key in ("path", "size", "sha256")} for row in auxiliary_json],
    }
    identity = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "folder": folder,
        "selected": selected,
        "folders": folders,
        "images": images,
        "metadata": metadata,
        "auxiliary_json": auxiliary_json,
        "identity": identity,
        "workflow_operation": operation.id,
        "workflow_state": workflow_state,
        "workflow_summary": operation_summary,
    }


def _read_existing(snapshot):
    metadata = snapshot["metadata"]
    if metadata is None:
        return {}, None, False
    try:
        text = metadata["path"].read_text(encoding="utf-8")
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError("product_info.json must contain an object")
        return value, None, True
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        return {}, f"Existing product_info.json could not be parsed: {error}", True


def _normalise_document(value):
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_METADATA_BYTES:
            raise ValueError("Metadata proposal exceeds the supported size limit")
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError(f"Metadata JSON is invalid: {error.msg} at line {error.lineno}") from error
    if not isinstance(value, dict):
        raise ValueError("product_info.json must contain an object")
    ordered = {}
    for key in SUPPORTED_FIELDS:
        if key in value:
            ordered[key] = value[key]
    for key in sorted(set(value) - set(ordered), key=lambda item: (item.casefold(), item)):
        ordered[key] = value[key]
    return ordered


def _finding(code, message, *, path="$", state="blocking"):
    return {"code": code, "message": message, "path": path, "state": state}


def _decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _modifier_parts(expression):
    parts = []
    for component in str(expression).split("|"):
        if "=" not in component:
            return None
        name, value = (item.strip() for item in component.split("=", 1))
        if not name or not value:
            return None
        parts.append((name, value))
    return parts


def _folder_analysis(snapshot, document):
    root_children = [path for path in snapshot["folders"] if len(PurePosixPath(path).parts) == 1]
    parent_variants = [path for path in root_children if is_reserved_directory_name(PurePosixPath(path).name)]
    non_parent = [path for path in root_children if path not in parent_variants]
    attributes = document.get("attributes") if isinstance(document.get("attributes"), dict) else {}
    image_attributes = document.get("image_attributes") if isinstance(document.get("image_attributes"), list) else []
    collection_type = document.get("collection_type")
    findings = []
    if collection_type == "Single Variable":
        resolution = resolve_single_variable_image_layout(
            document,
            snapshot["folders"],
            snapshot["images"],
        )
        findings.extend(resolution["findings"])
        expected_count = resolution["expected_variations"]
        visible_count = resolution["visible_variations"]
        parent_variants = resolution["parent_folders"]
        non_parent = resolution["product_folders"]
    else:
        expected_count = None
        visible_count = None
        if len(parent_variants) > 1:
            findings.append(_finding("duplicate_parent", "Multiple case variants of the reserved Parent directory are ambiguous."))
    titles = []
    shared_title = str(document.get("title") or "").strip()
    if collection_type == "Variable Collection":
        for name in non_parent:
            product_title = ""
            candidate = next(
                (item for item in snapshot.get("auxiliary_json", []) if item["path"] == f"{name}/{METADATA_FILENAME}"),
                None,
            )
            if candidate:
                try:
                    product_data = json.loads(candidate["file"].read_text(encoding="utf-8"))
                    if isinstance(product_data, dict):
                        product_title = str(product_data.get("title") or "").strip()
                except (OSError, UnicodeError, json.JSONDecodeError):
                    findings.append(_finding("product_json_invalid", f"Product metadata in ‘{name}’ could not be used for title preview.", state="warning"))
            base_title = product_title or name
            titles.append({"folder": name, "title": f"{base_title} - {shared_title}" if shared_title else base_title})
    elif collection_type in {"Simple", "Single Variable"}:
        fallback = snapshot["folder"].name
        titles.append({"folder": fallback, "title": shared_title or fallback})
    return {
        "root_folders": root_children,
        "parent_folders": parent_variants,
        "product_folders": non_parent,
        "expected_variations": expected_count,
        "visible_variations": visible_count,
        "image_resolutions": resolution["resolutions"] if collection_type == "Single Variable" else [],
        "image_health": resolution["image_health"] if collection_type == "Single Variable" else None,
        "title_previews": titles,
        "findings": findings,
    }


def _validate(document, snapshot):
    result = validate_product_info(document, "collection")
    findings = [
        _finding(issue.code, issue.message, path=issue.path)
        for issue in result.errors
    ] + [
        _finding(issue.code, issue.message, path=issue.path, state="warning")
        for issue in result.warnings
    ]
    if document.get("collection_type") not in COLLECTION_TYPES:
        findings.append(_finding("invalid_collection_type", "Select a supported collection type.", path="$.collection_type"))
    prefix = document.get("sku_prefix")
    if not isinstance(prefix, str) or not prefix.strip() or _UNSAFE_SKU.search(prefix):
        findings.append(_finding("invalid_sku_prefix", "SKU prefix is required and cannot contain path separators or control characters.", path="$.sku_prefix"))
    attributes = document.get("attributes")
    if isinstance(attributes, dict):
        seen_names = set()
        for name, values in attributes.items():
            folded = str(name).strip().casefold()
            if not folded or folded in seen_names:
                findings.append(_finding("duplicate_attribute", "Attribute names must be non-empty and unique after normalisation.", path="$.attributes"))
            seen_names.add(folded)
            if isinstance(values, list):
                folded_values = [str(value).strip().casefold() for value in values]
                if len(folded_values) != len(set(folded_values)):
                    findings.append(_finding("duplicate_attribute_value", f"Attribute ‘{name}’ contains duplicate values.", path=f"$.attributes.{name}"))
    image_attributes = document.get("image_attributes")
    if isinstance(image_attributes, list):
        folded = [str(value).strip().casefold() for value in image_attributes]
        if len(folded) != len(set(folded)):
            findings.append(_finding("duplicate_image_attribute", "Image attributes must be unique and ordered.", path="$.image_attributes"))
        if isinstance(attributes, dict):
            known = {str(name).casefold() for name in attributes}
            if any(value not in known for value in folded):
                findings.append(_finding("unknown_image_attribute", "Every image attribute must reference a defined attribute.", path="$.image_attributes"))
    modifiers = document.get("variation_modifiers")
    if isinstance(modifiers, dict) and isinstance(attributes, dict):
        for expression in modifiers:
            parts = _modifier_parts(expression)
            if not parts:
                findings.append(_finding("invalid_modifier", f"Modifier ‘{expression}’ must use Attribute=Value components.", path=f"$.variation_modifiers.{expression}"))
                continue
            for name, value in parts:
                if name not in attributes or value not in [str(item) for item in attributes.get(name, [])]:
                    findings.append(_finding("invalid_modifier_reference", f"Modifier ‘{expression}’ references an unknown attribute value.", path=f"$.variation_modifiers.{expression}"))
    regular = document.get("price")
    sale = document.get("sale_price")
    if regular not in (None, "") and sale not in (None, ""):
        regular_decimal, sale_decimal = _decimal(regular), _decimal(sale)
        if regular_decimal is not None and sale_decimal is not None and sale_decimal > regular_decimal:
            findings.append(_finding("sale_above_price", "Sale price exceeds the regular price.", path="$.sale_price", state="warning"))
    start, end = document.get("sale_start_date"), document.get("sale_end_date")
    if start and end:
        try:
            if date.fromisoformat(end) < date.fromisoformat(start):
                findings.append(_finding("invalid_sale_dates", "Sale end date cannot be before sale start date.", path="$.sale_end_date"))
        except ValueError:
            pass
    if (start or end) and sale in (None, ""):
        findings.append(_finding("sale_dates_without_price", "Sale dates are authored without a sale price.", state="warning"))
    analysis = _folder_analysis(snapshot, document)
    findings.extend(analysis["findings"])
    first_image = PurePosixPath(snapshot["images"][0]["path"]).name if snapshot["images"] else ""
    filename_prefix = first_image.split("_", 1)[0] if "_" in first_image else ""
    if prefix and filename_prefix and not str(prefix).casefold().startswith(filename_prefix.casefold()):
        findings.append(_finding("filename_prefix_mismatch", f"Image filenames use prefix ‘{filename_prefix}’, while metadata SKU prefix is ‘{prefix}’. This is allowed; confirm it is intentional.", path="$.sku_prefix", state="warning"))
    # Deduplicate schema/semantic overlap without hiding distinct messages.
    unique = []
    seen = set()
    for item in findings:
        key = (item["code"], item["path"], item["message"], item["state"])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique, analysis


def metadata_preview(root, relative, proposed=None):
    root = Path(root).resolve(strict=True)
    snapshot = _snapshot(root, relative)
    existing, parse_error, exists = _read_existing(snapshot)
    if proposed is None:
        document = existing
    else:
        document = _normalise_document(proposed)
    findings, analysis = _validate(document, snapshot)
    if parse_error and proposed is None:
        findings.insert(0, _finding("existing_json_invalid", parse_error))
    json_text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    proposal = {
        "kind": "prepared_metadata",
        "selected": snapshot["selected"],
        "workflow_operation": snapshot["workflow_operation"],
        "workflow_state": snapshot["workflow_state"],
        "source_identity": snapshot["identity"],
        "existing_metadata_identity": snapshot["metadata"]["sha256"] if snapshot["metadata"] else None,
        "document": document,
        "findings": findings,
        "folder_identity": {
            "folders": snapshot["folders"],
            "images": [{"path": item["path"], "sha256": item["sha256"]} for item in snapshot["images"]],
            "auxiliary_json": [{"path": item["path"], "sha256": item["sha256"]} for item in snapshot["auxiliary_json"]],
        },
    }
    digest = hashlib.sha256(
        json.dumps(proposal, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    blocking = [item for item in findings if item["state"] == "blocking"]
    warnings = [item for item in findings if item["state"] == "warning"]
    return {
        "source": snapshot["selected"],
        "result_name": snapshot["folder"].name,
        "workflow_status": snapshot["workflow_state"],
        "action": "update" if exists else "create",
        "existing": exists,
        "existing_parse_error": parse_error,
        "document": document,
        "json_text": json_text,
        "digest": digest,
        "ready": not blocking,
        "findings": findings,
        "blocking": blocking,
        "warnings": warnings,
        "analysis": analysis,
        "counts": {
            "products": len(analysis["product_folders"]) if document.get("collection_type") == "Variable Collection" else 1,
            "categories": len(document.get("categories", [])) if isinstance(document.get("categories"), list) else 0,
            "tags": len(document.get("tags", [])) if isinstance(document.get("tags"), list) else 0,
            "attributes": len(document.get("attributes", {})) if isinstance(document.get("attributes"), dict) else 0,
            "image_attributes": len(document.get("image_attributes", [])) if isinstance(document.get("image_attributes"), list) else 0,
            "modifiers": len(document.get("variation_modifiers", {})) if isinstance(document.get("variation_modifiers"), dict) else 0,
            "images": len(snapshot["images"]),
            "warnings": len(warnings),
            "errors": len(blocking),
        },
        "source_identity": snapshot["identity"],
        "folder_identity": proposal["folder_identity"],
        "supported_fields": SUPPORTED_FIELDS,
    }


def revalidate_metadata_proposal(relative, proposed, digest):
    readiness = intake_readiness()
    if not readiness["readable"]:
        raise MetadataProposalRejected("Catalogue Intake is unavailable")
    if not readiness["writable"]:
        raise MetadataProposalRejected("Catalogue Intake must be mounted read/write before metadata can be saved")
    preview = metadata_preview(configured_intake_root(), relative, proposed)
    if not hmac.compare_digest(preview["digest"], str(digest or "")):
        raise MetadataProposalRejected("The Prepared result or metadata proposal changed after preview. Review a fresh proposal.")
    if not preview["ready"]:
        raise MetadataProposalRejected("Metadata cannot be saved until every blocking error is corrected.")
    return preview


def _copy_tree_to_staging(source, stage, snapshot):
    stage.mkdir(mode=0o700)
    for relative in snapshot["folders"]:
        (stage / relative).mkdir(parents=True, exist_ok=True)
    allowed = {item["path"]: item for item in snapshot["images"]}
    for relative, expected in allowed.items():
        source_file = source / relative
        before = source_file.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink > 1 or _file_hash(source_file) != expected["sha256"]:
            raise MetadataProposalRejected("A Prepared image changed before staging")
        destination = stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with source_file.open("rb") as source_handle, destination.open("xb") as destination_handle:
            shutil.copyfileobj(source_handle, destination_handle, length=1024 * 1024)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
        os.chmod(destination, stat.S_IMODE(before.st_mode) & 0o666 or 0o600)
    if snapshot["metadata"]:
        source_file = snapshot["metadata"]["path"]
        if _file_hash(source_file) != snapshot["metadata"]["sha256"]:
            raise MetadataProposalRejected("Existing product_info.json changed before staging")
        shutil.copyfile(source_file, stage / METADATA_FILENAME)
        os.chmod(stage / METADATA_FILENAME, 0o600)
    for item in snapshot["auxiliary_json"]:
        source_file = item["file"]
        if _file_hash(source_file) != item["sha256"]:
            raise MetadataProposalRejected("A product-level product_info.json changed before staging")
        destination = stage / item["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        with source_file.open("rb") as source_handle, destination.open("xb") as destination_handle:
            shutil.copyfileobj(source_handle, destination_handle, length=1024 * 1024)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
        os.chmod(destination, 0o600)


def _write_metadata(stage, text):
    target = stage / METADATA_FILENAME
    temporary = stage / f".{METADATA_FILENAME}.tmp"
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
    with target.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or json.dumps(data, ensure_ascii=False, indent=2) + "\n" != text:
        raise MetadataProposalRejected("Written product_info.json failed deterministic validation")


def _verify_result(folder, preview):
    folder = Path(folder)
    metadata = folder / METADATA_FILENAME
    if metadata.is_symlink() or not metadata.is_file() or metadata.stat().st_size > MAX_METADATA_BYTES:
        raise MetadataProposalRejected("Saved product_info.json is unavailable or unsafe")
    if metadata.read_text(encoding="utf-8") != preview["json_text"]:
        raise MetadataProposalRejected("Saved product_info.json does not match the approved proposal")
    expected_folders = set(preview["analysis"]["root_folders"])
    del expected_folders  # Full folder/image identity is checked below.
    found_images, found_auxiliary = {}, {}
    found_folders = []
    for current, directories, files in os.walk(folder, followlinks=False):
        current_path = Path(current)
        for name in directories:
            child = current_path / name
            if child.is_symlink():
                raise MetadataProposalRejected("Saved result contains an unsafe directory")
            found_folders.append(child.relative_to(folder).as_posix())
        for name in files:
            file = current_path / name
            relative = file.relative_to(folder).as_posix()
            if relative == METADATA_FILENAME:
                continue
            if PurePosixPath(relative).name == METADATA_FILENAME:
                info = file.lstat()
                if file.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                    raise MetadataProposalRejected("Saved result contains unsafe product metadata")
                found_auxiliary[relative] = _file_hash(file)
                continue
            kind, _info = _classify_entry(file)
            if kind != "image":
                raise MetadataProposalRejected("Saved result contains an unexpected file")
            found_images[relative] = _file_hash(file)
    expected_images = {item["path"]: item["sha256"] for item in preview["folder_identity"]["images"]}
    expected_auxiliary = {item["path"]: item["sha256"] for item in preview["folder_identity"]["auxiliary_json"]}
    if sorted(found_folders, key=lambda value: tuple(_ordered(p) for p in PurePosixPath(value).parts)) != preview["folder_identity"]["folders"] or found_images != expected_images or found_auxiliary != expected_auxiliary:
        raise MetadataProposalRejected("Images or folders changed while metadata was saved")


def _notify_completed(progress, summary):
    from app.utils.discord import notify_intake_metadata_completed
    try:
        result = notify_intake_metadata_completed(summary, operation_id=progress.operation_id)
    except Exception:
        result = (False, "delivery failed")
    progress.record_discord("completed", result)


def _notify_failed(progress, relative, error):
    from app.utils.discord import notify_intake_metadata_failed
    try:
        result = notify_intake_metadata_failed(PurePosixPath(relative).name, sanitize_operation_error(error), operation_id=progress.operation_id)
    except Exception:
        result = (False, "delivery failed")
    progress.record_discord("failed", result)


def execute_metadata_operation(lease, relative, proposed, submitted_digest):
    root = Path(configured_intake_root()).resolve(strict=True)
    progress = _Progress(lease.id, 1)
    started = time.monotonic()
    operation_dir = root / INTAKE_STAGING_DIRECTORY / lease.id
    replacement_complete = False
    preview = None
    try:
        progress.update("revalidating_metadata_proposal", "Revalidating metadata proposal")
        preview = revalidate_metadata_proposal(relative, proposed, submitted_digest)
        progress.warnings = preview["counts"]["warnings"]
        progress.update("acquiring_intake_lock", "Catalogue Intake mutation lock acquired")
        cleanup_stale_staging(root, protected_ids={lease.id})
        staging_root = root / INTAKE_STAGING_DIRECTORY
        staging_root.mkdir(mode=0o700, exist_ok=True)
        operation_dir.mkdir(mode=0o700)
        _operation_marker(operation_dir, lease.id)
        stage_result = operation_dir / "result"
        snapshot = _snapshot(root, relative)
        if snapshot["identity"] != preview["source_identity"]:
            raise MetadataProposalRejected("The Prepared result changed before staging")
        progress.update("copying_working_result", "Copying working result to hidden staging")
        _copy_tree_to_staging(snapshot["folder"], stage_result, snapshot)
        progress.update("writing_product_info", "Writing product_info.json in staging")
        _write_metadata(stage_result, preview["json_text"])
        progress.update("validating_metadata", "Validating authored metadata")
        if not metadata_preview(root, relative, proposed)["ready"]:
            raise MetadataProposalRejected("Metadata validation changed during staging")
        progress.update("verifying_preservation", "Verifying image and folder preservation")
        _verify_result(stage_result, preview)
        visible = root / relative

        def verify_restored(_restored):
            if _snapshot(root, relative)["identity"] != preview["source_identity"]:
                raise WorkingResultRecoveryRequired("The restored Prepared result could not be verified")

        messages = {
            "moving_current_result_to_rollback": "Moving current result to protected rollback",
            "promoting_result": "Promoting metadata result under the same visible name",
            "verifying_promoted_result": "Verifying promoted metadata result",
            "verifying_restored_result": "Verifying automatically restored result",
            "removing_rollback": "Removing rollback after successful verification",
        }
        replacement = replace_working_result(
            root=root,
            operation_id=lease.id,
            staged_result=stage_result,
            visible_result=visible,
            verify_promoted=lambda promoted: _verify_result(promoted, preview),
            verify_restored=verify_restored,
            failed_result_parent=operation_dir,
            on_stage=lambda stage: progress.update(stage, messages[stage]),
        )
        replacement_complete = True
        marker = operation_dir / ".operation-owner"
        marker.unlink()
        operation_dir.rmdir()
        elapsed = time.monotonic() - started
        summary = {
            "source_relpath": relative,
            "prepared_relpath": relative,
            "result_name": preview["result_name"],
            "metadata_action": preview["action"],
            "collection_type": preview["document"].get("collection_type"),
            "sku_prefix": str(preview["document"].get("sku_prefix") or "")[:128],
            "shared_title_present": bool(preview["document"].get("title")),
            "publishing_intent": "published" if preview["document"].get("live") is True else "draft" if preview["document"].get("live") is False else "not_set",
            "category_count": preview["counts"]["categories"],
            "tag_count": preview["counts"]["tags"],
            "attribute_count": preview["counts"]["attributes"],
            "image_attribute_count": preview["counts"]["image_attributes"],
            "modifier_count": preview["counts"]["modifiers"],
            "warnings": preview["counts"]["warnings"],
            "blocking_errors": preview["counts"]["errors"],
            "warning_findings": bounded_warning_findings(preview["warnings"]),
            "failures": 0,
            "proposal_digest": preview["digest"],
            "workflow_status": METADATA_STATUS,
            "source_images": preview["counts"]["images"],
            "copied_images": preview["counts"]["images"],
            "failed_images": 0,
            "duration_seconds": round(elapsed, 3),
            **replacement,
        }
        _notify_completed(progress, summary)
        progress.copied = 1
        progress.stage = "completed_validation_required"
        progress.message = "Metadata complete — validation required"
        status = "partial" if progress.warnings else "succeeded"
        progress.persist(summary=summary, status=status)
        finish_intake_operation(lease, status=status, summary=summary)
    except Exception as error:
        failed_stage = progress.stage
        try:
            cleanup = "not_found" if replacement_complete else "cleaned" if _cleanup_operation_staging(root, lease.id) else "not_found"
        except OSError:
            cleanup = "retained_for_review"
        safe_error = str(error) if isinstance(error, GroupingRejected) else sanitize_operation_error(error)
        progress.failures = 1
        progress.stage = "failed"
        progress.message = safe_error
        _notify_failed(progress, relative, safe_error)
        summary = {
            "source_relpath": relative,
            "prepared_relpath": relative,
            "workflow_status": "failed",
            "failed_stage": failed_stage,
            "staging_cleanup": cleanup,
            "recovery_state": "manual_recovery_required" if isinstance(error, WorkingResultRecoveryRequired) else "restored" if "promot" in failed_stage or "rollback" in failed_stage else "not_required",
            "source_images": preview["counts"]["images"] if preview else 0,
            "copied_images": 0,
            "failed_images": 1,
        }
        progress.persist(summary=summary, status="failed")
        finish_intake_operation(lease, status="failed", error=safe_error, summary=summary)


def start_metadata_operation(app, relative, proposed, submitted_digest):
    preview = revalidate_metadata_proposal(relative, proposed, submitted_digest)
    lease = acquire_intake_operation(
        {
            "source_relpath": preview["source"],
            "proposed_result_name": preview["result_name"],
            "proposal_digest": preview["digest"],
            "source_images": preview["counts"]["images"],
            "group_count": len(preview["analysis"]["root_folders"]),
        },
        operation_type=INTAKE_METADATA_OPERATION_TYPE,
    )
    progress = _Progress(lease.id, 1)
    progress.stage = "revalidating_metadata_proposal"
    progress.message = "Revalidating metadata proposal"
    progress.persist()

    def target():
        with app.app_context():
            execute_metadata_operation(lease, relative, proposed, submitted_digest)

    thread = threading.Thread(target=target, name=f"intake-metadata-{lease.id[:8]}", daemon=True)
    try:
        thread.start()
    except Exception as error:
        finish_intake_operation(lease, status="failed", error=error, summary={"source_images": 0, "copied_images": 0, "failed_images": 1})
        raise
    return lease.id
