"""Single Simple draft proof of concept, independent of Woo publishing."""
import hashlib
import base64
import io
import json
import os
import stat
import uuid
import zlib
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
from app.customisations import CustomisationDefinitionError
from app.catalogue_images import product_image_diagnostics, _source_context
from app.models import Product, ShopifyProductIdentity, CatalogueOperation
from app.shopify_connection import ShopifyAdapter, ShopifyConfiguration, ShopifyError, _gid
from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation
from app import shopify_simple_fields as fields

MAX_IMAGE = 10 * 1024 * 1024
MAX_UPLOAD_BYTES = 30 * 1024 * 1024  # A bounded upload slice, not the whole gallery.
MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGES = 250  # Complete Shopify image gallery; transport/evidence byte bounds still apply.
UPLOAD_BATCH = 20
MAX_EVIDENCE_BYTES = 1024 * 1024


def now():
    return datetime.now(UTC).replace(tzinfo=None)


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def configuration():
    settings = shopify_settings()
    return ShopifyConfiguration.from_settings(settings).validated()


def encode(evidence):
    # Keep the existing SQLite bound. Large, repeated reviewed plans are stored
    # losslessly, not truncated; hashes still bind the fully expanded evidence.
    value = json.dumps(evidence, sort_keys=True, ensure_ascii=False)
    raw = value.encode()
    if len(raw) > MAX_EVIDENCE_BYTES or evidence.get("version") != 1:
        raise ShopifyError("Review evidence exceeds the 1 MiB decoded safety bound; no write attempted. Administrator evidence review is required.")
    if len(raw) <= 65536:
        return value
    packed = json.dumps({"version":1,"encoding":"zlib-json-v1","sha256":hashlib.sha256(raw).hexdigest(),
        "bytes":len(raw),"content":base64.b64encode(zlib.compress(raw)).decode()}, sort_keys=True)
    if len(packed.encode()) > 65536:
        raise ShopifyError("Lossless review evidence exceeds the database storage bound; no write attempted. Administrator evidence review is required.")
    return packed


def evidence(row):
    try:
        result = json.loads(row.media_evidence_json)
        if not isinstance(result,dict):
            raise ValueError()
        if result.get("encoding") == "zlib-json-v1":
            size = result.get("bytes")
            if type(size) is not int or not 0 < size <= MAX_EVIDENCE_BYTES:
                raise ValueError()
            stream = zlib.decompressobj()
            raw = stream.decompress(base64.b64decode(result["content"], validate=True), size + 1)
            if (len(raw) != size or not stream.eof or stream.unused_data or stream.unconsumed_tail
                    or hashlib.sha256(raw).hexdigest() != result["sha256"]):
                raise ValueError()
            result = json.loads(raw)
            if not isinstance(result,dict):
                raise ValueError()
        if result["version"] != 1:
            raise ValueError()
        return result
    except (ValueError, KeyError, TypeError, zlib.error):
        raise ShopifyError("Draft identity evidence is invalid. Administrator recovery review is required.") from None


def source_bytes(product, reference):
    context = _source_context(product)
    if not context:
        raise ShopifyError("Catalogue source is unavailable; no upload is authorized.")
    root, folder, _ = context
    name = Path(str(reference)).name
    try:
        path = (root / reference).resolve(strict=True)
        path.relative_to(root)
        path.relative_to(folder)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError()
            if info.st_size > MAX_IMAGE:
                raise ShopifyError(f'Image "{name}" is {info.st_size:,} bytes; the TLC individual-file upload limit is {MAX_IMAGE:,} bytes (10 MiB). Batching cannot fix an oversized individual file; no image was changed.')
            content = stream.read(MAX_IMAGE + 1)
        if len(content) > MAX_IMAGE:
            raise ShopifyError(f'Image "{name}" exceeds the TLC 10 MiB individual-file upload limit; no image was changed.')
        with Image.open(io.BytesIO(content)) as image:
            mime, suffixes = {"PNG": ("image/png", {".png"}), "JPEG": ("image/jpeg", {".jpg", ".jpeg"}),
                              "WEBP": ("image/webp", {".webp"})}[image.format]
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS or max(width, height) > 100 * min(width, height):
                raise ShopifyError(f'Image "{name}" is {width} × {height} pixels; the supported Shopify MediaImage API requires at most 20 megapixels and an aspect ratio within 100:1. No image was resized or compressed.')
            image.verify()
        if path.suffix.lower() not in suffixes:
            raise ValueError()
        return content, path.name, mime
    except ShopifyError:
        raise
    except (OSError, ValueError, RuntimeError, KeyError, UnidentifiedImageError, Image.DecompressionBombError):
        raise ShopifyError(f'Image "{name}" is unavailable, unsafe, corrupt or has an unsupported/mismatched extension. Supported original formats: PNG, JPEG and WebP; no image was changed.') from None


def local_plan(product):
    if product.product_type == "variable":
        from app.shopify_variables import local_plan as variable_plan
        return variable_plan(product)
    return common_plan(product)


def common_plan(product, *, variable=False):
    try:
        snapshot = catalogue_snapshot(product)
    except ValueError as exc:
        raise ShopifyError(str(exc)) from None
    if (not variable and (snapshot.product_type != "simple" or snapshot.data["variations"])) or product.catalogue_status != "active":
        raise ShopifyError("M3A supports one active Simple product with no child variations only.")
    if not product.source_relpath or not snapshot.sku:
        raise ShopifyError("Stable scanned source identity and SKU are required.")
    authored = snapshot.data["authored"]
    from app.shopify_publication import intent
    from app.shopify_connection import ShopifyReadinessFinding
    try:
        publication_intent = intent(authored)
    except ShopifyError as exc:
        raise ShopifyReadinessFinding(str(exc), code='publication_intent') from None
    title = authored.get("title") or snapshot.data["title"]
    body = authored.get("description", snapshot.data["description"]) or ""
    if not isinstance(title, str) or not title.strip() or len(title) > 255 or not isinstance(body, str) or len(body.encode()) > 262144:
        raise ShopifyReadinessFinding("Review the title/description; titles must fit 255 characters and description HTML the 256 KiB transport safety budget.", code='required_metadata')
    try:
        price = Decimal(str(authored.get("price", snapshot.data["price"])))
        if not price.is_finite() or price < 0 or price != price.quantize(Decimal("0.01")):
            raise ValueError()
    except (InvalidOperation, ValueError):
        raise ShopifyReadinessFinding("A finite, nonnegative price with at most two decimal places is required.", code='required_metadata') from None
    diagnostics = product_image_diagnostics(product)
    if variable:
        from app.shopify_variables import gallery_diagnostics
        diagnostics = gallery_diagnostics(product, diagnostics)
    limit = MAX_IMAGES
    from app.shopify_connection import ShopifyReadinessFinding
    if not diagnostics:
        raise ShopifyReadinessFinding('Publishing needs original catalogue/gallery artwork. Check the source folder and image references; no images are removed.', code='missing_images')
    if len(diagnostics) > limit:
        raise ShopifyReadinessFinding(f'Gallery exceeds the supported publication bound: {len(diagnostics)} images; limit {limit}. No images are truncated or removed.', code='gallery_bound')
    images = []
    for row in diagnostics:
        if not row["source_exists"] or not row["source_reference"]:
            raise ShopifyError("Every projected gallery image must resolve to an authorized catalogue source.")
        content, filename, mime = source_bytes(product, row["source_reference"])
        images.append({"source": row["source_reference"], "filename": filename, "extension": Path(filename).suffix,
                       "mime": mime, "size": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                       "alt": row["alt_text"] or title, "position": row["position"]})
    plan = {"product_id": product.id, "source_identity": snapshot.stable_identity, "sku": snapshot.sku,
            "title": title, "description": body, "price": format(price, ".2f"), "status": "DRAFT", "images": images}
    plan["managed"], plan["warnings"] = fields.contract(snapshot, plan)
    plan["intent"] = publication_intent['status'].title()
    plan["blockers"] = []
    if publication_intent['status'] != 'DRAFT' or publication_intent['channels']:
        plan['warnings'].append('New products start as unpublished Drafts. Existing products include eligible reviewed status/channel changes; incomplete or blocked availability is explicitly deferred.')
    from app.shopify_taxonomy import fingerprint as taxonomy_fingerprint
    plan["taxonomy_fingerprint"] = taxonomy_fingerprint(product)
    plan["price"] = plan["managed"]["variant"]["price"]
    plan["unsupported"] = ["Storefront navigation publishing is deferred", "Customer-facing customisation",
        "Inventory quantities/locations", "Media replacement, addition or removal on existing drafts",
        "Shopify saved-package assignment readback/clearing is unavailable; reviewed writes record acceptance only"]
    from app.shopify_products import source_fingerprint
    plan["workspace_source_fingerprint"] = source_fingerprint(product, images)
    from app.shopify_native_dimensions import desired
    plan["native_policy"] = desired(product)
    if plan["native_policy"]["enabled"]:
        plan["unsupported"] = [v for v in plan["unsupported"] if not v.startswith("Native Shopify Shipping/package")]
        plan["unsupported"].append("Shopify saved package presets / carrier labels")
    # Include resolved source/projection, not just the emitted subset, in freshness.
    plan["digest"] = digest({"plan": plan, "snapshot": dict(snapshot.data)})
    return plan


def serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="shopify-draft-preview-v1")


def matching_identity(store_id, product):
    # Read legacy domain-keyed rows without rewriting them. The verified Shop
    # GID, not a changeable endpoint, owns the duplicate-prevention boundary.
    rows = ShopifyProductIdentity.query.filter_by(store_id=store_id).filter(or_(
        ShopifyProductIdentity.product_id == product.id,
        ShopifyProductIdentity.source_identity == product.source_relpath)).limit(2).all()
    if len(rows) > 1:
        raise ShopifyError("Multiple retained Shopify identities require administrator review; another create is blocked.")
    if not rows and ShopifyProductIdentity.query.filter(or_(
            ShopifyProductIdentity.product_id == product.id,
            ShopifyProductIdentity.source_identity == product.source_relpath)).first():
        raise ShopifyError("Recovery required: this local product has a retained identity in another Shopify store. Restore that store; automatic copying/migration is not supported.")
    return rows[0] if rows else None


def require_absent_sku(adapter, config, sku):
    matches = adapter.find_sku(config, sku)
    if not isinstance(matches.get('nodes'), list) or matches.get('pageInfo', {}).get('hasNextPage') is not False:
        raise ShopifyError('Exact SKU discovery is incomplete. Another create is blocked; retry read-only discovery.')
    if matches['nodes']:
        from app.shopify_connection import ShopifyReadinessFinding
        raise ShopifyReadinessFinding('Existing Shopify SKU candidate found. Reconciliation review is required; never create a duplicate or adopt by title. First-time mapping without a retained identity needs a separately supported linking workflow.', code='reconciliation')


def preview(product, adapter=None, *, context=None, scope="full"):
    started = now().isoformat()
    try:
        return _preview(product, adapter, context=context, scope=scope, started=started)
    except CustomisationDefinitionError as exc:
        from app.shopify_connection import ShopifyReadinessFinding
        from app.shopify_comparison import failed
        failed(product, str(exc), started=started)
        raise ShopifyReadinessFinding(str(exc), code='customisation_configuration') from None
    except ShopifyError as exc:
        from app.shopify_comparison import failed
        failed(product, str(exc), started=started)
        raise


def _preview(product, adapter=None, *, context=None, scope="full", started=None):
    if scope == 'customisations':
        from app.shopify_customisations import preview as customisation_preview
        return customisation_preview(product, adapter)
    if scope not in {"full","inventory"}:
        raise ShopifyError("Choose a supported review scope.")
    if product.product_type == "variable":
        from app.shopify_variables import preview as variable_preview
        return variable_preview(product, adapter, context=context, scope=scope, started=started)
    plan = local_plan(product)
    plan['comparison_started_at'] = started or now().isoformat()
    config = configuration()
    adapter = adapter or ShopifyAdapter()
    shop = context["shop"] if context and context["config"] == config.fingerprint() else adapter.test_connection(config)
    can_write = "write_products" in shop["scopes"]
    store_key = digest({"id": shop["shop_id"]})
    row = matching_identity(shop["shop_id"], product)
    from app.shopify_comparison import identity_revision
    plan['read_identity_digest'] = identity_revision(row)
    if scope == "inventory" and (not row or row.state not in {"verified","scoped_verified"}):
        raise ShopifyError("Inventory-only Review requires a trusted retained product; reconcile unresolved operations first.")
    plan["scope"] = scope
    if row and (row.source_identity!=product.source_relpath or row.sku!=product.sku):
        raise ShopifyError("Stable local source/SKU does not match its retained Shopify identity.")
    if row and row.state == "processing":
        # A known successful write with asynchronous media is not an uncertain
        # write. Finalize through the same complete retained-plan readback only.
        finalize_processing(row, adapter)
        if row.state == "processing":
            raise ShopifyError("Shopify media/order is still processing. Refresh status shortly; no retry or new upload is needed.")
    if row and evidence(row).get("native_dimensions", {}).get("state") in {"pending", "uncertain"}:
        raise ShopifyError("Recovery required for native packed dimensions. Open the native dimension review and reconcile using reads only; normal product/media verification is retained.")
    if row:
        from app.shopify_package_assignments import guard as package_guard
        package_guard(evidence(row))
    from app.models import ShopifyProductComparison
    previous_comparison = ShopifyProductComparison.query.filter_by(store_id=shop["shop_id"], source_identity=product.source_relpath).first()
    if previous_comparison and json.loads(previous_comparison.summary_json).get("conflicting_changes"):
        plan["warnings"].append("Both local and Shopify data changed since the checkpoint. Inspect before/after values before replacing remote data.")
        plan["conflicting_changes"] = True
    from app.shopify_taxonomy import membership_plan
    plan["collections"], blockers, standard = ([],[],None) if scope == "inventory" else membership_plan(product, shop["shop_id"], row.shopify_product_id if row else None, config, adapter)
    plan["blockers"] += blockers
    if standard:
        plan["standard_category"] = adapter.standard_category(config, standard)
        plan["managed"]["product"]["category"] = standard
    else:
        plan["warnings"].append("Standard Shopify category is optional/unresolved; no category is inferred from TLC names.")
    plan["taxonomy_review_digest"] = digest({"collections": plan["collections"], "standard": standard})
    origin = plan["managed"]["variant"]["inventoryItem"].get("countryCodeOfOrigin")
    if origin and origin not in adapter.country_codes(config):
        raise ShopifyError("Country of origin is not supported by Shopify's current CountryCode contract.")
    if row and row.state not in {"failed_before_create", "remote_deleted"} and not upload_pending(row):
        if row.state not in {"verified","attribute_review_required","scoped_verified"} or row.verification_state not in {"verified","base_verified","inventory_verified"}:
            raise ShopifyError(f"Recovery required for Shopify identity {row.id} ({row.state}). Reconcile its retained operation before any write; another create is blocked.")
        result, token = update_preview(product, row, plan, config, shop, adapter)
        from app.shopify_native_dimensions import attach_review
        if scope == "inventory":
            from app.shopify_inventory_review import restrict
            restrict(result)
            result["native"] = {"enabled":False,"classification":"deferred","reason":"Native measurements are outside inventory-only policy correction."}
        else:
            attach_review(product, result, row, config, shop, adapter)
            from app.shopify_dimension_metadata import attach as attach_dimensions
            attach_dimensions(result, config, shop, adapter)
            if any(f['changed'] for f in result['fields']):
                result['classification'] = 'update'
            from app.shopify_optional_packages import attach as attach_packages
            attach_packages(product, result, row, config, shop)
            from app.shopify_publication import annotate
            annotate(product,result,row,config,shop,adapter)
        if result["classification"] == "update":
            token = serializer().dumps({"mode": "update", "scope":scope, **{key: result[key] for key in (
                "product_id", "digest", "identity_id", "remote_id", "store_id", "domain", "config_digest",
                "remote_digest", "identity_digest", "taxonomy_review_digest")}})
        if result["classification"] == "update" and not can_write:
            result["blockers"].append("Missing write_products; read-only comparison is available, writes are blocked.")
        if row.state=="attribute_review_required" and result["classification"]=="no_change":
            result["blockers"].append("Remaining attributes now match; complete read-only identity reconciliation before claiming final publishing verification.")
        from app.shopify_comparison import record
        record(product, result, row)
        from app.shopify_publication import has_reviewed_changes
        return result, token if not result["blockers"] and (scope=='inventory' or has_reviewed_changes(result)) else None
    require_absent_sku(adapter, config, plan["sku"])
    plan.update(store_key=store_key, store_id=shop["shop_id"], domain=config.domain,
                config_digest=config.fingerprint(),
                nonce=evidence(row)['plan']['nonce'] if upload_pending(row) else uuid.uuid4().hex)
    plan["handle"] = plan["managed"]["product"].get("handle") or "tlc-draft-" + plan["nonce"]
    if "handle" in plan["managed"]["product"] and adapter.find_draft(config, plan["handle"])["nodes"]:
        raise ShopifyError("The authored handle already belongs to a Shopify product. Another create is refused.")
    plan["classification"] = "create"
    if not can_write:
        plan["blockers"].append("Missing write_products; read-only comparison is available, writes are blocked.")
    plan["fields"] = [{"field": key, "before": None, "after": value, "changed": value is not None}
        for key, value in plan["managed"]["product"].items()]
    plan["fields"] += [{"field": key, "before": None, "after": value, "changed": value is not None}
        for key, value in plan["managed"]["variant"].items()]
    from app.shopify_customisations import attach as attach_customisations
    attach_customisations(product, plan, config, shop, adapter)
    from app.shopify_attributes import attach
    attach(product, plan, config, shop, adapter)
    from app.shopify_dimension_metadata import attach as attach_dimensions
    attach_dimensions(plan, config, shop, adapter)
    from app.shopify_optional_packages import attach as attach_packages
    attach_packages(product, plan, None, config, shop)
    from app.shopify_native_dimensions import attach_review
    attach_review(product, plan, None, config, shop, adapter)
    from app.shopify_publication import annotate
    annotate(product,plan,None,config,shop,adapter)
    from app.shopify_customisations import reserve as customisation_reserve
    encode({"version": 1, "plan": plan, 'storefront_configuration':customisation_reserve(plan), "reserve": "x" * 12000})
    token = serializer().dumps({k: plan[k] for k in ("product_id", "digest", "store_id", "store_key", "domain", "config_digest", "nonce", "taxonomy_review_digest")})
    from app.shopify_comparison import record
    record(product, plan, row)
    return plan, token if not plan["blockers"] else None


def save(row, data, state=None):
    from app.shopify_capacity import guard_owner
    guard_owner()
    from flask import has_request_context,g
    if has_request_context() and g.get("shopify_request_summary"):
        data["api_requests"]=g.shopify_request_summary
    encoded = encode(data)
    from app.shopify_capacity import fence_owner
    fence_owner()
    from app.publishing_progress import capture
    capture(row,data)
    row.media_evidence_json = encoded
    if state:
        row.state = state
        if state in {"uncertain", "failed_before_create"}:
            row.verification_state = "unverified"
    row.updated_at = now()
    from app.catalogue_views import invalidate as invalidate_view
    invalidate_view(product_id=row.product_id)
    db.session.commit()


def completed_step(row,data,stage):
    steps=data.setdefault("completed_steps",[])
    if stage not in steps: steps.append(stage)
    save(row,data)


def payload_result(data, name):
    payload = data.get(name)
    if not isinstance(payload, dict):
        raise ShopifyError("Shopify returned incomplete mutation evidence; reconcile before any retry.")
    if payload.get("userErrors"):
        raise ShopifyError(f"Shopify rejected fields at {name}. Inspect permissions/product constraints in Shopify Admin; earlier successful steps remain recorded.")
    return payload


def upload_batch(images, *, count=UPLOAD_BATCH, byte_limit=MAX_UPLOAD_BYTES):
    """Ordered prefix bounded by both file count and original bytes, never resized."""
    batch, size = [], 0
    for image in images:
        amount = image.get('size')
        if type(amount) is not int or not 0 < amount <= MAX_IMAGE:
            raise ShopifyError(f'Image "{image.get("filename", "Unknown image")}" has an invalid or oversized individual-file byte count. Rebuild the review from its original file.')
        if len(batch) >= count or size + amount > byte_limit:
            break
        batch.append(image)
        size += amount
    return batch


def stage_gallery(adapter, config, images):
    """Bounded staging batches, one complete ordered creation input; no truncation."""
    targets = []
    offset = 0
    while offset < len(images):
        batch = upload_batch(images[offset:])
        stage = payload_result(adapter.stage_images(config, batch), 'stagedUploadsCreate')
        part = stage.get('stagedTargets')
        if not isinstance(part, list) or len(part) != len(batch):
            raise ShopifyError('Incomplete staged image batch. Product creation was not attempted.')
        targets.extend(part)
        offset += len(batch)
    return targets


class GalleryPending(ShopifyError):
    """Known acknowledged pre-create uploads, intentionally yielding a slice."""


def upload_pending(row):
    if not row or row.state != 'uploading' or row.shopify_product_id:
        return False
    data = evidence(row)
    plan = data.get('plan') or {}
    uploads = data.get('upload_inputs')
    return (data.get('create_attempted') is False and isinstance(uploads, list)
        and 0 <= len(uploads) <= len(plan.get('images', [])) and plan.get('digest') == row.reviewed_digest
        and data.get('upload_attempt', {}).get('state') != 'pending'
        and all(isinstance(u, dict) and u.get('source') == image['source'] and u.get('sha256') == image['sha256']
                and isinstance(u.get('input'), dict) for u, image in zip(uploads, plan['images'])))


def upload_gallery(product, row, data, plan, config, adapter):
    """Resume acknowledged staged resources; never resend a completed upload.

    Only resource URLs (not signed upload parameters) persist. A timeout is not
    an acknowledgement and never becomes this safe continuation state.
    """
    from app.shopify_capacity import background
    from time import monotonic
    uploads = data.setdefault('upload_inputs', [])
    if data.get('upload_no_progress', 0) >= 3:
        raise ShopifyError('Gallery upload could not make progress within three bounded slices. Inspect its receipt and API capacity before retrying; no product Create was attempted.')
    data['upload_start_count'] = len(uploads)
    for saved, image in zip(uploads, plan['images']):
        if saved.get('source') != image['source'] or saved.get('sha256') != image['sha256']:
            raise ShopifyError('Retained upload belongs to different original artwork. No staged resource was reused.')
    if len(uploads) > len(plan['images']):
        raise ShopifyError('Retained upload count conflicts with the reviewed complete gallery.')
    completed, completed_bytes = 0, 0
    batches=[];offset=0
    while offset<len(plan['images']):
        part=upload_batch(plan['images'][offset:]);offset+=len(part);batches.append(offset)
    data['upload_batch_total']=len(batches)
    while len(uploads) < len(plan['images']):
        budget = background()
        if completed >= UPLOAD_BATCH or budget and (budget['remaining'] < 4 or budget['deadline']-monotonic() < 35):
            raise GalleryPending('Acknowledged image uploads retained; next bounded slice continues without re-uploading.')
        batch = upload_batch(plan['images'][len(uploads):], count=UPLOAD_BATCH-completed,
                             byte_limit=MAX_UPLOAD_BYTES-completed_bytes)
        if not batch:
            raise GalleryPending('The 30 MiB upload-slice budget is reached; acknowledged originals are retained for the next bounded slice.')
        number=next(i+1 for i,end in enumerate(batches) if end>len(uploads))
        batch=batch[:batches[number-1]-len(uploads)]
        data.update(upload_batch_number=number,stage='upload_batch_prepared')
        save(row,data)
        targets = stage_gallery(adapter, config, batch)
        data['stage']='upload_batch_started';save(row,data)
        used = {u['input']['originalSource'] for u in uploads}
        resources = [t.get('resourceUrl') for t in targets if isinstance(t,dict)]
        if len(resources) != len(batch) or len(set(resources)) != len(resources) or used.intersection(resources):
            raise ShopifyError('Staged image resource identities are duplicated/incomplete; no product Create attempted.')
        for image, target in zip(batch, targets):
            budget = background()
            if budget and (budget['remaining'] < 3 or budget['deadline']-monotonic() < 35):
                raise GalleryPending('Acknowledged image uploads retained for the next bounded slice.')
            content, filename, _ = source_bytes(product, image['source'])
            if len(content) != image['size'] or hashlib.sha256(content).hexdigest() != image['sha256']:
                raise ShopifyError('Original image changed since review; no product Create attempted.')
            # Durable before the binary request. If interrupted here, this is
            # NOT a known completed upload and requires explicit review.
            data['upload_attempt'] = {'source': image['source'], 'state': 'pending'}
            save(row, data)
            adapter.upload_image(config, target, image, content)
            value = {'originalSource': target['resourceUrl'], 'mediaContentType': 'IMAGE', 'alt': image['alt']}
            uploads.append({'source': image['source'], 'sha256': image['sha256'], 'input': value})
            data['media'].append({'source': image['source'], 'filename': filename, 'sha256': image['sha256'], 'upload': 'completed'})
            data['upload_attempt'] = {'source': image['source'], 'state': 'completed'}
            data['last_upload_ack_at']=now().isoformat()+'Z'
            if len(uploads)==batches[number-1]:data['stage']='batch_acknowledged'
            completed += 1
            completed_bytes += len(content)
            save(row, data)
    return [u['input'] for u in uploads]


def confirm(token, acknowledged, adapter=None, *, expected_product_id=None):
    if not acknowledged:
        raise ShopifyError("Explicit draft creation acknowledgement is required.")
    try:
        reviewed = serializer().loads(token, max_age=900)
    except BadSignature:
        raise ShopifyError("Draft Preview expired or is invalid. Generate a fresh Preview.") from None
    return execute_reviewed(reviewed, adapter, expected_product_id=expected_product_id)


def execute_reviewed(reviewed, adapter=None, *, expected_product_id=None):
    """Shared executor, not an HTTP approval endpoint.

    Manual callers arrive only after confirm's signature/acknowledgement gate.
    Automatic callers require a separately persisted, fenced publishing grant.
    Neither path skips source/digest, identity, reservation or readback checks.
    """
    if expected_product_id is not None and reviewed.get("product_id") != expected_product_id:
        raise ShopifyError("Reviewed product does not match this Products workspace. Open its own review; no write occurred.")
    if reviewed.get('mode') == 'package_correction':
        from app.shopify_package_assignments import correction_confirm
        return correction_confirm(reviewed, adapter)
    if reviewed.get('mode') == 'customisations':
        from app.shopify_customisations import confirm as customisation_confirm
        return customisation_confirm(reviewed, adapter)
    if reviewed.get("scope") == "inventory":
        from app.shopify_inventory_review import confirm as inventory_confirm
        return inventory_confirm(reviewed,adapter)
    if reviewed.get('mode') == 'publication':
        from app.shopify_publication import confirm as publication_confirm
        return publication_confirm(reviewed,adapter)
    if reviewed.get("mode") in {"variable","variable_resume"}:
        from app.shopify_variables import confirm as variable_confirm
        return variable_confirm(reviewed, adapter)
    if reviewed.get("mode") == "update":
        return confirm_update(reviewed, adapter)
    product = db.session.get(Product, reviewed["product_id"])
    if not product:
        raise ShopifyError("Local product is unavailable. Generate a fresh Preview.")
    plan = local_plan(product)
    if plan["blockers"]:
        raise ShopifyError("Local product validation blockers remain. Resolve them and build a fresh reviewed plan.")
    config = configuration()
    adapter = adapter or ShopifyAdapter()
    shop = adapter.test_connection(config)
    origin = plan["managed"]["variant"]["inventoryItem"].get("countryCodeOfOrigin")
    if origin and origin not in adapter.country_codes(config):
        raise ShopifyError("Country of origin is unsupported; generate a fresh Preview.")
    if (plan["digest"] != reviewed["digest"] or shop["shop_id"] != reviewed["store_id"]
            or config.domain != reviewed["domain"] or "write_products" not in shop["scopes"]
            or config.fingerprint() != reviewed["config_digest"]):
        raise ShopifyError("Source, image bytes, credentials or store changed. Generate a fresh Preview.")
    plan.update({k: reviewed[k] for k in ("store_key", "store_id", "domain", "nonce")})
    plan.update(config_digest=reviewed["config_digest"], classification="create")
    from app.shopify_taxonomy import membership_plan
    plan["collections"], blockers, standard = membership_plan(product, shop["shop_id"], None, config, adapter)
    if blockers:
        raise ShopifyError("Collection mappings are unresolved. Review again after resolving mappings.")
    if standard:
        plan["managed"]["product"]["category"] = adapter.standard_category(config, standard)["id"]
    plan["taxonomy_review_digest"] = digest({"collections": plan["collections"], "standard": standard})
    plan["fields"] = []
    from app.shopify_customisations import attach as attach_customisations
    attach_customisations(product, plan, config, shop, adapter)
    from app.shopify_attributes import attach
    attach(product, plan, config, shop, adapter)
    if plan["blockers"]:
        raise ShopifyError("Required attribute/intent prerequisites are unresolved; rebuild Review.")
    from app.shopify_dimension_metadata import attach as attach_dimensions
    attach_dimensions(plan, config, shop, adapter)
    from app.shopify_optional_packages import attach as attach_packages
    attach_packages(product, plan, None, config, shop)
    from app.shopify_native_dimensions import attach_review
    attach_review(product, plan, None, config, shop, adapter)
    from app.shopify_publication import annotate
    annotate(product,plan,None,config,shop,adapter)
    if plan["taxonomy_review_digest"] != reviewed.get("taxonomy_review_digest"):
        raise ShopifyError("Collection mapping/membership changed after review. Review again.")
    plan["handle"] = plan["managed"]["product"].get("handle") or "tlc-draft-" + plan["nonce"]
    if "handle" in plan["managed"]["product"] and adapter.find_draft(config, plan["handle"])["nodes"]:
        raise ShopifyError("The reviewed handle is now occupied. Generate a fresh Preview.")
    lease = acquire_catalogue_operation("shopify_draft_publish", {"product_id": product.id, "sku": product.sku, "store_id": shop["shop_id"]})
    row = None
    try:
        row = matching_identity(plan["store_id"], product)
        if row and row.state not in {"failed_before_create", "remote_deleted"} and not upload_pending(row):
            raise ShopifyError("A prior Shopify attempt exists. Reconcile its identity; no duplicate creation is allowed.")
        require_absent_sku(adapter, config, plan["sku"])
        if upload_pending(row):
            data = evidence(row)
            if data['plan']['digest'] != plan['digest'] or data['plan'].get('config_digest') != config.fingerprint():
                raise ShopifyError('Incomplete upload source/configuration changed. Inspect its retained receipt; no product Create attempted.')
            data['plan'] = plan
        else:
            data = {"version": 1, "plan": plan, "stage": "pending", "create_attempted": False, "media": []}
        if row and row.state == "remote_deleted":
            data["history_operation"] = evidence(row).get("history_operation")
        if row is None:
            row = ShopifyProductIdentity(product_id=product.id, source_identity=plan["source_identity"], sku=plan["sku"],
                store_key=plan["store_key"], store_id=plan["store_id"], store_domain=config.domain,
                reviewed_digest=plan["digest"], operation_id=lease.id, state="pending", verification_state="unverified",
                media_evidence_json=encode(data))
            db.session.add(row)
        else:
            if row.product_id is None:
                row.product_id=product.id  # reviewed stable source/SKU, not a remote-ID adoption
            row.operation_id, row.reviewed_digest, row.verification_state = lease.id, plan["digest"], "unverified"
        save(row, data, "pending")  # Durable uniqueness reservation BEFORE any mutation.
        from app.shopify_comparison import invalidate
        invalidate(row.store_id, row.source_identity)
    except Exception as exc:
        db.session.rollback()
        message = str(exc) if isinstance(exc, ShopifyError) else "Shopify identity is reserved. Review its retained attempt before creating another draft."
        finish_catalogue_operation(lease.id, status="failed", error=message)
        raise ShopifyError(message) from None
    try:
        data["stage"] = "staged_uploads"
        save(row, data)
        media_inputs = upload_gallery(product, row, data, plan, config, adapter)
        completed_step(row,data,"staged_uploads")
        completed_step(row,data,"original_image_uploads")
        # Recheck source after potentially slow uploads; no stale product create.
        if local_plan(product)["digest"] != plan["digest"]:
            raise ShopifyError("Local product changed during uploads. Product creation was not attempted.")
        data.update(stage="product_create", create_attempted=True)
        save(row, data, "creating")
        product_input = dict(plan["managed"]["product"])
        # The independent attempt metafield remains the durable correlation,
        # including when an explicitly reviewed authored handle is used.
        product_input.update(status="DRAFT", handle=plan["handle"], metafields=[
            {"namespace": "tlc_dashboard", "key": "publish_attempt", "type": "single_line_text_field", "value": plan["nonce"]},
            *plan["managed"]["product"].get("metafields", [])])
        response = adapter.create_draft(config, product_input, media_inputs)
        created = (response.get("productCreate") or {}).get("product")
        if isinstance(created, dict) and created.get("id"):
            row.shopify_product_id = _gid(created["id"], "Product")
            data["created_media"] = (created.get("media") or {}).get("nodes", [])
            save(row, data, "created")
        payload_result(response, "productCreate")
        completed_step(row,data,"product_create")
        if not row.shopify_product_id:
            raise ShopifyError("Product creation returned no trusted ID; reconcile the recorded handle.")
        variants = (created.get("variants") or {}).get("nodes", [])
        if len(variants) != 1:
            raise ShopifyError("Created draft did not return exactly one Simple variant. Reconcile in Shopify Admin.")
        variant_id = _gid(variants[0]["id"], "ProductVariant")
        data["variant_id"] = variant_id
        data["stage"] = "variant_update"
        save(row, data)
        payload_result(adapter.update_simple_variant(config, row.shopify_product_id,
            {"id": variant_id, **plan["managed"]["variant"]}), "productVariantsBulkUpdate")
        completed_step(row,data,"variant_update")
        data["stage"] = "independent_readback"
        save(row, data)
        from app.shopify_taxonomy import join_reviewed
        join_reviewed(row, data, config, adapter)
        from app.shopify_attributes import apply
        apply(row, data, config, adapter)
        from app.shopify_customisations import apply as apply_customisations
        apply_customisations(row, data, config, adapter)
        from app.shopify_dimension_metadata import apply as apply_dimensions
        apply_dimensions(row, data, config, adapter)
        apply_readback(row, data, adapter.read_simple(config, row.shopify_product_id, plan["managed"]["namespace"]), adapter)
        from app.shopify_publication import apply_reviewed, complete as publication_complete
        if apply_reviewed(row,data,config,adapter):
            apply_readback(row,data,adapter.read_simple(config,row.shopify_product_id,plan['managed']['namespace']),adapter)
        state = row.state
        from app.shopify_native_dimensions import after_publish
        finalisation_stage = 'native_dimensions'
        native_state = after_publish(row, plan, adapter, lease.id)
        from app.shopify_package_assignments import apply as apply_packages
        finalisation_stage = 'package_preflight'
        package_state=apply_packages(row,adapter,lease.id)
        finalisation_stage = 'operation_finalisation'
        finish_catalogue_operation(lease.id, status="succeeded" if state == "verified" and native_state in {"disabled", "verified", "unconfigured"} and package_state in {'none', 'accepted'} and publication_complete(evidence(row)) else "partial",
            products_attempted=1, products_succeeded=int(state == "verified"), recovery_state="none" if state == "verified" and native_state != "uncertain" and package_state!='uncertain' else "review_required",
            operation_summary={"identity_id": row.id, "shopify_product_id": row.shopify_product_id, "stage": evidence(row)['stage'], "state": row.state, "native_dimensions": native_state,'package_assignment':package_state})
        return row
    except GalleryPending as exc:
        data['upload_no_progress'] = data.get('upload_no_progress',0)+1 if len(data.get('upload_inputs',[]))==data.get('upload_start_count') else 0
        data.update(stage='gallery_uploading', error=None)
        save(row, data, 'uploading')
        finish_catalogue_operation(lease.id,status='partial',recovery_state='none',products_attempted=1,
            operation_summary={'identity_id':row.id,'stage':'gallery_uploading','create_attempted':False,
                'uploads_acknowledged':len(data.get('upload_inputs',[])),'images':len(plan['images'])})
        return row
    except Exception as exc:
        # Raw transport exceptions/payloads must never leak tokens or signed targets.
        db.session.rollback()
        row = db.session.get(ShopifyProductIdentity, row.id)
        data = evidence(row)
        message = str(exc) if isinstance(exc, ShopifyError) else "Draft execution failed unexpectedly. Retained identity requires explicit reconciliation."
        data["error"] = message
        diagnostic = failure_diagnostic(exc, locals().get('finalisation_stage') or data.get('stage'), lease.id)
        data['failure_diagnostic'] = diagnostic
        data['stage'] = 'recovery_required' if data['create_attempted'] else 'failed_before_create'
        save(row, data, "uncertain" if data["create_attempted"] else "failed_before_create")
        finish_catalogue_operation(lease.id, status="partial" if data["create_attempted"] else "failed", products_attempted=1,
            products_failed=1, error=message, recovery_state="review_required" if data["create_attempted"] else "none",
            operation_summary={"identity_id": row.id, "stage": diagnostic['stage'], "state": row.state,
                "create_attempted": data["create_attempted"], "diagnostic": diagnostic})
        return row


def failure_diagnostic(error, stage, operation_id):
    """Retain structural context, never arbitrary exception payloads/SQL/URLs."""
    from app.shopify_capacity import BackgroundYield
    from app.utils.redaction import redact_diagnostic
    reasons = {KeyError: 'Required internal state key is missing.',
        ValueError: 'Internal value validation failed.', TypeError: 'Internal state has an unexpected type.',
        TimeoutError: 'An execution step timed out.', OSError: 'An operating-system or transport step failed.'}
    reason = next((message for kind, message in reasons.items() if isinstance(error, kind)),
        'Unexpected internal exception; inspect the retained operation stage. No retry authorised.')
    if isinstance(error, (ShopifyError, BackgroundYield)):
        reason = 'Shopify request or verification rejected the execution step.' if isinstance(error, ShopifyError) else 'Worker ownership, capacity or bounded execution deferred this step.'
    if isinstance(error, KeyError) and error.args and isinstance(error.args[0], str) and error.args[0] in {
            'config_digest', 'digest', 'inventory_id', 'variant_id', 'shipping_package_id', 'native', 'plan'}:
        reason += ' Key: ' + error.args[0]
    return {'operation_id': operation_id, 'category': redact_diagnostic(type(error).__name__, limit=80),
        'stage': redact_diagnostic(stage or 'unknown', limit=100), 'reason': reason}


def apply_readback(row, data, remote, adapter=None, *, allow_attribute_review=False):
    from app.shopify_package_assignments import guard as package_guard
    package_guard(data)
    if data["plan"].get("managed"):
        return apply_simple_readback(row, data, remote, adapter, allow_attribute_review=allow_attribute_review)
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
    data["observed_status"] = remote.get("status")
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
        local = db.session.get(Product, row.product_id) if row.product_id else None
        try:
            current = local_plan(local) if local else None
        except ShopifyError:
            return None  # fix source validation before explicitly adopting media
        if current and current["images"] == data["plan"]["images"]:
            return serializer().dumps({"identity": row.id, "remote": data["recovery_remote_digest"],
                "id": row.shopify_product_id, "local": current["digest"]})
    return None


def simple_identity(row, data, remote):
    if (not isinstance(remote, dict) or remote.get("id") != row.shopify_product_id
            or remote.get("status") not in {"DRAFT","ACTIVE","ARCHIVED"} or not isinstance(remote.get("metafield"), dict)
            or remote["metafield"].get("value") != data["plan"]["nonce"]):
        raise ShopifyError("Recovery required: retained product ID, draft status or attempt correlation is not trusted. No create/update is allowed.")
    _gid(remote["id"], "Product")
    connection = remote.get("simpleVariants")
    if (not isinstance(connection, dict) or connection.get("pageInfo", {}).get("hasNextPage") is not False
            or len(connection.get("nodes", [])) != 1 or not isinstance(connection["nodes"][0], dict)):
        raise ShopifyError("Recovery required: retained product no longer has one complete Simple variant.")
    variant = connection["nodes"][0]
    _gid(variant.get("id"), "ProductVariant")
    if data.get("variant_id") and variant["id"] != data["variant_id"]:
        raise ShopifyError("Recovery required: retained Simple variant identity changed.")
    inventory = variant.get("inventoryItem")
    if not isinstance(inventory, dict):
        raise ShopifyError("Recovery required: inventory-item identity evidence is incomplete.")
    _gid(inventory.get("id"), "InventoryItem")
    if data.get("inventory_item_id") and inventory["id"] != data["inventory_item_id"]:
        raise ShopifyError("Recovery required: inventory-item identity changed.")
    media = remote.get("media")
    if (not isinstance(media, dict) or media.get("pageInfo", {}).get("hasNextPage") is not False
            or not isinstance(media.get("nodes"), list) or len(media["nodes"]) > MAX_IMAGES):
        raise ShopifyError("Recovery required: remote media evidence is incomplete.")
    ids = []
    for node in media["nodes"]:
        if not isinstance(node, dict) or node.get("mediaContentType") != "IMAGE":
            raise ShopifyError("Unsupported remote gallery content; review Shopify Admin.")
        if node.get("image") is not None and not isinstance(node.get("image"), dict):
            raise ShopifyError("Remote image evidence is incomplete.")
        ids.append(_gid(node.get("id"), "MediaImage"))
    if len(ids) != len(set(ids)):
        raise ShopifyError("Remote media identity is ambiguous.")
    return variant


def media_review(plan, data, remote):
    retained = data.get("media", [])
    if not isinstance(retained, list) or any(not isinstance(m, dict) for m in retained):
        raise ShopifyError("Retained media mapping is invalid; administrator recovery review is required.")
    by_source = {m.get("source"): m for m in retained}
    if len(by_source) != len(retained) or len(retained) != len(plan["images"]):
        raise ShopifyError("Unsupported media addition/removal. Existing media will not be deleted or re-uploaded.")
    desired, alt, rows = [], [], []
    remote_nodes = {m["id"]: m for m in remote["media"]["nodes"]}
    for image in plan["images"]:
        mapped = by_source.get(image["source"], {})
        if mapped.get("sha256") != image["sha256"] or mapped.get("id") not in remote_nodes:
            raise ShopifyError("Unsupported media replacement or missing mapping. Retain original files; no automatic upload/delete is allowed.")
        node = remote_nodes[mapped["id"]]
        if node.get("status") != "READY" or not (node.get("image") or {}).get("url", "").startswith("https://"):
            raise ShopifyError("Media processing is not ready; reconcile before reviewing updates.")
        desired.append(node["id"])
        changed = node.get("alt") != image["alt"]
        rows.append({"field": f"media.alt.{node['id']}", "before": node.get("alt"), "after": image["alt"], "changed": changed})
        if changed:
            alt.append({"id": node["id"], "alt": image["alt"]})
    if len(set(desired)) != len(desired) or set(desired) != set(remote_nodes):
        raise ShopifyError("Unsupported remote media addition/removal or ambiguous mapping. No media will be deleted.")
    before = [m["id"] for m in remote["media"]["nodes"]]
    rows.append({"field": "media.order", "before": before, "after": desired, "changed": before != desired})
    return {"ids": desired, "alt": alt, "reorder": before != desired}, rows


def update_preview(product, row, plan, config, shop, adapter):
    data = evidence(row)
    if row.source_identity != plan["source_identity"] or row.sku != plan["sku"]:
        raise ShopifyError("Stable local source/SKU does not match its retained Shopify identity.")
    remote = adapter.read_simple(config, row.shopify_product_id, plan["managed"]["namespace"])
    if remote is None:
        from app.shopify_connection import ShopifyProductAbsent
        raise ShopifyProductAbsent("Exact Shopify product read returned absent. Refresh its retained identity before reviewing recreation.")
    variant = simple_identity(row, data, remote)
    if not data.get("variant_id") and variant.get("sku") != row.sku:
        raise ShopifyError("Legacy Simple variant cannot be adopted with a different SKU; recovery review is required.")
    actions, media_rows = media_review(plan, data, remote)
    if actions["alt"] and "write_files" not in shop["scopes"]:
        plan["blockers"].append("Missing write_files for reviewed alt changes; no files will be written.")
    rows = fields.field_rows(plan["managed"], remote) + media_rows
    plan["fields"] = rows
    from app.shopify_attributes import attach
    if plan.get("scope") == "inventory":
        plan["attributes"] = []
        from app.taxonomy_assignments import product_document, resolve
        plan["attribute_diagnostics"] = [{"name":a["name"],"action":"Explicitly deferred; not compared or synchronised"} for a in resolve(product_document(product))["attributes"] if not a["variation"]]
    else:
        attach(product, plan, config, shop, adapter, row.shopify_product_id)
        from app.shopify_customisations import attach as attach_customisations
        attach_customisations(product, plan, config, shop, adapter, row.shopify_product_id)
    if "tags" in plan["managed"]["product"]:
        removed = sorted(set(remote.get("tags", [])) - set(plan["managed"]["product"]["tags"]))
        if removed:
            plan["warnings"].append("Explicit tag replacement removes: " + ", ".join(removed))
    classification = "update" if any(r["changed"] for r in rows) or any(r["action"] in {"Join","Create & Join"} for r in plan["collections"]) else "no_change"
    if "handle" in plan["managed"]["product"] and plan["managed"]["product"]["handle"] != remote["handle"]:
        collision = adapter.find_draft(config, plan["managed"]["product"]["handle"])
        if collision["pageInfo"].get("hasNextPage") or any(n.get("id") != row.shopify_product_id for n in collision["nodes"]):
            raise ShopifyError("The reviewed handle belongs to another Shopify product; no update is allowed.")
        plan["warnings"].append("Explicit URL-handle change; no redirect is created by this workflow.")
    plan.update(classification=classification, fields=rows, media_actions=actions,
        identity_id=row.id, remote_id=row.shopify_product_id, variant_id=variant["id"],
        inventory_item_id=variant["inventoryItem"]["id"], store_id=shop["shop_id"],
        store_key=row.store_key, domain=config.domain, config_digest=config.fingerprint(),
        handle=plan["managed"]["product"].get("handle", remote["handle"]), nonce=data["plan"]["nonce"],
        remote_digest=digest(remote), identity_digest=digest(data))
    plan["actual_status"] = remote.get("status", "Unknown")
    plan["remote_updated_at"] = remote.get("updatedAt")
    if actions["alt"]:
        plan["warnings"].append("Alt text is a Shopify file property; other uses of that same file may also reflect the change.")
    from app.shopify_customisations import reserve as customisation_reserve
    encode({**data,"version":1,"plan":plan,'storefront_configuration':customisation_reserve(plan),"previous_verified":{"plan":data["plan"]},"reserve":"x"*12000})
    token = None
    if classification == "update":
        token = serializer().dumps({"mode": "update", **{key: plan[key] for key in (
            "product_id", "digest", "identity_id", "remote_id", "store_id", "domain", "config_digest",
            "remote_digest", "identity_digest", "taxonomy_review_digest")}})
    return plan, token


def update_inputs(plan):
    changed = {r["field"] for r in plan["fields"] if r["changed"]}
    product = {"id": plan["remote_id"]}
    for key, value in plan["managed"]["product"].items():
        if key in changed:
            product[key] = value
        elif key == "seo":
            seo = {k: v for k, v in value.items() if f"seo.{k}" in changed}
            if seo:
                product[key] = seo
        elif key == "metafields":
            items = [i for i in value if "dimensions." + i["key"].removeprefix("product_") in changed]
            if items:
                product[key] = items
    variant = {"id": plan["variant_id"]}
    for key in ("price", "compareAtPrice", "inventoryPolicy"):
        if key in changed:
            variant[key] = plan["managed"]["variant"][key]
    inventory = {k: v for k, v in plan["managed"]["variant"]["inventoryItem"].items()
        if k in changed or (k == "measurement" and "weight" in changed)
        or (k == "sku" and "variant.sku" in changed)}
    if inventory:
        variant["inventoryItem"] = inventory
    if "handle" in product:
        product["redirectNewHandle"] = False
    return product, variant


def confirm_update(reviewed, adapter=None):
    adapter = adapter or ShopifyAdapter()
    product = db.session.get(Product, reviewed["product_id"])
    row = db.session.get(ShopifyProductIdentity, reviewed["identity_id"])
    if not product or not row or row.state not in {"verified","attribute_review_required","scoped_verified"} or row.verification_state not in {"verified","base_verified","inventory_verified"}:
        raise ShopifyError("Identity is not verified or another attempt requires recovery. Generate a fresh Preview.")
    # Acquire before revalidation; reviewed update cannot overlap another write.
    lease = acquire_catalogue_operation("shopify_draft_publish", {"product_id": product.id, "sku": product.sku, "mode": "update"})
    attempted = False
    try:
        plan, _ = preview(product, adapter)
        if plan["blockers"]:
            raise ShopifyError("Publication or collection mapping blockers remain. Review again; no write attempted.")
        for key in ("digest", "identity_id", "remote_id", "store_id", "domain", "config_digest", "remote_digest", "identity_digest", "taxonomy_review_digest"):
            if plan.get(key) != reviewed.get(key):
                raise ShopifyError("Source/configuration/remote state changed since review. Generate a fresh Preview; no update was attempted.")
        if plan["classification"] != "update":
            raise ShopifyError("The reviewed update is no longer current. Generate a fresh Preview.")
        data = evidence(row)
        data.pop("reorder_job", None)
        data["completed_steps"] = []
        data["previous_verified"] = {"operation_id": row.operation_id, "reviewed_digest": row.reviewed_digest,
            "verified_at": row.verified_at.isoformat() if row.verified_at else None, "plan": data["plan"],
            "publication_review_operation_id":data.get('publication_review_operation_id')}
        data.update(plan=plan, stage="update_pending", update_attempted=False, publication_review_operation_id=lease.id,
            variant_id=plan["variant_id"], inventory_item_id=plan["inventory_item_id"])
        row.operation_id, row.reviewed_digest, row.verification_state = lease.id, plan["digest"], "unverified"
        save(row, data, "pending")
        from app.shopify_comparison import invalidate
        invalidate(row.store_id, row.source_identity)
        config = configuration()
        product_input, variant_input = update_inputs(plan)
        steps = []
        if len(product_input) > 1:
            steps.append(("product_update", lambda: adapter.update_simple_product(config, product_input), "productUpdate"))
        if len(variant_input) > 1:
            steps.append(("variant_update", lambda: adapter.update_simple_variant(config, row.shopify_product_id, variant_input), "productVariantsBulkUpdate"))
        if plan["media_actions"]["alt"]:
            steps.append(("media_alt", lambda: adapter.update_media_alt(config, plan["media_actions"]["alt"]), "fileUpdate"))
        if plan["media_actions"]["reorder"]:
            steps.append(("media_order", lambda: adapter.reorder_media(config, row.shopify_product_id, plan["media_actions"]["ids"]), "productReorderMedia"))
        for stage, call, payload in steps:
            if local_plan(product)["digest"] != plan["digest"] or configuration().fingerprint() != plan["config_digest"]:
                raise ShopifyError("Local source/configuration changed during execution. Review retained evidence before any further write.")
            data.update(stage=stage, update_attempted=True)
            save(row, data, "updating")  # durable BEFORE every mutation
            attempted = True
            result = call()
            if payload == "productReorderMedia" and (result.get(payload) or {}).get("mediaUserErrors"):
                raise ShopifyError("Shopify rejected gallery order. Reconcile the retained update.")
            result = payload_result(result, payload)
            if payload == "productReorderMedia":
                job = (result.get("job") or {}).get("id")
                data["reorder_job"] = _gid(job, "Job")
            data.setdefault("completed_steps", []).append(stage)
            save(row, data)
        from app.shopify_taxonomy import join_reviewed
        if any(i["action"] in {"Join","Create & Join"} for i in plan["collections"]):
            attempted = True
        join_reviewed(row, data, config, adapter)
        from app.shopify_attributes import apply
        if any(a["changed"] for a in plan.get("attributes", [])):
            attempted = True
        apply(row, data, config, adapter)
        from app.shopify_customisations import apply as apply_customisations
        if (plan.get('customisations') or {}).get('changed'):
            attempted = True
        apply_customisations(row, data, config, adapter)
        from app.shopify_dimension_metadata import apply as apply_dimensions
        if any(s['changed'] for s in (plan.get('dimension_metadata') or {}).get('definitions', [])):
            attempted = True
        apply_dimensions(row, data, config, adapter)
        from app.shopify_publication import apply_reviewed
        if (plan.get('publication') or {}).get('pending') and (plan.get('publication') or {}).get('read_complete') and not (plan.get('publication') or {}).get('blockers'):
            attempted=True
        apply_reviewed(row,data,config,adapter)
        apply_simple_readback(row, data, adapter.read_simple(config, row.shopify_product_id, plan["managed"]["namespace"]), adapter)
        from app.shopify_native_dimensions import after_publish
        finalisation_stage = 'native_dimensions'
        native_state = after_publish(row, plan, adapter, lease.id)
        from app.shopify_package_assignments import apply as apply_packages
        finalisation_stage = 'package_preflight'
        package_state=apply_packages(row,adapter,lease.id)
        finalisation_stage = 'operation_finalisation'
        from app.shopify_publication import complete as publication_complete
        finish_catalogue_operation(lease.id, status="succeeded" if row.state == "verified" and native_state in {"disabled", "verified", "unconfigured"} and package_state in {'none', 'accepted'} and publication_complete(evidence(row)) else "partial",
            products_attempted=1, products_succeeded=int(row.state == "verified"),
            recovery_state="none" if row.state == "verified" and native_state != "uncertain" else "review_required",
            operation_summary={"identity_id": row.id, "mode": "update", "stage": evidence(row)['stage'], "completed_steps": evidence(row).get("completed_steps", []), "native_dimensions": native_state,'package_assignment':package_state})
        return row
    except Exception as exc:
        db.session.rollback()
        from app.shopify_capacity import BackgroundYield
        if isinstance(exc, BackgroundYield) and not attempted and row.operation_id != lease.id:
            # Preflight has not reserved this identity or attempted a mutation.
            # Let the existing task owner defer the same signed operation.
            raise
        message = str(exc) if isinstance(exc, ShopifyError) else "Update failed with uncertain evidence. Reconcile before another write."
        from app.shopify_package_assignments import attempted as package_attempted
        attempted=attempted or package_attempted(evidence(row),lease.id)
        diagnostic = failure_diagnostic(exc, locals().get('finalisation_stage') or evidence(row).get('stage'), lease.id)
        if attempted:
            row = db.session.get(ShopifyProductIdentity, row.id)
            data = evidence(row)
            data["error"] = message
            data['failure_diagnostic'] = diagnostic
            data['stage'] = 'recovery_required'
            save(row, data, "uncertain")
        finish_catalogue_operation(lease.id, status="partial" if attempted else "failed", error=message,
            recovery_state="review_required" if attempted else "none", products_attempted=1, products_failed=1,
            operation_summary={"identity_id": row.id, "mode": "update", "write_attempted": attempted,
                "stage": diagnostic['stage'], "diagnostic": diagnostic})
        if not attempted:
            # A local failure after reservation but before any write is safe to
            # restore; the failed operation remains visible in history.
            data = evidence(row)
            if data.get("stage") == "update_pending" and not data.get("update_attempted"):
                previous = data.pop("previous_verified")
                data.update(plan=previous["plan"], stage="verified", last_failed_review_operation_id=lease.id)
                data['publication_review_operation_id']=previous.get('publication_review_operation_id')
                row.operation_id = previous["operation_id"]
                row.reviewed_digest = previous["reviewed_digest"]
                row.verification_state = "verified"
                save(row, data, "verified")
            raise ShopifyError(message) from None
        return row


def apply_simple_readback(row, data, remote, adapter=None, *, allow_attribute_review=False):
    from app.shopify_package_assignments import guard as package_guard
    package_guard(data)
    plan = data["plan"]
    from app.shopify_publication import expected_status
    if remote.get('status') != expected_status(data):
        raise ShopifyError('Product status changed during metadata operation; reconcile the reviewed state before another write.')
    variant = simple_identity(row, data, remote)
    managed = {"product": {**plan["managed"]["product"], "handle": plan["handle"]},
        "variant": plan["managed"]["variant"]}
    data["checked_at"] = now().isoformat()
    data["recovery_remote_digest"] = digest(remote)
    data["observed_status"] = remote.get("status")
    data["observed_media"] = [{"id": m["id"], "url": (m.get("image") or {}).get("url"),
        "status": m.get("status"), "alt": m.get("alt")} for m in remote["media"]["nodes"]]
    # Retain trusted identity/media observations before optional-field parsing.
    # A later verifier error must not obscure successful creation or its gallery.
    data["verification"] = {"identity": "verified", "fields": "unverified",
        "memberships": "unverified", "media": "unverified", "checked_at": data["checked_at"],
        "operation_id": row.operation_id}
    data["stage"] = "managed_field_verification"
    save(row, data)
    rows = fields.field_rows(managed, remote)
    data["verification"]["fields"] = "verified" if not any(r["changed"] for r in rows) else "differences"
    save(row, data)
    if any(r["changed"] for r in rows):
        raise ShopifyError("Managed Simple fields differ from the reviewed plan. Reconcile; do not repeat the update blindly.")
    from app.shopify_taxonomy import verify_memberships
    data["stage"] = "collection_verification"
    save(row, data)
    verify_memberships(plan, configuration(), adapter or ShopifyAdapter(), row.shopify_product_id)
    from app.shopify_attributes import verify
    remaining=False
    try:
        verify(plan, configuration(), adapter or ShopifyAdapter(), row.shopify_product_id)
    except ShopifyError:
        if not allow_attribute_review:
            raise
        from app.shopify_attributes import remaining_review
        remaining=remaining_review(plan,configuration(),adapter or ShopifyAdapter(),row.shopify_product_id)
    data["verification"]["attributes"]="remaining_review" if remaining else "verified"
    from app.shopify_customisations import verify as verify_customisations
    verify_customisations(row, data, configuration(), adapter or ShopifyAdapter())
    from app.shopify_dimension_metadata import verify as verify_dimensions
    verify_dimensions(row, data, configuration(), adapter or ShopifyAdapter())
    data["verification"]["memberships"] = "verified"
    data["stage"] = "media_verification"
    save(row, data)
    expected = plan.get("media_actions", {}).get("ids") or [m.get("id") for m in data.get("created_media", [])]
    if not expected:
        raise ShopifyError("Creation media receipts are missing. Inspect the retained draft and explicitly approve its mapping; no duplicate create is allowed.")
    observed = [m["id"] for m in remote["media"]["nodes"]]
    if set(expected) != set(observed) or len(expected) != len(plan["images"]):
        raise ShopifyError("Media identities differ from the reviewed gallery; no media will be deleted or re-uploaded.")
    by_id = {m["id"]: m for m in remote["media"]["nodes"]}
    if any(by_id[mid].get("alt") != image["alt"] for mid, image in zip(expected, plan["images"])):
        raise ShopifyError("Media alt text differs from the reviewed plan.")
    if any(m.get("status") == "FAILED" for m in remote["media"]["nodes"]):
        raise ShopifyError("Media processing failed; inspect the retained draft.")
    ready = all(m.get("status") == "READY" and (m.get("image") or {}).get("url", "").startswith("https://") for m in remote["media"]["nodes"])
    if observed != expected and not data.get("reorder_job"):
        raise ShopifyError("Gallery order differs from the reviewed plan.")
    ready = ready and observed == expected
    data["verification"]["media"] = "verified" if ready else "processing"
    data["variant_id"], data["inventory_item_id"] = variant["id"], variant["inventoryItem"]["id"]
    mapping = {m["id"]: m for m in data["observed_media"]}
    for image, media_id in zip(plan["images"], expected):
        receipt = next((m for m in data["media"] if m.get("source") == image["source"]), None)
        if receipt is None:
            raise ShopifyError("Local media receipt is missing; automatic adoption is refused.")
        receipt.update(mapping[media_id])
    row.verification_state = "base_verified" if ready and remaining else "verified" if ready else "processing"
    if ready and not remaining:
        row.verified_at = now()
    data["stage"] = "attribute_review_required" if ready and remaining else "verified" if ready else "media_reordering" if observed != expected else "media_processing"
    data.pop("error", None)
    save(row, data, "attribute_review_required" if ready and remaining else "verified" if ready else "processing")
    if ready and not remaining:
        from app.shopify_comparison import record
        product = db.session.get(Product, row.product_id)
        if product:
            result = {**plan, "classification": "no_change", "fields": rows,
                "collections": [{**i, "action": "Already assigned"} for i in plan.get("collections", [])],
                "remote_id": row.shopify_product_id, "remote_digest": digest(remote), "actual_status": remote["status"],
                "remote_updated_at": remote.get("updatedAt")}
            from app.shopify_publication import comparison_after_write
            record(product, comparison_after_write(product, result, row, adapter or ShopifyAdapter()), row, post_write=True)


def reconcile(row, candidate_id=None, adapter=None, approval=None):
    operation = db.session.get(CatalogueOperation, row.operation_id) if row.operation_id else None
    if operation and operation.status == "running":
        raise ShopifyError("This publishing operation is still active. Wait for completion; reconciliation cannot overlap writes.")
    lease = acquire_catalogue_operation("shopify_draft_publish", {"mode": "read_only_reconciliation", "identity_id": row.id})
    try:
        result = _reconcile(row, candidate_id, adapter, approval, allow_attribute_review=True)
        finish_catalogue_operation(lease.id, status="succeeded" if result.state == "verified" else "partial",
            recovery_state="none" if result.state == "verified" else "review_required",
            operation_summary={"identity_id": row.id, "mode": "read_only_reconciliation", "state": result.state, "remote_writes": 0})
        from app.shopify_notifications import settle
        settle(result)
        return result
    except Exception:
        finish_catalogue_operation(lease.id, status="failed", error="Shopify read-only reconciliation requires review; original publishing history is retained.")
        raise


def finalize_processing(row, adapter=None, *, force=True):
    from app.shopify_variable_readback import pending,complete
    if pending(row):
        return complete(row,adapter or ShopifyAdapter(),allow_writes=False)
    if row.state != "processing":
        return row  # Never supersede genuinely uncertain attempts automatically.
    operation = db.session.get(CatalogueOperation, row.operation_id) if row.operation_id else None
    if operation and operation.status == "running":
        raise ShopifyError("Publishing is still active; wait for its retained operation to finish.")
    data = evidence(row)
    if not force:
        from app.shopify_comparison import recent
        from datetime import timedelta
        if recent(data.get("processing_checked_at"), timedelta(seconds=30)):
            return row
    data["processing_checked_at"] = now().isoformat()
    save(row, data)
    result = _reconcile(row, adapter=adapter)
    from app.shopify_notifications import settle
    settle(result)
    return result


def _reconcile(row, candidate_id=None, adapter=None, approval=None, *, allow_attribute_review=False):
    from app.shopify_package_assignments import guard as package_guard
    package_guard(evidence(row))
    operation = db.session.get(CatalogueOperation, row.operation_id) if row.operation_id else None
    if operation and operation.status == "running":
        raise ShopifyError("This publishing operation is still active. Wait for completion; reconciliation cannot overlap writes.")
    config = configuration()
    adapter = adapter or ShopifyAdapter()
    shop = adapter.test_connection(config)
    if shop["shop_id"] != row.store_id:
        raise ShopifyError("Configured Shopify store does not match this identity. Restore its store configuration.")
    data = evidence(row)
    if data.get('customisation_update', {}).get('state') in {'pending', 'uncertain'}:
        from app.shopify_customisations import reconcile as reconcile_customisations
        return reconcile_customisations(row, data, config, adapter)
    if data.get('publication_update',{}).get('state') in {'pending','uncertain'}:
        from app.shopify_publication import verify
        verify(row,data,adapter)
        if not data['publication_update'].get('unified'):
            return row
    if data.get('sale_transition',{}).get('state') in {'pending','uncertain'}:
        from app.shopify_sales import reconcile_identity
        return reconcile_identity(row,adapter)
    if data.get("inventory_update",{}).get("state") in {"pending","uncertain"}:
        from app.shopify_inventory_review import verify
        return verify(row,data,adapter)
    if data.get("kind") == "variable":
        from app.shopify_variables import reconcile as variable_reconcile
        return variable_reconcile(row, adapter, candidate_id=candidate_id, approval=approval, allow_attribute_review=allow_attribute_review)
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
    remote = (adapter.read_simple(config, _gid(product_id, "Product"), data["plan"]["managed"]["namespace"])
        if data["plan"].get("managed") else adapter.read_draft(config, _gid(product_id, "Product")))
    if (not isinstance(remote, dict) or not isinstance(remote.get("metafield"), dict)
            or remote["metafield"].get("value") != data["plan"]["nonce"]
            or (not data.get("update_attempted") and remote.get("handle") != data["plan"]["handle"])):
        raise ShopifyError("Candidate draft does not match this operation's correlation. Identity remains protected.")
    row.shopify_product_id = _gid(product_id, "Product")
    save(row, data)
    if data.get("taxonomy_attempt"):
        from app.shopify_taxonomy_batch import verify_product_attempt
        try:
            verify_product_attempt(row, data, remote, config, adapter)
        except ShopifyError as exc:
            data["error"] = str(exc)
            save(row, data, "uncertain")
            raise
        return row
    if approval:
        try:
            reviewed = serializer().loads(approval, max_age=900)
        except BadSignature:
            raise ShopifyError("Recovery review expired. Reconcile again before approving observed media.") from None
        local = db.session.get(Product, row.product_id) if row.product_id else None
        current = local_plan(local) if local else None
        if (not current or reviewed != {"identity": row.id, "remote": digest(remote), "id": row.shopify_product_id, "local": current["digest"]}
                or current["images"] != data["plan"]["images"]):
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
        apply_readback(row, data, remote, adapter, allow_attribute_review=allow_attribute_review)
    except ShopifyError as exc:
        data["error"] = str(exc)
        save(row, data, "uncertain")
        return row
    return row
