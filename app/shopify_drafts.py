"""Single Simple draft proof of concept, independent of Woo publishing."""
import hashlib
import io
import json
import os
import stat
import uuid
from datetime import datetime, UTC
from decimal import Decimal, InvalidOperation
from pathlib import Path

from flask import current_app
from itsdangerous import URLSafeTimedSerializer, BadSignature
from PIL import Image, UnidentifiedImageError
from sqlalchemy import or_

from app import db
from app.commerce import catalogue_snapshot
from app.commerce_config import shopify_settings
from app.catalogue_images import product_image_diagnostics, _source_context
from app.models import Product, ShopifyProductIdentity, CatalogueOperation
from app.shopify_connection import ShopifyAdapter, ShopifyConfiguration, ShopifyError, _gid
from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation

MAX_IMAGE = 10 * 1024 * 1024
MAX_TOTAL = 30 * 1024 * 1024
MAX_IMAGES = 6


def now():
    return datetime.now(UTC).replace(tzinfo=None)


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def configuration():
    settings = shopify_settings()
    return ShopifyConfiguration.from_settings(settings).validated()


def encode(evidence):
    value = json.dumps(evidence, sort_keys=True)
    if len(value.encode()) > 65536 or evidence.get("version") != 1:
        raise ShopifyError("Draft evidence exceeds the supported bound. Reduce description/images before reviewing.")
    return value


def evidence(row):
    try:
        result = json.loads(row.media_evidence_json)
        if result["version"] != 1:
            raise ValueError()
        return result
    except (ValueError, KeyError, TypeError):
        raise ShopifyError("Draft identity evidence is invalid. Administrator recovery review is required.") from None


def source_bytes(product, reference):
    context = _source_context(product)
    if not context:
        raise ShopifyError("Catalogue source is unavailable; no upload is authorized.")
    root, folder, _ = context
    try:
        path = (root / reference).resolve(strict=True)
        path.relative_to(root)
        path.relative_to(folder)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_IMAGE:
                raise ValueError()
            content = stream.read(MAX_IMAGE + 1)
        if len(content) > MAX_IMAGE:
            raise ValueError()
        with Image.open(io.BytesIO(content)) as image:
            mime, suffixes = {"PNG": ("image/png", {".png"}), "JPEG": ("image/jpeg", {".jpg", ".jpeg"}),
                              "WEBP": ("image/webp", {".webp"})}[image.format]
            image.verify()
        if path.suffix.lower() not in suffixes:
            raise ValueError()
        return content, path.name, mime
    except (OSError, ValueError, RuntimeError, KeyError, UnidentifiedImageError, Image.DecompressionBombError):
        raise ShopifyError("A selected source image is unsafe, corrupt, too large or has an unsupported/mismatched extension.") from None


def local_plan(product):
    snapshot = catalogue_snapshot(product)
    if snapshot.product_type != "simple" or snapshot.data["variations"] or product.catalogue_status != "active":
        raise ShopifyError("M3A supports one active Simple product with no child variations only.")
    if not product.source_relpath or not snapshot.sku:
        raise ShopifyError("Stable scanned source identity and SKU are required.")
    authored = snapshot.data["authored"]
    title = authored.get("title") or snapshot.data["title"]
    body = authored.get("description", snapshot.data["description"]) or ""
    if not isinstance(title, str) or not title.strip() or len(title) > 255 or not isinstance(body, str) or len(body) > 8000:
        raise ShopifyError("Review the title/description; this proof of concept supports titles up to 255 and descriptions up to 8,000 characters.")
    try:
        price = Decimal(str(authored.get("price", snapshot.data["price"])))
        if not price.is_finite() or price < 0 or price != price.quantize(Decimal("0.01")):
            raise ValueError()
    except (InvalidOperation, ValueError):
        raise ShopifyError("A finite, nonnegative price with at most two decimal places is required.") from None
    diagnostics = product_image_diagnostics(product)
    if len(diagnostics) > MAX_IMAGES or not diagnostics:
        raise ShopifyError("Select a product with one to six resolvable original catalogue images.")
    images = []
    for row in diagnostics:
        if not row["source_exists"] or not row["source_reference"]:
            raise ShopifyError("Every projected gallery image must resolve to an authorized catalogue source.")
        content, filename, mime = source_bytes(product, row["source_reference"])
        images.append({"source": row["source_reference"], "filename": filename, "extension": Path(filename).suffix,
                       "mime": mime, "size": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                       "alt": row["alt_text"] or title, "position": row["position"]})
    if sum(i["size"] for i in images) > MAX_TOTAL:
        raise ShopifyError("Selected images exceed the 30 MiB operation bound.")
    plan = {"product_id": product.id, "source_identity": snapshot.stable_identity, "sku": snapshot.sku,
            "title": title, "description": body, "price": format(price, ".2f"), "status": "DRAFT", "images": images}
    # Include resolved source/projection, not just the emitted subset, in freshness.
    plan["digest"] = digest({"plan": plan, "snapshot": dict(snapshot.data)})
    return plan


def serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="shopify-draft-preview-v1")


def matching_identity(store_key, product):
    return ShopifyProductIdentity.query.filter_by(store_key=store_key).filter(or_(
        ShopifyProductIdentity.product_id == product.id,
        ShopifyProductIdentity.source_identity == product.source_relpath)).first()


def preview(product, adapter=None):
    plan = local_plan(product)
    config = configuration()
    shop = (adapter or ShopifyAdapter()).test_connection(config)
    if "write_products" not in shop["scopes"]:
        raise ShopifyError("Grant write_products to create a reviewed draft; connection/discovery alone need only read_products.")
    store_key = digest({"id": shop["shop_id"], "domain": config.domain})
    row = matching_identity(store_key, product)
    if row and row.state != "failed_before_create":
        raise ShopifyError(f"This product already has Shopify attempt {row.id} ({row.state}). Open its identity and reconcile; another create is blocked.")
    plan.update(store_key=store_key, store_id=shop["shop_id"], domain=config.domain,
                config_digest=config.fingerprint(),
                nonce=uuid.uuid4().hex)
    plan["handle"] = "tlc-draft-" + plan["nonce"]
    encode({"version": 1, "plan": plan})
    token = serializer().dumps({k: plan[k] for k in ("product_id", "digest", "store_id", "store_key", "domain", "config_digest", "nonce")})
    return plan, token


def save(row, data, state=None):
    row.media_evidence_json = encode(data)
    if state:
        row.state = state
        if state in {"uncertain", "failed_before_create"}:
            row.verification_state = "unverified"
    row.updated_at = now()
    db.session.commit()


def payload_result(data, name):
    payload = data.get(name)
    if not isinstance(payload, dict):
        raise ShopifyError("Shopify returned incomplete mutation evidence; reconcile before any retry.")
    if payload.get("userErrors"):
        raise ShopifyError(f"Shopify rejected fields at {name}. Inspect permissions/product constraints in Shopify Admin; earlier successful steps remain recorded.")
    return payload


def confirm(token, acknowledged, adapter=None):
    if not acknowledged:
        raise ShopifyError("Explicit draft creation acknowledgement is required.")
    try:
        reviewed = serializer().loads(token, max_age=900)
    except BadSignature:
        raise ShopifyError("Draft Preview expired or is invalid. Generate a fresh Preview.") from None
    product = db.session.get(Product, reviewed["product_id"])
    if not product:
        raise ShopifyError("Local product is unavailable. Generate a fresh Preview.")
    plan = local_plan(product)
    config = configuration()
    adapter = adapter or ShopifyAdapter()
    shop = adapter.test_connection(config)
    if (plan["digest"] != reviewed["digest"] or shop["shop_id"] != reviewed["store_id"]
            or config.domain != reviewed["domain"] or "write_products" not in shop["scopes"]
            or config.fingerprint() != reviewed["config_digest"]):
        raise ShopifyError("Source, image bytes, credentials or store changed. Generate a fresh Preview.")
    plan.update({k: reviewed[k] for k in ("store_key", "store_id", "domain", "nonce")})
    plan["handle"] = "tlc-draft-" + plan["nonce"]
    lease = acquire_catalogue_operation("shopify_draft_publish", {"product_id": product.id, "sku": product.sku, "store_id": shop["shop_id"]})
    row = None
    try:
        row = matching_identity(plan["store_key"], product)
        if row and row.state != "failed_before_create":
            raise ShopifyError("A prior Shopify attempt exists. Reconcile its identity; no duplicate creation is allowed.")
        data = {"version": 1, "plan": plan, "stage": "pending", "create_attempted": False, "media": []}
        if row is None:
            row = ShopifyProductIdentity(product_id=product.id, source_identity=plan["source_identity"], sku=plan["sku"],
                store_key=plan["store_key"], store_id=plan["store_id"], store_domain=config.domain,
                reviewed_digest=plan["digest"], operation_id=lease.id, state="pending", verification_state="unverified",
                media_evidence_json=encode(data))
            db.session.add(row)
        else:
            row.operation_id, row.reviewed_digest, row.verification_state = lease.id, plan["digest"], "unverified"
        save(row, data, "pending")  # Durable uniqueness reservation BEFORE any mutation.
    except Exception:
        db.session.rollback()
        finish_catalogue_operation(lease.id, status="failed", error="Shopify identity is reserved by a prior attempt.")
        raise ShopifyError("Shopify identity is reserved. Review its retained attempt before creating another draft.") from None
    try:
        data["stage"] = "staged_uploads"
        save(row, data)
        stage = payload_result(adapter.stage_images(config, plan["images"]), "stagedUploadsCreate")
        targets = stage.get("stagedTargets")
        if not isinstance(targets, list) or len(targets) != len(plan["images"]):
            raise ShopifyError("Incomplete staged targets. Product creation was not attempted.")
        media_inputs = []
        for image, target in zip(plan["images"], targets):
            content, filename, mime = source_bytes(product, image["source"])
            if hashlib.sha256(content).hexdigest() != image["sha256"]:
                raise ShopifyError("A source image changed after review; product creation was not attempted.")
            adapter.upload_image(config, target, image, content)
            media_inputs.append({"originalSource": target["resourceUrl"], "mediaContentType": "IMAGE", "alt": image["alt"]})
            data["media"].append({"source": image["source"], "filename": filename, "sha256": image["sha256"], "upload": "completed"})
            save(row, data)
        # Recheck source after potentially slow uploads; no stale product create.
        if local_plan(product)["digest"] != plan["digest"]:
            raise ShopifyError("Local product changed during uploads. Product creation was not attempted.")
        data.update(stage="product_create", create_attempted=True)
        save(row, data, "creating")
        response = adapter.create_draft(config, {"title": plan["title"], "descriptionHtml": plan["description"],
            "status": "DRAFT", "handle": plan["handle"], "metafields": [{"namespace": "tlc_dashboard", "key": "publish_attempt",
                "type": "single_line_text_field", "value": plan["nonce"]}]}, media_inputs)
        created = (response.get("productCreate") or {}).get("product")
        if isinstance(created, dict) and created.get("id"):
            row.shopify_product_id = _gid(created["id"], "Product")
            data["created_media"] = (created.get("media") or {}).get("nodes", [])
            save(row, data, "created")
        payload_result(response, "productCreate")
        if not row.shopify_product_id:
            raise ShopifyError("Product creation returned no trusted ID; reconcile the recorded handle.")
        variants = (created.get("variants") or {}).get("nodes", [])
        if len(variants) != 1:
            raise ShopifyError("Created draft did not return exactly one Simple variant. Reconcile in Shopify Admin.")
        variant_id = _gid(variants[0]["id"], "ProductVariant")
        data["stage"] = "variant_update"
        save(row, data)
        payload_result(adapter.set_simple_variant(config, row.shopify_product_id, variant_id, plan["sku"], plan["price"]), "productVariantsBulkUpdate")
        data["stage"] = "independent_readback"
        save(row, data)
        apply_readback(row, data, adapter.read_draft(config, row.shopify_product_id))
        state = row.state
        finish_catalogue_operation(lease.id, status="succeeded" if state == "verified" else "partial",
            products_attempted=1, products_succeeded=int(state == "verified"), recovery_state="none" if state == "verified" else "review_required",
            operation_summary={"identity_id": row.id, "shopify_product_id": row.shopify_product_id, "stage": data["stage"], "state": state})
        return row
    except Exception as exc:
        # Raw transport exceptions/payloads must never leak tokens or signed targets.
        db.session.rollback()
        row = db.session.get(ShopifyProductIdentity, row.id)
        data = evidence(row)
        message = str(exc) if isinstance(exc, ShopifyError) else "Draft execution failed unexpectedly. Retained identity requires explicit reconciliation."
        data["error"] = message
        save(row, data, "uncertain" if data["create_attempted"] else "failed_before_create")
        finish_catalogue_operation(lease.id, status="partial" if data["create_attempted"] else "failed", products_attempted=1,
            products_failed=1, error=message, recovery_state="review_required" if data["create_attempted"] else "none",
            operation_summary={"identity_id": row.id, "stage": data["stage"], "state": row.state, "create_attempted": data["create_attempted"]})
        return row


def apply_readback(row, data, remote):
    plan = data["plan"]
    if (not isinstance(remote, dict) or remote.get("id") != row.shopify_product_id
            or remote.get("handle") != plan["handle"] or not isinstance(remote.get("metafield"), dict)
            or remote["metafield"].get("value") != plan["nonce"]):
        raise ShopifyError("Remote draft identity/correlation could not be independently verified. Another create remains blocked.")
    _gid(remote["id"], "Product")
    variants = remote.get("variants") or {}
    media = remote.get("media") or {}
    if (not isinstance(variants, dict) or not isinstance(media, dict)
            or not isinstance(variants.get("nodes"), list) or not isinstance(media.get("nodes"), list)
            or not all(isinstance(n, dict) for n in variants["nodes"] + media["nodes"])
            or not isinstance(variants.get("pageInfo"), dict) or not isinstance(media.get("pageInfo"), dict)):
        raise ShopifyError("Remote draft evidence is incomplete. Reconcile later; another create remains blocked.")
    for node in media["nodes"]:
        _gid(node.get("id"), "MediaImage")
        if node.get("image") is not None and not isinstance(node.get("image"), dict):
            raise ShopifyError("Remote media evidence is incomplete. Reconcile later; another create remains blocked.")
    data["recovery_remote_digest"] = digest(remote)
    data["checked_at"] = now().isoformat()
    data["observed_media"] = [{"id": m.get("id"), "url": (m.get("image") or {}).get("url"),
        "status": m.get("status"), "alt": m.get("alt")} for m in media.get("nodes", [])]
    # Persist successful observations even when a later verification fails.
    save(row, data, "created")
    nodes = variants.get("nodes", [])
    expected_media = data.get("created_media", [])
    if not expected_media and len(media.get("nodes", [])) == len(plan["images"]):
        # Creation timeout: preserve observations, but cannot prove original attachment ordering.
        raise ShopifyError("Draft was found after an uncertain create, but creation media receipts are missing. Inspect media/order in Shopify Admin; automatic verification is refused.")
    try:
        price_equal = len(nodes) == 1 and Decimal(str(nodes[0].get("price"))) == Decimal(plan["price"])
    except InvalidOperation:
        price_equal = False
    if (remote.get("status") != "DRAFT" or remote.get("title") != plan["title"]
            or remote.get("descriptionHtml") != plan["description"]
            or variants.get("pageInfo", {}).get("hasNextPage") is not False or len(nodes) != 1
            or nodes[0].get("sku") != plan["sku"] or not price_equal
            or media.get("pageInfo", {}).get("hasNextPage") is not False
            or len(expected_media) != len(plan["images"])
            or [m.get("id") for m in media.get("nodes", [])] != [m.get("id") for m in expected_media]
            or [m.get("alt") for m in media.get("nodes", [])] != [i["alt"] for i in plan["images"]]):
        raise ShopifyError("Draft fields, SKU/price or media/order differ from the reviewed plan. Inspect/fix the existing draft in Shopify Admin, then reconcile; no new product will be created.")
    for i, observed in zip(data["media"], data["observed_media"]):
        i.update(observed)
    if any(m["status"] == "FAILED" for m in data["observed_media"]):
        raise ShopifyError("Shopify image processing failed. Retained draft/media remain visible; inspect Shopify Admin before reconciliation.")
    ready = all(m["status"] == "READY" and isinstance(m["url"], str) and m["url"].startswith("https://") for m in data["observed_media"])
    row.verification_state = "verified" if ready else "processing"
    if ready:
        row.verified_at = now()
    data["stage"] = "verified" if ready else "media_processing"
    data.pop("error", None)
    save(row, data, "verified" if ready else "processing")


def recovery_token(row):
    data = evidence(row)
    if data.get("recovery_remote_digest") and not data.get("created_media"):
        return serializer().dumps({"identity": row.id, "remote": data["recovery_remote_digest"], "id": row.shopify_product_id})
    return None


def reconcile(row, candidate_id=None, adapter=None, approval=None):
    operation = db.session.get(CatalogueOperation, row.operation_id) if row.operation_id else None
    if operation and operation.status == "running":
        raise ShopifyError("This publishing operation is still active. Wait for completion; reconciliation cannot overlap writes.")
    config = configuration()
    adapter = adapter or ShopifyAdapter()
    shop = adapter.test_connection(config)
    if shop["shop_id"] != row.store_id or config.domain != row.store_domain:
        raise ShopifyError("Configured Shopify store does not match this identity. Restore its store configuration.")
    data = evidence(row)
    if not data["create_attempted"]:
        save(row, data, "failed_before_create")
        raise ShopifyError("No product creation was attempted. Generate a new Preview; staged-upload leftovers are not products and are never automatically deleted.")
    product_id = row.shopify_product_id or candidate_id
    if not product_id:
        found = adapter.find_draft(config, data["plan"]["handle"])
        if (not isinstance(found, dict) or not isinstance(found.get("nodes"), list)
                or not isinstance(found.get("pageInfo"), dict)
                or found["pageInfo"].get("hasNextPage") not in {True, False}
                or not all(isinstance(n, dict) for n in found["nodes"])):
            raise ShopifyError("Recovery search evidence is incomplete. Another create remains blocked.")
        if found["pageInfo"]["hasNextPage"] or len(found["nodes"]) != 1:
            raise ShopifyError("Uncertain create is not conclusively resolved. Search Shopify Admin for " + data["plan"]["handle"] + "; supply its GraphQL product ID here. An empty read does not authorize another create.")
        product_id = found["nodes"][0]["id"]
    remote = adapter.read_draft(config, _gid(product_id, "Product"))
    if (not isinstance(remote, dict) or not isinstance(remote.get("metafield"), dict)
            or remote["metafield"].get("value") != data["plan"]["nonce"]
            or remote.get("handle") != data["plan"]["handle"]):
        raise ShopifyError("Candidate draft does not match this operation's correlation. Identity remains protected.")
    row.shopify_product_id = _gid(product_id, "Product")
    save(row, data)
    if approval:
        try:
            reviewed = serializer().loads(approval, max_age=900)
        except BadSignature:
            raise ShopifyError("Recovery review expired. Reconcile again before approving observed media.") from None
        local = db.session.get(Product, row.product_id) if row.product_id else None
        if (reviewed != {"identity": row.id, "remote": digest(remote), "id": row.shopify_product_id}
                or not local or local_plan(local)["digest"] != row.reviewed_digest):
            raise ShopifyError("Remote/local state changed since recovery review. Review again; no mapping was adopted.")
        media = remote.get("media") or {}
        nodes = media.get("nodes", [])
        if len(nodes) != len(data["plan"]["images"]) or media.get("pageInfo", {}).get("hasNextPage") is not False:
            raise ShopifyError("Observed media is incomplete; approval cannot replace missing uploads.")
        for node in nodes:
            _gid(node.get("id"), "MediaImage")
        data["created_media"] = [{"id": m["id"], "alt": m.get("alt")} for m in nodes]
        data["media_mapping_approved_at"] = now().isoformat()
        save(row, data)
    try:
        apply_readback(row, data, remote)
    except ShopifyError as exc:
        data["error"] = str(exc)
        save(row, data, "uncertain")
        return row
    return row
