"""Local-only promotion estimates. Never contacts Shopify or changes checkout."""

from decimal import Decimal, ROUND_HALF_UP

from app.inventory.costing import variant_costs


def _price(row):
    try:
        amount = Decimal(str(row['price']))
        if not amount.is_finite() or amount < 0:
            return None
        return int((amount * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (ArithmeticError, KeyError, TypeError, ValueError):
        return None


def _percent(numerator, denominator):
    if denominator <= 0:
        return None
    return str((Decimal(numerator) * 100 / Decimal(denominator)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))


def review(definition, rows):
    costs = variant_costs(rows) if rows else {}
    variants = [{**row, 'price_pence':_price(row), 'costing':costs[row['sku']]} for row in rows]
    missing = sorted({message for row in variants for message in row['costing']['missing']})
    if not variants:
        missing.append('No qualifying variants have been resolved from retained local identities.')
    if any(row['price_pence'] is None for row in variants):
        missing.append('A local regular price is missing or invalid.')
    if any(row['costing']['cost'] is None or not row['costing']['bom_complete'] for row in variants):
        missing.append('At least one qualifying variant lacks an effective local cost-per-item value.')
    assumptions = ['This is contribution after the resolved product/variant cost-per-item, not final net profit.',
                   'The cost-per-item is the local value prepared for Shopify, not a live Shopify cost readback.',
                   'Packaging is included only when it is an authored BOM line; an explicitly categorised packaging line is shown separately.',
                   'Payment fees, postage, labour, electricity, wear and waste are excluded unless already represented by a costed BOM item.',
                   'The selected product mix is not assigned a probability or unweighted average.']
    if any(row['costing'].get('finished_product') for row in variants):
        assumptions[1] = 'Saved finished-product profitability profiles use the shared BOM engine, before transaction fees; other products retain their existing cost source.'
        assumptions[3] = 'Saved finished-product profiles include configured buffer, equipment and labour. Transaction fees, postage and other unconfigured costs are not included here. Use the fixed Bundle planner for transaction-level fees.'
    tiers = []
    if definition['kind'] == 'exact' and variants:
        prices = [row['price_pence'] for row in variants]
        known = [row['costing']['cost'] for row in variants]
        complete = all(row['costing']['bom_complete'] and row['costing']['cost'] is not None for row in variants)
        for tier in definition['tiers']:
            qty, offer = tier['qty'], tier['total']
            normal_low = qty * min(prices) if all(p is not None for p in prices) else None
            normal_high = qty * max(prices) if all(p is not None for p in prices) else None
            low_cost = qty * min(known) if complete else None
            high_cost = qty * max(known) if complete else None
            # The cost extrema are exact when one variant may repeat. Contribution
            # is exact only if every ordinary set total is at least the offer;
            # otherwise a native discount must not surcharge the cheaper set.
            precise_contribution = normal_low is not None and normal_low >= offer
            best_profit = offer-low_cost if precise_contribution and low_cost is not None else None
            worst_profit = offer-high_cost if precise_contribution and high_cost is not None else None
            loss_risk = (high_cost is not None and high_cost > offer or
                         any(row['costing']['cost'] is not None and row['price_pence'] is not None and
                             row['costing']['cost'] > row['price_pence'] for row in variants))
            # Current exact multi-buy semantics permit repeated units of one variant.
            # These are cost bounds, not a prediction of the customer's mix. A set
            # whose ordinary price is below the offer must never be surcharged.
            tiers.append({'qty':qty, 'offer':offer, 'normal_low':normal_low, 'normal_high':normal_high,
                          'discount_low':max(0, normal_low-offer) if normal_low is not None else None,
                          'discount_high':max(0, normal_high-offer) if normal_high is not None else None,
                          'best_cost':low_cost, 'worst_cost':high_cost,
                          'best_contribution':best_profit, 'worst_contribution':worst_profit,
                          'best_margin':_percent(best_profit,offer) if best_profit is not None else None,
                          'worst_margin':_percent(worst_profit,offer) if worst_profit is not None else None,
                          'best_markup':_percent(best_profit,low_cost) if best_profit is not None and low_cost else None,
                          'worst_markup':_percent(worst_profit,high_cost) if worst_profit is not None and high_cost else None,
                          'bound_only':not precise_contribution, 'loss_risk':loss_risk})
    elif definition['kind'] != 'exact':
        missing.append('A complete bundle-level profitability model is not available for this offer type.')
    return {'variants':variants, 'tiers':tiers, 'missing':list(dict.fromkeys(missing)),
            'assumptions':assumptions,
            'bom_complete':bool(variants) and all(row['costing']['bom_complete'] for row in variants),
            'repeat_variants_allowed':True}
