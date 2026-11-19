"""Operator labels and links only. Never used to approve or execute an operation."""
from flask import url_for
from decimal import Decimal, InvalidOperation
import re


def source_label(row):
    if row['state'] == 'inherited' and readable(row['resolved']) != readable(row['collection']):
        return 'Inherited + derived'
    if row['state'] == 'overridden' and readable(row['resolved']) != readable(row['override']):
        return 'Mixed sources / derived'
    return {'inherited': 'Inherited', 'overridden': 'Overridden',
            'product_additions': 'Mixed sources', 'scanner-generated': 'Derived'}.get(row['state'], 'Source details')


def readable(value):
    """Summarise structure without replacing the complete source comparison."""
    if value is None:
        return 'Not set'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, dict):
        return '; '.join(f'{str(k).replace("_", " ").title()}: {readable(v)}' for k, v in value.items()) or 'Empty'
    if isinstance(value, (list, tuple)):
        return ', '.join(readable(v) for v in value) or 'None'
    return str(value)


def resolved_value(row):
    value = row['resolved']
    if row['key'] in {'price', 'sale_price'} and value is not None and not isinstance(value, bool):
        try:
            amount = Decimal(str(value))
            if amount.is_finite() and amount == amount.quantize(Decimal('0.01')):
                return f'£{amount:.2f}'
        except InvalidOperation:
            pass
    if row['key'] == 'weight' and value is not None:
        return readable(value) + ' g'
    return readable(value)


def collection_links(collections):
    """Exact existing source selector; never substitute an arbitrary collection."""
    return [dict(label=c['label'], url=url_for('commerce.taxonomy_mappings', source=c['key']) + '#individual-mapping')
            for c in collections if c.get('key') and c.get('action') == 'Resolve mapping']


def product_issue_links(product, messages):
    from app.shopify_taxonomy import assignments
    from app.shopify_connection import ShopifyError
    try:
        sources, _ = assignments(product)
    except (ShopifyError, ValueError, TypeError):
        return []  # Do not invent a fix link for an unresolvable local source.
    return [dict(label=s['label'], url=url_for('commerce.taxonomy_mappings', source=s['key']) + '#individual-mapping')
            for s in sources if any(re.search(r'Collection recovery required:\s*' + re.escape(s['label'])
                + r'(?=$|[.;]\s| · )', str(message), re.IGNORECASE) for message in messages if message)]


def review_display(plan, rows):
    matching, changes, deferred = [], [], []
    for row in rows:
        target = matching if row['action'] == 'Already matching' else deferred if row['action'].startswith('Deferred') else changes
        target.append(row)
    return dict(changes=changes, matching=matching, deferred=deferred,
                collection_links=collection_links(plan.get('collections', [])))


def action_label(action):
    if action == 'Already matching':
        return 'Already Matching'
    if action.startswith('Deferred'):
        return 'Deferred'
    if action == 'Set on create' or action.startswith('New Shopify field'):
        return 'Create'
    if action == 'Change' or action.startswith('Changed Shopify field'):
        return 'Update'
    return action
