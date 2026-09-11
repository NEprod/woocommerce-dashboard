"""Read-only, bounded proposals from explicit, complete existing child projections."""
import hashlib
import json
from itertools import product as combinations
from math import prod

from app.taxonomy_assignments import product_document, variation_drivers, normal, match
from app import taxonomy_workspace as registry


def proposal(product):
    document = product_document(product)
    if product.product_type != "variable" or product.catalogue_status != "active" or "variation_attributes" not in document:
        raise registry.RegistryEditError("Only explicit Variable products can prepare variation taxonomy.")
    try:
        drivers = variation_drivers(document)
    except ValueError as error:
        raise registry.RegistryEditError(str(error)) from None
    if not drivers or not 1 <= len(product.variations) <= 1000:
        raise registry.RegistryEditError("A non-empty, bounded existing child projection is required.")
    if len({normal(n) for n in drivers}) != len(drivers):
        raise registry.RegistryEditError("Explicit driver names are ambiguous after normalization.")
    values = {}
    for name in drivers:
        authored = document["attributes"][name]
        if not isinstance(authored, list) or not authored or any(not isinstance(v, str) or not v.strip() for v in authored):
            raise registry.RegistryEditError("Drivers require non-empty authored option lists.")
        if len({normal(v) for v in authored}) != len(authored):
            raise registry.RegistryEditError("Driver options contain ambiguous normalized values.")
        values[name] = authored
    if prod(map(len, values.values())) > 1000:
        raise registry.RegistryEditError("Driver combinations exceed the 1,000-child bound.")
    observed, evidence, skus = [], [], set()
    projected_values = {name: {} for name in drivers}
    for child in sorted(product.variations, key=lambda row: row.id):
        selected = {a.name: a.value for a in child.attributes}
        if (child.catalogue_status != "active" or not child.sku or child.sku in skus or len(selected) != len(child.attributes)
                or set(selected) != set(drivers)):
            raise registry.RegistryEditError("Child projection is ambiguous or does not match the explicit drivers.")
        skus.add(child.sku)
        for name in drivers:
            value = selected[name]
            if not isinstance(value, str) or not value.strip():
                raise registry.RegistryEditError("Child selections must be non-empty strings.")
            prior = projected_values[name].setdefault(normal(value), value)
            if prior != value:
                raise registry.RegistryEditError("Child term spellings conflict after normalization. Review the projection.")
        observed.append(tuple(normal(selected[n]) for n in drivers))
        evidence.append([child.id, child.sku, selected])
    expected = set(combinations(*([normal(v) for v in values[n]] for n in drivers)))
    if len(observed) != len(expected) or set(observed) != expected:
        raise registry.RegistryEditError("Existing children do not contain the complete authored driver combinations. No taxonomy was proposed.")
    data, revision, missing = registry.current_source()
    if missing:
        raise registry.RegistryEditError("Provide a valid registry before preparing variation taxonomy.")
    groups, additions, used_attributes = [], 0, set()

    def resolve(value, rows):
        return match(value, [{**r, "value": r["name"]} for r in rows])

    for name in drivers:
        found = resolve(name, data["attributes"])
        if found and found["state"] != "active":
            raise registry.RegistryEditError("An existing driver definition is not active. Review it in Taxonomy first.")
        attribute = next((r for r in data["attributes"] if found and r["key"] == found["key"]), None)
        new = attribute is None
        if new:
            slug = registry._slug(name)
            attribute = {"key": "attr-" + slug, "name": name, "slug": slug, "state": "active",
                         "navigation": False, "visible_default": False, "terms": []}
            data["attributes"].append(attribute)
            additions += 1
        if attribute["key"] in used_attributes:
            raise registry.RegistryEditError("Multiple drivers resolve to the same registry attribute. Review aliases.")
        used_attributes.add(attribute["key"])
        group = {"name": name, "new": new, "terms": []}
        # Every authored option was proven present in the child projection above.
        for authored_value in values[name]:
            value = projected_values[name][normal(authored_value)]
            term = resolve(value, attribute["terms"])
            if term and term["state"] != "active":
                raise registry.RegistryEditError("An existing driver term is not active. Review it in Taxonomy first.")
            group["terms"].append({"name": value, "new": term is None})
            if term is None:
                slug = registry._slug(value)
                attribute["terms"].append({"key": "term-" + slug, "name": value, "slug": slug,
                                           "state": "active", "order": len(attribute["terms"])})
                additions += 1
        groups.append(group)
    raw = registry.validate(data)  # Includes key/slug/alias collision and scope checks.
    fingerprint = hashlib.sha256(json.dumps([product.id, product.sku, document, evidence, revision,
                                            raw.decode()], sort_keys=True).encode()).hexdigest()
    return {"data": data, "revision": revision, "digest": fingerprint,
            "groups": groups, "additions": additions, "children": len(observed)}
