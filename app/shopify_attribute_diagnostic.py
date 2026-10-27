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
        result['gallery'] = {'count': len(images), 'unresolved': sum(not r.get('source_exists') or not r.get('source_reference') for r in images), 'limit': drafts.MAX_IMAGES}
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


def variant_matrix(row, adapter=None):
    """Fresh bounded variant evidence; no reconcile, receipt or checkpoint writes."""
    from app import shopify_variables as variables, shopify_dimension_metadata as dimensions
    from app import shopify_simple_fields as fields
    from app.shopify_native_dimensions import equal as same_packed
    from app.packaging_contract import effective, packed_cm, finished_mm
    from app import package_links
    adapter = adapter or ShopifyAdapter()
    config = drafts.configuration()
    shop = adapter.test_connection(config)
    if shop['shop_id'] != row.store_id or 'read_products' not in shop['scopes']:
        raise ShopifyError('Exact retained shop and read_products are required; no diagnostic writes.')
    data = drafts.evidence(row)
    product = db.session.get(Product, row.product_id)
    if not product or product.product_type != 'variable':
        raise ShopifyError('Choose one existing Variable Collection or Single Variable product SKU.')
    # This diagnostic attests only variant metadata, not full gallery freshness.
    # Do not require a publishable parent/gallery merely to inspect its fields.
    local = {'variants': variables.projection(product),
             'managed': {'namespace': fields.namespace(), 'product': {}}}
    remote = adapter.read_variable(config, row.shopify_product_id, local['managed']['namespace'])
    nodes = variables.remote_identity(row, data, remote, native_only=True)
    by_id = {v['id']: v for v in nodes}
    definitions = dimensions.read(local, config, adapter)
    native = None
    if any(v['native']['enabled'] and v['native'].get('desired') for v in local['variants']):
        observed = adapter.read_variable(config, row.shopify_product_id, local['managed']['namespace'], native=True)
        native = {v['id']: v for v in variables.remote_identity(row, data, observed, native_only=True)}
    authored = attributes.product_document(product)
    rows = []
    for variant in local['variants']:
        mapping = data.get('variant_maps', {}).get(variant['identity'], {})
        current = by_id.get(mapping.get('id'))
        options = {o['name']: o['value'] for o in variant['options']}
        if (not current or current['sku'] != variant['sku'] or
                current['inventoryItem']['id'] != mapping.get('inventory_id') or
                {o['name']: o['value'] for o in current['selectedOptions']} != options):
            raise ShopifyError('Retained variant/SKU/options/InventoryItem mapping differs; no guessed matrix identity.')
        if native is not None and ((native.get(current['id']) or {}).get('inventoryItem') or {}).get('id') != mapping['inventory_id']:
            raise ShopifyError('Native read maps to a different InventoryItem; refresh evidence, never guess an association.')
        packing = effective(authored, options)
        selected = packing.get('selected_package_id')
        try:
            link = package_links.find(selected, config, shop['shop_id']) if selected else None
        except ValueError:
            raise ShopifyError('Central package link conflicts; inspect local package settings.') from None
        proof = data.get('package_assignments', {}).get(variant['identity'], {})
        matches = bool(link and proof.get('inventory_id') == mapping['inventory_id'] and
                       proof.get('shipping_package_id') == link.remote_id)
        actual_packed = (((native or {}).get(current['id'], {}).get('inventoryItem') or {}).get('measurement') or {}).get('packedDimensions')
        desired_packed = variant['native'].get('desired')
        values = []
        comparisons = fields.field_rows({'product': {'metafields': variant['managed'].get('metafields', [])},
            'variant': variant['managed']}, dict(current, simpleVariants={'nodes': [current], 'pageInfo': {'hasNextPage': False}}))
        for field in variant['managed'].get('metafields', []):
            axis = field['key'].removeprefix('product_')
            definition = definitions.get('PRODUCTVARIANT.' + field['key'])
            difference = next(v for v in comparisons if v['field'] == 'dimensions.' + axis)
            values.append({'namespace': field['namespace'], 'key': field['key'], 'type': field['type'],
                'desired': json.loads(field['value']), 'remote': current.get(axis),
                'value_state': 'changed' if difference['changed'] else 'matching',
                'definition_id': definition['id'] if definition else None,
                'storefront_access': (definition or {}).get('access', {}).get('storefront'),
                'definition_action': 'Create through Review & Sync' if not definition else
                    'Update storefront access through Review & Sync' if definition['access']['storefront'] != 'PUBLIC_READ' else 'None'})
        rows.append({'sku': variant['sku'], 'options': options, 'variant_id': current['id'],
            'inventory_item_id': mapping['inventory_id'], 'finished_owner': 'PRODUCTVARIANT',
            'effective_finished_mm': finished_mm(authored, options), 'finished_metafields': values,
            'effective_authored_packed_cm': packed_cm(packing), 'desired_native_packed': desired_packed,
            'remote_native_packed': actual_packed,
            'packed_state': 'unconfigured — no finished fallback published' if variant['native'].get('unconfigured') else
                'incomplete authored size — ' + variant['native'].get('reason', '') if not desired_packed else
                'not read — opt-in capability disabled' if native is None else
                'matching' if same_packed(actual_packed, desired_packed) else 'different or unavailable',
            'selected_local_package_id': selected, 'linked_shopify_package_id': link.remote_id if link else None,
            'package_receipt_state': proof.get('state', 'not submitted'), 'receipt_matches_current_target': matches,
            'assignment_independent_readback': 'unsupported', 'merchant_verification': 'not recorded'})
    return {'read_only': True, 'database_writes': 0, 'checked_at': drafts.now().isoformat(), 'api_version': config.api_version,
        'native_api_version': '2027-01' if native is not None else None, 'sku': row.sku,
        'shop_id': row.store_id, 'product_id': row.shopify_product_id, 'variants': rows,
        'note': 'Live API evidence only if this command ran against the configured merchant store. No theme binding or checkout verification is implied.'}


def navigation_matrix(row, adapter=None):
    """Reuse existing attribute/collection review readers, without applying them."""
    adapter = adapter or ShopifyAdapter()
    config = drafts.configuration()
    shop = adapter.test_connection(config)
    if shop['shop_id'] != row.store_id or 'read_products' not in shop['scopes']:
        raise ShopifyError('Exact retained shop and read_products are required.')
    product = db.session.get(Product, row.product_id)
    if not product:
        raise ShopifyError('Retained local product is unavailable.')
    remote = adapter.read_variable(config, row.shopify_product_id, 'tlc_dashboard') if product.product_type == 'variable' else adapter.read_simple(config, row.shopify_product_id, 'tlc_dashboard')
    if not remote or remote['id'] != row.shopify_product_id or (remote.get('metafield') or {}).get('value') != drafts.evidence(row)['plan']['nonce']:
        raise ShopifyError('Product correlation does not match the retained identity.')
    rows = []
    for attr in attributes.preview(product, row.store_id, row.shopify_product_id, config, shop, adapter):
        definition = next(r for r in attr['resources'] if r['kind'] == 'field')
        rows.append({'authored_field': 'attributes.' + attr['name'], 'registry_key': attr['key'],
            'name': attr['name'], 'terms': [t['name'] for t in attr['terms']], 'owner': 'PRODUCT',
            'type': 'list.metaobject_reference', 'namespace': (attr.get('before') or {}).get('namespace') or attributes.NAMESPACE,
            'key': attr['field_key'], 'current_value': attr['before'],
            'storefront_access': definition['before_access'] if 'before_access' in definition else None,
            'authored_public_policy': attr['public'], 'proposed_action': attr['action'],
            'resource_actions': [{'kind': r['kind'], 'action': r['action'], 'settings_changes': r.get('settings_changes', [])} for r in attr['resources']],
            'theme_access': 'Liquid .value list; compatible dynamic source depends on block and referenced name field',
            'inheritance': 'Existing effective shared collection / sparse product document; no duplicate authoring'})
    document = attributes.product_document(product)
    from app.shopify_taxonomy import assignments
    groups, blockers = assignments(product)
    return {'read_only': True, 'database_writes': 0, 'checked_at': drafts.now().isoformat(), 'product_id': row.shopify_product_id,
        'informational_attributes': rows,
        'native_sources': {key: document.get(key) for key in ('categories', 'primary_category', 'storefront_collections', 'tags', 'shopify_product_type')},
        'resolved_collection_destinations': [{'key': g['key'], 'name': g['name']} for g in groups],
        'classification_blockers': blockers,
        'note': 'Categories/ranges/browse destinations use native Shopify collections. Variation drivers use native options; no duplicate navigation metafields are proposed.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sku',required=True)
    parser.add_argument('--instance',default=str(Path(__file__).resolve().parent.parent/'instance'))
    parser.add_argument('--catalogue',action='store_true',help='Inspect product/variant/InventoryItem readback and operation receipts, without attribute reads/writes')
    parser.add_argument('--verification',action='store_true',help='Inspect only local verification components and gallery counts; no Shopify API requests or customer/field values')
    parser.add_argument('--variant-matrix', action='store_true', help='Read exact per-variant dimensions, definitions, native sizes and assignment receipts; no writes')
    parser.add_argument('--navigation-matrix', action='store_true', help='Read existing product classification destinations and public attribute definitions; no writes')
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
        try: print(json.dumps(verification(rows[0]) if args.verification else variant_matrix(rows[0]) if args.variant_matrix else navigation_matrix(rows[0]) if args.navigation_matrix else catalogue(rows[0]) if args.catalogue else inspect(rows[0]),indent=2,default=str))
        except ShopifyError as exc: parser.exit(1,str(exc)+'\n')


if __name__=='__main__': main()
