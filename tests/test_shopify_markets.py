"""Isolated source/scanner and signed Review & Sync with fake Shopify Markets."""
import copy
import json
import time

import pytest

from app import db, shopify_drafts as drafts
from app.models import CatalogueOperation, Product, ShopifyProductComparison, ShopifyPublishJob
from app.shopify_connection import ShopifyError
from app.shopify_products import source_fingerprint, states_for
from app.taxonomy_assignments import product_document
from test_metadata_editor_core import core, save
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_drafts import world
from test_shopify_publication_sales import AcceptanceAdapter, VariableAcceptanceAdapter
from test_shopify_simple_updates import change
from test_shopify_variables import variable_world


def saved_state(product):
    return states_for([product], fresh=True, url_builder=lambda *a, **k: None)[product.id]['state']


def market(n):
    return f'gid://shopify/Market/{n}'


def publication(n):
    return f'gid://shopify/Publication/{100 + n}'


def connection(nodes):
    return {'nodes': nodes, 'pageInfo': {'hasNextPage': False}}


def market_record(n, name, codes):
    catalog = f'gid://shopify/MarketCatalog/{n}'
    return {'id': market(n), 'name': name, 'type': 'REGION', 'status': 'ACTIVE',
            'parentMarkets': connection([]),
            'conditions': {'regionsCondition': {'regions': connection([{'code': code} for code in codes])}},
            'catalogs': connection([{'id': catalog, 'status': 'ACTIVE',
                'publication': {'id': publication(n), 'autoPublish': False, 'catalog': {'id': catalog}}}])}


class MarketMixin:
    def __init__(self):
        super().__init__()
        self.market_rows = [market_record(1, 'United Kingdom', ['GB']),
                            market_record(2, 'Europe', ['FR', 'DE']),
                            market_record(3, 'United States', ['US'])]
        self.market_memberships = set()
        self.memberships = {'gid://shopify/Publication/1'}
        self.market_writes = []
        self.market_scope = True
        self.publication_write_scope = True
        self.ambiguous = None
        self.context_mismatch = False

    def test_connection(self, config):
        result = super().test_connection(config)
        if self.market_scope:
            result['scopes'].append('read_markets')
        if not self.publication_write_scope:
            result['scopes'].remove('write_publications')
        return result

    def markets(self, config):
        self.calls.append('market_discovery')
        return copy.deepcopy(self.market_rows)

    def market_publication_state(self, config, product_id):
        self.calls.append('market_read')
        assert product_id == self.remote['id']
        return {**{key: self.remote[key] for key in ('id', 'status', 'metafield')},
                'resourcePublicationsV2': connection([{'publication': {'id': pid}, 'isPublished': True}
                                                       for pid in sorted(self.market_memberships)])}

    def market_context(self, config, product_id, countries):
        assert product_id == self.remote['id']
        self.calls.append('market_context')
        membership = {'GB': publication(1), 'FR': publication(2), 'DE': publication(2), 'US': publication(3)}
        result = {key: self.remote[key] for key in ('id', 'status', 'metafield')}
        result.update({f'c{i}': (membership[code] in self.market_memberships) != self.context_mismatch
                       for i, code in enumerate(countries)})
        return result

    def set_publication(self, config, product_id, pid, publish):
        assert product_id == self.remote['id'] and pid in {publication(1), publication(2), publication(3)}
        self.durable('publication_update')
        self.market_writes.append((product_id, pid, publish))
        (self.market_memberships.add if publish else self.market_memberships.discard)(pid)
        if pid == self.ambiguous:
            raise ShopifyError('Market publication response lost.')
        return {('publishablePublish' if publish else 'publishableUnpublish'): {'userErrors': []}}


class MarketAdapter(MarketMixin, AcceptanceAdapter):
    pass


class VariableMarketAdapter(MarketMixin, VariableAcceptanceAdapter):
    pass


@pytest.fixture
def retained(world, monkeypatch):
    monkeypatch.setattr('app.shopify_notifications.deliver', lambda *a, **k: False)
    product = world[0]
    adapter = MarketAdapter()
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    assert row.state == 'verified'
    assert not any('market' in call for call in adapter.calls)
    change(world, live=True)
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    assert row.state == 'verified' and adapter.remote['status'] == 'ACTIVE'
    adapter.calls.clear()
    adapter.market_memberships = {publication(1), publication(3)}
    return product, row, adapter


def test_signed_market_only_update_and_readback(world, retained):
    product, row, adapter = retained
    remote = copy.deepcopy(adapter.remote)
    change(world, shopify_market_ids=[market(2), market(1)])
    plan, token = drafts.preview(product, adapter)
    assert token and plan['classification'] == 'update'
    assert [(r['before'], r['after']) for r in plan['publication']['markets']['fields']] == [(True, True), (False, True), (True, False)]
    assert not adapter.market_writes and 'product_create' not in adapter.calls
    assert saved_state(product) == 'update'
    _, same_token = drafts.preview(product, adapter)
    assert drafts.serializer().loads(same_token) == drafts.serializer().loads(token)
    row = drafts.confirm(token, True, adapter)
    assert row.state == 'verified' and row.shopify_product_id == remote['id']
    assert adapter.market_writes == [(remote['id'], publication(3), False), (remote['id'], publication(2), True)]
    assert adapter.remote == remote
    assert adapter.memberships == {'gid://shopify/Publication/1'}
    assert drafts.evidence(row)['publication_update']['market_observed']['memberships'] == {
        publication(1): True, publication(2): True, publication(3): False}
    current = json.loads(ShopifyProductComparison.query.one().summary_json)
    assert current['classification'] == 'no_change' and current['read_complete']
    assert saved_state(product) == 'no_change'
    changed = source_fingerprint(product, [])
    change(world, shopify_market_ids=[market(1), market(2)])
    assert source_fingerprint(product, []) == changed
    assert drafts.preview(product, adapter)[1] is None
    assert len(adapter.market_writes) == 2


def test_discovery_and_review_http_are_read_only(preview_app, world, retained, monkeypatch):
    product, _, adapter = retained
    client = _client(preview_app)
    monkeypatch.setattr('app.shopify_connection.ShopifyAdapter', lambda: adapter)
    monkeypatch.setattr(drafts, 'ShopifyAdapter', lambda: adapter)
    discover = client.post('/shopify/markets/discover')
    assert discover.status_code == 200
    assert discover.json['markets'] == [
        {'id': market(1), 'name': 'United Kingdom', 'active': True},
        {'id': market(2), 'name': 'Europe', 'active': True},
        {'id': market(3), 'name': 'United States', 'active': True},
    ]
    change(world, shopify_market_ids=[market(1), market(2)])
    response = client.post(f'/products/{product.id}/shopify/review', data={'action': 'preview'})
    assert response.status_code == 200
    for text in (b'data-market-review', b'United Kingdom \xe2\x80\x94 Already matching',
                 b'Europe \xe2\x80\x94 Add', b'United States \xe2\x80\x94 Remove'):
        assert text in response.data
    adapter.market_scope = False
    failure = client.post('/shopify/markets/discover')
    assert failure.status_code == 400 and 'read_markets' in failure.json['error']
    assert not adapter.market_writes


@pytest.mark.parametrize('problem,reason', [
    ('missing_scope', 'read_markets'), ('shared', 'shared'), ('inheritance', 'inherited'),
    ('auto', 'automatic product inclusion'), ('missing_publication', 'dedicated catalogue'),
    ('unknown', 'missing, inactive'), ('context', 'buyer availability differ'),
])
def test_uncertain_or_unsafe_topology_never_authorises_market_writes(world, retained, problem, reason):
    product, row, adapter = retained
    selected = [market(1), market(2)]
    catalog = adapter.market_rows[1]['catalogs']['nodes'][0]
    if problem == 'missing_scope':
        adapter.market_scope = False
    elif problem == 'shared':
        catalog['publication']['id'] = publication(1)
    elif problem == 'inheritance':
        adapter.market_rows[1]['parentMarkets'] = connection([{'id': market(1)}])
    elif problem == 'auto':
        catalog['publication']['autoPublish'] = True
    elif problem == 'missing_publication':
        catalog['publication'] = None
    elif problem == 'unknown':
        selected = [market(99)]
    else:
        adapter.context_mismatch = True
    change(world, shopify_market_ids=selected)
    plan, token = drafts.preview(product, adapter)
    assert token is None and not plan['publication']['read_complete']
    assert reason in plan['publication']['markets']['reason']
    assert not adapter.market_writes
    assert row.state == 'verified'
    assert saved_state(product) == (
        'unavailable' if problem == 'missing_scope' else 'attention')


def test_write_scope_required_only_for_changes(world, retained):
    product, _, adapter = retained
    adapter.publication_write_scope = False
    change(world, shopify_market_ids=[market(2)])
    plan, token = drafts.preview(product, adapter)
    assert token is None and plan['publication']['read_complete']
    assert any('write_publications' in b for b in plan['publication']['blockers'])
    change(world, shopify_market_ids=[market(1), market(3)])
    plan, token = drafts.preview(product, adapter)
    assert token is None and plan['classification'] == 'no_change'
    assert not plan['publication']['blockers']


def test_topology_change_invalidates_signed_review_before_writes(world, retained):
    product, row, adapter = retained
    change(world, shopify_market_ids=[market(2)])
    _, token = drafts.preview(product, adapter)
    adapter.market_rows[2]['catalogs']['nodes'][0]['publication']['autoPublish'] = True
    with pytest.raises(ShopifyError, match='changed since review'):
        drafts.confirm(token, True, adapter)
    assert row.state == 'verified' and not adapter.market_writes


def test_partial_market_write_retains_uncertainty_without_replay(world, retained):
    product, row, adapter = retained
    change(world, shopify_market_ids=[market(1), market(2)])
    _, token = drafts.preview(product, adapter)
    adapter.ambiguous = publication(2)
    row = drafts.confirm(token, True, adapter)
    proof = drafts.evidence(row)['publication_update']
    assert row.state == 'uncertain'
    assert proof['completed'] == ['publication.' + publication(3)]
    assert proof['attempted'] == 'publication.' + publication(2)
    with pytest.raises(ShopifyError, match='Recovery required'):
        drafts.preview(product, adapter)
    assert len(adapter.market_writes) == 2
    drafts.reconcile(row, adapter=adapter)
    assert row.state == 'verified'
    assert len(adapter.market_writes) == 2


def test_hourly_publisher_cannot_approve_markets(world, retained):
    from app import automatic_publishing as publisher, scheduled_tasks as tasks
    from app.models import ScheduledTaskRun
    from test_automatic_publishing import authorise, queued
    product, row, adapter = retained
    before = row.operation_id
    change(world, shopify_market_ids=[market(1), market(2)])
    authorise(adapter)
    queued(product)
    run = tasks.enqueue(publisher.KEY)
    assert tasks.claim('markets-fixture') == (publisher.KEY, run)
    tasks.run_slice(publisher.KEY, run, 'markets-fixture', adapter=adapter)
    job = db.session.get(ShopifyPublishJob, product.id)
    assert db.session.get(ScheduledTaskRun, run).state == 'completed'
    assert job.state == 'paused' and 'explicit Review & Sync' in job.error
    assert row.operation_id == before and not adapter.market_writes
    assert saved_state(product) == 'update'
    _, token = drafts.preview(product, adapter)
    drafts.confirm(token, True, adapter)
    assert job.state == 'verified' and saved_state(product) == 'no_change'


def test_draft_parks_markets_until_same_identity_activates(world, monkeypatch):
    monkeypatch.setattr('app.shopify_notifications.deliver', lambda *a, **k: False)
    product, _, _ = world
    adapter = MarketAdapter()
    change(world, shopify_market_ids=[market(1)])
    plan, token = drafts.preview(product, adapter)
    assert not plan.get('publication')
    row = drafts.confirm(token, True, adapter)
    remote_id = row.shopify_product_id
    adapter.calls.clear()
    assert drafts.preview(product, adapter)[0]['classification'] == 'no_change'
    assert not adapter.calls.count('market_discovery') and not adapter.market_writes
    change(world, live=True)
    plan, token = drafts.preview(product, adapter)
    assert plan['publication']['markets']['fields'][0]['changed']
    row = drafts.confirm(token, True, adapter)
    assert row.state == 'verified' and row.shopify_product_id == remote_id
    assert adapter.remote['status'] == 'ACTIVE' and 'product_create' not in adapter.calls
    assert adapter.market_writes == [(remote_id, publication(1), True)]


def test_variable_market_create_then_update_keeps_variants(variable_world, monkeypatch):
    monkeypatch.setattr('app.shopify_notifications.deliver', lambda *a, **k: False)
    product, path, *_ = variable_world
    adapter = VariableMarketAdapter()
    adapter.memberships.clear()  # No unreviewed channels are assigned on new drafts.
    source = json.loads(path.read_text())
    path.write_text(json.dumps({**source, 'live': True, 'shopify_market_ids': [market(1)]}))
    plan, token = drafts.preview(product, adapter)
    assert token and plan['classification'] == 'create'
    row = drafts.confirm(token, True, adapter)
    assert row.state == 'verified' and adapter.remote['status'] == 'ACTIVE'
    remote_id = row.shopify_product_id
    variants = copy.deepcopy(adapter.remote['variants'])
    path.write_text(json.dumps({**source, 'live': True, 'shopify_market_ids': [market(2)]}))
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    assert row.state == 'verified' and row.shopify_product_id == remote_id
    assert adapter.remote['variants'] == variants
    assert adapter.market_memberships == {publication(2)}
    assert adapter.calls.count('product_create') == 1


def test_collection_editor_save_reuses_scoped_scan_and_inherits(core, monkeypatch):
    from app.utils import scan_runner
    app, client, root, _, ids, *_ = core
    name = 'Simple Collection/Blue Token'
    path = root/'Simple Collection/product_info.json'
    unrelated_path = root/'Variable Collection/product_info.json'
    unrelated = unrelated_path.read_bytes()
    scanned = []
    scan = scan_runner.scan_collection
    def record_scan(folder, *args, **kwargs):
        scanned.append(folder)
        return scan(folder, *args, **kwargs)
    monkeypatch.setattr(scan_runner, 'scan_collection', record_scan)
    monkeypatch.setattr('app.routes.start_scan', scan_runner.start_scan)
    monkeypatch.setattr('app.shopify_connection.ShopifyAdapter._transport',
                        lambda *a, **k: pytest.fail('Save or scan contacted Shopify'))
    before = json.loads(path.read_text())
    response = save(core, name, kind='shared', data={'shopify_market_ids': [market(2), market(1)]})
    assert response.status_code == 200, response.json
    deadline = time.monotonic()+15
    while time.monotonic() < deadline:
        with app.app_context():
            progress = scan_runner.get_progress(response.json['run_id'])
        if progress['status'] in {'done', 'error'}:
            break
        time.sleep(.03)
    assert progress['status'] == 'done', progress
    assert scanned == [str(path.parent)]
    assert json.loads(path.read_text()) == {**before, 'shopify_market_ids': [market(2), market(1)]}
    assert unrelated_path.read_bytes() == unrelated
    with app.app_context():
        product = db.session.get(Product, ids[name])
        assert product_document(product)['shopify_market_ids'] == [market(2), market(1)]
        job = db.session.get(ShopifyPublishJob, product.id)
        assert json.loads(job.view_json)['authored']['shopify_market_ids'] == [market(2), market(1)]
        assert job.scan_operation_id and job.requested_revision != 'unscanned'
        assert db.session.get(CatalogueOperation, job.scan_operation_id).status == 'succeeded'
        assert 'shopify_market_ids' not in product_document(db.session.get(Product, ids['Variable Collection/Badge One']))
    response = client.get(f'/edit_products/{ids[name]}/edit/shared')
    assert response.status_code == 200 and b'data-market-editor' in response.data and b'data-discover-channels' in response.data
    response = client.get(f'/edit_products/{ids[name]}/edit/override')
    assert response.status_code == 200 and b'data-market-editor' not in response.data


def test_schema_keeps_unspecified_distinct_and_rejects_empty_or_overrides():
    from app.product_info import validate_product_info
    from app.utils.json_utils import merge_product_json
    source = {'collection_type': 'Simple', 'sku_prefix': 'TLC', 'title': 'Card'}
    assert validate_product_info(source, 'collection').valid
    for value in ([], None, ['United Kingdom'], [market(1), market(1)]):
        assert not validate_product_info({**source, 'shopify_market_ids': value}, 'collection').valid
    assert not validate_product_info({'shopify_market_ids': [market(1)]}, 'override').valid
    assert merge_product_json({'shopify_market_ids': [market(1)]}, {'shopify_market_ids': [market(2)]})['shopify_market_ids'] == [market(1)]
