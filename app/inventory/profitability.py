"""Shared finished-product planning arithmetic. Never posts stock or Shopify writes."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_CEILING, localcontext

from app import db
from . import service as stock
from .models import Equipment, Item

DEFAULTS = dict(buffer="10", print_waste_rate="11", tariff=None, labour_rate=None, markup="0", fee_percent="0", fee_fixed="0", overhead="0")


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


def hypothetical_cost(line):
    """One purchase/direct-cost calculation for modal preview and BOM totals.

    A custom factor always means 1 purchase unit = N usage units; compatible
    units use the existing inventory conversion constants instead.
    """
    mode = line.get('cost_mode', 'direct')
    unit = line.get('unit')
    if unit not in stock.UNITS:
        raise ValueError('Choose a supported usage unit.')
    quantity = decimal(line.get('quantity'), 'Quantity used')
    if quantity <= 0 or unit == 'piece' and quantity != quantity.to_integral_value():
        raise ValueError('Usage quantity must be positive; pieces must be whole numbers.')
    family, usage_factor = stock.UNITS[unit]
    normalized_unit = {'mass':'g','volume':'ml','length':'mm','area':'mm2','piece':'piece','sheet':'sheet'}[family]
    with localcontext() as context:
        context.prec = 40
        if mode == 'direct':
            value = line.get('unit_cost')
            rate = None if value is None or not str(value).strip() else decimal(value, 'Direct unit cost') / usage_factor
        elif mode == 'purchase':
            purchase_unit = line.get('purchase_unit')
            if purchase_unit not in stock.UNITS and purchase_unit != 'pack':
                raise ValueError('Choose a purchased quantity unit.')
            purchased = decimal(line.get('purchase_quantity'), 'Purchased quantity')
            if purchased <= 0 or purchase_unit in {'piece','pack'} and purchased != purchased.to_integral_value():
                raise ValueError('Purchased quantity must be positive; purchased pieces and packs must be whole numbers.')
            factor = line.get('conversion_factor')
            compatible = purchase_unit != 'pack' and stock.UNITS[purchase_unit][0] == family
            if compatible:
                if factor is not None and str(factor).strip():
                    raise ValueError('Compatible units use their built-in conversion. Clear the custom factor.')
                purchased_base = purchased * stock.UNITS[purchase_unit][1]
            else:
                if purchase_unit == 'pack' and unit != 'piece':
                    raise ValueError('Purchased packs require piece usage and an explicit pieces-per-pack count.')
                if factor is None or not str(factor).strip():
                    raise ValueError('These units require an explicit conversion: 1 purchased unit = N usage units (pieces per pack for packs).')
                factor = decimal(factor, 'Conversion: usage units per purchased unit')
                if factor <= 0 or unit == 'piece' and factor != factor.to_integral_value():
                    raise ValueError('Conversion factor must be positive; pieces per purchased unit must be whole numbers.')
                purchased_base = purchased * factor * usage_factor
            value = line.get('purchase_cost')
            rate = None if value is None or not str(value).strip() else decimal(value, 'Total purchase cost') / purchased_base
        else:
            raise ValueError('Choose purchase cost or direct unit cost mode.')
        amount = None if rate is None else quantity * usage_factor * rate
        return dict(quantity=quantity, unit=unit, normalized_unit=normalized_unit, normalized_cost=rate,
                    usage_unit_cost=None if rate is None else rate * usage_factor, amount=amount)


def material_money(value):
    if value is None:
        return None
    if 0 < value < Decimal('.00000001'):
        return '<0.00000001'
    text = format(value.quantize(Decimal('.00000001'), rounding=ROUND_HALF_UP), 'f').rstrip('0').rstrip('.')
    return text + '.00' if '.' not in text else text + '0' if len(text.split('.')[1]) == 1 else text


def material_allowance(amount, rate):
    return amount * rate / 100


def component_breakdown(item, settings=None):
    """Read-only recipe estimate; allowance never enters stored recipes or stock rates."""
    rate = decimal((settings or {}).get('buffer', DEFAULTS['buffer']), 'Manufactured-recipe material allowance')
    rows = []
    base = stock.recipe_estimate(item)
    if not item.yield_qty:
        return None
    with localcontext() as context:
        context.prec = 40
        for recipe in item.materials:
            batch_qty = Decimal(recipe.qty) / stock.SCALE
            unit_rate = stock.rate(recipe.material)
            batch_cost = None if unit_rate is None else Decimal(stock.converted(
                recipe.qty, recipe.unit, recipe.material.unit)) / stock.SCALE * unit_rate
            scopes = {}
            for scope, divisor in (('piece', Decimal(item.yield_qty)), ('batch', Decimal(1))):
                quantity = batch_qty / divisor
                waste_qty = material_allowance(quantity, rate)
                cost = None if batch_cost is None else batch_cost / divisor
                waste_cost = None if cost is None else material_allowance(cost, rate)
                scopes[scope] = dict(quantity=material_money(quantity), waste_quantity=material_money(waste_qty),
                    total_quantity=material_money(quantity + waste_qty), base=material_money(cost),
                    waste=material_money(waste_cost), total=material_money(None if cost is None else cost + waste_cost))
            rows.append(dict(name=recipe.material.name, item_id=recipe.material_id, unit=recipe.unit, **scopes))
        totals = {}
        for scope, multiplier in (('piece', Decimal(1)), ('batch', Decimal(item.yield_qty))):
            cost = None if base is None else base * multiplier
            waste = None if cost is None else material_allowance(cost, rate)
            totals[scope] = dict(base=material_money(cost), waste=material_money(waste),
                total=material_money(None if cost is None else cost + waste))
    return dict(rate=str(rate), rows=rows, totals=totals)


def pricing(cost, chosen, config, markup, *, complete, rounding='exact'):
    percent = decimal(config['fee_percent'], 'Payment fee percentage', maximum='99.99') / 100
    fixed = decimal(config['fee_fixed'], 'Fixed transaction charge')
    fees = None if chosen is None else chosen * percent + fixed
    profit = None if chosen is None else chosen - cost - fees
    exact = (cost * (1 + markup / 100) + fixed) / (1 - percent)
    if rounding == 'exact':
        recommended = exact.quantize(Decimal('.01'), rounding=ROUND_CEILING)
    elif rounding in {'half','whole'}:
        step = Decimal('.50') if rounding == 'half' else Decimal(1)
        recommended = (exact / step).to_integral_value(rounding=ROUND_CEILING) * step
    elif rounding == '95':
        recommended = (exact - Decimal('.95')).to_integral_value(rounding=ROUND_CEILING) + Decimal('.95')
    else:
        raise ValueError('Choose a supported selling-price rounding option.')
    suggested_fees = recommended * percent + fixed
    suggested_profit = recommended - cost - suggested_fees
    return dict(cost=money(cost) if complete else None,
                suggested=money(cost * (1 + markup / 100)) if complete else None,
                fee_adjusted=money((cost * (1 + markup / 100) + fixed) / (1 - percent)) if complete else None,
                selling_price=money(chosen) if chosen is not None else None,
                fees=money(fees) if fees is not None else None,
                profit=money(profit) if complete and profit is not None else None,
                margin=money(profit / chosen * 100) if chosen and complete else None,
                suggestion_exact=str(exact) if complete else None,
                recommended_price=money(recommended) if complete else None,
                suggested_fees=money(suggested_fees) if complete else None,
                suggested_profit=money(suggested_profit) if complete else None,
                suggested_margin=money(suggested_profit / recommended * 100) if complete and recommended else None)


def process_runtime(line):
    """Pure runtime validation/allocation shared by JSON contracts and previews."""
    total = decimal(line['minutes'], 'Machine runtime minutes')
    minutes = total
    if line.get('timing_mode', 'item') == 'batch':
        usable = decimal(line.get('usable_units'), 'Usable units')
        if usable <= 0 or usable != usable.to_integral_value():
            raise ValueError('Batch allocation requires a positive whole number of usable units.')
        allocation = line.get('allocation')
        if allocation not in {'identical', 'explicit_equal', 'share'}:
            raise ValueError('Choose an explicit batch allocation method; mixed items are not assumed equal.')
        if allocation == 'share':
            minutes = minutes * decimal(line.get('share_percent'), 'Attributable batch share', maximum='100') / 100
        minutes /= usable
    elif line.get('timing_mode', 'item') != 'item':
        raise ValueError('Choose per-item or batch equipment timing.')
    return total, minutes


def process_cost(line, config):
    """Shared process preview/BOM allocation; persisted runtime remains minutes."""
    total, minutes = process_runtime(line)
    try:
        equipment = db.session.get(Equipment, int(line.get('equipment_id', '')))
    except (TypeError, ValueError):
        raise ValueError('Choose an equipment operating profile.') from None
    missing, provisional, electricity = [], [], None
    if not equipment or not equipment.active or equipment.watts is None:
        missing.append(f'{equipment.name if equipment else "Equipment"}: operating watts are unknown or unavailable.')
    elif config.get('tariff') in (None, ''):
        missing.append('Electricity tariff is not configured.')
    else:
        electricity = decimal(equipment.watts, 'Watts') / 1000 * minutes / 60 * decimal(config['tariff'], 'Tariff')
        if equipment.source != 'measured':
            provisional.append(f'{equipment.name} / {equipment.profile}: {equipment.source} watts.')
    return dict(total_minutes=total, attributable_minutes=minutes, electricity=electricity,
                name=f'{equipment.name} / {equipment.profile}' if equipment else 'Unavailable equipment',
                missing=missing, provisional=provisional)


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
    missing, provisional, rows, base, manufactured = [], [], [], Decimal(0), Decimal(0)
    print_material = Decimal(0)
    buffer_rate = decimal(document.get('buffer', config['buffer']), 'Manufacturing buffer')
    subtotals = {key: Decimal(0) for key in ('raw_material','purchased_component','manufactured_component','packaging')}
    lines = document.get('lines', [])
    if not isinstance(lines, list) or len(lines) > 80:
        raise ValueError('Use at most 80 inventory lines.')
    packaged_ids = {str(line.get('item_id')) for line in lines if line.get('purpose') == 'packaging' and line.get('item_id')}
    other_ids = {str(line.get('item_id')) for line in lines if line.get('purpose') != 'packaging' and line.get('item_id')}
    if packaged_ids & other_ids:
        raise ValueError('Packaging inventory is also present in materials/components. Combine its quantity in one section to avoid double-counting.')
    if not lines:
        missing.append('No inventory BOM lines configured.')
    for line in lines:
        packaging = line.get('purpose') == 'packaging'
        if line.get('source') == 'manual':
            if not direct_materials:
                raise ValueError('Hypothetical materials are supported only in standalone scenarios.')
            calculation = hypothetical_cost(line)
            quantity = calculation['quantity']
            name = str(line.get('name') or '').strip()
            if not name:
                raise ValueError('Name the hypothetical material.')
            if calculation['amount'] is None:
                missing.append(f'{name}: hypothetical unit cost is unknown.')
                rows.append(dict(name=name,cost=None,source='Hypothetical',quantity=str(quantity),unit=line['unit'],unit_cost=None))
            else:
                amount = calculation['amount']
                base += amount
                subtotals['packaging' if packaging else 'raw_material'] += amount
                if direct_materials and not packaging and line.get('print_material') == 'yes':
                    print_material += amount
                rows.append(dict(name=name,cost=material_money(amount),source='Hypothetical packaging' if packaging else 'Hypothetical',quantity=str(quantity),unit=line['unit'],unit_cost=format(calculation['usage_unit_cost'],'f')))
                provisional.append(f'{name}: hypothetical material cost.')
            continue
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
        category = (item.category or '').strip().casefold()
        group = 'packaging' if packaging or category in {'packaging','packaging material','packaging materials'} else {
            'material':'raw_material','purchased':'purchased_component','manufactured':'manufactured_component'}[item.kind]
        subtotals[group] += amount
        if item.kind == 'manufactured':
            manufactured += amount
        elif item.kind == 'material' and group != 'packaging' and line.get('print_material') == 'yes' and direct_materials:
            print_material += amount
        allowance = material_allowance(amount, buffer_rate) if item.kind == 'manufactured' else Decimal(0)
        rows.append(dict(name=item.name, cost=material_money(amount), source='Inventory / saved recipe',
                         allowance=material_money(allowance), allowance_exact=str(allowance),
                         allowance_rate=str(buffer_rate) if item.kind == 'manufactured' else None,
                         with_allowance=material_money(amount + allowance),
                         quantity=str(line['quantity']), unit=line['unit'],
                         unit_cost=format(amount / Decimal(str(line['quantity'])), '.6g')))
    buffer = material_allowance(manufactured, buffer_rate)
    waste_rate = decimal(document.get('print_waste_rate', config['print_waste_rate']), '3D-print material waste allowance', maximum='100')
    print_waste = print_material * waste_rate / 100
    electricity = Decimal(0)
    processes = []
    usage = document.get('equipment', [])
    if not isinstance(usage, list) or len(usage) > 40:
        raise ValueError('Use at most 40 equipment usage lines.')
    for line in usage:
        calculation = process_cost(line, config)
        processes.append(dict(name=calculation['name'], total_minutes=str(calculation['total_minutes']),
            attributable_minutes=str(calculation['attributable_minutes']),
            electricity=material_money(calculation['electricity'])))
        missing.extend(calculation['missing'])
        provisional.extend(calculation['provisional'])
        if calculation['electricity'] is not None:
            electricity += calculation['electricity']
    labour_minutes = decimal(document.get('labour_minutes', '0'), 'Hands-on labour minutes')
    labour_rate = document.get('labour_rate', config['labour_rate'])
    if labour_minutes and labour_rate is None:
        missing.append('Hands-on labour rate is not configured.')
    labour = labour_minutes / 60 * decimal(labour_rate or '0', 'Labour rate')
    other = decimal(document.get('other', '0'), 'Additional costs') + decimal(config['overhead'], 'Overhead')
    total = base + buffer + print_waste + electricity + labour + other
    markup = decimal(document.get('markup', config['markup']), 'Markup')
    entered_price = document.get('selling_price', '0')
    chosen = None if entered_price is None or str(entered_price).strip() == '' else decimal(entered_price, 'Chosen selling price')
    amounts = pricing(total, chosen, config, markup, complete=not missing, rounding=document.get('price_rounding','exact'))
    if selling_fees and not document.get('fee_profile') and not {'fee_percent', 'fee_fixed'} <= (settings or {}).keys():
        missing.append('Payment fees are not configured. Save applicable defaults or choose a fee profile; fees are not assumed free.')
        amounts.update(fees=None, fee_adjusted=None, profit=None, margin=None)
        amounts.update(recommended_price=None, suggested_fees=None, suggested_profit=None, suggested_margin=None, suggestion_exact=None)
    if not selling_fees:
        amounts.update(fees=None, fee_adjusted=None, profit=None, margin=None)
    return dict(complete=not missing, state='incomplete' if missing else 'provisional' if provisional else 'complete',
                provisional=provisional, missing=missing, lines=rows, material_component=money(base),
                subtotals={k:money(v) for k,v in subtotals.items()}, markup=format(markup,'f'),
                buffer=money(buffer), buffer_exact=str(buffer), buffer_rate=str(buffer_rate),
                electricity=money(electricity), labour=money(labour), other=money(other),
                processes=processes, labour_minutes=str(labour_minutes), labour_rate=labour_rate,
                print_waste=money(print_waste), print_waste_exact=str(print_waste),
                print_material_exact=str(print_material), print_waste_rate=str(waste_rate),
                packaging_included=any(line.get('purpose') == 'packaging' for line in lines) or subtotals['packaging'] > 0,
                **amounts,
                assumption='One finished product / bundle per transaction; fixed payment charge applied once. No VAT assumed.')


def finished_product_estimate(product, sku, *, documents=None, projection=None):
    """Current unit cost shared by product profitability and bundles, before selling fees."""
    import json
    from .models import CostingSettings, ProductCostingProfile
    from .product_bom import product_costs
    try:
        projection = projection if projection is not None else product_costs(product, documents).get(sku)
    except (ValueError, OSError):
        return dict(complete=False, cost=None, missing=['Finished-product BOM cannot currently be resolved.'])
    if not projection or projection.get('state') != 'complete':
        return dict(complete=False, cost=None, missing=['Finished-product BOM is missing or incomplete.'])
    profile = db.session.get(ProductCostingProfile, (product.id, sku))
    settings = db.session.get(CostingSettings, 1)
    from .product_bom import authored
    from .catalogue_bom import document as catalogue_document
    shared, local = documents if documents is not None else authored(product)
    document = catalogue_document(projection, shared.get('production', {}), local.get('production', {}),
                                  sku, legacy=json.loads(profile.document) if profile else {})
    return estimate(document, json.loads(settings.document) if settings else {}, direct_materials=True, selling_fees=False)


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
