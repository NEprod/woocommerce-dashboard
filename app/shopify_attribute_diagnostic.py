"""Fresh read-only assignment/access diagnostic; no app startup or reconciliation."""
import argparse
import json
from pathlib import Path
from flask import Flask
from app import db, shopify_drafts as drafts, shopify_attributes as attributes
from app.models import ShopifyProductIdentity, ShopifyAttributeIdentity
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


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sku',required=True)
    parser.add_argument('--instance',default=str(Path(__file__).resolve().parent.parent/'instance'))
    args=parser.parse_args()
    instance=Path(args.instance).resolve(); database=instance/'site.db'
    if not database.is_file(): parser.error('Existing database is unavailable; no database will be created.')
    app=Flask('shopify_read_only_diagnostic',instance_path=str(instance))
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///file:'+str(database)+'?mode=ro&uri=true',SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)  # deliberately NOT create_app / ensure_database / startup workers
    with app.app_context():
        rows=ShopifyProductIdentity.query.filter_by(sku=args.sku).all()
        if len(rows)!=1 or not rows[0].shopify_product_id: parser.error('One exact retained remote identity is required.')
        try: print(json.dumps(inspect(rows[0]),indent=2))
        except ShopifyError as exc: parser.exit(1,str(exc)+'\n')


if __name__=='__main__': main()
