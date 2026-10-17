"""Fresh read-only assignment/access diagnostic; no app startup or reconciliation."""
import argparse
import json
from pathlib import Path
from flask import Flask
from app import db, shopify_drafts as drafts, shopify_attributes as attributes
from app.models import ShopifyProductIdentity, ShopifyAttributeIdentity, CatalogueOperation, ShopifyProductComparison, Product
from app.shopify_connection import ShopifyAdapter, ShopifyError


def inspect(row, adapter=None):
    adapter = adapter or ShopifyAdapter()
    config = drafts.configuration()
    shop = adapter.test_connection(config)
    if shop['shop_id'] != row.store_id:
        raise ShopifyError('Configured Shopify shop differs from the retained identity.')
    missing=attributes.READ_SCOPES-set(shop['scopes'])
    if missing:
        raise ShopifyError('Missing actual token scopes: '+', '.join(sorted(missing)))
    plan=drafts.evidence(row)['plan']
    product=adapter.read_variable(config,row.shopify_product_id,plan['managed']['namespace']) if drafts.evidence(row).get('kind')=='variable' else adapter.read_simple(config,row.shopify_product_id,plan['managed']['namespace'])
    if not product or product['id']!=row.shopify_product_id or (product.get('metafield') or {}).get('value')!=plan['nonce']:
        raise ShopifyError('Exact retained product/correlation is not verified by this read.')
    values=adapter.attribute_values(config,row.shopify_product_id,attributes.NAMESPACE)
    assigned={v['key']:v for v in values}
    output=[]
    for mapping in ShopifyAttributeIdentity.query.filter_by(store_id=row.store_id).order_by(ShopifyAttributeIdentity.source_key).all():
        data=json.loads(mapping.evidence_json)
        if data.get('kind')!='field': continue
        spec=data['spec']; value=assigned.get(spec['key'])
        if not value: continue
        definition=adapter.attribute_resource(config,'field',spec)
        attributes.valid('field',definition,spec,mapping.source_key,mapping.remote_id)
        if not definition or definition['id']!=mapping.remote_id or value['type']!='list.metaobject_reference':
            raise ShopifyError('Metafield definition/type differs from its retained app-owned identity.')
        ids=json.loads(value['value'])
        if not isinstance(ids,list) or len(ids)>40 or any(not isinstance(i,str) for i in ids):
            raise ShopifyError('Term reference list exceeds the diagnostic bound or is invalid.')
        terms=adapter.attribute_entries(config,list(dict.fromkeys(ids))) if ids else []
        by_id={t['id']:t for t in terms if isinstance(t,dict)}
        checked=[]
        for gid in ids:
            term=by_id.get(gid)
            kept=ShopifyAttributeIdentity.query.filter_by(store_id=row.store_id,remote_id=gid).first()
            trusted=False
            if kept and term:
                evidence=json.loads(kept.evidence_json)
                attributes.valid('term',term,evidence['spec'],kept.source_key,gid)
                if term['definition']['id']!=next(v['value'] for v in spec['validations'] if v['name']=='metaobject_definition_id'):
                    raise ShopifyError('Referenced entry belongs to a different retained metaobject definition.')
                trusted=True
            access=(term or {}).get('definition',{}).get('access',{}).get('storefront')
            publishable=((term or {}).get('capabilities') or {}).get('publishable')
            readable=bool(term and access=='PUBLIC_READ' and (not publishable or publishable.get('status')=='ACTIVE') and definition['access']['storefront']=='PUBLIC_READ')
            checked.append({'id':gid,'retained_identity_matches':trusted,'entry':term,'storefront_readable':readable})
        output.append({'name':spec['name'],'definition_id':definition['id'],'definition_storefront_access':definition['access']['storefront'],
            'actual_product_metafield':value,'actual_referenced_entries':checked})
    return {'shop_id':shop['shop_id'],'product_id':row.shopify_product_id,'sku':row.sku,'api_version':config.api_version,
        'current_token_scopes':shop['scopes'],'checked_at':drafts.now().isoformat(),'read_only':True,'assignments':output,
        'note':'App-owned read-only Admin definitions and pinning are distinct from actual product values. No writes or local evidence changes were made.'}


def catalogue(row,adapter=None):
    """Use canonical readers/comparison in memory, never reconcile/save mappings."""
    adapter=adapter or ShopifyAdapter(); config=drafts.configuration(); shop=adapter.test_connection(config)
    if shop['shop_id']!=row.store_id or 'read_products' not in shop['scopes']:
        raise ShopifyError('Restore the exact store and read_products; no diagnostic writes are permitted.')
    data=drafts.evidence(row); plan=data['plan']
    variable=data.get('kind')=='variable'
    remote=adapter.read_variable(config,row.shopify_product_id,plan['managed']['namespace']) if variable else adapter.read_simple(config,row.shopify_product_id,plan['managed']['namespace'])
    if not remote: raise ShopifyError('Exact product read is absent; no diagnostic reconciliation or identity change is performed.')
    result={'read_only':True,'shop_id':shop['shop_id'],'api_version':config.api_version,'current_token_scopes':shop['scopes'],
        'product':{k:remote.get(k) for k in ('id','title','status','updatedAt','options')},'state':row.state,'verification':row.verification_state,
        'stored_stage':data.get('stage'),'retained_variant_mappings':data.get('variant_maps',{}),
        'variants':remote['variants' if variable else 'simpleVariants'],'media':remote['media'],
        'stored_native':data.get('variant_native') or data.get('native_dimensions'),'stored_api_requests':data.get('api_requests')}
    from app import shopify_variables
    try:
        product=db.session.get(Product,row.product_id)
        local=drafts.local_plan(product) if product else plan
        if variable:
            shopify_variables.compare(local,row,data,remote)
            result['managed_differences']=[f for f in local['fields'] if f['changed']]
            result['blockers']=local['blockers']
        else:
            drafts.simple_identity(row,data,remote)
            from app.shopify_simple_fields import field_rows
            result['managed_differences']=[f for f in field_rows(local['managed'],remote) if f['changed']]
        # Keep diagnostics bounded; no large descriptions or unrelated remote data.
        result['managed_differences']=[{'field':f['field'],'before':str(f['before'])[:300],'after':str(f['after'])[:300]} for f in result['managed_differences']]
    except (ShopifyError,ValueError) as exc: result['comparison_error']=str(exc)
    if (plan.get('native') or {}).get('enabled') or (plan.get('native_policy') or {}).get('enabled'):
        result['native_api_version']='2027-01'
        result['native_read']=adapter.read_variable(config,row.shopify_product_id,plan['managed']['namespace'],native=True) if variable else adapter.read_packed(config,row.shopify_product_id)
    operations=[]
    for op in CatalogueOperation.query.filter_by(operation_type='shopify_draft_publish').order_by(CatalogueOperation.started_at.desc()).limit(100).all():
        scope=json.loads(op.scope)
        if op.id==row.operation_id or scope.get('product_id')==row.product_id:
            operations.append({'id':op.id,'status':op.status,'recovery_state':op.recovery_state,'started_at':str(op.started_at),'finished_at':str(op.finished_at),'error':op.error,'summary':scope.get('operation_summary')})
        if len(operations)>=10: break
    result['operations']=operations
    comparison=ShopifyProductComparison.query.filter_by(store_id=row.store_id,source_identity=row.source_identity).first()
    if comparison:
        saved=json.loads(comparison.summary_json)
        result['stored_comparison']={k:saved.get(k) for k in ('classification','scope','full_compared_at','revision_checked_at','related_checked_at','blockers','refresh_failure','display_changes')}
    result['request_trace']=getattr(adapter,'request_trace',[])
    return result


def verification(row):
    """Local read-only evidence index. Never initializes workers or calls Shopify."""
    from app.catalogue_images import product_image_diagnostics
    from app.utils.operation_control import sanitize_operation_error
    product = db.session.get(Product, row.product_id)
    data = drafts.evidence(row)
    result = {'read_only': True, 'database_writes': 0, 'api_requests': 0,
        'sku': row.sku, 'state': row.state, 'verification': row.verification_state,
        'package_receipts': [{'state': p.get('state'), 'independently_verified': p.get('independently_verified')}
            for p in data.get('package_assignments', {}).values()]}
    try:
        images = product_image_diagnostics(product) if product else []
        if product and product.product_type == 'variable':
            from app.shopify_variables import gallery_diagnostics
            images = gallery_diagnostics(product, images)
        result['gallery'] = {'count': len(images), 'unresolved': sum(not r.get('source_exists') or not r.get('source_reference') for r in images), 'limit': 20}
    except (ValueError, OSError, RuntimeError):
        result['gallery'] = {'unavailable': True, 'action': 'Check catalogue mount/source layout.'}
    comparison = ShopifyProductComparison.query.filter_by(store_id=row.store_id, source_identity=row.source_identity).first()
    if comparison:
        saved = json.loads(comparison.summary_json)
        publication, native = saved.get('publication') or {}, saved.get('native') or {}
        result['comparison'] = {k: saved.get(k) for k in ('classification', 'read_complete', 'scope',
            'full_compared_at', 'related_checked_at', 'revision_checked_at', 'missing_evidence',
            'package_assignments_unverified', 'dimension_metadata_policy')}
        result['comparison'].update(
            failure=sanitize_operation_error((saved.get('refresh_failure') or {}).get('message', ''))[:500],
            blockers=[sanitize_operation_error(v)[:500] for v in saved.get('blockers', [])[:20]],
            publication={'read_complete': publication.get('read_complete'), 'reason': sanitize_operation_error(publication.get('reason', ''))[:500]},
            native={'enabled': native.get('enabled'), 'classification': native.get('classification'),
                'reason': sanitize_operation_error(native.get('reason', ''))[:500]})
        targets = native.get('variants') or {'product': native.get('plan', native)}
        result['comparison']['native']['targets'] = [
            {'identity': key, 'sku': target.get('sku', row.sku), 'inventory_item_id': target.get('inventory_id'),
                'unconfigured': target.get('unconfigured'), 'classification': target.get('classification'),
                'reason': sanitize_operation_error(target.get('reason', ''))[:350]}
            for key, target in targets.items()]
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sku',required=True)
    parser.add_argument('--instance',default=str(Path(__file__).resolve().parent.parent/'instance'))
    parser.add_argument('--catalogue',action='store_true',help='Inspect product/variant/InventoryItem readback and operation receipts, without attribute reads/writes')
    parser.add_argument('--verification',action='store_true',help='Inspect only local verification components and gallery counts; no Shopify API requests or customer/field values')
    args=parser.parse_args()
    instance=Path(args.instance).resolve(); database=instance/'site.db'
    if not database.is_file(): parser.error('Existing database is unavailable; no database will be created.')
    app=Flask('shopify_read_only_diagnostic',instance_path=str(instance))
    from config import Config
    app.config.from_object(Config)
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///file:'+str(database)+'?mode=ro&uri=true',SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)  # deliberately NOT create_app / ensure_database / startup workers
    with app.app_context():
        rows=ShopifyProductIdentity.query.filter_by(sku=args.sku).all()
        if len(rows)!=1 or not rows[0].shopify_product_id: parser.error('One exact retained remote identity is required.')
        try: print(json.dumps(verification(rows[0]) if args.verification else catalogue(rows[0]) if args.catalogue else inspect(rows[0]),indent=2,default=str))
        except ShopifyError as exc: parser.exit(1,str(exc)+'\n')


if __name__=='__main__': main()
