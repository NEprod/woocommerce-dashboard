"""Market catalogue evidence for the existing signed publication stage."""
import re

from app.shopify_connection import MARKET_CONTEXT_SIZE, ShopifyError, ShopifyReadinessFinding, _gid

KEY = 'shopify_market_ids'
MANUAL_REQUIRED = 'Market availability requires explicit Review & Sync approval. No Shopify write attempted.'
ONLINE_STORE = 'online-store'


def selected(authored):
    values = authored.get(KEY, [])
    if not isinstance(values, list) or len(values) > 20:
        raise ShopifyReadinessFinding(
            'Select up to twenty additional Shopify markets.',
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


def countries_for(market):
    regions = ((market.get('conditions') or {}).get('regionsCondition') or {}).get('regions')
    codes = [item.get('code') for item in _nodes(regions)]
    if (not codes or any(not isinstance(code, str) or not re.fullmatch(r'[A-Z]{2}', code) for code in codes)
            or len(set(codes)) != len(codes)):
        _unsupported(f'{market["name"][:120]}: a complete, explicit country list is required to verify availability.')
    return sorted(codes)


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
        active = status == 'ACTIVE' and kind == 'REGION'
        regions = ((market.get('conditions') or {}).get('regionsCondition') or {}).get('regions')
        home = active and isinstance(regions, dict) and regions.get('pageInfo', {}).get('hasNextPage') is False and regions.get('nodes') == [{'code': 'GB'}]
        result.append({'id': market_id, 'name': name[:120], 'active': active, 'home': home})
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
        codes = countries_for(row)
        if countries.intersection(codes):
            _unsupported(f'{name}: overlapping market regions cannot be safely managed here.')
        countries.update(codes)
        home = codes == ['GB']
        if 'GB' in codes and not home:
            _unsupported(f'{name}: the UK must have its own region market; a mixed-country market cannot distinguish UK default from international opt-in.')
        active = [c for c in _nodes(row.get('catalogs')) if c.get('status') == 'ACTIVE']
        if not any(c.get('publication') for c in active):
            # Store-default or pricing-only catalogues inherit the Online Store
            # assortment. There is no market publication to add/remove here.
            result.append({'id': row['id'], 'name': name, 'catalog': None,
                           'publication': None, 'countries': codes, 'home': home})
            continue
        if len(active) != 1:
            _unsupported(f'{name}: multiple active catalogues cannot be safely controlled by this review.')
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
                       'publication': pid, 'countries': codes, 'home': home})
    if not any(market['home'] for market in result):
        _unsupported('An active United Kingdom-only region market is required for the UK default.')
    if len(countries) > 120:
        _unsupported('The configured Markets exceed the 120-country verification bound.')
    return result


def read(row, data, adapter, config, topology, *, online_before, context=True, verifying=False):
    from app.shopify_publication import read_memberships
    observed = read_memberships(row, data, adapter.market_publication_state(config, row.shopify_product_id))
    memberships = {market['publication']: observed['memberships'].get(market['publication'], False)
                   for market in topology if market['publication']}
    availability = {}
    if context and observed['status'] == 'ACTIVE':
        from app.shopify_publication import require_identity
        targets = {code: online_before and memberships.get(market['publication'], True)
                   for market in topology for code in market['countries']}
        codes = sorted(targets)
        actual = {}
        for start in range(0, len(codes), MARKET_CONTEXT_SIZE):
            batch = codes[start:start + MARKET_CONTEXT_SIZE]
            proof = adapter.market_context(config, row.shopify_product_id, batch)
            require_identity(row, data, proof)
            if proof['status'] != observed['status'] or any(type(proof.get(f'c{i}')) is not bool for i in range(len(batch))):
                raise ShopifyError('Shopify market country-context readback is incomplete.')
            for i, code in enumerate(batch):
                actual[code] = proof[f'c{i}']
                if actual[code] != targets[code]:
                    if verifying:
                        raise ShopifyError(f'Publication readback differs or is partial: Shopify buyer availability for {code} does not match its catalogue. No automatic write retry.')
                    _unsupported(f'{code}: market catalogue membership and Shopify buyer availability differ. Check existing channels and market/catalogue setup.')
        availability = {market['id']: all(actual[code] for code in market['countries']) for market in topology}
    return {'status': observed['status'], 'memberships': memberships, 'availability': availability}


def comparison(wanted, row, data, adapter, config, shop, channels, online_before):
    mapping = topology(adapter, config, shop, wanted, channels)
    observed = read(row, data, adapter, config, mapping, online_before=online_before,
                    context=online_before) if row else None
    actions, fields, blockers = [], [], []
    for market in mapping:
        pid = market['publication']
        available = observed['availability'].get(market['id']) if observed else None
        after = market['home'] or market['id'] in wanted
        blocked = not pid and not after
        if blocked:
            blockers.append(f'{market["name"]}: inherits Online Store availability. Assign a dedicated catalogue with automatic inclusion off to exclude products in this market.')
        if pid:
            before = observed['memberships'][pid] if observed else False
            actions.append({'id': pid, 'before': before, 'after': after, 'changed': before != after})
        fields.append({'field': 'markets.' + market['id'], 'label': 'Market: ' + market['name'],
                       'before': available, 'after': after, 'changed': available != after,
                       'home': market['home'], 'blocked': blocked, 'publication_only': True})
    return {'topology': mapping, 'actions': actions, 'fields': fields, 'observed': observed,
            'blockers': blockers, 'online_before': online_before}


def check_reviewed(row, data, plan, adapter, config, shop, channels, *, verify=False, online_after=False):
    from app.shopify_publication import online_store_channel
    market = plan['markets']
    online = online_store_channel(channels)
    if not online or online['id'] != market.get('online_publication'):
        raise ShopifyError('The reviewed Online Store publication changed or is missing. Inspect retained evidence; no replay.')
    if topology(adapter, config, shop, plan['intent'][KEY], channels) != market['topology']:
        raise ShopifyError('Shopify market/catalogue configuration changed since review. Inspect retained evidence; no replay.')
    observed = read(row, data, adapter, config, market['topology'],
                    online_before=online_after if verify else market['online_before'],
                    context=verify and online_after, verifying=verify)
    if not verify and market['observed'] and any(observed[key] != market['observed'][key] for key in ('status', 'memberships')):
        raise ShopifyError('Shopify market membership changed since review. Rebuild Review; no market mutation attempted.')
    if any(observed['memberships'][action['id']] != action['after' if verify else 'before'] for action in market['actions']):
        raise ShopifyError('Publication readback differs or is partial: Market memberships differ from the reviewed availability. No automatic write retry.')
    if verify and online_after and any(observed['availability'].get(item['field'].removeprefix('markets.')) is not item['after']
                                      for item in market['fields']):
        raise ShopifyError('Publication readback differs or is partial: Shopify buyer availability does not match the reviewed market selections.')
    return observed
