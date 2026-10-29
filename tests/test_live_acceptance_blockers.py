"""Real 28 October receipt shapes; isolated sources and mocked Shopify only."""
import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from flask import current_app
from app import db, shopify_drafts as drafts, scheduled_tasks as tasks, manual_publishing as manual
from app.models import CatalogueOperation, ScheduledTask, ScheduledTaskRun, ShopifyProductIdentity
from app.publishing_progress import view
from app.shopify_capacity import BackgroundYield
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_drafts import world
from test_shopify_simple_updates import change
from test_shopify_package_assignments import existing, linked
from test_manual_publishing_worker import accepted, step, offline
from test_draft_attention_settlement import historical, view_cycle


def test_legacy_draft_identity_route_refreshes_current_comparison_then_settles(world):
    product, identity, job, adapter, comparison, original = historical(world)
    old = original.scope, original.error, original.status
    receipt = identity.media_evidence_json
    with patch('requests.Session.request', side_effect=AssertionError('Route must only enqueue reads')):
        response = _client(current_app).post(f'/shopify/drafts/identity/{identity.id}', data={})
    assert response.status_code == 302
    run_id = response.headers['Location'].rsplit('/', 1)[-1]
    assert db.session.get(ScheduledTaskRun, run_id).task_key == tasks.KEY
    for task in ScheduledTask.query.all():
        if task.key != tasks.KEY and not task.active_run_id:
            task.next_due = drafts.now() + timedelta(days=1)
    db.session.commit()
    assert tasks.claim('legacy-draft-read') == (tasks.KEY, run_id)
    tasks.run_slice(tasks.KEY, run_id, 'legacy-draft-read', adapter=adapter)
    view_cycle()
    assert job.state == 'paused' and identity.media_evidence_json == receipt
    assert identity.shopify_product_id == 'gid://shopify/Product/100'
    assert (original.scope, original.error, original.status) == old
    assert not any('write' in str(c) or 'create' in str(c) for c in adapter.calls)


@pytest.mark.parametrize('scope', [{}, {'identity_id': 1}, {'mode': 'read_only_reconciliation', 'identity_id': 1}])
def test_operation_detail_without_product_id_is_readable(world, scope):
    op = CatalogueOperation(id='legitimate-no-product', operation_type='shopify_draft_publish',
        status='succeeded', recovery_state='none', scope=json.dumps(scope),
        started_at=drafts.now(), finished_at=drafts.now())
    db.session.add(op); db.session.commit()
    response = _client(current_app).get('/operations/' + op.id)
    assert response.status_code == 200
    assert b'Opening this page never retries' in response.data


def test_preflight_yield_retains_same_approval_and_resumes_without_false_failure(world, offline):
    product, identity, adapter = existing(world)
    change(world, title='A genuinely changed title')
    op_id, token = accepted(world, adapter)
    old_identity = identity.operation_id, identity.media_evidence_json
    calls = list(adapter.calls)
    with patch.object(adapter, 'test_connection', side_effect=BackgroundYield('Capacity reserved for interactive work')):
        step(adapter)
    op = db.session.get(CatalogueOperation, op_id)
    assert op.status == 'pending' and op.finished_at is None and op.error is None
    assert op.recovery_state == 'none' and op.products_failed == 0
    assert (identity.operation_id, identity.media_evidence_json) == old_identity
    assert adapter.calls == calls
    assert view(op)['status'] == 'waiting'
    response = _client(current_app).post(f'/products/{product.id}/shopify/progress', data={'review': token})
    assert response.json['state'] == 'processing' and response.json['background_active']
    assert 'deferred' in response.json['message'].lower()
    step(adapter)
    assert db.session.get(CatalogueOperation, op_id).status == 'succeeded'
    assert identity.operation_id == op_id
    assert adapter.calls.count('product_create') == 0  # existing fixture identity


@pytest.mark.parametrize('late', [False, True])
def test_original_recovery_cannot_be_bypassed_by_new_manual_approval(world, offline, late):
    product, identity, adapter = existing(world)
    change(world, title='Metadata must not bypass unresolved recovery')
    original = db.session.get(CatalogueOperation, identity.operation_id)
    if late:
        op_id, token = accepted(world, adapter)
    original.recovery_state = 'review_required'; db.session.commit()
    old = identity.media_evidence_json, original.scope, original.recovery_state
    calls = list(adapter.calls)
    if late:
        step(adapter)
        op = db.session.get(CatalogueOperation, op_id)
        assert op.status == 'failed' and op.recovery_state == 'none'
        assert 'Existing Recovery Required' in op.error
    else:
        _, token = drafts.preview(product, adapter)
        calls = list(adapter.calls)
        response = _client(current_app).post(f'/products/{product.id}/shopify/review',
            data={'action': 'confirm', 'review': token, 'acknowledge': 'yes'}, headers={'Accept': 'application/json'})
        assert response.status_code == 400
        assert 'Existing Recovery Required' in response.get_data(as_text=True)
    assert adapter.calls == calls
    assert (identity.media_evidence_json, original.scope, original.recovery_state) == old


def test_historical_no_write_yield_is_stopped_not_running_or_uncertain(world):
    op = CatalogueOperation(id='historical-yield', operation_type='shopify_draft_publish',
        status='failed', recovery_state='none', started_at=drafts.now(), finished_at=drafts.now(),
        error='Update failed with uncertain evidence. Reconcile before another write.',
        scope=json.dumps({'operation_summary': {'write_attempted': False, 'diagnostic': {
            'category': 'BackgroundYield', 'stage': 'verified', 'reason': 'Capacity deferred the step.'}}}))
    db.session.add(op); db.session.commit()
    state = view(op)
    assert state['status'] == 'attention' and state['stage'] == 'deferred_before_write'
    assert 'No new write failed' in state['latest_message']
    assert op.status == 'failed'  # derived presentation never rewrites history


def test_manual_modal_retains_verified_product_scope_when_package_is_uncertain(world, offline):
    product, identity, adapter = existing(world); package = linked()
    change(world, packaging={'selected_package_id': package.id}, title='Verified product, uncertain package')
    adapter.assignment_failure = 'timeout'
    op_id, token = accepted(world, adapter)
    step(adapter)
    response = _client(current_app).post(f'/products/{product.id}/shopify/progress', data={'review': token})
    result = response.json
    assert result['state'] == 'recovery_required'
    assert result['package_state'] == 'uncertain'
    assert 'Fields readback verified' in result['completed_stages']
    assert result['pending_stages'] == ['Saved-package assignment: uncertain']
    before = list(adapter.calls)
    db.session.remove(); db.engine.dispose()
    for _ in range(2):
        _client(current_app).get('/operations/' + op_id)
    assert adapter.calls == before


def test_explicit_package_ack_needs_no_unsupported_readback_and_is_not_repeated(world, offline):
    product, identity, adapter = existing(world); package = linked()
    change(world, packaging={'selected_package_id': package.id})
    op_id, token = accepted(world, adapter); step(adapter)
    from app.shopify_notifications import product_result
    op = db.session.get(CatalogueOperation, op_id)
    assert op.status == 'succeeded' and product_result(op)['outcome'] == 'shopify_accepted'
    proof = next(iter(drafts.evidence(identity)['package_assignments'].values()))
    assert proof['state'] == 'accepted' and proof['independently_verified'] is False
    before = list(adapter.calls)
    db.session.remove(); db.engine.dispose()
    step(adapter)
    assert adapter.calls == before


def test_package_rejection_retains_safe_shopify_reason_not_a_false_ack(world):
    product, identity, adapter = existing(world); package = linked()
    change(world, packaging={'selected_package_id': package.id})
    adapter.assignment_failure = 'userErrors'
    _, token = drafts.preview(product, adapter); drafts.confirm(token, True, adapter)
    proof = next(iter(drafts.evidence(identity)['package_assignments'].values()))
    assert proof['state'] == 'rejected' and not proof.get('acknowledged_at')
    assert proof['errors'] == [{'field': 'input.measurement.shippingPackageId', 'message': 'synthetic rejection'}]
