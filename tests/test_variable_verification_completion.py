"""Acknowledged writes are not replayable just because readback is delayed."""
import copy
import json
from flask import current_app
import pytest
from app import db, shopify_drafts as drafts, shopify_variables as variable
from app.models import CatalogueOperation,ShopifyProductIdentity,ShopifyProductComparison,Product
from test_phase3_woo_publish_preview import preview_app,_client
from test_shopify_drafts import world
from test_shopify_variables import variable_world,rescan
from test_variable_creation_recovery import large_variable,finish_uploads,InterruptedBeforeVariants,assert_complete
from test_manual_publishing_worker import step


class DelayedVariants(InterruptedBeforeVariants):
    def create_variants(self,*args):
        before=copy.deepcopy(self.remote)
        result=super().create_variants(*args)
        self.old_snapshot=before;self.stale_reads=getattr(self,'delay',1)
        return result
    def read_variable(self,*args,**kwargs):
        if getattr(self,'stale_reads',0)>0:
            self.stale_reads-=1
            return copy.deepcopy(self.old_snapshot)
        return super().read_variable(*args,**kwargs)


def approve_recovery(w,adapter,monkeypatch,large=True):
    product=large_variable(w) if large else w[0]
    row=finish_uploads(product,adapter);assert row.state=='uncertain'
    adapter.interrupt=False
    plan,token=variable.preview(product,adapter,resume_creation=True);assert token,plan['blockers']
    monkeypatch.setattr(variable,'ShopifyAdapter',lambda:adapter)
    client=_client(current_app)
    response=client.post(f'/products/{product.id}/shopify/review',data={'action':'confirm','review':token,'acknowledge':'yes'},headers={'Accept':'application/json'})
    assert response.status_code==202,response.json
    return product,row,response.json['operation_id'],token


def test_acknowledged_variants_pending_readback_finishes_original_operation(variable_world,monkeypatch):
    adapter=DelayedVariants();adapter.delay=2
    product,row,operation_id,token=approve_recovery(variable_world,adapter,monkeypatch)
    step(adapter)
    assert row.state=='processing',drafts.evidence(row).get('error')
    op=db.session.get(CatalogueOperation,operation_id)
    assert op.status=='pending' and op.recovery_state in {None,'none'} and op.products_failed==0
    assert len(drafts.evidence(row)['variant_maps'])==14
    from app.shopify_notifications import product_result,KEY
    assert op.finished_at is None
    assert 'verified_success' not in json.loads(op.scope).get(KEY,{})
    writes=list(adapter.calls)
    for _ in range(6):
        step(adapter)
        if row.state=='verified':break
    assert_complete(product,row,adapter)
    assert row.operation_id==operation_id and op.status=='succeeded'
    assert adapter.calls.count('variants_create')==writes.count('variants_create')
    assert adapter.calls.count('variants_update')==writes.count('variants_update')
    assert product_result(op)['outcome']=='verified_success'
    summary=json.loads(ShopifyProductComparison.query.filter_by(source_identity=product.source_relpath).one().summary_json)
    assert summary['identity']==drafts.digest(row.media_evidence_json)
    assert _client(current_app).post(f'/products/{product.id}/shopify/progress',data={'review':token}).json['state']=='completed'


def test_immediate_completion_retains_current_comparison_identity(variable_world,monkeypatch):
    from app.models import ShopifyProductComparison
    from app.shopify_products import states_for
    adapter=DelayedVariants();adapter.delay=0
    product,row,operation_id,_=approve_recovery(variable_world,adapter,monkeypatch,large=False)
    step(adapter)
    assert row.state=='verified' and db.session.get(CatalogueOperation,operation_id).status=='succeeded'
    summary=json.loads(ShopifyProductComparison.query.filter_by(source_identity=product.source_relpath).one().summary_json)
    assert summary['identity']==drafts.digest(row.media_evidence_json)
    with current_app.test_request_context():assert states_for([product],fresh=True)[product.id]['state']=='no_change'


def test_unattended_live_create_waits_then_finishes_same_operation(variable_world):
    from test_automatic_publishing import authorise,execute
    rescan(variable_world,live=True)
    product=large_variable(variable_world);adapter=DelayedVariants();adapter.interrupt=False;adapter.delay=3
    authorise(adapter)
    retained=None
    for _ in range(20):
        state=execute(product,adapter)
        row=ShopifyProductIdentity.query.one()
        if row.state=='processing':retained=row.operation_id
        if state in {'verified','recovery_required','attention'}:break
    assert state=='verified',drafts.evidence(row).get('error')
    assert retained and row.operation_id==retained
    assert adapter.remote['status']=='ACTIVE'
    assert_complete(product,row,adapter)
    assert db.session.get(CatalogueOperation,retained).status=='succeeded'


def test_restart_pending_verification_never_repeats_variant_writes(variable_world,monkeypatch):
    from datetime import timedelta
    from app.models import ScheduledTask
    from app.manual_publishing import KEY
    adapter=DelayedVariants();adapter.delay=4
    product,row,operation_id,_=approve_recovery(variable_world,adapter,monkeypatch,large=False)
    step(adapter);assert row.state=='processing'
    identity_id=row.id;product_id=product.id;remote=copy.deepcopy(adapter.__dict__)
    task=db.session.get(ScheduledTask,KEY);task.owner='terminated-owner';task.lease_until=drafts.now()-timedelta(seconds=1);task.resume_at=None;db.session.commit()
    db.session.remove();db.engine.dispose()
    replacement=DelayedVariants();replacement.__dict__.update(remote)
    for _ in range(8):
        step(replacement,owner='replacement-worker')
        row=db.session.get(ShopifyProductIdentity,identity_id)
        if row.state=='verified':break
    assert row.state=='verified' and row.operation_id==operation_id
    assert replacement.calls.count('product_create')==1
    assert replacement.calls.count('variants_create')==1 and replacement.calls.count('variants_update')==1
    assert db.session.get(CatalogueOperation,operation_id).status=='succeeded'
    assert db.session.get(Product,product_id).sku==row.sku


@pytest.mark.parametrize('kind',['nonce','transport_ambiguity','never_consistent'])
def test_conflict_or_ambiguous_mutation_is_not_replayed(variable_world,monkeypatch,kind):
    adapter=DelayedVariants();adapter.delay=100 if kind=='never_consistent' else 0
    product,row,operation_id,_=approve_recovery(variable_world,adapter,monkeypatch,large=False)
    original=adapter.create_variants
    def mutate(*args):
        if kind=='transport_ambiguity':adapter.failure_stage='variants_create'
        result=original(*args)
        if kind=='nonce':adapter.remote['metafield']['value']='unrelated-owner'
        return result
    monkeypatch.setattr(adapter,'create_variants',mutate)
    for _ in range(15):
        step(adapter)
        if row.state=='uncertain':break
    assert row.state=='uncertain'
    op=db.session.get(CatalogueOperation,operation_id)
    assert op.status=='partial' and op.recovery_state=='review_required'
    assert op.products_succeeded==0
    writes=copy.deepcopy(adapter.calls)
    for _ in range(2):step(adapter)
    assert adapter.calls.count('variants_create')==writes.count('variants_create')==1
    assert adapter.calls.count('product_create')==1
    assert row.shopify_product_id==adapter.remote['id']


def test_acknowledged_live_transition_readback_does_not_repeat_status_write(variable_world,monkeypatch):
    rescan(variable_world,live=True)
    class DelayedStatus(DelayedVariants):
        def update_simple_product(self,config,value):
            result=super().update_simple_product(config,value)
            if value.get('status')=='ACTIVE':self.publication_delay=3
            return result
        def publication_state(self,*args):
            result=super().publication_state(*args)
            if getattr(self,'publication_delay',0):
                self.publication_delay-=1;result['status']='DRAFT'
            return result
    adapter=DelayedStatus();adapter.delay=0
    product,row,operation_id,_=approve_recovery(variable_world,adapter,monkeypatch,large=False)
    for _ in range(8):
        step(adapter)
        if row.state=='verified':break
    assert row.state=='verified',drafts.evidence(row).get('error')
    assert adapter.remote['status']=='ACTIVE' and adapter.calls.count('status_write')==1
    assert db.session.get(CatalogueOperation,operation_id).status=='succeeded'


def test_read_only_inspection_is_idempotent_and_does_not_activate_live(variable_world,monkeypatch):
    rescan(variable_world,live=True)
    adapter=DelayedVariants();adapter.delay=1
    product,row,operation_id,_=approve_recovery(variable_world,adapter,monkeypatch,large=False)
    step(adapter);assert row.state=='processing' and adapter.remote['status']=='DRAFT'
    writes=copy.deepcopy(adapter.calls)
    for _ in range(3):drafts.finalize_processing(row,adapter)
    assert row.operation_id==operation_id and adapter.remote['status']=='DRAFT'
    assert adapter.calls.count('variants_create')==writes.count('variants_create')
    assert 'status_write' not in adapter.calls
    step(adapter)
    assert row.state=='verified' and adapter.remote['status']=='ACTIVE'


def test_package_acknowledgement_is_in_final_comparison_not_a_stale_hash(variable_world):
    from test_shopify_package_assignments import linked,VariantAdapter
    from app.shopify_products import states_for
    package=linked();rescan(variable_world,packaging={'selected_package_id':package.id})
    product=variable_world[0];adapter=VariantAdapter()
    _,token=drafts.preview(product,adapter);row=drafts.confirm(token,True,adapter)
    assert row.state=='verified' and db.session.get(CatalogueOperation,row.operation_id).status=='succeeded'
    value=json.loads(ShopifyProductComparison.query.filter_by(source_identity=product.source_relpath).one().summary_json)
    assert value['identity']==drafts.digest(row.media_evidence_json)
    assert all(p['state']=='accepted' and not p['independently_verified'] for p in drafts.evidence(row)['package_assignments'].values())
    with current_app.test_request_context():assert states_for([product],fresh=True)[product.id]['state']=='no_change'


@pytest.mark.parametrize('workers',[2,4])
def test_one_owner_resumes_acknowledged_verification(variable_world,monkeypatch,workers):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from datetime import timedelta
    from app import scheduled_tasks as tasks
    from app.models import ScheduledTask
    from app.manual_publishing import KEY
    adapter=DelayedVariants();adapter.delay=3
    product,row,operation_id,_=approve_recovery(variable_world,adapter,monkeypatch,large=False)
    step(adapter);assert row.state=='processing'
    task=db.session.get(ScheduledTask,KEY);task.resume_at=drafts.now()-timedelta(seconds=1);db.session.commit()
    app=current_app._get_current_object();barrier=Barrier(workers)
    def claim(i):
        with app.app_context():
            barrier.wait();result=tasks.claim('verification-owner-'+str(i));db.session.remove();return i,result
    with ThreadPoolExecutor(workers) as pool:results=list(pool.map(claim,range(workers)))
    winners=[(i,r) for i,r in results if r]
    assert len(winners)==1
    i,claimed=winners[0];db.session.expire_all();tasks.run_slice(*claimed,'verification-owner-'+str(i),adapter=adapter)
    for _ in range(4):
        step(adapter)
        if row.state=='verified':break
    assert row.state=='verified' and row.operation_id==operation_id
    assert adapter.calls.count('variants_create')==1


def test_crash_after_verified_receipt_before_terminal_transition_seals_original(variable_world,monkeypatch):
    from datetime import timedelta
    from app.models import ScheduledTask
    from app.manual_publishing import KEY
    from app.utils import operation_control as control
    adapter=DelayedVariants();adapter.delay=0
    product,row,operation_id,_=approve_recovery(variable_world,adapter,monkeypatch,large=False)
    finish=control.finish_catalogue_operation
    def crash(oid,**kwargs):
        if oid==operation_id and kwargs['status']=='succeeded':raise SystemExit('Backend terminated after durable verification')
        return finish(oid,**kwargs)
    monkeypatch.setattr(control,'finish_catalogue_operation',crash)
    with pytest.raises(SystemExit):step(adapter)
    assert row.state=='verified' and drafts.evidence(row)['verification_wait']['state']=='completed'
    task=db.session.get(ScheduledTask,KEY);task.lease_until=drafts.now()-timedelta(seconds=1);task.resume_at=None;db.session.commit()
    monkeypatch.setattr(control,'finish_catalogue_operation',finish)
    writes=copy.deepcopy(adapter.calls);step(adapter,owner='finalisation-replacement')
    assert db.session.get(CatalogueOperation,operation_id).status=='succeeded'
    assert adapter.calls.count('variants_create')==writes.count('variants_create')==1


def test_documented_completion_probe_is_read_only(variable_world,monkeypatch,capsys):
    from pathlib import Path
    adapter=DelayedVariants();adapter.delay=0
    product,row,operation_id,_=approve_recovery(variable_world,adapter,monkeypatch,large=False)
    step(adapter)
    path=Path(db.engine.url.database);before=path.read_bytes()
    text=(Path(__file__).parents[1]/'docs/PUBLISHING_VERIFICATION_COMPLETION.md').read_text()
    probe=text.split("python - <<'PY'\n",1)[1].split('\nPY\n',1)[0]
    probe=probe.replace("Path('/app/instance/site.db')",f'Path({str(path)!r})').replace("SKU='3DCO-0001'",f'SKU={product.sku!r}')
    exec(compile(probe,'read-only-completion-evidence','exec'),{})
    result=json.loads(capsys.readouterr().out)
    assert result['database_writes']==result['shopify_requests']==0
    assert result['comparisons'][0]['identity_digest_matches']
    assert path.read_bytes()==before and row.state=='verified'


@pytest.mark.parametrize('interruption', ['none','preflight_yield','final_yield','final_exception','final_mismatch'])
def test_full_manual_variable_proof_update_keeps_current_receipts(variable_world,monkeypatch,interruption):
    """ADOC's full signed worker route, not the configuration-only shortcut."""
    from flask import g
    from app.shopify_capacity import BackgroundYield
    from app.shopify_notifications import product_result, KEY as notifications_key
    from test_adoc_status_handoff import AdocAdapter
    monkeypatch.setenv('TLC_SCHEDULER_READY_AT','99999999999')
    monkeypatch.setenv('SHOPIFY_NATIVE_DIMENSIONS_ENABLED','true')
    monkeypatch.setattr('requests.sessions.Session.request',lambda *a,**k:pytest.fail('External network'))
    monkeypatch.setattr('app.utils.discord.publishing_message',lambda *a,**k:(False,'disabled'))
    custom={'storefront_enabled':True,'proof_policy':'none','fields':{
        'dogs_name':{'type':'text','label':'Dogs Name','required':True,'min_length':2,'max_length':16,'order':0}}}
    rescan(variable_world,live=True,customisation=custom)
    product=variable_world[0];source_identity=product.source_relpath;adapter=AdocAdapter()
    _,token=drafts.preview(product,adapter);row=drafts.confirm(token,True,adapter)
    old_operation=row.operation_id;old_receipt=db.session.get(CatalogueOperation,old_operation).scope
    remote_id=row.shopify_product_id;variants=copy.deepcopy(adapter.remote['variants']);media=copy.deepcopy(adapter.remote['media'])
    assert db.session.get(CatalogueOperation,old_operation).status=='succeeded'
    rescan(variable_world,customisation={**custom,'proof_policy':'optional'})
    plan,token=drafts.preview(product,adapter)
    assert token and plan['scope']=='full'
    assert [f['field'] for f in plan['fields'] if f['changed']]==['customisations.configuration']
    g.pop('shopify_confirm_lease',None);g.pop('shopify_review_ticket',None)
    response=_client(current_app).post(f'/products/{product.id}/shopify/review',
        data={'action':'confirm','review':token,'acknowledge':'yes'},headers={'Accept':'application/json'})
    assert response.status_code==202,response.json
    operation_id=response.json['operation_id'];original=adapter.read_variable;used=[]
    def read(*args,**kwargs):
        receipt=drafts.evidence(row);wait=receipt.get('verification_wait') or {}
        final=wait.get('operation_id')==operation_id and wait.get('phase')=='final'
        if not used and (interruption=='preflight_yield' or final and interruption!='none'):
            used.append(True)
            if interruption.endswith('yield'):raise BackgroundYield('Interactive work temporarily owns capacity')
            if interruption=='final_exception':raise KeyError('config_digest SECRET_TOKEN signed-url customer-answer')
            result=original(*args,**kwargs);result['title']='Genuine remote mismatch';return result
        return original(*args,**kwargs)
    monkeypatch.setattr(adapter,'read_variable',read)
    adapter.calls.clear();step(adapter)
    op=db.session.get(CatalogueOperation,operation_id)
    if interruption in {'preflight_yield','final_yield'}:
        assert op.status=='pending' and op.recovery_state in {None,'none'}
        assert not json.loads(op.scope).get(notifications_key), 'Deferral must not generate failed-before-write notifications'
        assert adapter.calls.count('configuration_value_write')==int(interruption=='final_yield')
        # Lose the backend DB session between slices. The retained approval and
        # acknowledgements, not the browser, drive the replacement worker.
        identity_id=row.id;db.session.remove();db.engine.dispose()
        step(adapter,owner='proof-replacement');row=db.session.get(ShopifyProductIdentity,identity_id)
        op=db.session.get(CatalogueOperation,operation_id)
    receipt=drafts.evidence(row)
    assert row.operation_id==operation_id and row.shopify_product_id==remote_id
    assert adapter.calls.count('configuration_value_write')==1
    assert not any(c in adapter.calls for c in ('product_create','binary_upload','variants_create','variants_update','native_write','publication_write','status_write'))
    assert adapter.remote['variants']==variants and adapter.remote['media']==media and adapter.remote['status']=='ACTIVE'
    assert receipt['storefront_configuration']['state']=='verified'
    assert receipt['storefront_configuration']['operation_id']==operation_id
    assert receipt['verification']['operation_id']==operation_id
    assert db.session.get(CatalogueOperation,old_operation).scope==old_receipt
    if interruption=='final_exception':
        assert op.recovery_state=='review_required' and row.state=='uncertain'
        assert receipt['verification_wait']['phase']=='final' and receipt['verification_wait']['state']=='recovery_required'
        assert all(receipt['verification'][k]=='verified' for k in ('identity','fields','media','memberships','attributes'))
        diagnostic=json.loads(op.scope)['operation_summary']['diagnostic']
        assert diagnostic['category']=='KeyError' and diagnostic['stage']=='variable_readback.final'
        assert 'SECRET_TOKEN' not in op.scope+row.media_evidence_json+str(op.error)
        result=product_result(op)
        assert result['outcome']=='recovery_required' and result['verified_stages']
        writes=list(adapter.calls);step(adapter)
        assert adapter.calls==writes, 'Unknown exceptions must not auto-resume or repeat writes'
        retry=_client(current_app).post(f'/products/{product.id}/shopify/review',
            data={'action':'confirm','review':token,'acknowledge':'yes'},headers={'Accept':'application/json'})
        assert retry.status_code!=202 and adapter.calls==writes
    elif interruption=='final_mismatch':
        assert op.status=='pending' and row.state=='processing'  # existing bounded readback-lag handling
        assert receipt['verification_wait']['phase']=='final'
        step(adapter);assert db.session.get(CatalogueOperation,operation_id).status=='succeeded'
        assert adapter.calls.count('configuration_value_write')==1
    else:
        assert op.status=='succeeded' and row.state=='verified'
        summary=json.loads(ShopifyProductComparison.query.filter_by(source_identity=source_identity).one().summary_json)
        assert summary['classification']=='no_change' and summary['identity']==drafts.digest(row.media_evidence_json)
        assert product_result(op)['outcome']=='verified_success'


def test_automatic_exhausted_readback_is_terminal_recovery_not_an_active_root(variable_world):
    from test_automatic_publishing import authorise,execute
    rescan(variable_world,live=True)
    product=variable_world[0];adapter=DelayedVariants();adapter.interrupt=False;adapter.delay=100
    authorise(adapter)
    for _ in range(20):
        state=execute(product,adapter)
        if state=='recovery_required':break
    row=ShopifyProductIdentity.query.one();op=db.session.get(CatalogueOperation,row.operation_id)
    assert state=='recovery_required' and row.state=='uncertain'
    assert op.status=='partial' and op.recovery_state=='review_required' and op.products_failed==0
    assert adapter.calls.count('product_create')==adapter.calls.count('variants_create')==1


def test_new_local_edit_during_pending_readback_is_not_overwritten_or_activated(variable_world,monkeypatch):
    from app.shopify_products import states_for
    rescan(variable_world,live=True)
    adapter=DelayedVariants();adapter.delay=1
    product,row,operation_id,_=approve_recovery(variable_world,adapter,monkeypatch,large=False)
    step(adapter);assert row.state=='processing'
    source=variable_world[1];data=json.loads(source.read_text());data['description']='Newer local edit';source.write_text(json.dumps(data))
    step(adapter)
    assert json.loads(source.read_text())['description']=='Newer local edit'
    assert adapter.remote['status']=='DRAFT' and 'status_write' not in adapter.calls
    assert row.state=='uncertain' and db.session.get(CatalogueOperation,operation_id).recovery_state=='review_required'
    with current_app.test_request_context():assert states_for([product],fresh=True)[product.id]['state']=='recovery_required'


def test_automatic_final_read_finishes_inside_readonly_scope_without_losing_operation(variable_world):
    from test_automatic_publishing import authorise,execute
    from app.models import ShopifyPublishJob
    rescan(variable_world,live=True)
    class DelayedFinal(DelayedVariants):
        def update_simple_product(self,config,value):
            before=copy.deepcopy(self.remote);result=super().update_simple_product(config,value)
            if value.get('status')=='ACTIVE':self.old_snapshot=before;self.stale_reads=1
            return result
    product=variable_world[0];adapter=DelayedFinal();adapter.interrupt=False;adapter.delay=0;authorise(adapter)
    assert execute(product,adapter)=='retry'
    row=ShopifyProductIdentity.query.one();operation_id=row.operation_id
    assert drafts.evidence(row)['verification_wait']['phase']=='final'
    assert execute(product,adapter)=='verified'
    assert db.session.get(ShopifyPublishJob,product.id).operation_id==operation_id
    assert db.session.get(CatalogueOperation,operation_id).status=='succeeded'
    assert adapter.calls.count('status_write')==adapter.calls.count('variants_create')==adapter.calls.count('product_create')==1
