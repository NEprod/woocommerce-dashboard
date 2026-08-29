"""Read-only Scanner readiness and bounded Operations presentation."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

from flask import current_app, url_for
from sqlalchemy import or_, text

from app import db
from app.collection_identity import collection_display_name
from app.database import migration_head
from app.intake_warnings import blocking_count, warning_presentation
from app.models import CatalogueOperation, CatalogueOperationItem, Collection, Product, Settings
from app.utils.discord import configuration_summary
from app.utils.operation_control import get_active_operation
from app.utils.operation_live import persisted_live_state
from app.utils.redaction import redact_diagnostic, runtime_redaction_paths

TERMINAL_STATUSES = {"succeeded", "partial", "failed", "interrupted"}
STATUS_LABELS = {
    "running": "Running", "succeeded": "Completed", "partial": "Completed with warnings",
    "failed": "Failed", "interrupted": "Interrupted", "pending": "Queued",
}
TYPE_LABELS = {
    "append": "Append", "product_update": "Update", "shared_collection_update": "Shared collection update",
    "full": "Full", "reconstruction": "Reconstruction", "intake_group": "Catalogue Intake — Group Images",
    "intake_folder_edit": "Catalogue Intake — Edit Folder Structure",
    "intake_image_rename": "Catalogue Intake — Rename Images",
    "intake_metadata_save": "Catalogue Intake — Save Metadata",
    "intake_catalogue_handoff": "Catalogue Intake — Catalogue Handoff",
}
SCAN_MODES = (
    {
        "key": "append", "label": "Append", "impact": "Focused", "confirmation": "Confirm append scan",
        "description": "Discovers catalogue items that have not already been marked as scanned. It creates new parent and variation projections while preserving existing projected products and SKU identities.",
        "details": "Writes scanner markers for completed items, refreshes SQLite for affected parents, and may copy required images to the output mount. Existing scanned products are left unchanged.",
    },
    {
        "key": "update", "label": "Update", "impact": "Targeted", "confirmation": "Confirm update scan",
        "description": "Processes products selected by the established update-marker workflow and refreshes their complete parent projection. Unrelated products and collections are not selected.",
        "details": "Consumes successful update intent only after database and marker finalisation. Existing product, variation, SKU, and Woo placeholder identities are preserved. Images may be copied to the output mount.",
    },
    {
        "key": "full", "label": "Full", "impact": "Catalogue-wide", "confirmation": "I understand; run full scan",
        "description": "Runs the intentional exhaustive catalogue workflow across every resolvable collection. It can take substantially longer and may affect many products and collections.",
        "details": "Uses the established intentional full-scan SKU-index and marker behaviour, refreshes SQLite projections, reconciles missing catalogue state, and may copy images to the output mount. It does not implement rollback.",
    },
)


def _path_state(path, *, writable=False):
    available = bool(path and os.path.isdir(path))
    if available:
        available = os.access(path, os.R_OK | (os.W_OK if writable else 0))
    return available


def scanner_readiness():
    settings = Settings.query.first()
    catalogue_ok = _path_state(settings.product_folder if settings else None)
    output_ok = _path_state(settings.output_folder if settings else None, writable=True)
    database_ok = False
    integrity = "unavailable"
    try:
        db.session.execute(text("SELECT 1"))
        database_ok = True
        if db.engine.dialect.name == "sqlite":
            integrity = db.session.execute(text("PRAGMA quick_check")).scalar() or "unavailable"
        else:
            integrity = "available"
    except Exception:
        db.session.rollback()
    app_data_ok = database_ok and os.path.isdir(current_app.instance_path)
    active = get_active_operation()
    return {
        "checks": [
            {"key": "catalogue", "label": "Catalogue available", "ok": catalogue_ok, "detail": "Readable source mount" if catalogue_ok else "Catalogue mount is unavailable or unreadable"},
            {"key": "output", "label": "Output available", "ok": output_ok, "detail": "Writable generated-output mount" if output_ok else "Output mount is unavailable or not writable"},
            {"key": "app_data", "label": "App data available", "ok": app_data_ok, "detail": f"Database ready · migration {migration_head()}" if app_data_ok else "Persistent app data or database is unavailable"},
            {"key": "integrity", "label": "Database integrity", "ok": integrity == "ok", "detail": "SQLite quick check passed" if integrity == "ok" else "SQLite quick check did not pass"},
        ],
        "ready": catalogue_ok and output_ok and app_data_ok and integrity == "ok" and active is None,
        "mounts_ready": catalogue_ok and output_ok and app_data_ok and integrity == "ok",
        "active": active,
        "discord": configuration_summary(),
    }


def _safe_scope(row):
    try:
        value = json.loads(row.scope or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _duration(row):
    if not row.started_at:
        return None
    end = row.finished_at or datetime.now(UTC).replace(tzinfo=None)
    return max(0, int((end - row.started_at).total_seconds()))


def _live_run(operation_id, row=None):
    if row is not None:
        persisted = persisted_live_state(row)
        if persisted:
            return persisted
    from app.utils.scan_runner import operation_run_snapshot
    return operation_run_snapshot(operation_id)


def _discord_view(operation_id, row=None):
    run = _live_run(operation_id, row)
    if run and run.get("discord"):
        return run["discord"]
    config = configuration_summary()
    if config["state"] == "disabled":
        return {"state": "disabled", "label": "Discord disabled", "events": []}
    if config["state"] == "not_configured":
        return {"state": "not_configured", "label": "Discord not configured", "events": []}
    return {"state": "not_recorded", "label": "Delivery not retained after restart", "events": []}


def _presentation_paths():
    settings = Settings.query.first()
    return runtime_redaction_paths(
        catalogue=settings.product_folder if settings else None,
        output=settings.output_folder if settings else None,
        instance=current_app.instance_path,
    )


def operation_view(row, *, redaction_paths=None):
    scope = _safe_scope(row)
    persisted_summary = scope.get("operation_summary") if isinstance(scope.get("operation_summary"), dict) else {}
    live = _live_run(row.id, row)
    active_summary = (live or {}).get("summary") or persisted_summary
    source = scope.get("collection_relpath") or scope.get("source_relpath")
    live_warnings = int((live or {}).get("counts", {}).get("warnings", 0) or 0)
    warning_view = warning_presentation(active_summary, status=row.status)
    warning_count = max(live_warnings, warning_view["count"])
    status_label = STATUS_LABELS.get(row.status, row.status.title())
    if row.status == "succeeded" and live_warnings:
        status_label = "Completed with warnings"
    return {
        "id": row.id, "short_id": row.id[:8], "type": row.operation_type,
        "type_label": TYPE_LABELS.get(row.operation_type, row.operation_type.replace("_", " ").title()),
        "status": row.status, "status_label": status_label,
        "started_at": row.started_at, "finished_at": row.finished_at, "duration": _duration(row),
        "attempted": row.products_attempted, "succeeded": row.products_succeeded,
        "failed": row.products_failed, "missing": row.products_missing, "restored": row.products_restored,
        "warning_count": max(warning_count, int(row.status == "partial") + int(row.recovery_state not in (None, "none"))),
        "warning_groups": warning_view["groups"],
        "blocking_count": blocking_count(active_summary, row=row),
        "error_count": max(int((live or {}).get("counts", {}).get("failures", 0) or 0), row.products_failed + int(bool(row.error))),
        "scope": scope, "scope_label": source or scope.get("sku") or "Catalogue",
        "recovery_state": row.recovery_state or "none", "recoverable": row.recovery_state not in (None, "none"),
        "marker_state": row.marker_state, "error": redact_diagnostic(row.error, paths=redaction_paths, limit=1000) if row.error else None,
        "discord": _discord_view(row.id, row), "live": live,
        "summary": active_summary,
        "warning_summary": active_summary.get("warning_summary", []),
        "warning_entries": active_summary.get("warning_entries", []),
        "detail_url": url_for("main.operation_detail", operation_id=row.id),
    }


def parse_operation_filters(args):
    try:
        page = max(1, int(args.get("page", "1")))
        per_page = int(args.get("per_page", "25"))
    except ValueError as error:
        raise ValueError("Invalid operation pagination") from error
    if per_page not in {25, 50, 100}:
        per_page = 25
    status = args.get("status", "")
    operation_type = args.get("type", "")
    attention = args.get("attention", "")
    recoverable = args.get("recoverable", "")
    sort = args.get("sort", "started")
    order = args.get("order", "desc")
    if status and status not in {"running", *TERMINAL_STATUSES}:
        raise ValueError("Invalid operation status")
    if operation_type and operation_type not in TYPE_LABELS:
        raise ValueError("Invalid operation type")
    if attention not in {"", "warnings", "errors"} or recoverable not in {"", "yes", "no"}:
        raise ValueError("Invalid operation filter")
    if sort not in {"started", "finished", "status", "type"} or order not in {"asc", "desc"}:
        raise ValueError("Invalid operation sorting")
    return {"page": page, "per_page": per_page, "status": status, "type": operation_type,
            "attention": attention, "recoverable": recoverable, "q": args.get("q", "")[:100],
            "sort": sort, "order": order}


def operations_browser(filters):
    query = CatalogueOperation.query
    if filters["status"]:
        query = query.filter(CatalogueOperation.status == filters["status"])
    if filters["type"]:
        query = query.filter(CatalogueOperation.operation_type == filters["type"])
    if filters["attention"] == "warnings":
        query = query.filter(or_(CatalogueOperation.status == "partial", CatalogueOperation.recovery_state != "none"))
    elif filters["attention"] == "errors":
        query = query.filter(or_(CatalogueOperation.products_failed > 0, CatalogueOperation.error.isnot(None)))
    if filters["recoverable"] == "yes":
        query = query.filter(CatalogueOperation.recovery_state != "none")
    elif filters["recoverable"] == "no":
        query = query.filter(CatalogueOperation.recovery_state == "none")
    if filters["q"]:
        needle = f"%{filters['q']}%"
        query = query.filter(or_(CatalogueOperation.id.ilike(needle), CatalogueOperation.scope.ilike(needle)))
    columns = {"started": CatalogueOperation.started_at, "finished": CatalogueOperation.finished_at,
               "status": CatalogueOperation.status, "type": CatalogueOperation.operation_type}
    column = columns[filters["sort"]]
    query = query.order_by(column.asc() if filters["order"] == "asc" else column.desc(), CatalogueOperation.id.desc())
    pagination = query.paginate(page=filters["page"], per_page=filters["per_page"], error_out=False)
    redaction_paths = _presentation_paths()
    items = [operation_view(row, redaction_paths=redaction_paths) for row in pagination.items]

    # Resolve portable collection and product labels in two bounded queries.
    skus = {item["scope"].get("sku") for item in items if item["scope"].get("sku")}
    products = Product.query.filter(Product.sku.in_(skus)).all() if skus else []
    product_map = {product.sku: product for product in products}
    relpaths = {item["scope"].get("collection_relpath") for item in items if item["scope"].get("collection_relpath")}
    collections = Collection.query.filter(Collection.source_relpath.in_(relpaths)).all() if relpaths else []
    collection_map = {collection.source_relpath: collection for collection in collections}
    for item in items:
        product = product_map.get(item["scope"].get("sku"))
        collection = collection_map.get(item["scope"].get("collection_relpath")) or (product.collection if product else None)
        if product:
            item["scope_label"] = product.title
        elif collection:
            item["scope_label"] = collection_display_name(collection)
        item["collection_label"] = collection_display_name(collection) if collection else None
    return {"items": items, "filters": filters, "pagination": {
        "page": pagination.page, "pages": pagination.pages or 1, "total": pagination.total,
        "from": (pagination.page - 1) * pagination.per_page + 1 if pagination.total else 0,
        "to": min(pagination.page * pagination.per_page, pagination.total),
        "previous_url": url_for("main.operations", **{**filters, "page": pagination.prev_num}) if pagination.has_prev else None,
        "next_url": url_for("main.operations", **{**filters, "page": pagination.next_num}) if pagination.has_next else None,
    }}


def operation_detail_workspace(row, *, item_page=1, item_status=""):
    redaction_paths = _presentation_paths()
    item_query = CatalogueOperationItem.query.filter_by(operation_id=row.id)
    if item_status:
        item_query = item_query.filter_by(status=item_status)
    item_pagination = item_query.order_by(CatalogueOperationItem.started_at.asc(), CatalogueOperationItem.id.asc()).paginate(
        page=max(1, item_page), per_page=50, error_out=False
    )
    skus = {item.sku for item in item_pagination.items if item.sku}
    products = Product.query.filter(Product.sku.in_(skus)).all() if skus else []
    product_map = {product.sku: product for product in products}
    collection_ids = {product.collection_id for product in products if product.collection_id}
    collections = Collection.query.filter(Collection.id.in_(collection_ids)).all() if collection_ids else []
    collection_map = {collection.id: collection for collection in collections}
    item_views = []
    related_products = {}
    related_collections = {}
    for item in item_pagination.items:
        product = product_map.get(item.sku)
        collection = collection_map.get(product.collection_id) if product else None
        safe_source = redact_diagnostic(item.source_path, paths=redaction_paths, limit=1000) if item.source_path else None
        item_views.append({
            "id": item.id, "sku": item.sku, "status": item.status,
            "database_state": item.database_state, "marker_state": item.marker_state,
            "source": safe_source, "error": redact_diagnostic(item.error, paths=redaction_paths, limit=1000) if item.error else None,
            "started_at": item.started_at, "finished_at": item.finished_at,
            "product": {"id": product.id, "title": product.title, "url": url_for("main.product_detail", product_id=product.id)} if product else None,
            "collection": {"id": collection.id, "name": collection_display_name(collection), "url": url_for("main.collection_detail", collection_id=collection.id)} if collection else None,
        })
        if product:
            related_products[product.id] = item_views[-1]["product"]
        if collection:
            related_collections[collection.id] = item_views[-1]["collection"]
    view = operation_view(row, redaction_paths=redaction_paths)
    live = view["live"] or {}
    timeline = [
        {"label": "Operation started", "state": "complete", "at": row.started_at},
        {"label": (live.get("stage") or "Scanner processing").replace("_", " ").title(), "state": "active" if row.status == "running" else "complete", "at": None},
    ]
    if row.finished_at:
        timeline.append({"label": view["status_label"], "state": "error" if row.status == "failed" else "complete", "at": row.finished_at})
    retry_mode = {"append": "append", "product_update": "update", "full": "full"}.get(row.operation_type)
    intake = None
    if row.operation_type in {"intake_group", "intake_folder_edit", "intake_image_rename", "intake_metadata_save", "intake_catalogue_handoff"}:
        summary = view.get("summary") or {}
        prepared_relpath = summary.get("prepared_relpath")
        groups = []
        prepared_url = None
        navigation = None
        if isinstance(prepared_relpath, str) and prepared_relpath.startswith("Prepared/"):
            try:
                from app.image_preparation import browse_intake, configured_intake_root

                prepared = browse_intake(configured_intake_root(), prepared_relpath)
                remaining_files = 5000
                pending = list(prepared["directories"][:200])
                while pending and len(groups) < 200:
                    directory = pending.pop(0)
                    if remaining_files <= 0:
                        break
                    child = browse_intake(configured_intake_root(), directory["path"])
                    filenames = [image["name"] for image in child["images"][:remaining_files]]
                    remaining_files -= len(filenames)
                    groups.append({
                        "name": directory["name"],
                        "path": directory["path"],
                        "files": filenames,
                    })
                    pending.extend(child["directories"][: max(0, 200 - len(groups) - len(pending))])
                prepared_url = url_for("main.image_preparation", path=prepared_relpath)
            except (OSError, ValueError):
                groups = []
        if (
            prepared_url
            and row.status in {"succeeded", "partial"}
            and not view["recoverable"]
        ):
            try:
                from app.intake_navigation import prepared_result_navigation

                navigation = prepared_result_navigation(
                    configured_intake_root(), prepared_relpath
                )
            except (OSError, ValueError):
                navigation = None
        if navigation is None:
            if view["recoverable"]:
                heading = "Catalogue Intake recovery required"
                state_label = "Recovery required"
                explanation = "Recovery must be resolved before another Catalogue Intake stage can be opened."
            elif row.status in {"running", "pending"}:
                heading = "Catalogue Intake operation in progress"
                state_label = "Running"
                explanation = (
                    "This operation is still running. The next action will appear automatically "
                    "after successful completion."
                )
            elif row.status == "interrupted":
                heading = "Operation interrupted"
                state_label = "Interrupted"
                explanation = "This operation was interrupted. Review its recovery state before continuing."
            elif row.status == "failed":
                heading = "Catalogue Intake operation failed"
                state_label = "Failed"
                explanation = "This operation failed. No next action is available. Review the failure details before continuing."
            elif row.status in {"succeeded", "partial"} and not prepared_url:
                heading = "Prepared result unavailable"
                state_label = "Action unavailable"
                explanation = "Prepared result is no longer available. Return to Catalogue Intake to review current results."
            else:
                heading = "Catalogue Intake action unavailable"
                state_label = "Action unavailable"
                explanation = "No safe next action is currently available for this Catalogue Intake operation."
            navigation = {
                "primary_action": None,
                "explanation": explanation,
                "heading": heading,
                "state_label": state_label,
            }
        intake = {
            "is_grouping": row.operation_type == "intake_group",
            "is_folder_edit": row.operation_type == "intake_folder_edit",
            "is_image_rename": row.operation_type == "intake_image_rename",
            "is_metadata_save": row.operation_type == "intake_metadata_save",
            "is_handoff": row.operation_type == "intake_catalogue_handoff",
            "source_relpath": view["scope"].get("source_relpath"),
            "prepared_relpath": prepared_relpath,
            "prepared_url": prepared_url,
            "navigation": navigation,
            "next_action": navigation.get("primary_action"),
            "groups": groups,
            "workflow_status": summary.get("workflow_status") or view["scope"].get("workflow_status"),
            "failed_stage": summary.get("failed_stage"),
            "staging_cleanup": summary.get("staging_cleanup"),
            "prefix": summary.get("prefix"),
            "renamed_images": summary.get("renamed_images"),
            "parent_images": summary.get("parent_images"),
            "variation_images": summary.get("variation_images"),
            "other_images": summary.get("other_images"),
            "proposal_digest": summary.get("proposal_digest") or view["scope"].get("proposal_digest"),
            "metadata_action": summary.get("metadata_action"),
            "collection_type": summary.get("collection_type"),
            "sku_prefix": summary.get("sku_prefix"),
            "publishing_intent": summary.get("publishing_intent"),
            "category_count": summary.get("category_count"),
            "tag_count": summary.get("tag_count"),
            "attribute_count": summary.get("attribute_count"),
            "image_attribute_count": summary.get("image_attribute_count"),
            "modifier_count": summary.get("modifier_count"),
            "rollback_state": summary.get("rollback_state"),
            "recovery_state": summary.get("recovery_state"),
            "predecessor_relpath": summary.get("predecessor_relpath"),
            "predecessor_cleanup": summary.get("predecessor_cleanup"),
            "retry_url": (
                url_for("main.image_preparation_handoff", path=view["scope"].get("source_relpath"))
                if row.operation_type == "intake_catalogue_handoff" and view["scope"].get("source_relpath")
                else url_for("main.image_preparation_folders_edit", path=view["scope"].get("source_relpath"))
                if row.operation_type == "intake_folder_edit" and view["scope"].get("source_relpath")
                else url_for("main.image_preparation_rename", path=view["scope"].get("source_relpath"))
                if row.operation_type == "intake_image_rename" and view["scope"].get("source_relpath")
                else url_for("main.image_preparation_metadata_edit", path=view["scope"].get("source_relpath"))
                if row.operation_type == "intake_metadata_save" and view["scope"].get("source_relpath")
                else url_for("main.image_preparation_group", path=view["scope"].get("source_relpath"))
                if view["scope"].get("source_relpath")
                else None
            ),
            "folder_editor_url": url_for("main.image_preparation_folders_edit", path=prepared_relpath) if row.operation_type == "intake_group" and prepared_relpath else None,
            "rename_preview_url": url_for("main.image_preparation_rename", path=prepared_relpath) if row.operation_type == "intake_folder_edit" and prepared_relpath else None,
            "metadata_next": row.operation_type == "intake_image_rename" and summary.get("workflow_status") == "metadata_required",
            "metadata_editor_url": url_for("main.image_preparation_metadata_edit", path=prepared_relpath) if row.operation_type in {"intake_image_rename", "intake_metadata_save"} and prepared_relpath else None,
            "handoff_review_url": url_for("main.image_preparation_handoff_review", path=prepared_relpath) if row.operation_type == "intake_catalogue_handoff" and prepared_relpath else None,
            "catalogue_destination": summary.get("catalogue_destination"),
            "handoff_action": summary.get("handoff_action"),
            "completion_time": summary.get("completion_time"),
            "staged_verification": summary.get("staged_verification"),
            "promoted_verification": summary.get("promoted_verification"),
            "next_step": summary.get("next_step"),
        }
    return {
        "operation": view, "items": item_views,
        "item_pagination": {"page": item_pagination.page, "pages": item_pagination.pages or 1, "total": item_pagination.total},
        "timeline": timeline, "related_products": list(related_products.values()),
        "related_collections": list(related_collections.values()), "retry_mode": retry_mode,
        "cancellation_supported": False, "intake": intake,
    }
