"""Resolve local product requirements without moving stock or sending BOMs remotely."""
from decimal import Decimal, ROUND_HALF_UP, localcontext
import re

from app import db
from . import service as stock
from .models import Item

KEY = 'production'
LINE_ID = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}\Z')
MODES = {'unconfigured', 'calculated', 'manual'}
CENT = Decimal('.01')


def _object(value, allowed, label):
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError(f'{label} must contain only supported fields.')


def _layer(layer):
    _object(layer, {'lines', 'cost'}, 'Production layer')
    if 'cost' in layer:
        cost = layer['cost']
        _object(cost, {'mode', 'amount'}, 'Production cost')
        mode = cost.get('mode')
        if not isinstance(mode, str) or mode not in MODES or ('amount' in cost) != (mode == 'manual'):
            raise ValueError('Choose an unconfigured, calculated or manual production cost; only manual costs include an amount.')
        if mode == 'manual':
            stock.cost_number(cost['amount'], 'Manual production cost', optional=False)
    lines = layer.get('lines', {})
    if not isinstance(lines, dict) or len(lines) > 80:
        raise ValueError('A production layer supports at most 80 identified line changes.')
    for key, row in lines.items():
        if not LINE_ID.fullmatch(key):
            raise ValueError('Each BOM line needs a stable line ID.')
        _object(row, {'op', 'item_id', 'quantity', 'unit'}, 'BOM line')
        op = row.get('op')
        fields = set(row) - {'op'}
        if (op == 'add' and fields != {'item_id', 'quantity', 'unit'} or op == 'modify' and not fields
                or op == 'remove' and fields or op not in ('add', 'modify', 'remove')):
            raise ValueError('Use an explicit add, modify or remove instruction for each BOM line.')
        if 'item_id' in row and (type(row['item_id']) is not int or not 0 < row['item_id'] < 10**13):
            raise ValueError('Select an existing inventory item ID for each BOM requirement.')
        if 'unit' in row and (not isinstance(row['unit'], str) or row['unit'] not in stock.UNITS):
            raise ValueError('Choose a supported inventory unit.')
        if 'quantity' in row:
            stock.number(row['quantity'], 'BOM quantity', positive=True)


def validate(value):
    _object(value, {'lines', 'cost', 'variants'}, 'Production & Costing')
    _layer({k: v for k, v in value.items() if k != 'variants'})
    variants = value.get('variants', {})
    if not isinstance(variants, dict) or len(variants) > 160:
        raise ValueError('Variant BOM changes must be keyed by retained variant SKU (up to 160 per metadata owner).')
    for sku, layer in variants.items():
        if not sku.strip() or len(sku) > 191:
            raise ValueError('A variant BOM requires its retained SKU.')
        _layer(layer)


def authored(product):
    from app.metadata_workspace import metadata_source
    shared = metadata_source(product, 'shared')
    override = metadata_source(product, 'override')
    if override['reference'] == shared['reference']:
        override = {'data': {}}
    return shared['data'], override['data']


def _amount(value):
    return format(value, 'f') if value is not None else None


def item_rate(item):
    rate = stock.rate(item)
    return stock.recipe_estimate(item) if rate is None and item.kind == 'manufactured' else rate


def _layers(shared, override, variant_sku):
    shared, override = shared or {}, override or {}
    layers = [('Shared', shared), ('Product', override)]
    if variant_sku is not None:
        layers.extend((('Shared variant', shared.get('variants', {}).get(variant_sku, {})),
                       ('Variant', override.get('variants', {}).get(variant_sku, {}))))
    return layers


def _line_state(layers):
    all_lines, warnings, selected, conflict = {}, [], None, False
    for origin, layer in layers:
        if 'cost' in layer:
            selected = (origin, layer['cost'])
        for key, operation in layer.get('lines', {}).items():
            action = operation['op']
            if (key in all_lines) == (action == 'add'):
                warnings.append(f'{origin}: BOM line {key} conflicts with its inherited source. Reset the conflicting adjustment.')
                conflict = True
                continue
            previous = all_lines.get(key, {})
            all_lines[key] = dict(previous, **{k: v for k, v in operation.items() if k != 'op'},
                                  line_id=key, origin=origin, action=action, excluded=action == 'remove')
    return all_lines, warnings, selected, conflict


def resolve(shared, override=None, variant_sku=None, *, rates=None):
    """Shared → product → targeted shared-variant → targeted product-variant.

    Lines retain their keys even when hidden by a tombstone. A later layer may
    explicitly modify and re-enable that same requirement, never accidentally
    by omitting an instruction. No variant order or option label is used.
    """
    all_lines, warnings, selected, conflict = _line_state(_layers(shared, override, variant_sku))
    rates = {} if rates is None else rates
    rows, total, incomplete = [], Decimal(0), conflict
    with localcontext() as ctx:
        ctx.prec = 48
        for record in all_lines.values():
            row = dict(record, name=None, kind=None, cost=None, problem=None)
            if not row['excluded']:
                item_id = row['item_id']
                if item_id not in rates:
                    item = db.session.get(Item, item_id)
                    rates[item_id] = (item, item_rate(item) if item is not None and item.active else None)
                item, rate = rates[item_id]
                if item is None:
                    row['problem'] = f'Inventory item #{item_id} is missing.'
                else:
                    row.update(name=item.name, kind=item.kind)
                    try:
                        amount = stock.converted(stock.number(row['quantity'], 'BOM quantity', positive=True), row['unit'], item.unit)
                    except ValueError as exc:
                        row['problem'] = str(exc)
                    else:
                        if not item.active:
                            row['problem'] = 'Inventory item is archived.'
                        elif rate is None:
                            row['problem'] = 'Inventory unit cost is missing.'
                        else:
                            line_cost = Decimal(amount) * rate / stock.SCALE
                            row['cost'] = _amount(line_cost)
                            total += line_cost
                if row['problem']:
                    incomplete = True
                    warnings.append(f"{row['name'] or '#' + str(item_id)}: {row['problem']}")
            rows.append(row)
        count = sum(not row['excluded'] for row in rows)
        configured = bool(all_lines or conflict)
        state = 'incomplete' if incomplete else 'complete' if count else 'empty' if configured else 'not_configured'
        origin, choice = selected or ('Default', {'mode': 'calculated' if configured else 'unconfigured'})
        estimate = total if state == 'complete' else None
        effective = (Decimal(stock.cost_number(choice['amount'], optional=False)) / stock.SCALE if choice['mode'] == 'manual'
                     else estimate if choice['mode'] == 'calculated' else None)
        if effective is not None and effective > Decimal(stock.MAX_COST) / stock.SCALE:
            warnings.append('Calculated cost is outside the supported GBP range.')
            state, effective = 'incomplete', None
        return dict(rows=rows, count=count, state=state, warnings=warnings, estimated=_amount(estimate),
                    mode=choice['mode'], mode_source=origin,
                    shopify=_amount(effective.quantize(CENT, rounding=ROUND_HALF_UP)) if effective is not None else None)


def product_costs(product, documents=None):
    """Per sellable SKU for editors, durable review plans and future fulfilment."""
    shared_doc, override_doc = documents if documents is not None else authored(product)
    if KEY not in shared_doc and KEY not in override_doc:
        return {}
    shared, override = shared_doc.get(KEY, {}), override_doc.get(KEY, {})
    validate(shared)
    validate(override)
    rates = {}
    if product.product_type != 'variable':
        return {product.sku: resolve(shared, override, rates=rates)}
    variants = [v for v in product.variations if v.catalogue_status == 'active']
    result = {v.sku: resolve(shared, override, v.sku, rates=rates) for v in variants}
    missing = set(override.get('variants', {})) - result.keys()
    if missing:
        for value in result.values():
            value['warnings'].append('Unmatched variant BOM overrides (retained, not reassigned): ' + ', '.join(sorted(missing)))
            if value['mode'] == 'calculated':
                value.update(state='incomplete', shopify=None)
    return result


def contract(product, documents=None):
    """Small, private plan projection; never pass inventory/BOM records to Shopify."""
    return {sku: {'mode': v['mode'], 'value': v['shopify'],
                  **({'problem': (v['warnings'][0] if v['warnings'] else 'No costed components are configured.')[:220]}
                     if v['mode'] == 'calculated' and v['shopify'] is None else {})}
            for sku, v in product_costs(product, documents).items() if v['mode'] != 'unconfigured'}


def editor_options(product):
    rows = Item.query.filter_by(active=True).order_by(Item.name, Item.id).all()
    return {'items': [{'id': i.id, 'name': i.name, 'code': i.identity.code, 'supplier_sku': i.supplier_sku,
                       'unit': i.unit, 'units': [u for u, spec in stock.UNITS.items() if spec[0] == stock.UNITS[i.unit][0]]}
                      for i in rows],
            'variants': [{'sku': v.sku, 'label': ' · '.join(f'{a.name}: {a.value}' for a in v.attributes)}
                         for v in product.variations if v.catalogue_status == 'active'] if product.product_type == 'variable' else []}


def check_changed_references(before, after, *, inherited=None):
    """Reject invalid newly authored references without trapping old broken BOMs."""
    old_variants = before.get('variants', {})
    for sku, old, new in [(None, before, after),
                          *((sku, old_variants.get(sku, {}), patch) for sku, patch in after.get('variants', {}).items())]:
        state, _, _, _ = _line_state(_layers(inherited, after, sku) if inherited is not None else _layers(after, {}, sku))
        for line_id, line in new.get('lines', {}).items():
            if line == old.get('lines', {}).get(line_id) or line['op'] == 'remove':
                continue
            resolved = state.get(line_id)
            if not resolved or (line['op'] == 'add' and resolved['item_id'] != line['item_id']):
                raise ValueError(f'BOM line {line_id} no longer has an inherited source. Reset it or add a separate line.')
            item = stock.item_for(resolved['item_id'])
            stock.converted(stock.number(resolved['quantity'], 'BOM quantity', positive=True), resolved['unit'], item.unit)
