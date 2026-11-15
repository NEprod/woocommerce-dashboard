"""Cost-only stage of the existing signed product operation."""
from decimal import Decimal, InvalidOperation

from app import db
from app import shopify_drafts as drafts
from app.models import Product
from app.shopify_connection import ShopifyError, _gid


def _amount(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount.as_tuple().exponent < -12 or not 0 <= amount <= Decimal('1000000000000'):
            raise ValueError()
        return format(amount.normalize(), 'f')
    except (InvalidOperation, ValueError):
        raise ShopifyError('Shopify returned an invalid inventory-item cost.') from None


def _read(adapter, config, product_id, nonce, ids):
    if len(ids) > 32 or len(set(ids)) != len(ids):
        raise ShopifyError('Cost per item needs distinct retained Shopify variant IDs.')
    answer = adapter.read_item_costs(config, ids)
    def object_value(value):
        return value if isinstance(value, dict) else {}
    answer = object_value(answer)
    if object_value(answer.get('shop')).get('currencyCode') != 'GBP':
        raise ShopifyError('Cost per item requires a verified GBP Shopify store; no currency conversion or cost write is allowed.')
    nodes = answer.get('nodes', [] if not ids else None)
    if not isinstance(nodes, list) or len(nodes) != len(ids):
        raise ShopifyError('Shopify inventory-item cost readback is incomplete.')
    result = {}
    for node, ident in zip(nodes, ids):
        node = object_value(node)
        parent = object_value(node.get('product'))
        item = object_value(node.get('inventoryItem'))
        if (not parent or node.get('id') != ident or parent.get('id') != product_id
                or object_value(parent.get('metafield')).get('value') != nonce):
            raise ShopifyError('Shopify cost readback does not match the retained product and variant.')
        inventory_id = _gid(item.get('id'), 'InventoryItem')
        if 'unitCost' not in item:
            raise ShopifyError('Cost readback is unavailable. Check Shopify View product costs permission.')
        cost = item['unitCost']
        if cost is not None and (not isinstance(cost, dict) or cost.get('currencyCode') != 'GBP'):
            raise ShopifyError('Shopify returned incomplete or non-GBP cost readback.')
        result[ident] = (inventory_id, _amount(cost.get('amount')) if cost is not None else None)
    if len({iid for iid, _ in result.values()}) != len(result):
        raise ShopifyError('Shopify cost readback returned overlapping inventory-item identities.')
    return result


def _rows(targets, verified=False):
    for target in targets:
        before = Decimal(target['value'] if verified else target['before']) if verified or target['before'] is not None else None
        comparable = format(before, '.2f') if before is not None and before == before.quantize(Decimal('.01')) else format(before, 'f') if before is not None else None
        source = 'Manual cost' if target['mode'] == 'manual' else 'Calculated BOM'
        yield {'field': 'cost_per_item.' + target['sku'], 'label': 'Cost per item · ' + target['sku'],
               'before': '£' + comparable if comparable is not None else None,
               'after': '£' + target['value'] + ' (' + source + ')',
               'changed': before != Decimal(target['value'])}


def attach(plan, config, shop, adapter):
    costs = plan.get('production_costs') or {}
    if not costs:
        return
    for sku, cost in costs.items():
        if cost.get('problem'):
            reason = 'Cost per item · ' + sku + ': ' + cost['problem'] + ' Complete the BOM, choose a manual cost or leave Shopify cost unmanaged.'
            plan['blockers'].append(reason)
            plan.setdefault('item_cost_diagnostics', []).append({'reason':reason, 'authored':True})
    targets = ([{'key': v['identity'], 'sku': v['sku'], 'id': v.get('remote_id'), 'inventory': v.get('inventory_id')}
                for v in plan['variants']] if plan.get('kind') == 'variable' else
               [{'key': None, 'sku': plan['sku'], 'id': plan.get('variant_id'), 'inventory': plan.get('inventory_item_id')}])
    targets = [dict(t, **costs[t['sku']]) for t in targets if costs.get(t['sku'], {}).get('value') is not None]
    if not targets:
        return
    try:
        if not {'read_inventory', 'read_products', 'write_inventory'} & set(shop['scopes']):
            raise ShopifyError('Missing read_inventory or read_products.')
        observed = _read(adapter, config, plan.get('remote_id'), plan['nonce'], [t['id'] for t in targets if t['id']])
        for t in targets:
            iid, t['before'] = observed[t['id']] if t['id'] else (None, None)
            if iid != t['inventory']:
                raise ShopifyError('Shopify Cost per item inventory ID differs from the retained variant mapping.')
    except ShopifyError as exc:
        from app.shopify_connection import ShopifyReadThrottled
        if isinstance(exc, ShopifyReadThrottled):
            raise
        reason = 'Shopify Cost per item could not be verified. Check View product costs permission. ' + str(exc)
        plan['blockers'].append(reason)
        plan.setdefault('item_cost_diagnostics', []).append({'reason':reason})
        return
    plan['item_cost'] = targets
    rows = list(_rows(targets))
    plan['fields'].extend(rows)
    if any(r['changed'] for r in rows) and 'write_inventory' not in shop['scopes']:
        plan['blockers'].append('Missing write_inventory; Shopify Cost per item cannot be changed.')
    plan['taxonomy_review_digest'] = drafts.digest({'previous': plan['taxonomy_review_digest'], 'item_cost': targets})


def _targets(data):
    targets = []
    for t in data['plan'].get('item_cost', []):
        if t['key'] is None:
            vid, iid = data.get('variant_id'), data.get('inventory_item_id')
        else:
            mapping = data.get('variant_maps', {}).get(t['key'], {})
            vid, iid = mapping.get('id'), mapping.get('inventory_id')
            if mapping.get('sku') != t['sku']:
                raise ShopifyError('Cost per item needs the verified variant mapping for ' + t['sku'] + '.')
        receipt = data.get('item_cost_attempt', {}).get('items', {}).get(t['sku'], {})
        if ((t['id'] and vid != t['id']) or (t['inventory'] and iid != t['inventory'])
                or (receipt and (vid != receipt['variant'] or iid != receipt['id']))):
            raise ShopifyError('The reviewed Cost per item identity changed; no cost write is allowed.')
        targets.append((t, _gid(vid, 'ProductVariant'), _gid(iid, 'InventoryItem') if iid else None))
    return targets


def apply(row, data, config, adapter):
    plan = data['plan']
    if not any(r['changed'] for r in _rows(plan.get('item_cost', []))):
        return
    product = db.session.get(Product, row.product_id)
    if not product or drafts.local_plan(product)['digest'] != plan['digest'] or drafts.configuration().fingerprint() != plan['config_digest']:
        raise ShopifyError('Source costs or Shopify configuration changed after Review. Rebuild Review before writing cost.')
    targets = _targets(data)
    observed = _read(adapter, config, row.shopify_product_id, plan['nonce'], [vid for _, vid, _ in targets])
    changed = []
    for t, vid, iid in targets:
        found, value = observed[vid]
        if (iid and found != iid) or (t['id'] and value != t['before']):
            raise ShopifyError('Shopify cost or inventory identity changed since Review for ' + t['sku'] + '. Rebuild Review.')
        if value is None or Decimal(value) != Decimal(t['value']):
            changed.append((t, vid, found))
    if not changed:
        return
    proof = data['item_cost_attempt'] = {'operation_id': row.operation_id, 'items': {}}
    for t, vid, iid in changed:
        if t['key'] is None:
            data['inventory_item_id'] = iid
        data['stage'] = 'item_cost_update'
        stamp = proof['items'][t['sku']] = {'variant': vid, 'id': iid, 'state': 'pending'}
        drafts.save(row, data, 'updating')
        payload = drafts.payload_result(adapter.write_item_cost(config, iid, t['value']), 'inventoryItemUpdate')
        if payload.get('userErrors') != [] or (payload.get('inventoryItem') or {}).get('id') != iid:
            raise ShopifyError('Shopify Cost per item mutation evidence is incomplete. Reconcile with reads; do not retry blindly.')
        stamp['state'] = 'acknowledged'
        drafts.save(row, data)
    drafts.completed_step(row, data, 'item_cost_update')


def verify(row, data, config, adapter):
    if not data['plan'].get('item_cost'):
        return []
    targets = _targets(data)
    observed = _read(adapter, config, row.shopify_product_id, data['plan']['nonce'], [vid for _, vid, _ in targets])
    for t, vid, iid in targets:
        real, cost = observed[vid]
        if real != iid or cost is None or Decimal(cost) != Decimal(t['value']):
            raise ShopifyError('Shopify Cost per item readback differs for ' + t['sku'] + '. Reconcile with reads; do not retry blindly.')
    proof = data.get('item_cost_attempt', {})
    if proof.get('operation_id') == row.operation_id:
        for entry in proof['items'].values():
            entry['state'] = 'verified'
    data.setdefault('verification', {})['item_cost'] = 'verified'
    return list(_rows(data['plan']['item_cost'], verified=True))
