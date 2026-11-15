"""Shopify Admin API for the distinct, development-only Promotions installation."""
from base64 import urlsafe_b64encode
from dataclasses import dataclass, field
from decimal import Decimal
import json
import os
from pathlib import Path
import re
from time import time
from uuid import uuid4

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .domain import COMBINES, VARIANT, canonical, money, schedule_time

API = '2026-10'
HANDLE = 'tlc-exact-multibuy'
NAMESPACE = '$app:tlc-promotions'
CONFIG_KEY = 'configuration'
EXISTING_CLIENT = 'f9498b5a7e0393d9cddbb49defb4cacd'
PRODUCTION_SHOP = 'gid://shopify/Shop/103940129151'
DISCOUNT = r'gid://shopify/(?:DiscountNode|DiscountAutomaticNode|DiscountCodeNode)/[0-9]{1,30}'


class ShopifyPromotionError(ValueError):
    pass


class Rejected(ShopifyPromotionError):
    """Shopify explicitly refused a mutation without producing a discount."""


@dataclass(frozen=True)
class Configuration:
    domain: str
    store: str
    app: str
    client: str
    kid: str
    private_key: str = field(repr=False)
    uk_market: str = ''

    @classmethod
    def from_env(cls):
        prefix = 'TLC_PROMOTIONS_'
        config = cls(*(os.getenv(prefix + name, '').strip() for name in
            ('DEV_DOMAIN','DEV_STORE_ID','DEV_APP_ID','DEV_CLIENT_ID','DEV_KEY_ID','DEV_PRIVATE_KEY_PATH','DEV_UK_MARKET_ID')))
        if not any((config.domain, config.store, config.app, config.client, config.kid, config.private_key)):
            return None
        config.validate()
        return config

    def validate(self):
        if (not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.myshopify\.com',self.domain)
                or self.domain == 'tenderlcreations.myshopify.com'
                or not re.fullmatch(r'gid://shopify/Shop/[0-9]{1,30}',self.store) or self.store == PRODUCTION_SHOP
                or not re.fullmatch(r'gid://shopify/App/[0-9]{1,30}',self.app)
                or not re.fullmatch(r'[0-9a-f]{32}', self.client) or self.client in {EXISTING_CLIENT, os.getenv('SHOPIFY_CLIENT_ID')}
                or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', self.kid) or not Path(self.private_key).is_absolute()
                or self.uk_market and not re.fullmatch(r'gid://shopify/Market/[0-9]{1,30}',self.uk_market)):
            raise ShopifyPromotionError('Configure only the separate Promotions development app and an allowlisted development shop.')
        return self

    @property
    def token_url(self):
        return 'https://' + self.domain + '/admin/oauth/access_token'


def assertion(config, key, stamp=None):
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise ShopifyPromotionError('Use an RSA Promotions private key of at least 2048 bits.')
    stamp = int(time()) if stamp is None else stamp
    def part(data):
        return urlsafe_b64encode(data).rstrip(b'=')
    encoded = b'.'.join(part(canonical(data).encode()) for data in [
        {'alg':'RS256','kid':config.kid}, {'iss':config.client,'sub':config.client,'aud':config.token_url,
          'iat':stamp,'exp':stamp+60,'jti':uuid4().hex}])
    return (encoded + b'.' + part(key.sign(encoded,padding.PKCS1v15(),hashes.SHA256()))).decode()


ACCESS = '''query PromotionsInstallation {
  shop { id myshopifyDomain currencyCode plan { partnerDevelopment } }
  currentAppInstallation { id app { id } accessScopes { handle } }
}'''
FUNCTIONS = '''query PromotionsFunctions {
  shopifyFunctions(first:25) { nodes { id handle appKey app { id } } pageInfo { hasNextPage } }
}'''
CHECK_VARIANTS = '''query PromotionVariantReadback($ids:[ID!]!) {
 nodes(ids:$ids) { ... on ProductVariant { id sku selectedOptions { name value } product { id status } } }
}'''

TYPES = {
    ('automatic','exact'): ('AutomaticApp','automaticAppDiscount','automaticAppDiscount'),
    ('code','exact'): ('CodeApp','codeAppDiscount','codeAppDiscount'),
    ('automatic','basic'): ('AutomaticBasic','automaticBasicDiscount','automaticDiscountNode'),
    ('code','basic'): ('CodeBasic','basicCodeDiscount','codeDiscountNode'),
    ('automatic','bxgy'): ('AutomaticBxgy','automaticBxgyDiscount','automaticDiscountNode'),
    ('code','bxgy'): ('CodeBxgy','bxgyCodeDiscount','codeDiscountNode'),
}
_BASE = '''title tags status startsAt endsAt updatedAt
combinesWith { productDiscounts orderDiscounts shippingDiscounts productDiscountsWithTagsOnSameCartLine }
context { __typename ... on DiscountCustomerSegments { segments { id } }
 ... on DiscountMarkets { markets(first: 2) { nodes { id } pageInfo { hasNextPage } } } }'''
_ITEMS = '''items { __typename ... on DiscountProducts {
 products(first: 1) { nodes { id } pageInfo { hasNextPage } }
 productVariants(first: 100) { nodes { id } pageInfo { hasNextPage } }
} }'''
_BUY = '''customerBuys { isOneTimePurchase isSubscription %s value { ... on DiscountQuantity { quantity } } }''' % _ITEMS
_GET = '''customerGets { appliesOnOneTimePurchase appliesOnSubscription %s value {
 __typename ... on DiscountPercentage { percentage }
 ... on DiscountAmount { amount { amount currencyCode } appliesOnEachItem }
 ... on DiscountOnQuantity { quantity { quantity } effect { ... on DiscountPercentage { percentage } } }
} }''' % _ITEMS
_CODES = '''codes(first:2) { nodes { code } pageInfo { hasNextPage } } usageLimit appliesOncePerCustomer'''
SUMMARY = 'id discount { __typename ' + ' '.join(f'... on Discount{kind} {{ tags status title }}' for kind,_,_ in TYPES.values()) + '}'


def detail_query(discount_type):
    # Ask only for this kind's detail to keep the query cost bounded.
    if discount_type not in {row[0] for row in TYPES.values()}:
        raise ShopifyPromotionError('Unsupported Shopify discount type.')
    detail = _BASE + (_CODES if discount_type.startswith('Code') else '')
    if discount_type.endswith('App'):
        detail += ' appDiscountType { functionId appKey } discountClasses appliesOnOneTimePurchase appliesOnSubscription'
    elif discount_type.endswith('Basic'):
        detail += ' discountClasses ' + _GET + ' minimumRequirement { ... on DiscountMinimumQuantity { greaterThanOrEqualToQuantity } }'
    else:
        detail += ' usesPerOrderLimit ' + _BUY + _GET
    meta = 'metafield(namespace:"' + NAMESPACE + '",key:"' + CONFIG_KEY + '"){jsonValue}' if discount_type.endswith('App') else ''
    return 'query PromotionRead($id:ID!) { discountNode(id:$id) { id ' + meta + ' discount { __typename ... on Discount' + discount_type + ' {' + detail + '} } } }'


class Admin:
    def __init__(self, config):
        self.config = config.validate()
        self._token = None
        self._proof = None
        self._session = requests.Session()
        self._session.trust_env = False

    def _post(self, url, *, payload=None, authentication=None):
        try:
            with self._session.post(url, data=authentication, json=payload,
                headers={'X-Shopify-Access-Token': self._token} if payload is not None else {},
                timeout=(5,12), allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise ShopifyPromotionError('Shopify rejected the Promotions request. Check installation and permissions; reconcile any attempted write.')
                if payload is not None and response.headers.get('X-Shopify-API-Version') != API:
                    raise ShopifyPromotionError('Shopify did not confirm the expected Admin API version.')
                raw = bytearray()
                started = time()
                for part in response.iter_content(8192):
                    raw.extend(part)
                    if len(raw) > 524288 or time() - started > 18:
                        raise ShopifyPromotionError('Promotions response exceeded its verification budget.')
                answer = json.loads(raw)
        except (requests.RequestException, ValueError, OSError) as exc:
            if isinstance(exc, ShopifyPromotionError):
                raise
            raise ShopifyPromotionError('Shopify Promotions response could not be verified.') from None
        if not isinstance(answer, dict):
            raise ShopifyPromotionError('Shopify Promotions response could not be verified.')
        return answer

    def _graphql(self, query, variables=None):
        from app.deployment_guard import promotion_transport
        promotion_transport(self.config, writing=not query.lstrip().startswith("query "))
        if not self._token:
            try:
                path = Path(self.config.private_key)
                if path.stat().st_size > 16384 or path.stat().st_mode & 0o077:
                    raise ValueError()
                key = serialization.load_pem_private_key(path.read_bytes(),password=None)
            except (OSError,ValueError,TypeError):
                raise ShopifyPromotionError('The Promotions private key must be a readable, protected PEM file (mode 0600 or 0400).') from None
            response = self._post(self.config.token_url, authentication={
                'grant_type':'client_credentials', 'client_id':self.config.client,
                'client_assertion_type':'urn:ietf:params:oauth:client-assertion-type:jwt-bearer',
                'client_assertion':assertion(self.config,key)})
            token, expiry = response.get('access_token'), response.get('expires_in')
            if not isinstance(token,str) or not token or type(expiry) is not int or not 30 < expiry <= 86400:
                raise ShopifyPromotionError('A current short-lived Promotions credential could not be verified.')
            self._token = token
        result = self._post(f'https://{self.config.domain}/admin/api/{API}/graphql.json',
                            payload={'query':query,'variables':variables or {}})
        if result.get('errors') or not isinstance(result.get('data'),dict):
            raise ShopifyPromotionError('Shopify could not verify the Promotions operation. Check the API contract and app scopes.')
        return result['data']

    def connection(self):
        data = self._graphql(ACCESS)
        shop, install = data.get('shop') or {}, data.get('currentAppInstallation') or {}
        if (shop.get('id') != self.config.store or shop.get('myshopifyDomain') != self.config.domain or
                (shop.get('plan') or {}).get('partnerDevelopment') is not True or
                (install.get('app') or {}).get('id') != self.config.app):
            raise ShopifyPromotionError('Promotions writes require the allowlisted development store and separate app installation. Production is blocked.')
        if shop.get('currencyCode') != 'GBP':
            raise ShopifyPromotionError('This release supports Promotions on GBP development stores only.')
        scopes = {s['handle'] for s in install.get('accessScopes', []) if isinstance(s,dict) and 'handle' in s}
        missing = {'read_products','write_discounts'} - scopes
        if missing:
            raise ShopifyPromotionError('Missing Promotions app scopes: ' + ', '.join(sorted(missing)) + '.')
        self._proof = {'shop':shop['id'],'app':self.config.app,'scopes':sorted(scopes)}
        return self._proof

    def function(self):
        data = bounded(self._graphql(FUNCTIONS).get('shopifyFunctions'), 25)
        found = [n for n in data if n.get('handle') == HANDLE and (n.get('app') or {}).get('id') == self.config.app]
        if len(found) != 1 or not found[0].get('id') or not found[0].get('appKey') or not self.config.uk_market:
            raise ShopifyPromotionError('Exact multi-buy needs the separate installed development Function and the intended UK Market ID.')
        market = self._graphql('query PromotionsMarket($id:ID!){market(id:$id){id status}}',
                              {'id':self.config.uk_market}).get('market') or {}
        if market.get('id') != self.config.uk_market or market.get('status') != 'ACTIVE':
            raise ShopifyPromotionError('The configured UK Market is not verifiably active in the development store.')
        return {'id':found[0]['id'],'app_key':found[0]['appKey'],'market':self.config.uk_market}

    def verify_variants(self, rows):
        for start in range(0,len(rows),50):
            batch = rows[start:start+50]
            nodes = self._graphql(CHECK_VARIANTS, {'ids':[r['id'] for r in batch]}).get('nodes')
            if not isinstance(nodes,list) or len(nodes) != len(batch):
                raise ShopifyPromotionError('The current Shopify variant identities could not be verified.')
            for row, actual in zip(batch,nodes):
                if (not isinstance(actual,dict) or actual.get('id') != row['id'] or actual.get('sku') != row['sku'] or
                        (actual.get('product') or {}).get('id') != row['product'] or
                        (actual.get('product') or {}).get('status') != 'ACTIVE' or
                        (row['options'] and {o['name']:o['value'] for o in actual.get('selectedOptions',[])} != row['options'])):
                    raise ShopifyPromotionError(f'{row["sku"]}: retained variant, options or active Shopify product could not be verified.')

    def find(self, marker):
        query = 'query FindPromotion($q:String!) {discountNodes(first:3,query:$q){nodes{' + SUMMARY + '} pageInfo{hasNextPage}}}'
        rows = bounded(self._graphql(query,{'q':'tag:' + marker}).get('discountNodes'),3)
        return [row for row in rows if marker in (row.get('discount') or {}).get('tags',[])]

    def code_owner(self, code):
        node = self._graphql('query PromotionCode($code:String!){codeDiscountNodeByCode(code:$code){id}}',{'code':code}).get('codeDiscountNodeByCode')
        return node['id'] if node else None

    def automatic_count(self):
        query = '''query ActivePromotions { discountNodes(first:25,query:"method:automatic status:active,scheduled"){
          nodes {id} pageInfo {hasNextPage} } }'''
        return len(bounded(self._graphql(query).get('discountNodes'),25))

    def read(self, ident, kind):
        if not re.fullmatch(DISCOUNT, ident or ''):
            raise ShopifyPromotionError('Invalid retained Shopify discount ID.')
        return self._graphql(detail_query(kind),{'id':ident}).get('discountNode')

    def apply(self, operation, expected, old):
        granted = self.connection()['scopes']  # fresh proof before any mutation, never a storefront token
        if expected['kind']=='exact' and 'read_markets' not in granted or expected.get('segment') and 'read_customers' not in granted:
            raise Rejected('The Promotions app lost a required read scope. No change was submitted.')
        if operation == 'disable':
            name = 'discount' + ('Code' if expected['method']=='code' else 'Automatic') + 'Deactivate'
            node = 'codeDiscountNode' if expected['method']=='code' else 'automaticDiscountNode'
            args, defs, variables = 'id:$id', '$id:ID!', {'id':old['id']}
            field_selection = 'id'
        else:
            mode = expected['kind'] if expected['kind'] in ('bxgy','exact') else 'basic'
            kind, argument, node = TYPES[(expected['method'],mode)]
            name = 'discount' + kind + ('Create' if operation == 'create' else 'Update')
            args, defs = argument+':$input', '$input:Discount'+kind+'Input!'
            variables = {'input':mutation_input(expected, old)}
            if operation != 'create':
                args, defs = 'id:$id,'+args, '$id:ID!,'+defs
                variables['id'] = old['id']
            field_selection = 'discountId' if mode=='exact' else 'id'
        result = self._graphql(f'mutation PublishPromotion({defs}){{{name}({args}){{{node}{{{field_selection}}} userErrors{{field code}}}}}}',variables).get(name)
        if not isinstance(result,dict):
            raise ShopifyPromotionError('Shopify did not confirm the mutation response. Reconcile before retrying.')
        if result.get('userErrors') and not result.get(node):
            raise Rejected('Shopify refused the reviewed discount. Check its type, eligibility, limits and permissions.')
        if result.get('userErrors') != []:
            raise ShopifyPromotionError('Shopify could not verify the mutation outcome. Reconcile before retrying.')
        ident = (result.get(node) or {}).get(field_selection)
        if not re.fullmatch(DISCOUNT,ident or ''):
            raise ShopifyPromotionError('Shopify did not return a verified discount ID. Reconcile before retrying.')
        return ident


def bounded(connection, limit):
    if (not isinstance(connection,dict) or not isinstance(connection.get('nodes'),list) or
            len(connection['nodes']) > limit or (connection.get('pageInfo') or {}).get('hasNextPage') is not False):
        raise ShopifyPromotionError('Shopify returned incomplete or too many records for safe verification.')
    return connection['nodes']


def same_identity(left, right):
    return bool(left and right and re.fullmatch(DISCOUNT,left) and re.fullmatch(DISCOUNT,right)
                and left.rsplit('/',1)[1] == right.rsplit('/',1)[1])


def mutation_input(e, old=None):
    old = old or {}
    data = {'title':e['title'],'tags':[e['marker']],'startsAt':e['starts'],'endsAt':e['ends'],'combinesWith':e['combines']}
    def ids(key):
        new, prior = set(e[key]), set(old.get(key,[]))
        return {'products':{'productVariantsToAdd':sorted(new-prior),'productVariantsToRemove':sorted(prior-new)}}
    if e['method'] == 'code':
        data.update(code=e['code'],usageLimit=e['usage'],appliesOncePerCustomer=e['once'])
    if e['kind'] == 'exact':
        data.update(discountClasses=['PRODUCT'],functionHandle=HANDLE,appliesOnOneTimePurchase=True,appliesOnSubscription=False,
             metafields=[{'namespace':NAMESPACE,'key':CONFIG_KEY,'type':'json','value':canonical(e['function_config'])}])
        old_market, market = old.get('function',{}).get('market'), e['function']['market']
        if old_market!=market:
            data['context']={'markets':{'add':[market], **({'remove':[old_market]} if old_market else {})}}
        return data
    if e.get('segment'):
        data['context'] = {'customerSegments':{'add':[e['segment']]}}
        if old.get('segment') and old['segment'] != e['segment']:
            data['context']['customerSegments']['remove'] = [old['segment']]
    else:
        data['context'] = {'all':True}
    if e['kind'] == 'bxgy':
        data.update(usesPerOrderLimit=str(e['limit']),
            customerBuys={'items':ids('variants'),'value':{'quantity':str(e['buy'])},'isOneTimePurchase':True,'isSubscription':False},
            customerGets={'items':ids('rewards'),
                'value':{'discountOnQuantity':{'quantity':str(e['get']),'effect':{'percentage':e['value']/10000}}},
                'appliesOnOneTimePurchase':True,'appliesOnSubscription':False})
    else:
        value = ({'percentage':e['value']/10000} if e['kind']=='percent' else
                 {'discountAmount':{'amount':money(e['value']), 'appliesOnEachItem':e['scope']=='product'}})
        data.update(customerGets={'items':{'all':True} if e['scope']=='order' else ids('variants'),
            'value':value, 'appliesOnOneTimePurchase':True,'appliesOnSubscription':False},
            minimumRequirement={'quantity':{'greaterThanOrEqualToQuantity':str(e['minimum'])}})
    return data


def _variants(value):
    if value.get('__typename') == 'AllDiscountItems':
        return []
    if value.get('__typename') != 'DiscountProducts' or bounded(value.get('products'),1):
        raise ShopifyPromotionError('Shopify discount eligibility is not solely explicit variants.')
    rows = bounded(value.get('productVariants'),100)
    ids = [r['id'] for r in rows]
    if len(ids) != len(set(ids)) or any(not re.fullmatch(VARIANT, v) for v in ids):
        raise ShopifyPromotionError('Shopify discount returned invalid or ambiguous variant membership.')
    return sorted(ids)


def observed(node, e):
    """Normalize exactly the supported remote contract; do not infer missing fields."""
    try:
        remote = node['discount']
        discount_kind = TYPES[(e['method'],e['kind'] if e['kind'] in ('exact','bxgy') else 'basic')][0]
        if (remote['__typename'] != 'Discount' + discount_kind or remote['tags'] != [e['marker']] or
                not re.fullmatch(DISCOUNT,node['id'])):
            raise ShopifyPromotionError('The Shopify discount identity or TLC ownership marker did not match.')
        if remote['combinesWith'].get('productDiscountsWithTagsOnSameCartLine') not in ([],None):
            raise ShopifyPromotionError('Remote discount has unsupported same-line stacking.')
        obj = {'id':node['id'],'title':remote['title'],'status':remote['status'],'starts':schedule_time(remote['startsAt'],required=True),
               'ends':schedule_time(remote['endsAt']), 'combines':{k:remote['combinesWith'][k] for k in COMBINES},
               'updated_at':remote['updatedAt']}
        context = remote['context']
        if e['method']=='code':
            codes = bounded(remote['codes'],2)
            if len(codes) != 1:
                raise ShopifyPromotionError('Promotion must own exactly one Shopify discount code.')
            obj.update(code=codes[0]['code'],usage=remote['usageLimit'],once=remote['appliesOncePerCustomer'])
        if e['kind']=='exact':
            if (remote['discountClasses'] != ['PRODUCT'] or context['__typename'] != 'DiscountMarkets' or
                    remote['appliesOnOneTimePurchase'] is not True or remote['appliesOnSubscription'] is not False):
                raise ShopifyPromotionError('Shopify exact-price scope or one-time-purchase restriction is unverified.')
            markets = bounded(context['markets'],2)
            if len(markets) != 1:
                raise ShopifyPromotionError('Shopify did not verify a single exact-price market.')
            obj.update(function={'id':remote['appDiscountType']['functionId'],'app_key':remote['appDiscountType']['appKey'],
                                 'market':markets[0]['id']},
                       function_config=node['metafield']['jsonValue'])
            return obj
        if context['__typename'] == 'DiscountCustomerSegments' and len(context['segments']) == 1:
            obj['segment'] = context['segments'][0]['id']
        elif context['__typename'] == 'DiscountCustomerAll':
            obj['segment'] = None
        else:
            raise ShopifyPromotionError('Shopify returned unsupported customer eligibility.')
        gets = remote['customerGets']
        if gets['appliesOnOneTimePurchase'] is not True or gets['appliesOnSubscription'] is not False:
            raise ShopifyPromotionError('Shopify one-time purchase eligibility could not be verified.')
        value = gets['value']
        if e['kind']=='bxgy':
            buys = remote['customerBuys']
            if buys['isOneTimePurchase'] is not True or buys['isSubscription'] is not False:
                raise ShopifyPromotionError('Shopify Buy X prerequisite could not be verified.')
            obj.update(variants=_variants(buys['items']),rewards=_variants(gets['items']),buy=int(buys['value']['quantity']),
                get=int(value['quantity']['quantity']),value=int(Decimal(str(value['effect']['percentage']))*10000),limit=int(remote['usesPerOrderLimit']))
        else:
            actual_scope = 'order' if gets['items']['__typename']=='AllDiscountItems' else 'product'
            if value['__typename']=='DiscountAmount':
                if value['amount']['currencyCode'] != 'GBP' or value['appliesOnEachItem'] is not (actual_scope=='product'):
                    raise ShopifyPromotionError('Shopify fixed amount currency or per-item scope differs.')
                actual_kind, magnitude = 'fixed', Decimal(value['amount']['amount'])*100
            elif value['__typename']=='DiscountPercentage':
                actual_kind, magnitude = 'percent', Decimal(str(value['percentage']))*10000
            else:
                raise ShopifyPromotionError('The Shopify discount value is unsupported.')
            if magnitude % 1 or actual_kind != e['kind'] or remote['discountClasses'] != [actual_scope.upper()]:
                raise ShopifyPromotionError('Shopify discount type, scope or precision differs.')
            obj.update(variants=_variants(gets['items']),scope=actual_scope,value=int(magnitude),
                       minimum=int(remote['minimumRequirement']['greaterThanOrEqualToQuantity']))
        return obj
    except (AttributeError,KeyError,TypeError,ArithmeticError,ValueError) as exc:
        if isinstance(exc, ShopifyPromotionError):
            raise
        raise ShopifyPromotionError('Shopify discount readback was incomplete or unsupported.') from None
