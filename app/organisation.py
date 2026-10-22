"""Four-field additive organisation; other overrides and variation contracts are unchanged."""
from copy import deepcopy

KEY = {'type': 'string', 'pattern': r'^[a-z][a-z0-9_-]{0,95}$'}
KEYS = {'type': 'array', 'maxItems': 500, 'uniqueItems': True, 'items': KEY}
ADDITIONS_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    **{kind: KEYS for kind in ('categories', 'storefront_collections', 'tags')},
    'attributes': {'type': 'object', 'maxProperties': 200, 'propertyNames': KEY,
                   'additionalProperties': KEYS},
}}


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


def _append(values, keys, rows, kind):
    result, seen = [], set()
    for value in values:
        identity = _identity(value, rows)
        if identity not in seen:
            result.append(value)
            seen.add(identity)
    for key in keys:
        row = _definition(key, rows, kind)
        identity = ('key', key)
        if identity not in seen:
            result.append(row['value'])
            seen.add(identity)
    return result


def apply_additions(resolved, shared, override, *, registry=None):
    kinds=('categories','storefront_collections','tags')
    if not any(key in layer for layer in (shared,override) for key in (*kinds,'attributes','taxonomy_additions')):
        return resolved
    layers = [layer.get('taxonomy_additions', {}) for layer in (shared, override)]
    from jsonschema import Draft202012Validator
    for layer in layers:
        if next(Draft202012Validator(ADDITIONS_SCHEMA).iter_errors(layer), None):
            raise ValueError('Invalid taxonomy_additions structure; use bounded stable registry-key lists.')
    registry = registry if registry is not None else registry_options()
    if any(layers) and registry.get('status') not in {'ready', 'read_only'}:
        raise ValueError('Taxonomy additions need a readable registry; restore the taxonomy mount before resolving them.')
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
            attributes[prior]=_append([*attributes.get(prior,[]),*values],[],terms,'term')
    if 'attributes' in shared or 'attributes' in override:
        result['attributes']=attributes
    # Adding information must never create another implicit variant axis.
    if (own_attributes or any(layer.get('attributes') for layer in layers)) and 'variation_attributes' not in result:
        result['variation_attributes'] = list(drivers)
    for kind in kinds:
        if kind in shared or kind in override:
            result[kind]=_append([*shared.get(kind,[]),*override.get(kind,[])],[],registry.get(kind,[]),kind)
    for index, layer in enumerate(layers):
        for kind in kinds:
            if kind in layer:
                result[kind] = _append(result.get(kind, []), layer[kind], registry[kind], kind)
        for key, terms in layer.get('attributes', {}).items():
            definition = _definition(key, registry['attributes'], 'attribute')
            attributes = result.setdefault('attributes', {})
            names = [name for name in attributes if _identity(name, registry['attributes']) == ('key', key)]
            if any(_identity(name, registry['attributes']) == ('key', key) for name in drivers):
                raise ValueError(f'Informational additions cannot change variation-driving attribute {definition["value"]}. Use the existing variation editor.')
            name = names[0] if names else definition['value']
            attributes[name] = _append(attributes.get(name, []), terms, definition['terms'], 'term')
    # This is effective data only. Authored source documents remain sparse.
    result.pop('taxonomy_additions', None)
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
