"""Read-only capability probe through the existing bounded Shopify transport.

Package mutations without a public package readback contract are deliberately
blocked. A write-only shippingPackageId cannot prove an assignment was verified.
"""
from datetime import datetime
import json
from app import db
from app.packaging_models import PackagingEvidence
from app.shopify_connection import ShopifyAdapter, ShopifyError

from app.packaging_queries import CAPABILITY


def assess(raw,scopes):
    def names(key,field='fields'):
        value=raw.get(key)
        if value is None:return set()
        if not isinstance(value,dict) or not isinstance(value.get(field),list):raise ShopifyError('Package capability evidence is incomplete.')
        return {r['name'] for r in value[field] if isinstance(r,dict) and isinstance(r.get('name'),str)}
    query,mutations=names('queryRoot'),names('mutations')
    read=names('measurement');write=names('measurementInput','inputFields')
    shop=raw.get('shop',{})
    if not shop.get('id'):raise ShopifyError('Store capability evidence is incomplete.')
    return {'store_id':shop['id'],'api_version':'2026-10','scopes':sorted(scopes),
        'package_list_schema':'shippingPackages' in query,'package_read_schema':'shippingPackage' in query,
        'package_create_schema':'shippingPackageCreate' in mutations,
        'package_update_schema':'shippingPackageUpdate' in mutations,
        'package_default_schema':'shippingPackageMakeDefault' in mutations,
        'assignment_write_schema':'shippingPackageId' in write,'assignment_read_schema':'shippingPackageId' in read,
        'missing_scopes':[s for s in ('read_shipping','write_shipping') if s not in scopes],
        'package_sync_enabled':False,
        'limitation':'No tested public package discovery/readback contract. Package/default/assignment writes stay blocked; use Shopify Admin and preserve the default. A schema mutation alone is not verified synchronisation.'}


def refresh(adapter=None):
    from app.shopify_drafts import configuration
    config=configuration();adapter=adapter or ShopifyAdapter()
    shop=adapter.test_connection(config)
    raw=adapter._read(config,CAPABILITY)
    if raw.get('shop',{}).get('id')!=shop['shop_id']:raise ShopifyError('Store identity differs; no package links changed.')
    result=assess(raw,shop['scopes'])
    key='shopify:'+shop['shop_id']
    row=db.session.get(PackagingEvidence,key) or PackagingEvidence(key=key)
    row.value=json.dumps(result,sort_keys=True);row.checked_at=datetime.utcnow()
    db.session.add(row);db.session.commit()
    return result


def current():
    row=PackagingEvidence.query.filter(PackagingEvidence.key.like('shopify:%')).order_by(PackagingEvidence.checked_at.desc()).first()
    if not row:return {
        'package_sync_enabled':False,'limitation':'Shopify package reads/creation and assignment readback have not been verified. No remote writes enabled.'}
    return dict(json.loads(row.value),checked_at=row.checked_at.isoformat()+'Z')
