"""The actual scheduled entry point and inherited Draft channel preferences."""
import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from flask import current_app

from app import db, automatic_publishing as publisher, catalogue_views as views, scheduled_tasks as tasks
from app import shopify_drafts as drafts
from app.models import Product, ShopifyPublishJob, ShopifyProductIdentity, ShopifyProductComparison, ScheduledTask, ScheduledTaskRun, CatalogueOperation
from test_phase3_woo_publish_preview import preview_app
from test_shopify_drafts import world
from test_shopify_variables import variable_world
from test_obsolete_attention import verified, obsolete, capture
from test_automatic_publishing import notices, queued, authorise
from test_shopify_simple_updates import change
from test_shopify_publication_sales import AcceptanceAdapter


def clean_attention(w, revision):
    product, job, adapter = verified(w)
    capture(product)
    obsolete(product, job, revision)
    job.view_revision, job.view_pending = revision, False
    # Keep this isolated test's scheduler scope to its one real source.
    Product.query.filter(Product.id != product.id).update({'catalogue_status': 'missing'})
    publisher.configure(enabled=False, actor_id=publisher.policy().actor_id)
    ScheduledTask.query.update({'enabled': False})
    task = db.session.get(ScheduledTask, views.KEY)
    task.enabled, task.next_due = True, drafts.now()-timedelta(seconds=1)
    db.session.commit()
    assert ShopifyPublishJob.query.filter_by(view_pending=True).count() == 0
    return product, job, adapter


def scheduled_view_pass():
    run_id = tasks.enqueue(views.KEY)  # Deliberately NOT manual=True.
    assert run_id, 'Clean-view shortcut skipped obsolete Attention'
    assert tasks.enqueue(views.KEY) == run_id  # no second durable run
    claimed = tasks.claim('scheduled-acceptance-owner')
    assert claimed == (views.KEY, run_id)
    assert tasks.claim('competing-owner') is None
    tasks.run_slice(*claimed, 'scheduled-acceptance-owner')
    return run_id


@pytest.mark.parametrize('revision', ['unscanned', 'old-numeric-format'])
def test_scheduled_clean_views_settle_hero_and_3dco_then_idle_without_source_walk(world, revision, notices):
    product, job, adapter = clean_attention(world, revision)
    receipt = db.session.get(CatalogueOperation, job.operation_id) if job.operation_id else None
    history = (receipt.status, receipt.scope, receipt.error) if receipt else None
    comparison = ShopifyProductComparison.query.one()
    original = comparison.summary_json, comparison.compared_at
    adapter.calls.clear(); notices.clear()
    scheduled_view_pass()
    assert job.state == 'verified' and publisher.no_changes_settlement(job)
    assert job.requested_revision == revision and job.verified_revision is None
    assert (comparison.summary_json, comparison.compared_at) == original
    if receipt: assert (receipt.status, receipt.scope, receipt.error) == history
    count = ScheduledTaskRun.query.count()
    def forbidden(*a, **kw): raise AssertionError('Idle scheduling must not walk sources')
    with patch.object(publisher, 'local_revision', forbidden):
        for _ in range(2):
            db.session.get(ScheduledTask, views.KEY).next_due = drafts.now()-timedelta(seconds=1)
            db.session.commit()
            assert tasks.enqueue(views.KEY) is None
    assert ScheduledTaskRun.query.count() == count and not adapter.calls and not notices


def test_permanently_ineligible_attention_is_not_continually_scheduled(world):
    product, job, adapter = clean_attention(world, 'old-numeric-format')
    # Matching fallback display is not enough: the retained failure is not an
    # explicit no-write prerequisite, so the settlement guard must keep it.
    receipt = db.session.get(CatalogueOperation, job.operation_id)
    scope = json.loads(receipt.scope); scope['write_attempted'] = True
    receipt.scope = json.dumps(scope); db.session.commit()
    scheduled_view_pass()
    assert job.state == 'attention'
    count = ScheduledTaskRun.query.count()
    for _ in range(2):
        db.session.get(ScheduledTask, views.KEY).next_due = drafts.now()-timedelta(seconds=1)
        db.session.commit()
        assert tasks.enqueue(views.KEY) is None
    assert ScheduledTaskRun.query.count() == count
    # A new complete observation may re-evaluate; no blacklist or lost recovery.
    drafts.preview(product, adapter)
    assert job.state == 'attention'


def test_supervisor_tick_reaches_clean_attention_and_next_idle_tick_does_no_work(world, monkeypatch):
    product, job, adapter = clean_attention(world, 'unscanned')
    monkeypatch.setenv('TLC_SCHEDULER_READY_AT', '0')
    adapter.calls.clear()
    claimed = tasks.tick('actual-supervisor-owner', adapter=adapter)
    assert claimed and claimed[0] == views.KEY and job.state == 'verified'
    assert publisher.no_changes_settlement(job) and not adapter.calls
    db.session.get(ScheduledTask, views.KEY).next_due = drafts.now()-timedelta(seconds=1)
    db.session.commit()
    with patch.object(publisher, 'local_revision', side_effect=AssertionError('Idle source inspection')):
        assert tasks.tick('next-supervisor-owner', adapter=adapter) is None
    assert not adapter.calls


def test_actual_3dco_gallery_settles_through_scheduled_entry(variable_world):
    from test_variable_creation_recovery import large_variable, finish_uploads, RealPagedVariable
    from test_shopify_variables import rescan
    rescan(variable_world, live=True)
    product = large_variable(variable_world); adapter = RealPagedVariable()
    identity = finish_uploads(product, adapter)
    assert identity.state == 'verified'
    capture(product)
    job = db.session.get(ShopifyPublishJob, product.id)
    obsolete(product, job, 'old-numeric-format')
    job.view_pending, job.view_revision = False, job.requested_revision
    Product.query.filter(Product.id != product.id).update({'catalogue_status': 'missing'})
    ScheduledTask.query.update({'enabled': False})
    task = db.session.get(ScheduledTask, views.KEY)
    task.enabled, task.next_due = True, drafts.now()-timedelta(seconds=1)
    db.session.commit(); adapter.calls.clear()
    scheduled_view_pass()
    assert job.state == 'verified' and publisher.no_changes_settlement(job)
    assert job.verified_revision is None and job.requested_revision == 'old-numeric-format'
    data = drafts.evidence(identity)
    assert len(data['variant_maps']) == 14 and len(data['upload_inputs']) == 53
    assert not adapter.calls


def inherited_draft(w):
    product, folder, shared = w
    change(w, live=True, shopify_status='ACTIVE', shopify_channels={
        'online-store': True, 'shop': True, 'pos': True})
    override = folder/'product_info.json'
    override.write_text(json.dumps({'live': False}))
    return product, shared, override


def test_uncreated_override_draft_retains_channels_without_publication_or_creation(world):
    from app.taxonomy_assignments import product_document
    from app.shopify_products import states_for
    product, shared, override = inherited_draft(world)
    original = shared.read_bytes(), override.read_bytes()
    adapter = AcceptanceAdapter(); authorise(adapter)
    job = queued(product)
    plan, _ = drafts.preview(product, adapter)
    assert not plan.get('publication', {}).get('pending')
    assert not plan.get('publication', {}).get('configuration_error')
    assert 'publications_read' not in adapter.calls
    capture(product)
    state = views.saved_states([product], url_builder=lambda *a, **kw: None)[product.id]
    assert state['state'] == 'draft' and state['label'] == 'Local Draft'
    with current_app.test_request_context(): assert states_for([product], fresh=True)[product.id]['state'] == 'draft'
    adapter.calls.clear()
    assert publisher.publish_one(job, owner='draft-worker', guard=lambda: None, fence=lambda: None, adapter=adapter) == 'paused'
    assert not adapter.calls and ShopifyProductIdentity.query.count() == 0
    assert all(product_document(product)['shopify_channels'].values())
    assert (shared.read_bytes(), override.read_bytes()) == original


@pytest.mark.parametrize('finding', ['old_draft_channels', 'failed_page', 'identity_conflict'])
def test_old_draft_finding_projection_does_not_fabricate_verification_or_hide_failures(world, finding):
    from app import shopify_publication as publication
    from app.shopify_products import states_for
    product, *_ = inherited_draft(world)
    job = queued(product)
    # Produce the actual pre-correction comparison, without editing source JSON.
    with patch.object(publication, 'immediate_intent', publication.intent):
        drafts.preview(product, AcceptanceAdapter())
    comparison = ShopifyProductComparison.query.one()
    if finding != 'old_draft_channels':
        data = json.loads(comparison.summary_json)
        data['missing_evidence'] = []
        data['refresh_failure'] = {'kind': 'identity_conflict' if finding == 'identity_conflict' else 'incomplete_evidence',
            'message': 'Conflicting remote identity' if finding == 'identity_conflict' else 'Required API page failed'}
        comparison.summary_json = json.dumps(data); db.session.commit()
    original = comparison.summary_json, comparison.compared_at
    with current_app.test_request_context():
        state = states_for([product], fresh=True)[product.id]
    assert state['state'] == {'old_draft_channels': 'draft', 'failed_page': 'unavailable',
                              'identity_conflict': 'recovery_required'}[finding]
    capture(product)
    state = views.saved_states([product], url_builder=lambda *a, **kw: None)[product.id]
    assert state['state'] == {'old_draft_channels': 'draft', 'failed_page': 'unavailable',
                              'identity_conflict': 'recovery_required'}[finding]
    assert (comparison.summary_json, comparison.compared_at) == original
    assert json.loads(comparison.summary_json)['read_complete'] is False


def test_linked_draft_metadata_pauses_then_live_reuses_identity_and_channels(world):
    product, shared, override = inherited_draft(world)
    adapter = AcceptanceAdapter(); authorise(adapter)
    # Merchant-approved initial Draft creation is still available manually.
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    remote_id, before_title = row.shopify_product_id, adapter.remote['title']
    override.write_text(json.dumps({'live': False, 'title': 'Retained Draft edit'}))
    original = shared.read_bytes(), override.read_bytes()
    job = queued(product); adapter.calls.clear()
    assert publisher.publish_one(job, owner='draft-worker', guard=lambda: None, fence=lambda: None, adapter=adapter) == 'paused'
    assert adapter.remote['status'] == 'DRAFT' and adapter.remote['title'] == before_title
    assert not any(c in adapter.calls for c in ('product_update', 'publication_write', 'stage_write', 'product_create'))
    assert (shared.read_bytes(), override.read_bytes()) == original
    override.write_text(json.dumps({'live': True, 'title': 'Retained Draft edit'}))
    job = queued(product); adapter.calls.clear()
    assert publisher.publish_one(job, owner='live-worker', guard=lambda: None, fence=lambda: None, adapter=adapter) == 'verified'
    assert row.shopify_product_id == remote_id and adapter.calls.count('product_create') == 0
    assert adapter.remote['status'] == 'ACTIVE'
    assert adapter.remote['title'] == drafts.local_plan(product)['managed']['product']['title']
    assert adapter.memberships == {f'gid://shopify/Publication/{i}' for i in (1,2,3)}
