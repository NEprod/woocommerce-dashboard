"""Real adapter pagination and canonical variable plans; no live Shopify transport."""
import copy
import json
import io
from unittest.mock import patch
import pytest
from PIL import Image
from app import db, shopify_drafts as drafts, shopify_variables as variable
from app.models import ProductImage, ShopifyProductIdentity
from app.shopify_connection import ShopifyAdapter, ShopifyError, VARIABLE_READ, GALLERY_QUERY
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_drafts import world
from test_shopify_variables import variable_world, rescan
from test_shopify_publication_sales import VariableAcceptanceAdapter


def gallery(count=53, stamp='2026-10-28T10:00:00Z'):
    return {'id':'gid://shopify/Product/100','status':'DRAFT','updatedAt':stamp,'metafield':{'value':'owned-attempt'},
        'variants':{'nodes':[],'pageInfo':{'hasNextPage':False}},
        'media':{'nodes':[{'id':f'gid://shopify/MediaImage/{i}','alt':str(i),'status':'READY','mediaContentType':'IMAGE',
            'image':{'url':f'https://cdn.shopify.com/{i}.png'}} for i in range(count)],'pageInfo':{'hasNextPage':False}}}


def test_timestamp_change_restarts_entire_variable_snapshot_not_just_gallery(world):
    adapter=ShopifyAdapter();remote=gallery();calls=[];changed=False
    def transport(config,query,variables,**kwargs):
        nonlocal changed
        calls.append(query);part=copy.deepcopy(remote)
        if query==GALLERY_QUERY and not changed:
            changed=True;remote['updatedAt']='2026-10-28T10:00:01Z';part=copy.deepcopy(remote)
        start=int(variables.get('after') or 0) if query==GALLERY_QUERY else 0
        part['media']={'nodes':part['media']['nodes'][start:start+20],
            'pageInfo':{'hasNextPage':start+20<53,'endCursor':str(start+20)}}
        return {'product':part}
    with patch.object(adapter,'_transport',side_effect=transport):
        result=adapter.read_variable(drafts.configuration(),remote['id'],'tlc_dashboard')
    assert len(result['media']['nodes'])==53 and result['updatedAt']==remote['updatedAt']
    assert calls.count(VARIABLE_READ)==2  # discard variants/options from the old snapshot too
    assert adapter.snapshot_restarts
    with patch.object(adapter,'_transport',side_effect=transport):
        adapter.read_variable(drafts.configuration(),remote['id'],'tlc_dashboard')
    assert adapter.snapshot_restarts==[]  # no stale diagnostics on another stable inspection


def large_variable(w):
    product,shared,collection,*_=w
    authored=json.loads(shared.read_text());attrs=copy.deepcopy(authored['attributes'])
    first,second=list(attrs);attrs[second]=attrs[second]+[f'Choice {i}' for i in range(5)]
    if collection.name=='Single Variable':
        for a in attrs[first]:
            for b in attrs[second]:
                folder=collection/a/b
                if not folder.exists():
                    folder.mkdir(parents=True);Image.new('RGB',(5,5),'red').save(folder/'original.png')
    rescan(w,attributes=attrs)
    assert len(product.variations)==14
    from app.catalogue_images import _source_context
    _,folder,_=_source_context(product)
    initial=len(variable.local_plan(product)['images'])
    output=io.BytesIO();Image.new('RGB',(1280,1280),'red').save(output,'PNG',compress_level=0)
    for i in range(53-initial):
        name=f'gallery-{i:03}.png';(folder/name).write_bytes(output.getvalue())
        product.images.append(ProductImage(url=name,position=100+i,alt_text=f'Gallery {i}'))
    db.session.commit()
    plan=variable.local_plan(product)
    assert len(plan['images'])==53 and sum(i['size'] for i in plan['images'])>30*1024*1024
    return product


class RealPagedVariable(VariableAcceptanceAdapter):
    """Actual 16-variant/20-media adapter pagination over a mutable mock store."""
    _complete_gallery=ShopifyAdapter._complete_gallery
    def read_variable(self,config,pid,namespace,*,native=False):
        return ShopifyAdapter.read_variable(self,config,pid,namespace,native=native)
    def _read(self,config,query,variables=None,**kwargs):
        assert query==GALLERY_QUERY
        return self._transport(config,query,variables,**kwargs)
    def _transport(self,config,query,variables,**kwargs):
        assert query in {VARIABLE_READ,GALLERY_QUERY}
        if query==GALLERY_QUERY and getattr(self,'churn',False):
            self.churn=False;self.remote['updatedAt']='2026-10-28T11:00:01Z'
        remote=VariableAcceptanceAdapter.read_variable(self,config,variables['id'],variables.get('namespace'))
        if query==VARIABLE_READ:
            start=int(variables.get('after') or 0);nodes=remote['variants']['nodes']
            remote['variants']={'nodes':nodes[start:start+16],'pageInfo':{'hasNextPage':start+16<len(nodes),'endCursor':str(start+16)}}
            start=0
        else:start=int(variables['after'])
        nodes=remote['media']['nodes'];remote['media']={'nodes':nodes[start:start+20],
            'pageInfo':{'hasNextPage':start+20<len(nodes),'endCursor':str(start+20)}}
        return {'product':remote}
    def create_variable(self,*args):
        result=super().create_variable(*args);self.churn=True
        return result


def finish_uploads(product,adapter):
    for _ in range(15):
        _,token=drafts.preview(product,adapter)
        assert token
        row=drafts.confirm(token,True,adapter)
        if not drafts.upload_pending(row):return row
    raise AssertionError('Gallery did not complete its bounded slices')


def assert_complete(product,row,adapter):
    proof=drafts.evidence(row)
    assert row.state=='verified',proof.get('error')
    assert len(adapter.remote['variants']['nodes'])==len(proof['variant_maps'])==14
    assert len(proof['media'])==len(adapter.remote['media']['nodes'])==53
    for wanted in proof['plan']['variants']:
        mapping=proof['variant_maps'][wanted['identity']]
        actual=next(v for v in adapter.remote['variants']['nodes'] if v['id']==mapping['id'])
        assert mapping['sku']==actual['sku']==wanted['sku']
        assert actual['selectedOptions']==wanted['options'] and actual['price']==wanted['managed']['price']
    assert adapter.calls.count('binary_upload')==53 and adapter.calls.count('product_create')==1
    assert all(m['alt']==i['alt'] for m,i in zip(adapter.remote['media']['nodes'],proof['plan']['images']))


def test_fourteen_variants_fifty_three_images_real_pagination_and_own_revision_changes(variable_world):
    product=large_variable(variable_world);adapter=RealPagedVariable()
    row=finish_uploads(product,adapter)
    assert_complete(product,row,adapter)
    assert drafts.evidence(row)['gallery_snapshot_restarts'][-1]['updatedAt']['after']=='2026-10-28T11:00:01Z'


class InterruptedBeforeVariants(RealPagedVariable):
    def read_variable(self,*args,**kwargs):
        if getattr(self,'interrupt',True) and self.remote:
            raise ShopifyError('Product revision/correlation changed during gallery pagination; compare again without writing.')
        return super().read_variable(*args,**kwargs)


def test_partial_draft_completion_requires_new_signed_approval_without_reupload_or_recreate(variable_world,monkeypatch):
    from flask import current_app
    from test_manual_publishing_worker import step
    product=large_variable(variable_world);adapter=InterruptedBeforeVariants()
    row=finish_uploads(product,adapter);proof=drafts.evidence(row)
    assert row.state=='uncertain' and len(adapter.remote['variants']['nodes'])==1
    assert len(proof['plan']['variants'])==14 and len(proof['upload_inputs'])==53
    assert not proof['variant_creates_attempted'] and proof['completed_steps'][-1]=='product_create'
    from app.shopify_notifications import product_result
    from app.models import CatalogueOperation
    assert product_result(db.session.get(CatalogueOperation,row.operation_id))['outcome']=='recovery_required'
    retained=row.shopify_product_id;original_operation=row.operation_id;before=copy.deepcopy(proof)
    adapter.interrupt=False
    plan,token=variable.preview(product,adapter,resume_creation=True)
    assert token and plan['remote_id']==retained and plan['classification']=='update'
    assert before==drafts.evidence(row)  # reads do not adopt mappings or clear recovery
    monkeypatch.setattr(variable,'ShopifyAdapter',lambda:adapter)
    client=_client(current_app)
    response=client.post(f'/products/{product.id}/shopify/review',data={'action':'confirm','review':token,'acknowledge':'yes'},headers={'Accept':'application/json'})
    assert response.status_code==202,response.json
    del client  # no browser connection remains
    step(adapter)
    assert row.shopify_product_id==retained
    assert_complete(product,row,adapter)
    assert drafts.evidence(row)['creation_resume_from']['operation_id']==original_operation
    assert product_result(db.session.get(CatalogueOperation,row.operation_id))['outcome']=='verified_success'


@pytest.mark.parametrize('field',['id','status','metafield','updatedAt'])
def test_real_identity_conflict_or_persistent_churn_is_not_suppressed(world,field):
    from app.shopify_connection import ShopifySnapshotChanged
    adapter=ShopifyAdapter();remote=gallery();calls=[]
    def transport(config,query,variables,**kwargs):
        calls.append(query);part=copy.deepcopy(remote)
        start=int(variables.get('after') or 0) if query==GALLERY_QUERY else 0
        if query==GALLERY_QUERY:
            part[field]={'value':'other-secret-attempt'} if field=='metafield' else '2026-10-28T10:00:01Z' if field=='updatedAt' else 'ACTIVE' if field=='status' else 'gid://shopify/Product/999'
        part['media']={'nodes':part['media']['nodes'][start:start+20],'pageInfo':{'hasNextPage':start+20<53,'endCursor':str(start+20)}}
        return {'product':part}
    with patch.object(adapter,'_transport',side_effect=transport),pytest.raises(ShopifySnapshotChanged) as error:
        adapter.read_variable(drafts.configuration(),remote['id'],'tlc_dashboard')
    assert error.value.changed==[field]
    assert calls.count(VARIABLE_READ)==(3 if field=='updatedAt' else 1)
    assert 'other-secret-attempt' not in str(error.value)
    assert field in error.value.diagnostic


@pytest.mark.parametrize('defect',['source','nonce','status','variant','attempted','media','ack','order','receipt_digest'])
def test_uncertain_partial_creation_is_never_replayed(variable_world,defect):
    product=variable_world[0];adapter=InterruptedBeforeVariants();row=finish_uploads(product,adapter)
    adapter.interrupt=False;data=drafts.evidence(row)
    if defect=='source':rescan(variable_world,description='A genuine later local edit')
    if defect=='nonce':adapter.remote['metafield']['value']='another-attempt'
    if defect=='status':adapter.remote['status']='ACTIVE'
    if defect=='variant':adapter.remote['variants']['nodes'][0]['sku']=adapter.remote['variants']['nodes'][0]['inventoryItem']['sku']='EXTERNAL'
    if defect=='attempted':data['variant_creates_attempted']=['ambiguous-attempt'];drafts.save(row,data)
    if defect=='media':adapter.remote['media']['nodes'].pop()
    if defect=='ack':data['upload_inputs'].pop();drafts.save(row,data)
    if defect=='order':adapter.remote['media']['nodes'].reverse()
    if defect=='receipt_digest':row.reviewed_digest='invalid';db.session.commit()
    count=len(adapter.calls)
    with pytest.raises(ShopifyError):variable.preview(product,adapter,resume_creation=True)
    assert not any(c in {'product_create','binary_upload','variants_create','variants_update','product_update'} for c in adapter.calls[count:])
    assert row.shopify_product_id==adapter.remote['id'] and row.state=='uncertain'


def test_stale_recovery_review_rejected_and_prior_uncertainty_preserved(variable_world):
    product=variable_world[0];adapter=InterruptedBeforeVariants();row=finish_uploads(product,adapter);adapter.interrupt=False
    _,token=variable.preview(product,adapter,resume_creation=True)
    old=copy.deepcopy(drafts.evidence(row));count=len(adapter.calls)
    adapter.remote['updatedAt']='2026-10-29T10:00:00Z'
    with pytest.raises(ShopifyError,match='changed after Review'):drafts.confirm(token,True,adapter)
    assert row.state=='uncertain' and drafts.evidence(row)==old
    assert not any(c in {'product_create','binary_upload','variants_create','variants_update'} for c in adapter.calls[count:])


def test_manual_live_creation_waits_for_complete_media_then_activates_without_new_approval(variable_world,monkeypatch):
    from flask import current_app
    from test_manual_publishing_worker import step
    from app.models import CatalogueOperation
    rescan(variable_world,live=True)
    product=large_variable(variable_world);adapter=RealPagedVariable();adapter.processing=True
    _,token=drafts.preview(product,adapter)
    client=_client(current_app)
    response=client.post(f'/products/{product.id}/shopify/review',data={'action':'confirm','review':token,'acknowledge':'yes'},headers={'Accept':'application/json'})
    assert response.status_code==202,response.json
    for _ in range(15):
        step(adapter);row=ShopifyProductIdentity.query.one()
        if row.state=='processing':break
    assert row.state=='processing' and adapter.remote['status']=='DRAFT'
    assert len(adapter.remote['variants']['nodes'])==14
    assert db.session.get(CatalogueOperation,response.json['operation_id']).status=='pending'
    for m in adapter.remote['media']['nodes']:m.update(status='READY',image={'url':'https://cdn.shopify.com/ready.png'})
    del client
    step(adapter)
    op=db.session.get(CatalogueOperation,response.json['operation_id'])
    assert op.status=='succeeded',(op.error,op.scope,drafts.evidence(row).get('stage'))
    assert_complete(product,row,adapter)
    assert adapter.remote['status']=='ACTIVE'
    assert db.session.get(CatalogueOperation,response.json['operation_id']).status=='succeeded'


def test_automatic_creation_uses_same_real_gallery_and_variant_pipeline(variable_world):
    from test_automatic_publishing import authorise,execute
    rescan(variable_world,live=True)
    product=large_variable(variable_world);adapter=RealPagedVariable();authorise(adapter)
    for _ in range(15):
        state=execute(product,adapter)
        if state in {'verified','recovery_required'}:break
    row=ShopifyProductIdentity.query.one()
    assert state=='verified',drafts.evidence(row).get('error')
    assert_complete(product,row,adapter)
    assert adapter.remote['status']=='ACTIVE'


def test_read_only_documented_probe_preserves_database_and_approval(variable_world,capsys):
    from pathlib import Path
    product=variable_world[0];adapter=InterruptedBeforeVariants();row=finish_uploads(product,adapter)
    database=Path(db.engine.url.database);before=database.read_bytes()
    text=(Path(__file__).parents[1]/'docs/VARIABLE_CREATION_RECOVERY.md').read_text()
    probe=text.split("python - <<'PY'\n",1)[1].split('\nPY\n',1)[0]
    probe=probe.replace("Path('/app/instance/site.db')",f'Path({str(database)!r})').replace("SKU='3DCO-0001'",f'SKU={product.sku!r}')
    exec(compile(probe,'read-only-original-variable-probe','exec'),{})
    result=json.loads(capsys.readouterr().out)
    assert result['database_writes']==result['shopify_requests']==0
    assert result['identities'][0]['reviewed_variant_count']==4
    assert result['identities'][0]['variant_creates_attempted']==[]
    assert database.read_bytes()==before and row.state=='uncertain'


def test_independent_prewrite_review_failure_retains_safe_underlying_cause(variable_world):
    from app.models import CatalogueOperation
    product=variable_world[0];adapter=RealPagedVariable();_,token=drafts.preview(product,adapter)
    adapter.country_codes=lambda config:(_ for _ in ()).throw(ShopifyError('Required definition read unavailable'))
    with pytest.raises(ShopifyError,match='definition read unavailable'):drafts.confirm(token,True,adapter)
    op=CatalogueOperation.query.filter_by(operation_type='shopify_draft_publish').order_by(CatalogueOperation.started_at.desc()).first()
    assert op.status=='failed' and 'before remote writes' in op.error and 'definition read unavailable' in op.error
    assert ShopifyProductIdentity.query.count()==0 and 'product_create' not in adapter.calls and 'binary_upload' not in adapter.calls


def test_concurrent_reconciliation_is_blocked_during_variable_creation(variable_world):
    class Checking(RealPagedVariable):
        def read_variable(self,*args,**kwargs):
            if self.remote and not getattr(self,'checked',False):
                self.checked=True
                row=ShopifyProductIdentity.query.one()
                with pytest.raises(ShopifyError,match='still active'):drafts.reconcile(row,adapter=self)
            return super().read_variable(*args,**kwargs)
    adapter=Checking();row=finish_uploads(variable_world[0],adapter)
    assert row.state=='verified' and adapter.checked and adapter.calls.count('product_create')==1


def test_local_edit_during_post_create_read_stops_before_variant_writes(variable_world):
    class Editing(RealPagedVariable):
        def read_variable(self,*args,**kwargs):
            result=super().read_variable(*args,**kwargs)
            if self.remote and not getattr(self,'edited',False):
                self.edited=True
                p=variable_world[1];d=json.loads(p.read_text());d['description']='New merchant edit retained';p.write_text(json.dumps(d))
            return result
    adapter=Editing();row=finish_uploads(variable_world[0],adapter)
    assert row.state=='uncertain' and row.shopify_product_id==adapter.remote['id']
    assert len(adapter.remote['variants']['nodes'])==1 and adapter.remote['status']=='DRAFT'
    assert 'variants_update' not in adapter.calls and 'variants_create' not in adapter.calls
    assert json.loads(variable_world[1].read_text())['description']=='New merchant edit retained'
