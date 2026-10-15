"""M3A.8.5.3: mocked Shopify/Discord, durable ledger and readonly lifecycle."""
import copy
import json
from datetime import timedelta

import pytest
import requests
from sqlalchemy import create_engine, select

from app import db, shopify_drafts as drafts, shopify_status as status
from app import shopify_notifications as notices
from app.models import CatalogueOperation, ShopifyProductComparison
from app.shopify_connection import ShopifyError
from app.utils import discord
from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation, recover_interrupted_operations
from test_shopify_drafts import world
from test_shopify_simple_updates import UpdateAdapter, change
from test_shopify_status_reconciliation import RevisionAdapter, age, displayed
from test_shopify_variables import variable_world, create
from test_phase3_woo_publish_preview import preview_app, _client
from test_transactional_ingest import transactional_app, _rows, _write_product
from test_discord_notifications import discord_enabled, Response


@pytest.fixture
def delivery(monkeypatch):
    sent = []
    def send(summary):
        # Another connection sees the terminal operation and claimed event
        # before webhook delivery; no publishing transaction remains open.
        engine = create_engine(str(db.engine.url))
        with engine.connect() as connection:
            rows = connection.execute(select(CatalogueOperation.__table__)).mappings().all()
            assert any(row['status'] not in {'running','pending'} and '"sending"' in row['scope'] for row in rows)
        engine.dispose()
        sent.append(copy.deepcopy(summary))
        return True, 'sent'
    monkeypatch.setattr(discord, 'publishing_message', send)
    monkeypatch.setattr(discord, 'notify_operation_attention', lambda *a, **kw: pytest.fail('Read-only/generic Shopify notice'))
    return sent


def publish(world, adapter=None):
    adapter = adapter or RevisionAdapter()
    _, token = drafts.preview(world[0], adapter)
    return drafts.confirm(token, True, adapter), adapter


@pytest.mark.parametrize('path', ['/products', '/products?search=example', '/api/edit_products', '/products/1', '/products/1/shopify/review'])
def test_readonly_navigation_never_notifies(world, delivery, path):
    from flask import current_app
    client = _client(current_app._get_current_object())
    for _ in range(2):
        assert client.get(path).status_code == 200
    assert delivery == []


def test_create_update_durable_dedup_and_readonly_silence(world, delivery):
    row, adapter = publish(world, UpdateAdapter())
    assert len(delivery) == 1
    first = delivery[0]['products'][0]
    assert first['operation'] == 'Created' and first['outcome'] == 'verified_success'
    assert first['variants'] == 1 and first['images'] == 2 and first['shopify_status'] == 'DRAFT'
    assert first['url'].endswith('/admin/products/100')
    drafts.preview(world[0], adapter)  # no-change review is not an operation
    status.refresh([world[0]], force=True, adapter=adapter)
    drafts.reconcile(row, adapter=adapter)
    db.session.expire_all()  # restart/polling cannot repeat logical event
    operation = db.session.get(CatalogueOperation, row.operation_id)
    assert notices.enqueue(operation) is None
    assert notices.deliver(operation.id, 'verified_success') is False
    assert len(delivery) == 1
    change(world, description='A real reviewed edit')
    _, token = drafts.preview(world[0], adapter)
    drafts.confirm(token, True, adapter)
    assert len(delivery) == 2 and delivery[-1]['products'][0]['operation'] == 'Updated'
    assert adapter.calls.count('product_create') == 1


def test_readonly_errors_and_restart_do_not_notify(world, delivery):
    lease = acquire_catalogue_operation('shopify_status_refresh', {'products': [1]})
    finish_catalogue_operation(lease.id, status='partial', recovery_state='none')
    lease = acquire_catalogue_operation('shopify_draft_publish', {'mode':'read_only_reconciliation'})
    finish_catalogue_operation(lease.id, status='failed', recovery_state='review_required')
    lease = acquire_catalogue_operation('shopify_native_dimensions', {'mode':'read_only_preview'})
    finish_catalogue_operation(lease.id, status='failed')
    lease = acquire_catalogue_operation('shopify_status_refresh', {})
    assert recover_interrupted_operations() == 1
    finish_catalogue_operation(lease.id, status='interrupted')  # release process lease
    assert delivery == []


@pytest.mark.parametrize('failure,outcome', [('stage','failed_before_write'), ('variant','recovery_required')])
def test_definitive_and_uncertain_outcomes_are_distinct(world, delivery, failure, outcome):
    adapter = RevisionAdapter()
    adapter.failure = failure
    row, _ = publish(world, adapter)
    result = delivery[-1]['products'][0]
    assert result['outcome'] == outcome and 'shopify_status' not in result
    assert row.state != 'verified'


def test_browser_disconnect_processing_later_completes_original_event(world, delivery):
    adapter = RevisionAdapter()
    adapter.processing = True
    row, _ = publish(world, adapter)
    operation_id = row.operation_id
    assert row.state == 'processing' and delivery[-1]['partial_completion'] == 1
    for media in adapter.remote['media']['nodes']:
        media.update(status='READY', image={'url':'https://cdn.shopify.com/retained.png'})
    drafts.finalize_processing(row, adapter)
    assert row.state == 'verified' and delivery[-1]['verified_success'] == 1
    assert db.session.get(CatalogueOperation, operation_id).status == 'succeeded'
    drafts.finalize_processing(row, adapter)
    assert len(delivery) == 2


@pytest.mark.parametrize('result,expected', [('disabled','disabled'), ('not configured','disabled'), ('rate limited','failed'), ('uncertain delivery: timeout','uncertain')])
def test_discord_failure_does_not_fail_publish_and_retry_is_controlled(world, monkeypatch, result, expected):
    sends = []
    monkeypatch.setattr(discord, 'publishing_message', lambda payload: sends.append(payload) or (False,result))
    row, adapter = publish(world)
    assert row.state == 'verified'
    operation = db.session.get(CatalogueOperation, row.operation_id)
    assert operation.status == 'succeeded'
    assert notices.events_view(operation)[0]['state'] == expected
    assert notices.deliver(operation.id,'verified_success') is False and len(sends) == 1
    if expected == 'uncertain':
        with pytest.raises(ValueError, match='Acknowledge'):
            notices.deliver(operation.id,'verified_success',retry=True)
    monkeypatch.setattr(discord, 'publishing_message', lambda payload: (True,'sent'))
    assert notices.deliver(operation.id,'verified_success',retry=True,acknowledge_uncertain=True)
    assert notices.events_view(operation)[0]['attempts'] == 2


def test_competing_delivery_claim_and_crash_require_acknowledgement(world, delivery):
    row, _ = publish(world)
    operation = db.session.get(CatalogueOperation,row.operation_id)
    scope = notices.scope_of(operation)
    event = scope[notices.KEY]['verified_success']
    event.update(state='sending', attempted_at=drafts.now().isoformat())
    operation.scope=json.dumps(scope); db.session.commit()
    assert notices.deliver(operation.id,'verified_success',retry=True) is False
    event['attempted_at']=(drafts.now()-timedelta(minutes=3)).isoformat()
    operation.scope=json.dumps(scope); db.session.commit()
    db.session.expire_all()
    assert notices.events_view(operation)[0]['state'] == 'uncertain'
    with pytest.raises(ValueError, match='Acknowledge'):
        notices.deliver(operation.id,'verified_success',retry=True)
    assert len(delivery) == 1


def test_controlled_retry_route_requires_admin_csrf_and_ack(world, monkeypatch):
    from flask import current_app
    monkeypatch.setattr(discord,'publishing_message',lambda payload:(False,'uncertain delivery'))
    row,_=publish(world)
    client=_client(current_app._get_current_object())
    endpoint=f'/operations/{row.operation_id}/discord/verified_success/retry'
    assert client.post(endpoint).status_code==400
    assert client.get(f'/operations/{row.operation_id}').status_code==200
    current_app.config['WTF_CSRF_ENABLED']=True
    assert client.post(endpoint,data={'acknowledge_uncertain':'yes'}).status_code==400


def test_webhook_timeout_has_one_attempt_and_safe_payload(discord_enabled, monkeypatch):
    calls=[]
    def timeout(url,**kwargs):
        calls.append(kwargs)
        raise requests.Timeout('Do not disclose webhook credentials')
    monkeypatch.setattr(discord_enabled.requests,'post',timeout)
    summary=notices.aggregate([{'title':'TLC example','sku':'EX-1','operation':'Updated','outcome':'verified_success','completed_at':'2026-10-14T12:00:00Z','variants':9,'images':13,'shopify_status':'DRAFT'}])
    ok, message=discord_enabled.publishing_message(summary)
    assert not ok and 'uncertain' in message and len(calls)==1
    assert calls[0]['timeout']==(3.05,5)
    assert calls[0]['params']=={'wait':'true'} and calls[0]['allow_redirects'] is False
    assert calls[0]['json']['allowed_mentions']=={'parse':[]}
    text=json.dumps(calls[0]['json'])
    assert 'TLC Shopify Sync' in text and 'Images synchronised/retained' in text
    assert 'webhooks' not in text


def test_future_multi_product_aggregation():
    summary=notices.aggregate([{'sku':'ONE','outcome':'verified_success'},{'sku':'TWO','outcome':'recovery_required'},{'sku':'THREE','outcome':'failed_before_write'},{'sku':'FOUR','outcome':'partial_completion'}])
    assert summary['attempted']==4 and all(summary[key]==1 for key in ['verified_success','recovery_required','failed_before_write','partial_completion'])


def test_publishing_transport_confirms_delivery_without_changing_scanner_transport(discord_enabled, monkeypatch):
    calls=[]
    monkeypatch.setattr(discord_enabled.requests,'post',lambda url,**kwargs:calls.append(kwargs) or Response(200))
    summary=notices.aggregate([{'title':'TLC example','sku':'EX-1','operation':'Created','outcome':'verified_success','completed_at':'2026-10-14T12:00:00Z'}])
    assert discord_enabled.publishing_message(summary)==(True,'sent')
    assert calls[-1]['params']=={'wait':'true'}
    assert discord_enabled.notify_scan_started('append',1)==(True,'sent')
    assert 'params' not in calls[-1]


def test_ingest_discovery_once_not_on_unchanged_or_changed_upserts(transactional_app, monkeypatch):
    from app.utils.ingest import ingest_rows_to_db
    sent=[]
    monkeypatch.setattr('app.utils.ingest.notify_ingest_product',lambda **kwargs:sent.append(kwargs))
    with transactional_app[0].app_context():
        _write_product(transactional_app[1],'First Product','NOTICE-1')
        _write_product(transactional_app[1],'New Product','NOTICE-2')
        for title in ['First','First','Changed']:
            result=ingest_rows_to_db(_rows('NOTICE-1',title),log=lambda *a,**kw:None)
            assert not result['products_failed']
        ingest_rows_to_db(_rows('NOTICE-2','New'),log=lambda *a,**kw:None)
        assert [item['sku'] for item in sent]==['NOTICE-1','NOTICE-2']


def test_ten_minute_success_and_failed_request_never_extend_it(world, delivery):
    row,adapter=publish(world)
    age(9,'full_compared_at','revision_checked_at','related_checked_at')
    adapter.calls.clear()
    assert status.refresh([world[0]],adapter=adapter)['full_comparisons']==0 and not adapter.calls
    age(11,'full_compared_at','revision_checked_at','related_checked_at')
    saved=ShopifyProductComparison.query.one(); before=json.loads(saved.summary_json)['full_compared_at']
    adapter.product_revisions=lambda *args: (_ for _ in ()).throw(ShopifyError('Read throttled'))
    assert status.refresh([world[0]],adapter=adapter)['errors']
    assert json.loads(saved.summary_json)['full_compared_at']==before
    assert displayed(world[0])['label']=='Verification Unavailable'
    assert len(delivery)==1


def test_stat_change_auto_resolves_and_real_managed_change_stays_update(world, delivery):
    import os
    row,adapter=publish(world)
    image=world[1]/'original.png'
    stat=image.stat(); os.utime(image,ns=(stat.st_atime_ns,stat.st_mtime_ns+1000000))
    assert displayed(world[0])['label']=='Checking Changes'
    result=status.refresh([world[0]],adapter=adapter)
    assert result['full_comparisons']==1 and displayed(world[0])['label']=='Up to Date'
    change(world,weight=51)
    assert displayed(world[0])['label']=='Checking Changes'
    status.refresh([world[0]],adapter=adapter)
    assert displayed(world[0])['label']=='Update Available' and len(delivery)==1


def test_incomplete_attribute_read_preserves_success_checkpoint(world, monkeypatch):
    row,adapter=publish(world)
    saved=ShopifyProductComparison.query.one(); before=json.loads(saved.summary_json)
    def incomplete(product,plan,*args,**kwargs):
        plan['attribute_diagnostics']=[{'name':'Recipient','action':'Required read unavailable'}]
        plan['attributes']=[]
        plan['blockers'].append('Required attribute read unavailable')
    monkeypatch.setattr('app.shopify_attributes.attach',incomplete)
    drafts.preview(world[0],adapter)
    after=json.loads(saved.summary_json)
    assert after['full_compared_at']==before['full_compared_at'] and after['equality_at']==before['equality_at']
    assert displayed(world[0])['label']=='Verification Unavailable'


def test_variable_internal_notes_do_not_invalidate_and_expired_status_reads_all(variable_world, delivery):
    row,adapter=create(variable_world)
    product,shared,*_=variable_world
    original=json.loads(shared.read_text()); original['production_note']='Internal only'
    shared.write_text(json.dumps(original))
    adapter.calls.clear()
    assert displayed(product)['label']=='Up to Date'
    assert status.refresh([product],adapter=adapter)['full_comparisons']==0 and not adapter.calls
    age(11,'full_compared_at','revision_checked_at','related_checked_at')
    result=status.refresh([product],adapter=adapter)
    assert result['full_comparisons']==1 and displayed(product)['label']=='Up to Date'
    assert len(drafts.evidence(row)['variant_maps'])==4 and len(delivery)==1


def test_simultaneous_workers_claim_one_delivery(world, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, local
    from flask import current_app
    monkeypatch.setattr(discord,'publishing_message',lambda summary:(False,'disabled'))
    identity,_=publish(world)
    operation=db.session.get(CatalogueOperation,identity.operation_id)
    data=notices.scope_of(operation); data[notices.KEY]['verified_success']['state']='pending'
    operation.scope=json.dumps(data); db.session.commit()
    barrier=Barrier(2); thread_state=local(); original=notices.scope_of; sends=[]
    def sync_scope(operation):
        value=original(operation)
        if value.get(notices.KEY,{}).get('verified_success',{}).get('state')=='pending' and not getattr(thread_state,'seen',False):
            thread_state.seen=True; barrier.wait(timeout=5)
        return value
    monkeypatch.setattr(notices,'scope_of',sync_scope)
    monkeypatch.setattr(discord,'publishing_message',lambda summary:sends.append(summary) or (True,'sent'))
    app=current_app._get_current_object(); operation_id=identity.operation_id
    def worker():
        with app.app_context():
            return notices.deliver(operation_id,'verified_success')
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _:worker(),range(2)))==[False,True]
    assert len(sends)==1


def test_multiple_tabs_preserve_shared_status_operation_lock(world, delivery):
    from app.utils.operation_control import CatalogueOperationActive
    identity,adapter=publish(world); adapter.calls.clear()
    lease=acquire_catalogue_operation('shopify_status_refresh',{'products':[world[0].id]})
    try:
        with pytest.raises(CatalogueOperationActive):
            status.refresh([world[0]],force=True,adapter=adapter)
        assert not adapter.calls
    finally:
        finish_catalogue_operation(lease.id,status='succeeded')
    assert len(delivery)==1


def test_preview_no_change_renews_success_without_notification(world, delivery):
    row,adapter=publish(world)
    age(11,'full_compared_at','revision_checked_at','related_checked_at')
    before=json.loads(ShopifyProductComparison.query.one().summary_json)['equality_at']
    plan,token=drafts.preview(world[0],adapter)
    assert plan['classification']=='no_change' and token is None
    assert json.loads(ShopifyProductComparison.query.one().summary_json)['equality_at']>=before
    assert displayed(world[0])['label']=='Up to Date' and len(delivery)==1


def test_failed_native_read_cannot_renew_success_checkpoint(world, monkeypatch):
    row,adapter=publish(world)
    saved=ShopifyProductComparison.query.one(); original=json.loads(saved.summary_json)
    plan,_=drafts.preview(world[0],adapter)
    before=json.loads(saved.summary_json)
    plan['native']={'enabled':True,'classification':'blocked','reason':'Native inventory read timed out'}
    plan['comparison_started_at']=drafts.now().isoformat()  # this fixture simulates a NEW failed required read
    from app.shopify_comparison import record
    record(world[0],plan,row)
    after=json.loads(saved.summary_json)
    assert after['full_compared_at']==before['full_compared_at'] and after['equality_at']==before['equality_at']
    assert not after['read_complete'] and after['refresh_failure']
    assert displayed(world[0])['label']=='Verification Unavailable'


def test_pending_notification_retained_and_secrets_redacted(world, monkeypatch):
    from app.utils.operation_control import prune_operation_history
    monkeypatch.setattr(discord,'publishing_message',lambda summary:(False,'Discord delivery error'))
    row,_=publish(world)
    monkeypatch.setenv('SHOPIFY_CLIENT_SECRET','fictional-long-client-secret')
    monkeypatch.setattr(discord,'publishing_message',lambda summary:(False,'Discord error fictional-long-client-secret'))
    notices.deliver(row.operation_id,'verified_success',retry=True)
    operation=db.session.get(CatalogueOperation,row.operation_id)
    assert 'fictional-long-client-secret' not in operation.scope
    operation.finished_at=drafts.now()-timedelta(days=800); db.session.commit()
    prune_operation_history()
    assert db.session.get(CatalogueOperation,row.operation_id) is not None


def test_local_managed_edit_invalidates_previous_failure_backoff(world, delivery):
    row,adapter=publish(world)
    age(11,'full_compared_at','revision_checked_at','related_checked_at')
    original=adapter.product_revisions
    adapter.product_revisions=lambda *args:(_ for _ in ()).throw(ShopifyError('Read temporarily unavailable'))
    status.refresh([world[0]],adapter=adapter)
    assert json.loads(ShopifyProductComparison.query.one().summary_json)['retry_after']
    adapter.product_revisions=original
    change(world,weight=71)
    assert status.refresh([world[0]],adapter=adapter)['full_comparisons']==1
    assert displayed(world[0])['label']=='Update Available'
