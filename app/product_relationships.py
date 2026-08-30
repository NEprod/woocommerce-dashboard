"""Filesystem-authored product relationships and their SQLite projection."""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
import json
import os
from pathlib import Path, PurePosixPath
from time import monotonic
import uuid

from flask import current_app
from sqlalchemy import or_
from sqlalchemy.orm import joinedload, selectinload

from app import db
from app.catalogue_images import product_thumbnail_url
from app.collection_identity import collection_display_name
from app.models import CatalogueOperation, Category, Collection, Product, ProductAttribute, ProductRelationship, Settings, Tag
from app.product_info import validate_product_info
from app.utils.atomic_files import atomic_write_json, atomic_write_text
from app.utils.backup_retention import create_metadata_backup
from app.utils.discord import notify_product_relationships_completed
from app.utils.json_utils import merge_product_json
from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation

RELATIONSHIP_TYPES = {"cross_sell": "Cross-sells", "upsell": "Upsells"}
JSON_KEYS = {"cross_sell": "cross_sells", "upsell": "upsells"}
LEGACY_KEYS = {"cross_sell": "crosssells", "upsell": "upsells"}
MAX_SEARCH_RESULTS = 100
MAX_TARGETS = 250
MAX_MUTUAL_PRODUCTS = 30


class RelationshipValidationError(ValueError):
    def __init__(self, message, *, details=None):
        super().__init__(message)
        self.details = details or {}


def _load_object(path):
    if path is None or not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RelationshipValidationError("Relationship metadata could not be read safely.") from error
    if not isinstance(value, dict):
        raise RelationshipValidationError("Relationship metadata must contain a JSON object.")
    return value


def _catalogue_root():
    settings = Settings.query.first()
    if not settings or not settings.product_folder:
        raise RelationshipValidationError("The catalogue folder is not configured.")
    try:
        root = Path(settings.product_folder).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise RelationshipValidationError("The configured catalogue folder is unavailable.") from error
    if not root.is_dir():
        raise RelationshipValidationError("The configured catalogue folder is unavailable.")
    return root


def _inside(path, root):
    try:
        return os.path.commonpath((str(path), str(root))) == str(root)
    except ValueError:
        return False


def _safe_path(root, value):
    if not value:
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        parts = PurePosixPath(str(value).replace("\\", "/")).parts
        if any(part in {"", ".", ".."} for part in parts):
            return None
        candidate = root.joinpath(*parts)
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    return resolved if _inside(resolved, root) else None


def relationship_owner(product):
    """Resolve the supported authoritative file without inventing another layer."""
    root = _catalogue_root()
    collection_type = ((product.collection.collection_type if product.collection else "") or "").strip().casefold()
    if collection_type == "single variable":
        reference = product.shared_json_path or (product.collection.shared_json_path if product.collection else None)
        if not reference and product.collection and product.collection.source_relpath:
            reference = f"{product.collection.source_relpath}/product_info.json"
        kind = "collection"
    else:
        reference = product.override_json_path
        if not reference and product.source_relpath:
            reference = f"{product.source_relpath}/product_info.json"
        if not reference and product.product_dir:
            reference = str(Path(product.product_dir) / "product_info.json")
        kind = "override"
    path = _safe_path(root, reference)
    if path is None or path.name != "product_info.json" or not _inside(path.parent.resolve(strict=False), root):
        raise RelationshipValidationError("The authoritative relationship metadata path is unavailable or unsafe.")
    return {"path": path, "relative": path.relative_to(root).as_posix(), "kind": kind, "marker": path.parent / ".update"}


def _normalise_skus(values):
    if not isinstance(values, list):
        raise RelationshipValidationError("Target products must be supplied as an ordered list.")
    if len(values) > MAX_TARGETS:
        raise RelationshipValidationError(f"A maximum of {MAX_TARGETS} relationships may be changed at once.")
    result = []
    for value in values:
        sku = value.strip() if isinstance(value, str) else ""
        if not sku or len(sku) > 64:
            raise RelationshipValidationError("One or more product SKUs are invalid.")
        result.append(sku)
    return result


def _explicit_relationships(document):
    block = document.get("relationships")
    if not isinstance(block, dict):
        return None
    relationships = {kind: _normalise_skus(block.get(key, [])) for kind, key in JSON_KEYS.items()}
    if any(len(set(values)) != len(values) for values in relationships.values()):
        raise RelationshipValidationError("Relationship SKU arrays must not contain duplicates.")
    return relationships


def relationship_source(product):
    owner = relationship_owner(product)
    explicit = _explicit_relationships(_load_object(owner["path"]))
    if explicit is not None:
        return {"relationships": explicit, "source": "authored", "owner": owner}
    root = _catalogue_root()
    shared_path = _safe_path(root, product.shared_json_path or (product.collection.shared_json_path if product.collection else None))
    override_path = _safe_path(root, product.override_json_path)
    shared = _load_object(shared_path)
    override = _load_object(override_path) if override_path and override_path != shared_path else {}
    resolved = merge_product_json(shared, override, path=PurePosixPath(product.source_relpath or "").name)
    relationships = {}
    for kind, key in LEGACY_KEYS.items():
        value = resolved.get(key, [])
        relationships[kind] = _normalise_skus(value) if isinstance(value, list) else []
    return {"relationships": relationships, "source": "legacy", "owner": owner}


def _ordered_edges(product_id, kind):
    return ProductRelationship.query.filter_by(source_product_id=product_id, relationship_type=kind).order_by(ProductRelationship.position.asc(), ProductRelationship.id.asc()).all()


def _targets_by_sku(skus):
    values = set(skus)
    if not values:
        return {}
    rows = Product.query.options(joinedload(Product.collection), selectinload(Product.categories), selectinload(Product.tags), selectinload(Product.images), selectinload(Product.assets), selectinload(Product.variations)).filter(Product.sku.in_(values)).all()
    return {row.sku: row for row in rows if row.sku}


def _publishing_view(product):
    if product.published is True:
        return {"state": "published", "label": "Published intent"}
    if product.published is False:
        return {"state": "draft", "label": "Draft intent"}
    return {"state": "unresolved", "label": "Intent not projected"}


def target_warnings(product):
    warnings = []
    if product.published is not True:
        warnings.append("Publishing intent is not Published.")
    if not ((product.title or "").strip() and (product.sku or "").strip() and ((product.description or "").strip() or (product.short_description or "").strip())):
        warnings.append("Metadata is incomplete.")
    if product.catalogue_status == "archived":
        warnings.append("Product is archived.")
    return warnings


def _product_view(product, relationship=None):
    return {"id": product.id, "title": product.title, "sku": product.sku or "SKU unavailable", "collection": collection_display_name(product.collection) if product.collection else "Unassigned", "category": product.categories[0].name if product.categories else "Uncategorised", "product_type": (product.product_type or "unknown").replace("_", " ").title(), "publishing_intent": _publishing_view(product), "catalogue_status": product.catalogue_status or "unknown", "thumbnail": product_thumbnail_url(product), "warnings": target_warnings(product), "relationship_id": relationship.id if relationship else None, "position": relationship.position if relationship else None, "broken": False}


def relationship_workspace(product):
    try:
        source = relationship_source(product)
        source_name = source["source"]
        source_label = "Product relationships" if source_name == "authored" else "Legacy resolved metadata"
    except RelationshipValidationError:
        # Historical/incomplete projections remain renderable, but mutation
        # still revalidates and refuses an unsupported authored destination.
        source_name, source_label = "unavailable", "Relationship source unavailable"
    result = {"source": source_name, "source_label": source_label}
    for kind, label in RELATIONSHIP_TYPES.items():
        edges = _ordered_edges(product.id, kind)
        targets = _targets_by_sku(edge.target_sku for edge in edges)
        items = []
        for edge in edges:
            target = targets.get(edge.target_sku)
            if target:
                items.append(_product_view(target, edge))
            else:
                items.append({"id": None, "title": "Referenced product is unavailable", "sku": edge.target_sku, "collection": "Unknown", "category": "Unknown", "product_type": "Unknown", "publishing_intent": {"state": "unresolved", "label": "Unknown"}, "catalogue_status": "invalid", "thumbnail": None, "warnings": ["The referenced SKU is not currently projected. Remove or repair this relationship."], "relationship_id": edge.id, "position": edge.position, "broken": True})
        result[kind] = {"label": label, "items": items, "count": len(items)}
    return result


def search_products(source_product_id, query, *, limit=MAX_SEARCH_RESULTS):
    text = (query or "").strip()
    if not text:
        return []
    pattern = f"%{text[:120]}%"
    rows = Product.query.options(joinedload(Product.collection), selectinload(Product.categories), selectinload(Product.tags), selectinload(Product.attributes), selectinload(Product.images), selectinload(Product.assets), selectinload(Product.variations)).filter(Product.id != source_product_id).filter(or_(Product.title.ilike(pattern), Product.sku.ilike(pattern), Product.product_type.ilike(pattern), Product.catalogue_status.ilike(pattern), Product.collection.has(Collection.name.ilike(pattern)), Product.collection.has(Collection.collection_type.ilike(pattern)), Product.categories.any(Category.name.ilike(pattern)), Product.tags.any(Tag.name.ilike(pattern)), Product.attributes.any(ProductAttribute.name.ilike(pattern)), Product.attributes.any(ProductAttribute.values.ilike(pattern)), (Product.published.is_(True) if text.lower() in {"published", "publish"} else Product.id == -1), (Product.published.is_(False) if text.lower() == "draft" else Product.id == -1))).order_by(Product.title.asc(), Product.sku.asc(), Product.id.asc()).limit(min(max(1, int(limit)), MAX_SEARCH_RESULTS)).all()
    return [_product_view(row) for row in rows]


def refresh_relationship_projection(product, *, commit=False):
    source = relationship_source(product)
    ProductRelationship.query.filter_by(source_product_id=product.id).delete(synchronize_session=False)
    targets = _targets_by_sku(sku for values in source["relationships"].values() for sku in values)
    for kind, skus in source["relationships"].items():
        for position, sku in enumerate(skus):
            target = targets.get(sku)
            db.session.add(ProductRelationship(source_product_id=product.id, target_sku=sku, resolved_target_product_id=target.id if target else None, relationship_type=kind, position=position))
    if commit:
        db.session.commit()
    return source


def rebuild_relationship_projection(*, commit=True):
    ProductRelationship.query.delete(synchronize_session=False)
    for product in Product.query.order_by(Product.id.asc()).all():
        refresh_relationship_projection(product)
    if commit:
        db.session.commit()


def resolve_relationship_targets(*, commit=True):
    targets = {row.sku: row.id for row in Product.query.filter(Product.sku.isnot(None)).all()}
    for edge in ProductRelationship.query.all():
        edge.resolved_target_product_id = targets.get(edge.target_sku)
    if commit:
        db.session.commit()


def preview_update(source_product, relationship_type, target_skus, *, mode="replace"):
    if relationship_type not in RELATIONSHIP_TYPES or mode not in {"add", "replace"}:
        raise RelationshipValidationError("Unsupported relationship update.")
    requested = _normalise_skus(target_skus)
    duplicates = [sku for sku, count in Counter(requested).items() if count > 1]
    deduped = list(dict.fromkeys(requested))
    existing = [edge.target_sku for edge in _ordered_edges(source_product.id, relationship_type)]
    desired = list(dict.fromkeys(existing + deduped)) if mode == "add" else deduped
    targets = _targets_by_sku(desired)
    invalid, warnings = [], []
    for sku in desired:
        target = targets.get(sku)
        if source_product.sku and sku == source_product.sku:
            invalid.append({"sku": sku, "reason": "A product cannot relate to itself."})
        elif target is None and sku not in existing:
            invalid.append({"sku": sku, "reason": "Local product does not exist."})
        elif target is not None and target.catalogue_status == "missing":
            invalid.append({"sku": sku, "reason": "Missing products cannot be added."})
        elif target is not None and target_warnings(target):
            warnings.append({"sku": sku, "title": target.title, "messages": target_warnings(target)})
    if relationship_type == "upsell":
        for target in targets.values():
            if ProductRelationship.query.filter_by(source_product_id=target.id, target_sku=source_product.sku, relationship_type="upsell").first():
                invalid.append({"sku": target.sku, "reason": "A direct circular upsell is not allowed."})
    invalid.extend({"sku": sku, "reason": "Duplicate selection."} for sku in duplicates)
    return {"target_skus": desired, "selected_count": len(deduped), "new_count": len([sku for sku in desired if sku not in existing]), "already_linked_count": len([sku for sku in deduped if sku in existing]), "duplicate_count": len(duplicates), "invalid_count": len(invalid), "invalid": invalid, "warnings": warnings, "continuation_allowed": not invalid}


def preview_mutual_cross_sells(selected_skus):
    skus = list(dict.fromkeys(_normalise_skus(selected_skus)))
    if len(skus) < 2:
        raise RelationshipValidationError("Select at least two products for mutual cross-sells.")
    if len(skus) > MAX_MUTUAL_PRODUCTS:
        raise RelationshipValidationError(f"Select no more than {MAX_MUTUAL_PRODUCTS} products.")
    targets = _targets_by_sku(skus)
    invalid = []
    for sku in skus:
        target = targets.get(sku)
        if target is None:
            invalid.append({"sku": sku, "reason": "Local product does not exist."})
        elif target.catalogue_status == "missing":
            invalid.append({"sku": sku, "reason": "Missing products cannot join a relationship family."})
        else:
            try:
                relationship_owner(target)
            except RelationshipValidationError as error:
                invalid.append({"sku": sku, "reason": str(error)})
    existing = {(edge.source_product.sku, edge.target_sku) for edge in ProductRelationship.query.filter(ProductRelationship.relationship_type == "cross_sell", ProductRelationship.source_product.has(Product.sku.in_(skus)), ProductRelationship.target_sku.in_(skus)).all()}
    desired = {(source, target) for source in skus for target in skus if source != target}
    warnings = [{"sku": sku, "title": targets[sku].title, "messages": target_warnings(targets[sku])} for sku in skus if sku in targets and target_warnings(targets[sku])]
    return {"selected_skus": skus, "selected_count": len(skus), "exact_relationship_count": len(desired), "new_count": len(desired - existing), "already_linked_count": len(existing), "invalid_count": len(invalid), "invalid": invalid, "warnings": warnings, "continuation_allowed": not invalid}


def _document_with_relationships(owner, relationships):
    document = _load_object(owner["path"])
    document["relationships"] = {JSON_KEYS[kind]: list(values) for kind, values in relationships.items()}
    validation = validate_product_info(document, owner["kind"])
    if validation.errors:
        raise RelationshipValidationError("Relationship metadata failed validation.", details=validation.to_dict())
    return document


def _manifest_directory():
    path = Path(current_app.instance_path) / "relationship-transactions"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def _write_manifest(path, manifest):
    atomic_write_json(path, manifest)
    path.chmod(0o600)


def _plan_documents(products, relationship_sets):
    root, plans = _catalogue_root(), []
    try:
        for product in sorted(products, key=lambda row: (row.sku or "", row.id)):
            owner = relationship_owner(product)
            document = _document_with_relationships(owner, relationship_sets[product.sku])
            owner["path"].parent.mkdir(parents=True, exist_ok=True)
            stage = owner["path"].with_name(f".{owner['path'].name}.relationships.{uuid.uuid4().hex}.stage")
            atomic_write_text(stage, json.dumps(document, ensure_ascii=False, indent=2))
            stage.chmod(0o600)
            plans.append({"sku": product.sku, "target": owner["relative"], "stage": stage.relative_to(root).as_posix(), "marker": owner["marker"].relative_to(root).as_posix(), "target_existed": owner["path"].exists(), "marker_existed": owner["marker"].exists(), "backup": None, "state": "staged"})
    except Exception:
        for plan in plans:
            (root / plan["stage"]).unlink(missing_ok=True)
        raise
    return plans


def _inject(stage, plan=None):
    hook = current_app.config.get("RELATIONSHIP_FAILURE_INJECTOR")
    if hook:
        hook(stage, plan)


def _rollback_manifest(manifest, path):
    root, failures = _catalogue_root(), []
    for plan in reversed(manifest.get("plans", [])):
        target, stage, marker = root / plan["target"], root / plan["stage"], root / plan["marker"]
        try:
            _inject("before_rollback", plan)
            if plan.get("state") in {"promoting", "promoted", "marker_written"}:
                if plan.get("target_existed") and plan.get("backup"):
                    restored = _load_object(root / plan["backup"])
                    atomic_write_text(target, json.dumps(restored, ensure_ascii=False, indent=2))
                elif not plan.get("target_existed"):
                    target.unlink(missing_ok=True)
            if not plan.get("marker_existed"):
                marker.unlink(missing_ok=True)
            stage.unlink(missing_ok=True)
            plan["state"] = "rolled_back"
        except Exception as error:
            failures.append({"sku": plan.get("sku"), "error": type(error).__name__})
    manifest["state"] = "recovery_required" if failures else "rolled_back"
    manifest["rollback_failures"] = failures
    _write_manifest(path, manifest)
    if not failures:
        path.unlink(missing_ok=True)
    return not failures


def _promote_documents(operation_id, plans):
    root = _catalogue_root()
    path = _manifest_directory() / f"{operation_id}.json"
    manifest = {"version": 1, "operation_id": operation_id, "state": "staged", "created_at": datetime.now(UTC).isoformat(), "plans": plans}
    _write_manifest(path, manifest)
    try:
        for plan in plans:
            target = root / plan["target"]
            if plan["target_existed"]:
                plan["backup"] = create_metadata_backup(target).relative_to(root).as_posix()
        manifest["state"] = "backed_up"
        _write_manifest(path, manifest)
        for plan in plans:
            _inject("before_promote", plan)
            plan["state"] = "promoting"
            _write_manifest(path, manifest)
            os.replace(root / plan["stage"], root / plan["target"])
            plan["state"] = "promoted"
            _write_manifest(path, manifest)
        for plan in plans:
            marker = root / plan["marker"]
            if not plan["marker_existed"]:
                atomic_write_text(marker, "1")
            plan["state"] = "marker_written"
            _write_manifest(path, manifest)
        manifest["state"] = "projection_pending"
        _write_manifest(path, manifest)
        return manifest, path
    except Exception:
        if not _rollback_manifest(manifest, path):
            raise RelationshipValidationError("Relationship files require recovery before another operation can continue.")
        raise


def _complete_transaction(products, relationship_sets, *, action, relationship_type, preview):
    started = monotonic()
    lease = acquire_catalogue_operation("product_relationship_update", {"action": action, "product_count": len(products), "relationship_type": relationship_type})
    manifest = path = None
    try:
        plans = _plan_documents(products, relationship_sets)
        manifest, path = _promote_documents(lease.id, plans)
        for product in products:
            refresh_relationship_projection(product)
        resolve_relationship_targets(commit=False)
        rows = ProductRelationship.query.filter(ProductRelationship.source_product_id.in_([row.id for row in products])).all()
        summary = {"product": products[0].title if len(products) == 1 else f"{len(products)}-product family", "product_count": len(products), "relationship_type": relationship_type, "relationship_count": preview.get("exact_relationship_count", len(relationship_sets[products[0].sku][relationship_type])), "new_relationship_count": preview.get("new_count", 0), "cross_sell_count": sum(row.relationship_type == "cross_sell" for row in rows), "upsell_count": sum(row.relationship_type == "upsell" for row in rows), "duration_ms": max(0, int((monotonic() - started) * 1000)), "woo_activity": False}
        finish_catalogue_operation(lease.id, status="succeeded", products_attempted=len(products), products_succeeded=len(products), operation_summary=summary)
        path.unlink(missing_ok=True)
        try:
            notify_product_relationships_completed(summary, operation_id=lease.id)
        except Exception:
            current_app.logger.warning("Discord relationship notification failed safely")
        return {"operation_id": lease.id, "summary": summary, "relationships": relationship_workspace(products[0]) if len(products) == 1 else None}
    except Exception as error:
        db.session.rollback()
        recovered = True
        if manifest and path and path.exists():
            recovered = _rollback_manifest(manifest, path)
        finish_catalogue_operation(lease.id, status="failed", products_attempted=len(products), products_failed=len(products), error=error, recovery_state="rolled_back" if recovered else "recovery_required", operation_summary={"product_count": len(products), "relationship_type": relationship_type, "woo_activity": False})
        raise


def apply_update(source_product, relationship_type, target_skus, *, mode="replace"):
    preview = preview_update(source_product, relationship_type, target_skus, mode=mode)
    if not preview["continuation_allowed"]:
        raise RelationshipValidationError("Relationship update contains blocking validation errors.", details=preview)
    state = relationship_source(source_product)["relationships"]
    state = {kind: list(values) for kind, values in state.items()}
    state[relationship_type] = preview["target_skus"]
    return _complete_transaction([source_product], {source_product.sku: state}, action="update", relationship_type=relationship_type, preview=preview)


def apply_mutual_cross_sells(selected_skus):
    preview = preview_mutual_cross_sells(selected_skus)
    if not preview["continuation_allowed"]:
        raise RelationshipValidationError("Mutual cross-sell operation contains blocking validation errors.", details=preview)
    target_map = _targets_by_sku(preview["selected_skus"])
    products = [target_map[sku] for sku in preview["selected_skus"]]
    states = {}
    for product in products:
        current = relationship_source(product)["relationships"]
        states[product.sku] = {"cross_sell": list(dict.fromkeys(current["cross_sell"] + [sku for sku in preview["selected_skus"] if sku != product.sku])), "upsell": list(current["upsell"])}
    return _complete_transaction(products, states, action="mutual_cross_sell", relationship_type="cross_sell", preview=preview)


def recover_relationship_transactions():
    recovered = required = 0
    for path in sorted(_manifest_directory().glob("*.json")):
        try:
            manifest = _load_object(path)
            row = db.session.get(CatalogueOperation, manifest.get("operation_id"))
            # The database commit is the operation's final authoritative gate.
            # A crash after that commit but before manifest cleanup must finalize,
            # not undo the already-committed authored files.
            if row and row.status == "succeeded" and manifest.get("state") == "projection_pending":
                path.unlink(missing_ok=True)
                recovered += 1
                continue
            success = _rollback_manifest(manifest, path)
            if row:
                row.status = "interrupted"
                row.finished_at = datetime.now(UTC).replace(tzinfo=None)
                row.recovery_state = "rolled_back" if success else "recovery_required"
                row.error = "Interrupted relationship transaction was rolled back." if success else "Relationship transaction requires recovery."
            recovered += int(success)
            required += int(not success)
        except Exception:
            required += 1
    db.session.commit()
    return {"recovered": recovered, "recovery_required": required}
