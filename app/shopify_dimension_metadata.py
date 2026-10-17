"""Reviewed finished-size definition ownership; never native shipping dimensions.

Values retain the established product_length/width/height dimension keys. One
bounded definition read per owner covers shared access independently of values.
"""
from app import shopify_drafts as drafts
from app.shopify_connection import ShopifyError, _gid

POLICY_VERSION = 1
AXES = ('length', 'width', 'height')


def requirements(plan):
    result = {}
    for item in plan['managed']['product'].get('metafields', []):
        if item['key'] in {'product_' + axis for axis in AXES}:
            result[('PRODUCT', item['key'])] = item
    for variant in plan.get('variants', []):
        for item in variant['managed'].get('metafields', []):
            if item['key'] in {'product_' + axis for axis in AXES}:
                result[('PRODUCTVARIANT', item['key'])] = item
    return result


def read(plan, config, adapter):
    wanted = requirements(plan)
    result = {}
    for owner in sorted({owner for owner, _ in wanted}):
        values = adapter.dimension_definitions(config, plan['managed']['namespace'], owner)
        if not isinstance(values, dict) or set(values) != set(AXES):
            raise ShopifyError('Finished-size definition evidence is incomplete; no equality or creation assumed.')
        for axis in AXES:
            key = 'product_' + axis
            if (owner, key) not in wanted:
                continue
            field = values[axis]
            if field is not None:
                if not isinstance(field, dict):
                    raise ShopifyError('Finished-size definition evidence is invalid.')
                _gid(field.get('id'), 'MetafieldDefinition')
                if (field.get('namespace') != plan['managed']['namespace'] or field.get('key') != key
                        or field.get('ownerType') != owner or (field.get('type') or {}).get('name') != 'dimension'
                        or (field.get('access') or {}).get('storefront') not in {'PUBLIC_READ', 'NONE'}):
                    raise ShopifyError('Existing finished-size definition identity/type/access is incompatible. No duplicate or replacement is permitted.')
            result[owner + '.' + key] = field
    return result


def attach(plan, config, shop, adapter):
    before = read(plan, config, adapter)
    from app import db
    from app.models import ShopifyProductIdentity
    identity = db.session.get(ShopifyProductIdentity, plan['identity_id']) if plan.get('identity_id') else None
    retained = drafts.evidence(identity).get('dimension_definitions', {}) if identity else {}
    if any(proof.get('id') and proof['id'] != (before.get(key) or {}).get('id')
           for key, proof in retained.items() if key in before):
        raise ShopifyError('Retained finished-size definition was removed or replaced. No automatic recreation/adoption; inspect identity recovery.')
    specs = []
    for (owner, key), item in sorted(requirements(plan).items()):
        field = before[owner + '.' + key]
        spec = {'ownerType': owner, 'namespace': item['namespace'], 'key': key,
            'type': 'dimension', 'name': 'TLC finished product ' + key.removeprefix('product_'),
            'access': {'storefront': 'PUBLIC_READ'}}
        changed = field is None or field['access']['storefront'] != 'PUBLIC_READ'
        specs.append({'before': field, 'spec': spec, 'changed': changed})
        plan['fields'].append({'field': 'dimension_definitions.' + owner + '.' + key,
            'label': 'Finished Product Dimensions · ' + owner + ' · ' + key + ' definition / storefront access',
            'before': field['access']['storefront'] if field else None,
            'after': 'PUBLIC_READ', 'changed': changed})
    plan['dimension_metadata'] = {'policy': POLICY_VERSION, 'definitions': specs}
    if any(s['changed'] for s in specs) and 'write_products' not in shop['scopes']:
        plan['blockers'].append('Missing write_products for reviewed finished-size definitions/access.')
    plan['taxonomy_review_digest'] = drafts.digest({'previous': plan.get('taxonomy_review_digest'),
        'finished_dimensions': plan['dimension_metadata']})


def apply(row, data, config, adapter):
    plan = data['plan']
    metadata = plan.get('dimension_metadata')
    if not metadata or not any(s['changed'] for s in metadata['definitions']):
        return
    current = read(plan, config, adapter)
    for entry in metadata['definitions']:
        spec, before = entry['spec'], entry['before']
        if current[spec['ownerType'] + '.' + spec['key']] != before:
            raise ShopifyError('Finished-size definition changed since Review. Rebuild Review; no unreviewed replacement.')
    for entry in metadata['definitions']:
        if not entry['changed']:
            continue
        # All stages are committed before the remote request. A timeout is
        # recovered only through verify(); creation is never blindly replayed.
        from app.models import Product
        from app import db
        product = db.session.get(Product, row.product_id)
        if not product or drafts.local_plan(product)['digest'] != plan['digest'] or drafts.configuration().fingerprint() != config.fingerprint():
            raise ShopifyError('Source/configuration changed during finished-size publication; reconcile before further writes.')
        spec, before = entry['spec'], entry['before']
        key = spec['ownerType'] + '.' + spec['key']
        data['stage'] = 'finished_dimension_definition'
        proof = data.setdefault('dimension_definitions', {})
        proof[key] = {'state': 'pending', 'before_id': before['id'] if before else None}
        drafts.save(row, data)
        if before:
            patch = {k: spec[k] for k in ('ownerType', 'namespace', 'key', 'access')}
            response = adapter.update_dimension_definition(config, patch)
            drafts.payload_result(response, 'metafieldDefinitionUpdate')
        else:
            response = adapter.create_attribute_resource(config, 'field', spec)
            payload = drafts.payload_result(response, 'metafieldDefinitionCreate')
            # Retain the returned identity before independently reading it.
            proof[key]['id'] = _gid((payload.get('createdDefinition') or {}).get('id'), 'MetafieldDefinition')
            drafts.save(row, data)
        observed = read(plan, config, adapter)[key]
        _verify_entry(entry, observed, proof[key])
        proof[key].update(state='verified', id=observed['id'], checked_at=drafts.now().isoformat())
        drafts.save(row, data)


def _verify_entry(entry, observed, proof):
    before = entry['before']
    expected_id = proof.get('id') or (before or {}).get('id')
    if not observed or observed['access']['storefront'] != 'PUBLIC_READ' or expected_id and observed['id'] != expected_id:
        raise ShopifyError('Finished-size definition readback differs or remains incomplete. Read-only recovery is required; do not retry creation.')


def verify(row, data, config, adapter):
    metadata = data['plan'].get('dimension_metadata')
    if not metadata:
        return  # historical operations attest only their historical scope
    observed = read(data['plan'], config, adapter)
    for entry in metadata['definitions']:
        key = entry['spec']['ownerType'] + '.' + entry['spec']['key']
        proof = data.setdefault('dimension_definitions', {}).setdefault(key, {})
        _verify_entry(entry, observed[key], proof)
        proof.update(state='verified', id=observed[key]['id'], checked_at=drafts.now().isoformat())
    drafts.save(row, data)
