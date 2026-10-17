"""Read-only package choices and effective authored assignment dependencies."""
from app.models import Product
from app.packaging_models import Package
from app.packaging_catalogue import view
from app.packaging_contract import effective


def packages():
    return [view(p) for p in Package.query.order_by(Package.name, Package.id).all()]


def dependencies(package_id):
    from app.taxonomy_assignments import product_document
    found = []
    for product in Product.query.filter_by(catalogue_status='active').enable_eagerloads(False).yield_per(100):
        try:
            document = product_document(product)
        except ValueError:
            continue
        for variant in product.variations if product.product_type == 'variable' else [None]:
            attributes = {a.name:a.value for a in variant.attributes} if variant else None
            if effective(document, attributes).get('selected_package_id') == package_id:
                found.append({'product_id':product.id, 'variant_id':variant.id if variant else None,
                    'sku':variant.sku if variant else product.sku, 'title':product.title})
    return found
