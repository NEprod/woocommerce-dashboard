"""Existing scheduled task and publishing receipts with isolated Shopify doubles."""
import copy
import json

import pytest

from app import automatic_publishing as publisher, db, scheduled_tasks as tasks, shopify_drafts as drafts
from app.models import ScheduledTask, ShopifyProductIdentity, ShopifyPublishJob, User
from app.shopify_eu_identifier import KEY, MANUAL
from app.shopify_markets import MANUAL_REQUIRED, REMOVAL_REVIEW
from test_automatic_publishing import authorise, queued
from test_phase3_woo_publish_preview import preview_app
from test_shopify_drafts import world
from test_shopify_eu_identifier import ComplianceFixture
from test_shopify_markets import MarketAdapter, VariableMarketAdapter, connection, market, publication, retained, saved_state
from test_shopify_simple_updates import change
from test_shopify_variables import variable_world


class CombinedAdapter(ComplianceFixture, MarketAdapter):
    pass


class CombinedVariableAdapter(ComplianceFixture, VariableMarketAdapter):
    pass


@pytest.fixture(autouse=True)
def no_notifications(monkeypatch):
    monkeypatch.setattr('app.shopify_notifications.deliver', lambda *a, **kw: False)


def hourly(product, adapter):
    task = db.session.get(ScheduledTask, publisher.KEY)
    task.next_due = tasks.now()
    db.session.commit()
    run = tasks.enqueue(publisher.KEY)
    if not task.enabled:
        assert run is None
        return db.session.get(ShopifyPublishJob, product.id, populate_existing=True)
    assert tasks.claim('field-test') == (publisher.KEY, run)
    tasks.run_slice(publisher.KEY, run, 'field-test', adapter=adapter)
    return db.session.get(ShopifyPublishJob, product.id, populate_existing=True)


def test_hourly_market_add_readback_lag_then_adopted_removal(world, retained, monkeypatch):
    product, row, adapter = retained
    remote = copy.deepcopy(adapter.remote)
    authorise(adapter)
    change(world, shopify_market_ids=[market(2), market(3)])
    job = queued(product)
    # Old publisher's exact manual-only hold is picked up without editing again.
    job.state, job.error = 'paused', MANUAL_REQUIRED
    job.scan_operation_id = 'test-completed-scan'
    db.session.commit()
    original = adapter.market_context
    lag = [True]
    waits = []

    def context(config, product_id, countries):
        actual = original(config, product_id, countries)
        if adapter.market_writes and lag:
            actual[f'c{countries.index("DE")}'] = False
            lag.pop()
        return actual

    monkeypatch.setattr(adapter, 'market_context', context)
    monkeypatch.setattr('app.shopify_publication.sleep', waits.append)
    job = hourly(product, adapter)
    assert job.state == 'verified', job.error
    assert waits == [2]
    assert adapter.market_memberships == {publication(2), publication(3)}
    assert saved_state(product) == 'no_change'
    change(world, shopify_market_ids=[market(3)])
    queued(product)
    assert hourly(product, adapter).state == 'verified'
    assert adapter.market_writes == [
        (row.shopify_product_id, publication(2), True), (row.shopify_product_id, publication(2), False)]
    assert adapter.remote == remote and adapter.memberships == {'gid://shopify/Publication/1'}
    assert saved_state(product) == 'no_change'
    hourly(product, adapter)
    assert len(adapter.market_writes) == 2
    # An existing verified catalogue is still not authority to use a missing
    # local selection as an instruction to withdraw international listings.
    authored = json.loads(world[-1].read_text())
    authored.pop('shopify_market_ids')
    world[-1].write_text(json.dumps(authored))
    queued(product)
    assert hourly(product, adapter).error == REMOVAL_REVIEW
    assert adapter.market_memberships == {publication(3)} and len(adapter.market_writes) == 2


@pytest.mark.parametrize('kind,variable_world', [('simple', 'Single Variable'), ('variable', 'Single Variable')], indirect=['variable_world'])
def test_hourly_create_and_update_markets_metadata_and_y081(world, variable_world, kind):
    product, path = (world[0], world[-1]) if kind == 'simple' else variable_world[:2]
    adapter = CombinedAdapter() if kind == 'simple' else CombinedVariableAdapter()
    adapter.memberships.clear()

    def author(**values):
        doc = json.loads(path.read_text())
        doc.update(values)
        if doc.get(KEY) is None:
            doc.pop(KEY, None)
        path.write_text(json.dumps(doc))
        queued(product)

    authorise(adapter)
    author(live=True, shopify_channels={'online-store': True}, shopify_market_ids=[market(2)], **{KEY: True})
    assert hourly(product, adapter).state == 'verified'
    row = ShopifyProductIdentity.query.filter_by(product_id=product.id).one()
    remote = copy.deepcopy(adapter.remote)
    ids = {v['id'] for v in remote['variants']['nodes']}
    assert remote['status'] == 'ACTIVE' and adapter.enabled == ids
    assert adapter.market_writes == [(row.shopify_product_id, publication(2), True)]
    assert saved_state(product) == 'no_change'
    author(description='Reviewed description update', shopify_market_ids=[market(3)], **{KEY: False})
    assert hourly(product, adapter).state == 'verified'
    assert adapter.remote['id'] == remote['id']
    assert adapter.remote['descriptionHtml'] == 'Reviewed description update'
    assert adapter.remote['variants'] == remote['variants'] and adapter.remote['media'] == remote['media']
    assert adapter.memberships == {'gid://shopify/Publication/1'}
    assert adapter.market_writes[1:] == [
        (row.shopify_product_id, publication(2), False), (row.shopify_product_id, publication(3), True)]
    assert {v['id'] for v in adapter.writes[-1]['variants']} == ids
    assert all(v['compliance'][0]['value'] == 'false' for v in adapter.writes[-1]['variants'])
    assert not adapter.enabled and saved_state(product) == 'no_change'
    hourly(product, adapter)
    assert len(adapter.writes) == 2 and len(adapter.market_writes) == 3
    author(**{KEY: None})
    checked = len(adapter.requests)
    assert hourly(product, adapter).state == 'verified'
    assert len(adapter.requests) == checked and len(adapter.writes) == 2
    assert adapter.calls.count('product_create') == 1


def test_old_y081_hold_reopens_and_uncertain_result_never_replays(world):
    product = world[0]
    adapter = CombinedAdapter()
    adapter.memberships.clear()
    authorise(adapter)
    change(world, live=True, shopify_channels={'online-store': True})
    queued(product)
    assert hourly(product, adapter).state == 'verified'
    row = ShopifyProductIdentity.query.filter_by(product_id=product.id).one()
    change(world, **{KEY: True})
    job = queued(product)
    job.state, job.error = 'attention', MANUAL
    job.scan_operation_id = 'test-completed-scan'
    db.session.commit()
    adapter.problem = 'stale'
    job = hourly(product, adapter)
    assert job.state == 'recovery_required', job.error
    assert saved_state(product) == 'recovery_required' and len(adapter.writes) == 1
    adapter.problem = None
    assert hourly(product, adapter).state == 'recovery_required' and len(adapter.writes) == 1
    drafts.reconcile(row, adapter=adapter)
    assert saved_state(product) == 'no_change' and len(adapter.writes) == 1


def test_uncertain_scheduled_market_write_reconciles_without_replay(world, retained):
    product, row, adapter = retained
    authorise(adapter)
    change(world, shopify_market_ids=[market(2), market(3)])
    queued(product)
    adapter.ambiguous = publication(2)
    assert hourly(product, adapter).state == 'recovery_required'
    assert publication(2) in adapter.market_memberships and len(adapter.market_writes) == 1
    assert hourly(product, adapter).state == 'recovery_required'
    drafts.reconcile(row, adapter=adapter)
    assert saved_state(product) == 'no_change' and len(adapter.market_writes) == 1


@pytest.mark.parametrize('field', ['matching', 'markets', 'y081'])
def test_matching_fields_are_noop_and_sync_now_still_requires_review(world, field):
    product = world[0]
    adapter = CombinedAdapter()
    adapter.memberships.clear()
    authorise(adapter)
    change(world, live=True, shopify_channels={'online-store': True})
    queued(product)
    assert hourly(product, adapter).state == 'verified'
    row = ShopifyProductIdentity.query.filter_by(product_id=product.id).one()
    if field == 'matching':
        adapter.market_memberships.add(publication(2))
        adapter.enabled.add(drafts.evidence(row)['variant_id'])
        change(world, shopify_market_ids=[market(2)], **{KEY: True})
        queued(product)
        assert hourly(product, adapter).state == 'verified'
        assert saved_state(product) == 'no_change'
    else:
        change(world, **({'shopify_market_ids': [market(2)]} if field == 'markets' else {KEY: True}))
        queued(product).scan_operation_id = 'test-completed-scan'
        db.session.commit()
        publisher.sync_now(product, actor_id=User.query.filter_by(is_admin=True).first().id, acknowledged=True)
        job = hourly(product, adapter)
        assert job.error == (MANUAL_REQUIRED if field == 'markets' else MANUAL)
    assert not adapter.writes and not adapter.market_writes
    if field != 'matching':
        assert hourly(product, adapter).state == 'verified'
        assert len(adapter.market_writes) == (field == 'markets')
        assert len(adapter.writes) == (field == 'y081')


@pytest.mark.parametrize('reason', ['disabled', 'draft', 'catalogue', 'barcode'])
def test_scheduled_field_changes_respect_existing_protections(world, reason):
    product = world[0]
    adapter = CombinedAdapter()
    if reason != 'disabled':
        authorise(adapter)
    if reason == 'barcode':
        adapter.memberships.clear()
        change(world, live=True, shopify_channels={'online-store': True})
        queued(product)
        assert hourly(product, adapter).state == 'verified'
        row = ShopifyProductIdentity.query.filter_by(product_id=product.id).one()
        adapter.standardized.add(drafts.evidence(row)['variant_id'])
    elif reason == 'catalogue':
        adapter.market_rows[2]['catalogs'] = connection([])
    change(world, live=reason != 'draft', shopify_channels={'online-store': True},
           shopify_market_ids=[market(2)], **{KEY: True})
    job = queued(product)
    adapter.calls.clear()
    job = hourly(product, adapter)
    assert not adapter.writes and not adapter.market_writes
    assert 'product_create' not in adapter.calls
    if reason == 'catalogue':
        assert job.state == 'attention' and 'dedicated catalogue' in job.error
    elif reason == 'barcode':
        assert job.state == 'attention' and 'standardized identifier' in job.error, job.error
    else:
        assert job.state in {'pending', 'paused'} and not adapter.calls
