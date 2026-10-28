"""Real scheduler/receipts with isolated catalogue files and mocked Shopify."""
import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from flask import current_app

from app import db, automatic_publishing as publisher, shopify_drafts as drafts, scheduled_tasks as tasks
from app.models import (Product, ShopifyPublishJob, CatalogueOperation, CatalogueOperationItem,
    ShopifyProductIdentity, ScheduledTask, ScheduledTaskRun)
from app.publishing_checkpoint import validate
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_drafts import world
from test_shopify_variables import variable_world
from test_shopify_simple_updates import change
from test_shopify_publication_sales import AcceptanceAdapter, VariableAcceptanceAdapter
from test_automatic_publishing import authorise, queued, execute


@pytest.fixture(autouse=True)
def no_notifications(monkeypatch):
    monkeypatch.setattr('app.utils.discord.publishing_message', lambda data: (True, 'mocked'))


def history(product):
    Product.query.filter(Product.id != product.id).update({Product.catalogue_status: 'missing'})
    from app.metadata_workspace import metadata_source
    source = metadata_source(product, 'shared')['path']
    data = json.loads(source.read_text())
    if 'sku_prefix' not in data:
        source.write_text(json.dumps(dict(data, sku_prefix='FIXTURE-')))
    product.resolved_row_json = product.resolved_row_json or json.dumps({'SKU': product.sku, 'Type': product.product_type})
    product.override_json_relpath = None
    op = CatalogueOperation(id='legacy-scan', operation_type='append', status='succeeded',
        scope='{}', started_at=drafts.now()-timedelta(minutes=1), finished_at=drafts.now(), recovery_state='none')
    db.session.add(op)
    db.session.add(CatalogueOperationItem(operation_id=op.id, sku=product.sku,
        source_path=product.source_relpath, status='succeeded', database_state='committed'))
    db.session.commit()
    return op


def bootstrap(product):
    job = queued(product)
    job.state, job.trigger, job.requested_revision = 'attention', 'bootstrap', 'unscanned'
    job.scan_operation_id = job.operation_id = job.verified_revision = None
    job.error = 'A completed collection scan is required before automatic publishing.'
    db.session.commit()
    return job


def cycle(adapter):
    task = db.session.get(ScheduledTask, publisher.KEY)
    task.next_due = drafts.now()-timedelta(seconds=1)
    db.session.commit()
    run_id = tasks.enqueue(publisher.KEY)
    assert tasks.claim('checkpoint-worker') == (publisher.KEY, run_id)
    assert tasks.claim('other-worker') is None
    tasks.run_slice(publisher.KEY, run_id, 'checkpoint-worker', adapter=adapter)
    run = db.session.get(ScheduledTaskRun, run_id)
    assert run.state == 'completed', (run.error, run.data_json)
    return run


def validate_local(product):
    with patch('requests.Session.request', side_effect=AssertionError('network during source validation')):
        return validate(product, actor_id=1, acknowledged=True)


def test_legacy_live_create_validation_then_actual_hourly_publisher(world):
    product, folder, shared = world
    change(world, live=True)
    scan = history(product); job = bootstrap(product)
    before = shared.read_bytes(), [(p.name, p.read_bytes()) for p in folder.iterdir()]
    adapter = AcceptanceAdapter(); authorise(adapter); adapter.calls.clear()
    run = cycle(adapter)
    data = json.loads(run.data_json)
    assert data['eligibility']['missing_checkpoint'] == 1 and not adapter.calls
    validation = validate_local(product)
    db.session.expire_all(); job = db.session.get(ShopifyPublishJob, product.id)
    assert job.state == 'pending' and job.requested_revision != 'unscanned'
    assert job.scan_operation_id == scan.id and scan.status == 'succeeded'
    assert job.verified_revision is None
    assert before == (shared.read_bytes(), [(p.name, p.read_bytes()) for p in folder.iterdir()])
    run = cycle(adapter)
    assert adapter.calls.count('product_create') == 1
    assert json.loads(run.data_json)['publication_counts']['created_verified'] == 1
    assert ShopifyProductIdentity.query.one().state == 'verified'
    product_id = product.id
    db.session.remove()  # persisted checkpoint and final state, not an in-memory cache
    product = db.session.get(Product, product_id)
    assert db.session.get(ShopifyPublishJob, product.id).state == 'verified'
    assert db.session.get(CatalogueOperation, validation).status == 'succeeded'
    from app.catalogue_views import saved_states
    with current_app.test_request_context():
        assert saved_states([product])[product.id]['state'] == 'no_change'
    adapter.calls.clear(); cycle(adapter)
    assert not adapter.calls


@pytest.mark.parametrize('problem', ['changed_json', 'changed_image', 'missing_image', 'invalid_json',
    'draft', 'recovery', 'owned', 'missing_history', 'corrupt_snapshot'])
def test_legacy_validation_refuses_unsafe_sources_without_adoption(world, problem):
    product, folder, shared = world
    change(world, live=True)
    history(product); job = bootstrap(product)
    if problem == 'changed_json': change(world, title='Unscanned source change')
    elif problem == 'changed_image': (folder/'original.png').touch()
    elif problem == 'missing_image': (folder/'original.png').unlink()
    elif problem == 'invalid_json': shared.write_text('{')
    elif problem == 'draft': change(world, live=False)
    elif problem == 'recovery': job.state = 'recovery_required'
    elif problem == 'owned': job.owner = 'another-worker'
    elif problem == 'missing_history': CatalogueOperationItem.query.delete()
    elif problem == 'corrupt_snapshot': product.resolved_row_json = '{"SKU":"another-product","Type":"simple"}'
    db.session.commit()
    with pytest.raises((ValueError, RuntimeError)):
        validate_local(product)
    db.session.expire_all()
    assert db.session.get(ShopifyPublishJob, product.id).requested_revision == 'unscanned'
    assert not ShopifyProductIdentity.query.count()


@pytest.mark.parametrize('manual', [False, True])
def test_reconciliation_drift_reopens_only_existing_scan_authority_and_publisher_corrects(world, manual):
    from app.catalogue_views import saved_states
    from test_scheduled_tasks import age, run
    product, *_ = world
    Product.query.filter(Product.id != product.id).update({Product.catalogue_status: 'missing'})
    change(world, live=True)
    scan = history(product)
    adapter = AcceptanceAdapter(); authorise(adapter)
    assert execute(product, adapter) == 'verified'
    job = db.session.get(ShopifyPublishJob, product.id); job.scan_operation_id = scan.id
    db.session.commit()
    original_id = ShopifyProductIdentity.query.one().shopify_product_id
    adapter.remote['title'] = 'Changed externally on Shopify'
    age(drafts.now()-timedelta(days=2)); adapter.calls.clear()
    result, data = run(product, adapter, manual=manual)
    assert result.state == 'completed', (result.error, data)
    assert data['counts']['updates'] == 1
    assert not any('write' in call or 'create' in call or 'update' in call for call in adapter.calls)
    db.session.expire_all(); job = db.session.get(ShopifyPublishJob, product.id)
    assert job.state == 'pending' and job.trigger == 'reconciliation'
    assert job.verified_revision is None
    cycle(adapter)
    assert adapter.remote['title'] != 'Changed externally on Shopify'
    assert 'product_create' not in adapter.calls and 'stage_write' not in adapter.calls
    assert ShopifyProductIdentity.query.one().shopify_product_id == original_id
    with current_app.test_request_context():
        assert saved_states([product])[product.id]['state'] == 'no_change'
    age(drafts.now()-timedelta(days=2))
    _, matched = run(product, adapter, manual=manual)
    assert matched['counts']['up_to_date'] == 1


def test_empty_cycles_and_reconciliation_are_paginated_in_existing_event_history(world):
    product, *_ = world; change(world, live=True)
    history(product); bootstrap(product)
    adapter = AcceptanceAdapter(); authorise(adapter); adapter.calls.clear()
    run = cycle(adapter)
    client = _client(current_app)
    html = client.get('/operations/events?trigger=hourly').get_data(as_text=True)
    assert run.id in html and 'missing checkpoint: 1' in html and 'No eligible publishing work' in html
    assert '1 events' in html and not adapter.calls
    detail = client.get('/settings/tasks/runs/'+run.id).get_data(as_text=True)
    assert 'Saved eligibility at cycle start' in detail
    assert client.get('/settings/tasks').status_code == 200
    for index in range(12):
        db.session.add(ScheduledTaskRun(id=f'reconciliation-{index}', task_key=tasks.KEY,
            trigger='manual', state='failed' if index==0 else 'completed', created_at=drafts.now(),
            completed_at=drafts.now(), data_json=json.dumps({'counts': {'checked': 1, 'updates': 1}, 'read_only': True})))
    db.session.commit()
    first = client.get('/operations/events?trigger=reconciliation').get_data(as_text=True)
    second = client.get('/operations/events?trigger=reconciliation&page=2').get_data(as_text=True)
    assert '12 events' in first and 'Next' in first and '12 events' in second and 'Previous' in second
    assert 'no publication or local scan' in first
    assert '1 events' in client.get('/operations/events?trigger=reconciliation&result=failed').get_data(as_text=True)
    assert 'Historical selection/exclusion details were not recorded' not in first


def test_legacy_linked_live_validation_updates_same_identity_without_uploads(world):
    product, *_ = world; change(world, live=True)
    adapter = AcceptanceAdapter(); authorise(adapter)
    assert execute(product, adapter) == 'verified'
    remote = ShopifyProductIdentity.query.one().shopify_product_id
    change(world, title='A previously scanned local title')
    history(product); bootstrap(product)
    adapter.calls.clear(); validate_local(product)
    cycle(adapter)
    assert ShopifyProductIdentity.query.one().shopify_product_id == remote
    assert 'product_update' in adapter.calls and 'product_create' not in adapter.calls
    assert 'binary_upload' not in adapter.calls


@pytest.mark.parametrize('problem', ['missing_checkpoint', 'stale_source', 'recovery', 'draft', 'incomplete'])
def test_real_reconciliation_never_grants_missing_or_unsafe_write_authority(world, problem):
    from test_scheduled_tasks import age, run
    product, folder, _ = world; change(world, live=True)
    scan = history(product)
    adapter = AcceptanceAdapter(); authorise(adapter)
    assert execute(product, adapter) == 'verified'
    job = db.session.get(ShopifyPublishJob, product.id); job.scan_operation_id = scan.id
    if problem == 'missing_checkpoint': job.scan_operation_id = None
    elif problem == 'stale_source': change(world, title='New JSON not yet scanned')
    elif problem == 'recovery':
        job.state = 'recovery_required'
        ShopifyProductIdentity.query.one().state = 'uncertain'
    elif problem == 'draft': change(world, live=False)
    elif problem == 'incomplete':
        adapter.remote['media']['nodes'][0].update(status='PROCESSING', image=None)
    db.session.commit()
    adapter.remote['title'] = 'Remote drift'
    age(drafts.now()-timedelta(days=2)); adapter.calls.clear()
    run(product, adapter, manual=True)
    db.session.expire_all(); job = db.session.get(ShopifyPublishJob, product.id)
    assert job.state != 'pending'
    assert not any('write' in call or 'create' in call or 'update' in call for call in adapter.calls)
    if problem == 'recovery': assert job.state == 'recovery_required'


def test_checkpoint_validation_get_and_missing_acknowledgement_cannot_grant_authority(world):
    product, *_ = world; change(world, live=True)
    history(product); bootstrap(product)
    client = _client(current_app)
    with patch('requests.Session.request', side_effect=AssertionError('remote')):
        assert client.get(f'/publishing/scan/{product.id}').status_code == 200
        assert client.post(f'/publishing/checkpoint/{product.id}').status_code == 409
    assert ShopifyPublishJob.query.one().requested_revision == 'unscanned'


def test_legacy_variable_checkpoint_validates_fourteen_variants_and_fifty_three_original_images(variable_world):
    from test_variable_creation_recovery import large_variable, RealPagedVariable, assert_complete
    from test_shopify_variables import rescan
    rescan(variable_world, live=True)
    product = large_variable(variable_world)
    history(product); bootstrap(product)
    adapter = RealPagedVariable(); authorise(adapter)
    validate_local(product)
    # Existing bounded continuation, not a new gallery/variant execution path.
    job = db.session.get(ShopifyPublishJob, product.id)
    for _ in range(20):
        job.next_attempt = drafts.now(); db.session.commit()
        run_id = tasks.enqueue(publisher.KEY, manual=True, product_ids=[product.id])
        claimed = tasks.claim('legacy-variable-worker')
        assert claimed == (publisher.KEY, run_id)
        tasks.run_slice(*claimed, 'legacy-variable-worker', adapter=adapter)
        db.session.refresh(job)
        if job.state == 'verified': break
        task = db.session.get(ScheduledTask, publisher.KEY)
        task.resume_at = drafts.now(); db.session.commit()
    assert job.state == 'verified', job.error
    assert_complete(product, ShopifyProductIdentity.query.one(), adapter)
