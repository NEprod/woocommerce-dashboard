"""No real transport: durable authorisation, restart evidence and delivery claims."""
import json
from datetime import timedelta
from unittest.mock import patch

import pytest

from app import db, automatic_publishing as publisher, shopify_drafts as drafts
from app import shopify_capacity as capacity, shopify_notifications as notices
from app.models import CatalogueOperation, ShopifyPublishJob, ShopifyProductIdentity, User, ScheduledTaskRun
from app.shopify_connection import ShopifyError
from test_automatic_publishing import authorise, queued, execute
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_drafts import world
from test_shopify_simple_updates import change
from test_shopify_publication_sales import AcceptanceAdapter


@pytest.fixture(autouse=True)
def offline_notices(monkeypatch):
    monkeypatch.setattr('app.utils.discord.publishing_message', lambda data: (False, 'disabled'))


def test_background_transport_still_rejects_unauthorised_writes(world):
    with capacity.background_work(lambda: None):
        with pytest.raises(capacity.BackgroundYield, match='forbidden'):
            capacity.before(drafts.configuration(), writing=True, query='mutation { productCreate }')


@pytest.mark.parametrize('change_authority', ['pause', 'epoch', 'config', 'owner', 'admin'])
def test_every_transport_write_rechecks_durable_authority(world, change_authority):
    product, *_ = world
    adapter = AcceptanceAdapter(); authorise(adapter)
    job = queued(product); revision = job.requested_revision
    job.owner, job.running_revision = 'owner', revision
    db.session.commit()
    config = drafts.configuration(); epoch = publisher.policy().revision
    calls = []

    def guard():
        publisher.authority(job, config, expected_policy=epoch)
        if job.owner != 'owner': raise capacity.BackgroundYield('owner changed')
        calls.append('allowed')

    with capacity.background_work(lambda: None, publisher_authority=guard):
        capacity.before(config, writing=True)
        policy = publisher.policy()
        if change_authority == 'pause': policy.enabled = False
        if change_authority == 'epoch': policy.revision += 1
        if change_authority == 'config': policy.config_digest = 'different'
        if change_authority == 'owner': job.owner = 'new-owner'
        if change_authority == 'admin': db.session.get(User, policy.actor_id).is_admin = False
        db.session.commit()
        with pytest.raises((capacity.BackgroundYield, ValueError)):
            capacity.before(config, writing=True)
    assert calls == ['allowed']


def test_explicit_connection_confirmation_is_required(world):
    with pytest.raises(ValueError, match='confirm'):
        publisher.configure(enabled=True, actor_id=User.query.first().id,
            confirmation='some-other-store.myshopify.com', adapter=AcceptanceAdapter())
    assert not publisher.policy().enabled


def test_prewrite_throttle_uses_durable_backoff_without_product_creation(world):
    product, *_ = world
    change(world, live=True)
    adapter = AcceptanceAdapter(); authorise(adapter)
    def throttle(config):
        raise ShopifyError('Please retry later', category='rate_limited')
    adapter.test_connection = throttle
    assert execute(product, adapter) == 'retry'
    job = db.session.get(ShopifyPublishJob, product.id)
    assert job.next_attempt > drafts.now() and job.attempts == 1
    assert not ShopifyProductIdentity.query.count()
    assert 'product_create' not in adapter.calls


def test_restart_reuses_exact_verified_receipt_without_replaying_mutation(world):
    product, *_ = world
    change(world, live=True)
    adapter = AcceptanceAdapter(); authorise(adapter)
    assert execute(product, adapter) == 'verified'
    job = db.session.get(ShopifyPublishJob, product.id)
    job.owner, job.running_revision, job.state = 'fixture-owner', job.requested_revision, 'processing'
    job.verified_revision = None
    db.session.commit(); adapter.calls.clear()
    publisher.recover_jobs('new-task-owner')
    db.session.refresh(job)
    assert job.state == 'verified' and job.verified_revision == job.requested_revision
    assert job.owner is None and not adapter.calls


def test_restart_never_replays_ambiguous_create(world):
    product, *_ = world
    change(world, live=True)
    adapter = AcceptanceAdapter(); authorise(adapter); adapter.failure = 'create'
    assert execute(product, adapter) == 'recovery_required'
    job = db.session.get(ShopifyPublishJob, product.id)
    job.owner, job.running_revision = 'fixture-owner', job.requested_revision
    db.session.commit(); adapter.calls.clear()
    publisher.recover_jobs('new-task-owner')
    assert job.state == 'recovery_required' and not adapter.calls
    assert ShopifyProductIdentity.query.one().shopify_product_id is None


def test_delivery_failure_does_not_invalidate_verified_publication(world, monkeypatch):
    product, *_ = world
    change(world, live=True)
    sent = []
    monkeypatch.setattr('app.utils.discord.publishing_message', lambda data: (sent.append(data) or False, 'failed'))
    adapter = AcceptanceAdapter(); authorise(adapter)
    assert execute(product, adapter) == 'verified'
    identity = ShopifyProductIdentity.query.one()
    operation = db.session.get(CatalogueOperation, identity.operation_id)
    assert operation.status == 'succeeded'
    notice = notices.scope_of(operation)[notices.KEY]['verified_success']
    assert notice['state'] == 'failed' and len(sent) == 1
    assert notices.retry_pending() == 0  # durable backoff
    clock = drafts.now() + timedelta(hours=2)
    monkeypatch.setattr(notices, 'now', lambda: clock)
    monkeypatch.setattr('app.utils.discord.publishing_message', lambda data: (sent.append(data) or True, 'sent'))
    assert notices.retry_pending() == 1
    assert notices.retry_pending() == 0
    assert len(sent) == 2 and identity.state == 'verified'


def test_uncertain_delivery_is_not_automatically_retried(world, monkeypatch):
    product, *_ = world
    change(world, live=True)
    monkeypatch.setattr('app.utils.discord.publishing_message', lambda data: (False, 'uncertain delivery'))
    adapter = AcceptanceAdapter(); authorise(adapter)
    assert execute(product, adapter) == 'verified'
    operation = db.session.get(CatalogueOperation, ShopifyProductIdentity.query.one().operation_id)
    assert notices.scope_of(operation)[notices.KEY]['verified_success']['state'] == 'uncertain'
    monkeypatch.setattr('app.utils.discord.publishing_message', lambda data: pytest.fail('ambiguous webhook replayed'))
    assert notices.retry_pending() == 0


def test_admin_settings_get_has_no_shopify_calls_and_csrf_still_protects_posts(world):
    from flask import current_app
    product, *_ = world
    client = _client(current_app)
    with patch('app.automatic_publishing.ShopifyAdapter', side_effect=AssertionError('GET must not contact Shopify')):
        assert client.get('/settings/publishing').status_code == 200
    assert not publisher.policy().enabled
    current_app.config['WTF_CSRF_ENABLED'] = True
    assert client.post('/settings/publishing', json={'enabled': True}).status_code == 400
    assert client.post(f'/products/{product.id}/sync-now', json={'acknowledged': True}).status_code == 400
    current_app.config['WTF_CSRF_ENABLED'] = False
    User.query.first().is_admin = False; db.session.commit()
    assert client.post(f'/products/{product.id}/sync-now', json={'acknowledged': True}).status_code == 403


def test_different_worker_cannot_overlap_publisher_and_reconciliation(world):
    from app import scheduled_tasks as tasks
    publisher_run = tasks.enqueue(tasks.PUBLISHER_KEY, manual=True)
    read_run = tasks.enqueue(tasks.KEY, manual=True)
    assert tasks.claim('publisher-owner') == (tasks.PUBLISHER_KEY, publisher_run)
    assert tasks.claim('other-worker') is None
    data = json.loads(db.session.get(ScheduledTaskRun, publisher_run).data_json)
    tasks.save_progress(tasks.PUBLISHER_KEY, publisher_run, 'publisher-owner', data, terminal='completed')
    assert tasks.claim('other-worker') == (tasks.KEY, read_run)


def test_complete_drift_read_reopens_pending_work_but_not_draft_metadata(world):
    product, *_ = world
    change(world, live=True)
    from test_publishing_checkpoint_integration import history, bootstrap, validate_local
    history(product)
    bootstrap(product)
    validate_local(product)
    adapter = AcceptanceAdapter(); authorise(adapter)
    assert execute(product, adapter) == 'verified'
    job = db.session.get(ShopifyPublishJob, product.id)
    assert job.verified_revision == job.requested_revision
    adapter.remote['title'] = 'Remote drift'
    plan, _ = drafts.preview(product, adapter)
    db.session.refresh(job)
    assert plan['classification'] == 'update'
    assert job.state == 'pending' and job.verified_revision is None
    assert job.trigger == 'reconciliation'
    assert publisher.publish_one(job, owner='drift', guard=lambda: None, fence=lambda: None, adapter=adapter) == 'verified'
    change(world, live=False)
    assert execute(product, adapter) == 'paused'
    adapter.remote['title'] = 'Remote Draft change'
    # Only a complete read is eligible; authored Draft/channel conflicts cannot
    # accidentally queue metadata writes or fabricate full-equality evidence.
    publisher.observe_comparison(product, {'classification': 'update'}, expected_revision=job.requested_revision, complete=True)
    db.session.commit(); db.session.refresh(job)
    assert job.state == 'paused'


def test_new_live_requirement_blocks_existing_verified_live_until_corrected(world):
    product, *_ = world
    change(world, live=True, short_description='')
    adapter = AcceptanceAdapter(); authorise(adapter)
    assert execute(product, adapter) == 'verified'
    publisher.configure(enabled=True, actor_id=User.query.first().id,
        confirmation=drafts.configuration().domain, required_fields=['short_description'], adapter=adapter)
    before = adapter.remote.copy(); adapter.calls.clear()
    # A new policy never republishes a conclusively unchanged product. The
    # requirement applies when a later authored change reaches the write boundary.
    assert execute(product, adapter) == 'verified'
    assert not adapter.calls
    change(world, description='A pending metadata edit')
    assert execute(product, adapter) == 'attention'
    assert adapter.remote == before and not adapter.calls
    operation = db.session.get(CatalogueOperation, ShopifyPublishJob.query.one().operation_id)
    assert json.loads(operation.scope)['operation_summary']['write_attempted'] is False
    change(world, short_description='Now complete')
    assert execute(product, adapter) == 'verified'
    from app.operation_reporting import issue_projection
    from flask import current_app
    with current_app.test_request_context('/'):
        assert not any(i.get('identifier') == operation.id for i in issue_projection()['items'])
    assert db.session.get(CatalogueOperation, operation.id).status == 'failed'


def test_persistent_technical_logs_rotate_without_field_payloads(world, tmp_path):
    from app import publishing_logs
    from flask import Flask
    app = Flask('log-fixture', instance_path=str(tmp_path / 'persistent'))
    handler = publishing_logs.configure(app, max_bytes=250, backups=2)
    job = queued(world[0])
    job.error = 'customer answer and credential must never be logged'
    try:
        for _ in range(20): publishing_logs.event(job, 'verified')
    finally:
        import logging
        logging.getLogger(publishing_logs.LOGGER).removeHandler(handler); handler.close()
    files = list((tmp_path / 'persistent/logs').glob('publishing.log*'))
    logs = [p for p in files if p.suffix != '.lock']
    assert len(logs) <= 3
    assert any(p.name.endswith('.1') for p in logs)
    assert all('customer answer' not in p.read_text() and 'credential' not in p.read_text() for p in logs)
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in logs)


def test_priority_event_is_scoped_without_advancing_hourly_schedule(world):
    from app import scheduled_tasks as tasks
    from app.models import Product, ScheduledTask
    product, *_ = world
    change(world, live=True)
    adapter=AcceptanceAdapter();authorise(adapter)
    job=queued(product);job.priority=2;job.trigger='sale_activate'
    other=db.session.get(Product,2)
    db.session.add(ShopifyPublishJob(product_id=other.id,source_identity=other.source_relpath,
        requested_revision='other-scanned-revision',state='pending',trigger='scan',priority=0,
        next_attempt=drafts.now(),updated_at=drafts.now()))
    task=db.session.get(ScheduledTask,tasks.PUBLISHER_KEY)
    task.next_due=drafts.now()+timedelta(hours=1)
    db.session.commit();next_hour=task.next_due
    run_id=publisher.enqueue_priority()
    run=db.session.get(ScheduledTaskRun,run_id)
    assert run.trigger=='priority' and json.loads(run.data_json)['scope']==[product.id]
    assert task.next_due==next_hour
    assert tasks.claim('priority-owner')==(tasks.PUBLISHER_KEY,run_id)
    tasks.run_slice(tasks.PUBLISHER_KEY,run_id,'priority-owner',adapter=adapter)
    assert db.session.get(ShopifyPublishJob,other.id).state=='pending'
    assert task.next_due==next_hour


def test_mocked_transport_budget_and_real_task_fences_cover_entire_creation(world, record_property):
    from app import scheduled_tasks as tasks
    from app.shopify_connection import ShopifyAdapter
    product,*_=world
    change(world,live=True)
    adapter=AcceptanceAdapter();authorise(adapter);queued(product)
    writes={'stage_images','upload_image','create_draft','set_simple_variant','update_simple_product',
        'update_simple_variant','set_publication','set_attributes','create_attribute_resource','update_dimension_definition'}
    reads={'test_connection','read_draft','read_simple','find_draft','find_sku','collection_state','dimension_definitions',
        'publications','publication_state','customisation_definition','customisation_value','attribute_schema','attribute_values'}
    import functools
    for name in writes|reads:
        method=getattr(adapter,name,None)
        if not callable(method):continue
        @functools.wraps(method)
        def transport(config,*args,_method=method,_writing=name in writes,**kwargs):
            capacity.before(config,writing=_writing)
            return _method(config,*args,**kwargs)
        setattr(adapter,name,transport)
    run_id=tasks.enqueue(tasks.PUBLISHER_KEY)
    assert tasks.claim('transport-owner')==(tasks.PUBLISHER_KEY,run_id)
    from time import perf_counter
    started=perf_counter()
    tasks.run_slice(tasks.PUBLISHER_KEY,run_id,'transport-owner',adapter=adapter)
    elapsed=perf_counter()-started
    run=db.session.get(ScheduledTaskRun,run_id)
    assert run.state=='completed',run.error
    assert ShopifyProductIdentity.query.one().state=='verified'
    assert json.loads(run.data_json)['counts']['api_requests']>10
    record_property('mocked_api_requests',json.loads(run.data_json)['counts']['api_requests'])
    record_property('mocked_verified_products',1)
    record_property('mocked_seconds',round(elapsed,3))
    record_property('mocked_products_per_minute',round(60/elapsed,2))


@pytest.mark.parametrize('raise_source_error',[False,True])
def test_invalid_source_is_a_product_preflight_not_an_uncaught_batch_failure(world, monkeypatch, raise_source_error):
    product,_,source=world
    change(world,live=True)
    adapter=AcceptanceAdapter();authorise(adapter);job=queued(product)
    source.write_text('{invalid json')
    if raise_source_error:
        monkeypatch.setattr(publisher,'local_revision',lambda product: (_ for _ in ()).throw(ValueError('Invalid source JSON')))
    adapter.calls.clear()
    assert publisher.publish_one(job,owner='invalid-source',guard=lambda:None,fence=lambda:None,adapter=adapter)=='attention'
    assert not adapter.calls
    assert ('Local publishing source requires correction' if raise_source_error else 'Local source changed after its completed scan') in job.error
    assert not ShopifyProductIdentity.query.count()


def test_simultaneous_workers_claim_only_one_conflicting_task(world):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from flask import current_app
    from app import scheduled_tasks as tasks
    app=current_app._get_current_object()
    tasks.enqueue(tasks.PUBLISHER_KEY,manual=True)
    tasks.enqueue(tasks.KEY,manual=True)
    barrier=Barrier(3)
    def claim(index):
        with app.app_context():
            barrier.wait(timeout=10)
            return tasks.claim('simultaneous-'+str(index))
    with ThreadPoolExecutor(max_workers=3) as workers:
        claimed=list(workers.map(claim,range(3)))
    assert sum(value is not None for value in claimed)==1


def test_publisher_batch_does_not_claim_complete_publication_for_blocked_target(world):
    from app import scheduled_tasks as tasks
    product,*_=world
    change(world,live=True,short_description='')
    adapter=AcceptanceAdapter()
    publisher.configure(enabled=True,actor_id=User.query.first().id,
        confirmation=drafts.configuration().domain,required_fields=['short_description'],adapter=adapter)
    queued(product)
    run_id=tasks.enqueue(tasks.PUBLISHER_KEY)
    assert tasks.claim('blocked-owner')==(tasks.PUBLISHER_KEY,run_id)
    tasks.run_slice(tasks.PUBLISHER_KEY,run_id,'blocked-owner',adapter=adapter)
    assert db.session.get(ScheduledTaskRun,run_id).state=='partially_completed'
    assert not ShopifyProductIdentity.query.count()
    assert db.session.get(ShopifyPublishJob,product.id).state=='attention'


@pytest.mark.parametrize('outcome,expected',[('attention','pending'),('retry','pending'),('recovery_required','recovery_required')])
def test_later_scanned_edit_survives_old_completion_but_not_uncertainty(world,outcome,expected):
    product,*_=world
    change(world,live=True);job=queued(product);old=job.requested_revision
    job.owner,job.running_revision='older',old;db.session.commit()
    change(world,title='Newer corrected source');queued(product)
    publisher._settle_job(job,owner='older',revision=old,state=outcome,error='Older attempt result')
    db.session.refresh(job)
    assert job.state==expected and job.requested_revision!=old
    assert (job.error is None) if outcome!='recovery_required' else job.error=='Older attempt result'
