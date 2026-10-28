"""Real queue shapes from Unraid; isolated sources and mocked Shopify only."""
import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import current_app

from app import db, automatic_publishing as publisher, shopify_drafts as drafts
from app.models import CatalogueOperation, ShopifyProductComparison, ShopifyProductIdentity, ShopifyPublishJob
from test_phase3_woo_publish_preview import preview_app
from test_shopify_drafts import world
from test_shopify_variables import variable_world, rescan
from test_shopify_simple_updates import change
from test_automatic_publishing import queued, authorise, notices
from test_shopify_publication_sales import AcceptanceAdapter, VariableAcceptanceAdapter
from test_variable_creation_recovery import large_variable, finish_uploads, RealPagedVariable, assert_complete


ERROR = 'Local source changed after its completed scan. Run its collection update before publication.'


def obsolete(product, job, revision='unscanned'):
    job.requested_revision, job.verified_revision, job.state = revision, None, 'attention'
    job.owner, job.running_revision = None, None
    job.trigger, job.scan_operation_id = ('bootstrap', None) if revision == 'unscanned' else ('scan', job.scan_operation_id)
    job.operation_id = None
    job.error = 'A completed collection scan is required before automatic publishing.'
    if revision != 'unscanned':
        job.error = ERROR
        job.operation_id = publisher._preflight_receipt(job, product, ERROR)
    db.session.commit()
    return job


def verified(w):
    product = w[0]
    change(w, live=True)
    adapter = AcceptanceAdapter(); authorise(adapter)
    job = queued(product)
    assert publisher.publish_one(job, owner='fixture', guard=lambda: None, fence=lambda: None,
                                 adapter=adapter) == 'verified'
    return product, job, adapter


def capture(product):
    from app.catalogue_views import capture as save_view
    assert save_view(product)
    db.session.commit()


def test_hero_older_complete_matching_bootstrap_settles_without_inventing_checkpoint(world, notices):
    product, job, adapter = verified(world)
    comparison = ShopifyProductComparison.query.one()
    comparison.compared_at -= timedelta(days=1)
    data = json.loads(comparison.summary_json)
    for key in ('full_compared_at', 'revision_checked_at', 'related_checked_at', 'equality_at'):
        data[key] = comparison.compared_at.isoformat()
    comparison.summary_json = json.dumps(data)
    original = comparison.summary_json, comparison.compared_at
    identity = ShopifyProductIdentity.query.one()
    retained = identity.media_evidence_json, identity.shopify_product_id, identity.verified_at
    obsolete(product, job)
    error = job.error
    adapter.calls.clear(); notices.clear()
    capture(product)
    assert job.state == 'verified' and publisher.no_changes_settlement(job)
    assert job.requested_revision == 'unscanned' and job.verified_revision is None
    assert job.scan_operation_id is None and job.error == error
    assert (comparison.summary_json, comparison.compared_at) == original
    assert (identity.media_evidence_json, identity.shopify_product_id, identity.verified_at) == retained
    assert not adapter.calls and not notices
    from app.catalogue_views import saved_states
    state = saved_states([product], url_builder=lambda *a, **kw: None)[product.id]
    assert state['state'] == 'no_change' and state['label'] == 'Last check matched'
    with current_app.test_request_context('/publishing/queue'):
        from app.operations_views import queue
        row = queue()['rows'][0]
        assert row['no_changes'] and not row['scan_required'] and row['actual'] == 'ACTIVE'
    # It is still NOT a completed scan checkpoint permitting Sync Now.
    with pytest.raises(ValueError, match='completed scan revision'):
        publisher.sync_now(product, actor_id=publisher.policy().actor_id, acknowledged=True)


def test_3dco_numeric_attention_fourteen_variants_fifty_three_images(variable_world, notices):
    product = large_variable(variable_world)
    adapter = RealPagedVariable()
    identity = finish_uploads(product, adapter)
    assert_complete(product, identity, adapter)
    job = db.session.get(ShopifyPublishJob, product.id)
    # Recreate the old transient ORM number formatting on the last ingested row,
    # without changing source values or the production fingerprint implementation.
    from app.shopify_comparison import fingerprint
    images = drafts.evidence(identity)['plan']['images']
    def legacy_sha256(payload):
        data = json.loads(payload)
        child = data['variable']['variations'][-1]
        child['weight'] = f"{float(child['weight']):.3f}" if child['weight'] is not None else None
        child['dimensions'] = [f'{float(v):.3f}' if v is not None else None for v in child['dimensions']]
        return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode())
    with patch('app.shopify_products.hashlib', SimpleNamespace(sha256=legacy_sha256)):
        old = fingerprint(product, images)
    assert old != publisher.local_revision(product)
    obsolete(product, job, old)
    operation = db.session.get(CatalogueOperation, job.operation_id)
    history = operation.scope, operation.status, operation.error
    comparison = ShopifyProductComparison.query.one()
    before = comparison.summary_json, comparison.compared_at, identity.media_evidence_json
    adapter.calls.clear(); notices.clear()
    capture(product)
    assert job.state == 'verified' and job.requested_revision == old and job.verified_revision is None
    assert job.error == ERROR and (operation.scope, operation.status, operation.error) == history
    assert (comparison.summary_json, comparison.compared_at, identity.media_evidence_json) == before
    assert not adapter.calls and not notices
    product_id = product.id
    db.session.remove(); db.engine.dispose()  # persisted proof survives a new session
    job = db.session.get(ShopifyPublishJob, product_id)
    assert publisher.no_changes_settlement(job)['local'] != old


@pytest.mark.parametrize('defect', ['source', 'image', 'new_image', 'owner', 'recovery', 'identity',
    'uncertain', 'incomplete', 'invalidated', 'failed_read', 'update', 'package', 'scope', 'wrong_local',
    'wrong_config', 'wrong_receipt', 'write_attempted', 'unrelated_error', 'active_operation', 'duplicate_identity'])
def test_obsolete_attention_rejects_unsafe_evidence(world, defect):
    product, job, adapter = verified(world)
    obsolete(product, job, 'obsolete-numeric-hash')
    identity = ShopifyProductIdentity.query.one()
    comparison = ShopifyProductComparison.query.one()
    data = json.loads(comparison.summary_json)
    if defect == 'source': change(world, title='Edited without scanning')
    elif defect in {'image', 'new_image'}:
        from PIL import Image
        Image.new('RGB', (7, 8), 'blue').save(world[1] / ('original.png' if defect == 'image' else 'new.png'))
        if defect == 'new_image':
            from app.models import ProductImage
            product.images.append(ProductImage(url='new.png', position=10, alt_text='New image'))
    elif defect == 'owner': job.owner = 'other-worker'
    elif defect == 'recovery': job.state = 'recovery_required'
    elif defect == 'identity': identity.shopify_product_id = 'gid://shopify/Product/9999'
    elif defect == 'uncertain':
        evidence = drafts.evidence(identity)
        evidence['publication_update'] = {'state': 'uncertain'}
        identity.media_evidence_json = json.dumps(evidence)
        data['identity'] = drafts.digest(identity.media_evidence_json)
    elif defect == 'incomplete': data['read_complete'] = False
    elif defect == 'invalidated': data['invalidated'] = True
    elif defect == 'failed_read': data['refresh_failure'] = {'message': 'Required API page failed'}
    elif defect == 'update': data.update(classification='update', change_count=1)
    elif defect == 'package': data['local_package_changes'] = 1
    elif defect == 'scope': data['scope'] = 'inventory'
    elif defect == 'wrong_local': data['local'] = 'different-revision'
    elif defect == 'wrong_config': data['config'] = 'another-store-configuration'
    elif defect == 'wrong_receipt': data['identity'] = 'different-receipt'
    elif defect == 'write_attempted':
        operation = db.session.get(CatalogueOperation, job.operation_id)
        scope = json.loads(operation.scope); scope['write_attempted'] = True
        operation.scope = json.dumps(scope)
    elif defect == 'unrelated_error': job.error = 'A genuine unrelated failure'
    elif defect == 'active_operation':
        db.session.add(CatalogueOperation(id='accepted-manual-work', operation_type='shopify_draft_publish',
            status='pending', recovery_state='none', scope=json.dumps({'product_id': product.id}), started_at=drafts.now()))
    elif defect == 'duplicate_identity':
        db.session.add(ShopifyProductIdentity(product_id=product.id, source_identity=product.source_relpath,
            sku=product.sku, store_key='another-store', store_id='another-store', store_domain='other.myshopify.com',
            state='verified', verification_state='verified', reviewed_digest='other', media_evidence_json='{}'))
    comparison.summary_json = json.dumps(data)
    db.session.commit(); adapter.calls.clear()
    assert publisher.settle_obsolete_attention(product) is False
    assert job.state != 'verified' and job.verified_revision is None and not adapter.calls


@pytest.mark.parametrize('sku', ['16bit-0001', '16bit-0002', 'ADOC-0001'])
def test_real_updates_without_checkpoint_stay_attention(world, sku):
    product, job, adapter = verified(world)
    obsolete(product, job)
    change(world, title='A genuine pending title update')
    plan, _ = drafts.preview(product, adapter)
    assert plan['classification'] == 'update'
    capture(product)
    assert job.state == 'attention' and job.requested_revision == 'unscanned'
    assert not publisher.no_changes_settlement(job)


@pytest.mark.parametrize('live', [False, True])
def test_lcbc_live_create_and_local_drafts_never_borrow_scan_evidence(world, live):
    product = world[0]
    change(world, live=live)
    job = obsolete(product, queued(product))
    adapter = AcceptanceAdapter()
    drafts.preview(product, adapter)
    capture(product)
    assert job.state == 'attention' and job.requested_revision == 'unscanned'
    assert ShopifyProductIdentity.query.count() == 0
    assert 'product_create' not in adapter.calls


@pytest.mark.parametrize('method', ['comparison', 'view_worker'])
def test_followup_matching_comparison_or_existing_view_worker_settles_old_attention(world, method):
    product, job, adapter = verified(world)
    obsolete(product, job)
    if method == 'comparison':
        plan, _ = drafts.preview(product, adapter)
        assert plan['classification'] == 'no_change'
    else:
        from app import scheduled_tasks as tasks, catalogue_views
        # Existing view was current: the bounded Attention pass must still see it.
        job.view_pending = False; db.session.commit()
        run_id = tasks.enqueue(catalogue_views.KEY, manual=True)
        claimed = tasks.claim('settlement-view-worker')
        assert claimed == (catalogue_views.KEY, run_id)
        tasks.run_slice(*claimed, 'settlement-view-worker')
    assert job.state == 'verified' and publisher.no_changes_settlement(job)
    assert job.requested_revision == 'unscanned' and job.verified_revision is None


def test_noop_reopens_for_real_change_and_never_authorises_unscanned_update(world):
    product, job, adapter = verified(world)
    obsolete(product, job); capture(product)
    change(world, title='New genuine change')
    plan, _ = drafts.preview(product, adapter)
    assert plan['classification'] == 'update' and job.state == 'attention'
    assert job.requested_revision == 'unscanned' and job.verified_revision is None
    adapter.calls.clear()
    assert publisher.publish_one(job, owner='hourly', guard=lambda: None,
        fence=lambda: None, adapter=adapter) == 'attention'
    assert not adapter.calls


@pytest.mark.parametrize('contender', ['scan', 'owner', 'identity', 'comparison', 'operation'])
def test_queue_cas_does_not_consume_newer_scan_or_other_owner(world, monkeypatch, contender):
    from sqlalchemy import update
    product, job, adapter = verified(world)
    obsolete(product, job)
    from app import shopify_capacity
    def race():
        if contender in {'scan', 'owner'}:
            values = dict(requested_revision='new-scanned-revision', generation=job.generation+1,
                state='pending') if contender == 'scan' else dict(owner='competing-worker')
            db.session.execute(update(ShopifyPublishJob).where(ShopifyPublishJob.product_id == product.id)
                .values(**values), execution_options={'synchronize_session': False})
        elif contender == 'identity':
            db.session.execute(update(ShopifyProductIdentity).values(shopify_product_id='gid://shopify/Product/9999'),
                execution_options={'synchronize_session': False})
        elif contender == 'comparison':
            db.session.execute(update(ShopifyProductComparison).values(summary_json='{}'),
                execution_options={'synchronize_session': False})
        else:
            db.session.add(CatalogueOperation(id='concurrently-accepted', operation_type='shopify_draft_publish',
                status='pending', recovery_state='none', scope='{}', started_at=drafts.now()))
            db.session.flush()
    monkeypatch.setattr(shopify_capacity, 'fence_owner', race)
    assert publisher.settle_obsolete_attention(product) is False
    db.session.refresh(job)
    if contender == 'scan':
        assert job.state == 'pending' and job.requested_revision == 'new-scanned-revision'
    else:
        assert job.state == 'attention' and job.requested_revision == 'unscanned'


def test_only_proven_before_write_incident_resolves_but_original_history_remains(world):
    from app.operation_reporting import issue_projection
    product, job, adapter = verified(world)
    obsolete(product, job, 'old-source-format')
    operation_id = job.operation_id
    with current_app.test_request_context('/'):
        before = issue_projection()
        assert any(i['identifier'] == operation_id for i in before['items'])
    capture(product)
    with current_app.test_request_context('/'):
        after = issue_projection()
        assert not any(i['identifier'] == operation_id for i in after['items'])
    original = db.session.get(CatalogueOperation, operation_id)
    assert original.status == 'failed' and original.error == ERROR
    assert json.loads(original.scope)['write_attempted'] is False


def test_fresh_signed_manual_sync_then_view_settles_unscanned_attention(world):
    product, job, adapter = verified(world)
    obsolete(product, job)
    change(world, title='A genuinely changed title approved manually')
    _, token = drafts.preview(product, adapter)
    assert token
    row = drafts.confirm(token, True, adapter)
    assert row.state == 'verified'
    calls = list(adapter.calls)
    capture(product)
    assert job.state == 'verified' and publisher.no_changes_settlement(job)
    assert job.requested_revision == 'unscanned' and job.verified_revision is None
    assert adapter.calls == calls  # no replay to settle the queue


def test_settled_queue_and_catalogue_gets_never_reinspect_or_settle_sources(world, monkeypatch):
    from test_phase3_woo_publish_preview import _client
    product, job, adapter = verified(world)
    obsolete(product, job); capture(product)
    before = job.view_json, job.error, job.updated_at
    def forbidden(*a, **kw):
        raise AssertionError('GET must not inspect, settle, or contact Shopify')
    monkeypatch.setattr(publisher, 'local_revision', forbidden)
    monkeypatch.setattr(publisher, 'settle_obsolete_attention', forbidden)
    monkeypatch.setattr('requests.sessions.Session.request', forbidden)
    client = _client(current_app)
    for route in ('/publishing/queue', '/api/edit_products', '/'):
        assert client.get(route).status_code == 200
    assert (job.view_json, job.error, job.updated_at) == before
