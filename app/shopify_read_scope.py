"""Bounded read-only inspection snapshots and payload-free request accounting.

Never used by publishing/readback executors. No persistent cache or source of truth.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from hashlib import sha256
import json

_scope = ContextVar('shopify_inspection_scope', default=None)
_request = ContextVar('shopify_inspection_request', default=None)
REFERENCE_NAMES = frozenset({'Connection', 'SupportedCountries', 'TLCChannels',
    'FinishedDimensionDefinitions', 'AttributeSchema', 'AttributeDefinition',
    'AttributeTerm', 'AttributeField', 'StandardCategory', 'CollectionCandidates', 'PackedCapability'})


def phase(name):
    if name in {'Connection', 'ExistingSku', 'ExistingSkus', 'DraftFind'}: return 'identity_discovery'
    if 'Definition' in name or name in {'AttributeSchema', 'AttributeField', 'PackedCapability', 'SupportedCountries'}: return 'shared_definitions'
    if name in {'AttributeTerm', 'AttributeEntries', 'AttributeValues'}: return 'taxonomy_metafields'
    if 'Collection' in name or name == 'StandardCategory': return 'collections_taxonomy'
    if 'Channel' in name or 'Publication' in name: return 'publication'
    if 'Customer' in name: return 'customisations'
    if 'Packed' in name or 'Inventory' in name: return 'inventory'
    if 'Variant' in name: return 'variants'
    if 'Media' in name or 'Gallery' in name: return 'media'
    return 'product_and_verification'


@contextmanager
def inspection(*, reuse=True):
    state = {'reuse': reuse, 'cache': {}, 'seen': set(), 'profile': {
        'http_requests': 0, 'graphql_operations': 0, 'cache_hits': 0,
        'repeated_identical_requests': 0, 'cost_observations': 0,
        'requested_cost': 0, 'actual_cost': 0, 'latency_ms': 0, 'throttled': 0,
        'retry_attempts': 0, 'pagination_requests': 0, 'phases': {}}}
    token = _scope.set(state)
    try: yield state['profile']
    finally: _scope.reset(token)


@contextmanager
def transport(config, name, query, variables, version, writing):
    state = _scope.get()
    if state and writing:
        from app.shopify_connection import ShopifyError
        raise ShopifyError('Read-only inspection scope cannot perform Shopify mutations.')
    key = sha256(json.dumps([config.fingerprint(), version, query, variables], sort_keys=True).encode()).hexdigest()
    token = _request.set((key, name, any((variables or {}).get(k) for k in ('after', 'media', 'variants'))))
    cached = state and state['reuse'] and name in REFERENCE_NAMES and key in state['cache']
    if cached: state['profile']['cache_hits'] += 1
    result = {'cached': bool(cached), 'value': deepcopy(state['cache'][key]) if cached else None}
    try:
        yield result
        if state and not cached and state['reuse'] and name in REFERENCE_NAMES and len(state['cache']) < 256 and 'result' in result:
            state['cache'][key] = deepcopy(result['result'])
    finally: _request.reset(token)


def observe(detail):
    state, request = _scope.get(), _request.get()
    if not state or not request: return
    key, name, paginated = request
    p = state['profile']
    repeated = key in state['seen']
    if len(state['seen']) < 4096: state['seen'].add(key)
    bucket = p['phases'].setdefault(phase(name), {'http_requests': 0, 'requested_cost': 0,
        'actual_cost': 0, 'cost_observations': 0, 'latency_ms': 0, 'repeated': 0, 'throttled': 0})
    p['http_requests'] += 1; p['graphql_operations'] += 1
    p['pagination_requests'] += int(paginated)
    p['repeated_identical_requests'] += int(repeated)
    bucket['http_requests'] += 1; bucket['repeated'] += int(repeated)
    for target in (p, bucket):
        target['latency_ms'] += detail.get('latency_ms', 0)
        target['throttled'] += int(detail.get('throttled', False))
        if 'requestedQueryCost' in detail and 'actualQueryCost' in detail:
            target['cost_observations'] += 1
            target['requested_cost'] += detail['requestedQueryCost']
            target['actual_cost'] += detail['actualQueryCost']


def retried():
    state = _scope.get()
    if state: state['profile']['retry_attempts'] += 1


def accumulate(target, source):
    for key, value in source.items():
        if isinstance(value, dict): accumulate(target.setdefault(key, {}), value)
        elif isinstance(value, (int, float)): target[key] = target.get(key, 0) + value
