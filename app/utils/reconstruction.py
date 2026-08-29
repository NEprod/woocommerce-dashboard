"""Identity-preserving catalogue reconstruction and setup-state detection."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from app import db
from app.database import (
    backup_database,
    check_database_integrity,
    migration_head,
    restore_database,
)
from app.utils.backup_retention import mark_backup_recovery_required
from app.models import CatalogueOperationItem, Product, Settings
from app.utils.file_markers import (
    PENDING_FILE,
    SCANNED_FILE,
    iter_pending_scanned,
    load_pending_scanned,
    load_scanned,
)
from app.utils.ingest import ReconstructionParentError, ingest_reconstruction_rows
from app.utils.marker_recovery import (
    finalize_ingested_markers,
    mark_pending_database_recovery,
)
from app.utils.operation_control import (
    acquire_catalogue_operation,
    finish_catalogue_operation,
    sanitize_operation_error,
)
from app.utils.scan_runner import build_scan_scope
from app.utils.scanner import scan_collection


@dataclass(frozen=True)
class SetupState:
    code: str
    collection_count: int
    product_count: int
    marker_count: int
    pending_count: int
    projection_products: int
    safe_to_run: bool
    recommended_action: str | None
    identities_preserved: bool
    full_scan_automatic: bool = False
    message: str = ""
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReconstructionResult:
    operation_id: str
    status: str
    backup_path: Path | None
    collections: int
    products: int
    markers: int
    products_missing: int = 0
    products_restored: int = 0
    variations_missing: int = 0
    variations_restored: int = 0
    recovery_required: bool = False
    error: str | None = None


def _quiet_log(*_args, **_kwargs):
    return None


def _safe_catalogue_error(error, root=None):
    value = sanitize_operation_error(error)
    if root:
        value = value.replace(str(Path(root).resolve()), "[catalogue]")
    return value


def _read_marker(folder):
    pending_path = folder / PENDING_FILE
    scanned_path = folder / SCANNED_FILE
    if pending_path.exists():
        pending = load_pending_scanned(str(folder), log=_quiet_log)
        if not pending:
            raise ValueError(f"Malformed pending identity at {folder.name}")
        return pending["marker"], True
    if scanned_path.exists():
        marker = load_scanned(str(folder), log=_quiet_log)
        if not marker.get("sku"):
            raise ValueError(f"Malformed scanned identity at {folder.name}")
        return marker, False
    return None, False


def _validate_override_files(root, source_relpaths):
    for relative in source_relpaths:
        path = root / relative / "product_info.json"
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError(
                f"Product metadata must be an object: {relative}/product_info.json"
            )


def detect_setup_state() -> SetupState:
    settings = Settings.query.first()
    projection_products = Product.query.count()
    if not settings or not settings.product_folder:
        return SetupState(
            "ambiguous",
            0,
            0,
            0,
            0,
            projection_products,
            False,
            None,
            False,
            message="Catalogue settings are incomplete.",
            errors=("Configure the catalogue folder before continuing.",),
        )

    root = Path(settings.product_folder)
    if not root.is_dir():
        return SetupState(
            "ambiguous",
            0,
            0,
            0,
            0,
            projection_products,
            False,
            None,
            False,
            message="The configured catalogue is unavailable.",
            errors=("Mount or correct the configured catalogue folder.",),
        )

    try:
        visible_collections = tuple(
            child
            for child in sorted(root.iterdir())
            if child.is_dir() and not child.name.startswith("_")
        )
        if not visible_collections:
            plan = None
            sources = frozenset()
        else:
            plan = build_scan_scope(str(root), "full")
            if not plan.complete:
                raise ValueError(plan.error or "Catalogue scope is incomplete")
            sources = plan.seen_source_relpaths
        _validate_override_files(root, sources)

        marker_count = 0
        pending_count = 0
        parent_skus = set()
        variation_skus = set()
        for relative in sources:
            marker, pending = _read_marker(root / relative)
            if not marker:
                continue
            marker_count += 1
            pending_count += int(pending)
            sku = marker["sku"]
            if sku in parent_skus:
                raise ValueError("Duplicate parent SKU identities were found")
            parent_skus.add(sku)
            for variation in marker.get("variations", []):
                variation_sku = variation.get("sku")
                if not variation_sku or variation_sku in variation_skus:
                    raise ValueError("Malformed or duplicate variation identities were found")
                variation_skus.add(variation_sku)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        return SetupState(
            "ambiguous",
            len(visible_collections) if "visible_collections" in locals() else 0,
            len(sources) if "sources" in locals() else 0,
            0,
            0,
            projection_products,
            False,
            None,
            False,
            message="Catalogue identity state cannot be resolved safely.",
            errors=(_safe_catalogue_error(error, root),),
        )

    if projection_products == 0 and marker_count == 0:
        return SetupState(
            "new_catalogue",
            len(visible_collections),
            len(sources),
            marker_count,
            pending_count,
            projection_products,
            True,
            "append",
            False,
            message="No existing catalogue identities were found. A normal initial scan may allocate SKUs.",
        )
    if projection_products == 0:
        return SetupState(
            "reconstruction_required",
            len(visible_collections),
            len(sources),
            marker_count,
            pending_count,
            projection_products,
            True,
            "reconstruction",
            True,
            message="Existing catalogue identities were found and must be preserved by reconstruction.",
        )
    return SetupState(
        "ready",
        len(visible_collections),
        len(sources),
        marker_count,
        pending_count,
        projection_products,
        True,
        "append",
        True,
        message="A catalogue projection already exists. Reconstruction is available as a controlled recovery action.",
    )


def _marker_identity(folder):
    marker, _pending = _read_marker(folder)
    return marker or {}


def _database_identity_overlays(root):
    overlays = {}
    for product in Product.query.filter(Product.source_relpath.isnot(None)).all():
        folder = root / product.source_relpath
        marker = dict(_marker_identity(folder)) if folder.is_dir() else {}
        marker["sku"] = marker.get("sku") or product.sku
        marker.setdefault("title", product.title)
        marker.setdefault("images_used", [])
        existing_variations = marker.setdefault("variations", [])
        known = {
            json.dumps(
                sorted(item.get("attributes", {}).items()),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            for item in existing_variations
        }
        for variation in product.variations:
            try:
                pairs = json.loads(variation.source_identity or "[]")
                attributes = dict(pairs)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            identity = json.dumps(
                sorted(attributes.items()),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if identity not in known:
                existing_variations.append(
                    {"attributes": attributes, "sku": variation.sku}
                )
                known.add(identity)
        marker["variation_count"] = len(existing_variations)
        overlays[os.path.realpath(folder)] = marker
    return overlays


def _pre_resolve(root, output_folder, url_prefix, operation_id, log):
    plan = build_scan_scope(str(root), "full")
    if not plan.complete or not plan.authoritative:
        raise ValueError(plan.error or "Catalogue scope is not exhaustive")
    _validate_override_files(root, plan.seen_source_relpaths)
    overlays = _database_identity_overlays(root)
    rows = []
    for collection_path in plan.collection_paths:
        collection_rows = scan_collection(
            collection_path,
            url_prefix,
            output_folder,
            force_update=True,
            update_csv=True,
            log=log,
            defer_markers=True,
            operation_id=operation_id,
            reset_sku_index=False,
            preserve_existing_markers=True,
            identity_overrides=overlays,
        )
        expected = plan.expected_parent_counts[collection_path]
        emitted = sum(
            row.get("Type") in ("simple", "variable")
            for row in collection_rows
        )
        if emitted != expected:
            raise ValueError(
                f"Collection resolution incomplete: expected {expected} parent(s), got {emitted}"
            )
        rows.extend(collection_rows)
    return plan, rows


def run_reconstruction(*, failure_injector=None, log=_quiet_log):
    lease = acquire_catalogue_operation(
        "reconstruction",
        {"scope_kind": "catalogue", "exhaustive": True, "identity_mode": "preserve"},
    )
    backup_path = None
    settings = Settings.query.first()
    product_folder = settings.product_folder if settings else None
    output_folder = settings.output_folder if settings else None
    url_prefix = settings.url_prefix if settings else None
    state = detect_setup_state()
    collections = state.collection_count
    products = state.product_count
    markers = state.marker_count
    try:
        if not state.safe_to_run:
            raise ValueError(state.message or "Catalogue state is ambiguous")
        if not settings:
            raise ValueError("Catalogue settings are unavailable")
        root = Path(product_folder)
        plan, rows = _pre_resolve(
            root,
            output_folder,
            url_prefix,
            lease.id,
            log,
        )
        collections = len(plan.collection_paths)
        products = len(plan.seen_source_relpaths)

        database_path = Path(db.engine.url.database).resolve()
        db.session.rollback()
        check_database_integrity(database_path)
        backup_path = backup_database(
            database_path,
            source_revision=migration_head(),
            target_revision="reconstruction",
            purpose="reconstruction",
        )
        if failure_injector:
            failure_injector("before_replacement", None)
        summary = ingest_reconstruction_rows(
            rows,
            operation_id=lease.id,
            failure_injector=failure_injector,
            log=log,
        )
        try:
            if failure_injector:
                failure_injector("after_replacement", None)
            check_database_integrity(database_path)
        except Exception:
            db.session.remove()
            db.engine.dispose()
            restore_database(backup_path, database_path)
            check_database_integrity(database_path)
            raise

        marker_outcome = finalize_ingested_markers(
            str(root), lease.id, log=log
        )
        outstanding_pending = len(iter_pending_scanned(str(root)))
        recovery_required = bool(
            marker_outcome["database_recovery_required"]
            or marker_outcome["marker_recovery_required"]
            or outstanding_pending
        )
        if recovery_required and backup_path:
            mark_backup_recovery_required(backup_path)
        status = "partial" if recovery_required else "succeeded"
        recovery_state = "recovery_required" if recovery_required else "none"
        finish_catalogue_operation(
            lease.id,
            status=status,
            products_attempted=products,
            products_succeeded=products,
            products_failed=0,
            products_missing=summary["products_missing"],
            products_restored=summary["products_restored"],
            variations_missing=summary["variations_missing"],
            variations_restored=summary["variations_restored"],
            marker_state=(
                "recovery_required" if recovery_required else "finalized"
            ),
            recovery_state=recovery_state,
        )
        return ReconstructionResult(
            lease.id,
            status,
            backup_path,
            collections,
            products,
            markers,
            products_missing=summary["products_missing"],
            products_restored=summary["products_restored"],
            variations_missing=summary["variations_missing"],
            variations_restored=summary["variations_restored"],
            recovery_required=recovery_required,
        )
    except Exception as error:
        db.session.rollback()
        if backup_path:
            try:
                mark_backup_recovery_required(backup_path)
            except Exception:
                pass
        safe_error = _safe_catalogue_error(error, product_folder)
        pending_recovery = 0
        if product_folder:
            recovery = mark_pending_database_recovery(
                product_folder, lease.id, safe_error, log=log
            )
            pending_recovery = recovery["database_recovery_required"]
        if isinstance(error, ReconstructionParentError):
            existing_item = CatalogueOperationItem.query.filter_by(
                operation_id=lease.id, sku=error.sku
            ).first()
            if not existing_item:
                db.session.add(
                    CatalogueOperationItem(
                        operation_id=lease.id,
                        source_path=error.source_path,
                        sku=error.sku,
                        status="failed",
                        database_state="rolled_back",
                        marker_state=(
                            "database_recovery_required"
                            if pending_recovery
                            else "preserved"
                        ),
                        error=safe_error,
                    )
                )
                db.session.commit()
        finish_catalogue_operation(
            lease.id,
            status="failed",
            products_attempted=products,
            products_succeeded=0,
            products_failed=max(1, products),
            error=safe_error,
            marker_state=(
                "database_recovery_required" if pending_recovery else "not_started"
            ),
            recovery_state=(
                "database_recovery_required" if pending_recovery else "none"
            ),
        )
        return ReconstructionResult(
            lease.id,
            "failed",
            backup_path,
            collections,
            products,
            markers,
            recovery_required=bool(pending_recovery),
            error=safe_error,
        )


def _safe_result(result):
    payload = asdict(result)
    payload["backup_path"] = (
        f"backups/{result.backup_path.name}" if result.backup_path else None
    )
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "run"))
    args = parser.parse_args(argv)
    from app import create_app

    app = create_app()
    with app.app_context():
        if args.action == "status":
            print(json.dumps(asdict(detect_setup_state()), sort_keys=True))
            return 0
        def cli_log(message, level="INFO"):
            print(f"[{level}] {message}")

        result = run_reconstruction(log=cli_log)
        print(json.dumps(_safe_result(result), sort_keys=True, default=str))
        return 0 if result.status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
