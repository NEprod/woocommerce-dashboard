"""Validate and resolve promotion definitions without changing catalogue records."""
from datetime import datetime, UTC
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from zoneinfo import ZoneInfo

from app import db
from app.models import Collection, Product, ShopifyProductIdentity

KINDS = {'exact':'Exact multi-buy', 'bxgy':'Buy X Get Y', 'percent':'Percentage off', 'fixed':'Fixed amount off'}
COMBINES = ('productDiscounts', 'orderDiscounts', 'shippingDiscounts')
MAX_VARIANTS = 100
UK = ZoneInfo('Europe/London')
VARIANT = r'gid://shopify/ProductVariant/[0-9]{1,30}'
PRODUCT = r'gid://shopify/Product/[0-9]{1,30}'
SEGMENT = r'gid://shopify/Segment/[0-9]{1,30}'


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def fingerprint(value):
    return sha256(canonical(value).encode()).hexdigest()


def clean(value, label, limit=120, *, required=True):
    if not isinstance(value, str):
        raise ValueError(f'Enter a valid {label}.')
    value = value.strip()
    if (required and not value or len(value) > limit or
            any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        raise ValueError(f'Enter a valid {label} (up to {limit} characters).')
    return value


def integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label} must be a whole number between {low} and {high}.')
    return value


def minor(value, label, bound=1000000):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,7}(?:\.[0-9]{1,2})?', value.strip()):
        raise ValueError(f'{label} needs a positive value with up to two decimal places.')
    try:
        return integer(int(Decimal(value.strip()) * 100), label, 1, bound)
    except InvalidOperation:
        raise ValueError(f'Enter a valid {label}.') from None


def money(amount):
    return f'{amount // 100}.{amount % 100:02d}'


def iso(value):
    return value.astimezone(UTC).isoformat(timespec='seconds').replace('+00:00','Z')


def schedule_time(raw, *, required=False):
    if raw in (None, '') and not required:
        return None
    try:
        value = datetime.fromisoformat(clean(raw, 'schedule date and time', 35))
    except (TypeError, ValueError):
        raise ValueError('Enter a schedule date and time like 2027-01-15 18:00.') from None
    if not 2000 <= value.year <= 2100 or value.microsecond or len(raw) < 16:
        raise ValueError('Enter a complete date and time in the supported range, without fractional seconds.')
    if value.tzinfo is None:
        choices = {iso(candidate) for fold in (0, 1)
                   if (candidate := value.replace(tzinfo=UK, fold=fold)).astimezone(UTC).astimezone(UK).replace(tzinfo=None) == value}
        if len(choices) != 1:
            raise ValueError('This UK clock-change time is ambiguous or missing. Choose another time or include an explicit offset (+01:00 or +00:00).')
        return choices.pop()
    return iso(value)


def _targets(selectors):
    if not isinstance(selectors, list) or len(selectors) > MAX_VARIANTS:
        raise ValueError('Use up to 100 explicit product, collection or variant selections.')
    unique = {}
    for value in selectors:
        if not isinstance(value, dict):
            raise ValueError('Choose an eligible target from the catalogue.')
        mode = value.get('mode')
        if mode not in ('product', 'collection', 'option', 'fixed'):
            raise ValueError('Unsupported catalogue selection.')
        ref = {'mode':mode, 'id':integer(value.get('id'), 'Catalogue ID', 1, 10**10)}
        ref['source'] = clean(value.get('source'), 'retained source identity', 1024)
        if mode == 'option':
            ref.update(name=clean(value.get('name'), 'option name', 255), value=clean(value.get('value'), 'option value', 255))
        elif mode == 'fixed':
            sku = clean(value.get('sku'), 'variant SKU', 64)
            ident = value.get('gid')
            if not isinstance(ident, str) or not re.fullmatch(VARIANT, ident):
                raise ValueError('A fixed selection needs a retained Shopify variant ID.')
            ref.update(sku=sku, gid=ident)
        unique[canonical(ref)] = ref
    return [unique[key] for key in sorted(unique)]


def definition(data):
    if not isinstance(data, dict):
        raise ValueError('Enter a promotion definition.')
    kind, method = data.get('kind'), data.get('method')
    scope, intent = data.get('scope'), data.get('intent')
    if not isinstance(kind,str) or kind not in KINDS or method not in ('automatic','code') or scope not in ('product','order'):
        raise ValueError('Choose a supported promotion type, scope and activation method.')
    if intent not in ('draft','live','disabled','archived'):
        raise ValueError('Choose a valid local intent.')
    if scope == 'order' and kind in ('exact','bxgy'):
        raise ValueError('Exact multi-buy and Buy X Get Y target products, not the whole order.')
    start, end = schedule_time(data.get('starts')), schedule_time(data.get('ends'))
    if start and end and end <= start:
        raise ValueError('The end time must be after the start time.')
    if intent == 'live' and not start:
        raise ValueError('Set an explicit start time before making a promotion Live.')
    targets = _targets(data.get('targets', [])) if scope == 'product' else []
    exclusions = _targets(data.get('exclusions', [])) if scope == 'product' else []
    if any(item['mode'] not in ('product', 'fixed') for item in exclusions):
        raise ValueError('Exclude an entire product or one retained variant.')
    flags = data.get('combines', {})
    if not isinstance(flags, dict) or any(k not in COMBINES or type(v) is not bool for k,v in flags.items()):
        raise ValueError('Choose supported combinations only.')
    out = dict(name=clean(data.get('name'), 'promotion name', 95), kind=kind, method=method, scope=scope,
               intent=intent, starts=start, ends=end, targets=targets, exclusions=exclusions,
               combines={k:flags.get(k,False) for k in COMBINES})
    if 'description' in data:
        out['description'] = clean(data['description'], 'planning description', 500, required=False)
    if kind == 'exact' and (out['combines']['orderDiscounts'] or out['combines']['shippingDiscounts']):
        raise ValueError('Exact-price offers can only combine with product offers on different eligible items.')
    if kind == 'bxgy' and out['combines']['productDiscounts']:
        raise ValueError('This release does not stack Buy X Get Y with other product discounts.')
    if method == 'code':
        code = clean(data.get('code'), 'discount code', 40).upper()
        if not re.fullmatch(r'[A-Z0-9_-]{3,40}', code):
            raise ValueError('Codes use 3–40 letters, digits, hyphens or underscores.')
        once = data.get('once', False)
        if type(once) is not bool:
            raise ValueError('Specify whether the code is restricted to one use per customer.')
        usage = data.get('usage')
        segment = data.get('segment') or None
        if segment and (not isinstance(segment,str) or not re.fullmatch(SEGMENT, segment)):
            raise ValueError('Enter an existing Shopify Segment ID, or leave it empty for all customers.')
        if kind == 'exact' and segment:
            raise ValueError('Exact-price offers are UK-only. Combining a customer segment with a market restriction is not supported here.')
        out.update(code=code, usage=integer(usage,'Code use limit',1,1000000) if usage is not None else None,
                   once=once, segment=segment)
    if kind == 'exact':
        tiers = data.get('tiers')
        if not isinstance(tiers, list) or not 1 <= len(tiers) <= 8 or any(not isinstance(t,dict) or set(t)!={'qty','total'} for t in tiers):
            raise ValueError('Enter one to eight exact-price tiers.')
        tiers = [{'qty':integer(t['qty'],'Tier quantity',2,24), 'total':integer(t['total'],'Tier price (pence)',1,1000000)}
                 for t in tiers]
        if not tiers or len({t['qty'] for t in tiers}) != len(tiers):
            raise ValueError('Each tier must have a different quantity and an exact positive price.')
        repeat = data.get('repeat',False)
        if type(repeat) is not bool:
            raise ValueError('Choose whether the offer repeats.')
        out.update(tiers=sorted(tiers,key=lambda t:t['qty']), repeat=repeat)
    elif kind == 'bxgy':
        out.update(buy=integer(data.get('buy'),'Buy quantity',1,24), get=integer(data.get('get'),'Reward quantity',1,24),
                   value=integer(data.get('value'),'Reward percentage',1,10000),
                   rewards=_targets(data.get('rewards',[])),
                   limit=integer(data.get('limit',1),'Reward sets per order',1,24))
    else:
        out.update(value=integer(data.get('value'),'Discount amount',1,10000 if kind == 'percent' else 1000000),
                   minimum=integer(data.get('minimum',1),'Minimum quantity',1,128))
    return out


def product_choices(product, store_id):
    """Only retained, verified mappings may be used; compare by identity, not option order."""
    from app.shopify_drafts import evidence
    mappings = ShopifyProductIdentity.query.filter_by(product_id=product.id, store_id=store_id,
        source_identity=product.source_relpath, sku=product.sku, state='verified', verification_state='verified').limit(2).all()
    if len(mappings) != 1 or not re.fullmatch(PRODUCT,mappings[0].shopify_product_id or ''):
        return []
    mapping = mappings[0]
    proof = evidence(mapping)
    base = {'product':mapping.shopify_product_id,'name':product.title,'local_id':product.id,'source':product.source_relpath}
    if product.product_type == 'simple':
        vid = proof.get('variant_id')
        return [dict(base,id=vid,sku=product.sku,options={},price=str(product.regular_price))] if re.fullmatch(VARIANT,vid or '') else []
    if product.product_type != 'variable':
        return []
    sellable = [v for v in product.variations if v.catalogue_status == 'active' and v.visible is not False]
    rows = []
    for v in sellable:
        link = proof.get('variant_maps',{}).get(v.source_identity,{})
        options = {a.name:a.value for a in v.attributes}
        remote_options = {a['name']:a['value'] for a in link.get('options',[])} if link else {}
        if (v.sku and link.get('sku') == v.sku and options == remote_options and
                re.fullmatch(VARIANT,link.get('id') or '')):
            rows.append(dict(base,id=link['id'],sku=v.sku,options=options,price=str(v.regular_price)))
        else:
            rows.append(dict(base,id=None,sku=v.sku,options=options,price=str(v.regular_price)))
    return rows


def resolve(selectors, store_id, exclusions=()):
    rows = {}
    excluded_products = set()
    for selection in exclusions:
        product = db.session.get(Product,selection['id'])
        if not product or product.source_relpath != selection['source']:
            raise ValueError('An excluded product no longer has its retained catalogue identity.')
        if selection['mode'] == 'product':
            excluded_products.add(product.id)
        elif selection['mode'] != 'fixed':
            raise ValueError('Exclude an entire product or one retained variant.')
    for selection in selectors:
        if selection['mode'] == 'collection':
            collection = db.session.get(Collection,selection['id'])
            if not collection or collection.source_relpath != selection['source']:
                raise ValueError('A selected collection no longer has its retained catalogue identity.')
            products = Product.query.filter_by(collection_id=collection.id,catalogue_status='active').limit(MAX_VARIANTS+1).all()
            if len(products) > MAX_VARIANTS:
                raise ValueError('Collection exceeds the 100-variant publication budget; narrow the eligibility.')
        else:
            product = db.session.get(Product,selection['id'])
            if not product or product.catalogue_status != 'active' or product.source_relpath != selection['source']:
                raise ValueError('A selected product no longer has its retained catalogue identity.')
            products = [product]
        for product in products:
            if product.id in excluded_products:
                continue
            options = product_choices(product,store_id)
            if selection['mode'] == 'fixed':
                options = [v for v in options if v['sku'] == selection['sku'] and v['id'] == selection['gid']]
            if selection['mode'] == 'option':
                options = [v for v in options if v['options'].get(selection['name']) == selection['value']]
            if not options:
                raise ValueError(f'{product.sku}: no matching verified Shopify identity; review the retained catalogue mapping.')
            for row in options:
                if not row['id']:
                    raise ValueError(f'{row["sku"]}: matching variant has no verified Shopify identity.')
                if row['id'] in rows and rows[row['id']]['sku'] != row['sku']:
                    raise ValueError('Two local SKUs claim the same Shopify variant; review the mapping.')
                rows[row['id']] = row
                if len(rows) > MAX_VARIANTS:
                    raise ValueError('The promotion exceeds the 100-variant publication budget; narrow the eligibility.')
    for selection in exclusions:
        if selection['mode'] == 'fixed' and selection['id'] not in excluded_products:
            product = db.session.get(Product,selection['id'])
            if not any(row['id'] == selection['gid'] and row['sku'] == selection['sku']
                       for row in product_choices(product, store_id)):
                raise ValueError('An excluded variant no longer has its retained verified identity.')
            rows = {gid: row for gid, row in rows.items() if not
                    (row['local_id'] == product.id and row['sku'] == selection['sku'] and gid == selection['gid'])}
    if not rows:
        raise ValueError('Choose at least one verified eligible variant.')
    return [rows[key] for key in sorted(rows)]
