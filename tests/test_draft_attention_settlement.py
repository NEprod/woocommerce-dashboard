"""Historical Draft preflight settlement through real read/view scheduler paths."""
import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from flask import current_app
from app import db, automatic_publishing as publisher, catalogue_views as views, scheduled_tasks as tasks
from app import shopify_drafts as drafts
from app.models import CatalogueOperation, Product, ScheduledTask, ScheduledTaskRun, ShopifyProductIdentity, ShopifyPublishJob
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_drafts import world
from test_publishing_finalisation import legacy_draft
from test_automatic_publishing import notices, execute, queued, authorise
from test_shopify_publication_sales import AcceptanceAdapter
from test_shopify_simple_updates import change
from test_scheduled_tasks import age, run
from test_shopify_package_assignments import existing, linked

ERROR = 'Publication requires a fully verified retained product. Create/verify its draft first or reconcile outstanding recovery.'


def historical(world):
    product, identity, job, adapter, comparison = legacy_draft(world)
    Product.query.filter(Product.id != product.id).update({Product.catalogue_status: 'missing'})
    job.operation_id = publisher._preflight_receipt(job, product, ERROR)
    job.state, job.error, job.attempts = 'attention', ERROR, 1
    publisher.configure(enabled=False, actor_id=publisher.policy().actor_id)
    age(drafts.now() - timedelta(hours=3))
    views.capture(product); db.session.commit()  # clean but stale projection
    original = db.session.get(CatalogueOperation, job.operation_id)
    return product, identity, job, adapter, comparison, original


def view_cycle():
    for task in ScheduledTask.query.all():
        if task.key != views.KEY and not task.active_run_id:
            task.next_due = drafts.now() + timedelta(days=1)
    task = db.session.get(ScheduledTask, views.KEY)
    task.enabled = True; task.next_due = drafts.now() - timedelta(seconds=1)
    db.session.commit()
    run_id = tasks.enqueue(views.KEY)
    assert run_id
    assert tasks.claim('draft-view-worker') == (views.KEY, run_id)
    assert tasks.claim('second-view-worker') is None
    tasks.run_slice(views.KEY, run_id, 'draft-view-worker')
    assert db.session.get(ScheduledTaskRun, run_id).state == 'completed'


@pytest.mark.parametrize('manual', [False, True])
def test_fresh_reconciliation_then_scheduled_projection_pauses_old_draft(world, notices, manual):
    product, identity, job, adapter, comparison, original = historical(world)
    old = original.scope, original.status, original.error, original.finished_at
    source_revision, verified_revision = job.requested_revision, job.verified_revision
    identity_receipt = identity.media_evidence_json
    notices.clear(); adapter.calls.clear()
    with patch('app.utils.scanner.scan_collection', side_effect=AssertionError('No local scan')):
        result, data = run(product, adapter, manual=manual)
        assert result.state == 'completed' and data['counts']['checked'] == 1
        view_cycle()
    assert job.state == 'paused' and publisher.draft_pause_settlement(job)
    assert (job.requested_revision, job.verified_revision) == (source_revision, verified_revision)
    assert identity.media_evidence_json == identity_receipt and adapter.remote['status'] == 'DRAFT'
    assert (original.scope, original.status, original.error, original.finished_at) == old
    assert not publisher.policy().enabled and not notices
    assert not any('write' in str(call) or 'create' in str(call) or 'upload' in str(call) for call in adapter.calls)
    with current_app.test_request_context('/'):
        state = views.saved_states([product])[product.id]
        assert state['state'] not in {'attention', 'recovery_required'}
        from app.operation_reporting import issue_projection
        assert not any(i['identifier'] == original.id for i in issue_projection()['items'])
    # Rebuilding a clean view preserves the pause proof; idle scheduling is idle.
    views.capture(product); db.session.commit()
    assert publisher.draft_pause_settlement(job) and not list(views.attention_candidates())
    checkpoint = comparison.summary_json, comparison.compared_at
    calls = list(adapter.calls)
    assert execute(product, adapter) == 'paused' and adapter.calls == calls
    assert (comparison.summary_json, comparison.compared_at) == checkpoint


@pytest.mark.parametrize('defect', ['stale', 'source', 'identity', 'remote_active', 'incomplete',
    'write_attempted', 'recovery', 'pending_package', 'owned', 'live'])
def test_draft_settlement_rejects_unproved_or_unsafe_state(world, defect):
    product, identity, job, adapter, comparison, original = historical(world)
    value = json.loads(comparison.summary_json)
    for key in ('full_compared_at', 'revision_checked_at', 'related_checked_at'):
        value[key] = drafts.now().isoformat()
    comparison.compared_at = drafts.now()
    if defect == 'stale':
        for key in ('full_compared_at', 'revision_checked_at', 'related_checked_at'):
            value[key] = (drafts.now() - timedelta(hours=3)).isoformat()
    elif defect == 'source': value['local'] = 'different-authoritative-source'
    elif defect == 'identity': value['remote_id'] = 'gid://shopify/Product/999'
    elif defect == 'remote_active': value['actual_status'] = 'ACTIVE'
    elif defect == 'incomplete': value['read_complete'] = False
    elif defect == 'write_attempted':
        scope = json.loads(original.scope); scope['operation_summary']['write_attempted'] = True
        original.scope = json.dumps(scope)
    elif defect == 'recovery': job.state = 'recovery_required'
    elif defect == 'pending_package':
        data = drafts.evidence(identity); data['package_assignments'] = {'product': {'state': 'uncertain'}}
        drafts.save(identity, data); value['identity'] = drafts.digest(identity.media_evidence_json)
    elif defect == 'owned': job.owner = 'another-worker'
    elif defect == 'live': change(world, live=True); job.state = 'attention'; job.error = ERROR
    comparison.summary_json = json.dumps(value); db.session.commit()
    assert not publisher.settle_obsolete_attention(product)
    assert job.state == ('recovery_required' if defect == 'recovery' else 'attention')
    assert not publisher.draft_pause_settlement(job)


def test_stale_clean_attention_is_bounded_not_continual_scheduler_work(world):
    product, identity, job, adapter, comparison, original = historical(world)
    # The stale evidence was already checked once; no continuous five-minute work.
    assert not list(views.attention_candidates()) and not job.view_pending
    run(product, adapter, manual=True)
    assert job.view_pending
    view_cycle()
    assert job.state == 'paused' and not list(views.attention_candidates())


def test_accepted_manual_work_prevents_draft_queue_settlement(world):
    product, identity, job, adapter, comparison, original = historical(world)
    run(product, adapter, manual=True)
    accepted = CatalogueOperation(id='accepted-manual', operation_type='shopify_draft_publish',
        status='pending', recovery_state='none', scope='{}', started_at=drafts.now())
    db.session.add(accepted); db.session.commit()
    views.capture(product); db.session.commit()
    assert job.state == 'attention' and not list(views.attention_candidates())
    accepted.status = 'failed'; accepted.finished_at = drafts.now(); db.session.commit()
    assert list(views.attention_candidates())  # ownership release, not a new remote read
    view_cycle()
    assert job.state == 'paused'


def test_draft_pause_survives_database_restart_without_renewing_metadata(world):
    product, identity, job, adapter, comparison, original = historical(world)
    run(product, adapter, manual=True); view_cycle()
    product_id, original_id = product.id, original.id
    checkpoint = job.requested_revision, job.verified_revision, comparison.summary_json, identity.media_evidence_json
    original_receipt = original.scope, original.status, original.error
    db.session.remove(); db.engine.dispose()
    job = db.session.get(ShopifyPublishJob, product_id)
    identity = ShopifyProductIdentity.query.filter_by(product_id=product_id).one()
    original = db.session.get(CatalogueOperation, original_id)
    from app.models import ShopifyProductComparison
    comparison = ShopifyProductComparison.query.filter_by(source_identity=job.source_identity).one()
    assert job.state == 'paused' and publisher.draft_pause_settlement(job)
    assert (job.requested_revision, job.verified_revision, comparison.summary_json, identity.media_evidence_json) == checkpoint
    assert (original.scope, original.status, original.error) == original_receipt
    assert not publisher.policy().enabled


def test_subsequent_live_transition_uses_original_identity_and_normal_safeguards(world):
    product = world[0]; adapter = AcceptanceAdapter()
    _, token = drafts.preview(product, adapter); identity = drafts.confirm(token, True, adapter)
    original_id = identity.shopify_product_id
    change(world, title='Accumulated Draft metadata')
    job = queued(product)
    job.operation_id = publisher._preflight_receipt(job, product, ERROR)
    job.state, job.error = 'attention', ERROR
    db.session.commit()
    drafts.preview(product, adapter)
    assert job.state == 'paused' and publisher.draft_pause_settlement(job)
    authorise(adapter)
    change(world, live=True)
    assert execute(product, adapter) == 'verified'
    assert adapter.remote['status'] == 'ACTIVE' and identity.shopify_product_id == original_id
    assert adapter.calls.count('product_create') == 1


def test_package_acknowledgement_survives_restart_and_readback_without_reassignment(world, notices):
    product, identity, adapter = existing(world); package = linked()
    change(world, packaging={'selected_package_id': package.id})
    _, token = drafts.preview(product, adapter); drafts.confirm(token, True, adapter)
    data = drafts.evidence(identity)
    acknowledgement = data['package_assignments']['product'].copy()
    assert acknowledgement['state'] == 'accepted' and not acknowledgement['independently_verified']
    # Acknowledgement is conclusive input acceptance, not independent package
    # readback. Restart while a product-media readback is still outstanding.
    data['verification']['media'] = 'processing'; data['stage'] = 'media_processing'
    identity.verification_state = 'processing'; drafts.save(identity, data, 'processing')
    operation = db.session.get(CatalogueOperation, identity.operation_id)
    operation.status = 'partial'; db.session.commit()
    identity_id, operation_id = identity.id, operation.id
    writes = len([c for c in adapter.calls if isinstance(c, tuple)])
    creates = adapter.calls.count('product_create')
    db.session.remove(); db.engine.dispose()
    identity = db.session.get(ShopifyProductIdentity, identity_id)
    drafts.finalize_processing(identity, adapter, force=True)
    assert identity.state == 'verified'
    assert drafts.evidence(identity)['package_assignments']['product'] == acknowledgement
    assert db.session.get(CatalogueOperation, operation_id).status == 'succeeded'
    assert len([c for c in adapter.calls if isinstance(c, tuple)]) == writes
    assert adapter.calls.count('product_create') == creates
