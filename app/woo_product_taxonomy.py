"""Explicit product assignments consume registry identities, never create taxonomy."""
from itertools import product as combinations
from math import prod

from app.models import WooTaxonomyIdentity
from app.taxonomy_assignments import product_document, resolve, normal, options
from app.taxonomy_registry import load_configured_registry


def verify_identities(contracts, get):
    """Bounded GET-by-trusted-ID verification, never matching/discovery or repair."""
    from app.woo_taxonomy_sync import canonical, digest, SyncError
    rows = { (r["kind"], r["remote_scope"], r["woo_id"]): r
             for value in contracts if value for r in value["identities"] }
    if len(rows) > 200:
        raise ValueError("Verified taxonomy readback exceeds 200 definitions. Review a smaller product scope.")
    for (kind, scope, woo_id), row in rows.items():
        route = (f"products/attributes/{scope}/terms/{woo_id}" if kind == "terms" else
                 f"products/{'brands' if kind == 'storefront_collections' else kind}/{woo_id}")
        remote = get(route)
        try:
            valid = (isinstance(remote, dict) and remote.get("id") == woo_id and
                     digest(canonical(remote, kind)) == row["remote_digest"] and
                     (kind != "categories" or remote.get("parent") == row["expected_parent"]))
        except SyncError:
            valid = False
        if not valid:
            raise ValueError(f"Verified {kind} identity '{row['key']}' changed remotely. Review Taxonomy Sync; publication was refused.")


def contracts(products, store_key):
    from app.metadata_workspace import _catalogue_root
    root = _catalogue_root()
    return {p.id: contract(p, store_key, catalogue_root=root) for p in products}


def contract(product, store_key, *, catalogue_root=...):
    """Return a deterministic local contract; absent opt-in is strictly legacy."""
    document = product_document(product, catalogue_root=catalogue_root)
    if "variation_attributes" not in document:
        return None
    # Deferred import: definition sync already imports the Preview store helper.
    from app.woo_taxonomy_sync import local_digest, digest
    result = {"payload": {"categories": [], "brands": [], "attributes": []},
              "children": {}, "identities": [], "blockers": [], "source": document}
    blockers = result["blockers"]
    for field in ("categories", "storefront_collections"):
        values = document.get(field, [])
        if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
            blockers.append(f"{field} must contain readable assignment names/paths. Correct the authored metadata before Preview.")
    if blockers:
        result["digest"] = digest(result)
        return result
    registry = load_configured_registry()
    if not registry.available:
        blockers.append("Taxonomy registry is not ready. Restore the local registry and review Taxonomy Sync.")
        result["digest"] = digest(result)
        return result
    result["registry_digest"] = registry.snapshot.digest
    data = registry.snapshot.data
    resolved = resolve(document, registry=options(snapshot=registry.snapshot))
    if resolved["error"]:
        blockers.append(resolved["error"])
    identities = {(r.kind, r.scope_key, r.local_key): r for r in
                  WooTaxonomyIdentity.query.filter_by(store_key=store_key).all()}
    definitions = {kind: {r["key"]: r for r in data[kind]}
                   for kind in ("categories", "storefront_collections", "attributes")}
    checked = {}

    def require(kind, definition, scope="", remote_scope=0):
        if definition is None:
            return None
        key = (kind, scope, definition["key"])
        if key in checked:
            return checked[key]
        identity = identities.get(key)
        if (definition.get("state") != "active" or identity is None or
                identity.state != "verified" or type(identity.woo_id) is not int or identity.woo_id <= 0 or
                identity.remote_scope != remote_scope or not identity.verified_at or not identity.remote_digest or
                identity.local_digest != local_digest(definition, kind, scope)):
            blockers.append(f"{kind.replace('_', ' ').title()} '{definition['name']}' has no current verified identity in this store/scope. Review Taxonomy Sync.")
            checked[key] = None
            return None
        result["identities"].append({"kind": kind, "key": definition["key"], "scope": scope,
                                     "woo_id": identity.woo_id, "remote_scope": remote_scope,
                                     "local_digest": identity.local_digest, "remote_digest": identity.remote_digest})
        checked[key] = identity.woo_id
        return identity.woo_id

    def category(key):
        definition = definitions["categories"][key]
        parent = definition.get("parent")
        parent_id = category(parent) if parent else 0
        if parent and parent_id is None:
            return None
        woo_id = require("categories", definition)
        if woo_id:
            next(r for r in result["identities"] if r["kind"] == "categories" and r["key"] == key)["expected_parent"] = parent_id
        return woo_id

    for kind, field in (("categories", "categories"), ("storefront_collections", "brands")):
        for assignment in resolved[kind]:
            match = assignment["definition"]
            if match is None:
                blockers.append(f"Unknown {kind.replace('_', ' ')} assignment '{assignment['value']}'. Choose a registry definition and review Taxonomy Sync.")
                continue
            definition = definitions[kind][match["key"]]
            woo_id = category(match["key"]) if kind == "categories" else require(kind, definition)
            if woo_id and {"id": woo_id} not in result["payload"][field]:
                result["payload"][field].append({"id": woo_id})
    drivers = {}
    attribute_blockers_start = len(blockers)
    assigned_attribute_keys = set()
    for position, assignment in enumerate(resolved["attributes"]):
        match = assignment["definition"]
        if match is None:
            blockers.append(f"Unknown attribute '{assignment['name']}'. Register it and review Taxonomy Sync.")
            continue
        definition = definitions["attributes"][match["key"]]
        if definition["key"] in assigned_attribute_keys:
            blockers.append(f"Attribute '{definition['name']}' is assigned more than once through equivalent names/aliases. Resolve the duplicate assignment.")
            continue
        assigned_attribute_keys.add(definition["key"])
        woo_id = require("attributes", definition)
        terms = {t["key"]: t for t in definition["terms"]}
        values, aliases = [], {}
        for term in assignment["terms"]:
            matched = term["definition"]
            if matched is None:
                blockers.append(f"Unknown term '{term['value']}' under attribute '{assignment['name']}'. Review Taxonomy Sync in that attribute scope.")
                continue
            term_definition = terms[matched["key"]]
            if woo_id and require("terms", term_definition, definition["key"], woo_id):
                values.append(term_definition["name"])
                aliases[normal(term["value"])] = term_definition["name"]
        if woo_id:
            if len(set(values)) != len(values):
                blockers.append(f"Attribute '{definition['name']}' contains duplicate term assignments.")
            projected = next((a for a in product.attributes if a.name == assignment["name"]), None)
            result["payload"]["attributes"].append({"id": woo_id, "name": definition["name"],
                "options": values, "position": position, "visible": projected.visible is not False if projected else definition.get("visible_default", True),
                "variation": assignment["variation"]})
            if assignment["variation"]:
                drivers[assignment["name"]] = (woo_id, values, aliases)
    # An incomplete verified attribute map is not evidence of stale scanner rows.
    # Resolve the taxonomy prerequisite first; only then compare child selections.
    if resolved["error"] or len(blockers) > attribute_blockers_start:
        result["digest"] = digest(result)
        return result
    # Verify the existing scanner projection; never synthesize new children/SKUs here.
    count = prod(len(v[1]) for v in drivers.values()) if drivers else 0
    if count > 1000:
        blockers.append("Explicit variation combinations exceed the bounded 1,000-child validation limit.")
        result["digest"] = digest(result)
        return result
    expected = set(combinations(*(v[1] for v in drivers.values()))) if drivers else set()
    observed = []
    for child in product.variations:
        selected = {a.name: a.value for a in child.attributes}
        if len(selected) != len(child.attributes) or set(selected) != set(drivers):
            blockers.append(f"Variation '{child.sku}' does not match the explicit drivers. Run a local Update scan before Preview.")
            continue
        attributes, selection = [], []
        for name, (woo_id, values, aliases) in drivers.items():
            value = aliases.get(normal(selected[name]))
            if value not in values:
                blockers.append(f"Variation '{child.sku}' has an unrecognised selection for '{name}'. Run a local Update scan.")
                break
            attributes.append({"id": woo_id, "option": value})
            selection.append(value)
        else:
            observed.append(tuple(selection))
            result["children"][str(child.id)] = attributes
    if product.product_type == "variable" and (not drivers or set(observed) != expected or len(observed) != len(expected)):
        blockers.append("Variable product requires a complete, non-empty explicit driver/child projection. Review drivers and run a local Update scan; no children will be invented.")
    if product.product_type == "simple" and (drivers or product.variations):
        blockers.append("Simple product cannot publish variation drivers or child variations.")
    result["digest"] = digest(result)
    return result
