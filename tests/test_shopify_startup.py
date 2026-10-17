"""Boot uses the existing read-only Products runner, never a local scanner."""
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from app import db, scheduled_tasks as tasks, catalogue_startup as legacy
from app.models import ScheduledTask, ScheduledTaskRun, CatalogueOperation, ShopifyProductComparison, Product
from app.shopify_connection import ShopifyError
from app.shopify_products import source_fingerprint
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_drafts import world
from test_scheduled_tasks import published, age
from test_shopify_simple_updates import change
from test_shopify_variables import variable_world, VariableAdapter
from test_metadata_editor_core import core, save


def launch():
    run_id=tasks.enqueue_startup()
    assert tasks.claim('boot-owner')==(tasks.KEY,run_id)
    return run_id


@pytest.mark.parametrize('minutes,skip',[(1,True),(9,True),(10,False),(20,False)])
def test_boot_freshness_and_durable_trigger(published,minutes,skip):
    product,row,adapter=published
    age(tasks.now()-timedelta(minutes=minutes))
    task=db.session.get(ScheduledTask,tasks.KEY)
    interval=task.interval_minutes
    original_evidence=row.media_evidence_json
    run_id=launch()
    with db.engine.connect() as other:
        assert other.execute(text('select trigger from scheduled_task_run where id=:id'),{'id':run_id}).scalar()=='startup'
    tasks.run_slice(tasks.KEY,run_id,'boot-owner',adapter=adapter)
    run=db.session.get(ScheduledTaskRun,run_id);data=json.loads(run.data_json)
    assert run.state=='completed' and data['counts']['skipped_recent']==int(skip)
    assert data['counts']['checked']==int(not skip)
    assert ('product_read' in adapter.calls)==(not skip)
    assert task.interval_minutes==interval
    assert row.media_evidence_json==original_evidence
    assert not any('write' in c or 'create' in c for c in adapter.calls)


def test_boot_effective_change_compares_without_erasing_evidence(world,published):
    product,row,adapter=published
    old=ShopifyProductComparison.query.one().summary_json
    change(world,description='A real managed change')
    run_id=launch()
    assert ShopifyProductComparison.query.one().summary_json==old
    tasks.run_slice(tasks.KEY,run_id,'boot-owner',adapter=adapter)
    data=json.loads(db.session.get(ScheduledTaskRun,run_id).data_json)
    assert data['counts']['checked']==1 and data['counts']['updates']==1
    assert not any('write' in c for c in adapter.calls)


@pytest.mark.parametrize('manual',[True,False])
def test_boot_coalesces_existing_owner_without_replacing_queue_or_cursor(world,manual):
    task=db.session.get(ScheduledTask,tasks.KEY)
    task.selection_cursor=42;task.next_due=tasks.now()-timedelta(seconds=1);db.session.commit()
    run_id=tasks.enqueue(manual=manual,product_ids=[world[0].id] if manual else None)
    tasks.claim('existing-owner')
    before=(task.owner,task.lease_until,task.selection_cursor,task.next_due,
        db.session.get(ScheduledTaskRun,run_id).data_json)
    assert tasks.enqueue_startup()==run_id
    assert before==(task.owner,task.lease_until,task.selection_cursor,task.next_due,
        db.session.get(ScheduledTaskRun,run_id).data_json)
    assert ScheduledTaskRun.query.filter_by(task_key=tasks.KEY).count()==1
    assert tasks.claim('other-owner') is None


def test_boot_retains_cursor_and_suppresses_nearby_timer_run(world):
    task=db.session.get(ScheduledTask,tasks.KEY)
    task.interval_minutes=30;task.selection_cursor=5;task.next_due=tasks.now()-timedelta(seconds=1)
    db.session.commit()
    run_id=tasks.enqueue_startup()
    assert task.selection_cursor==5 and task.interval_minutes==30
    assert abs((task.next_due-tasks.now()).total_seconds()-1800)<2
    assert tasks.enqueue()==run_id
    assert ScheduledTaskRun.query.count()==1
    assert tasks.claim('owner')==(tasks.KEY,run_id)
    data=json.loads(db.session.get(ScheduledTaskRun,run_id).data_json)
    tasks.save_progress(tasks.KEY,run_id,'owner',data,terminal='completed')
    assert tasks.enqueue() is None
    assert ScheduledTaskRun.query.count()==1


def test_boot_honours_disabled_setting_and_no_sale_authority(world):
    from app.models import ShopifySaleSchedule
    task=db.session.get(ScheduledTask,tasks.KEY)
    tasks.configure(tasks.KEY,enabled=False,interval=30)
    due=task.next_due
    assert tasks.enqueue_startup() is None
    assert task.enabled is False and task.interval_minutes==30 and task.next_due==due
    assert not ScheduledTaskRun.query.first() and not ShopifySaleSchedule.query.first()
    with pytest.raises(ValueError): tasks.enqueue(tasks.SALE_KEY,startup=True)
    with pytest.raises(ValueError): tasks.enqueue(startup=True,manual=True)


def test_due_sales_keep_priority_over_boot(world):
    boot=tasks.enqueue_startup()
    sale=tasks.enqueue(tasks.SALE_KEY,manual=True)
    assert tasks.claim('owner')==(tasks.SALE_KEY,sale)
    assert db.session.get(ScheduledTask,tasks.KEY).active_run_id==boot


def historical_run(*,owned=False):
    stamp=tasks.now();run_id='a'*32
    db.session.add(ScheduledTask(key=legacy.KEY,enabled=False,interval_minutes=1440,next_due=stamp,
        state='running' if owned else 'paused',active_run_id=run_id,owner='old-owner' if owned else None,
        lease_until=stamp+tasks.LEASE if owned else None))
    db.session.add(ScheduledTaskRun(id=run_id,task_key=legacy.KEY,trigger='startup',created_at=stamp,
        state='running' if owned else 'paused',data_json=json.dumps({'version':1,'queue':['Known collection'],
            'scan_operation':'b'*32,'counts':{'checked':0},'items':[]})))
    db.session.add(CatalogueOperation(id='b'*32,operation_type='append',status='interrupted',
        recovery_state='review_required',scope='{"trigger":"startup","collection_relpath":"Known collection"}'))
    db.session.commit()
    return run_id


@pytest.mark.parametrize('owned',[True,False])
def test_legacy_queue_cannot_resume_scanning_or_erase_history(world,owned):
    run_id=historical_run(owned=owned)
    before=db.session.get(ScheduledTaskRun,run_id).data_json
    boot=tasks.enqueue_startup()
    old=db.session.get(ScheduledTask,legacy.KEY)
    assert not old.enabled
    if owned:
        assert old.owner=='old-owner' and old.active_run_id==run_id
        assert db.session.get(ScheduledTaskRun,run_id).data_json==before
        old.lease_until=tasks.now()-timedelta(seconds=1);db.session.commit();legacy.retire()
    assert old.active_run_id is None
    saved=db.session.get(ScheduledTaskRun,run_id)
    assert saved.state=='cancelled' and json.loads(saved.data_json)['queue']==['Known collection']
    assert db.session.get(CatalogueOperation,'b'*32).recovery_state=='review_required'
    assert tasks.claim('worker')==(tasks.KEY,boot)
    assert ScheduledTaskRun.query.filter_by(task_key=legacy.KEY).count()==1


def test_retirement_leaves_markers_and_pending_scan_recoverable(core,monkeypatch):
    app,_,root,*_=core
    pending=root/'Simple Collection/Blue Token/.update';pending.write_text('known pending edit')
    with app.app_context():
        run_id=historical_run()
        marker=pending.parent/'.scanned';before=marker.read_bytes()
        tasks.enqueue_startup()
        assert pending.read_text()=='known pending edit' and marker.read_bytes()==before
        op=db.session.get(CatalogueOperation,'b'*32)
        assert op.status=='interrupted' and op.recovery_state=='review_required'
        assert db.session.get(ScheduledTaskRun,run_id).state=='cancelled'


@pytest.mark.parametrize('source,kind',[
    ('Simple Collection/Blue Token','override'),('Simple Collection/Blue Token','shared'),
    ('Variable Collection/Badge One','override'),('Variable Collection/Badge One','shared'),('Single Variable','shared')])
def test_editors_keep_fields_and_one_main_review_entry(core,source,kind,monkeypatch):
    app,client,root,_,ids,*_=core
    monkeypatch.setattr('requests.sessions.Session.request',lambda *a,**k:pytest.fail('Rendering must be local'))
    page=client.get(f'/edit_products/{ids[source]}/edit/{kind}')
    assert page.status_code==200
    for marker in (b'publishing-title',b'Desired Shopify status',b'data-channel-choices',b'data-discover-channels'):
        assert marker in page.data
    assert b'Review &amp; Sync saved product, status &amp; channels' not in page.data
    assert b'Save locally, then review Shopify changes from Products or Product Details.' in page.data
    assert b'col-12' in page.data or b'editor-workspace' in page.data


def test_supervisor_queues_once_without_network_or_scanner(core,monkeypatch):
    from app import runtime
    app,*_=core
    starts=[];calls=[]
    monkeypatch.setenv('TLC_MANAGED_RUNTIME','test-restored-afterwards')
    monkeypatch.setattr('app.create_app',lambda **kw:(calls.append(kw) or app))
    monkeypatch.setattr(runtime.signal,'signal',lambda *a:None)
    monkeypatch.setattr(runtime.subprocess,'Popen',lambda args:(starts.append(args) or SimpleNamespace(poll=lambda:0,wait=lambda **k:0)))
    monkeypatch.setattr('requests.sessions.Session.request',lambda *a,**k:pytest.fail('Startup made a network call'))
    monkeypatch.setattr('app.utils.scan_runner.start_scan',lambda *a,**k:pytest.fail('Startup scanned'))
    runtime.main()
    assert calls==[{'recover':True}] and len(starts)==2
    assert starts[1][-2:]==['-m','app.scheduler_worker']
    with app.app_context():
        runs=ScheduledTaskRun.query.all()
        assert len(runs)==2 and all(run.trigger=='startup' for run in runs)
        assert {run.task_key for run in runs}=={tasks.KEY,tasks.ORDERS_KEY}


def test_web_workers_do_not_enqueue_startup(preview_app):
    # create_app is deliberately not the boot hook; Gunicorn worker imports can
    # never queue per-process startup work.
    import inspect
    from app import create_app
    assert 'enqueue_startup' not in inspect.getsource(create_app)
    with preview_app.app_context(): assert ScheduledTaskRun.query.count()==0


def test_boot_failure_does_not_erase_historical_comparison_or_freshness(published):
    product,row,adapter=published
    age(tasks.now()-timedelta(minutes=20))
    saved=ShopifyProductComparison.query.one();old=json.loads(saved.summary_json)
    adapter.test_connection=lambda c:(_ for _ in ()).throw(ShopifyError('Read-only Shopify verification unavailable'))
    run_id=launch();tasks.run_slice(tasks.KEY,run_id,'boot-owner',adapter=adapter)
    now=json.loads(saved.summary_json)
    assert now['full_compared_at']==old['full_compared_at']
    assert db.session.get(ScheduledTaskRun,run_id).state=='failed'


def test_startup_run_history_uses_existing_admin_surface(world):
    from flask import current_app
    run_id=tasks.enqueue_startup()
    client=_client(current_app._get_current_object())
    page=client.get('/settings/tasks/shopify_products/history')
    assert page.status_code==200 and b'Startup' in page.data
    assert client.get('/settings/tasks/runs/'+run_id).status_code==200
