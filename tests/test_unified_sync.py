"""Main-review authority, durable partial recovery and local startup isolation."""
import copy
import json
import time
from datetime import timedelta
from pathlib import Path

import pytest
from app import db, shopify_drafts as drafts, scheduled_tasks as tasks
from app.models import CatalogueOperation, ScheduledTask, ScheduledTaskRun, Product, ShopifyProductComparison
from app.shopify_connection import ShopifyError
from app.shopify_products import states_for
from app import catalogue_startup as startup
from test_shopify_drafts import world
from test_shopify_publication_sales import published, clock, AcceptanceAdapter, VariableAcceptanceAdapter
from test_shopify_simple_updates import change
from test_shopify_variables import variable_world
from test_metadata_editor_core import core
from test_phase3_woo_publish_preview import preview_app


def test_main_publication_only_signed_update(world,published):
    product,row,adapter,notices=published
    original=(row.shopify_product_id,copy.deepcopy(drafts.evidence(row)['media']))
    change(world,live=True,shopify_status='ACTIVE',shopify_channels={'online-store':True,'shop':False})
    plan,token=drafts.preview(product,adapter)
    assert plan['classification']=='update' and token
    assert not any('write' in c for c in adapter.calls)
    result=drafts.confirm(token,True,adapter)
    assert result.state=='verified'
    assert adapter.remote['status']=='ACTIVE' and adapter.memberships=={'gid://shopify/Publication/1'}
    assert original==(result.shopify_product_id,drafts.evidence(result)['media'])
    assert adapter.calls.count('status_write')==1 and adapter.calls.count('publication_write')==1
    assert 'variant_update' not in adapter.calls
    assert db.session.get(CatalogueOperation,result.operation_id).status=='succeeded'
    with __import__('flask').current_app.test_request_context():
        assert states_for([product])[product.id]['label']=='Up to Date'
    again,token=drafts.preview(product,adapter)
    assert again['classification']=='no_change' and token is None


@pytest.mark.parametrize('timeout_after',[True,False])
def test_unified_uncertain_recovery_never_replays(world,published,timeout_after):
    product,row,adapter,_=published
    change(world,live=True,shopify_channels={'online-store':True},description='Reviewed full description')
    if timeout_after: adapter.channel_timeout=True
    else:
        def timeout(*a): raise ShopifyError('Response not available')
        adapter.set_publication=timeout
    _,token=drafts.preview(product,adapter)
    drafts.confirm(token,True,adapter)
    assert row.state=='uncertain'
    count=len(adapter.calls)
    if timeout_after:
        drafts.reconcile(row,adapter=adapter)
        assert row.state=='verified'
    else:
        with pytest.raises(ShopifyError,match='differs|partial'): drafts.reconcile(row,adapter=adapter)
        assert row.state=='uncertain'
    assert all('write' not in c for c in adapter.calls[count:])


def test_source_channel_or_remote_change_invalidates_signature(world,published):
    product,row,adapter,_=published
    change(world,live=True,shopify_channels={'online-store':True})
    _,token=drafts.preview(product,adapter)
    adapter.memberships={'gid://shopify/Publication/1'}
    before=len(adapter.calls)
    with pytest.raises(ShopifyError,match='changed'): drafts.confirm(token,True,adapter)
    assert all('write' not in c for c in adapter.calls[before:])
    assert row.state=='verified'


def test_blocked_publication_keeps_metadata_partial_and_no_visibility_write(world,published):
    product,row,adapter,_=published
    change(world,live=True,shopify_channels={'online-store':True},description='Metadata can be applied independently')
    original=adapter.test_connection
    adapter.test_connection=lambda c:{**original(c),'scopes':[s for s in original(c)['scopes'] if s!='write_publications']}
    plan,token=drafts.preview(product,adapter)
    assert token and plan['publication']['blockers']
    drafts.confirm(token,True,adapter)
    assert adapter.remote['status']=='DRAFT' and not getattr(adapter,'memberships',set())
    assert adapter.remote['descriptionHtml']=='Metadata can be applied independently'
    assert db.session.get(CatalogueOperation,row.operation_id).status=='partial'
    from app.shopify_notifications import complete
    assert not complete(row,drafts.evidence(row))


@pytest.mark.parametrize('status',['DRAFT','ARCHIVED'])
def test_status_and_explicit_unpublish_keep_unmanaged_membership(world,published,status):
    product,row,adapter,_=published
    adapter.remote['status']='ACTIVE';adapter.memberships={'gid://shopify/Publication/1','gid://shopify/Publication/2'}
    change(world,live=False,shopify_status=status,shopify_channels={'online-store':False})
    _,token=drafts.preview(product,adapter)
    drafts.confirm(token,True,adapter)
    assert adapter.remote['status']==status
    assert adapter.memberships=={'gid://shopify/Publication/2'}


def test_variable_main_review_publication(variable_world):
    product,shared,*_=variable_world
    adapter=VariableAcceptanceAdapter()
    _,token=drafts.preview(product,adapter);row=drafts.confirm(token,True,adapter)
    assert row.state=='verified'
    identities=copy.deepcopy(drafts.evidence(row)['variant_maps'])
    source=json.loads(shared.read_text());source.update(live=True,shopify_channels={'online-store':True})
    shared.write_text(json.dumps(source))
    plan,token=drafts.preview(product,adapter)
    assert token and plan['publication']['pending']
    drafts.confirm(token,True,adapter)
    assert row.state=='verified' and adapter.remote['status']=='ACTIVE'
    assert identities==drafts.evidence(row)['variant_maps']


@pytest.mark.parametrize('variable',[False,True])
def test_new_creation_includes_reviewed_availability(world,variable_world,variable):
    # Both layouts use the original publisher: Draft -> verified media -> only
    # then the signed availability stages. No write occurs on Preview.
    if variable:
        product,shared,*_=variable_world
        value=json.loads(shared.read_text());value.update(live=True,shopify_channels={'online-store':True})
        shared.write_text(json.dumps(value));adapter=VariableAcceptanceAdapter()
    else:
        product=world[0];change(world,live=True,shopify_channels={'online-store':True});adapter=AcceptanceAdapter()
    _,token=drafts.preview(product,adapter)
    assert not any('write' in c for c in adapter.calls)
    row=drafts.confirm(token,True,adapter)
    assert row.state=='verified' and adapter.remote['status']=='ACTIVE'
    assert adapter.memberships=={'gid://shopify/Publication/1'}
    assert db.session.get(CatalogueOperation,row.operation_id).status=='succeeded'


def test_banner_not_a_generic_partial_or_paused_alarm(world):
    task=db.session.get(ScheduledTask,tasks.KEY)
    tasks.enqueue(manual=True);tasks.claim('test')
    run=db.session.get(ScheduledTaskRun,task.active_run_id)
    data=json.loads(run.data_json);data.update(queue=[],reason='Optional media deferred')
    tasks.save_progress(task.key,run.id,'test',data,terminal='partially_completed')
    assert tasks.activity() is None
    run.error='Missing critical read_products scope';db.session.commit()
    activity=tasks.activity()
    assert activity['attention'] and activity['run_id']==run.id and 'read_products' in activity['reason']


def wait_scan(operation_id):
    for _ in range(150):
        db.session.expire_all()
        op=db.session.get(CatalogueOperation,operation_id)
        if op.status!='running': return op
        time.sleep(.02)
    raise AssertionError('Local scanner failed to complete')


def test_startup_incremental_three_layouts_restart_and_missing_source(core,monkeypatch):
    app,client,root,_,parents,*_=core
    monkeypatch.setattr('requests.sessions.Session.request',lambda *a,**k:pytest.fail('Startup must be offline'))
    with app.app_context():
        before={p.source_relpath:(p.id,p.sku,[(v.id,v.sku) for v in p.variations]) for p in Product.query.all()}
        indices={str(p):p.read_bytes() for p in root.rglob('sku_index.json')}
        authored={str(p):p.read_bytes() for p in root.rglob('product_info.json')}
        run_id=startup.enqueue();assert startup.enqueue()==run_id
        assert tasks.claim('startup')==(startup.KEY,run_id)
        for _ in range(10):
            startup.run_slice(startup.KEY,run_id,'startup')
            run=db.session.get(ScheduledTaskRun,run_id);data=json.loads(run.data_json)
            if data.get('scan_operation'): assert wait_scan(data['scan_operation']).status in {'succeeded','partial'}
            if run.completed_at: break
            db.session.execute(__import__('sqlalchemy').update(ScheduledTask).where(ScheduledTask.key==startup.KEY).values(owner='startup',lease_until=tasks.now()+tasks.LEASE,resume_at=None));db.session.commit()
        assert db.session.get(ScheduledTaskRun,run_id).state=='completed'
        assert before=={p.source_relpath:(p.id,p.sku,[(v.id,v.sku) for v in p.variations]) for p in Product.query.all()}
        assert authored=={str(p):p.read_bytes() for p in root.rglob('product_info.json')}
        assert indices=={str(p):p.read_bytes() for p in root.rglob('sku_index.json')}
        assert startup.selection()[0]==[]
        target=root/'Simple Collection'/'product_info.json'
        value=json.loads(target.read_text());value['description']='Only this collection changed';target.write_text(json.dumps(value))
        assert startup.selection()[0]==['Simple Collection']


def test_startup_waits_for_existing_catalogue_lease(core):
    app,*_=core
    from app.utils.operation_control import acquire_catalogue_operation,finish_catalogue_operation
    with app.app_context():
        lease=acquire_catalogue_operation('product_update',{})
        run_id=startup.enqueue();tasks.claim('worker')
        startup.run_slice(startup.KEY,run_id,'worker')
        run=db.session.get(ScheduledTaskRun,run_id)
        assert run.state=='paused' and 'scan_operation' not in json.loads(run.data_json)
        finish_catalogue_operation(lease.id,status='succeeded')


def test_projected_scope_queues_only_actual_managed_revision(world,published):
    from app.models import CatalogueOperationItem
    product,row,adapter,_=published
    operation=CatalogueOperation(id='f'*32,operation_type='product_update',status='succeeded')
    db.session.add(operation);db.session.flush()
    db.session.add(CatalogueOperationItem(operation_id=operation.id,source_path=product.source_relpath,sku=product.sku,
        status='succeeded',database_state='committed'))
    db.session.commit()
    original=drafts.evidence(row)
    change(world,production_notes='Not managed on Shopify')
    assert tasks.queue_projected(operation.id) is None
    change(world,description='Actual effective managed change')
    run_id=tasks.queue_projected(operation.id)
    assert json.loads(db.session.get(ScheduledTaskRun,run_id).data_json)['scope']==[product.id]
    assert original==drafts.evidence(row)
    assert not any('write' in c for c in adapter.calls)


def test_last_checkpoint_retained_without_becoming_current_equality(world,published):
    from app.shopify_comparison import invalidate,current
    product,row,adapter,_=published
    saved=ShopifyProductComparison.query.one();before=json.loads(saved.summary_json)
    invalidate(row.store_id,row.source_identity)
    retained=json.loads(saved.summary_json)
    assert retained['invalidated'] and retained['full_compared_at']==before['full_compared_at']
    assert retained['local']==before['local'] and retained['classification']==before['classification']
    assert current(product,saved,row,{},freshness=False) is None
    drafts.preview(product,adapter)
    assert not json.loads(saved.summary_json).get('invalidated')


@pytest.mark.parametrize('problem',['missing_channel','conflict','incomplete','missing_scope'])
def test_main_publication_blockers_never_grant_write_authority(world,published,problem):
    product,row,adapter,_=published
    change(world,live=True,shopify_channels={'online-store':True})
    if problem=='missing_channel': change(world,shopify_channels={'not-installed':True})
    elif problem=='conflict': change(world,live=False,shopify_channels={'online-store':True})
    elif problem=='incomplete': adapter.incomplete_channels=True
    else:
        original=adapter.test_connection
        adapter.test_connection=lambda c:{**original(c),'scopes':['read_products','write_products']}
    plan,token=drafts.preview(product,adapter)
    assert plan['publication']['pending'] and token is None
    assert not any('write' in c for c in adapter.calls)


def test_completed_scan_handoff_is_after_commit_and_before_remote_work(core,monkeypatch):
    from test_metadata_editor_core import save
    from app.utils.scan_runner import start_scan
    from app.models import CatalogueOperationItem
    app,*_=core
    handoffs=[]
    monkeypatch.setattr('app.routes.start_scan',start_scan)
    def handoff(operation_id):
        op=db.session.get(CatalogueOperation,operation_id)
        assert op.status!='running'
        assert CatalogueOperationItem.query.filter_by(operation_id=op.id,database_state='committed').count()>0
        handoffs.append(op.id)
    monkeypatch.setattr(tasks,'queue_projected',handoff)
    monkeypatch.setattr('requests.sessions.Session.request',lambda *a,**k:pytest.fail('Local save must not call Shopify'))
    response=save(core,data={'description':'Save → targeted scan → queue read-only comparison'})
    assert response.status_code==200
    with app.app_context():
        operation_id=response.json['operation_id']
        assert wait_scan(operation_id).products_succeeded>0
        for _ in range(100):
            if handoffs: break
            time.sleep(.01)
        assert handoffs==[operation_id]
        assert Product.query.filter_by(source_relpath='Simple Collection/Blue Token').one().description.startswith('Save →')


def test_startup_source_removal_is_reported_without_deletion(core):
    app,_,root,*_=core
    with app.app_context():
        # Move only an isolated fixture, never production sources.
        (root/'Simple Collection'/'Green Token').rename(root/'_temporarily-unmounted-fixture')
        _,_,missing=startup.selection()
        assert 'Simple Collection/Green Token' in missing
        assert Product.query.filter_by(source_relpath='Simple Collection/Green Token').one().catalogue_status=='active'


def test_new_auto_assigned_channels_require_explicit_review(world):
    adapter=AcceptanceAdapter();original=adapter.publications
    def automatically_assigned(config):
        result=original(config);result[0]['autoPublish']=True;return result
    adapter.publications=automatically_assigned
    change(world,live=True)
    plan,token=drafts.preview(world[0],adapter)
    assert plan['publication']['blockers'] and token  # Draft creation only
    row=drafts.confirm(token,True,adapter)
    assert row.state=='verified' and adapter.remote['status']=='DRAFT'
    assert db.session.get(CatalogueOperation,row.operation_id).status=='partial'
    assert 'status_write' not in adapter.calls


def test_new_processing_product_never_activates_on_read_only_recovery(world):
    adapter=AcceptanceAdapter();adapter.processing=True
    change(world,live=True,shopify_channels={'online-store':True})
    _,token=drafts.preview(world[0],adapter)
    row=drafts.confirm(token,True,adapter)
    assert row.state=='processing' and adapter.remote['status']=='DRAFT'
    for i,node in enumerate(adapter.remote['media']['nodes']):
        node.update(status='READY',image={'url':f'https://cdn.shopify.com/{i}.png'})
    count=len(adapter.calls)
    drafts.reconcile(row,adapter=adapter)
    assert row.state=='verified' and adapter.remote['status']=='DRAFT'
    assert all('write' not in c for c in adapter.calls[count:])
    plan,token=drafts.preview(world[0],adapter)
    assert token and plan['publication']['pending']


def test_unexpected_new_channel_assignment_blocks_activation(world):
    adapter=AcceptanceAdapter();original=adapter.create_draft
    def create(*args):
        result=original(*args);adapter.memberships={'gid://shopify/Publication/2'};return result
    adapter.create_draft=create
    change(world,live=True,shopify_channels={'online-store':True})
    _,token=drafts.preview(world[0],adapter)
    row=drafts.confirm(token,True,adapter)
    assert row.state=='uncertain' and adapter.remote['status']=='DRAFT'
    assert 'status_write' not in adapter.calls
    drafts.reconcile(row,adapter=adapter)
    assert row.state=='verified' and adapter.remote['status']=='DRAFT'


def test_old_publication_proof_cannot_verify_new_blocked_operation(world,published):
    product,row,adapter,_=published
    change(world,live=True,shopify_channels={'online-store':True})
    _,token=drafts.preview(product,adapter);drafts.confirm(token,True,adapter)
    assert adapter.remote['status']=='ACTIVE'
    adapter.remote['status']='DRAFT';adapter.memberships=set()
    change(world,description='Fresh metadata, old publication proof is not new authority')
    original=adapter.test_connection
    adapter.test_connection=lambda c:{**original(c),'scopes':[s for s in original(c)['scopes'] if s!='write_publications']}
    _,token=drafts.preview(product,adapter);drafts.confirm(token,True,adapter)
    assert row.state=='verified' and adapter.remote['status']=='DRAFT'
    assert db.session.get(CatalogueOperation,row.operation_id).status=='partial'


def test_approved_sale_after_unified_publication_retains_durable_success(world,published,clock):
    from app import shopify_sales as sales
    from test_shopify_publication_sales import approve
    product,row,adapter,_=published
    change(world,live=True,shopify_channels={'online-store':True})
    _,token=drafts.preview(product,adapter);drafts.confirm(token,True,adapter)
    schedule,_=approve(world,published,live=True,shopify_channels={'online-store':True})
    clock[0]=__import__('datetime').datetime(2026,11,1,10)
    assert sales.transition(schedule,adapter)=='active'
    assert adapter.remote['status']=='ACTIVE'
    from app.shopify_notifications import complete
    assert complete(row,drafts.evidence(row))
