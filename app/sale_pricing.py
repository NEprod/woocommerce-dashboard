"""One exact-decimal, UK-time effective price projection for review/status/tasks."""
from datetime import datetime, date, time, timedelta, UTC
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

LONDON = ZoneInfo('Europe/London')


def instant(value, *, end=False):
    if value in (None, ''):
        return None
    if not isinstance(value, str):
        raise ValueError('Sale dates must be ISO dates or date-times.')
    try:
        if len(value) == 10:
            day = date.fromisoformat(value) + (timedelta(days=1) if end else timedelta())
            local = datetime.combine(day, time(), LONDON)
        else:
            local = datetime.fromisoformat(value.replace('Z','+00:00'))
            if local.tzinfo is None:
                candidates = [local.replace(tzinfo=LONDON, fold=fold) for fold in (0,1)]
                valid = {v.astimezone(UTC) for v in candidates
                         if v.astimezone(UTC).astimezone(LONDON).replace(tzinfo=None) == local}
                if len(valid) != 1:
                    raise ValueError('Ambiguous or nonexistent Europe/London sale time; supply an explicit UTC offset.')
                return valid.pop().replace(tzinfo=None)
        return local.astimezone(UTC).replace(tzinfo=None)
    except (TypeError, OverflowError, ValueError) as exc:
        raise ValueError('Invalid/ambiguous Europe/London sale date-time; use a valid ISO date or explicit offset.') from exc


def money(value):
    try:
        amount = Decimal(str(value))
        if isinstance(value,bool) or not amount.is_finite() or amount < 0 or amount > 1000000000 or amount != amount.quantize(Decimal('.01')):
            raise ValueError()
        return format(amount, '.2f')
    except (InvalidOperation, ValueError):
        raise ValueError('Prices must be nonnegative exact currency amounts with at most two decimal places.') from None


def schedule(authored, *, partial=False):
    start = instant(authored.get('sale_start_date'))
    end = instant(authored.get('sale_end_date'), end=True)
    if start and end and end <= start:
        raise ValueError('Sale end must follow the start (date-only end includes that entire local day).')
    if not partial and (start or end) and authored.get('sale_price') in (None,'') and not any(
            isinstance(v,dict) and v.get('sale_price') not in (None,'') for v in authored.get('variation_modifiers',{}).values()):
        raise ValueError('A sale schedule requires an explicit sale amount.')
    return start, end


def project(authored, regular, sale=None, *, at=None):
    from app.shopify_drafts import now
    at = at or now()
    if at.tzinfo:
        at = at.astimezone(UTC).replace(tzinfo=None)
    regular = money(regular)
    start, end = schedule(authored)
    sale = authored.get('sale_price', sale)
    if sale in (None,''):
        return {'price':regular,'compareAtPrice':None,'state':'none'}
    sale = money(sale)
    if Decimal(sale) >= Decimal(regular):
        raise ValueError('Sale price must be below each effective regular price.')
    state = 'scheduled' if start and at < start else 'expired' if end and at >= end else 'active'
    return {'price':sale if state == 'active' else regular,
            'compareAtPrice':regular if state == 'active' else None,'state':state}


def variant_sale(authored, attributes, fallback):
    # Reuse existing exact/most-specific modifier selection. These are absolute
    # amounts, not a new discount mechanism or changes to scanner row semantics.
    from app.utils.json_utils import apply_variation_modifiers
    selected = apply_variation_modifiers(authored, attributes, log=lambda *a,**k:None)
    return selected.get('sale_price', fallback)


def catalogue(product, *, authored=None, at=None):
    """Read-only current local prices, NOT Shopify verification or write authority.

    Indexed sale amounts are configuration, never a permanently effective price.
    Reuse project()/the existing modifier selector at request time. Raw indexed
    dates are a display fallback only when the authored source is unavailable.
    """
    import json
    from app.shopify_drafts import now
    from app.taxonomy_assignments import product_document
    at = at or now()
    if at.tzinfo:
        at = at.astimezone(UTC).replace(tzinfo=None)
    authored = product_document(product) if authored is None else authored
    if 'collection_type' not in authored:
        raw = json.loads(product.resolved_row_json or '{}')
        authored = {**authored, **{key: raw[column] for key, column in (
            ('sale_start_date', 'Date sale price starts'), ('sale_end_date', 'Date sale price ends'))
            if raw.get(column) not in (None, '')}}
    variants = []
    try:
        start, end = schedule(authored, partial=True)
        children = [v for v in product.variations if v.catalogue_status == 'active'] if product.product_type == 'variable' else []
        for item in children or [product]:
            regular = authored.get('price', item.regular_price) if item is product else item.regular_price
            if regular is None:
                continue
            sale = authored.get('sale_price', item.sale_price) if item is product else variant_sale(
                authored, {a.name:a.value for a in item.attributes}, item.sale_price)
            pricing = project(dict(authored, sale_price=sale), regular, at=at)
            variants.append({'id':item.id, 'sku':item.sku, 'regular':money(regular),
                'configured_sale':money(sale) if sale not in (None, '') else None, **pricing})
        def bounds(key):
            values = [Decimal(v[key]) for v in variants if v[key] is not None]
            return [format(fn(values), '.2f') if values else None for fn in (min, max)]
        states = {v['state'] for v in variants}
        state = next((s for s in ('active', 'scheduled', 'expired') if s in states), 'none')
        pending = [t for t in (start, end) if t and t > at] if states - {'none'} else []
        return {'minimum':bounds('price')[0], 'maximum':bounds('price')[1],
            'regular_minimum':bounds('regular')[0], 'regular_maximum':bounds('regular')[1],
            'configured_sale_minimum':bounds('configured_sale')[0], 'configured_sale_maximum':bounds('configured_sale')[1],
            'state':state, 'label':{'none':'No Sale', 'scheduled':'Scheduled', 'active':'Active', 'expired':'Ended'}[state],
            'next_transition':min(pending).isoformat()+'Z' if pending else None,
            'as_of':at.isoformat()+'Z', 'variants':variants, 'error':None}
    except (ValueError, TypeError, ArithmeticError) as exc:
        return {'minimum':None, 'maximum':None, 'regular_minimum':None, 'regular_maximum':None,
            'configured_sale_minimum':None, 'configured_sale_maximum':None, 'state':'invalid',
            'label':'Needs Attention', 'next_transition':None, 'as_of':at.isoformat()+'Z',
            'variants':[], 'error':'Resolve local pricing/schedule metadata before relying on effective prices.'}
