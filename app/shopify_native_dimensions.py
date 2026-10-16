"""Opt-in 2027-01 packed measurements, separate from metadata/publication evidence."""
import json
import os
from decimal import Decimal, InvalidOperation

from itsdangerous import BadSignature
from app import db
from app.models import Product, ShopifyProductComparison
from app import shopify_drafts as drafts
from app.shopify_connection import ShopifyAdapter, ShopifyError, ShopifyNativeWriteRejected, _gid
from app.catalogue_fields import shipping_details
from app.taxonomy_assignments import product_document
from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation

VERSION = "2027-01"
TOLERANCE_MM = Decimal("0.000001")
FACTORS = {"MILLIMETERS": Decimal(1), "CENTIMETERS": Decimal(10), "METERS": Decimal(1000),
    "INCHES": Decimal("25.4"), "FEET": Decimal("304.8"), "YARDS": Decimal("914.4")}


def flag(name):
    value = os.environ.get(name, "false").strip().lower()
    if value not in {"true", "false", "1", "0", ""}:
        raise ShopifyError(f"Invalid {name}: use true or false.")
    return value in {"true", "1"}


def desired(product):
    enabled = flag("SHOPIFY_NATIVE_DIMENSIONS_ENABLED")
    use_product = flag("SHOPIFY_PACKED_DIMENSIONS_USE_PRODUCT_DIMENSIONS")
    result = {"version": VERSION, "enabled": enabled, "use_product_dimensions": use_product,
        "desired": None, "local_mm": None, "reason": "Native packed dimensions disabled; product metafields are not shipping dimensions."}
    if not enabled:
        return result
    document = product_document(product)
    packaging = document.get('packaging') or {}
    if 'packed_dimensions_cm' in packaging:
        result.update(explicit_packed(packaging))
        return result
    if not use_product:
        result["reason"] = "Packed measurement policy unresolved. Explicitly approve reuse of authored product dimensions only when they accurately describe the packed item."
        return result
    try:
        local = shipping_details(document, product)["dimensions_mm"]
        values = {key: Decimal(str(local[key])) for key in ("length", "width", "height")}
        if any(isinstance(local[k], bool) or not v.is_finite() or v <= 0 or v > Decimal("1000000000") for k, v in values.items()):
            raise ValueError()
        # Float input at the boundary, Decimal source/comparison. 1e-7 cm
        # rounding is at most half the 1e-6 mm comparison tolerance.
        result.update(local_mm={k: format(v, "f") for k, v in values.items()}, reason="",
            desired={**{k: float((v / 10).quantize(Decimal("0.0000001"))) for k, v in values.items()}, "unit": "CENTIMETERS"})
        if any(result["desired"][k] <= 0 for k in values):
            raise ValueError()
    except (KeyError, ValueError, TypeError, InvalidOperation, OSError, RuntimeError):
        result.update(desired=None, local_mm=None)
        result["reason"] = "Complete positive authored length, width and height are required; no packaging values are invented."
    return result


def explicit_packed(packaging):
    """Authored measured shipping size wins; never silently fall back to artwork."""
    from app.packaging_contract import validate, AXES, number
    try:
        validate(packaging)
        if not packaging.get('measured'): raise ValueError('Confirm packed measurements before Shopify review.')
        values={k:number(packaging.get('packed_dimensions_cm',{}).get(k),k) for k in AXES}
        return {'desired':{**{k:float(v) for k,v in values.items()},'unit':'CENTIMETERS'},
            'local_mm':{k:str(v*10) for k,v in values.items()},'reason':'','measurement_source':'authored_packed_size'}
    except ValueError as exc:
        return {'desired':None,'local_mm':None,'reason':str(exc),'measurement_source':'authored_packed_size'}


def millimetres(value):
    if value is None:
        return None
    try:
        if not isinstance(value, dict):
            raise ValueError()
        factor = FACTORS[value["unit"]]
        values = {key: Decimal(str(value[key])) * factor for key in ("length", "width", "height")}
        if any(isinstance(value[k], bool) or not v.is_finite() or v < 0 or v > Decimal("1000000000") for k, v in values.items()):
            raise ValueError()
        return values
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise ShopifyError("Native packed-dimension evidence is incomplete or invalid; no equality or write retry is assumed.") from None


def equal(left, right):
    a, b = millimetres(left), millimetres(right)
    return a is not None and b is not None and all(abs(a[k] - b[k]) <= TOLERANCE_MM for k in a)


def capability(adapter, config, shop):
    if not {"read_inventory", "read_products"}.intersection(shop["scopes"]):
        raise ShopifyError("Missing read_inventory or read_products for native measurement reads.")
    schema = adapter.packed_capability(config)
    if not isinstance(schema, dict):
        raise ShopifyError("2027-01 packedDimensions capability response is incomplete. No native write allowed.")
    requirements = {"measurement": ("fields", {"packedDimensions"}),
        "measurementInput": ("inputFields", {"packedDimensions"}),
        "dimensionsInput": ("inputFields", {"length", "width", "height", "unit"}),
        "units": ("enumValues", set(FACTORS))}
    for name, (field, expected) in requirements.items():
        rows = (schema.get(name) or {}).get(field)
        if (not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get("name"), str) for r in rows)
                or not expected.issubset({r["name"] for r in rows})):
            raise ShopifyError("2027-01 packedDimensions capability is unavailable. Normal 2026-10 product operations remain supported.")
    return "write_inventory" in shop["scopes"]


def identity_read(row, adapter, config):
    data = drafts.evidence(row)
    remote = adapter.read_packed(config, row.shopify_product_id)
    try:
        variants = remote["variants"]
        nodes = variants["nodes"]
        if (remote["id"] != row.shopify_product_id or remote.get("status") not in {"DRAFT", "ACTIVE", "ARCHIVED"} or remote["metafield"]["value"] != data["plan"]["nonce"]
                or variants["pageInfo"]["hasNextPage"] is not False or len(nodes) != 1
                or nodes[0]["sku"] != row.sku or nodes[0]["id"] != data["variant_id"]
                or nodes[0]["inventoryItem"]["id"] != data["inventory_item_id"]):
            raise ValueError()
        item = nodes[0]["inventoryItem"]
        _gid(nodes[0]["id"], "ProductVariant")
        _gid(item["id"], "InventoryItem")
        packed = item["measurement"]["packedDimensions"]
        if packed is None:
            raise ValueError()  # 2027-01 returns a non-null BoxDimensions object
        millimetres(packed)
        return item["id"], packed
    except (KeyError, TypeError, IndexError, ValueError):
        raise ShopifyError("Native inventory-item/product/variant identity or measurement evidence is untrusted. Inspect the retained identity; no write allowed.") from None


def review(product, row=None, adapter=None, *, config=None, shop=None):
    if product.product_type != "simple" or product.catalogue_status != "active":
        raise ShopifyError("Native dimension publishing supports active Simple parents only.")
    plan = desired(product)
    plan.update(product_id=product.id, source=product.source_relpath, sku=product.sku,
        classification="blocked", inventory_id=None, before=None, checked_at=None)
    if not plan["enabled"]:
        return plan, None
    adapter = adapter or ShopifyAdapter()
    config = config or drafts.configuration()
    shop = shop or adapter.test_connection(config)
    plan.update(store_id=shop["shop_id"], config=config.fingerprint())
    if row is None:
        row = drafts.matching_identity(shop["shop_id"], product)
    if row:
        plan.update(identity_id=row.id, remote_id=row.shopify_product_id)
        if row.source_identity != product.source_relpath or row.sku != product.sku:
            raise ShopifyError("Stable local source/SKU does not match the retained Shopify identity. No native write allowed.")
        attempt = drafts.evidence(row).get("native_dimensions", {})
        if attempt.get("state") in {"pending", "uncertain"}:
            plan["reason"] = "Native dimension outcome requires read-only reconciliation; no blind retry."
            return plan, None
        if row.state != "verified" or row.verification_state != "verified" or row.store_id != shop["shop_id"]:
            plan["reason"] = "Complete product/media verification first. Native dimensions do not authorize product recovery or another Create."
            return plan, None
    try:
        can_write = capability(adapter, config, shop)
        if row:
            plan["inventory_id"], plan["before"] = identity_read(row, adapter, config)
        plan["checked_at"] = drafts.now().isoformat()
        plan["capable"] = True
        if plan["desired"]:
            plan["classification"] = "no_change" if row and equal(plan["before"], plan["desired"]) else "update" if row else "create_stage"
            plan["reason"] = "" if can_write or plan["classification"] == "no_change" else "Missing write_inventory; metadata can succeed, native stage remains outstanding."
        plan["can_write"] = can_write
    except ShopifyError as exc:
        plan["reason"] = str(exc)
    return plan, drafts.serializer().dumps({"native_review": plan}) if row and plan["classification"] == "update" and not plan["reason"] else None


def persist(row, value):
    data = drafts.evidence(row)
    data["native_dimensions"] = value
    drafts.save(row, data)
    # Rebind only identity evidence for our isolated known stage. Never advance
    # metadata/full comparison times merely because dimensions were checked.
    for summary in ShopifyProductComparison.query.filter_by(store_id=row.store_id, source_identity=row.source_identity).all():
        known = json.loads(summary.summary_json)
        known["identity"] = drafts.digest(row.media_evidence_json)
        known["native"] = value
        if value.get("state") == "verified":
            for key in ("changes", "metadata_changes"):
                known[key] = [r for r in known.get(key, []) if r["field"] != "native.packed_dimensions"]
            for key in ("display_changes", "metadata_display"):
                known[key] = [r for r in known.get(key, []) if not r.startswith("Native packed dimensions:")]
            known["change_count"] = len(known.get("changes", []))
            if known.get("classification") in {"update", "no_change"}:
                known["classification"] = "update" if known["change_count"] else "no_change"
        from app.shopify_comparison import store_summary
        store_summary(summary, known)


def apply_reviewed(row, plan, adapter, *, operation_id=None):
    if not plan.get("enabled"):
        return "disabled"
    if plan.get("reason") or not plan.get("desired") or row.state != "verified":
        persist(row, {"version": 1, "state": "blocked", "plan": plan,
            "message": plan.get("reason") or "Product/media processing must finish before a separately reviewed native stage."})
        return "blocked"
    config = drafts.configuration()
    product = db.session.get(Product, plan["product_id"])
    if not product or product.source_relpath != row.source_identity or product.sku != row.sku:
        raise ShopifyError("Native local source/SKU identity changed. No native write attempted.")
    if desired(product)["desired"] != plan["desired"] or config.fingerprint() != plan["config"]:
        raise ShopifyError("Native source/configuration changed. Rebuild the reviewed operation; no native write attempted.")
    inventory_id, before = identity_read(row, adapter, config)
    if plan.get("inventory_id") and (inventory_id != plan["inventory_id"] or before != plan["before"]):
        raise ShopifyError("Native inventory identity/measurements changed since review; no native write attempted.")
    if equal(before, plan["desired"]):
        persist(row, {"version": 1, "state": "verified", "plan": plan, "inventory_id": inventory_id,
            "observed": before, "checked_at": drafts.now().isoformat(), "operation_id": operation_id})
        return "verified"
    # Scope/capability are read again at execution; no write-as-permission-test.
    shop = adapter.test_connection(config)
    if shop["shop_id"] != row.store_id or not capability(adapter, config, shop):
        raise ShopifyError("Native store/capability/write_inventory permission changed; no native write attempted.")
    attempt = {"version": 1, "state": "pending", "plan": plan, "inventory_id": inventory_id,
        "operation_id": operation_id, "attempted_at": drafts.now().isoformat()}
    persist(row, attempt)  # durable BEFORE the single, never-retried mutation
    try:
        response = adapter.write_packed(config, inventory_id, plan["desired"])
        payload = response.get("inventoryItemUpdate")
        if isinstance(payload, dict) and payload.get("userErrors") and payload.get("inventoryItem") is None:
            attempt.update(state="blocked", message="Shopify rejected native dimensions. Inspect values/permissions, then build a new review; no automatic retry.")
            persist(row, attempt)
            return "blocked"
        if not isinstance(payload, dict) or payload.get("userErrors") or (payload.get("inventoryItem") or {}).get("id") != inventory_id:
            raise ShopifyError("Native mutation receipt is incomplete; reconcile before retry.")
        _, after = identity_read(row, adapter, config)
        if not equal(after, plan["desired"]):
            raise ShopifyError("Native packed dimensions are not independently verified; reconcile before retry.")
        attempt.update(state="verified", observed=after, checked_at=drafts.now().isoformat())
        persist(row, attempt)
        return "verified"
    except ShopifyNativeWriteRejected as exc:
        attempt.update(state="blocked", message=str(exc))
        persist(row, attempt)
        return "blocked"
    except Exception as exc:
        attempt.update(state="uncertain", message=str(exc) if isinstance(exc, ShopifyError) else "Native write outcome is uncertain; reconcile before any retry.")
        persist(row, attempt)
        return "uncertain"


def after_publish(row, plan, adapter, operation_id):
    reviewed = plan.get("native")
    if not reviewed or not reviewed.get("enabled"):
        return "disabled"
    try:
        return apply_reviewed(row, reviewed, adapter, operation_id=operation_id)
    except ShopifyError as exc:
        # apply_reviewed handles ALL exceptions once its write is reserved.
        # These failures occurred during read/preflight: no native write ambiguity.
        persist(row, {"version": 1, "state": "blocked", "plan": reviewed, "message": str(exc)})
        return "blocked"


def attach_review(product, plan, row, config, shop, adapter):
    native, _ = review(product, row, adapter, config=config, shop=shop)
    plan["native"] = native
    plan["taxonomy_review_digest"] = drafts.digest({"taxonomy": plan["taxonomy_review_digest"],
        "native": {k:v for k,v in native.items() if k != "checked_at"}})
    if native["enabled"]:
        plan["warnings"].append("2027-01 release candidate: native packed dimensions are independently reviewed. Product measurements are reused only under the explicitly configured packed-measurement policy.")
        if native["reason"]:
            plan["warnings"].append(native["reason"])
        elif row:
            changed = native["classification"] == "update"
            plan["fields"].append({"field": "native.packed_dimensions", "before": native["before"], "after": native["desired"], "changed": changed})
            if changed:
                plan["classification"] = "update"
    return plan


def check(product, adapter=None):
    """Persist an independently matching read without advancing metadata proof."""
    lease = acquire_catalogue_operation("shopify_native_dimensions", {"product_id": product.id, "mode": "read_only_preview"})
    try:
        plan, token = review(product, adapter=adapter)
        if plan["classification"] == "no_change":
            row = drafts.matching_identity(plan["store_id"], product)
            persist(row, {"version": 1, "state": "verified", "plan": plan,
                "inventory_id": plan["inventory_id"], "observed": plan["before"],
                "checked_at": plan["checked_at"], "operation_id": lease.id, "mode": "read_only_comparison"})
        finish_catalogue_operation(lease.id, status="succeeded", operation_summary={"native_dimensions": plan["classification"], "remote_writes": 0})
        return plan, token
    except Exception:
        finish_catalogue_operation(lease.id, status="failed", error="Native read-only preview needs attention; no remote write attempted.")
        raise


def confirm(token, acknowledged, adapter=None, *, expected_product_id=None):
    if not acknowledged:
        raise ShopifyError("Explicit approval that the authored dimensions are accurate packed measurements is required.")
    try:
        plan = drafts.serializer().loads(token, max_age=900)["native_review"]
        product = db.session.get(Product, plan["product_id"])
        if not product or expected_product_id and product.id != expected_product_id:
            raise ValueError()
    except (BadSignature, KeyError, ValueError, TypeError):
        raise ShopifyError("Native dimension review expired or is invalid. Build a fresh review.") from None
    adapter = adapter or ShopifyAdapter()
    lease = acquire_catalogue_operation("shopify_native_dimensions", {"product_id": product.id, "mode": "reviewed_update"})
    try:
        current, _ = review(product, adapter=adapter)
        if {k:v for k,v in current.items() if k != "checked_at"} != {k:v for k,v in plan.items() if k != "checked_at"}:
            raise ShopifyError("Native dimensions, source, identity, capability or permissions changed after review. No write attempted.")
        row = drafts.matching_identity(plan["store_id"], product)
        state = apply_reviewed(row, plan, adapter, operation_id=lease.id)
        finish_catalogue_operation(lease.id, status="succeeded" if state == "verified" else "partial",
            recovery_state="review_required" if state == "uncertain" else "none",
            operation_summary={"identity_id": row.id, "native_dimensions": state})
        return row
    except Exception:
        finish_catalogue_operation(lease.id, status="failed", error="Native stage requires review; product/media identities and historical evidence are retained.")
        raise


def reconcile(row, adapter=None):
    adapter = adapter or ShopifyAdapter()
    lease = acquire_catalogue_operation("shopify_native_dimensions", {"identity_id": row.id, "mode": "read_only_reconciliation"})
    try:
        value = drafts.evidence(row).get("native_dimensions")
        if not value or value.get("state") not in {"pending", "uncertain"}:
            raise ShopifyError("No uncertain native stage exists. Build a fresh native Review instead.")
        config = drafts.configuration()
        shop = adapter.test_connection(config)
        if shop["shop_id"] != row.store_id:
            raise ShopifyError("Native store identity mismatch; restore configuration.")
        capability(adapter, config, shop)
        item_id, observed = identity_read(row, adapter, config)
        if item_id != value["inventory_id"] or not equal(observed, value["plan"]["desired"]):
            raise ShopifyError("Native outcome remains unresolved. No automatic retry; inspect the retained inventory item in Shopify Admin.")
        value.update(state="verified", observed=observed, checked_at=drafts.now().isoformat(), reconciled_by=lease.id)
        value.pop("message", None)
        persist(row, value)
        finish_catalogue_operation(lease.id, status="succeeded", operation_summary={"native_dimensions": "verified", "remote_writes": 0})
        return row
    except Exception:
        finish_catalogue_operation(lease.id, status="failed", error="Read-only native reconciliation requires review; no mutation retried.")
        raise


def local_status(product, row=None, compared=None):
    wanted = desired(product)
    retained = drafts.evidence(row).get("native_dimensions", {}) if row else {}
    value = retained if retained.get("state") in {"pending", "uncertain"} else compared or retained
    plan = value.get("plan", value)
    state = value.get("state") or ("verified" if plan.get("classification") == "no_change" else "update")
    from app.shopify_comparison import recent, MAX_AGE
    fresh = recent(value.get("checked_at"), MAX_AGE)
    if state in {"pending", "uncertain"}:
        return {"state": "uncertain", "message": "Native packed-dimension outcome requires explicit read-only reconciliation. Metadata verification remains separate."}
    if not wanted["enabled"]:
        return {"state": "disabled", "message": wanted["reason"]}
    if not wanted["desired"]:
        return {"state": "blocked", "message": wanted["reason"]}
    if value.get("state") == "blocked" or plan.get("reason"):
        return {"state": "blocked", "message": value.get("message") or plan["reason"]}
    if state == "verified" and fresh and plan.get("desired") == wanted["desired"]:
        return {"state": "verified", "message": "Native packed dimensions independently checked " + value["checked_at"]}
    if state == "verified" and not fresh and plan.get("desired") == wanted["desired"]:
        return {"state": "stale", "message": "Last packed-dimension check matched; its ten-minute freshness expired. A bounded read is due, not a confirmed mismatch."}
    if not value or state == "verified" and not fresh:
        return {"state": "unavailable", "message": "Native packed-dimension evidence is absent/stale; a targeted read is required independently of Product.updatedAt. Metadata evidence remains separate."}
    return {"state": "update", "message": value.get("message") or plan.get("reason") or "Native packed dimensions need a fresh reviewed comparison (independent of Product.updatedAt)."}
