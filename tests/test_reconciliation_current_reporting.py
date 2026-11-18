"""Saved reporting and product-image preflight only; fictional Shopify and SQLite."""
import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from flask import current_app

from app import db, scheduled_tasks as tasks, shopify_drafts as drafts, shopify_status
from app.models import Product, ScheduledTaskRun, ShopifyPublishJob, CatalogueOperation
from app.shopify_connection import ShopifyReadinessFinding
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_drafts import world
from test_shopify_simple_updates import change
from test_shopify_publication_sales import AcceptanceAdapter
from test_automatic_publishing import queued, authorise, execute
from test_manual_publishing_worker import accepted, step, products_badge


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr('requests.sessions.Session.request', lambda *a, **k: pytest.fail('No external network'))
    monkeypatch.setattr('app.utils.discord.publishing_message', lambda *a, **k: (False, 'isolated'))
    monkeypatch.setenv('TLC_SCHEDULER_READY_AT', '99999999999')


def report(product, *, error=None, scope=None):
    run_id = tasks.enqueue(tasks.KEY, manual=True, product_ids=scope or [product.id])
    assert tasks.claim('reader') == (tasks.KEY, run_id)
    run = db.session.get(ScheduledTaskRun, run_id)
    data = json.loads(run.data_json)
    data.update(initialized=True, queue=[], errors=[error] if error else [], items=[{
        'id':product.id, 'sku':product.sku, 'state':'Verification Unavailable' if error else 'Up to Date',
        'reason':error}])
    data['counts'].update(considered=1, checked=1, unavailable=int(bool(error)), deferred=0)
    tasks.save_progress(tasks.KEY, run_id, 'reader', data,
        terminal='partially_completed' if error else 'completed', error=error)
    return db.session.get(ScheduledTaskRun, run_id)


@pytest.mark.parametrize('mode', ['manual', 'hourly'])
def test_verified_product_supersedes_read_failure_without_rewriting_history(world, monkeypatch, mode):
    product = world[0]
    change(world, live=True, shopify_channels={'online-store':True})
    queued(product)
    old = report(product, error='Historical image/read prerequisite')
    original = old.data_json, old.error, old.state
    assert tasks.activity()['attention']
    adapter = AcceptanceAdapter()
    if mode == 'manual':
        op, _ = accepted(world, adapter); step(adapter)
        assert db.session.get(CatalogueOperation, op).status == 'succeeded'
    else:
        authorise(adapter)
        assert execute(product, adapter) == 'verified'
    assert products_badge(product)['label'] == 'Up to Date'
    assert tasks.activity() is None
    monkeypatch.setattr(drafts, 'now', lambda: old.completed_at+timedelta(hours=1))
    assert tasks.activity() is None  # freshness expiry cannot resurrect a resolved incident
    assert (old.data_json, old.error, old.state) == original
    assert adapter.calls.count('product_create') == 1


def test_narrow_success_does_not_hide_other_product_or_unknown_execution_failure(world):
    first = world[0]
    other = Product.query.filter(Product.id != first.id).first()
    old = report(first, error='Original artwork requires correction')
    report(other)
    assert tasks.activity()['attention'] and first.sku in tasks.activity()['reason']
    assert old.error == 'Original artwork requires correction'


def test_execution_error_is_separate_and_not_cleared_by_unrelated_scope(world):
    first = world[0]
    other = Product.query.filter(Product.id != first.id).first()
    old = report(first, error='Connection read_products missing')
    data = json.loads(old.data_json); data['items']=[]; old.data_json=json.dumps(data)
    db.session.commit()
    report(other)
    assert tasks.activity()['run_id'] == old.id
    assert 'execution needs attention' in tasks.activity()['label']
    assert 'read_products' in tasks.activity()['reason']


def test_scoped_failure_before_item_results_can_be_superseded_by_complete_manual_readback(world):
    product=world[0]
    change(world,live=True,shopify_channels={'online-store':True})
    queued(product)
    old=report(product,error='Read interrupted before per-product results')
    data=json.loads(old.data_json);data['items']=[];old.data_json=json.dumps(data);db.session.commit()
    assert tasks.activity()['attention']
    adapter=AcceptanceAdapter();op,_=accepted(world,adapter);step(adapter)
    assert db.session.get(CatalogueOperation,op).status=='succeeded'
    assert tasks.activity() is None


def test_new_readiness_finding_reappears_without_overwriting_success_history(world):
    product, folder, _ = world
    change(world, live=True, shopify_channels={'online-store':True})
    queued(product); adapter=AcceptanceAdapter()
    op, _ = accepted(world, adapter); step(adapter)
    assert tasks.activity() is None
    with (folder/'original.png').open('ab') as stream:
        stream.write(b'\0'*drafts.MAX_IMAGE)
    result = shopify_status.refresh([product], force=True, adapter=adapter)
    assert not result['errors'] and result['findings'][0]['code'] == 'image_size'
    assert tasks.activity()['attention']
    assert '19,000,000' in tasks.activity()['reason']
    assert db.session.get(CatalogueOperation, op).status == 'succeeded'
    page=_client(current_app).get('/publishing/queue').get_data(as_text=True)
    assert 'Product readiness:' in page and '19,000,000' in page


def test_uncertain_or_corrupt_identity_cannot_be_cleared_by_a_successful_scan(world):
    from app.models import ShopifyProductIdentity
    product=world[0]
    change(world,live=True,shopify_channels={'online-store':True})
    queued(product); adapter=AcceptanceAdapter()
    op,_=accepted(world,adapter); step(adapter)
    report(product)
    identity=ShopifyProductIdentity.query.one()
    identity.state='uncertain';db.session.commit()
    assert tasks.activity()['attention']
    html=_client(current_app).get('/publishing/queue').get_data(as_text=True)
    assert 'Recovery Required' in html and 'Queue checkpoint: Pending' not in html
    identity.media_evidence_json='broken';db.session.commit()
    assert tasks.activity()['attention']


def test_scan_queue_shows_independent_manual_success_without_settling_old_authority(world):
    product=world[0]
    change(world, live=True, shopify_channels={'online-store':True})
    job=queued(product)
    old_revision=drafts.digest('older scan checkpoint')
    job.requested_revision=old_revision; db.session.commit()
    adapter=AcceptanceAdapter(); op, _ = accepted(world, adapter); step(adapter)
    db.session.refresh(job)
    original=(job.state,job.requested_revision,job.verified_revision,job.operation_id)
    assert job.state=='pending'
    response=_client(current_app).get('/publishing/queue')
    assert response.status_code==200
    html=response.get_data(as_text=True)
    assert 'Queued by scan' in html and 'Up to Date' in html
    assert 'Successful manual Review &amp; Sync receipt' in html and op in html
    assert 'Not attempted by queue' in html and 'Queue checkpoint: Pending' in html
    db.session.refresh(job)
    assert (job.state,job.requested_revision,job.verified_revision,job.operation_id)==original


@pytest.mark.parametrize('size', [18_999_999,19_000_000,19_000_001,11_620_469])
def test_individual_size_boundaries_preserve_original_and_batch_budget(world,size):
    product,folder,_=world
    path=folder/'original.png'
    original=path.read_bytes()
    path.write_bytes(original+b'\0'*(size-len(original)))
    import hashlib
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    reference=path.relative_to(folder.parent.parent).as_posix()
    if size>drafts.MAX_IMAGE:
        with pytest.raises(ShopifyReadinessFinding) as caught:
            drafts.source_bytes(product,reference)
        assert caught.value.code=='image_size'
        assert all(text in str(caught.value) for text in ['original.png',f'{size:,}','19,000,000'])
    else:
        content,name,mime=drafts.source_bytes(product,reference)
        assert len(content)==size and name=='original.png' and mime=='image/png'
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    assert drafts.MAX_UPLOAD_BYTES==30*1024*1024
    assert len(drafts.upload_batch([{'filename':'a.png','size':19_000_000},
        {'filename':'b.png','size':19_000_000}]))==1


def test_readiness_failure_does_not_stop_the_next_product(world,monkeypatch):
    product=world[0]; other=Product.query.filter(Product.id!=product.id).first()
    seen=[]
    def preview(target,*a,**k):
        seen.append(target.id)
        if target.id==product.id:
            raise ShopifyReadinessFinding('Image original.png is 19,000,001 bytes; maximum 19,000,000 bytes',code='image_size')
        return None,None
    monkeypatch.setattr(drafts,'preview',preview)
    adapter=AcceptanceAdapter()
    result=shopify_status.refresh([product,other],force=True,adapter=adapter)
    assert seen==[product.id,other.id] and result['checked']==seen
    assert not result['errors'] and not result['pending']
    assert result['findings'][0]['product_id']==product.id
    row=CatalogueOperation.query.filter_by(operation_type='shopify_status_refresh').one()
    assert row.status=='succeeded' and row.recovery_state=='none'
    assert not any('write' in call or 'create' in call for call in adapter.calls)
