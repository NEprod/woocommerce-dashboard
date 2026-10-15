"""Source ownership and signed local edit checks; mutation uses existing routes."""
import json
from pathlib import Path, PurePosixPath

from flask import current_app
from itsdangerous import URLSafeTimedSerializer, BadSignature

from app.metadata_edit import MetadataEditConflict, prepare_edit, source_digest
from app.metadata_workspace import metadata_source, resolved_metadata, _catalogue_root
from app.models import Product
from app.product_info import validate_product_info

REVIEW_AGE = 4 * 60 * 60
MAX_SHARED_PRODUCTS = 1000


def raw_text(path):
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def source(product, kind):
    if kind not in {"shared", "override"}:
        raise ValueError("Choose shared collection metadata or product overrides.")
    declared = product.shared_json_relpath if kind == "shared" else product.override_json_relpath
    if declared and (PurePosixPath(declared.replace("\\", "/")).is_absolute() or
                     ".." in PurePosixPath(declared.replace("\\", "/")).parts):
        raise ValueError("Metadata source reference is not a safe catalogue-relative path.")
    root = _catalogue_root()
    item = metadata_source(product, kind, catalogue_root=root)
    if root is None or not item["reference"]:
        raise ValueError("Metadata source unavailable. Restore the catalogue mount.")
    raw_path = root / item["reference"]
    path = raw_path.resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise ValueError("Metadata destination is outside the authorised catalogue.") from None
    if path.name != "product_info.json" or not path.parent.is_dir() or raw_path.is_symlink():
        raise ValueError("Metadata destination is not an authorised product definition.")
    if kind == "override":
        shared = metadata_source(product, "shared", catalogue_root=root)
        if shared["reference"] and path == (root / shared["reference"]).resolve():
            raise ValueError("This layout has one JSON owner. Use Edit Shared Collection Data.")
        if product.source_relpath and path != (root / product.source_relpath / "product_info.json").resolve():
            raise ValueError("The selected override does not belong to this product folder.")
    if path.exists() and (not item["exists"] or item["error"]):
        raise ValueError("Existing metadata must be restored or corrected before saving.")
    text = raw_text(path) if path.exists() else "{}"
    source_digest(text)
    return path, text, item


def revision(product, kind):
    from app.taxonomy_assignments import options
    path, text, item = source(product, kind)
    result = {"product": product.id, "sku": product.sku, "kind": kind, "source": item["reference"],
              "exists": path.exists(), "digest": source_digest(text),
              "registry": options()["digest"]}
    if kind == "override":
        _, shared, info = source(product, "shared")
        result["shared"] = [info["reference"], source_digest(shared)]
    else:
        children = Product.query.filter_by(collection_id=product.collection_id).order_by(Product.id).limit(MAX_SHARED_PRODUCTS+1).all() if product.collection_id else [product]
        if len(children) > MAX_SHARED_PRODUCTS:
            raise ValueError("Shared scope exceeds the supported review bound.")
        dependencies = []
        for child in children:
            override = metadata_source(child, "override")
            if override["reference"] == item["reference"]:
                continue
            dependencies.append([child.id, child.source_relpath, override["reference"], override["error"],
                source_digest(raw_text(override["path"])) if override["path"] else None])
        result["children"] = source_digest(json.dumps(dependencies, sort_keys=True))
    return result


def serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="tlc-metadata-local-edit-v1")


def review_token(product, kind):
    return serializer().dumps(revision(product, kind))


def verify_revision(product, kind, token):
    if not isinstance(token, str) or not token or len(token) > 16384:
        raise MetadataEditConflict("A valid source review is required. Reload the editor before saving.")
    try:
        saved = serializer().loads(token, max_age=REVIEW_AGE)
    except (BadSignature, TypeError):
        raise MetadataEditConflict("Edit review is missing, invalid or expired. Reload the editor; your draft is not saved.") from None
    if saved != revision(product, kind):
        raise MetadataEditConflict("Source, inheritance or registry changed since opening. Reload and review your draft before saving.")
    return saved


def prepare_request(product, kind, payload):
    saved = verify_revision(product, kind, payload.get("revision"))
    path, text, _ = source(product, kind)
    if payload.get("replace"):
        raise ValueError("Whole-document replacement is not supported. Reload the editor to use reviewed patches.")
    changes, removals = payload.get("data", {}), payload.get("remove_paths", [])
    if not isinstance(removals, list) or len(removals) > 1000:
        raise ValueError("Use a bounded list of explicit removal paths.")
    edit = prepare_edit(text, changes, expected_digest=saved["digest"],
                        kind="collection" if kind == "shared" else "override", remove_paths=removals)
    if kind == "override" and any(key in edit.data for key in ("sku_prefix", "collection_type")):
        # Existing historic keys are preserved but cannot be introduced/changed.
        before = json.loads(text)
        if any(edit.data.get(k) != before.get(k) for k in ("sku_prefix", "collection_type")):
            raise ValueError("Layout and SKU prefix belong to shared metadata, not a child override.")
    if kind == "shared":
        before = json.loads(text)
        if any(k in before and edit.data.get(k) != before[k] for k in ("collection_type", "sku_prefix")):
            raise ValueError("Changing an existing folder layout or SKU allocation prefix requires a separate reviewed operation.")
    if edit.validation.valid:
        effective = edit.data
        if kind == "override":
            _, shared, _ = source(product, "shared")
            effective = resolved_metadata(product, json.loads(shared), edit.data)
        else:
            children = Product.query.filter_by(collection_id=product.collection_id).limit(MAX_SHARED_PRODUCTS+1).all() if product.collection_id else [product]
            if len(children) > MAX_SHARED_PRODUCTS:
                raise ValueError("Shared edit exceeds the 1000-product review bound; narrow the catalogue scope first.")
            for child in children:
                override = metadata_source(child, "override")
                # Single Variable shares one owner; don't merge its source twice.
                if override["reference"] == saved["source"]:
                    continue
                if override["exists"] and override["error"]:
                    raise ValueError(f"Child {child.sku} has invalid override metadata; repair it before a shared save.")
                child_effective = resolved_metadata(child, edit.data, override["data"] if override["exists"] else {})
                result = validate_product_info(child_effective, "collection")
                if not result.valid:
                    raise ValueError(f"Shared change is invalid for {child.sku}: {result.errors[0].message}")
        result = validate_product_info(effective, "collection")
        if not result.valid:
            raise ValueError("Effective inherited metadata is invalid: " + result.errors[0].message)
    return path, edit
