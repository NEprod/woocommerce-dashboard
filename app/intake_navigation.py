"""Navigation-only next-step mapping for durable Catalogue Intake results."""

from __future__ import annotations

import json
import stat
from pathlib import Path, PurePosixPath

from flask import current_app, url_for
from itsdangerous import BadData, URLSafeSerializer

from app.image_preparation import PREPARED_DIRECTORY, _ordered, _portable_parts
from app.intake_warnings import blocking_count, warning_presentation
from app.models import CatalogueOperation


INTAKE_OPERATION_TYPES = {
    "intake_group",
    "intake_folder_edit",
    "intake_image_rename",
    "intake_metadata_save",
    "intake_catalogue_handoff",
    "intake_structured_import",
}

WORKFLOW_STEPS = {
    "folder_review_required": {
        "operation_type": "intake_group",
        "state_label": "Folder review required",
        "heading": "Grouping complete — folder review required",
        "action": "Review and Rename Folders",
        "endpoint": "main.image_preparation_folders_edit",
    },
    "image_renaming_required": {
        "operation_type": "intake_folder_edit",
        "state_label": "Image renaming required",
        "heading": "Folder structure confirmed — image renaming required",
        "action": "Rename Images",
        "endpoint": "main.image_preparation_rename",
    },
    "metadata_required": {
        "operation_type": "intake_image_rename",
        "state_label": "Metadata required",
        "heading": "Images renamed — metadata required",
        "action": "Create Product Metadata",
        "endpoint": "main.image_preparation_metadata_edit",
    },
    "validation_required": {
        "operation_type": "intake_metadata_save",
        "state_label": "Validation required",
        "heading": "Metadata complete — validation required",
        "action": "Validate and Copy to Catalogue",
        "endpoint": "main.image_preparation_handoff_review",
        "extra": {"fresh": "1"},
    },
    "catalogue_handoff_complete": {
        "operation_type": "intake_catalogue_handoff",
        "state_label": "Catalogue handoff complete",
        "heading": "Catalogue handoff complete",
        "action": "Open Scanner",
        "endpoint": "main.scanner",
        "detail": "Run Append Scan to ingest the new catalogue collection.",
    },
}


def _scope(row):
    try:
        scope = json.loads(row.scope or "{}")
    except (TypeError, ValueError):
        return {}, {}
    if not isinstance(scope, dict):
        return {}, {}
    summary = scope.get("operation_summary")
    return scope, summary if isinstance(summary, dict) else {}


def _canonical_prepared(relative):
    parts = _portable_parts(relative)
    if (
        len(parts) != 2
        or parts[0] != PREPARED_DIRECTORY
        or parts[1].startswith(".")
    ):
        raise ValueError("Select one Prepared result directly beneath Prepared")
    return PurePosixPath(*parts).as_posix()


def _prepared_folder(root, canonical):
    root = Path(root).resolve(strict=True)
    prepared = root / PREPARED_DIRECTORY
    folder = prepared / PurePosixPath(canonical).name
    try:
        info = folder.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or folder.parent.resolve(strict=True) != prepared.resolve(strict=True)
        ):
            return None
    except OSError:
        return None
    return folder


def _latest_operation(canonical):
    rows = (
        CatalogueOperation.query.filter(
            CatalogueOperation.operation_type.in_(INTAKE_OPERATION_TYPES)
        )
        .order_by(
            CatalogueOperation.started_at.desc(),
            CatalogueOperation.id.desc(),
        )
        .all()
    )
    for row in rows:
        scope, summary = _scope(row)
        selected = summary.get("prepared_relpath") or scope.get("source_relpath")
        if selected == canonical:
            return row, scope, summary
    return None, {}, {}


def _serializer():
    return URLSafeSerializer(
        current_app.secret_key,
        salt="catalogue-intake-next-step-v1",
    )


def navigation_token(relative, workflow_state):
    canonical = _canonical_prepared(relative)
    return _serializer().dumps(
        {"result": canonical, "state": str(workflow_state or "")[:64]}
    )


def decode_navigation_token(token):
    try:
        payload = _serializer().loads(token)
        if not isinstance(payload, dict):
            return None
        canonical = _canonical_prepared(payload.get("result"))
        expected_state = str(payload.get("state") or "")[:64]
    except (BadData, TypeError, ValueError):
        return None
    return {"result": canonical, "expected_state": expected_state}


def prepared_result_navigation(root, relative):
    """Return the current safe next action for one direct Prepared result."""

    canonical = _canonical_prepared(relative)
    name = PurePosixPath(canonical).name
    folder = _prepared_folder(root, canonical)
    base = {
        "name": name,
        "path": canonical,
        "exists": folder is not None,
        "workflow_state": None,
        "state_label": "Workflow state unavailable",
        "heading": "Prepared result requires review",
        "primary_action": None,
        "explanation": "No durable Catalogue Intake workflow state is available.",
        "operation_id": None,
        "warning_count": 0,
        "warning_groups": [],
        "warning_review_url": None,
        "completed_with_warnings": False,
        "blocking_count": 0,
        "origin_label": None,
        "import_mode": None,
        "source_preserved": False,
    }
    row, scope, summary = _latest_operation(canonical)
    if row is None:
        return base
    state = summary.get("workflow_status") or scope.get("workflow_status")
    warning_view = warning_presentation(summary, status=row.status)
    blockers = blocking_count(summary, row=row)
    base.update(
        {
            "workflow_state": state,
            "operation_id": row.id,
            "warning_count": warning_view["count"],
            "warning_groups": warning_view["groups"],
            "warning_review_url": (
                url_for("main.operation_detail", operation_id=row.id)
                if warning_view["count"]
                else None
            ),
            "completed_with_warnings": bool(
                row.status in {"succeeded", "partial"}
                and warning_view["count"]
                and not blockers
            ),
            "blocking_count": blockers,
        }
    )
    if folder is None:
        base["explanation"] = "Prepared result is no longer available. Return to Catalogue Intake to review current results."
        return base
    if row.status not in {"succeeded", "partial"}:
        base.update(
            state_label="Action unavailable",
            heading="Catalogue Intake step did not complete",
            explanation="No next action is available because the latest Catalogue Intake operation did not complete successfully.",
        )
        return base
    if blockers:
        base.update(
            state_label="Blocking errors",
            heading="Catalogue Intake blocking errors",
            explanation="Blocking errors must be fixed before proceeding.",
        )
        return base
    if row.recovery_state not in {None, "", "none"}:
        base.update(
            state_label="Recovery required",
            heading="Catalogue Intake recovery required",
            explanation="Recovery must be resolved before another Catalogue Intake stage can be opened.",
        )
        return base
    step = WORKFLOW_STEPS.get(state)
    operation_matches = bool(
        step
        and (
            row.operation_type == step["operation_type"]
            or (
                row.operation_type == "intake_structured_import"
                and state in {"folder_review_required", "image_renaming_required"}
            )
        )
    )
    if step is None or not operation_matches:
        base.update(
            explanation="The latest workflow metadata is incomplete or no longer eligible for a direct next action."
        )
        return base

    label = step["action"]
    if state == "metadata_required" and (folder / "product_info.json").is_file():
        label = "Edit Product Metadata"
    token = navigation_token(canonical, state)
    base.update(
        state_label=step["state_label"],
        heading=step["heading"],
        explanation=(
            "Completed with warnings. You may continue. Review the warnings below."
            if warning_view["count"]
            else step.get("detail")
            or "The next page will revalidate this Prepared result before opening."
        ),
        primary_action={
            "label": label,
            "url": url_for("main.image_preparation_next", token=token),
            "endpoint": step["endpoint"],
            "kwargs": ({"path": canonical} if step["endpoint"] != "main.scanner" else {})
            | dict(step.get("extra") or {}),
        },
        origin_label=(
            "Imported structured source"
            if row.operation_type == "intake_structured_import"
            else None
        ),
        import_mode=summary.get("import_mode"),
        source_preserved=bool(summary.get("source_preserved")),
    )
    return base


def prepared_result_navigations(root):
    """Return bounded current workflow presentation for direct Prepared children."""

    root = Path(root).resolve(strict=True)
    prepared = root / PREPARED_DIRECTORY
    try:
        if prepared.is_symlink() or not prepared.is_dir():
            return []
        entries = sorted(prepared.iterdir(), key=lambda item: _ordered(item.name))
    except OSError:
        return []
    results = []
    for entry in entries:
        try:
            info = entry.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            continue
        results.append(
            prepared_result_navigation(
                root, PurePosixPath(PREPARED_DIRECTORY, entry.name).as_posix()
            )
        )
    return results


def navigation_destination(navigation):
    action = (navigation or {}).get("primary_action") or {}
    endpoint = action.get("endpoint")
    if not endpoint:
        return None
    return url_for(endpoint, **dict(action.get("kwargs") or {}))
