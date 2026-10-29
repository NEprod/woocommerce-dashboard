"""Read-only refresh execution is independent of authored product readiness."""
import json
import pytest
from datetime import timedelta

from app import db, shopify_drafts as drafts, shopify_status
from app.models import CatalogueOperation, ShopifyProductComparison
from test_phase3_woo_publish_preview import preview_app
from test_shopify_drafts import world
from test_shopify_publication_sales import AcceptanceAdapter
from test_shopify_simple_updates import change
from app import shopify_notifications as notices
from app.utils import discord
from test_shopify_variables import variable_world
from test_shopify_publication_sales import VariableAcceptanceAdapter


@pytest.mark.parametrize('definition,reason', [
    ({'storefront_enabled': True, 'proof_policy': 'none'}, 'no supported enabled fields'),
    ({'storefront_enabled': True, 'fields': {'photo': {'type': 'photo', 'label': 'Photo'}}}, 'secure upload service'),
])
def test_invalid_customer_configuration_is_specific_readiness_not_missing_source(world, definition, reason):
    check_customer_readiness(world, AcceptanceAdapter(), definition, reason)


def test_variable_customer_readiness_preserves_retained_identity(variable_world):
    check_customer_readiness(variable_world, VariableAcceptanceAdapter(),
        {'storefront_enabled': True, 'proof_policy': 'none'}, 'no supported enabled fields')


def check_customer_readiness(fixture, adapter, definition, reason):
    product = fixture[0]
    _, token = drafts.preview(product, adapter)
    identity = drafts.confirm(token, True, adapter)
    assert identity.state == 'verified'
    saved = ShopifyProductComparison.query.one()
    previous = json.loads(saved.summary_json).get('full_compared_at')
    source = fixture[2] if fixture[2].is_file() else fixture[1]
    source.write_text(json.dumps({**json.loads(source.read_text()), 'customisation': definition}))
    authored = source.read_bytes()
    adapter.calls.clear()
    result = shopify_status.refresh([product], force=True, adapter=adapter)
    assert not result['errors'], result
    assert any(f['code'] == 'customisation_configuration' and reason in f['message'] for f in result['findings'])
    assert CatalogueOperation.query.filter_by(operation_type='shopify_status_refresh').one().status == 'succeeded'
    retained = json.loads(saved.summary_json)
    assert retained.get('full_compared_at') == previous
    assert retained['refresh_failure']['kind'] == 'readiness'
    assert identity.state == 'verified' and source.read_bytes() == authored
    assert adapter.calls == ['connection_read']  # refresh's single shared connection check only
    from app.shopify_products import product_state
    from flask import current_app
    with current_app.test_request_context():
        state = product_state(product, [identity])
    assert state['state'] == 'attention' and reason in state['message']
    assert 'unavailable' not in state['message'].lower()
    from app.shopify_customisations import desired
    from app.shopify_connection import ShopifyReadinessFinding
    with pytest.raises(ShopifyReadinessFinding, match=reason):
        desired(product)
    # Correcting the owning JSON makes a subsequent real read conclusive again;
    # the old finding must not permanently override newer comparison evidence.
    source.write_text(json.dumps({**json.loads(source.read_text()), 'customisation': {'storefront_enabled': False}}))
    adapter.calls.clear()
    assert not shopify_status.refresh([product], force=True, adapter=adapter)['errors']
    retained = json.loads(saved.summary_json)
    assert retained['read_complete'] and not retained.get('refresh_failure')
    assert retained['full_compared_at'] != previous
    assert not any('write' in call or 'update' in call or 'create' in call for call in adapter.calls)


def test_actionable_conflict_alerts_once_resolves_and_reopens(world, monkeypatch):
    sent = []
    monkeypatch.setattr(discord, 'actionable_message', lambda summary: sent.append(summary) or (True, 'sent'))
    monkeypatch.setattr(discord, 'publishing_message', lambda summary: (_ for _ in ()).throw(AssertionError('Routine alert')))
    product = world[0]
    adapter = AcceptanceAdapter()
    _, token = drafts.preview(product, adapter)
    identity = drafts.confirm(token, True, adapter)
    assert not sent
    change(world, live=False, shopify_status='ARCHIVED', shopify_channels={'online-store': True})
    for _ in range(2):
        assert not shopify_status.refresh([product], force=True, adapter=adapter)['errors']
        db.session.expire_all()
    assert len(sent) == 1
    issue = sent[0]['issues'][0]
    assert issue['entity'] == product.sku and issue['severity'] == 'Action Required'
    assert 'Draft/Archived' in issue['cause'] and issue['path'] == f'/products/{product.id}'
    change(world, shopify_channels={'online-store': False})
    assert not shopify_status.refresh([product], force=True, adapter=adapter)['errors']
    assert len(sent) == 1
    change(world, shopify_channels={'online-store': True})
    shopify_status.refresh([product], force=True, adapter=adapter)
    assert len(sent) == 2
    assert identity.state == 'verified'  # no write/recovery identity was replaced
    assert CatalogueOperation.query.filter_by(operation_type='shopify_status_refresh').count() == 4


def test_execution_failure_alert_repeats_only_after_recovery(world, monkeypatch):
    sent = []
    monkeypatch.setattr(discord, 'actionable_message', lambda summary: sent.append(summary) or (True, 'sent'))
    product = world[0]
    adapter = AcceptanceAdapter()
    _, token = drafts.preview(product, adapter)
    drafts.confirm(token, True, adapter)
    change(world, live=True, shopify_status='ACTIVE', shopify_channels={'online-store': False})
    adapter.incomplete_channels = True
    for _ in range(2):
        assert shopify_status.refresh([product], force=True, adapter=adapter)['errors']
    assert len(sent) == 1
    adapter.incomplete_channels = False
    assert not shopify_status.refresh([product], force=True, adapter=adapter)['errors']
    adapter.incomplete_channels = True
    assert shopify_status.refresh([product], force=True, adapter=adapter)['errors']
    assert len(sent) == 2


def test_publication_conflict_is_not_failed_inspection(world):
    product = world[0]
    adapter = AcceptanceAdapter()
    _, token = drafts.preview(product, adapter)
    drafts.confirm(token, True, adapter)
    change(world, live=False, shopify_status='ARCHIVED', shopify_channels={'online-store': True})
    saved = ShopifyProductComparison.query.one()
    before = json.loads(saved.summary_json).get('full_compared_at')
    adapter.calls.clear()
    result = shopify_status.refresh([product], force=True, adapter=adapter)
    assert result['errors'] == []
    assert any(f['code'] == 'publication_configuration' for f in result['findings'])
    row = CatalogueOperation.query.filter_by(operation_type='shopify_status_refresh').one()
    assert row.status == 'succeeded'
    evidence = json.loads(saved.summary_json)
    assert evidence['read_complete'] is False
    assert evidence.get('full_compared_at') == before
    assert evidence['refresh_failure']['kind'] == 'authored_configuration'
    assert not any('write' in call or 'create' in call for call in adapter.calls)


def test_incomplete_channel_read_remains_execution_failure(world):
    product = world[0]
    adapter = AcceptanceAdapter()
    _, token = drafts.preview(product, adapter)
    drafts.confirm(token, True, adapter)
    change(world, live=True, shopify_status='ACTIVE', shopify_channels={'online-store': True})
    adapter.incomplete_channels = True
    result = shopify_status.refresh([product], force=True, adapter=adapter)
    assert result['errors']
    assert CatalogueOperation.query.filter_by(operation_type='shopify_status_refresh').one().status == 'partial'
    assert json.loads(ShopifyProductComparison.query.one().summary_json)['refresh_failure']['kind'] == 'incomplete_evidence'


def test_fifteen_product_scheduled_refresh_with_live_shaped_findings(world, monkeypatch):
    """268 is a simulated request-accounting fixture, not a live API result."""
    from app import scheduled_tasks as tasks, shopify_capacity as capacity
    from app.models import Product, ScheduledTask, ScheduledTaskRun
    from app.shopify_connection import ShopifyReadinessFinding
    Product.query.update({Product.catalogue_status: 'missing'})
    products = [Product(sku=sku, title=sku, product_type='simple', catalogue_status='active', source_relpath=f'fixture/{sku}')
        for sku in ['16bit-0003', '3DCO-0001', 'CURRENT-1', 'CURRENT-2', 'UPDATE-1', 'UPDATE-2', 'UPDATE-3',
                    'ADOC', 'NBNOC', 'LCBC', 'NEW-4', 'NEW-5', 'NEW-6', 'NEW-7', 'NEW-8']]
    db.session.add_all(products); db.session.commit()
    states = {p.id: {'state': 'unavailable' if p.sku == '16bit-0003' else 'no_change' if p.sku.startswith('CURRENT') else
        'update' if p.sku.startswith('UPDATE') else 'attention' if p.sku == '3DCO-0001' else 'create',
        'label': 'Up to Date' if p.sku.startswith('CURRENT') else 'Publishing prerequisite needed',
        'message': 'Publishing prerequisite needed', 'blockers': []} for p in products}
    monkeypatch.setattr('app.shopify_products.states_for', lambda rows: {p.id: states[p.id] for p in rows})
    monkeypatch.setattr('app.shopify_comparison.current', lambda *a, **k: None)
    monkeypatch.setattr('app.taxonomy_assignments.product_document', lambda product: {'live': True})
    sent = []
    monkeypatch.setattr(discord, 'actionable_message', lambda summary: sent.append(summary) or (True, 'sent'))
    def inspect(product, adapter, **kwargs):
        capacity.background()['requests'] += 16 if product.sku == '16bit-0003' else 18
        if product.sku == '3DCO-0001':
            raise ShopifyReadinessFinding('Gallery exceeds the supported publication bound: 53 images; limit 20. No images are truncated or removed.', code='gallery_bound')
        data = {'config': drafts.configuration().fingerprint(), 'read_complete': product.sku != '16bit-0003',
            'classification': 'no_change' if product.sku.startswith('CURRENT') else 'update' if product.sku.startswith('UPDATE') else 'create'}
        if product.sku == '16bit-0003':
            data['refresh_failure'] = {'kind': 'authored_configuration',
                'message': 'Required managed evidence is incomplete: publication: Draft/Archived intent cannot request channel publication.'}
        db.session.add(ShopifyProductComparison(store_id='gid://shopify/Shop/42', source_identity=product.source_relpath,
            compared_at=drafts.now(), summary_json=json.dumps(data)))
        db.session.commit()
    monkeypatch.setattr(drafts, 'preview', inspect)
    monkeypatch.setattr(tasks, 'FULL_PER_SLICE', 5)
    run_id = tasks.enqueue(manual=True, product_ids=[p.id for p in products])
    for _ in range(3):
        task = db.session.get(ScheduledTask, tasks.KEY)
        task.resume_at = tasks.now()-timedelta(seconds=1); db.session.commit()
        assert tasks.claim('owner') == (tasks.KEY, run_id)
        tasks.run_slice(tasks.KEY, run_id, 'owner', adapter=AcceptanceAdapter())
    run = db.session.get(ScheduledTaskRun, run_id)
    data = json.loads(run.data_json)
    assert run.state == 'completed' and not data['queue'] and not data.get('errors'), data
    assert data['counts'] == {'considered': 15, 'checked': 15, 'deferred': 0, 'skipped_recent': 0,
        'throttled': 0, 'unavailable': 1, 'up_to_date': 2, 'updates': 3, 'attention': 9, 'api_requests': 268}
    assert sum(s['total'] for s in sent) == 2  # conflict and intended-publication gallery blocker only
    receipts = CatalogueOperation.query.filter_by(operation_type='shopify_status_refresh').all()
    assert all(r.status == 'succeeded' for r in receipts)
    assert all(not json.loads(r.scope)['operation_summary']['errors'] for r in receipts)
    conflict = ShopifyProductComparison.query.filter_by(source_identity=products[0].source_relpath).one()
    assert not json.loads(conflict.summary_json)['read_complete']
    assert not json.loads(conflict.summary_json).get('full_compared_at')


def test_probe_separates_stored_partial_from_conclusive_evidence():
    from types import SimpleNamespace
    from app.operations_probe import execution_evidence
    row = SimpleNamespace(id='retained', operation_type='shopify_status_refresh', status='partial',
        products_failed=0, marker_state='not_started', recovery_state='none', error=None,
        scope=json.dumps({'operation_summary': {'findings': [{'code': 'reconciliation'}]}}))
    assert execution_evidence(row)['evidence_class'] == 'insufficient_retained_execution_evidence'
    row.scope = json.dumps({'operation_summary': {'execution_complete': True, 'findings': [{'code': 'reconciliation'}]}})
    evidence = execution_evidence(row)
    assert evidence['stored_status'] == 'partial' and evidence['derived_execution'] == 'succeeded'
    row.recovery_state = 'review_required'
    assert execution_evidence(row)['evidence_class'] == 'protected_recovery'
    row.error = 'Do not export private details: customer@example.invalid'
    assert 'customer@' not in json.dumps(execution_evidence(row))


def test_action_alert_has_safe_correction_link(monkeypatch):
    sent = []
    monkeypatch.setenv('DASHBOARD_BASE_URL', 'https://tlc.private.example')
    monkeypatch.setattr(discord, 'send_discord_message', lambda **kwargs: sent.append(kwargs) or (True, 'sent'))
    summary = {'total': 1, 'issues': [{'entity': '16bit-0003', 'severity': 'Action Required',
        'cause': 'Draft/Archived intent cannot request channel publication.',
        'action': 'Review product publishing settings.', 'path': '/products/3'}]}
    assert discord.actionable_message(summary) == (True, 'sent')
    assert '[Open TLCDashboard](https://tlc.private.example/products/3)' in json.dumps(sent)
    assert sent[0]['channels'] == ['scans_errors'] and sent[0]['single_attempt']


def test_readonly_probe_dashboard_matches_real_projection(world):
    from pathlib import Path
    from app.operations_probe import dashboard_snapshot
    from app.dashboard import build_dashboard_data
    from flask import current_app
    count = CatalogueOperation.query.count()
    database = Path(db.engine.url.database)
    with current_app.test_request_context('/'):
        expected = build_dashboard_data()
    result = dashboard_snapshot(database)
    assert result['local_catalogue'] == expected['health']
    assert result['active_issue_counts'] == expected['issue_data']['counts']
    assert CatalogueOperation.query.count() == count


def test_legacy_pending_success_notice_is_suppressed_not_delivery_failed(world, monkeypatch):
    adapter = AcceptanceAdapter()
    _, token = drafts.preview(world[0], adapter)
    identity = drafts.confirm(token, True, adapter)
    operation = db.session.get(CatalogueOperation, identity.operation_id)
    scope = notices.scope_of(operation)
    scope[notices.KEY]['verified_success']['state'] = 'pending'  # pre-policy durable event
    operation.scope = json.dumps(scope); db.session.commit()
    monkeypatch.setattr(discord, 'send_discord_message', lambda **kwargs: (_ for _ in ()).throw(AssertionError('Routine Discord request')))
    assert notices.deliver(operation.id, 'verified_success') is False
    assert notices.events_view(operation)[0]['state'] == 'suppressed'
    assert not notices.events_view(operation)[0]['retryable']
