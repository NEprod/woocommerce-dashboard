"""Shared, read-only sellable-variant costing projection from the authoritative BOM."""

from decimal import Decimal, ROUND_HALF_UP

from app import db
from app.models import Product
from .models import Item
from .product_bom import product_costs


def pennies(value):
    return int((Decimal(str(value)) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def variant_costs(variants):
    """Resolve each retained SKU once; unknown BOM inputs are never treated as zero."""
    products, result = {}, {}
    for variant in variants:
        product_id, sku = variant['local_id'], variant['sku']
        if product_id not in products:
            product = db.session.get(Product, product_id)
            try:
                products[product_id] = product_costs(product) if product else {}
            except (ValueError, OSError) as exc:
                products[product_id] = {'_error':str(exc)[:220]}
        projection = products[product_id].get(sku)
        if not isinstance(projection, dict):
            message = products[product_id].get('_error') or 'No resolved product/variant BOM cost is configured.'
            result[sku] = {'sku':sku, 'cost':None, 'bom_complete':False, 'mode':'unconfigured',
                           'material_component':None, 'packaging':None, 'lines':[], 'missing':[message]}
            continue
        lines, material, packaging, packaging_seen = [], Decimal(0), Decimal(0), False
        ids = [line['item_id'] for line in projection['rows'] if not line['excluded'] and line.get('item_id')]
        categories = {item.id:(item.category or '').strip().casefold() for item in
                      Item.query.filter(Item.id.in_(ids)).all()} if ids else {}
        for line in projection['rows']:
            if line['excluded']:
                continue
            category = categories.get(line['item_id'], '')
            is_packaging = category in {'packaging', 'packaging material', 'packaging materials'}
            if is_packaging:
                packaging_seen = True
            amount = Decimal(str(line['cost'])) if line.get('cost') is not None else None
            if amount is not None:
                if is_packaging:
                    packaging += amount
                else:
                    material += amount
            lines.append({'name':line.get('name') or f'Inventory item #{line["item_id"]}',
                          'category':category or 'Unclassified BOM item', 'cost':pennies(amount) if amount is not None else None,
                          'problem':line.get('problem')})
        mode = projection['mode']
        amount = projection.get('shopify')
        complete = amount is not None and mode in {'calculated', 'manual'}
        itemized = projection['state'] == 'complete' and mode == 'calculated'
        missing = list(dict.fromkeys(projection.get('warnings') or []))
        if mode == 'manual':
            missing.append('Manual product cost is available, but its component breakdown is not verified by this cost source.')
        result[sku] = {'sku':sku, 'cost':pennies(amount) if amount is not None else None,
                       'bom_complete':complete, 'mode':mode,
                       'material_component':pennies(material) if itemized else None,
                       'packaging':pennies(packaging) if itemized and packaging_seen else None,
                       'lines':lines, 'missing':missing}
        if mode == 'calculated':
            from .profitability import finished_product_estimate
            try:
                planned = finished_product_estimate(db.session.get(Product, product_id), sku, projection=projection)
            except (ValueError, OSError) as exc:
                planned = dict(complete=False, cost=None, missing=[str(exc)[:220]])
            result[sku].update(cost=pennies(planned['cost']) if planned.get('cost') is not None else None,
                               bom_complete=planned['complete'], missing=planned['missing'], finished_product=True)
            for key, label in [('buffer','Manufacturing allowance'), ('print_waste','3D-print material waste allowance'),
                               ('electricity','Equipment electricity'),
                               ('labour','Hands-on labour'), ('other','Other / overhead')]:
                if key in planned:
                    result[sku]['lines'].append(dict(name=label, category='Finished-product planning',
                                                     cost=pennies(planned[key]), problem=None))
    return result
