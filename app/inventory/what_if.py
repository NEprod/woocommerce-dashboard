"""Read-only effective product inputs for independent standalone experiments."""
import copy
import json
from app import db
from .models import ProductCostingProfile


def load(product, sku):
    from .product_bom import product_costs, authored
    from .catalogue_bom import document
    from app.sale_pricing import catalogue
    documents = authored(product)
    costs = product_costs(product, documents)
    if sku not in costs:
        raise ValueError('Choose an existing sellable product/variant SKU.')
    projection = costs[sku]
    if projection.get('state') != 'complete':
        raise ValueError('This product BOM is incomplete: ' + '; '.join(projection.get('warnings') or ['configure its registered requirements first.']))
    prices = catalogue(product)
    price = next((v['price'] for v in prices['variants'] if v['sku'] == sku), product.regular_price)
    profile = db.session.get(ProductCostingProfile, (product.id, sku))
    result = document(projection, documents[0].get('production', {}), documents[1].get('production', {}),
                      sku if product.product_type == 'variable' else None,
                      legacy=json.loads(profile.document) if profile else {}, price=str(price) if price is not None else '')
    # Legacy what-if profile prices must never substitute for the current price.
    result['selling_price'] = str(price) if price is not None else ''
    return copy.deepcopy(result)
