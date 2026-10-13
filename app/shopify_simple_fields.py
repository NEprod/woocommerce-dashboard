"""Bounded Simple field mapping/comparison. No remote writes or source mutation."""
import json
import os
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from app.shopify_connection import ShopifyError


# Shopify canonicalizes measurement unit names in metafield values. Read old
# abbreviated receipts too, without rewriting their signed/reviewed provenance.
DIMENSION_FACTORS = {"mm": Decimal(1), "cm": Decimal(10), "m": Decimal(1000),
    "in": Decimal("25.4"), "ft": Decimal("304.8"), "yd": Decimal("914.4")}
DIMENSION_ALIASES = {"millimeters": "mm", "centimeters": "cm", "meters": "m",
    "inches": "in", "feet": "ft", "yards": "yd"}
DIMENSION_TOLERANCE_MM = Decimal("0.000001")


def dimension_mm(value):
    try:
        item = json.loads(value)
        if not isinstance(item, dict) or isinstance(item["value"], bool):
            raise ValueError()
        unit = item["unit"].lower()
        amount = Decimal(str(item["value"]))
        result = amount * DIMENSION_FACTORS[DIMENSION_ALIASES.get(unit, unit)]
        if not amount.is_finite() or result <= 0 or result > Decimal("1000000000"):
            raise ValueError()
        return result
    except (KeyError, TypeError, AttributeError, ValueError, InvalidOperation):
        raise ShopifyError("Remote dimension evidence is invalid. Inspect the retained product metafield type, value and unit; no automatic overwrite or retry.") from None


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
    warnings = ["DRAFT only. No inventory quantities, locations or fulfilment configuration will change."]
    tracking = authored.get("manage_stock", False)
    if type(tracking) is not bool or authored.get("stock_quantity") is not None and "manage_stock" not in authored:
        raise ShopifyError("Resolve the explicit finished-product tracking policy before publishing; quantities never imply an invented policy.")
    backorders = authored.get("backorders", "no")
    if backorders not in ("no", "yes", "notify", None):
        raise ShopifyError("Unsupported authored backorder policy.")
    variant["inventoryItem"]["tracked"] = tracking
    variant["inventoryPolicy"] = "CONTINUE" if not tracking or backorders in ("yes", "notify") else "DENY"
    warnings.append("Reviewed finished-product inventory: " + ("tracking enabled; existing quantities unchanged" if tracking else "untracked / continue selling; no artificial stock quantity"))
    if not base["description"]:
        warnings.append("Main product description is empty; confirming a reviewed description change may clear existing main copy. SEO description is separate and will not be substituted.")
    if "tags" not in authored:
        warnings.append("Tags are unspecified; existing remote tags are not managed.")
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
    if "tags" in authored:
        tags = list(authored["tags"])
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
    if authored.get("shopify_product_type"):
        value = authored["shopify_product_type"]
        if not isinstance(value, str) or len(value) > 255:
            raise ShopifyError("Shopify product type must be an explicit bounded label.")
        product["productType"] = value
    else:
        warnings.append("Shopify product type is unspecified; no type is inferred from folder/category names.")
    dimensions = []
    if not isinstance(shipping["dimensions_mm"], Mapping):
        raise ShopifyError("Product dimensions must be a length/width/height object.")
    for key, value in shipping["dimensions_mm"].items():
        if value not in (None, "", 0, "0"):
            value = number(value, f"product {key}", positive=True)
            if Decimal(str(float(value))) != Decimal(value):
                raise ShopifyError("Dimension precision cannot be represented safely; review millimetres before publishing.")
            dimensions.append({"namespace": namespace(), "key": f"product_{key}", "type": "dimension",
                "value": json.dumps({"value": float(value), "unit": "millimeters"}, sort_keys=True)})
    if dimensions:
        product["metafields"] = dimensions
        warnings.append("Dimensions use supported product dimension metafields, NOT native shipping/package measurements or Shopify parcel presets. Authored millimetres are preserved.")
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


def review_rows(plan):
    """Readable view only; canonical comparison and signed evidence are unchanged."""
    labels = {"title": "Title", "descriptionHtml": "Main description", "vendor": "Vendor",
        "handle": "URL handle", "tags": "Tags", "seo.title": "SEO title",
        "seo.description": "SEO description", "price": "Customer price",
        "compareAtPrice": "Compare-at price", "variant.sku": "SKU", "sku": "SKU",
        "requiresShipping": "Requires shipping", "countryCodeOfOrigin": "Country of origin",
        "weight": "Weight", "tracked": "Track finished-product inventory", "inventoryPolicy": "Out-of-stock policy", "native.packed_dimensions": "Native packed dimensions", "media.order": "Gallery order", "category": "Standard Shopify category", "productType": "Shopify product type"}
    def value(item):
        if item is None or item == "":
            return "Not set"
        if isinstance(item, bool):
            return "Yes" if item else "No"
        if isinstance(item, (list, tuple)):
            return ", ".join(value(v) for v in item) or "None"
        if isinstance(item, Mapping):
            if {"length","width","height","unit"}.issubset(item):
                unit={"CENTIMETERS":"cm","MILLIMETERS":"mm","METERS":"m","INCHES":"in","FEET":"ft","YARDS":"yd"}.get(item["unit"],str(item["unit"]))
                return f'{item["length"]} × {item["width"]} × {item["height"]} {unit}'
            if "weight" in item:
                return value(item["weight"])
            if "value" in item and "unit" in item:
                unit = {"GRAMS": "g", "KILOGRAMS": "kg", "POUNDS": "lb", "OUNCES": "oz"}.get(item["unit"], item["unit"].lower())
                return f'{item["value"]} {unit}'
            if item.get("type") == "dimension":
                try:
                    millimetres = dimension_mm(item["value"])
                    return f'{format(millimetres.normalize(), "f")} mm'
                except (ShopifyError, ValueError, TypeError, KeyError):
                    pass
            return "; ".join(f"{labels.get(k, k)}: {value(v)}" for k, v in item.items())
        return str(item)
    rows = []
    for row in plan["fields"]:
        expanded = [(row["field"], row["before"], row["after"])]
        # Create plans contain nested objects; show the same useful leaf fields as updates.
        if row["field"] in {"inventoryItem", "seo"} and isinstance(row["after"], Mapping):
            expanded = [(f"seo.{key}" if row["field"] == "seo" else "weight" if key == "measurement" else key,
                None, item) for key, item in row["after"].items()]
        elif row["field"] == "metafields":
            expanded = [("dimensions." + item["key"].removeprefix("product_"), None, item)
                for item in row["after"]]
        for key, before, after in expanded:
            label=labels.get(key, key.replace("dimensions.", "Product ").replace("media.alt.", "Image alt "))
            if key.startswith("variants."):
                _,sku,leaf=key.split(".",2)
                label=f'Variant {sku} · {labels.get(leaf, leaf.replace("dimensions.", "Product ").replace("create", "Create"))}'
            elif key.startswith("native.") and key != "native.packed_dimensions":
                label="Packed dimensions · " + key.removeprefix("native.")
            rows.append({"label": label,
                "before": "Not created" if plan["classification"] == "create" and after is not None else value(before),
                "after": value(after), "action": "Set on create" if plan["classification"] == "create" and after is not None
                    else "Change" if row["changed"] else "Already matching"})
    return rows


def field_rows(managed, remote):
    """Compare only explicitly managed fields; incomplete evidence never equals."""
    rows = []
    def add(name, before, after, equal=None):
        rows.append({"field": name, "before": before, "after": after,
            "changed": before != after if equal is None else not equal})
    product = managed["product"]
    for key in ("title", "descriptionHtml", "vendor", "handle", "tags", "productType", "category"):
        if key in product:
            if key not in remote:
                raise ShopifyError(f"Remote {key} evidence is incomplete.")
            if key == "tags":
                if not isinstance(remote[key], list) or any(not isinstance(t, str) for t in remote[key]):
                    raise ShopifyError("Remote tag evidence is incomplete.")
            elif key == "category":
                if remote[key] is not None and not isinstance(remote[key], dict):
                    raise ShopifyError("Remote standard category evidence is incomplete.")
            elif not isinstance(remote[key], str):
                raise ShopifyError(f"Remote {key} evidence is incomplete.")
            before = sorted(remote[key]) if key == "tags" else (remote[key] or {}).get("id") if key == "category" else remote[key]
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
            equal = abs(dimension_mm(before.get("value")) - dimension_mm(item["value"])) <= DIMENSION_TOLERANCE_MM
        add(f"dimensions.{key}", before, item, equal)
    variants = remote.get("simpleVariants")
    if (not isinstance(variants, dict) or variants.get("pageInfo", {}).get("hasNextPage") is not False
            or len(variants.get("nodes", [])) != 1):
        raise ShopifyError("Exactly one independently read Simple variant is required.")
    variant = variants["nodes"][0]
    if "inventoryPolicy" in managed["variant"]:
        if variant.get("inventoryPolicy") not in {"CONTINUE", "DENY"}:
            raise ShopifyError("Remote inventory policy evidence is incomplete.")
        add("inventoryPolicy", variant["inventoryPolicy"], managed["variant"]["inventoryPolicy"])
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
