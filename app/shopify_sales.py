"""Reviewed price authority and bounded transitions in the existing task worker."""
import copy
import json
import uuid
from datetime import timedelta

from app import db
from app.models import Product, ShopifyProductIdentity, ShopifySaleSchedule, CatalogueOperation
from app import shopify_drafts as drafts
from app.shopify_connection import ShopifyAdapter, ShopifyError
from app.sale_pricing import schedule, money, variant_sale
from app.taxonomy_assignments import product_document
from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation, CatalogueOperationActive

KEY='shopify_sales'


def summary(product):
    from app.sale_pricing import project
    try:
        authored=product_document(product)
        pricing=project(authored,authored.get('price',product.regular_price),product.sale_price)
        state=pricing['state']
        message='Authored effective price; automatic changes require separate authorisation.'
    except (ValueError,ShopifyError): state,message='needs_attention','Resolve authored sale metadata before review.'
    row=ShopifySaleSchedule.query.filter_by(source_identity=product.source_relpath).first()
    if row:
        state=row.state
        message=row.error or 'Retained per-product approval; unrelated or revised source details must be reviewed again.'
        if state in {'authorised','active'}:
            try:
                identity=db.session.get(ShopifyProductIdentity,row.identity_id)
                authority=json.loads(row.evidence_json)
                if not identity or context(product,identity)!=authority['source'] or drafts.configuration().fingerprint()!=authority['config']:
                    state,message='needs_attention','Source/defaults/identity changed. Existing approval will not execute; review and authorise again.'
            except (ValueError,ShopifyError,KeyError):
                state,message='needs_attention','Sale authority cannot be validated locally; review before execution.'
    return {'state':state,'message':message,'next_due':row.next_due.isoformat()+'Z' if row and row.next_due else None,
            'id':row.id if row else None}


def context(product,row):
    from app.catalogue_fields import shipping_details
    from app.shopify_taxonomy import fingerprint
    authored=product_document(product)
    data=drafts.evidence(row)
    from app.product_defaults import effective
    return drafts.digest({'authored':authored,'source':product.source_relpath,'sku':product.sku,
        'store':row.store_id,'remote':row.shopify_product_id,'status':product.catalogue_status,
        'defaults':[shipping_details(authored,product),effective(authored)[0]],
        'taxonomy':fingerprint(product),
        'maps':data.get('variant_maps') or [data.get('variant_id'),data.get('inventory_item_id')],
        'variations':[(v.source_identity,v.sku,str(v.regular_price),str(v.sale_price),str(v.weight),
            str(v.length),str(v.width),str(v.height),v.catalogue_status,
            sorted((a.name,a.value) for a in v.attributes)) for v in sorted(product.variations,key=lambda v:v.id)]})


def amounts(product,row):
    authored=product_document(product)
    data=drafts.evidence(row)
    result=[]
    if product.product_type=='variable':
        for variant in sorted(product.variations,key=lambda v:(v.menu_order or 0,v.id)):
            if variant.catalogue_status!='active': continue
            mapped=data.get('variant_maps',{}).get(variant.source_identity)
            if not mapped: raise ShopifyError('Every sale variant needs its retained ProductVariant/InventoryItem identity.')
            sale=variant_sale(authored,{a.name:a.value for a in variant.attributes},variant.sale_price)
            result.append({'id':mapped['id'],'inventory_id':mapped['inventory_id'],'sku':variant.sku,
                'identity':variant.source_identity,'regular':money(variant.regular_price),
                'sale':money(sale) if sale not in (None,'') else None})
    else:
        sale=authored.get('sale_price',product.sale_price)
        result=[{'id':data.get('variant_id'),'inventory_id':data.get('inventory_item_id'),'sku':product.sku,
            'identity':product.source_relpath,'regular':money(authored.get('price',product.regular_price)),
            'sale':money(sale) if sale not in (None,'') else None}]
    from decimal import Decimal
    if not 1<=len(result)<=32 or any(not r['id'] or not r['inventory_id'] or r['sale'] is not None and Decimal(r['sale'])>=Decimal(r['regular']) for r in result):
        raise ShopifyError('Sale amounts/retained identities are invalid; no authorisation or price write.')
    if not any(r['sale'] is not None for r in result): raise ShopifyError('Author an explicit sale amount before authorising transitions.')
    return result


def price_field(field):
    return field in {'price','compareAtPrice'} or field.startswith('variants.') and field.rsplit('.',1)[-1] in {'price','compareAtPrice'}


def eligible(plan):
    from app.shopify_publication import has_metadata_changes
    if plan['blockers'] or plan.get('attribute_diagnostics') or (plan.get('publication') or {}).get('pending'):
        raise ShopifyError('Sale held: publication/attributes or other required verification is incomplete.')
    if any(f['changed'] and not price_field(f['field']) for f in plan['fields']) or any(
            c['action'] in {'Join','Create & Join'} for c in plan.get('collections',[])) or any(v.get('action')=='Create' for v in plan.get('variants',[])):
        raise ShopifyError('Sale held: unrelated managed changes must be reviewed/verified first. Only approved price differences may proceed.')


def review(product,adapter=None):
    adapter=adapter or ShopifyAdapter()
    config=drafts.configuration()
    shop=adapter.test_connection(config)
    row=drafts.matching_identity(shop['shop_id'],product)
    from app.shopify_notifications import complete
    if not row or not complete(row,drafts.evidence(row)):
        raise ShopifyError('Sale authorisation requires a fully verified retained product and variant identities.')
    if 'write_products' not in shop['scopes']: raise ShopifyError('Missing write_products for approved variant prices.')
    start,end=schedule(product_document(product))
    plan,_=drafts.preview(product,adapter)
    eligible(plan)
    value={'version':1,'product_id':product.id,'identity_id':row.id,'store_id':row.store_id,
        'remote_id':row.shopify_product_id,'sku':product.sku,'title':product.title,
        'source':context(product,row),'config':config.fingerprint(),'variants':amounts(product,row),
        'images':[{k:i[k] for k in ('source','sha256','position','alt')} for i in plan['images']],
        'start':start.isoformat() if start else None,'end':end.isoformat() if end else None,
        'remote_digest':plan.get('remote_digest'),'identity':drafts.digest(drafts.evidence(row))}
    value['digest']=drafts.digest(value)
    token=drafts.serializer().dumps({'mode':'sale_authorise','product_id':product.id,'digest':value['digest']})
    return value,token


def authorise(product,token,acknowledged,adapter=None):
    if not acknowledged: raise ShopifyError('Explicit future-price authorisation is required; saving JSON is not approval.')
    from itsdangerous import BadSignature
    try: signed=drafts.serializer().loads(token,max_age=900)
    except BadSignature: raise ShopifyError('Sale review is invalid or expired; review again.') from None
    if signed.get('product_id')!=product.id or signed.get('mode')!='sale_authorise': raise ShopifyError('Sale review belongs to another product/workflow.')
    lease=acquire_catalogue_operation('shopify_draft_publish',{'product_id':product.id,'sku':product.sku,'mode':'read_only_sale_authorisation'})
    try:
        current,_=review(product,adapter)
        if signed['digest']!=current['digest']: raise ShopifyError('Sale details, identities or remote state changed; no authorisation saved.')
        row=ShopifySaleSchedule.query.filter_by(store_id=current['store_id'],source_identity=product.source_relpath).first()
        if row and row.state in {'activating','ending','recovery_required'}: raise ShopifyError('Reconcile the existing price transition before replacing approval.')
        stamp=drafts.now()
        if not row:
            row=ShopifySaleSchedule(id=uuid.uuid4().hex,product_id=product.id,store_id=current['store_id'],source_identity=product.source_relpath)
            db.session.add(row)
        row.identity_id=current['identity_id'];row.generation=uuid.uuid4().hex
        row.state='authorised';row.error=None;row.operation_id=lease.id
        from datetime import datetime
        row.start_at=datetime.fromisoformat(current['start']) if current['start'] else None
        row.end_at=datetime.fromisoformat(current['end']) if current['end'] else None
        row.next_due=row.start_at if row.start_at and row.start_at>stamp else stamp
        row.authorised_at=row.updated_at=stamp
        row.evidence_json=json.dumps(current,sort_keys=True)
        db.session.commit()
        finish_catalogue_operation(lease.id,status='succeeded',operation_summary={'authorisation':row.id,'generation':row.generation,
            'reviewed':current,'remote_writes':0})
        return row
    except Exception:
        db.session.rollback()
        finish_catalogue_operation(lease.id,status='failed',error='Sale authorisation was not saved; no Shopify price write occurred.')
        raise


def pause(row):
    if row.state in {'activating','ending','recovery_required'}: raise ShopifyError('Reconcile the uncertain transition first; pausing cannot erase write evidence.')
    row.state='paused';row.next_due=None;row.updated_at=drafts.now()
    db.session.commit()


def read_variants(row,data,adapter,config):
    if data.get('kind')=='variable':
        from app.shopify_variables import remote_identity
        remote=adapter.read_variable(config,row.shopify_product_id,data['plan']['managed']['namespace'])
        nodes=remote_identity(row,data,remote)
    else:
        remote=adapter.read_simple(config,row.shopify_product_id,data['plan']['managed']['namespace'])
        nodes=[drafts.simple_identity(row,data,remote)]
    return remote,nodes


def matching(nodes,inputs):
    by_id={v['id']:v for v in nodes}
    for value in inputs:
        observed=by_id.get(value['id'])
        if not observed or money(observed.get('price'))!=value['price']: return False
        before=observed.get('compareAtPrice')
        if (money(before) if before is not None else None)!=value['compareAtPrice']: return False
    return True


def settle(schedule_row,row,data,adapter):
    proof=data['sale_transition'];config=drafts.configuration()
    shop=adapter.test_connection(config)
    if shop['shop_id']!=row.store_id or config.fingerprint()!=proof['config']:
        raise ShopifyError('Restore the approved Shopify store/configuration before read-only sale recovery.')
    remote,nodes=read_variants(row,data,adapter,config)
    if not matching(nodes,proof['inputs']): raise ShopifyError('Approved price transition is incomplete/different. Recovery Required; no automatic replay.')
    # Bind the independently observed prices before the full verifier's final
    # identity/comparison commit. The identity remains updating/uncertain until
    # all other required domains pass; do not mutate it after indexing equality.
    proof.update(state='verified',checked_at=drafts.now().isoformat())
    # All existing verification domains are read back, not borrowed from a price-only response.
    if data.get('kind')=='variable':
        from app.shopify_variables import verify
        verify(row,data,config,adapter)
    else:
        drafts.apply_simple_readback(row,data,remote,adapter)
    # Existing full verifiers above record independently read fields/memberships;
    # never manufacture equality by resetting every reviewed difference.
    schedule_row.state='active' if proof['phase']=='activate' else 'completed'
    schedule_row.next_due=schedule_row.end_at if proof['phase']=='activate' else None
    schedule_row.updated_at=drafts.now();schedule_row.error=None
    db.session.commit()
    return row


def reconcile_identity(row,adapter=None):
    data=drafts.evidence(row)
    schedule_row=db.session.get(ShopifySaleSchedule,data['sale_transition']['schedule_id'])
    if not schedule_row or schedule_row.generation!=data['sale_transition']['generation']:
        raise ShopifyError('Original sale authority is missing; retain recovery evidence.')
    return settle(schedule_row,row,data,adapter or ShopifyAdapter())


def transition(schedule_row,adapter=None,*,run_id=None,owner=None):
    """One due authorised product; no due row ever creates an API request."""
    stamp=drafts.now()
    if schedule_row.state not in {'authorised','active','activating','ending'} or not schedule_row.next_due or schedule_row.next_due>stamp: return 'not_due'
    product=db.session.get(Product,schedule_row.product_id)
    row=db.session.get(ShopifyProductIdentity,schedule_row.identity_id)
    authority=json.loads(schedule_row.evidence_json)
    if not product or not row or row.store_id!=schedule_row.store_id or row.source_identity!=schedule_row.source_identity:
        raise ShopifyError('Sale source/retained identity is unavailable; no product recreation.')
    if context(product,row)!=authority['source'] or drafts.configuration().fingerprint()!=authority['config']:
        raise ShopifyError('Sale approval invalidated by catalogue/default/taxonomy/identity changes. Review and authorise again.')
    adapter=adapter or ShopifyAdapter()
    if schedule_row.state in {'activating','ending'}:
        # Interrupted accepted mutation: READ only. A nonmatching result blocks;
        # never repeat the write, even after worker/server restart.
        original=db.session.get(CatalogueOperation,schedule_row.operation_id)
        if original and original.status in {'running','pending'}: raise ShopifyError('Original sale operation is still active; reconcile after interruption recovery.')
        reconcile_identity(row,adapter)
        from app.shopify_notifications import settle as settle_notice
        settle_notice(row)
        return 'reconciled'
    from app.shopify_notifications import complete
    if not complete(row,drafts.evidence(row)): raise ShopifyError('Sale held: product is not fully verified or has an unresolved write.')
    phase='end' if schedule_row.end_at and stamp>=schedule_row.end_at else 'activate'
    transition_id=drafts.digest([schedule_row.id,schedule_row.generation,phase])[:32]
    existing=db.session.get(CatalogueOperation,transition_id)
    if existing:
        raise ShopifyError('This transition already has a durable receipt; inspect/reconcile it instead of replaying.')
    # Capacity/interactive priority can defer this first read without consuming
    # the deterministic write receipt or changing approved authority.
    shop=adapter.test_connection(drafts.configuration())
    if shop['shop_id']!=row.store_id or 'write_products' not in shop['scopes']:
        raise ShopifyError('Missing write_products or wrong approved store.')
    lease=acquire_catalogue_operation('shopify_draft_publish',{'product_id':product.id,'sku':product.sku,'mode':'sale_'+phase,
        'scheduled_run':run_id,'scheduled_owner':owner,'authorisation':schedule_row.id,'generation':schedule_row.generation},transition_id=transition_id)
    attempted=False
    try:
        plan,_=drafts.preview(product,adapter)
        eligible(plan)
        if [{k:i[k] for k in ('source','sha256','position','alt')} for i in plan['images']]!=authority['images']:
            raise ShopifyError('Sale image/source revision changed; review/authorise again.')
        inputs=[{'id':v['id'],'price':v['sale'] if phase=='activate' and v['sale'] is not None else v['regular'],
                 'compareAtPrice':v['regular'] if phase=='activate' and v['sale'] is not None else None} for v in authority['variants']]
        data=copy.deepcopy(drafts.evidence(row));remote,nodes=read_variants(row,data,adapter,drafts.configuration())
        # Remote differences must be exactly the approved previous/target price;
        # an unrelated manual price edit is held, never overwritten silently.
        allowed={v['id']:{(v['regular'],None),(v['sale'],v['regular'])} for v in authority['variants']}
        for v in nodes:
            if v['id'] in allowed and (money(v['price']),money(v['compareAtPrice']) if v.get('compareAtPrice') is not None else None) not in allowed[v['id']]:
                raise ShopifyError('Remote price changed outside the approved regular/sale pair; manual review required.')
        changed=not matching(nodes,inputs)
        data['plan']=plan
        data['sale_transition']={'state':'pending','schedule_id':schedule_row.id,'generation':schedule_row.generation,
            'phase':phase,'operation_id':lease.id,'config':authority['config'],'inputs':inputs,'write_attempted':changed}
        data['stage']='sale_transition'
        row.operation_id=lease.id
        schedule_row.operation_id=lease.id;schedule_row.state='activating' if phase=='activate' else 'ending'
        row.verification_state='unverified'
        drafts.save(row,data,'updating')  # commits schedule+identity before mutation
        if changed:
            attempted=True
            if data.get('kind')=='variable': result=adapter.update_variants(drafts.configuration(),row.shopify_product_id,inputs)
            else: result=adapter.update_simple_variant(drafts.configuration(),row.shopify_product_id,inputs[0])
            drafts.payload_result(result,'productVariantsBulkUpdate')
        settle(schedule_row,row,data,adapter)
        finish_catalogue_operation(lease.id,status='succeeded',products_attempted=1,products_succeeded=1,
            operation_summary={'stage':'sale_'+phase,'write_attempted':attempted,'variants':len(inputs),'sale_status':schedule_row.state})
        return schedule_row.state
    except Exception as exc:
        db.session.rollback()
        message=str(exc) if isinstance(exc,ShopifyError) else 'Sale transition could not be verified. Inspect retained evidence before any further write.'
        if attempted:
            data=drafts.evidence(row);data['sale_transition']['state']='uncertain';data['error']=message
            schedule_row.state='recovery_required'
            drafts.save(row,data,'uncertain')
        else: schedule_row.state='needs_attention'
        schedule_row.error=message[:1000];schedule_row.updated_at=drafts.now();db.session.commit()
        finish_catalogue_operation(lease.id,status='partial' if attempted else 'failed',products_attempted=1,
            recovery_state='review_required' if attempted else 'none',error=message,
            operation_summary={'write_attempted':attempted,'stage':'sale_'+phase})
        return schedule_row.state
