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
