"""Local authored assignments; registry matching never writes either source."""
import unicodedata


PUBLISH_BLOCK = ("Product uses the M5.3 explicit attribute contract. Woo publisher "
                 "integration for informational vs variation-driving attributes is not yet enabled.")


def normal(value):
    return " ".join(unicodedata.normalize("NFC", str(value)).casefold().split())


def variation_drivers(data):
    attributes = data.get("attributes", {})
    if "variation_attributes" not in data:
        return list(attributes) if isinstance(attributes, dict) else []  # legacy
    if not isinstance(attributes, dict):
        raise ValueError("Explicit variation attributes require an attributes object.")
    names = data["variation_attributes"]
    if not isinstance(names, list) or any(not isinstance(n, str) or not n.strip() for n in names):
        raise ValueError("variation_attributes must be an array of assigned attribute names.")
    if len(set(names)) != len(names) or any(n not in attributes for n in names):
        raise ValueError("Variation drivers must be unique exact names of assigned attributes.")
    if len(names) > 5:
        raise ValueError("The current scanner row contract supports at most five explicit variation drivers. Informational attributes are not limited to five.")
    if data.get("collection_type") == "Simple" and names:
        raise ValueError("Simple products may have informational attributes but no variation drivers.")
    if names and data.get("collection_type") == "Single Variable" and any(n not in names for n in data.get("image_attributes", [])):
        raise ValueError("Single Variable image attributes must remain variation drivers; image-folder semantics are unchanged.")
    return names


def options():
    from app.taxonomy_registry import load_configured_registry
    from app.taxonomy_workspace import category_paths
    result = load_configured_registry()
    data = result.snapshot.data if result.available else {}
    paths = category_paths(data) if data else {}
    return {"status": result.status, "digest": result.snapshot.digest if result.available else None,
            "categories": [{"key": r["key"], "value": paths[r["key"]], "name": r["name"], "aliases": list(r.get("aliases", [])), "state": r["state"]} for r in data.get("categories", [])],
            "attributes": [{"key": r["key"], "value": r["name"], "aliases": list(r.get("aliases", [])), "state": r["state"],
                            "terms": [{"key": t["key"], "value": t["name"], "aliases": list(t.get("aliases", [])), "state": t["state"]} for t in r["terms"]]} for r in data.get("attributes", [])]}


def match(value, rows):
    matches = [r for r in rows if normal(value) in {normal(r["value"]), normal(r.get("name", r["value"])), *(normal(a) for a in r.get("aliases", []))}]
    return matches[0] if len(matches) == 1 else None


def resolve(data, registry=None):
    registry = options() if registry is None else registry
    try:
        drivers, error = variation_drivers(data), None
    except ValueError as invalid:
        drivers, error = [], str(invalid)
    category_values = data.get("categories", [])
    categories = [{"value": value, "definition": match(value, registry["categories"])} for value in category_values] if isinstance(category_values, list) else []
    attributes = []
    authored_attributes = data.get("attributes", {})
    for name, values in (authored_attributes.items() if isinstance(authored_attributes, dict) else []):
        definition = match(name, registry["attributes"])
        attributes.append({"name": name, "definition": definition,
                           "variation": name in drivers and data.get("collection_type") != "Simple",
                           "terms": [{"value": value, "definition": match(value, definition["terms"]) if definition else None} for value in (values if isinstance(values, list) else [values])]})
    return {"categories": categories, "attributes": attributes, "explicit": "variation_attributes" in data,
            "drivers": drivers, "error": error, "registry_status": registry["status"]}


def product_document(product, *, catalogue_root=...):
    from app.metadata_workspace import metadata_source, resolved_metadata, _catalogue_root
    root = _catalogue_root() if catalogue_root is ... else catalogue_root
    shared = metadata_source(product, "shared", catalogue_root=root)
    override = metadata_source(product, "override", catalogue_root=root)
    return resolved_metadata(product, shared["data"], override["data"] if override["exists"] else {})


def guard_products(products):
    """Read current authored source, not a potentially stale SQLite projection."""
    from app.metadata_workspace import _catalogue_root
    root = _catalogue_root()  # one local configuration lookup for the whole scope
    return [p for p in products if "variation_attributes" in product_document(p, catalogue_root=root)]
