"""Shared finished-product planning arithmetic. Never posts stock or Shopify writes."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app import db
from . import service as stock
from .models import Equipment, Item

DEFAULTS = dict(buffer="10", tariff=None, labour_rate=None, markup="0", fee_percent="0", fee_fixed="0", overhead="0")


def decimal(value, label, *, maximum="1000000000"):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number > Decimal(maximum):
            raise ValueError()
        return number
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label}: enter a non-negative finite number within range.") from None


def money(value):
    return format(value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP), '.2f')


def pricing(cost, chosen, config, markup, *, complete):
    percent = decimal(config['fee_percent'], 'Payment fee percentage', maximum='99.99') / 100
    fixed = decimal(config['fee_fixed'], 'Fixed transaction charge')
    fees = chosen * percent + fixed
    profit = chosen - cost - fees
    return dict(cost=money(cost) if complete else None,
                suggested=money(cost * (1 + markup / 100)) if complete else None,
                fee_adjusted=money((cost * (1 + markup / 100) + fixed) / (1 - percent)) if complete else None,
                selling_price=money(chosen), fees=money(fees), profit=money(profit) if complete else None,
                margin=money(profit / chosen * 100) if chosen and complete else None)


def estimate(document, settings=None, *, direct_materials=False, selling_fees=True):
    """Equipment times and hands-on minutes belong to ONE finished product.

    A fixed fee is charged once for this transaction. Manufactured component
    material is derived from its recipe, never from electricity/labour estimates.
    """
    config = {**DEFAULTS, **(settings or {})}
    if document.get('fee_profile'):
        profiles = {p['id']: p for p in config.get('fee_profiles', [])}
        profile = profiles.get(document['fee_profile'])
        if not profile:
            raise ValueError('Selected payment fee profile is no longer available.')
        config.update(fee_percent=profile['percent'], fee_fixed=profile['fixed'])
    missing, rows, base, manufactured = [], [], Decimal(0), Decimal(0)
    lines = document.get('lines', [])
    if not isinstance(lines, list) or len(lines) > 80:
        raise ValueError('Use at most 80 inventory lines.')
    if not lines:
        missing.append('No inventory BOM lines configured.')
    for line in lines:
        item = db.session.get(Item, int(line['item_id']))
        if not item or not item.active:
            missing.append('A BOM inventory item is missing or archived.')
            continue
        if item.kind == 'material' and not direct_materials:
            raise ValueError('Product BOMs use manufactured or purchased components, not direct raw materials.')
        quantity = stock.converted(stock.number(line['quantity'], 'BOM quantity', positive=True), line['unit'], item.unit)
        rate = stock.recipe_estimate(item) if item.kind == 'manufactured' else stock.rate(item)
        if item.kind == 'manufactured' and any(not r.material.active for r in item.materials):
            rate = None
        if rate is None:
            missing.append(f'{item.name}: material or purchase cost is unknown.')
            rows.append(dict(name=item.name, cost=None))
            continue
        amount = Decimal(quantity) / stock.SCALE * rate
        base += amount
        if item.kind == 'manufactured':
            manufactured += amount
        rows.append(dict(name=item.name, cost=money(amount)))
    buffer = manufactured * decimal(document.get('buffer', config['buffer']), 'Manufacturing buffer') / 100
    electricity = Decimal(0)
    usage = document.get('equipment', [])
    if not isinstance(usage, list) or len(usage) > 40:
        raise ValueError('Use at most 40 equipment usage lines.')
    for line in usage:
        equipment = db.session.get(Equipment, int(line['equipment_id']))
        minutes = decimal(line['minutes'], 'Attributable equipment minutes')
        if not equipment or not equipment.active or equipment.watts is None:
            missing.append(f'{equipment.name if equipment else "Equipment"}: operating watts are unknown or unavailable.')
        elif config['tariff'] is None:
            missing.append('Electricity tariff is not configured.')
        else:
            electricity += decimal(equipment.watts, 'Watts') / 1000 * minutes / 60 * decimal(config['tariff'], 'Tariff')
    labour_minutes = decimal(document.get('labour_minutes', '0'), 'Hands-on labour minutes')
    labour_rate = document.get('labour_rate', config['labour_rate'])
    if labour_minutes and labour_rate is None:
        missing.append('Hands-on labour rate is not configured.')
    labour = labour_minutes / 60 * decimal(labour_rate or '0', 'Labour rate')
    other = decimal(document.get('other', '0'), 'Additional costs') + decimal(config['overhead'], 'Overhead')
    total = base + buffer + electricity + labour + other
    markup = decimal(document.get('markup', config['markup']), 'Markup')
    chosen = decimal(document.get('selling_price', '0'), 'Chosen selling price')
    amounts = pricing(total, chosen, config, markup, complete=not missing)
    if selling_fees and not document.get('fee_profile') and not {'fee_percent', 'fee_fixed'} <= (settings or {}).keys():
        missing.append('Payment fees are not configured. Save applicable defaults or choose a fee profile; fees are not assumed free.')
        amounts.update(fees=None, fee_adjusted=None, profit=None, margin=None)
    if not selling_fees:
        amounts.update(fees=None, fee_adjusted=None, profit=None, margin=None)
    return dict(complete=not missing, missing=missing, lines=rows, material_component=money(base),
                buffer=money(buffer), electricity=money(electricity), labour=money(labour), other=money(other),
                **amounts,
                assumption='One finished product / bundle per transaction; fixed payment charge applied once. No VAT assumed.')


def finished_product_estimate(product, sku):
    """Current unit cost shared by product profitability and bundles, before selling fees."""
    import json
    from .models import CostingSettings, ProductCostingProfile
    from .product_bom import product_costs
    try:
        projection = product_costs(product).get(sku)
    except (ValueError, OSError):
        return dict(complete=False, cost=None, missing=['Finished-product BOM cannot currently be resolved.'])
    if not projection or projection.get('state') != 'complete':
        return dict(complete=False, cost=None, missing=['Finished-product BOM is missing or incomplete.'])
    profile = db.session.get(ProductCostingProfile, (product.id, sku))
    settings = db.session.get(CostingSettings, 1)
    if not profile:
        return dict(complete=False, cost=None, missing=['Finished-product processing assumptions have not been saved.'])
    document = json.loads(profile.document)
    document['lines'] = [dict(item_id=r['item_id'], quantity=r['quantity'], unit=r['unit'])
                         for r in projection['rows'] if not r['excluded']]
    return estimate(document, json.loads(settings.document) if settings else {}, selling_fees=False)


def bundle_estimate(products, document, settings=None):
    """Quantity-weighted finished-product costs; transaction fees charged once.

    Constituent electricity/labour/material buffers are already in unit costs,
    so none is reapplied. Additional lines and processing belong only to bundle.
    """
    from app.models import Product
    if not isinstance(products, list) or len(products) > 80:
        raise ValueError('Use at most 80 finished-product bundle lines.')
    result = estimate(document, settings, direct_materials=True)
    total, missing = Decimal(0), []
    for line in products:
        product = db.session.get(Product, int(line['product_id']))
        quantity = decimal(line['quantity'], 'Bundle quantity')
        if quantity <= 0 or quantity != quantity.to_integral_value():
            raise ValueError('Bundle product quantities must be positive whole numbers.')
        unit = finished_product_estimate(product, line['sku']) if product else dict(complete=False, missing=['A bundle product is missing.'])
        if not unit['complete']:
            missing.extend(unit['missing'])
        else:
            total += Decimal(unit['cost']) * quantity
    if not products:
        missing.append('Choose at least one finished product for the bundle.')
    # An empty bundle-only inventory list is intentionally allowed: constituent
    # products provide the BOM. Other unknown additional costs remain blockers.
    result['missing'] = [m for m in result['missing'] if m != 'No inventory BOM lines configured.'] + missing
    result['complete'] = not result['missing']
    config = {**DEFAULTS, **(settings or {})}
    if document.get('fee_profile'):
        profile = next((p for p in config.get('fee_profiles', []) if p['id'] == document['fee_profile']), None)
        if profile:
            config.update(fee_percent=profile['percent'], fee_fixed=profile['fixed'])
    additional = sum(Decimal(result[key]) for key in ('material_component', 'buffer', 'electricity', 'labour', 'other'))
    cost = total + additional
    markup = decimal(document.get('markup', config['markup']), 'Markup')
    price = Decimal(result['selling_price'])
    result.update(constituent_cost=money(total), **pricing(cost, price, config, markup, complete=result['complete']))
    return result
