"""Incident evidence over fictional shops/files; no live Shopify requests."""
import copy
import json
import sqlite3
from datetime import timedelta
from pathlib import Path
import pytest
from flask import current_app
from sqlalchemy.exc import OperationalError
from app import db, shopify_drafts as drafts, shopify_status as status
from app.models import CatalogueOperation, ShopifyProductIdentity, ShopifyProductComparison
from app.shopify_connection import ShopifyAdapter, ShopifyError, ShopifyReadThrottled, VARIABLE_READ, VARIABLE_NATIVE_READ, ATTRIBUTE_VALUES, PRICE_QUERY, ShopifyConfiguration
from app.shopify_products import states_for
from app.shopify_progress import receipt,accept,finalise_unfinished
from app.utils.operation_control import recover_interrupted_operations,finish_catalogue_operation
from test_shopify_drafts import world
from test_shopify_simple_updates import created,change,UpdateAdapter
from test_shopify_variables import variable_world,VariableAdapter,rescan,create
from test_shopify_taxonomy_products import registry
from test_shopify_taxonomy_lifecycle import informational
from test_shopify_live_fixes import AccessAdapter
from test_shopify_foundation import Response,Session,TOKEN,isolated_transport
from test_phase3_woo_publish_preview import preview_app,_client


def display(product):
    with current_app.test_request_context(): return states_for([product])[product.id]


def test_confirm_receipt_is_committed_before_api_validation(world,monkeypatch):
    product,row,adapter=created(world); change(world,price='4.50')
    _,token=drafts.preview(product,adapter)
    original=adapter.test_connection
    def connected(config):
        separate=sqlite3.connect(db.engine.url.database)
        scope,opid=separate.execute("SELECT scope,id FROM catalogue_operation WHERE status='running' AND operation_type='shopify_draft_publish'").fetchone()
        separate.close()
        assert json.loads(scope)['review_ticket']==drafts.digest(token)
        assert db.session.connection().connection.driver_connection.in_transaction is False
        result=receipt(product.id,token)
        assert result['state']=='processing' and result['operation_id']==opid
        assert result['worker_health']['state']=='healthy'
        return original(config)
    adapter.test_connection=connected
    monkeypatch.setattr(drafts,'ShopifyAdapter',lambda:adapter)
    client=_client(current_app._get_current_object())
    response=client.post(f'/products/{product.id}/shopify/review',data={'action':'confirm','review':token,'acknowledge':'yes'},headers={'Accept':'application/json'})
    assert response.status_code==202,response.data
    from test_manual_publishing_worker import step
    with current_app.test_request_context():step(adapter)
    response=client.post(f'/products/{product.id}/shopify/progress',data={'review':token})
    assert response.json['state']=='completed'
    assert CatalogueOperation.query.filter_by(operation_type='shopify_draft_publish').count()==2


def test_before_write_failure_has_durable_precise_receipt(world,monkeypatch):
    product,row,adapter=created(world); change(world,price='4.50'); _,token=drafts.preview(product,adapter)
    change(world,price='5.50')
    monkeypatch.setattr(drafts,'ShopifyAdapter',lambda:adapter)
    client=_client(current_app._get_current_object())
    response=client.post(f'/products/{product.id}/shopify/review',data={'action':'confirm','review':token,'acknowledge':'yes'},headers={'Accept':'application/json'})
    assert response.status_code==202
    from test_manual_publishing_worker import step
    step(adapter)
    result=client.post(f'/products/{product.id}/shopify/progress',data={'review':token}).json
    assert result['state']=='failed' and 'changed' in result['message'].lower()
    assert 'product_update' not in adapter.calls


def test_receipt_lookup_not_limited_to_latest_100_operations(world):
    product,row,adapter=created(world)
    token=drafts.serializer().dumps({'product_id':product.id})
    operation=db.session.get(CatalogueOperation,row.operation_id)
    operation.scope=json.dumps({'review_ticket':drafts.digest(token)})
    for i in range(110):
        db.session.add(CatalogueOperation(id=f'later-{i}',operation_type='shopify_draft_publish',status='failed',scope='{}',started_at=drafts.now()+timedelta(seconds=i+1)))
    db.session.commit()
    with current_app.test_request_context(): result=receipt(product.id,token)
    assert result['state']=='completed' and result['operation_id']==row.operation_id


def test_restart_receipt_interrupted_not_indefinite_processing(world):
    token=drafts.serializer().dumps({'product_id':world[0].id})
    with current_app.test_request_context():
        lease=accept(world[0].id,token,True)
        assert receipt(world[0].id,token)['state']=='running'
        recover_interrupted_operations()
        assert receipt(world[0].id,token)['state']=='recovery_required'
        finalise_unfinished(lease.id)
    # Existing process-local fixture lock is only cleared by its test teardown.
    assert ShopifyProductIdentity.query.count()==0


def test_receipt_database_busy_is_not_a_shopify_failure(world,monkeypatch):
    import app.shopify_progress as service
    def busy(*args): raise OperationalError('redacted',{},sqlite3.OperationalError('database is locked'))
    monkeypatch.setattr(service,'receipt',busy)
    response=_client(current_app._get_current_object()).post(f'/products/{world[0].id}/shopify/progress',data={'review':'ignored'})
    assert response.status_code==503 and 'temporarily unavailable' in response.json['error']
    assert 'database is locked' not in response.json['error']


def test_mutation_success_final_receipt_failure_requires_recovery(world,monkeypatch):
    product=world[0]; adapter=UpdateAdapter(); _,token=drafts.preview(product,adapter)
    monkeypatch.setattr(drafts,'ShopifyAdapter',lambda:adapter)
    original=drafts.finish_catalogue_operation
    calls=[]
    def lost(*args,**kwargs):
        calls.append(1)
        if len(calls)==1: raise OperationalError('redacted',{},sqlite3.OperationalError('receipt commit failed'))
        return original(*args,**kwargs)
    monkeypatch.setattr(drafts,'finish_catalogue_operation',lost)
    client=_client(current_app._get_current_object())
    result=client.post(f'/products/{product.id}/shopify/review',data={'action':'confirm','review':token,'acknowledge':'yes'},headers={'Accept':'application/json'})
    assert result.status_code==202
    from test_manual_publishing_worker import step
    step(adapter)
    result=client.post(f'/products/{product.id}/shopify/progress',data={'review':token})
    assert result.json['state']=='recovery_required'
    assert ShopifyProductIdentity.query.one().shopify_product_id and adapter.calls.count('product_create')==1
    repeated=client.post(f'/products/{product.id}/shopify/review',data={'action':'confirm','review':token,'acknowledge':'yes'},headers={'Accept':'application/json'})
    assert repeated.json['state']=='recovery_required' and adapter.calls.count('product_create')==1


class Pages(ShopifyAdapter):
    def __init__(self,pages): self.pages=pages; self.calls=[]
    def _transport(self,config,query,variables,**kwargs):
        self.calls.append((query,copy.deepcopy(variables),kwargs))
        value=self.pages[len(self.calls)-1]
        if isinstance(value,Exception): raise value
        return {'product':copy.deepcopy(value)}


def page(nodes,cursor=None,more=False,stamp='same'):
    return {'id':'gid://shopify/Product/100','status':'DRAFT','updatedAt':stamp,'metafield':{'value':'nonce'},
        'variants':{'nodes':[{'id':f'gid://shopify/ProductVariant/{n}'} for n in nodes],'pageInfo':{'hasNextPage':more,'endCursor':cursor}}}


@pytest.mark.parametrize('native',[False,True])
def test_complete_pages_return_all_nine_and_pin_api(native):
    adapter=Pages([page(range(1,7),'next',True),page(range(7,10))])
    result=adapter.read_variable(ShopifyConfiguration('fictional.myshopify.com',TOKEN),'gid://shopify/Product/100','tlc_dashboard',native=native)
    assert len(result['variants']['nodes'])==9 and result['variants']['pageInfo']['hasNextPage'] is False
    assert adapter.calls[1][1]['after']=='next'
    assert all(q==(VARIABLE_NATIVE_READ if native else VARIABLE_READ) for q,_,_ in adapter.calls)
    assert all(k==({'api_version':'2027-01'} if native else {}) for _,_,k in adapter.calls)


@pytest.mark.parametrize('kind',['cursor','duplicate','revision','timeout','absent','budget','overflow','connection','invalid_id'])
def test_incomplete_pages_never_return_partial_equality(kind):
    pages=[page(range(1,7),'next',True),page(range(7,10))]
    if kind=='cursor': pages[0]['variants']['pageInfo']['endCursor']=None
    if kind=='duplicate': pages[1]=page([1,7,8])
    if kind=='revision': pages[1]['updatedAt']='changed'
    if kind=='timeout': pages[1]=ShopifyError('Read unavailable')
    if kind=='absent': pages[1]=None
    if kind=='budget': pages=[page(range(i*2+1,i*2+3),str(i),True) for i in range(4)]
    if kind=='overflow': pages=[page(range(1,34))]
    if kind=='connection': pages[0]['variants']=None
    if kind=='invalid_id': pages[0]['variants']['nodes'][0]['id']={}
    adapter=Pages(pages)
    with pytest.raises(ShopifyError): adapter.read_variable(ShopifyConfiguration('fictional.myshopify.com',TOKEN),'gid://shopify/Product/100','tlc_dashboard')
    assert len(adapter.calls)<=4


def test_late_complete_page_cannot_bypass_read_deadline(monkeypatch):
    import app.shopify_connection as connection
    clock=iter([0,1,61]); monkeypatch.setattr(connection,'monotonic',lambda:next(clock))
    adapter=Pages([page([1])])
    with pytest.raises(ShopifyError,match='deadline'): adapter.read_variable(ShopifyConfiguration('fictional.myshopify.com',TOKEN),'gid://shopify/Product/100','tlc_dashboard')
    assert len(adapter.calls)==1


@pytest.mark.parametrize('explicit_null',[False,True])
def test_variable_absence_requires_explicit_successful_null(explicit_null,monkeypatch):
    adapter=ShopifyAdapter(); monkeypatch.setattr(adapter,'_transport',lambda *a,**kw:{'product':None} if explicit_null else {})
    args=(ShopifyConfiguration('fictional.myshopify.com',TOKEN),'gid://shopify/Product/100','tlc_dashboard')
    if explicit_null: assert adapter.read_variable(*args) is None
    else:
        with pytest.raises(ShopifyError,match='absence is not established'): adapter.read_variable(*args)


@pytest.mark.parametrize('field',['price','weight','dimension','policy','missing','conflict','extra'])
def test_variable_remote_differences_and_identity_classification(variable_world,field):
    product=variable_world[0]; row,adapter=create(variable_world)
    identities=copy.deepcopy(drafts.evidence(row)['variant_maps'])
    actual=adapter.remote['variants']['nodes'][0]
    if field=='price': actual['price']='123.00'
    if field=='weight': actual['inventoryItem']['measurement']['weight']['value']+=5
    if field=='dimension':
        value=json.loads(actual['length']['value']); value['value']+=1; actual['length']['value']=json.dumps(value)
    if field=='policy': actual['inventoryPolicy']='DENY'
    if field=='missing': adapter.remote['variants']['nodes'].pop(0)
    if field=='conflict': actual['inventoryItem']['id']='gid://shopify/InventoryItem/99999'
    if field=='extra':
        extra=adapter._variant(9999,[{'name':o['name'],'value':'Remote only'} for o in actual['selectedOptions']]); extra['sku']=extra['inventoryItem']['sku']='REMOTE-ONLY'; adapter.remote['variants']['nodes'].append(extra)
    writes=[c for c in adapter.calls if 'create' in c or 'update' in c]
    result=status.refresh([product],force=True,adapter=adapter)
    state=display(product)
    assert state['state']==('attention' if field=='missing' else 'recovery_required' if field=='conflict' else 'no_change' if field=='extra' else 'update'),state
    assert identities==drafts.evidence(row)['variant_maps']
    assert writes==[c for c in adapter.calls if 'create' in c or 'update' in c]


def test_status_endpoint_includes_both_variable_layouts(variable_world,monkeypatch):
    product=variable_world[0]; row,adapter=create(variable_world)
    adapter.calls.clear()  # Count refresh work, not the independent Create readbacks.
    seen=[]; original=status.refresh
    def checked(products,**kwargs): seen.extend(p.id for p in products); return original(products,adapter=adapter,**kwargs)
    monkeypatch.setattr(status,'refresh',checked)
    client=_client(current_app._get_current_object())
    response=client.post('/api/shopify/status',json={'ids':[product.id]})
    assert response.status_code==200 and seen==[product.id]
    assert response.json['states'][str(product.id)]['state']=='no_change'
    assert adapter.calls.count('variable_read')<=2
    reads=adapter.calls.count('variable_read')
    again=client.post('/api/shopify/status',json={'ids':[product.id]})
    assert again.status_code==200 and adapter.calls.count('variable_read')==reads


class PaginatedVariableAdapter(VariableAdapter):
    """The real bounded reader over fictional, independently paged remote data."""
    def __init__(self):
        super().__init__(); self.page_calls=[]
    _complete_gallery=ShopifyAdapter._complete_gallery

    def read_variable(self,config,pid,namespace,*,native=False):
        return ShopifyAdapter.read_variable(self,config,pid,namespace,native=native)

    def _transport(self,config,query,variables,**kwargs):
        assert config.api_version=='2026-10'
        assert query in {VARIABLE_READ,VARIABLE_NATIVE_READ}
        native=query==VARIABLE_NATIVE_READ
        assert kwargs==({'api_version':'2027-01'} if native else {})
        remote=VariableAdapter.read_variable(self,config,variables['id'],variables.get('namespace'),native=native)
        start=int(variables.get('after') or 0); end=start+3
        nodes=remote['variants']['nodes']; more=end<len(nodes)
        remote['variants']={'nodes':nodes[start:end],'pageInfo':{'hasNextPage':more,'endCursor':str(end) if more else None}}
        self.page_calls.append((native,start,len(remote['variants']['nodes'])))
        return {'product':remote}


def test_nine_variants_complete_readback_across_both_layouts(variable_world,monkeypatch):
    from PIL import Image
    from app import shopify_variables as variable
    monkeypatch.setenv('SHOPIFY_NATIVE_DIMENSIONS_ENABLED','true')
    monkeypatch.setenv('SHOPIFY_PACKED_DIMENSIONS_USE_PRODUCT_DIMENSIONS','true')
    product,shared,collection,_,_=variable_world
    old_skus={v.sku for v in product.variations}
    authored=json.loads(shared.read_text())
    attrs=copy.deepcopy(authored['attributes'])
    first,second=list(attrs)
    attrs[first].append('Third'); attrs[second].append('Third')
    if collection.name=='Single Variable':
        for a in attrs[first]:
            for b in attrs[second]:
                folder=collection/a/b
                if not folder.exists():
                    folder.mkdir(parents=True)
                    Image.new('RGB',(5,5),'green').save(folder/'original.png')
    modifiers={**authored.get('variation_modifiers',{}),f'{second}=Third':{'price':'23.00','weight':95,'dimensions':{'length':155,'width':100,'height':3}}}
    rescan(variable_world,attributes=attrs,variation_modifiers=modifiers,
        packaging={'packed_dimensions_cm':{'length':'16','width':'11','height':'.4'},'measured':True})
    assert len(product.variations)==9 and old_skus.issubset({v.sku for v in product.variations})
    adapter=PaginatedVariableAdapter()
    plan,token=drafts.preview(product,adapter)
    assert token and not plan['blockers'],plan['blockers']
    monkeypatch.setattr(drafts,'ShopifyAdapter',lambda:adapter)
    monkeypatch.setattr(variable,'ShopifyAdapter',lambda:adapter)
    # Every remote mutation sees a committed receipt and no open SQLite writer.
    for method in ('stage_images','upload_image','create_variable','update_variants','create_variants','write_packed'):
        original=getattr(adapter,method)
        def durable(*args,_original=original,**kwargs):
            with sqlite3.connect(db.engine.url.database) as separate:
                assert separate.execute("SELECT count(*) FROM catalogue_operation WHERE status='running' AND operation_type='shopify_draft_publish'").fetchone()[0]==1
                assert separate.execute('SELECT operation_id FROM shopify_product_identity').fetchone()[0]
            assert db.session.connection().connection.driver_connection.in_transaction is False
            return _original(*args,**kwargs)
        monkeypatch.setattr(adapter,method,durable)
    client=_client(current_app._get_current_object())
    response=client.post(f'/products/{product.id}/shopify/review',data={'action':'confirm','review':token,'acknowledge':'yes'},headers={'Accept':'application/json'})
    assert response.status_code==202,response.json
    from test_manual_publishing_worker import step
    step(adapter)
    response=client.post(f'/products/{product.id}/shopify/progress',data={'review':token})
    assert response.json['state']=='completed',response.json
    row=ShopifyProductIdentity.query.one(); proof=drafts.evidence(row)
    mappings=copy.deepcopy(proof['variant_maps'])
    assert len(mappings)==9 and len({v['inventory_id'] for v in mappings.values()})==9
    expected=variable.local_plan(product)['variants']
    for wanted in expected:
        mapping=mappings[wanted['identity']]
        actual=next(v for v in adapter.remote['variants']['nodes'] if v['id']==mapping['id'])
        assert actual['sku']==mapping['sku']==wanted['sku']
        assert actual['selectedOptions']==mapping['options']==wanted['options']
        assert actual['price']==wanted['managed']['price']
        assert actual['inventoryItem']['measurement']['weight']==wanted['managed']['inventoryItem']['measurement']['weight']
        assert actual['inventoryItem']['tracked'] is False and actual['inventoryPolicy']=='CONTINUE'
        assert adapter.packed[mapping['inventory_id']]==wanted['native']['desired']
        assert proof['variant_native'][wanted['identity']]['state']=='verified'
        if wanted['media_source']:
            media=next(m for m in proof['media'] if m['source']==wanted['media_source'])
            assert actual['media']['nodes'][0]['id']==media['id']
    assert any(v['managed']['price']=='23.00' for v in expected)
    writes=[c for c in adapter.calls if 'create' in c or 'update' in c or c=='native_write']
    plan,token=drafts.preview(product,adapter)
    assert plan['classification']=='no_change' and token is None
    assert any(not native and start==6 for native,start,_ in adapter.page_calls)
    assert any(native and start==6 for native,start,_ in adapter.page_calls)
    assert mappings==drafts.evidence(row)['variant_maps']
    assert writes==[c for c in adapter.calls if 'create' in c or 'update' in c or c=='native_write']
    # Drift on the LAST page must not be hidden by parent/first-page equality.
    last=adapter.remote['variants']['nodes'][-1]
    last['inventoryPolicy']='DENY'; last['media']={'nodes':[],'pageInfo':{'hasNextPage':False}}
    adapter.packed[last['inventoryItem']['id']]['height']+=1
    plan,token=drafts.preview(product,adapter)
    assert plan['classification']=='update' and token
    assert any(f['changed'] and f['field'].endswith('inventoryPolicy') for f in plan['fields'])
    assert any(f['changed'] and 'media' in f['field'] for f in plan['fields'])
    assert any(f['changed'] and f['field'].startswith('native.') for f in plan['fields'])
    assert mappings==drafts.evidence(row)['variant_maps']


class SequenceSession(Session):
    def __init__(self,responses): super().__init__(); self.responses=responses
    def post(self,url,**kwargs): self.calls.append((url,kwargs)); return self.responses[min(len(self.calls)-1,len(self.responses)-1)]


@pytest.mark.parametrize('kind',['http','graphql'])
def test_read_throttling_is_bounded_and_cost_is_safe(kind,monkeypatch):
    import app.shopify_connection as connection
    monkeypatch.setattr(connection,'sleep',lambda delay:None)
    error=Response({'errors':[{'message':TOKEN,'extensions':{'code':'THROTTLED'}}], 'extensions':{'cost':{'requestedQueryCost':10,'actualQueryCost':0,'throttleStatus':{'currentlyAvailable':0,'restoreRate':100}}}})
    if kind=='http': error=Response({},429); error.headers={'Retry-After':'0.25','X-Shopify-API-Version':'2026-10'}
    session=SequenceSession([error,Response({'data':{'product':{'id':'example'}}})]); adapter=ShopifyAdapter()
    value=adapter._transport(ShopifyConfiguration('fictional.myshopify.com',TOKEN),ATTRIBUTE_VALUES,{'id':'gid://shopify/Product/100'},session=session)
    assert value['product']['id']=='example' and len(session.calls)==2
    assert adapter.request_trace[0]['throttled'] and TOKEN not in json.dumps(adapter.request_trace)
    forever=SequenceSession([error])
    with pytest.raises(ShopifyReadThrottled): adapter._transport(ShopifyConfiguration('fictional.myshopify.com',TOKEN),ATTRIBUTE_VALUES,{},session=forever)
    assert len(forever.calls)==3


def test_mutation_throttling_never_retries(monkeypatch):
    session=SequenceSession([Response({'errors':[{'extensions':{'code':'THROTTLED'}}]})])
    with pytest.raises(ShopifyError): ShopifyAdapter()._transport(ShopifyConfiguration('fictional.myshopify.com',TOKEN),PRICE_QUERY,{},session=session)
    assert len(session.calls)==1


def test_term_definition_conflict_and_draft_access_cannot_verify(world,registry):
    from app import shopify_attributes as attributes
    informational(registry); change(world,attributes={'Occasion':['Birthday']},variation_attributes=[])
    adapter=AccessAdapter(); _,token=drafts.preview(world[0],adapter); row=drafts.confirm(token,True,adapter)
    original=adapter.attribute_resource
    def incorrect(config,kind,spec):
        result=original(config,kind,spec)
        if kind=='term': result['definition']['id']='gid://shopify/MetaobjectDefinition/99999'
        return result
    adapter.attribute_resource=incorrect
    with pytest.raises(ShopifyError,match='different metaobject definition'): attributes.verify(drafts.evidence(row)['plan'],drafts.configuration(),adapter,row.shopify_product_id)
    def draft_term(config,kind,spec):
        result=original(config,kind,spec)
        if kind=='term': result['capabilities']['publishable']={'status':'DRAFT'}
        return result
    adapter.attribute_resource=draft_term
    with pytest.raises(ShopifyError,match='not storefront-readable'): attributes.verify(drafts.evidence(row)['plan'],drafts.configuration(),adapter,row.shopify_product_id)


@pytest.mark.parametrize('namespace,owner,allowed',[('$app:tlc_attributes','PRODUCT',True),('app--123--tlc_attributes','PRODUCT',True),('merchant_tlc_attributes','PRODUCT',False),('app--123--foreign_tlc_attributes','PRODUCT',False),('$app:tlc_attributes','PRODUCTVARIANT',False)])
def test_informational_field_exact_app_namespace_and_product_ownership(namespace,owner,allowed):
    from app import shopify_attributes as attributes
    spec={'namespace':'$app:tlc_attributes','key':'occasion','validations':[{'name':'metaobject_definition_id','value':'gid://shopify/MetaobjectDefinition/100'}],'access':{'storefront':'PUBLIC_READ'}}
    actual={**spec,'id':'gid://shopify/MetafieldDefinition/200','namespace':namespace,'ownerType':owner,'type':{'name':'list.metaobject_reference'}}
    if allowed: assert attributes.valid('field',actual,spec,'occasion',actual['id'])==actual
    else:
        with pytest.raises(ShopifyError,match='owner/namespace'): attributes.valid('field',actual,spec,'occasion',actual['id'])


@pytest.mark.parametrize('fault',['wrong_product','incomplete','missing_fields','duplicate'])
def test_actual_attribute_assignments_require_complete_exact_product_read(fault,monkeypatch):
    value={'id':'gid://shopify/Metafield/123','key':'occasion','type':'list.metaobject_reference','value':'[]','compareDigest':'fictional-digest'}
    product={'id':'gid://shopify/Product/100','metafields':{'nodes':[value],'pageInfo':{'hasNextPage':False}}}
    if fault=='wrong_product': product['id']='gid://shopify/Product/999'
    if fault=='incomplete': product['metafields']['pageInfo']={}
    if fault=='missing_fields': value.pop('compareDigest')
    if fault=='duplicate': product['metafields']['nodes'].append(copy.deepcopy(value))
    adapter=ShopifyAdapter(); monkeypatch.setattr(adapter,'_read',lambda *a,**kw:{'product':product})
    with pytest.raises(ShopifyError): adapter.attribute_values(ShopifyConfiguration('fictional.myshopify.com',TOKEN), 'gid://shopify/Product/100','$app:tlc_attributes')


def test_catalogue_diagnostic_keeps_database_and_remote_evidence_read_only(variable_world):
    from app.shopify_attribute_diagnostic import catalogue
    product=variable_world[0]; row,adapter=create(variable_world)
    old_evidence=row.media_evidence_json
    old_operations=[(o.id,o.status,o.scope) for o in CatalogueOperation.query.all()]
    adapter.remote['variants']['nodes'][-1]['price']='99.00'
    writes=[c for c in adapter.calls if 'create' in c or 'update' in c]
    output=catalogue(row,adapter)
    assert output['read_only'] and len(output['variants']['nodes'])==4
    assert any('price' in v['field'] for v in output['managed_differences'])
    assert row.media_evidence_json==old_evidence
    assert old_operations==[(o.id,o.status,o.scope) for o in CatalogueOperation.query.all()]
    assert writes==[c for c in adapter.calls if 'create' in c or 'update' in c]
