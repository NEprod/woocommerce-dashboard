"""Automatic authority and real existing executors with mocked Shopify only."""
import json
from unittest.mock import patch

import pytest

from app import db, automatic_publishing as publisher, shopify_drafts as drafts
from app.models import AutomaticPublishing, ShopifyPublishJob, ShopifyProductIdentity, User, CatalogueOperation
from app.shopify_capacity import BackgroundYield
from test_phase3_woo_publish_preview import preview_app
from test_shopify_drafts import world
from test_shopify_simple_updates import change
from test_shopify_publication_sales import AcceptanceAdapter, VariableAcceptanceAdapter
from test_shopify_variables import variable_world


@pytest.fixture(autouse=True)
def notices(monkeypatch):
    messages = []
    monkeypatch.setattr('app.utils.discord.publishing_message', lambda message: (messages.append(message) or True, 'mocked delivery'))
    return messages


def authorise(adapter):
    return publisher.configure(enabled=True, actor_id=User.query.filter_by(is_admin=True).first().id,
        confirmation=drafts.configuration().domain, adapter=adapter)


def queued(product):
    publisher.queue_product(product)
    db.session.commit()
    return db.session.get(ShopifyPublishJob, product.id, populate_existing=True)


def execute(product, adapter):
    return publisher.publish_one(queued(product), owner='fixture-owner', guard=lambda: None,
                                  fence=lambda: None, adapter=adapter)


def test_default_is_disabled_and_no_manual_signature_is_forged(world):
    product, *_ = world
    assert publisher.policy().enabled is False
    change(world, live=True)
    adapter = AcceptanceAdapter()
    assert execute(product, adapter) == 'paused'
    assert ShopifyProductIdentity.query.count() == 0
    assert not adapter.calls


def test_new_draft_stays_local_with_automatic_publishing_enabled(world):
    product, *_ = world
    adapter = AcceptanceAdapter(); authorise(adapter); adapter.calls.clear()
    assert execute(product, adapter) == 'paused'
    assert ShopifyProductIdentity.query.count() == 0
    assert not adapter.calls


def test_automatic_live_create_then_unchanged_does_not_repeat(world):
    product, *_ = world
    change(world, live=True)
    adapter = AcceptanceAdapter(); authorise(adapter)
    # Automatic authority uses the executor, never a forged manual confirmation.
    with patch.object(drafts, 'confirm', side_effect=AssertionError('Manual approval must not be forged')):
        assert execute(product, adapter) == 'verified'
    row = ShopifyProductIdentity.query.one()
    remote = row.shopify_product_id
    assert adapter.remote['status'] == 'ACTIVE'
    job = db.session.get(ShopifyPublishJob, product.id)
    assert job.verified_revision == job.requested_revision
    operation = db.session.get(CatalogueOperation, row.operation_id)
    assert json.loads(operation.scope)['automatic_publishing']['authority']['kind'] == 'automatic'
    adapter.calls.clear()
    assert execute(product, adapter) == 'verified'
    assert adapter.calls == []
    assert ShopifyProductIdentity.query.one().shopify_product_id == remote


def test_changed_live_uses_changed_only_executor(world):
    product, *_ = world
    change(world, live=True)
    adapter = AcceptanceAdapter(); authorise(adapter)
    assert execute(product, adapter) == 'verified'
    remote = ShopifyProductIdentity.query.one().shopify_product_id
    adapter.calls.clear(); adapter.inputs.clear()
    change(world, title='An automatically updated title')
    assert execute(product, adapter) == 'verified'
    assert adapter.remote['title'] == drafts.local_plan(product)['managed']['product']['title']
    assert 'stage_write' not in adapter.calls and 'product_create' not in adapter.calls
    assert ShopifyProductIdentity.query.one().shopify_product_id == remote


def test_live_to_draft_pauses_metadata_then_reactivates_same_identity(world):
    product, *_ = world
    change(world, live=True)
    adapter = AcceptanceAdapter(); authorise(adapter)
    assert execute(product, adapter) == 'verified'
    remote = ShopifyProductIdentity.query.one().shopify_product_id
    old_title = adapter.remote['title']
    change(world, live=False, title='Hold this title until Live')
    assert execute(product, adapter) == 'paused'
    assert adapter.remote['status'] == 'DRAFT' and adapter.remote['title'] == old_title
    adapter.calls.clear()
    change(world, description='Accumulated changes while Draft')
    assert execute(product, adapter) == 'paused'
    assert not adapter.calls
    job = db.session.get(ShopifyPublishJob, product.id)
    assert job.requested_revision != job.verified_revision
    change(world, live=True)
    assert execute(product, adapter) == 'verified'
    assert adapter.remote['status'] == 'ACTIVE'
    assert adapter.remote['title'] == drafts.local_plan(product)['managed']['product']['title']
    assert adapter.remote['descriptionHtml'] == 'Accumulated changes while Draft'
    assert ShopifyProductIdentity.query.one().shopify_product_id == remote


def test_five_edits_coalesce_and_json_formatting_does_not_change_revision(world):
    product, _, source = world
    adapter = AcceptanceAdapter(); authorise(adapter)
    for i in range(5):
        change(world, live=True, title=f'Edition {i}')
        queued(product)
    assert ShopifyPublishJob.query.count() == 1
    job = ShopifyPublishJob.query.one(); revision = job.requested_revision
    source.write_text(json.dumps(json.loads(source.read_text()), indent=4, sort_keys=True))
    assert queued(product).requested_revision == revision
    assert execute(product, adapter) == 'verified'
    assert adapter.remote['title'] == drafts.local_plan(product)['managed']['product']['title']
    assert adapter.calls.count('product_create') == 1


def test_uncertain_write_is_not_retried_or_cleared_by_new_edits(world):
    product, *_ = world
    change(world, live=True)
    adapter = AcceptanceAdapter(); adapter.failure = 'create'; authorise(adapter)
    assert execute(product, adapter) == 'recovery_required'
    adapter.calls.clear()
    change(world, title='A later local edit')
    assert execute(product, adapter) == 'recovery_required'
    assert not adapter.calls


def test_stale_completion_cannot_replace_a_new_owner(world):
    product, *_ = world
    job = queued(product)
    job.owner, job.running_revision = 'new-owner', job.requested_revision
    db.session.commit()
    with pytest.raises(BackgroundYield, match='ownership'):
        publisher._settle_job(job, owner='expired-owner', revision=job.running_revision, state='verified')
    assert db.session.get(ShopifyPublishJob, product.id).verified_revision is None


def test_live_requirements_are_opt_in_and_do_not_block_draft_saving(world):
    product, *_ = world
    adapter = AcceptanceAdapter()
    publisher.configure(enabled=True, actor_id=User.query.first().id,
        confirmation=drafts.configuration().domain, required_fields=['short_description'], adapter=adapter)
    change(world, live=True, short_description='')
    assert execute(product, adapter) == 'attention'
    assert not ShopifyProductIdentity.query.count()


def test_hourly_coordinator_uses_existing_lease_and_emits_one_verified_event(world, notices):
    from app import scheduled_tasks as tasks
    from app.models import ScheduledTask, ScheduledTaskRun, ShopifyPublishingEvent
    product, *_ = world
    change(world, live=True)
    adapter = AcceptanceAdapter(); authorise(adapter); queued(product)
    run_id = tasks.enqueue(publisher.KEY)
    assert run_id
    assert tasks.claim('worker-a') == (publisher.KEY, run_id)
    assert tasks.claim('worker-b') is None
    tasks.run_slice(publisher.KEY, run_id, 'worker-a', adapter=adapter)
    run = db.session.get(ScheduledTaskRun, run_id)
    assert run.state == 'completed', (run.state, run.error, run.data_json)
    row = ShopifyProductIdentity.query.one()
    assert row.state == 'verified', (drafts.evidence(row).get('error'), adapter.calls,
        db.session.get(ShopifyPublishJob, product.id).error, run.data_json)
    assert len(notices) == 1
    assert notices[0]['notify_verified'] is True
    assert ShopifyPublishingEvent.query.count() == 1
    job = db.session.get(ShopifyPublishJob, product.id)
    assert job.state == 'verified'
    task = db.session.get(ScheduledTask, publisher.KEY)
    assert task.interval_minutes == 60
    assert db.session.get(ScheduledTask, tasks.KEY).interval_minutes == 1440


def test_manual_sync_now_is_scoped_and_works_when_automatic_is_off(world):
    from app import scheduled_tasks as tasks
    from app.models import ScheduledTaskRun
    product, *_ = world
    change(world, live=True); queued(product)
    adapter = AcceptanceAdapter()
    job = publisher.sync_now(product, actor_id=User.query.first().id, acknowledged=True)
    key, run_id = tasks.claim('manual-worker')
    assert key == publisher.KEY
    assert json.loads(db.session.get(ScheduledTaskRun, run_id).data_json)['scope'] == [product.id]
    tasks.run_slice(key, run_id, 'manual-worker', adapter=adapter)
    assert db.session.get(ScheduledTaskRun, run_id).state == 'completed'
    assert db.session.get(ShopifyPublishJob, job.product_id).state == 'verified'
    assert publisher.policy().enabled is False


def test_startup_grace_does_not_execute_automatic_tasks_but_allows_manual(world, monkeypatch):
    from app import scheduled_tasks as tasks
    from app.models import ScheduledTaskRun
    from datetime import UTC
    product, *_ = world
    change(world, live=True); queued(product)
    adapter = AcceptanceAdapter(); authorise(adapter)
    future = drafts.now().replace(tzinfo=UTC).timestamp() + 600
    monkeypatch.setenv('TLC_SCHEDULER_READY_AT', str(future))
    assert tasks.tick('grace', adapter=adapter) is None
    assert not ShopifyProductIdentity.query.count()
    job = publisher.sync_now(product, actor_id=User.query.first().id, acknowledged=True)
    assert tasks.tick('manual-grace', adapter=adapter)[0] == publisher.KEY
    assert job.state == 'verified'


def test_sync_now_rejects_unscanned_edits_and_wrong_acknowledgement(world):
    product, *_ = world
    change(world, live=True); queued(product)
    with pytest.raises(ValueError, match='acknowledgement'):
        publisher.sync_now(product, actor_id=User.query.first().id, acknowledged=False)
    change(world, title='Not yet scanned')
    with pytest.raises(ValueError, match='completed scan'):
        publisher.sync_now(product, actor_id=User.query.first().id, acknowledged=True)


def test_older_verified_completion_retains_a_newer_pending_revision(world):
    product, *_ = world
    change(world, live=True); job = queued(product)
    old = job.requested_revision
    job.owner, job.running_revision = 'old-worker', old
    db.session.commit()
    change(world, title='Newer pending revision'); queued(product)
    publisher._settle_job(job, owner='old-worker', revision=old, state='verified')
    db.session.refresh(job)
    assert job.state == 'pending' and job.verified_revision == old
    assert job.requested_revision != old


def test_variable_layouts_automatic_creation_and_reactivation_preserve_variants(variable_world):
    product, source, *_ = variable_world
    def edit(**values):
        source.write_text(json.dumps({**json.loads(source.read_text()), **values}))
    adapter = VariableAcceptanceAdapter(); authorise(adapter)
    edit(live=True)
    assert execute(product, adapter) == 'verified'
    row = ShopifyProductIdentity.query.one()
    remote = row.shopify_product_id
    variants = {key: value['id'] for key, value in drafts.evidence(row)['variant_maps'].items()}
    edit(live=False)
    assert execute(product, adapter) == 'paused'
    edit(live=True, description='Updated while retaining all option and variant identities')
    assert execute(product, adapter) == 'verified'
    assert row.shopify_product_id == remote
    assert {key: value['id'] for key, value in drafts.evidence(row)['variant_maps'].items()} == variants
    assert adapter.calls.count('product_create') == 1


def test_known_media_processing_resumes_readback_then_activation_without_duplicate_create(world, notices):
    product,*_=world
    change(world,live=True)
    adapter=AcceptanceAdapter();adapter.processing=True;authorise(adapter)
    assert execute(product,adapter)=='retry'
    row=ShopifyProductIdentity.query.one();remote=row.shopify_product_id
    assert publisher.processing_readback(row) and not notices
    from app.operation_reporting import interpretation, operation_issues
    from flask import current_app
    original=db.session.get(CatalogueOperation,row.operation_id)
    assert interpretation(original)['execution']=='processing'
    with current_app.test_request_context('/'):
        assert not operation_issues([original])['recovery']
    job=db.session.get(ShopifyPublishJob,product.id)
    job.owner='fixture-owner';db.session.commit()
    publisher.recover_jobs('restart-owner')
    assert job.state=='pending' and job.owner is None
    adapter.calls.clear()
    assert execute(product,adapter)=='retry'
    assert 'product_create' not in adapter.calls
    for i,node in enumerate(adapter.remote['media']['nodes']):
        node.update(status='READY',image={'url':f'https://cdn.shopify.com/completed-{i}.png'})
    assert execute(product,adapter)=='verified'
    assert row.shopify_product_id==remote and adapter.remote['status']=='ACTIVE'
    assert 'product_create' not in adapter.calls
    assert len(notices)==1


def test_variable_media_processing_is_read_only_until_ready_then_reuses_all_ids(variable_world, notices):
    product,source,*_=variable_world
    source.write_text(json.dumps({**json.loads(source.read_text()),'live':True}))
    adapter=VariableAcceptanceAdapter();adapter.processing=True;authorise(adapter)
    assert execute(product,adapter)=='retry'
    row=ShopifyProductIdentity.query.one();remote=row.shopify_product_id
    variants={key:value['id'] for key,value in drafts.evidence(row)['variant_maps'].items()}
    adapter.calls.clear()
    for i,node in enumerate(adapter.remote['media']['nodes']):
        node.update(status='READY',image={'url':f'https://cdn.shopify.com/variable-ready-{i}.png'})
    assert execute(product,adapter)=='verified'
    assert row.shopify_product_id==remote
    assert {key:value['id'] for key,value in drafts.evidence(row)['variant_maps'].items()}==variants
    assert 'product_create' not in adapter.calls and len(notices)==1


def test_processing_keeps_one_durable_run_until_readback_finishes(world, monkeypatch):
    from datetime import timedelta
    from app import scheduled_tasks as tasks
    from app.models import ScheduledTaskRun
    product,*_=world;change(world,live=True)
    adapter=AcceptanceAdapter();adapter.processing=True;authorise(adapter);queued(product)
    run_id=tasks.enqueue(publisher.KEY)
    assert tasks.claim('processing-owner')==(publisher.KEY,run_id)
    tasks.run_slice(publisher.KEY,run_id,'processing-owner',adapter=adapter)
    run=db.session.get(ScheduledTaskRun,run_id)
    assert run.state=='paused'
    for i,node in enumerate(adapter.remote['media']['nodes']):
        node.update(status='READY',image={'url':f'https://cdn.shopify.com/run-ready-{i}.png'})
    later=drafts.now()+timedelta(seconds=31)
    monkeypatch.setattr(drafts,'now',lambda:later)
    assert tasks.claim('readback-owner')==(publisher.KEY,run_id)
    tasks.run_slice(publisher.KEY,run_id,'readback-owner',adapter=adapter)
    db.session.refresh(run)
    assert run.state=='completed',run.error
    counts=json.loads(run.data_json)['counts']
    assert counts['considered']==counts['checked']==counts['up_to_date']==1
    assert counts['attempts']==2 and counts['deferred']==0
    assert adapter.calls.count('product_create')==1
