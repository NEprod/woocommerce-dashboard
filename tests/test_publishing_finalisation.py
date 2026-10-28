"""28 October receipt shapes; disposable sources and fake Shopify only."""
import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from flask import current_app
from app import db, automatic_publishing as publisher, shopify_drafts as drafts
from app.models import CatalogueOperation, Product, ShopifyProductComparison, ShopifyPublishingEvent, ShopifyProductIdentity
from app.shopify_notifications import product_result, operation_facts
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_drafts import world
from test_shopify_simple_updates import change
from test_shopify_variables import variable_world, rescan
from test_shopify_publication_sales import AcceptanceAdapter, VariableAcceptanceAdapter, SafetyMixin
from test_shopify_native_dimensions import NativeAdapter
from test_shopify_package_assignments import existing, linked
from test_automatic_publishing import authorise, execute, queued, notices


class NativeAcceptance(SafetyMixin, NativeAdapter):
    pass


def test_lcbc_verified_live_create_with_unconfigured_native_is_succeeded(world, monkeypatch, notices):
    product = world[0]
    monkeypatch.setenv('SHOPIFY_NATIVE_DIMENSIONS_ENABLED', 'true')
    change(world, live=True)
    adapter = NativeAcceptance(); authorise(adapter)
    assert execute(product, adapter) == 'verified'
    row = ShopifyProductIdentity.query.one()
    op = db.session.get(CatalogueOperation, row.operation_id)
    assert op.status == 'succeeded' and op.recovery_state == 'none'
    assert json.loads(op.scope)['operation_summary']['native_dimensions'] == 'unconfigured'
    assert adapter.remote['status'] == 'ACTIVE' and adapter.calls.count('product_create') == 1
    assert 'native_write' not in adapter.calls
    assert product_result(op)['operation'] == 'Created'  # activation is not a second update
    assert product_result(op)['outcome'] == 'verified_success'
    assert json.loads(ShopifyPublishingEvent.query.one().receipt_json)['mode'] == 'create'
    assert len(notices) == 1


def test_variable_unconfigured_native_remains_successful(variable_world, monkeypatch):
    monkeypatch.setenv('SHOPIFY_NATIVE_DIMENSIONS_ENABLED', 'true')
    adapter = VariableAcceptanceAdapter()
    _, token = drafts.preview(variable_world[0], adapter)
    row = drafts.confirm(token, True, adapter)
    assert row.state == 'verified'
    op = db.session.get(CatalogueOperation, row.operation_id)
    assert op.status == 'succeeded' and 'native_write' not in adapter.calls
    assert product_result(op)['operation'] == 'Created'


def test_variable_update_is_not_labelled_as_its_historical_create(variable_world):
    adapter = VariableAcceptanceAdapter(); product = variable_world[0]
    _, token = drafts.preview(product, adapter); row = drafts.confirm(token, True, adapter)
    assert row.state == 'verified'
    rescan(variable_world, title='A separately updated variable product')
    _, token = drafts.preview(product, adapter); drafts.confirm(token, True, adapter)
    op = db.session.get(CatalogueOperation, row.operation_id)
    assert op.status == 'succeeded' and product_result(op)['operation'] == 'Updated'
    from app.publishing_progress import view
    assert view(op)['publishing']['product_create_attempted'] is False
    assert adapter.calls.count('product_create') == 1


@pytest.mark.parametrize('native_state', ['blocked', 'pending', 'uncertain'])
def test_configured_native_outstanding_is_not_succeeded(world, monkeypatch, native_state):
    change(world, packaging={'packed_dimensions_cm': {'length': '15', 'width': '10', 'height': '1'}, 'measured': True})
    adapter = NativeAcceptance()
    monkeypatch.setenv('SHOPIFY_NATIVE_DIMENSIONS_ENABLED', 'true')
    _, token = drafts.preview(world[0], adapter)
    with patch('app.shopify_native_dimensions.after_publish', return_value=native_state):
        row = drafts.confirm(token, True, adapter)
    assert db.session.get(CatalogueOperation, row.operation_id).status == 'partial'


def legacy_draft(world):
    product = world[0]; adapter = AcceptanceAdapter(); authorise(adapter)
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    data = drafts.evidence(row)
    data.pop('observed_status', None)
    data['verification'].pop('attributes', None)
    drafts.save(row, data)
    change(world, title='Draft metadata awaiting future Live activation')
    job = queued(product)
    # A subsequent genuine full comparison, against the retained legacy identity.
    drafts.preview(product, adapter)
    comparison = ShopifyProductComparison.query.one()
    assert json.loads(comparison.summary_json)['actual_status'] == 'DRAFT'
    adapter.calls.clear()
    return product, row, job, adapter, comparison


def test_lcbc_linked_draft_uses_current_bound_comparison_without_preflight(world, notices):
    product, row, job, adapter, comparison = legacy_draft(world)
    original = row.media_evidence_json, comparison.summary_json, comparison.compared_at
    op_count = CatalogueOperation.query.count(); notices.clear()
    with patch('app.shopify_publication.preview', side_effect=AssertionError('No publication preflight for an observed Draft')):
        assert publisher.publish_one(job, owner='fixture', guard=lambda: None, fence=lambda: None, adapter=adapter) == 'paused'
    assert not adapter.calls and not notices
    assert job.verified_revision != job.requested_revision  # accumulated metadata is not matching
    assert (row.media_evidence_json, comparison.summary_json, comparison.compared_at) == original
    assert CatalogueOperation.query.count() == op_count
    # The real mutation guard is still intact. A legacy receipt needs a fresh
    # explicit verification before activation; it was not upgraded by the no-op.
    from app.shopify_notifications import complete
    assert not complete(row, drafts.evidence(row))


@pytest.mark.parametrize('defect', ['stale', 'identity', 'source', 'partial', 'recovery'])
def test_draft_shortcut_rejects_unbound_or_incomplete_evidence(world, defect):
    product, row, job, adapter, comparison = legacy_draft(world)
    data = json.loads(comparison.summary_json)
    if defect == 'stale':
        comparison.compared_at -= timedelta(days=1)
        for key in ('full_compared_at', 'revision_checked_at', 'related_checked_at'):
            data[key] = comparison.compared_at.isoformat()
    elif defect == 'identity': data['remote_id'] = 'gid://shopify/Product/999'
    elif defect == 'source': data['local'] = 'another-revision'
    elif defect == 'partial': data['read_complete'] = False
    else: db.session.get(CatalogueOperation, row.operation_id).recovery_state = 'review_required'
    comparison.summary_json = json.dumps(data); db.session.commit()
    assert not publisher.current_draft_comparison(product, row)
    assert not adapter.calls


def test_previous_package_ack_is_historical_not_current_update_outcome(world):
    product, row, adapter = existing(world); package = linked()
    change(world, packaging={'selected_package_id': package.id})
    _, token = drafts.preview(product, adapter); drafts.confirm(token, True, adapter)
    old_id = row.operation_id
    assert product_result(db.session.get(CatalogueOperation, old_id))['outcome'] == 'shopify_accepted'
    old_receipt = db.session.get(CatalogueOperation, old_id).scope
    writes = len([c for c in adapter.calls if isinstance(c, tuple)])
    change(world, title='A later metadata-only update')
    _, token = drafts.preview(product, adapter); drafts.confirm(token, True, adapter)
    op = db.session.get(CatalogueOperation, row.operation_id)
    result = product_result(op)
    assert result['outcome'] == 'verified_success' and result['operation'] == 'Updated'
    assert 'Historical package acknowledgement' in result['historical_evidence_note']
    assert 'evidence_note' not in result and not result['deferred']
    assert len([c for c in adapter.calls if isinstance(c, tuple)]) == writes
    assert db.session.get(CatalogueOperation, old_id).scope == old_receipt


def test_verified_product_then_package_preflight_exception_retains_safe_diagnostic(world, monkeypatch):
    product, row, adapter = existing(world); package = linked()
    authorise(adapter)
    change(world, packaging={'selected_package_id': package.id}, title='Verified before package failure')
    _, token = drafts.preview(product, adapter)
    def fail(*args): raise KeyError('config_digest')
    with patch('app.shopify_package_assignments.apply', side_effect=fail):
        drafts.confirm(token, True, adapter)
    op = db.session.get(CatalogueOperation, row.operation_id)
    assert row.state == 'uncertain' and op.recovery_state == 'review_required'
    data = drafts.evidence(row); result = product_result(op)
    assert data['verification']['fields'] == data['verification']['media'] == 'verified'
    assert data['failure_diagnostic'] == {'operation_id': op.id, 'category': 'KeyError',
        'stage': 'package_preflight', 'reason': 'Required internal state key is missing. Key: config_digest'}
    assert result['outcome'] == 'recovery_required' and result['stage'] == 'package_preflight'
    assert 'no assignment attempt or acknowledgement retained' in ' '.join(result['deferred'])
    assert not data.get('package_assignments')
    from app.publishing_progress import view
    progress = view(op)
    assert progress['status'] == 'recovery_required' and progress['stage'] == 'package_preflight'
    assert progress['publishing']['product_create_attempted'] is False
    event = ShopifyPublishingEvent.query.filter_by(operation_id=op.id).one()
    receipt = json.loads(event.receipt_json)
    assert any(c['field'] == 'title' for c in receipt['changes'])
    assert not any(c['field'].startswith('package_assignment.') for c in receipt['changes'])
    page = _client(current_app).get('/operations/events/' + op.id).get_data(as_text=True)
    assert 'Verified in this operation' in page and 'KeyError at package_preflight' in page
    calls = list(adapter.calls)
    identity_id, operation_id, product_id = row.id, op.id, product.id
    retained = row.media_evidence_json, op.scope, op.recovery_state
    db.session.remove(); db.engine.dispose()
    row = db.session.get(ShopifyProductIdentity, identity_id)
    op = db.session.get(CatalogueOperation, operation_id)
    product = db.session.get(Product, product_id)
    assert (row.media_evidence_json, op.scope, op.recovery_state) == retained
    assert execute(product, adapter) == 'recovery_required'
    assert adapter.calls == calls  # no automatic package replay or second ProductCreate


@pytest.mark.parametrize('error', [RuntimeError('customer@example.test https://upload.invalid/signed?token=secret'),
    ValueError('{"customer_answer":"private-personalisation","password":"secret"}'),
    KeyError('private-customer-answer')])
def test_exception_diagnostics_never_persist_arbitrary_payload(error):
    result = json.dumps(drafts.failure_diagnostic(error, 'package_preflight', 'fixture-operation'))
    for private in ('customer@example', 'upload.invalid', 'secret', 'private-personalisation', 'private-customer-answer'):
        assert private not in result
    assert type(error).__name__ in result and 'package_preflight' in result


def test_historical_verification_does_not_prove_current_applied_changes(world):
    product, row, adapter = existing(world)
    old = db.session.get(CatalogueOperation, row.operation_id)
    new = CatalogueOperation(id='new-with-old-evidence', operation_type='shopify_draft_publish',
        status='partial', recovery_state='review_required', started_at=drafts.now(), finished_at=drafts.now(),
        scope=json.dumps({'product_id': product.id, 'mode': 'update'}))
    db.session.add(new); row.operation_id = new.id; db.session.commit()
    data = drafts.evidence(row)
    assert not operation_facts(new, row, data)['readback_complete']
    from app.publishing_events import record
    record(new, product, row, data); db.session.commit()
    assert json.loads(ShopifyPublishingEvent.query.filter_by(operation_id=new.id).one().receipt_json)['changes'] == []
    assert old.status == 'succeeded'


def test_acknowledged_product_then_uncertain_package_never_claims_package_verified(world):
    product, row, adapter = existing(world); package = linked()
    authorise(adapter)
    change(world, packaging={'selected_package_id': package.id}, title='Product verified, package uncertain')
    adapter.assignment_failure = 'timeout'
    _, token = drafts.preview(product, adapter); drafts.confirm(token, True, adapter)
    op = db.session.get(CatalogueOperation, row.operation_id)
    facts = operation_facts(op, row, drafts.evidence(row))
    assert facts['readback_complete'] and facts['package_attempted'] and facts['package'] == 'uncertain'
    assert row.state == 'uncertain' and product_result(op)['outcome'] == 'recovery_required'
    receipt = json.loads(ShopifyPublishingEvent.query.filter_by(operation_id=op.id).one().receipt_json)
    assert any(c['field'] == 'title' for c in receipt['changes'])
    assert not any(c['field'].startswith('package_assignment.') for c in receipt['changes'])
    calls = list(adapter.calls)
    assert execute(product, adapter) == 'recovery_required' and adapter.calls == calls
    from app.shopify_package_assignments import guard
    with pytest.raises(Exception, match='no assignment readback'):
        guard(drafts.evidence(row))
