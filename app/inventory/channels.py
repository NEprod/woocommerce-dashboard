"""Channel assumptions and advisory prices over the shared Decimal pricing engine."""
from decimal import Decimal, ROUND_CEILING

DEFAULTS = dict(shopify_rounding='49_99', etsy_rounding='49_99', etsy_uplift_percent='15',
                etsy_minimum_uplift='1', transaction_percent='6.5', payment_percent='4',
                payment_fixed='0.20', regulatory_percent='0.48', listing_fixed='0.15',
                offsite_percent='0', vat_percent='20',
                **{f'vat_{key}': True for key in ('transaction','payment','regulatory','listing','offsite')})
ROUNDING = {'49_99', 'exact', 'half', 'whole', '95'}
FEES = [('transaction','Transaction fee'),('payment','Payment processing'),
        ('regulatory','Regulatory fee'),('listing','Listing / renewal allowance'),('offsite','Optional offsite advertising')]


def round_49_99(value):
    # Smallest qualifying ending >= the input, without binary-float arithmetic.
    return (value * 2 - Decimal('.98')).to_integral_value(rounding=ROUND_CEILING) / 2 + Decimal('.49')


def settings(config):
    return {**DEFAULTS, **config.get('channel_pricing', {})}


def validate(values):
    from .profitability import decimal
    result = {}
    for key, default in DEFAULTS.items():
        value = values.get(key, default)
        if key.endswith('rounding'):
            if value not in ROUNDING:
                raise ValueError('Choose a supported channel price rounding option.')
            result[key] = value
        elif key.startswith('vat_') and key != 'vat_percent':
            if type(value) is not bool:
                raise ValueError('Choose which fees are subject to VAT.')
            result[key] = value
        else:
            result[key] = str(decimal(value, key.replace('_',' '), maximum='100' if key.endswith('percent') else '1000000000'))
    percent = sum(Decimal(result.get(key+'_percent','0')) *
                  (1 + Decimal(result['vat_percent'])/100 if result['vat_'+key] else 1)
                  for key in ('transaction','payment','regulatory','offsite'))
    if percent > Decimal('99.99'):
        raise ValueError('Combined Etsy percentage fees including VAT must not exceed 99.99%.')
    return result


def comparison(cost, chosen, config, amounts, document, *, complete):
    from .profitability import decimal, money, pricing
    options = settings(config)
    override = document.get('etsy_price')
    override_price = decimal(override, 'Etsy what-if price') if override is not None and str(override).strip() else None
    if not complete:
        return dict(complete=False, notice='Channel profit requires complete production costs and configured Shopify fees.')
    base = chosen if chosen is not None else Decimal(amounts['recommended_price'])
    uplift = max(base * Decimal(options['etsy_uplift_percent'])/100, Decimal(options['etsy_minimum_uplift']))
    # Use the same rounding implementation as all Shopify suggestions.
    suggestion = pricing(base+uplift, None, dict(fee_percent='0',fee_fixed='0'), Decimal(0),
                         complete=True, rounding=options['etsy_rounding'])['recommended_price']
    etsy = override_price if override_price is not None else Decimal(suggestion)
    breakdown, percent, fixed, vat = [], Decimal(0), Decimal(0), Decimal(0)
    for key, label in FEES:
        rate = Decimal(options.get(key+'_percent','0'))/100
        charge = Decimal(options['payment_fixed'] if key == 'payment' else options['listing_fixed'] if key == 'listing' else '0')
        amount = etsy*rate+charge
        multiplier = Decimal(1)+Decimal(options['vat_percent'])/100 if options['vat_'+key] else Decimal(1)
        vat += amount*(multiplier-1)
        percent += rate*multiplier
        fixed += charge*multiplier
        breakdown.append(dict(name=label, amount=money(amount), rate=str(rate*100),fixed=str(charge),vat=options['vat_'+key]))
    calculated = pricing(cost, etsy, dict(fee_percent=str(percent*100),fee_fixed=str(fixed)),
                         Decimal(0), complete=True)
    shopify = dict(name='Shopify',price=money(base),cost=money(cost),
                   fees=amounts['fees'] if chosen is not None else amounts['suggested_fees'],
                   profit=amounts['profit'] if chosen is not None else amounts['suggested_profit'],
                   margin=amounts['margin'] if chosen is not None else amounts['suggested_margin'],
                   basis='Current / chosen selling price' if chosen is not None else 'Suggested Shopify price')
    return dict(complete=True, rows=[shopify,dict(name='Etsy',price=money(etsy),cost=money(cost),
                fees=calculated['fees'],profit=calculated['profit'],margin=calculated['margin'],
                basis='What-if override' if override is not None and str(override).strip() else 'Uplifted Shopify price')],
                etsy_suggested=suggestion,base=money(base),uplift=money(uplift),
                uplift_percent=options['etsy_uplift_percent'],minimum_uplift=options['etsy_minimum_uplift'],
                etsy_fees=breakdown,vat=money(vat),vat_percent=options['vat_percent'],
                notice='One item per order; the full fixed payment charge is allocated to this item. Percentage fees use the item selling price only: postage, gift wrap and buyer taxes are not modelled. Listing/renewal is an editable GBP allowance, not a live USD conversion. VAT applies only to the selected fees; adjust to your VAT treatment. Optional advertising is included only at its configured rate. No channel prices are published.')
