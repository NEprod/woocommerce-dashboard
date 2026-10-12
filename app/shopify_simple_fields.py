"""Bounded Simple field mapping/comparison. No remote writes or source mutation."""
import json
import os
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from app.shopify_connection import ShopifyError


def number(value, label, *, money=False, positive=False):
    try:
        if isinstance(value, bool):
            raise ValueError()
        result = Decimal(str(value))
        if (not result.is_finite() or result < 0 or result > Decimal("1000000000") or (positive and not result > 0)
                or (money and result != result.quantize(Decimal("0.01")))):
            raise ValueError()
        return format(result, ".2f") if money else format(result, "f")
    except (InvalidOperation, ValueError):
        raise ShopifyError(f"Invalid {label}; review its authored value before publishing.") from None


def namespace():
    value = os.environ.get("SHOPIFY_PRODUCT_DIMENSIONS_NAMESPACE", "tlc_dashboard")
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", value):
        raise ShopifyError("Invalid product-dimension metafield namespace configuration.")
    return value


def contract(snapshot, base):
    authored = snapshot.data["authored"]
    product = {"title": base["title"], "descriptionHtml": base["description"]}
    variant = {"price": base["price"], "compareAtPrice": None, "inventoryItem": {"sku": base["sku"]}}
    warnings = ["DRAFT only. No inventory quantities, tracking, locations or fulfilment configuration will change."]
    sale = authored.get("sale_price", snapshot.data["sale_price"])
    if sale not in (None, ""):
        if authored.get("sale_start_date") or authored.get("sale_end_date"):
            raise ShopifyError("Scheduled sale dates are unsupported; review an explicit unscheduled sale before publishing.")
        sale = number(sale, "sale price", money=True)
        if Decimal(sale) >= Decimal(base["price"]):
            raise ShopifyError("Sale price must be below the regular price; misleading compare-at pricing is refused.")
        variant.update(price=sale, compareAtPrice=base["price"])
    shipping = snapshot.data["shipping"]
    if shipping["weight_g"] not in (None, ""):
        grams = number(shipping["weight_g"], "weight")
        if Decimal(str(float(grams))) != Decimal(grams):
            raise ShopifyError("Weight precision cannot be represented safely; review grams before publishing.")
        variant["inventoryItem"]["measurement"] = {"weight": {"value": float(grams), "unit": "GRAMS"}}
    for local, remote in (("requires_shipping", "requiresShipping"), ("country_of_origin", "countryCodeOfOrigin")):
        if shipping[local] is not None:
            variant["inventoryItem"][remote] = shipping[local]
        else:
            warnings.append(f"{local} is unspecified; remote value is not managed.")
    if shipping["origin_source"] == "deployment_default":
        warnings.append("Country of origin uses the reviewed deployment default; confirm manufacturing origin is accurate.")
    for local, remote, maximum in (("vendor", "vendor", 255), ("handle", "handle", 255)):
        if authored.get(local) not in (None, ""):
            value = authored[local]
            if not isinstance(value, str) or len(value) > maximum or not value.strip():
                raise ShopifyError(f"Invalid authored {local}.")
            if local == "handle" and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
                raise ShopifyError("Handle must be an explicit lowercase URL slug.")
            product[remote] = value
    if "tags" in authored or snapshot.data["tags"]:
        tags = list(authored.get("tags", snapshot.data["tags"]))
        if len(tags) > 250 or any(not isinstance(t, str) or not t.strip() or len(t) > 255 for t in tags):
            raise ShopifyError("Tags must be bounded nonempty strings.")
        product["tags"] = sorted(set(tags))
        warnings.append("Reviewed tags replace the remote tag list, including remote-only tags.")
    seo = {}
    for key, local, maximum in (("title", "meta_title", 70), ("description", "meta_description", 320)):
        if local in authored:
            value = authored[local]
            if not isinstance(value, str) or len(value) > maximum:
                raise ShopifyError(f"Invalid {local}; supported maximum is {maximum} characters.")
            seo[key] = value
    if seo:
        product["seo"] = seo
    dimensions = []
    if not isinstance(shipping["dimensions_mm"], Mapping):
        raise ShopifyError("Product dimensions must be a length/width/height object.")
    for key, value in shipping["dimensions_mm"].items():
        if value not in (None, "", 0, "0"):
            value = number(value, f"product {key}", positive=True)
            if Decimal(str(float(value))) != Decimal(value):
                raise ShopifyError("Dimension precision cannot be represented safely; review millimetres before publishing.")
            dimensions.append({"namespace": namespace(), "key": f"product_{key}", "type": "dimension",
                "value": json.dumps({"value": float(value), "unit": "mm"}, sort_keys=True)})
    if dimensions:
        product["metafields"] = dimensions
        warnings.append("Dimensions are individual product measurements, NOT parcels or Shopify package presets.")
    return {"product": product, "variant": variant, "namespace": namespace()}, warnings


def weight_grams(weight):
    try:
        factors = {"GRAMS": Decimal(1), "KILOGRAMS": Decimal(1000),
            "OUNCES": Decimal("28.349523125"), "POUNDS": Decimal("453.59237")}
        result = Decimal(str(weight["value"])) * factors[weight["unit"]]
        if not result.is_finite() or result < 0:
            raise ValueError()
        return result
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise ShopifyError("Remote weight evidence is incomplete.") from None


def field_rows(managed, remote):
    """Compare only explicitly managed fields; incomplete evidence never equals."""
    rows = []
    def add(name, before, after, equal=None):
        rows.append({"field": name, "before": before, "after": after,
            "changed": before != after if equal is None else not equal})
    product = managed["product"]
    for key in ("title", "descriptionHtml", "vendor", "handle", "tags"):
        if key in product:
            if key not in remote:
                raise ShopifyError(f"Remote {key} evidence is incomplete.")
            if key == "tags":
                if not isinstance(remote[key], list) or any(not isinstance(t, str) for t in remote[key]):
                    raise ShopifyError("Remote tag evidence is incomplete.")
            elif not isinstance(remote[key], str):
                raise ShopifyError(f"Remote {key} evidence is incomplete.")
            before = sorted(remote[key]) if key == "tags" else remote[key]
            add(key, before, product[key])
    for key, value in product.get("seo", {}).items():
        if not isinstance(remote.get("seo"), dict) or key not in remote["seo"]:
            raise ShopifyError("Remote SEO evidence is incomplete.")
        before = remote["seo"][key]
        if before is not None and not isinstance(before, str):
            raise ShopifyError("Remote SEO evidence is invalid.")
        add(f"seo.{key}", before, value, (before or "") == value)
    for item in product.get("metafields", []):
        key = item["key"].removeprefix("product_")
        if key not in remote:
            raise ShopifyError("Remote dimension evidence is incomplete.")
        before = remote.get(key)
        equal = False
        if before is not None:
            if not isinstance(before, dict) or before.get("type") != "dimension":
                raise ShopifyError("Existing product-dimension metafield has an incompatible type. Review it; no overwrite is allowed.")
            try:
                actual = json.loads(before["value"])
                factor = {"mm": 1, "cm": 10, "m": 1000, "in": Decimal("25.4"), "ft": Decimal("304.8"), "yd": Decimal("914.4")}[actual["unit"]]
                equal = Decimal(str(actual["value"])) * factor == Decimal(str(json.loads(item["value"])["value"]))
            except (KeyError, TypeError, ValueError, InvalidOperation):
                raise ShopifyError("Remote dimension evidence is invalid.") from None
        add(f"dimensions.{key}", before, item, equal)
    variants = remote.get("simpleVariants")
    if (not isinstance(variants, dict) or variants.get("pageInfo", {}).get("hasNextPage") is not False
            or len(variants.get("nodes", [])) != 1):
        raise ShopifyError("Exactly one independently read Simple variant is required.")
    variant = variants["nodes"][0]
    if "sku" not in variant:
        raise ShopifyError("Remote variant SKU evidence is incomplete.")
    add("variant.sku", variant["sku"], managed["variant"]["inventoryItem"]["sku"])
    for key in ("price", "compareAtPrice"):
        if key not in variant:
            raise ShopifyError("Remote price evidence is incomplete.")
        value = managed["variant"][key]
        before = variant[key]
        equal = before is None and value is None
        if before is not None and value is not None:
            equal = Decimal(number(before, "remote price")) == Decimal(value)
        add(key, before, value, equal)
    inventory = variant.get("inventoryItem")
    if not isinstance(inventory, dict):
        raise ShopifyError("Remote inventory-item evidence is incomplete.")
    for key, value in managed["variant"]["inventoryItem"].items():
        if key == "measurement":
            before = (inventory.get("measurement") or {}).get("weight")
            add("weight", before, value["weight"], abs(weight_grams(before) - weight_grams(value["weight"])) <= Decimal("0.000001"))
        else:
            if key not in inventory:
                raise ShopifyError(f"Remote inventory {key} evidence is incomplete.")
            add(key, inventory[key], value)
    return rows
