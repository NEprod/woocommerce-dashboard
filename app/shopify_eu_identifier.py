"""Signed Y081 variant compliance stage; the normal product receipt owns recovery."""
from app import db, shopify_drafts as drafts
from app.models import Product
from app.shopify_capacity import background
from app.shopify_connection import ShopifyError, _gid
from app.taxonomy_assignments import product_document

KEY = 'no_eu_standardized_identifier'
MANUAL = 'No EU standardized identifier changes require explicit Review & Sync approval.'


def _read(adapter, config, product_id, nonce, ids, *, probe=False):
    if len(ids) > 32 or len(set(ids)) != len(ids):
        raise ShopifyError('Y081 needs distinct retained variant identities within the review bound.')
    try:
        result = adapter.read_eu_identifier(config, ids, probe=probe)
    except ShopifyError as exc:
        raise ShopifyError('Shopify 2027-01 Y081 readback is unavailable. ' + str(exc)) from None
    if not isinstance(result, dict):
        raise ShopifyError('Shopify Y081 variant readback is incomplete.')
    if probe:
        for alias, field, expected in (
            ('bulk', 'inputFields', {'compliance'}),
            ('declaration', 'inputFields', {'key', 'value'}),
            ('keys', 'enumValues', {'Y081'}),
        ):
            node = result.get(alias)
            entries = node.get(field) if isinstance(node, dict) else None
            if not isinstance(entries, list) or not expected <= {
                    v['name'] for v in entries if isinstance(v, dict) and isinstance(v.get('name'), str)}:
                raise ShopifyError('Shopify 2027-01 does not expose the Y081 compliance update schema. No declaration write is authorised.')
    nodes = result.get('nodes', [] if not ids else None)
    if not isinstance(nodes, list) or len(nodes) != len(ids):
        raise ShopifyError('Shopify Y081 variant readback is incomplete.')
    observed = {}
    for node, vid in zip(nodes, ids):
        parent = node.get('product') if isinstance(node, dict) else None
        correlation = parent.get('metafield') if isinstance(parent, dict) else None
        if (not isinstance(parent, dict) or node.get('id') != vid or parent.get('id') != product_id
                or not isinstance(correlation, dict) or correlation.get('value') != nonce):
            raise ShopifyError('Y081 variant/parent identity does not match the retained product.')
        compliance = node.get('compliance')
        if not isinstance(compliance, list):
            raise ShopifyError('Shopify Y081 compliance readback is incomplete.')
        values = {}
        for entry in compliance:
            if (not isinstance(entry, dict) or not isinstance(entry.get('key'), str)
                    or not isinstance(entry.get('value'), str) or entry['key'] in values):
                raise ShopifyError('Shopify returned ambiguous compliance values; Y081 is not verified.')
            values[entry['key']] = entry['value']
        if 'Y081' in values and values['Y081'] != 'true':
            raise ShopifyError('Shopify returned an unsupported Y081 value; the declaration is not verified.')
        observed[vid] = {'enabled': 'Y081' in values, 'standardized': 'S_PID' in values}
    return observed


def _rows(contract, *, verified=False):
    after = contract['desired']
    for target in contract['targets']:
        before = after if verified else target['before']
        yield {'field': KEY + '.' + target['sku'],
               'label': 'No EU standardized identifier · ' + target['sku'],
               'before': None if before is None else 'Enabled' if before else 'Disabled',
               'after': 'Enabled' if after else 'Disabled', 'changed': before is not after}


def attach(product, plan, config, shop, adapter):
    authored = product_document(product)
    if KEY not in authored:
        return
    desired = authored[KEY]
    if type(desired) is not bool:
        raise ShopifyError('No EU standardized identifier must be true or false, or left unspecified.')
    if 'read_products' not in shop['scopes']:
        raise ShopifyError('Missing read_products; Shopify Y081 cannot be compared.')
    targets = ([{'key': v['identity'], 'sku': v['sku'], 'id': v.get('remote_id')}
                for v in plan['variants']] if plan.get('kind') == 'variable' else
               [{'key': None, 'sku': plan['sku'], 'id': plan.get('variant_id')}])
    observed = _read(adapter, config, plan.get('remote_id'), plan['nonce'],
                     [v['id'] for v in targets if v['id']], probe=True)
    for target in targets:
        current = observed.get(target['id'])
        target['before'] = current['enabled'] if current else None
        if desired and current and current['standardized']:
            plan['blockers'].append('Y081 cannot be enabled for ' + target['sku'] + ' while Shopify has a standardized identifier. No barcode will be changed.')
    contract = plan['eu_identifier'] = {'desired': desired, 'targets': targets}
    plan['fields'].extend(_rows(contract))
    if any(v['before'] is not desired for v in targets):
        if 'write_products' not in shop['scopes']:
            plan['blockers'].append('Missing write_products; the reviewed Y081 declaration cannot be changed.')
        if (background() or {}).get('publishing_context'):
            plan['blockers'].append(MANUAL)
    plan['taxonomy_review_digest'] = drafts.digest({
        'previous': plan['taxonomy_review_digest'], 'eu_identifier': contract})


def _targets(data):
    result = []
    for target in data['plan']['eu_identifier']['targets']:
        vid = (data.get('variant_maps', {}).get(target['key'], {}).get('id') if target['key'] is not None
               else data.get('variant_id'))
        _gid(vid, 'ProductVariant')
        if target['id'] and vid != target['id']:
            raise ShopifyError('The reviewed Y081 variant identity changed; no write or verification allowed.')
        result.append((target, vid))
    return result


def apply(row, data, config, adapter):
    plan = data['plan']
    contract = plan.get('eu_identifier')
    if not contract or all(v['before'] is contract['desired'] for v in contract['targets']):
        return
    if (background() or {}).get('publishing_context'):
        raise ShopifyError(MANUAL)
    product = db.session.get(Product, row.product_id)
    if (not product or drafts.local_plan(product)['digest'] != plan['digest']
            or drafts.configuration().fingerprint() != plan['config_digest']):
        raise ShopifyError('Source or configuration changed before the reviewed Y081 write. Rebuild Review.')
    targets = _targets(data)
    observed = _read(adapter, config, row.shopify_product_id, plan['nonce'], [vid for _, vid in targets])
    desired = contract['desired']
    changed = []
    for target, vid in targets:
        actual = observed[vid]
        if desired and actual['standardized']:
            raise ShopifyError('A standardized identifier prevents enabling Y081. No barcode was changed.')
        if target['id'] and actual['enabled'] is not target['before']:
            raise ShopifyError('Shopify Y081 changed since Review. No declaration was written; inspect the latest readback.')
        if actual['enabled'] is not desired:
            changed.append(vid)
    if not changed:
        return
    data['stage'] = 'eu_identifier_update'
    data['eu_identifier_attempt'] = {'operation_id': row.operation_id, 'state': 'pending', 'variants': changed}
    drafts.save(row, data, 'updating')
    result = adapter.write_eu_identifier(config, row.shopify_product_id, changed, desired)
    payload = drafts.payload_result(result, 'productVariantsBulkUpdate')
    returned = payload.get('productVariants')
    if (payload.get('userErrors') != [] or not isinstance(returned, list) or len(returned) != len(changed)
            or any(not isinstance(v, dict) or v.get('id') not in changed for v in returned)
            or {v['id'] for v in returned} != set(changed)):
        raise ShopifyError('Shopify Y081 mutation evidence is incomplete. Reconcile using reads only; no write retry.')
    data['eu_identifier_attempt']['state'] = 'acknowledged'
    drafts.completed_step(row, data, 'eu_identifier_update')


def verify(row, data, config, adapter):
    plan = data['plan']
    contract = plan.get('eu_identifier')
    if not contract:
        return []
    ids = [vid for _, vid in _targets(data)]
    observed = _read(adapter, config, row.shopify_product_id, plan['nonce'], ids)
    if any(v['enabled'] is not contract['desired'] or v['enabled'] and v['standardized'] for v in observed.values()):
        raise ShopifyError('Shopify Y081 readback differs from the approved declaration. Reconcile using reads only; no write retry.')
    proof = data.get('eu_identifier_attempt') or {}
    if proof.get('operation_id') == row.operation_id:
        proof['state'] = 'verified'
    return list(_rows(contract, verified=True))
