"""Read-only catalogue/order projections into the single mixed-basket planner."""
from copy import deepcopy
import json
import uuid
from sqlalchemy import or_
from app import db
from app.models import Product, Variation, ShopifyProductIdentity
from app.packaging_models import Package, ShippingRates, PackingRecord
from app.packaging_catalogue import view, dumps
from app.packaging_contract import effective
from app.packing import plan


def packages():return [view(p) for p in Package.query.order_by(Package.name,Package.id).all()]


def rates():
    row=ShippingRates.query.order_by(ShippingRates.created_at.desc(),ShippingRates.id.desc()).first()
    if not row:return None
    return dict(json.loads(row.configuration),version=row.id)


def item(product,variant=None,*,quantity=1,identity=None):
    from app.taxonomy_assignments import product_document
    from app.product_defaults import effective as defaults
    document=product_document(product)
    attrs={a.name:a.value for a in variant.attributes} if variant else None
    packaging=effective(document,attrs)
    result=dict(packaging)
    result.update(identity=identity or f'product:{product.id}:variant:{variant.id if variant else "simple"}',
        product_id=product.id,variant_id=variant.id if variant else None,
        title=product.title,sku=variant.sku if variant else product.sku,quantity=quantity,
        requires_shipping=defaults(document)[0]['requires_shipping'])
    if 'weight_g' not in result:
        from app.utils.json_utils import apply_variation_modifiers
        resolved=apply_variation_modifiers(document,attrs,log=lambda *a,**k:None) if variant else document
        amount=resolved.get('weight',variant.weight if variant else product.weight)
        result['weight_g']=str(amount) if amount is not None else None
    result['assignment_source']='variant modifier' if attrs and any('packaging' in m for m in document.get('variation_modifiers',{}).values()) else 'resolved collection/product JSON'
    return result


def selected(lines):
    if not isinstance(lines,list) or len(lines)>200:raise ValueError('Choose at most 200 basket lines.')
    result=[]
    for line in lines:
        if not isinstance(line,dict) or type(line.get('product_id')) is not int:raise ValueError('Choose an exact product identity.')
        p=db.session.get(Product,line['product_id'])
        if not p or p.catalogue_status!='active':raise ValueError('Choose an active local product.')
        v=db.session.get(Variation,int(line['variant_id'])) if line.get('variant_id') else None
        if v and v.product_id!=p.id:raise ValueError('Variant does not belong to this product.')
        if p.product_type=='variable' and v is None:raise ValueError('Select the exact variant.')
        data=item(p,v,quantity=line.get('quantity',1))
        # Simulation overrides do not save to authored JSON or assignments.
        if line.get('package_override'):
            data['selected_package_id']=line['package_override']
            data['simulation_override']=True
        result.append(data)
    return result


def simulate(lines,*,allow_estimated=False,order_id=None):
    result=plan(lines,packages(),allow_estimated=allow_estimated,rate_configuration=rates())
    result['checkout_integrated']=False
    result['notice']='Local simulation only. Existing Shopify checkout rates have not changed.'
    record=PackingRecord(id=str(uuid.uuid4()),order_id=order_id,result=dumps(result))
    db.session.add(record);db.session.commit()
    return dict(result,record_id=record.id)


def dependencies(package_id):
    # Bounded indexed catalogue iteration, only on explicit usage inspection.
    # No source tree traversal and no automatic scanner invocation.
    found=[]
    for p in Product.query.filter_by(catalogue_status='active').enable_eagerloads(False).yield_per(100):
        candidates=p.variations if p.product_type=='variable' else [None]
        for v in candidates:
            try:data=item(p,v)
            except ValueError:continue
            if data.get('selected_package_id')==package_id:
                found.append({'product_id':p.id,'variant_id':v.id if v else None,'sku':v.sku if v else p.sku,'title':p.title})
    return found


def order_lines(order):
    """Use retained store/product/variant GIDs, never join by ambiguous SKU/name."""
    from app import shopify_drafts
    result=[];warnings=[]
    for line in order.lines:
        if not line.is_current or not line.unfulfilled_quantity:continue
        identities=ShopifyProductIdentity.query.filter_by(store_id=order.store_id,shopify_product_id=line.product_id).all()
        if len(identities)!=1:
            warnings.append(f'Line {line.remote_id}: no unique retained local product mapping.');continue
        mapping=identities[0];p=db.session.get(Product,mapping.product_id);v=None
        if mapping.state not in {'verified','scoped_verified','attribute_review_required'}:
            warnings.append(f'Line {line.remote_id}: retained product identity requires recovery.');continue
        if p and p.product_type=='variable':
            evidence=shopify_drafts.evidence(mapping)
            entries=evidence.get('variant_maps',{})
            for key,value in entries.items():
                if isinstance(value,dict) and value.get('id')==line.variant_id:
                    v=Variation.query.filter_by(product_id=p.id,source_identity=key).one_or_none()
                    if v and v.sku!=value.get('sku'):v=None
            if v is None:warnings.append(f'Line {line.remote_id}: exact variant mapping unavailable.');continue
        if not p:warnings.append(f'Line {line.remote_id}: local product no longer exists.');continue
        if not v and p.product_type!='variable':
            evidence=shopify_drafts.evidence(mapping)
            remote_variant=evidence.get('variant_id')
            if not remote_variant or remote_variant!=line.variant_id:
                warnings.append(f'Line {line.remote_id}: variant identity differs.');continue
        result.append(item(p,v,quantity=line.unfulfilled_quantity,identity=order.remote_id+'/'+line.remote_id))
    return result,warnings
