"""Deliberate one-assignment correction; all remote calls are synthetic."""
import copy
import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from flask import current_app, g
from app import db, shopify_drafts as drafts, shopify_package_assignments as packages
from app.models import CatalogueOperation, ShopifyProductIdentity, ShopifyPublishJob, ScheduledTask
from app.shopify_connection import ShopifyError
from app.shopify_notifications import product_result
from test_shopify_package_assignments import world, existing, linked
from test_shopify_simple_updates import change
from test_phase3_woo_publish_preview import preview_app, _client
from test_manual_publishing_worker import step


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('TLC_SCHEDULER_READY_AT','99999999999')
    monkeypatch.setattr('app.utils.discord.publishing_message',lambda data:(False,'disabled'))


def protected(world):
    product,row,adapter = existing(world)
    pkg = linked()
    change(world,packaging={'selected_package_id':pkg.id})
    # Current product metadata was read back conclusively, but no package
    # intent/ack was retained (the real 16bit-0002 pattern).
    data = drafts.evidence(row)
    old = db.session.get(CatalogueOperation,row.operation_id)
    old.status='partial';old.recovery_state='review_required';old.error='Saved-package finalisation uncertain'
    data['verification'].update(operation_id=old.id,checked_at=drafts.now().isoformat())
    data['verification']['attributes']='verified'
    drafts.save(row,data)
    from app.automatic_publishing import queue_product
    queue_product(product)
    job=db.session.get(ShopifyPublishJob,product.id)
    job.state='recovery_required';job.operation_id=old.id;job.scan_operation_id='retained-completed-scan'
    db.session.commit()
    # Existing adapter asserts original publish digest. Correction intentionally
    # keeps that historic product plan, while signing a NEW package source.
    def assign(config,inventory,package):
        retained=drafts.evidence(row)
        proof=retained['package_assignments']['product']
        assert proof['source_revision']==retained['package_correction']['digest']
        with db.engine.connect() as conn:
            from sqlalchemy import text
            durable=json.loads(conn.execute(text('select media_evidence_json from shopify_product_identity')).scalar_one())
            assert durable['package_assignments']['product']['state']=='pending'
        adapter.calls.append(('package_assign',inventory,package))
        failure=getattr(adapter,'assignment_failure',None)
        if failure=='timeout':raise ShopifyError('Synthetic response lost',category='network')
        if failure=='reject':return {'inventoryItemUpdate':{'inventoryItem':None,'userErrors':[{'message':'Synthetic rejected input'}]}}
        if failure=='crash':raise SystemExit('Synthetic process termination')
        return {'inventoryItemUpdate':{'inventoryItem':{'id':inventory},'userErrors':[]}}
    adapter.assign_package=assign
    adapter.calls.clear()
    return product,row,adapter,old,job


def approve(product, adapter):
    reviewed,token=packages.correction_review(product,adapter)
    g.pop('shopify_confirm_lease',None);g.pop('shopify_review_ticket',None)
    client=_client(current_app)
    response=client.post(f'/products/{product.id}/shopify/package-correction',
        data={'action':'confirm','review':token,'acknowledge':'yes'},headers={'Accept':'application/json'})
    assert response.status_code==202,response.json
    return reviewed,token,response.json['operation_id']


def writes(adapter):
    return [c for c in adapter.calls if isinstance(c,tuple)]


def test_fresh_approved_package_only_correction_settles_blocker_preserving_history(world):
    product,row,adapter,old,job=protected(world)
    original=(old.scope,old.error,old.status,old.recovery_state,row.verified_at)
    managed=copy.deepcopy(drafts.evidence(row)['verification']);remote=copy.deepcopy(adapter.remote)
    reviewed,token,op_id=approve(product,adapter)
    assert not writes(adapter)
    step(adapter)
    op=db.session.get(CatalogueOperation,op_id)
    assert op.status=='succeeded',op.error
    assert row.state==row.verification_state=='verified'
    assert adapter.remote==remote and len(writes(adapter))==1
    assert 'product_create' not in adapter.calls and 'binary_upload' not in adapter.calls
    assert drafts.evidence(row)['verification']==managed
    assert (old.scope,old.error,old.status,old.recovery_state,row.verified_at)==original
    assert packages.superseded_operations([old,op])=={old.id}
    assert job.state=='paused' and job.verified_revision is None and job.operation_id==op.id
    result=product_result(op)
    assert result['operation']=='Package corrected' and result['outcome']=='shopify_accepted'
    assert result['verified_stages']==[]  # no new product readback is claimed
    from app.models import ShopifyPublishingEvent
    event=ShopifyPublishingEvent.query.filter_by(operation_id=op.id).one()
    assert event.event_type=='accepted'
    details=json.loads(event.receipt_json)
    assert len(details['changes'])==1 and details['changes'][0]['field'].startswith('package_assignment.')
    with current_app.test_request_context():
        from app.operation_reporting import operation_issues
        assert not operation_issues([old,op])['recovery']
    client=_client(current_app)
    response=client.post(f'/products/{product.id}/shopify/package-correction',data={
        'action':'confirm','review':token,'acknowledge':'yes'},headers={'Accept':'application/json'})
    assert response.status_code==200 and response.json['operation_id']==op.id
    assert response.json['completion_label']=='Completed — Shopify Accepted'
    assert len(writes(adapter))==1
    from app.manual_publishing import require_no_prior_recovery
    require_no_prior_recovery(product.id,{'mode':'update'})
    with pytest.raises(ShopifyError,match='already has Shopify Accepted'):
        packages.correction_review(product,adapter)
    change(world,description='A later independently reviewed metadata update')
    _,next_token=drafts.preview(product,adapter)
    assert next_token
    drafts.confirm(next_token,True,adapter)
    assert len(writes(adapter))==1 and adapter.remote['descriptionHtml']=='A later independently reviewed metadata update'


@pytest.mark.parametrize('failure',['timeout','reject'])
def test_uncertain_or_rejected_correction_never_clears_original_or_repeats(world,failure):
    product,row,adapter,old,job=protected(world)
    _,token,op_id=approve(product,adapter);adapter.assignment_failure=failure
    step(adapter)
    op=db.session.get(CatalogueOperation,op_id)
    assert (op.status,op.recovery_state)==(('partial','review_required') if failure=='timeout' else ('failed','none'))
    assert product_result(op)['outcome']==('recovery_required' if failure=='timeout' else 'failed')
    assert not packages.superseded_operations([old,op])
    assert job.state=='recovery_required' and len(writes(adapter))==1
    for _ in range(2):step(adapter)
    assert len(writes(adapter))==1 and old.recovery_state=='review_required'


@pytest.mark.parametrize('change_kind',['source','unscanned_source','image','mapping','variant','inventory','remote','ownership'])
def test_approval_bound_to_source_mapping_identity_and_owner(world,change_kind):
    product,row,adapter,old,job=protected(world)
    _,_,op_id=approve(product,adapter)
    if change_kind=='source':change(world,title='Changed after package approval')
    elif change_kind=='unscanned_source':
        raw=json.loads(world[2].read_text());raw['title']='Unscanned source edit';world[2].write_text(json.dumps(raw))
    elif change_kind=='image':
        from PIL import Image
        Image.new('RGB',(8,8),'blue').save(world[1]/'original.png')
    elif change_kind=='mapping':
        from app import package_links
        from app.packaging_models import Package
        pkg=Package.query.filter_by(name='Fictional mailer 123').one()
        package_links.set_id(pkg,'gid://shopify/ShippingPackage/125',['gid://shopify/ShippingPackage/123'])
    elif change_kind=='variant':adapter.remote['simpleVariants']['nodes'][0]['id']='gid://shopify/ProductVariant/999'
    elif change_kind=='inventory':adapter.remote['simpleVariants']['nodes'][0]['inventoryItem']['id']='gid://shopify/InventoryItem/999'
    elif change_kind=='remote':adapter.remote['updatedAt']='2026-10-30T01:01:01Z'
    else:job.owner='other';db.session.commit()
    step(adapter)
    op=db.session.get(CatalogueOperation,op_id)
    assert op.status=='failed' and not writes(adapter)
    assert old.recovery_state=='review_required' and job.state=='recovery_required'


def test_worker_restart_after_durable_intent_never_replays(world):
    product,row,adapter,old,job=protected(world)
    _,_,op_id=approve(product,adapter);adapter.assignment_failure='crash'
    product_id=product.id
    with pytest.raises(SystemExit):step(adapter)
    from app import manual_publishing
    task=db.session.get(ScheduledTask,manual_publishing.KEY)
    task.owner='dead';task.lease_until=drafts.now()-timedelta(seconds=1);task.resume_at=None
    db.session.commit();db.session.remove()
    adapter.assignment_failure=None
    step(adapter,'replacement')
    assert len(writes(adapter))==1
    assert db.session.get(CatalogueOperation,op_id).recovery_state=='review_required'
    assert db.session.get(ShopifyPublishJob,product_id).state=='recovery_required'


def test_worker_restart_after_ack_only_finalises_without_repeating_mutation(world):
    product,row,adapter,old,job=protected(world)
    _,_,op_id=approve(product,adapter)
    old_id=old.id
    with patch.object(packages,'finish_correction',side_effect=SystemExit('Crash after durable ack')):
        with pytest.raises(SystemExit):step(adapter)
    from app import manual_publishing
    task=db.session.get(ScheduledTask,manual_publishing.KEY)
    task.owner='dead';task.lease_until=drafts.now()-timedelta(seconds=1);task.resume_at=None
    db.session.commit();db.session.remove()
    step(adapter,'replacement')
    assert len(writes(adapter))==1
    assert db.session.get(CatalogueOperation,op_id).status=='succeeded'
    assert old_id in packages.superseded_operations([db.session.get(CatalogueOperation,old_id),db.session.get(CatalogueOperation,op_id)])


def test_get_is_local_only_and_approval_is_required(world):
    product,row,adapter,old,job=protected(world)
    client=_client(current_app)
    with patch('app.shopify_connection.ShopifyAdapter.test_connection',side_effect=AssertionError('GET must not contact Shopify')):
        response=client.get(f'/products/{product.id}/shopify/package-correction')
    assert response.status_code==200 and b'One package assignment only' in response.data
    reviewed,token=packages.correction_review(product,adapter)
    response=client.post(f'/products/{product.id}/shopify/package-correction',data={
        'action':'confirm','review':token},headers={'Accept':'application/json'})
    assert response.status_code==400 and not writes(adapter)


@pytest.mark.parametrize('workers',[2,4])
def test_only_one_worker_owns_approved_package_correction(world,workers):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app import scheduled_tasks
    product,row,adapter,old,job=protected(world)
    approve(product,adapter)
    app=current_app._get_current_object();barrier=Barrier(workers)
    def claim(index):
        with app.app_context():
            barrier.wait()
            try:return scheduled_tasks.claim('package-worker-'+str(index))
            finally:db.session.remove()
    with ThreadPoolExecutor(max_workers=workers) as pool:results=list(pool.map(claim,range(workers)))
    assert sum(result is not None for result in results)==1 and not writes(adapter)


@pytest.mark.parametrize('domain',['native_dimensions','publication_update','inventory_update','customisation_update'])
def test_package_correction_cannot_clear_another_uncertain_domain(world,domain):
    product,row,adapter,old,job=protected(world)
    data=drafts.evidence(row);data[domain]={'state':'uncertain'};drafts.save(row,data)
    with pytest.raises(ShopifyError,match='unresolved'):
        packages.correction_review(product,adapter)
    assert not writes(adapter) and job.state=='recovery_required'


def test_live_intent_reopens_normal_eligibility_without_inventing_verification(world):
    product,row,adapter,old,job=protected(world)
    change(world,live=True)
    from app.automatic_publishing import queue_product
    queue_product(product);db.session.commit()
    _,_,op_id=approve(product,adapter)
    step(adapter)
    assert db.session.get(CatalogueOperation,op_id).status=='succeeded'
    assert job.state=='pending' and job.view_pending and job.verified_revision is None
    assert row.shopify_product_id=='gid://shopify/Product/100' and len(writes(adapter))==1
