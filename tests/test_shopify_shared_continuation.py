"""Durable publishing continuation, using fictional Shopify and isolated SQLite."""
import json
from datetime import timedelta

import pytest
from app import db, shopify_drafts as drafts
from app.models import CatalogueOperation, ShopifyProductIdentity, ScheduledTask
from app.shopify_capacity import BackgroundYield, RequestNotSent, background
from test_manual_publishing_worker import accepted, offline, products_badge, step
from test_phase3_woo_publish_preview import preview_app
from test_shopify_drafts import world
from test_shopify_publication_sales import AcceptanceAdapter
from test_shopify_simple_updates import change
from test_shopify_variables import rescan, variable_world
from test_shopify_taxonomy_products import registry


@pytest.mark.parametrize('mode', ['manual', 'hourly'])
@pytest.mark.parametrize('initial', [True, False])
def test_simple_verified_read_pause_is_not_mutation_recovery(world,monkeypatch,mode,initial):
    from app import automatic_publishing as publisher, scheduled_tasks as tasks, shopify_publication as publication
    from app.models import ShopifyPublishJob
    from test_automatic_publishing import authorise, queued
    from test_publishing_checkpoint_integration import history

    product = world[0]
    product_id = product.id
    change(world,live=True,shopify_channels={'online-store':True})
    adapter = AcceptanceAdapter()
    if not initial:
        op,_ = accepted(world,adapter)
        step(adapter)
        assert db.session.get(CatalogueOperation,op).status == 'succeeded'
        change(world,description='One independently reviewed change')
        adapter.calls.clear()
        adapter.inputs.clear()
    if mode == 'hourly':
        authorise(adapter)
        history(product)
    queued(product)
    if mode == 'manual':
        op,_ = accepted(world,adapter)
        advance = lambda owner:step(adapter,owner=owner)
    else:
        task = db.session.get(ScheduledTask,publisher.KEY)
        task.next_due = drafts.now()-timedelta(seconds=1)
        db.session.commit()
        run = tasks.enqueue(publisher.KEY)
        def advance(owner):
            task = db.session.get(ScheduledTask,publisher.KEY)
            task.resume_at = drafts.now()-timedelta(seconds=1)
            db.session.get(ShopifyPublishJob,product_id).next_attempt = task.resume_at
            db.session.commit()
            assert tasks.claim(owner)==(publisher.KEY,run)
            tasks.run_slice(publisher.KEY,run,owner,adapter=adapter)
    compare = publication.comparison_after_write
    parked = []

    def yield_after_verification(product,result,row,connector):
        if background() and not parked:
            parked.append((row.operation_id,row.shopify_product_id))
            receipt = drafts.evidence(row)
            assert receipt['stage']=='verified' and all(
                receipt['verification'][k]=='verified' for k in ('identity','fields','media','memberships','attributes'))
            raise RequestNotSent('Synthetic read deferred before dispatch.',code='request_budget')
        return compare(product,result,row,connector)

    monkeypatch.setattr(publication,'comparison_after_write',yield_after_verification)
    advance('first-worker')
    op,gid = parked[0]
    receipt = db.session.get(CatalogueOperation,op)
    scope = json.loads(receipt.scope)
    assert receipt.status=='pending' and receipt.recovery_state=='none'
    assert scope['worker_deferral']['deferral']=='request_budget' and scope['worker_deferral']['request_not_sent'] is True
    before_approval = scope.get('manual_review',{}).get('proof')
    db.session.remove();db.engine.dispose()
    advance('replacement-worker')
    receipt = db.session.get(CatalogueOperation,op)
    assert receipt.status=='succeeded' and receipt.recovery_state=='none'
    assert json.loads(receipt.scope).get('manual_review',{}).get('proof')==before_approval
    assert ShopifyProductIdentity.query.one().shopify_product_id==gid
    assert adapter.calls.count('product_create')==int(initial)
    assert adapter.calls.count('binary_upload')==2*int(initial)
    assert adapter.calls.count('variant_write')==int(initial)
    assert adapter.calls.count('status_write')==int(initial)
    assert adapter.calls.count('publication_write')==int(initial)
    assert adapter.calls.count('product_update')==int(not initial)
    if not initial:
        assert adapter.inputs == [{'id':gid,'descriptionHtml':'One independently reviewed change'}]
    assert products_badge(db.session.get(type(product),product_id))['label']=='Up to Date'


@pytest.mark.parametrize('mode', ['manual', 'hourly'])
def test_variable_53_images_14_variants_three_deferrals_same_operation(variable_world, monkeypatch, mode):
    from datetime import timedelta
    from app import shopify_capacity as capacity, shopify_publication as publication
    from app.manual_publishing import KEY
    from app.models import Product, ScheduledTask, ScheduledTaskRun, ShopifyPublishJob
    from test_automatic_publishing import authorise, queued
    from test_manual_publishing_worker import products_badge
    from test_shopify_publication_sales import VariableAcceptanceAdapter
    from test_variable_creation_recovery import large_variable

    rescan(variable_world, live=True, shopify_channels={'online-store': True})
    product = large_variable(variable_world)
    product_id = product.id
    adapter = VariableAcceptanceAdapter()
    if mode == 'manual':
        queued(product)
        op, _ = accepted((product,), adapter)
        task_key = KEY
    else:
        from app import automatic_publishing as publisher, scheduled_tasks as tasks
        from test_publishing_checkpoint_integration import history
        history(product)
        queued(product)
        authorise(adapter)
        task_key = publisher.KEY
        scheduled = db.session.get(ScheduledTask, task_key)
        scheduled.next_due = drafts.now() - timedelta(seconds=1)
        db.session.commit()
        run_id = tasks.enqueue(task_key)
        op = None
    seen = []
    original = publication.comparison_after_write

    def check(product, result, row, connector):
        nonlocal op
        if op is None:
            op = row.operation_id
        phase = drafts.evidence(row).get('verification_wait', {}).get('phase')
        if background() and row.operation_id == op and ((not seen and phase == 'base') or
           (phase == 'final' and len(seen) < 3)):
            seen.append((phase, row.shopify_product_id))
            if len(seen) == 2:
                with monkeypatch.context() as gate:
                    gate.setitem(background(), 'remaining', 0)
                    capacity.before(drafts.configuration())
            else:
                with monkeypatch.context() as gate:
                    gate.setattr(capacity, 'monotonic', lambda: background()['deadline'] + 1)
                    capacity.before(drafts.configuration())
        return original(product, result, row, connector)

    monkeypatch.setattr(publication, 'comparison_after_write', check)
    for attempt in range(25):
        owner = f'worker-{attempt}'
        if mode == 'manual':
            step(adapter, owner=owner)
        else:
            scheduled = db.session.get(ScheduledTask, task_key)
            if scheduled.resume_at:
                scheduled.resume_at = drafts.now() - timedelta(seconds=1)
            job = db.session.get(ShopifyPublishJob, product_id)
            if job.state in {'retry','pending'}:
                job.next_attempt = drafts.now() - timedelta(seconds=1)
            db.session.commit()
            assert tasks.claim(owner) == (task_key, run_id)
            tasks.run_slice(task_key, run_id, owner, adapter=adapter)
        receipt = db.session.get(CatalogueOperation, op) if op else None
        if receipt and receipt.finished_at and receipt.status != 'pending':
            break
        if len(seen) == 1:
            task = db.session.get(ScheduledTask, task_key)
            task.owner = 'terminated-owner'; task.lease_until = drafts.now() - timedelta(seconds=1)
            db.session.commit()
            db.session.remove()
            db.engine.dispose()
    row = ShopifyProductIdentity.query.one()
    assert [phase for phase, _ in seen] == ['base', 'final', 'final']
    assert len({gid for _, gid in seen}) == 1 and seen[0][1] == row.shopify_product_id
    assert row.operation_id == op and receipt.status == 'succeeded' and receipt.recovery_state == 'none'
    assert adapter.calls.count('product_create') == adapter.calls.count('variants_create') == 1
    assert adapter.calls.count('variants_update') == adapter.calls.count('status_write') == adapter.calls.count('publication_write') == 1
    assert adapter.calls.count('binary_upload') == 53
    assert len(drafts.evidence(row)['variant_maps']) == 14
    assert len(adapter.remote['media']['nodes']) == 53 and adapter.remote['status'] == 'ACTIVE'
    if mode == 'hourly':
        assert db.session.get(ScheduledTaskRun, run_id).state == 'completed'
    assert products_badge(db.session.get(Product, product_id))['label'] == 'Up to Date'


def test_due_hourly_does_not_take_over_pending_signed_manual_receipt(variable_world, monkeypatch):
    from datetime import timedelta
    from app import automatic_publishing as publisher, manual_publishing as manual, scheduled_tasks as tasks
    from app import shopify_publication as publication
    from app.models import ScheduledTask, ScheduledTaskRun, ShopifyPublishJob
    from test_automatic_publishing import authorise, queued
    from test_shopify_publication_sales import VariableAcceptanceAdapter

    rescan(variable_world, live=True, shopify_channels={'online-store': True})
    product = variable_world[0]
    adapter = VariableAcceptanceAdapter()
    authorise(adapter)
    queued(product)
    op, _ = accepted((product,), adapter)
    auto = db.session.get(ScheduledTask, publisher.KEY)
    auto.next_due = drafts.now() - timedelta(seconds=1)
    db.session.commit()
    auto_run = tasks.enqueue(publisher.KEY)
    compare = publication.comparison_after_write
    paused = []

    def inject(product, result, row, connector):
        if background() and row.operation_id == op and not paused:
            paused.append(row.shopify_product_id)
            raise RequestNotSent('Synthetic manual read pause')
        return compare(product, result, row, connector)

    monkeypatch.setattr(publication, 'comparison_after_write', inject)
    manual_claim = tasks.claim('manual-owner')
    assert manual_claim[0] == manual.KEY and tasks.claim('hourly-cannot-overlap') is None
    tasks.run_slice(*manual_claim, 'manual-owner', adapter=adapter)
    assert paused and db.session.get(CatalogueOperation, op).status == 'pending'
    auto_claim = tasks.claim('hourly-owner')
    assert auto_claim == (publisher.KEY, auto_run)
    prefix_writes = [c for c in adapter.calls if 'write' in c or 'create' in c or 'upload' in c or c == 'variants_update']
    assert 'status_write' not in prefix_writes and 'publication_write' not in prefix_writes
    cut = len(adapter.calls)
    tasks.run_slice(*auto_claim, 'hourly-owner', adapter=adapter)
    receipt = db.session.get(CatalogueOperation, op)
    assert CatalogueOperation.query.filter_by(operation_type='shopify_draft_publish').count() == 1
    assert receipt.status == 'pending', (receipt.status, auto_run, db.session.get(ScheduledTaskRun,auto_run).state,
        db.session.get(ShopifyPublishJob,product.id).error, adapter.calls[cut:])
    assert [c for c in adapter.calls[cut:] if 'write' in c or 'create' in c or 'upload' in c or c == 'variants_update'] == []
    assert db.session.get(ScheduledTask, manual.KEY).active_run_id == manual_claim[1]
    step(adapter,owner='replacement-manual')
    assert db.session.get(CatalogueOperation, op).status == 'succeeded'
    assert adapter.calls.count('product_create') == adapter.calls.count('status_write') == adapter.calls.count('publication_write') == 1


@pytest.mark.parametrize('changed', ['policy', 'unscanned_source', 'scanned_source'])
def test_changed_hourly_authority_cannot_resume_original_availability(world, monkeypatch, changed):
    from app import automatic_publishing as publisher, shopify_publication as publication
    from app.models import ShopifyPublishJob
    from test_automatic_publishing import authorise, execute

    product = world[0]
    change(world, live=True, shopify_channels={'online-store': True})
    adapter = AcceptanceAdapter()
    authorise(adapter)
    with monkeypatch.context() as paused:
        def stop(*args):
            raise RequestNotSent('Paused before availability.', code='request_budget')
        paused.setattr(publication, 'comparison_after_write', stop)
        assert execute(product, adapter) == 'retry'
    row = ShopifyProductIdentity.query.one()
    op = db.session.get(CatalogueOperation, row.operation_id)
    original = json.loads(op.scope)['automatic_publishing']['authority']
    assert op.status == 'pending' and op.recovery_state == 'none'
    assert adapter.remote['status'] == 'DRAFT' and adapter.calls.count('product_create') == 1

    if changed == 'policy':
        authorise(adapter)  # A new policy revision, not an extension of the old one.
        assert publisher.policy().revision != original['revision']
    else:
        change(world, description='An unapproved description change')
    adapter.calls.clear()
    if changed == 'unscanned_source':
        job = db.session.get(ShopifyPublishJob, product.id)
        outcome = publisher.publish_one(job, owner='replacement', guard=lambda: None, fence=lambda: None, adapter=adapter)
    else:
        outcome = execute(product, adapter)
    assert outcome == 'recovery_required'
    assert not adapter.calls
    assert ShopifyProductIdentity.query.one().operation_id == op.id
    assert json.loads(op.scope)['automatic_publishing']['authority'] == original
    assert adapter.remote['status'] == 'DRAFT'
    assert op.status == 'interrupted' and op.recovery_state == 'review_required'
    assert not CatalogueOperation.query.filter(CatalogueOperation.status.in_({'pending', 'running'})).count()
    assert execute(product, adapter) == 'recovery_required' and not adapter.calls


@pytest.mark.parametrize('mode',['manual','hourly'])
@pytest.mark.parametrize('kill_at',['verified_read','unacknowledged_create'])
def test_fresh_worker_process_restarts_from_persistent_receipt(world,tmp_path,mode,kill_at):
    """Two real interpreter processes; a hard exit leaves the owned lease on disk."""
    import os
    from pathlib import Path
    import subprocess
    import sys

    from app import automatic_publishing as publisher, manual_publishing as manual, scheduled_tasks as tasks
    from test_automatic_publishing import authorise, queued
    from test_publishing_checkpoint_integration import history
    from test_shopify_foundation import TOKEN

    product = world[0]
    product_id = product.id
    change(world,live=True,shopify_channels={'online-store':True})
    adapter = AcceptanceAdapter()
    if mode == 'manual':
        queued(product)
        accepted(world,adapter)
        task_key = manual.KEY
    else:
        history(product)
        queued(product)
        authorise(adapter)
        task_key = publisher.KEY
        task = db.session.get(ScheduledTask,task_key)
        task.next_due = drafts.now()-timedelta(seconds=1)
        db.session.commit()
        tasks.enqueue(task_key)
    database = str(db.engine.url)
    mock_file = tmp_path / 'fictional-shop.json'
    repo = Path(__file__).resolve().parents[1]
    local_env = {'PATH':os.defpath,'PYTHONDONTWRITEBYTECODE':'1',
        'PYTHONPATH':os.pathsep.join((str(repo),str(repo/'tests'))),
        'SECRET_KEY':'fictional-worker-restart-signed-approval-key',
        'SHOPIFY_STORE_DOMAIN':'fictional.myshopify.com','SHOPIFY_ADMIN_ACCESS_TOKEN':TOKEN}
    worker = r'''
import json, os, sys
from pathlib import Path
from unittest.mock import patch
from config import Config
from app import create_app, db, scheduled_tasks as tasks
from app import shopify_publication as publication
from test_shopify_publication_sales import AcceptanceAdapter
database, fixture, key, worker, kill_at = sys.argv[1:]
fixture = Path(fixture)
Config.SQLALCHEMY_DATABASE_URI = database
def checkpoint(adapter):
    state = dict(adapter.__dict__)
    state['memberships'] = list(state.get('memberships',[]))
    fixture.write_text(json.dumps(state))
class Adapter(AcceptanceAdapter):
    def create_draft(self,*args):
        result = super().create_draft(*args)
        if worker=='first' and kill_at=='unacknowledged_create':
            checkpoint(self)
            os._exit(42)
        return result
adapter = Adapter()
if fixture.exists():
    adapter.__dict__.update(json.loads(fixture.read_text()))
    adapter.memberships = set(adapter.memberships)
compare = publication.comparison_after_write
def stop_at_read(*args):
    if worker=='first' and kill_at=='verified_read':
        checkpoint(adapter)
        os._exit(42)
    return compare(*args)
publication.comparison_after_write = stop_at_read
with patch('requests.Session.request', side_effect=AssertionError('No real network in worker tests')):
    app = create_app(recover=worker!='first')
    with app.app_context():
        claim = tasks.claim(worker)
        assert claim and claim[0]==key
        tasks.run_slice(*claim,worker,adapter=adapter)
        checkpoint(adapter)
'''
    def spawn(owner):
        db.session.remove();db.engine.dispose()
        result = subprocess.run([sys.executable,'-c',worker,database,str(mock_file),task_key,owner,kill_at],
            env=local_env,cwd=tmp_path,capture_output=True,text=True,timeout=45)
        assert result.returncode == (42 if owner=='first' else 0),result.stderr

    spawn('first')
    row = ShopifyProductIdentity.query.one()
    op,gid = row.operation_id,row.shopify_product_id
    expected_proof = json.loads(db.session.get(CatalogueOperation,op).scope).get('manual_review',{}).get('proof')
    task = db.session.get(ScheduledTask,task_key)
    assert task.owner=='first' and task.active_run_id
    task.lease_until = drafts.now()-timedelta(seconds=1)
    db.session.commit()
    spawn('replacement')
    after = json.loads(mock_file.read_text())
    row = ShopifyProductIdentity.query.one()
    op_record = db.session.get(CatalogueOperation,op)
    assert row.product_id==product_id and row.operation_id==op
    assert json.loads(op_record.scope).get('manual_review',{}).get('proof')==expected_proof
    assert after['calls'].count('product_create')==1 and after['calls'].count('binary_upload')==2
    if kill_at=='verified_read':
        assert op_record.status=='succeeded' and row.shopify_product_id==gid and row.state=='verified'
        assert after['calls'].count('status_write')==after['calls'].count('publication_write')==1
        assert after['remote']['status']=='ACTIVE'
    else:
        assert op_record.recovery_state=='review_required'
        assert not any(c in after['calls'] for c in ('variant_write','status_write','publication_write'))


def test_create_and_collection_acknowledgement_survive_three_pauses(world,registry):
    from test_shopify_taxonomy_lifecycle import LifecycleAdapter
    from app.models import ShopifyCollectionIdentity

    change(world,categories=['Cards'])
    class Adapter(LifecycleAdapter):
        pause_create = True
        pause_variant = True
        pause_collection_read = True
        def create_draft(self,*args):
            if self.pause_create:
                self.pause_create = False
                raise RequestNotSent('Create did not leave the worker.',code='request_budget')
            return super().create_draft(*args)
        def update_simple_variant(self,*args):
            if self.pause_variant:
                self.pause_variant = False
                raise RequestNotSent('Variant update did not leave the worker.',code='time_budget')
            return super().update_simple_variant(*args)
        def collection_state(self,*args):
            if self.pause_collection_read and 'collection_create' in self.calls:
                self.pause_collection_read = False
                raise RequestNotSent('Acknowledged collection read deferred.',code='capacity')
            return super().collection_state(*args)

    adapter = Adapter()
    op,_ = accepted(world,adapter)
    for expected in ('request_budget','time_budget','capacity'):
        step(adapter)
        receipt = db.session.get(CatalogueOperation,op)
        assert receipt.status=='pending' and receipt.recovery_state=='none'
        assert json.loads(receipt.scope)['worker_deferral']['deferral']==expected
        assert not drafts.evidence(ShopifyProductIdentity.query.one()).get('dispatch')
    step(adapter,owner='replacement-after-collection-ack')
    assert db.session.get(CatalogueOperation,op).status=='succeeded'
    assert adapter.calls.count('product_create')==adapter.calls.count('variant_write')==adapter.calls.count('collection_create')==1
    assert adapter.calls.count('binary_upload')==2
    assert ShopifyCollectionIdentity.query.filter_by(operation_id=op,state='verified').count()==1


@pytest.mark.parametrize('mode',['manual','hourly'])
def test_market_then_activation_then_channel_resume_without_replay(world,mode):
    from app import automatic_publishing as publisher, scheduled_tasks as tasks
    from app.models import ShopifyPublishJob
    from test_automatic_publishing import authorise, queued
    from test_publishing_checkpoint_integration import history
    from test_shopify_markets import MarketAdapter, market, publication

    product = world[0]
    change(world,live=True,shopify_channels={'online-store':True},shopify_market_ids=[market(2)])
    class Adapter(MarketAdapter):
        stop_status = stop_channel = True
        def update_simple_product(self,config,payload):
            if self.stop_status and set(payload)=={'id','status'}:
                self.stop_status = False
                raise RequestNotSent('Activation deferred.',code='time_budget')
            return super().update_simple_product(config,payload)
        def set_publication(self,config,product_id,pid,publish):
            if self.stop_channel and pid=='gid://shopify/Publication/1':
                self.stop_channel = False
                raise RequestNotSent('Channel write deferred.',code='request_budget')
            return super().set_publication(config,product_id,pid,publish)

    adapter = Adapter()
    adapter.memberships.clear()
    if mode=='manual':
        queued(product)
        op,_ = accepted(world,adapter)
        advance = lambda owner:step(adapter,owner=owner)
    else:
        history(product)
        queued(product)
        authorise(adapter)
        task = db.session.get(ScheduledTask,publisher.KEY)
        task.next_due = drafts.now()-timedelta(seconds=1)
        db.session.commit()
        run = tasks.enqueue(publisher.KEY)
        def advance(owner):
            task = db.session.get(ScheduledTask,publisher.KEY)
            task.resume_at = drafts.now()-timedelta(seconds=1)
            db.session.get(ShopifyPublishJob,product.id).next_attempt = task.resume_at
            db.session.commit()
            assert tasks.claim(owner)==(publisher.KEY,run)
            tasks.run_slice(publisher.KEY,run,owner,adapter=adapter)
    for i,ref in enumerate(('time_budget','request_budget')):
        advance(f'availability-worker-{i}')
        row = ShopifyProductIdentity.query.one()
        op = db.session.get(CatalogueOperation,row.operation_id)
        assert op.status=='pending' and op.recovery_state=='none',op.error
        assert json.loads(op.scope)['worker_deferral']['deferral']==ref
        assert len(adapter.market_writes)==1 and adapter.market_writes[0][1]==publication(2)
        assert adapter.calls.count('status_write')==i
        assert 'publication_write' not in adapter.calls
    advance('availability-replacement')
    assert db.session.get(CatalogueOperation,row.operation_id).status=='succeeded'
    assert len(adapter.market_writes)==adapter.calls.count('status_write')==adapter.calls.count('publication_write')==1
    assert adapter.calls.count('product_create')==1 and adapter.calls.count('binary_upload')==2
    assert adapter.market_context(drafts.configuration(),row.shopify_product_id,['GB','FR'])['c1'] is True
    assert products_badge(product)['label']=='Up to Date'


def test_legacy_verified_simple_draft_requires_fresh_availability_only_review(world,monkeypatch):
    from app import shopify_continuation as checkpoint, shopify_publication as publication

    change(world,live=True,shopify_channels={'online-store':True})
    adapter = AcceptanceAdapter()
    old,_ = accepted(world,adapter)
    with monkeypatch.context() as legacy:
        legacy.setattr(checkpoint,'begin',lambda *args,**kw:False)
        def stop(*args):
            raise BackgroundYield('Legacy unclassified pre-publication pause.')
        legacy.setattr(publication,'comparison_after_write',stop)
        step(adapter)
    row = ShopifyProductIdentity.query.one()
    assert db.session.get(CatalogueOperation,old).recovery_state=='review_required'
    gid = row.shopify_product_id
    prior = list(adapter.calls)
    drafts.reconcile(row,adapter=adapter)
    assert row.state=='creation_review_required' and row.verification_state=='base_verified'
    assert not any('write' in c or 'create' in c or 'upload' in c for c in adapter.calls[len(prior):])
    historical = db.session.get(CatalogueOperation,old).scope
    path = world[2]; original = path.read_text()
    change(world,description='A new change cannot ride the recovery approval')
    _,token = drafts.preview(world[0],adapter)
    assert token is None
    path.write_text(original)
    fresh,_ = accepted(world,adapter)
    assert fresh!=old
    step(adapter)
    assert row.shopify_product_id==gid and row.state=='verified'
    assert db.session.get(CatalogueOperation,old).scope==historical
    assert db.session.get(CatalogueOperation,fresh).status=='succeeded'
    assert adapter.calls.count('product_create')==adapter.calls.count('status_write')==adapter.calls.count('publication_write')==1
    assert adapter.calls.count('binary_upload')==2 and 'product_update' not in adapter.calls
