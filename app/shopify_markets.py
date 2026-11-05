"""Market catalogue evidence for the existing signed publication stage."""
import re

from app.shopify_connection import MARKET_CONTEXT_SIZE, ShopifyError, ShopifyReadinessFinding, _gid

KEY = 'shopify_market_ids'
MANUAL_REQUIRED = 'Market availability requires explicit Review & Sync approval. No Shopify write attempted.'


def selected(authored):
    if KEY not in authored:
        return None  # absent means unmanaged, never unpublish
    values = authored[KEY]
    if not isinstance(values, list) or not 1 <= len(values) <= 20:
        raise ShopifyReadinessFinding(
            'Select at least one Shopify market or leave markets unmanaged.',
            code='publication_configuration')
    try:
        ids = [_gid(value, 'Market') for value in values]
    except ShopifyError:
        raise ShopifyReadinessFinding('Use discovered Shopify market IDs.',
                                       code='publication_configuration') from None
    if len(ids) != len(set(ids)):
        raise ShopifyReadinessFinding('Shopify markets must be unique.',
                                       code='publication_configuration')
    return sorted(ids)


def require_read(config, shop):
    if config.api_version != '2026-10':
        raise ShopifyError('Shopify Markets requires the supported Admin API 2026-10.')
    missing = {'read_markets', 'read_products'} - set(shop['scopes'])
    if not {'read_publications', 'write_publications'}.intersection(shop['scopes']):
        missing.add('read_publications')
    if missing:
        raise ShopifyError('Missing Shopify access for Markets: ' + ', '.join(sorted(missing)) + '.')


def discover(adapter, config, shop):
    require_read(config, shop)
    rows = adapter.markets(config)
    seen, result = set(), []
    for market in rows:
        market_id = _gid(market.get('id'), 'Market')
        name, status, kind = market.get('name'), market.get('status'), market.get('type')
        if market_id in seen or not isinstance(name, str) or not name.strip() or status not in ('ACTIVE', 'DRAFT'):
            raise ShopifyError('Shopify returned ambiguous market identities or status.')
        seen.add(market_id)
        result.append({'id': market_id, 'name': name[:120], 'active': status == 'ACTIVE' and kind == 'REGION'})
    return rows, sorted(result, key=lambda row: row['id'])


def _nodes(connection):
    if (not isinstance(connection, dict) or (connection.get('pageInfo') or {}).get('hasNextPage') is not False
            or not isinstance(connection.get('nodes'), list)
            or any(not isinstance(value, dict) for value in connection['nodes'])):
        raise ShopifyError('Shopify Markets catalogue, hierarchy or region readback is incomplete.')
    return connection['nodes']


def _unsupported(reason):
    raise ShopifyReadinessFinding(reason, code='publication_configuration')


def topology(adapter, config, shop, wanted, channels):
    """Only support a complete, unambiguous market-to-publication mapping.

    Unselected active markets also need proven control: an omitted fallback
    catalogue cannot be assumed unavailable. Never rewrite global catalogues.
    """
    rows, choices = discover(adapter, config, shop)
    if set(wanted) - {choice['id'] for choice in choices if choice['active']}:
        _unsupported('A selected Shopify market is missing, inactive or not a region market. Review the saved selection.')
    publications, countries, result = set(), set(), []
    channel_ids = {row['id'] for row in channels}
    for row in sorted(rows, key=lambda item: item['id']):
        catalogs = _nodes(row.get('catalogs'))
        if row['status'] == 'DRAFT':
            # Protect inactive catalogue memberships as well.
            publications.update(_gid(c['publication'].get('id'), 'Publication') for c in catalogs if c.get('publication'))
    for row in sorted(rows, key=lambda item: item['id']):
        if row['status'] == 'DRAFT':
            continue
        name = row['name'][:120]
        if row['type'] != 'REGION' or _nodes(row.get('parentMarkets')):
            _unsupported(f'{name}: inherited or non-region Shopify markets require manual availability management.')
        regions = ((row.get('conditions') or {}).get('regionsCondition') or {}).get('regions')
        codes = [item.get('code') for item in _nodes(regions)]
        if not codes or any(not isinstance(code, str) or not re.fullmatch(r'[A-Z]{2}', code) for code in codes):
            _unsupported(f'{name}: a complete, explicit country list is required to verify availability.')
        if countries.intersection(codes) or len(set(codes)) != len(codes):
            _unsupported(f'{name}: overlapping market regions cannot be safely managed here.')
        countries.update(codes)
        active = [c for c in _nodes(row.get('catalogs')) if c.get('status') == 'ACTIVE']
        if len(active) != 1 or not active[0].get('publication'):
            _unsupported(f'{name}: assign one active, dedicated catalogue with a product publication in Shopify Admin.')
        catalog = active[0]
        catalog_id = _gid(catalog.get('id'), 'MarketCatalog')
        publication = catalog['publication']
        pid = _gid(publication.get('id'), 'Publication')
        if pid in publications or pid in channel_ids or (publication.get('catalog') or {}).get('id') != catalog_id:
            _unsupported(f'{name}: the catalogue publication is shared or is not dedicated to this market.')
        if publication.get('autoPublish') is not False:
            _unsupported(f'{name}: disable automatic product inclusion in its Shopify catalogue before managing Markets here.')
        publications.add(pid)
        result.append({'id': row['id'], 'name': name, 'catalog': catalog_id,
                       'publication': pid, 'countries': sorted(codes)})
    if len(countries) > 120:
        _unsupported('The configured Markets exceed the 120-country verification bound.')
    return result


def read(row, data, adapter, config, topology, *, context=True, verifying=False):
    from app.shopify_publication import read_memberships
    observed = read_memberships(row, data, adapter.market_publication_state(config, row.shopify_product_id))
    memberships = {market['publication']: observed['memberships'].get(market['publication'], False)
                   for market in topology}
    if context and observed['status'] == 'ACTIVE':
        from app.shopify_publication import require_identity
        targets = {code: memberships[market['publication']] for market in topology for code in market['countries']}
        codes = sorted(targets)
        for start in range(0, len(codes), MARKET_CONTEXT_SIZE):
            batch = codes[start:start + MARKET_CONTEXT_SIZE]
            proof = adapter.market_context(config, row.shopify_product_id, batch)
            require_identity(row, data, proof)
            if proof['status'] != observed['status'] or any(type(proof.get(f'c{i}')) is not bool for i in range(len(batch))):
                raise ShopifyError('Shopify market country-context readback is incomplete.')
            for i, code in enumerate(batch):
                if proof[f'c{i}'] != targets[code]:
                    if verifying:
                        raise ShopifyError(f'Publication readback differs or is partial: Shopify buyer availability for {code} does not match its catalogue. No automatic write retry.')
                    _unsupported(f'{code}: market catalogue membership and Shopify buyer availability differ. Check existing channels and market/catalogue setup.')
    return {'status': observed['status'], 'memberships': memberships}


def comparison(wanted, row, data, adapter, config, shop, channels):
    mapping = topology(adapter, config, shop, wanted, channels)
    observed = read(row, data, adapter, config, mapping) if row else None
    actions, fields = [], []
    for market in mapping:
        pid = market['publication']
        before = observed['memberships'][pid] if observed else False
        after = market['id'] in wanted
        changed = before != after
        actions.append({'id': pid, 'before': before, 'after': after, 'changed': changed})
        fields.append({'field': 'markets.' + market['id'], 'label': 'Market: ' + market['name'],
                       'before': before, 'after': after, 'changed': changed, 'publication_only': True})
    return {'topology': mapping, 'actions': actions, 'fields': fields, 'observed': observed}


def check_reviewed(row, data, plan, adapter, config, shop, channels, *, verify=False):
    market = plan['markets']
    if topology(adapter, config, shop, plan['intent'][KEY], channels) != market['topology']:
        raise ShopifyError('Shopify market/catalogue configuration changed since review. Inspect retained evidence; no replay.')
    observed = read(row, data, adapter, config, market['topology'], context=verify, verifying=verify)
    if not verify and market['observed'] and observed != market['observed']:
        raise ShopifyError('Shopify market membership changed since review. Rebuild Review; no market mutation attempted.')
    if any(observed['memberships'][action['id']] != action['after' if verify else 'before'] for action in market['actions']):
        raise ShopifyError('Publication readback differs or is partial: Market memberships differ from the reviewed availability. No automatic write retry.')
    return observed
