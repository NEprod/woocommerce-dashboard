"""Browser initiation/disconnection is separate from leased backend execution."""
import json
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch
import pytest
from flask import current_app, g
from app import db, shopify_drafts as drafts, scheduled_tasks as tasks, manual_publishing as manual
from app.models import CatalogueOperation, ScheduledTask, ShopifyProductIdentity, Product
from app.publishing_progress import view
from app.utils.operation_live import persisted_log_page
from app.shopify_connection import ShopifyError
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_drafts import world
from test_shopify_gallery_bytes import originals, ByteAdapter


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr('app.utils.discord.publishing_message',lambda data:(False,'disabled'))
    monkeypatch.setenv('TLC_SCHEDULER_READY_AT','99999999999')


def accepted(world,adapter):
    product=world[0];_,token=drafts.preview(product,adapter)
    client=_client(current_app)
    g.pop('shopify_confirm_lease',None);g.pop('shopify_review_ticket',None)
    response=client.post(f'/products/{product.id}/shopify/review',data={'action':'confirm','review':token,'acknowledge':'yes'},headers={'Accept':'application/json'})
    assert response.status_code==202,response.json
    return response.json['operation_id'],token


def step(adapter,owner='manual-worker'):
    task=db.session.get(ScheduledTask,manual.KEY)
    if task.resume_at:task.resume_at=drafts.now()-timedelta(seconds=1);db.session.commit()
    return tasks.tick(owner,adapter=adapter)


def test_browser_can_close_after_accepted_and_worker_finishes_same_operation(world):
    product,content=originals(world)
    adapter=ByteAdapter();op_id,token=accepted(world,adapter)
    assert adapter.calls.count('binary_upload')==0 and 'product_create' not in adapter.calls
    # There is now no HTTP request/client. Execute only the normal scheduler.
    for _ in range(5):
        step(adapter)
        op=db.session.get(CatalogueOperation,op_id)
        if op.status=='succeeded':break
    assert op.status=='succeeded',op.error
    row=ShopifyProductIdentity.query.one()
    assert row.operation_id==op_id and adapter.calls.count('binary_upload')==53 and adapter.calls.count('product_create')==1
    assert view(op)['publishing']['bytes_acknowledged']==53*len(content)
    another=_client(current_app)
    assert another.get('/operations/'+op_id).status_code==200
    assert another.get('/api/operations/'+op_id+'/status').json['operation']['status']=='succeeded'
    assert 'Batch acknowledged' in another.get('/api/operations/'+op_id+'/logs').get_data(as_text=True)
    with patch('app.shopify_drafts.execute_reviewed',side_effect=AssertionError('GET cannot publish')):
        assert another.get('/api/operations/'+op_id+'/status').status_code==200


def test_reconnect_and_repeated_confirm_only_observe_durable_approval(world):
    originals(world);adapter=ByteAdapter();op_id,token=accepted(world,adapter)
    assert step(adapter)
    row=ShopifyProductIdentity.query.one();assert len(drafts.evidence(row)['upload_inputs'])==20
    client=_client(current_app)
    before=adapter.calls.count('binary_upload')
    for _ in range(3):
        response=client.post(f'/products/{world[0].id}/shopify/review',data={'action':'confirm','review':token,'acknowledge':'yes'},headers={'Accept':'application/json'})
        assert response.status_code==200 and response.json['operation_id']==op_id
        assert client.get('/api/operations/'+op_id+'/status').status_code==200
    assert adapter.calls.count('binary_upload')==before
    assert CatalogueOperation.query.filter_by(operation_type='shopify_draft_publish').count()==1


def test_expired_owner_is_stalled_not_browser_heartbeat_and_no_get_write(world):
    originals(world);adapter=ByteAdapter();op_id,_=accepted(world,adapter)
    key,run_id=tasks.claim('expired')
    op=db.session.get(CatalogueOperation,op_id);scope=json.loads(op.scope)
    scope['manual_worker']={'task_key':key,'run_id':run_id,'owner':'expired'}
    scope['manual_review']['state']='running';op.scope=json.dumps(scope);op.status='running'
    task=db.session.get(ScheduledTask,key);task.lease_until=drafts.now()-timedelta(seconds=1);db.session.commit()
    before=op.scope;client=_client(current_app)
    for _ in range(3):
        status=client.get('/api/operations/'+op_id+'/status').json
        assert status['operation']['status']=='stalled' and status['terminal']
    db.session.refresh(op);assert op.scope==before and not adapter.remote
    assert 'product_create' not in adapter.calls


def test_acknowledged_batches_survive_expired_worker_claim_without_reupload(world):
    product,_=originals(world);adapter=ByteAdapter();op_id,_=accepted(world,adapter)
    step(adapter);row=ShopifyProductIdentity.query.one();assert len(drafts.evidence(row)['upload_inputs'])==20
    task=db.session.get(ScheduledTask,manual.KEY);task.owner='dead';task.lease_until=drafts.now()-timedelta(seconds=1);task.resume_at=None;db.session.commit()
    db.session.remove()
    for _ in range(4):
        step(adapter,'replacement')
        if db.session.get(CatalogueOperation,op_id).status=='succeeded':break
    assert db.session.get(CatalogueOperation,op_id).status=='succeeded'
    assert adapter.calls.count('binary_upload')==53 and adapter.calls.count('product_create')==1


def test_async_wait_and_long_readback_do_not_repeat_upload_or_create(world):
    originals(world);adapter=ByteAdapter();adapter.processing=True;op_id,_=accepted(world,adapter)
    for _ in range(3):step(adapter)
    op=db.session.get(CatalogueOperation,op_id);assert view(op)['status']=='waiting'
    for _ in range(2):step(adapter)
    assert adapter.calls.count('binary_upload')==53 and adapter.calls.count('product_create')==1
    for i,m in enumerate(adapter.remote['media']['nodes']):m.update(status='READY',image={'url':f'https://cdn.shopify.com/{i}.png'})
    step(adapter)
    assert db.session.get(CatalogueOperation,op_id).status=='succeeded'


def test_partial_upload_timeout_remains_recovery_required(world):
    originals(world);adapter=ByteAdapter();op_id,_=accepted(world,adapter);step(adapter)
    adapter.failure='upload';step(adapter)
    op=db.session.get(CatalogueOperation,op_id)
    assert op.recovery_state=='review_required'
    count=adapter.calls.count('binary_upload')
    for _ in range(2):step(adapter)
    assert adapter.calls.count('binary_upload')==count and adapter.remote is None


def test_new_local_revision_is_not_published_by_old_approval(world):
    originals(world);adapter=ByteAdapter();op_id,_=accepted(world,adapter);step(adapter)
    shared=world[2];authored=json.loads(shared.read_text());authored['price']='99.00';shared.write_text(json.dumps(authored))
    step(adapter)
    op=db.session.get(CatalogueOperation,op_id)
    assert op.status=='failed' and 'revision' in op.error.lower()
    assert adapter.calls.count('binary_upload')==20 and adapter.remote is None
    assert json.loads(shared.read_text())['price']=='99.00'


@pytest.mark.parametrize('workers',[2,4])
def test_two_and_four_workers_cannot_claim_same_manual_task(world,workers):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    originals(world);adapter=ByteAdapter();accepted(world,adapter)
    app=current_app._get_current_object();barrier=Barrier(workers)
    def claim(index):
        with app.app_context():
            barrier.wait()
            try:return tasks.claim('worker-'+str(index))
            finally:db.session.remove()
    with ThreadPoolExecutor(max_workers=workers) as pool:results=list(pool.map(claim,range(workers)))
    assert sum(result is not None for result in results)==1
    assert next(result for result in results if result)[0]==manual.KEY


def test_progress_logs_are_bounded_and_polling_does_not_persist(world):
    originals(world);adapter=ByteAdapter();op_id,_=accepted(world,adapter)
    for _ in range(3):step(adapter)
    op=db.session.get(CatalogueOperation,op_id);live=view(op)
    assert live['publishing']['uploads_acknowledged']==53
    logs=persisted_log_page(op)['items'];assert 3<=len(logs)<100
    assert not any('https://' in line or 'nonce' in line or 'token' in line for line in logs)
    before=op.scope;client=_client(current_app)
    for _ in range(5):client.get('/api/operations/'+op_id+'/status');client.get('/api/operations/'+op_id+'/logs')
    db.session.refresh(op);assert before==op.scope


@pytest.mark.parametrize('stage',['upload','create'])
def test_abrupt_backend_termination_is_not_a_browser_disconnect_or_safe_retry(world,stage):
    originals(world)
    class Killed(ByteAdapter):
        def upload_image(self,*args):
            if stage=='upload' and self.calls.count('binary_upload')==2:raise SystemExit('fixture worker killed before acknowledgement')
            return super().upload_image(*args)
        def create_draft(self,*args):
            result=super().create_draft(*args)
            if stage=='create':raise SystemExit('fixture worker killed after remote Create, before response persistence')
            return result
    adapter=Killed();op_id,_=accepted(world,adapter)
    with pytest.raises(SystemExit):
        for _ in range(4):step(adapter)
    row=ShopifyProductIdentity.query.one();proof=drafts.evidence(row)
    if stage=='upload':assert proof['upload_attempt']['state']=='pending' and len(proof['upload_inputs'])==2
    else:assert proof['create_attempted'] and adapter.remote
    before=(adapter.calls.count('binary_upload'),adapter.calls.count('product_create'))
    task=db.session.get(ScheduledTask,manual.KEY);task.lease_until=drafts.now()-timedelta(seconds=1);task.resume_at=None;db.session.commit()
    assert view(db.session.get(CatalogueOperation,op_id))['status']=='stalled'
    step(adapter,'replacement-after-kill')
    op=db.session.get(CatalogueOperation,op_id)
    assert op.recovery_state=='review_required'
    assert before==(adapter.calls.count('binary_upload'),adapter.calls.count('product_create'))


def test_waiting_readback_with_dead_scheduler_becomes_stalled_without_mutation(world):
    originals(world);adapter=ByteAdapter();adapter.processing=True;op_id,_=accepted(world,adapter)
    for _ in range(3):step(adapter)
    task=db.session.get(ScheduledTask,manual.KEY);task.resume_at=drafts.now()-timedelta(seconds=121);db.session.commit()
    op=db.session.get(CatalogueOperation,op_id)
    assert view(op)['status']=='stalled'
    assert adapter.calls.count('binary_upload')==53 and adapter.calls.count('product_create')==1


@pytest.mark.parametrize('changed',['connection','admin','proof'])
def test_revoked_approval_and_changed_connection_never_gain_background_authority(world,monkeypatch,changed):
    originals(world);adapter=ByteAdapter();op_id,_=accepted(world,adapter)
    if changed=='connection':monkeypatch.setenv('SHOPIFY_ADMIN_ACCESS_TOKEN','changed-fixture-token')
    elif changed=='admin':
        from app.models import User
        db.session.get(User,1).is_admin=False;db.session.commit()
    else:
        op=db.session.get(CatalogueOperation,op_id);scope=json.loads(op.scope)
        scope['manual_review']['reviewed']['digest']='tampered';op.scope=json.dumps(scope);db.session.commit()
    step(adapter)
    assert db.session.get(CatalogueOperation,op_id).status=='failed'
    assert 'binary_upload' not in adapter.calls and 'product_create' not in adapter.calls


def test_original_operation_probe_is_read_only_and_does_not_import_the_application(world,capsys):
    originals(world);adapter=ByteAdapter();op_id,_=accepted(world,adapter)
    path=Path(db.engine.url.database);before=path.read_bytes()
    doc=(Path(__file__).parents[1]/'docs/BROWSER_INDEPENDENT_PUBLISHING.md').read_text()
    code=doc.split("python - <<'PY'\n",1)[1].split('\nPY\n',1)[0]
    code=code.replace("Path('/app/instance/site.db')",f'Path({str(path)!r})').replace("SKU='3DCO-0001'",f'SKU={world[0].sku!r}')
    assert 'from app' not in code and 'import app' not in code
    exec(compile(code,'read-only-original-operation-probe','exec'),{})
    result=json.loads(capsys.readouterr().out)
    assert result['database_writes']==result['shopify_requests']==0
    assert result['operations'][0]['id']==op_id and result['operations'][0]['execution_path']=='durable manual worker'
    assert before==path.read_bytes() and 'binary_upload' not in adapter.calls


def test_legacy_upload_receipt_does_not_pretend_a_worker_is_running(world):
    originals(world);adapter=ByteAdapter();_,token=drafts.preview(world[0],adapter)
    row=drafts.confirm(token,True,adapter);op=db.session.get(CatalogueOperation,row.operation_id)
    assert row.state=='uploading' and view(op)['status']=='attention'
    assert view(op)['publishing']['uploads_acknowledged']==20
    assert 'Legacy request-scoped' in view(op)['latest_message']


def test_progress_writes_are_stage_or_ack_boundaries_not_transferred_bytes(world):
    from sqlalchemy import event
    originals(world);adapter=ByteAdapter();writes=[]
    def statement(conn,cursor,sql,parameters,context,executemany):
        if sql.lower().startswith('update catalogue_operation'):writes.append(1)
    event.listen(db.engine,'before_cursor_execute',statement)
    try:
        op_id,_=accepted(world,adapter)
        for _ in range(3):step(adapter)
        assert db.session.get(CatalogueOperation,op_id).status=='succeeded'
    finally:event.remove(db.engine,'before_cursor_execute',statement)
    assert 53<=len(writes)<53*5+60


def test_late_old_worker_cannot_save_acknowledgements_after_owner_changes(world):
    from app.shopify_capacity import background_work,BackgroundYield
    originals(world);adapter=ByteAdapter();accepted(world,adapter);step(adapter)
    task=db.session.get(ScheduledTask,manual.KEY);run_id=task.active_run_id
    task.resume_at=None;db.session.commit();assert tasks.claim('old-owner')
    task.owner='new-owner';task.lease_until=tasks.now()+tasks.LEASE;db.session.commit()
    row=ShopifyProductIdentity.query.one();before=row.media_evidence_json;data=drafts.evidence(row)
    with background_work(lambda:tasks.renew(manual.KEY,run_id,'old-owner'),fence=lambda:tasks.fence(manual.KEY,run_id,'old-owner')):
        with pytest.raises(BackgroundYield):drafts.save(row,data,'verified')
    db.session.refresh(row);assert row.media_evidence_json==before and row.state=='uploading'
