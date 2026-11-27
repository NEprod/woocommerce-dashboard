"""Readable product URLs without changing retained identities or recovery nonces."""
import re
import unicodedata
from app.shopify_connection import ShopifyError

POLICY_VERSION = 1


def legacy(value):
    return isinstance(value,str) and bool(re.fullmatch(r'tlc-draft-[0-9a-f]{32}',value))


def slug(value):
    value=unicodedata.normalize('NFKD',str(value)).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','-',value).strip('-')


def prepare(plan, config, adapter, *, remote=None, retained=None):
    product=plan['managed']['product']
    explicit=product.get('handle')
    if remote is not None and (plan.get('scope','full')!='full' or not explicit and not legacy(remote.get('handle'))):
        plan['handle']=remote['handle']
        return
    candidate=retained or explicit or slug(product['title'])[:255].rstrip('-') or 'product-'+slug(plan['sku'])[:240]
    def occupied(handle):
        found=adapter.find_draft(config,handle)
        if not isinstance(found,dict) or not isinstance(found.get('nodes'),list) or found.get('pageInfo',{}).get('hasNextPage') is not False:
            raise ShopifyError('URL handle discovery is incomplete. No handle change or create is authorised.')
        if any(not isinstance(node,dict) or not isinstance(node.get('id'),str) or not re.fullmatch(r'gid://shopify/Product/\d+',node['id']) for node in found['nodes']):
            raise ShopifyError('URL handle discovery returned invalid product identities. No write is authorised.')
        return any(node.get('id') != (remote or {}).get('id') for node in found['nodes'])
    if occupied(candidate):
        if explicit or retained:
            raise ShopifyError('The authored URL handle belongs to another Shopify product. Choose a unique handle.')
        suffix=slug(plan['sku'])[:100]
        candidate=candidate[:254-len(suffix)].rstrip('-')+'-'+suffix
        if occupied(candidate):
            raise ShopifyError('The title and SKU URL handle is also occupied. Enter a unique SEO URL handle and review again.')
    product['handle']=plan['handle']=candidate
    if remote is not None and candidate != remote['handle']:
        plan['warnings'].append('URL correction: '+remote['handle']+' → '+candidate+'. Shopify will redirect the old product URL to the new one.')
    elif remote is None:
        plan['warnings'].append('New product URL handle: '+candidate+'. Draft status is independent of its readable URL.')
