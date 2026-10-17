"""Read-only capability probe through the existing bounded Shopify transport.

Package mutations without a public package readback contract are deliberately
blocked. A write-only shippingPackageId cannot prove an assignment was verified.
"""
from datetime import datetime
import json
import re
from app import db
from app.packaging_models import PackagingEvidence
from app.shopify_connection import ShopifyAdapter, ShopifyError

from app.packaging_queries import CAPABILITY


def assess(raw,scopes):
    def names(key,field='fields'):
        value=raw.get(key)
        if value is None:return set()
        if not isinstance(value,dict) or not isinstance(value.get(field),list):raise ShopifyError('Package capability evidence is incomplete.')
        if any(not isinstance(r,dict) or not isinstance(r.get('name'),str) for r in value[field]):raise ShopifyError('Package schema evidence is malformed, not proof of an unsupported operation.')
        return {r['name'] for r in value[field] if isinstance(r,dict) and isinstance(r.get('name'),str)}
    query,mutations=names('queryRoot'),names('mutations')
    read=names('measurement');write=names('measurementInput','inputFields')
    shop=raw.get('shop',{})
    if not re.fullmatch(r'gid://shopify/Shop/[0-9]+',shop.get('id','')):raise ShopifyError('Store capability evidence is incomplete.')
    return {'store_id':shop['id'],'api_version':'2026-10','scopes':sorted(scopes),
        'package_list_schema':'shippingPackages' in query,'package_read_schema':'shippingPackage' in query,
        'shop_package_list_schema':'shippingPackages' in names('shopType'),
        'package_create_schema':'shippingPackageCreate' in mutations,
        'package_update_schema':'shippingPackageUpdate' in mutations,
        'package_default_schema':'shippingPackageMakeDefault' in mutations,
        'assignment_write_schema':'shippingPackageId' in write,'assignment_read_schema':'shippingPackageId' in read,
        'package_fields':sorted(names('package')),
        'update_input_fields':sorted(names('packageInput','inputFields')),
        'package_types':sorted(names('packageTypes','enumValues')),
        'update_payload_fields':sorted(names('updatePayload')),
        'missing_scopes':[s for s in ('read_shipping','write_shipping') if s not in scopes],
        'package_sync_enabled':False,
        'limitation':'No verified saved-package listing/readback adapter. A documented update mutation alone cannot prove identity or success. Create this package in Shopify Admin, then reconcile using merchant-supplied measurements. Remote package/default/assignment writes remain blocked.'}


def refresh(adapter=None):
    from app.shopify_drafts import configuration
    config=configuration();adapter=adapter or ShopifyAdapter()
    if config.api_version!='2026-10':raise ShopifyError('Package discovery uses the established 2026-10 API only; no global version change is made.')
    shop=adapter.test_connection(config)
    raw=adapter._read(config,CAPABILITY)
    if raw.get('shop',{}).get('id')!=shop['shop_id']:raise ShopifyError('Store identity differs; no package links changed.')
    result=assess(raw,shop['scopes'])
    domain=(raw.get('shop') or {}).get('myshopifyDomain')
    if isinstance(domain,str) and re.fullmatch(r'[a-z0-9][a-z0-9-]*\.myshopify\.com',domain):
        result['admin_url']='https://'+domain+'/admin/settings/shipping'
    result['config']=config.fingerprint()
    key='shopify:'+shop['shop_id']
    row=db.session.get(PackagingEvidence,key) or PackagingEvidence(key=key)
    row.value=json.dumps(result,sort_keys=True);row.checked_at=datetime.utcnow()
    db.session.add(row);db.session.commit()
    return result


def current():
    row=PackagingEvidence.query.filter(PackagingEvidence.key.like('shopify:%')).order_by(PackagingEvidence.checked_at.desc()).first()
    if not row:return {
        'package_sync_enabled':False,'limitation':'Shopify package reads/creation and assignment readback have not been verified. No remote writes enabled.'}
    from app.shopify_drafts import configuration
    value=json.loads(row.value)
    try: matches=value.get('config')==configuration().fingerprint()
    except (ShopifyError,ValueError): matches=False
    if not matches:return {'package_sync_enabled':False,'limitation':'No current-store capability evidence. Refresh Shopify capabilities; no remote write is enabled.'}
    return dict(value,checked_at=row.checked_at.isoformat()+'Z')
