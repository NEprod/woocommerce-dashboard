"""Four-field additive organisation; other overrides and variation contracts are unchanged."""
from copy import deepcopy


def registry_options():
    from flask import has_app_context
    from app.taxonomy_assignments import options
    if has_app_context():
        return options()
    from config import Config
    from app.taxonomy_registry import load_registry
    result = load_registry(Config.TAXONOMY_ROOT)
    return options(snapshot=result.snapshot) if result.available else {'status': result.status}


def _definition(key, rows, kind):
    row = next((row for row in rows if row['key'] == key), None)
    if row is None:
        raise ValueError(f'Unresolved {kind} registry key: {key}. Review the taxonomy registry; no replacement was guessed.')
    return row


def _identity(value, rows):
    from app.taxonomy_assignments import match
    row = next((r for r in rows if r['key'] == value), None) or match(value, rows)
    return ('key', row['key']) if row else ('legacy', value)


def _deduplicate(values, rows):
    result, seen = [], set()
    for value in values:
        identity = _identity(value, rows)
        if identity not in seen:
            result.append(value)
            seen.add(identity)
    return result


def merge_organisation_fields(resolved, shared, override, *, registry=None):
    kinds=('categories','storefront_collections','tags')
    if not any(key in layer for layer in (shared,override) for key in (*kinds,'attributes')):
        return resolved
    registry = registry if registry is not None else registry_options()
    result = deepcopy(resolved)
    from app.taxonomy_assignments import variation_drivers
    simple=result.get('collection_type')=='Simple'
    parent_attributes=shared.get('attributes',{})
    own_attributes=override.get('attributes',{})
    definitions=registry.get('attributes',[])
    parent_drivers=[] if simple else variation_drivers(shared)
    parent_driver_ids={_identity(name,definitions) for name in parent_drivers}
    replaces_drivers=any(_identity(name,definitions) in parent_driver_ids for name in own_attributes)
    if simple:
        drivers=[]
    elif 'variation_attributes' in result:
        drivers=list(result['variation_attributes'])
    elif replaces_drivers:
        drivers=variation_drivers(result)  # retain the pre-existing implicit variation contract
    else:
        drivers=parent_drivers
    attributes={}
    for index,source in enumerate((parent_attributes,own_attributes)):
        for name,values in source.items():
            identity=_identity(name,definitions)
            if index==0 and replaces_drivers and identity in parent_driver_ids:
                continue
            prior=next((key for key in attributes if _identity(key,definitions)==identity),name)
            definition=next((row for row in definitions if ('key',row['key'])==identity),None)
            terms=definition['terms'] if definition else []
            attributes[prior]=_deduplicate([*attributes.get(prior,[]),*values],terms)
    if 'attributes' in shared or 'attributes' in override:
        result['attributes']=attributes
    # Adding information must never create another implicit variant axis.
    if own_attributes and 'variation_attributes' not in result:
        result['variation_attributes'] = list(drivers)
    for kind in kinds:
        if kind in shared or kind in override:
            result[kind]=_deduplicate([*shared.get(kind,[]),*override.get(kind,[])],registry.get(kind,[]))
    # This is effective data only. Authored source documents remain sparse.
    return result


def product_type(data, *, registry=None):
    key = data.get('shopify_product_type_key')
    if key is None:
        return data.get('shopify_product_type') or None  # compatible historic label
    registry = registry if registry is not None else registry_options()
    if registry.get('status') not in {'ready', 'read_only'}:
        raise ValueError('Product Type needs a readable taxonomy registry; no remote value will be guessed or cleared.')
    row = _definition(key, registry.get('categories', []), 'Product Type')
    if row.get('parent') is not None:
        raise ValueError('Product Type must reference a top-level parent category, not a subcategory.')
    return row['name']
