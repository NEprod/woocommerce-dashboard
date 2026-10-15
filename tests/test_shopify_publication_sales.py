"""Isolated catalogue/SQLite and fixed fake APIs only; never live writes/webhooks."""
import copy
import json
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from alembic import command
from app import db,shopify_drafts as drafts,shopify_sales as sales,shopify_publication as publication,scheduled_tasks as tasks
from app.models import ShopifyProductIdentity,ShopifySaleSchedule,ShopifyProductComparison,CatalogueOperation,ScheduledTask,ScheduledTaskRun
from app.product_defaults import effective
from app.sale_pricing import project,instant
from app.shopify_connection import ShopifyError,ShopifyAdapter,PUBLICATIONS_QUERY,PUBLICATION_STATE,PUBLICATION_PUBLISH,PUBLICATION_UNPUBLISH
from app.shopify_products import source_fingerprint,states_for
from app.utils.json_utils import merge_product_json,apply_variation_modifiers,json_key
from app.database import ensure_database,_alembic_config
from test_shopify_drafts import world
from test_phase3_woo_publish_preview import preview_app,_client
from test_shopify_simple_updates import UpdateAdapter,change
from test_shopify_variables import variable_world,VariableAdapter


class SafetyMixin:
    def test_connection(self,config):
        result=super().test_connection(config)
        result['scopes'] += ['read_publications','write_publications']
        return result

    def publications(self,config):
        self.calls.append('publications_read')
        return [{'id':f'gid://shopify/Publication/{i}','autoPublish':False,
            'channels':{'nodes':[{'id':f'gid://shopify/Channel/{i}','name':name,'handle':key}],'pageInfo':{'hasNextPage':False}}}
            for i,name,key in [(1,'Online Store','online-store'),(2,'Shop','shop'),(3,'Point of Sale','pos')]]

    def publication_state(self,config,product_id):
        self.calls.append('publication_read')
        return {'id':self.remote['id'],'status':self.remote['status'],'metafield':self.remote['metafield'],
            'resourcePublicationsV2':{'nodes':[{'isPublished':True,'publishDate':None,'publication':{'id':i}} for i in getattr(self,'memberships',set())],
                                     'pageInfo':{'hasNextPage':getattr(self,'incomplete_channels',False)}}}

    def durable(self,key):
        row=ShopifyProductIdentity.query.one()
        with db.engine.connect() as connection:
            raw=connection.execute(text('select media_evidence_json from shopify_product_identity where id=:id'),{'id':row.id}).scalar()
            proof=drafts.evidence(SimpleNamespace(media_evidence_json=raw))[key]
            status=connection.execute(text('select status from catalogue_operation where id=:id'),{'id':row.operation_id}).scalar()
            assert status=='running' and proof['state']=='pending'
            if key=='sale_transition':
                assert connection.execute(text('select state from shopify_sale_schedule where id=:id'),{'id':proof['schedule_id']}).scalar() in {'activating','ending'}

    def update_simple_product(self,config,value):
        if set(value)=={'id','status'}:
            self.durable('publication_update');self.calls.append('status_write')
            self.remote['status']=value['status']
            return {'productUpdate':{'product':{'id':value['id']},'userErrors':[]}}
        return super().update_simple_product(config,value)

    def set_publication(self,config,product_id,publication_id,published):
        self.durable('publication_update');self.calls.append('publication_write')
        self.memberships=getattr(self,'memberships',set())
        if published: self.memberships.add(publication_id)
        else: self.memberships.discard(publication_id)
        if getattr(self,'channel_timeout',False): raise ShopifyError('Publication response lost')
        return {'publishablePublish' if published else 'publishableUnpublish':{'userErrors':[]}}

    def update_simple_variant(self,config,product_id,value):
        data=drafts.evidence(ShopifyProductIdentity.query.one())
        if data.get('stage')=='sale_transition':
            self.durable('sale_transition')
            if getattr(self,'sale_timeout',None)=='before': raise ShopifyError('Price request uncertain')
        result=super().update_simple_variant(config,product_id,value)
        if data.get('stage')=='sale_transition' and getattr(self,'sale_timeout',None)=='after': raise ShopifyError('Price response lost')
        return result

    def update_variants(self,config,product_id,values):
        data=drafts.evidence(ShopifyProductIdentity.query.one())
        if data.get('stage')=='sale_transition':
            self.durable('sale_transition');self.calls.append('sale_variants_write');self.inputs.append(copy.deepcopy(values))
            for value in values: self._apply(next(v for v in self.remote['variants']['nodes'] if v['id']==value['id']),value)
            return {'productVariantsBulkUpdate':{'productVariants':[],'userErrors':[]}}
        return super().update_variants(config,product_id,values)


class AcceptanceAdapter(SafetyMixin,UpdateAdapter): pass
class VariableAcceptanceAdapter(SafetyMixin,VariableAdapter): pass


@pytest.fixture
def clock(monkeypatch):
    value=[datetime(2026,10,20,10)]
    monkeypatch.setattr(drafts,'now',lambda:value[0])
    return value


@pytest.fixture
def published(world,clock,monkeypatch):
    notices=[]
    monkeypatch.setattr('app.shopify_notifications.deliver',lambda op,event,**k:notices.append((op,event)))
    product=world[0];adapter=AcceptanceAdapter()
    _,token=drafts.preview(product,adapter);row=drafts.confirm(token,True,adapter)
    assert row.state=='verified'
    notices.clear();adapter.calls.clear();adapter.inputs.clear()
    return product,row,adapter,notices


def approve(world,published,**metadata):
    change(world,sale_price='3.00',sale_start_date='2026-11-01T10:00:00+00:00',sale_end_date='2026-11-02T10:00:00+00:00',**metadata)
    plan,token=sales.review(published[0],published[2])
    return sales.authorise(published[0],token,True,published[2]),plan


@pytest.mark.parametrize('key,value,environment',[
    ('country_of_origin','CN','DEFAULT_COUNTRY_OF_ORIGIN'),('requires_shipping',False,'DEFAULT_REQUIRES_SHIPPING'),
    ('requires_shipping',True,'DEFAULT_REQUIRES_SHIPPING'),('manage_stock',True,'DEFAULT_MANAGE_STOCK'),
    ('manage_stock',False,'DEFAULT_MANAGE_STOCK'),('backorders','no','DEFAULT_BACKORDERS')])
def test_explicit_values_beat_environment_and_shared(key,value,environment,monkeypatch):
    monkeypatch.setenv(environment,'GB' if key=='country_of_origin' else 'yes' if key=='backorders' else str(not value).lower())
    resolved=merge_product_json({key: 'GB' if key=='country_of_origin' else 'yes' if key=='backorders' else not value},{key:value})
    assert effective(resolved)[0][key]==value
    assert effective({})[1][key]=='deployment_default'


@pytest.mark.parametrize('key,value',[('manage_stock',None),('requires_shipping',None),('backorders',None),('country_of_origin',None)])
def test_null_does_not_mean_inherit_or_false(key,value):
    from app.product_info import validate_product_info
    assert not validate_product_info({key:value},'override').valid


@pytest.mark.parametrize('override,status',[({'live':False},'DRAFT'),({'shopify_status':'ARCHIVED'},'ARCHIVED'),({'shopify_status':'ACTIVE'},'ACTIVE')])
def test_legacy_child_intent_has_priority(override,status):
    resolved=merge_product_json({'live':True,'shopify_status':'ACTIVE'},override)
    assert publication.intent(resolved)['status']==status


@pytest.mark.parametrize('at,price,compare,state',[
    (datetime(2026,10,31,10),'4.00',None,'scheduled'),(datetime(2026,11,1,10),'3.00','4.00','active'),
    (datetime(2026,11,2,10),'4.00',None,'expired')])
def test_exact_sale_boundaries(at,price,compare,state):
    result=project({'sale_price':'3.00','sale_start_date':'2026-11-01T10:00:00Z','sale_end_date':'2026-11-02T10:00:00Z'},'4.00',at=at)
    assert result=={'price':price,'compareAtPrice':compare,'state':state}


@pytest.mark.parametrize('value',[True,'nan','Infinity','-1','3.001','4.00','5.00'])
def test_invalid_sale_currency(value):
    with pytest.raises(ValueError): project({'sale_price':value},'4.00')


@pytest.mark.parametrize('date_text',['2026-03-29T01:30','2026-10-25T01:30'])
def test_dst_ambiguous_and_nonexistent_rejected(date_text):
    with pytest.raises(ValueError): instant(date_text)


def test_dst_date_only_days_and_explicit_offsets():
    assert (instant('2026-03-29',end=True)-instant('2026-03-29')).total_seconds()==23*3600
    assert (instant('2026-10-25',end=True)-instant('2026-10-25')).total_seconds()==25*3600
    assert instant('2026-10-25T01:30+01:00')!=instant('2026-10-25T01:30+00:00')
    assert project({'sale_price':'0.10'},'0.30')['price']=='0.10'


def test_publication_review_and_signed_confirm_preserve_identity(world,published):
    product,row,adapter,_=published
    before=(row.shopify_product_id,drafts.evidence(row)['variant_id'],drafts.evidence(row)['inventory_item_id'])
    change(world,live=True,shopify_status='ACTIVE',shopify_channels={'online-store':True,'shop':True})
    plan,token=publication.preview(product,adapter)
    assert len(plan['channels'])==3 and all('write' not in c for c in adapter.calls)
    assert plan['classification']=='update'
    drafts.confirm(token,True,adapter,expected_product_id=product.id)
    assert adapter.remote['status']=='ACTIVE' and adapter.memberships=={'gid://shopify/Publication/1','gid://shopify/Publication/2'}
    assert before==(row.shopify_product_id,drafts.evidence(row)['variant_id'],drafts.evidence(row)['inventory_item_id'])
    plan,token=publication.preview(product,adapter)
    assert plan['classification']=='no_change' and token is None
    plan,_=drafts.preview(product,adapter)
    assert plan['publication']['pending'] is False and plan['classification']=='no_change'


def test_reviewed_unpublish_changes_only_selected_channel(world,published):
    product,row,adapter,_=published
    adapter.memberships={'gid://shopify/Publication/1','gid://shopify/Publication/2'}
    change(world,live=True,shopify_channels={'online-store':False})
    _,token=publication.preview(product,adapter);drafts.confirm(token,True,adapter)
    assert adapter.memberships=={'gid://shopify/Publication/2'}
    assert adapter.calls.count('publication_write')==1


@pytest.mark.parametrize('failure',['scope','unavailable','incomplete','nonce','changed','ack'])
def test_publication_failure_does_not_guess_or_write(world,published,failure):
    product,row,adapter,_=published
    change(world,live=True,shopify_channels={'online-store':True})
    if failure=='scope':
        adapter.test_connection=lambda c:{'shop_id':row.store_id,'scopes':['read_products','write_products','read_publications']}
        plan,token=publication.preview(product,adapter)
        assert any('write_publications' in b for b in plan['blockers']) and token is None
    elif failure=='unavailable':
        change(world,shopify_channels={'not-installed':True})
        with pytest.raises(ShopifyError): publication.preview(product,adapter)
    elif failure=='incomplete':
        adapter.incomplete_channels=True
        with pytest.raises(ShopifyError): publication.preview(product,adapter)
    elif failure=='nonce':
        adapter.remote['metafield']['value']='wrong'
        with pytest.raises(ShopifyError): publication.preview(product,adapter)
    else:
        _,token=publication.preview(product,adapter)
        if failure=='changed': change(world,description='Other draft')
        with pytest.raises(ShopifyError): drafts.confirm(token,failure!='ack',adapter)
    assert not any(c in adapter.calls for c in ['status_write','publication_write','product_update','product_create'])


def test_publication_timeout_requires_read_only_recovery(world,published):
    product,row,adapter,_=published
    change(world,live=True,shopify_channels={'online-store':True})
    _,token=publication.preview(product,adapter);adapter.channel_timeout=True
    drafts.confirm(token,True,adapter)
    assert row.state=='uncertain'
    writes=adapter.calls.count('publication_write')
    with pytest.raises(ShopifyError): publication.preview(product,adapter)
    drafts.reconcile(row,adapter=adapter)
    assert row.state=='verified' and adapter.calls.count('publication_write')==writes


def test_future_price_changes_do_not_invalidate_effective_fingerprint(world,published):
    product,row,adapter,_=published
    initial=source_fingerprint(product,[])
    approve(world,published)
    assert source_fingerprint(product,[])==initial
    change(world,sale_price='2.50',sale_end_date='2026-11-04')
    assert source_fingerprint(product,[])==initial
    assert sales.context(product,row)!=json.loads(ShopifySaleSchedule.query.one().evidence_json)['source']


def test_authorised_activation_expiry_and_deduplicated_outcomes(world,published,clock):
    product,row,adapter,notices=published
    auth,_=approve(world,published)
    assert sales.transition(auth,adapter)=='not_due' and not any('write' in c for c in adapter.calls)
    clock[0]=datetime(2026,11,1,10)
    assert sales.transition(auth,adapter)=='active'
    current=adapter.remote['simpleVariants']['nodes'][0]
    assert (current['price'],current['compareAtPrice'])==('3.00','3.50')
    assert len(notices)==1
    assert sales.transition(auth,adapter)=='not_due' and len(notices)==1
    clock[0]=datetime(2026,11,2,10)
    assert sales.transition(auth,adapter)=='completed'
    assert (current['price'],current['compareAtPrice'])==('3.50',None)
    assert len(notices)==2
    with __import__('flask').current_app.test_request_context(): assert states_for([product])[product.id]['label']=='Up to Date'
    for operation_id,event in notices:
        operation=db.session.get(CatalogueOperation,operation_id)
        message=json.loads(operation.scope)['shopify_discord_events'][event]['summary']['products'][0]
        assert message['operation'] in {'Sale Activated','Sale Ended'} and message['outcome']=='verified_success'


@pytest.mark.parametrize('failure',['source','remote_price','verification','permission'])
def test_sale_held_on_unrelated_or_unapproved_changes(world,published,clock,failure):
    product,row,adapter,_=published;auth,_=approve(world,published);clock[0]=datetime(2026,11,1,10)
    if failure=='source': change(world,description='Unapproved metadata')
    elif failure=='remote_price': adapter.remote['simpleVariants']['nodes'][0]['price']='99.00'
    elif failure=='verification': row.state='uncertain';db.session.commit()
    else: adapter.test_connection=lambda c:{'shop_id':row.store_id,'scopes':['read_products']}
    try: state=sales.transition(auth,adapter)
    except ShopifyError: state='held'
    assert state in {'held','needs_attention'} and 'variant_write' not in adapter.calls


@pytest.mark.parametrize('timeout',['before','after'])
def test_sale_uncertainty_is_never_replayed(world,published,clock,timeout):
    product,row,adapter,_=published;auth,_=approve(world,published);clock[0]=datetime(2026,11,1,10)
    adapter.sale_timeout=timeout
    assert sales.transition(auth,adapter)=='recovery_required'
    calls=adapter.calls.count('variant_write')
    assert sales.transition(auth,adapter)=='not_due'
    if timeout=='after':
        drafts.reconcile(row,adapter=adapter)
        assert row.state=='verified' and auth.state=='active'
        message=json.loads(db.session.get(CatalogueOperation,row.operation_id).scope)['shopify_discord_events']['verified_success']['summary']['products'][0]
        assert message['sale_status']=='active' and message['operation']=='Sale Activated'
    else:
        with pytest.raises(ShopifyError): drafts.reconcile(row,adapter=adapter)
        assert row.state=='uncertain'
    assert adapter.calls.count('variant_write')==calls


def test_downtime_does_not_activate_an_expired_sale(world,published,clock):
    auth,_=approve(world,published);clock[0]=datetime(2026,11,3,10)
    assert sales.transition(auth,published[2])=='completed'
    assert 'variant_write' not in published[2].calls and not published[3]


def test_unauthorised_and_future_run_now_are_local(world,published,clock):
    product,row,adapter,_=published
    tasks.enqueue(tasks.SALE_KEY,manual=True)
    key,run_id=tasks.claim('sale-owner');tasks.run_slice(key,run_id,'sale-owner',adapter=adapter)
    assert adapter.calls==[]
    auth,_=approve(world,published);adapter.calls.clear()
    tasks.enqueue(tasks.SALE_KEY,manual=True)
    key,run_id=tasks.claim('sale-owner');tasks.run_slice(key,run_id,'sale-owner',adapter=adapter)
    assert adapter.calls==[] and auth.state=='authorised'


@pytest.mark.parametrize('variable_world',['Variable Collection','Single Variable'],indirect=True)
def test_variable_sale_amounts_and_all_retained_identities(variable_world,clock,monkeypatch):
    monkeypatch.setattr('app.shopify_notifications.deliver',lambda *a,**k:False)
    monkeypatch.setenv('DEFAULT_REQUIRES_SHIPPING','false')
    monkeypatch.setenv('DEFAULT_COUNTRY_OF_ORIGIN','GB')
    monkeypatch.setenv('DEFAULT_BACKORDERS','no')
    monkeypatch.setenv('DEFAULT_MANAGE_STOCK','false')
    product,path,*_=variable_world;adapter=VariableAcceptanceAdapter()
    from test_shopify_variables import rescan
    rescan(variable_world,requires_shipping=False)
    _,token=drafts.preview(product,adapter);row=drafts.confirm(token,True,adapter)
    baseline=copy.deepcopy(drafts.evidence(row)['variant_maps'])
    before=source_fingerprint(product,[])
    for variant in adapter.remote['variants']['nodes']:
        assert variant['inventoryPolicy']=='DENY' and variant['inventoryItem']['tracked'] is False
        assert variant['inventoryItem']['requiresShipping'] is False and variant['inventoryItem']['countryCodeOfOrigin']=='GB'
    document=json.loads(path.read_text())
    document.update(sale_price='1.00',sale_start_date='2026-11-01T10:00Z',sale_end_date='2026-11-02T10:00Z')
    for index,variant in enumerate(product.variations):
        attributes={a.name:a.value for a in variant.attributes}
        values=apply_variation_modifiers(document,attributes,log=lambda *a,**k:None)
        document.setdefault('variation_modifiers',{})[json_key(attributes)]={**values,'sale_price':str(Decimal('1.00')+Decimal(index)/10)}
    path.write_text(json.dumps(document))
    from app.utils.scanner import scan_collection
    from app.utils.ingest import ingest_rows_to_db
    rows=scan_collection(str(path.parent),'https://invalid.example/',str(variable_world[3]),force_update=True,log=lambda *a,**k:None)
    ingest_rows_to_db(rows,log=lambda *a,**k:None)
    assert source_fingerprint(product,[])==before  # Future-only metadata is not a current-price change.
    _,token=sales.review(product,adapter);auth=sales.authorise(product,token,True,adapter)
    clock[0]=datetime(2026,11,1,10)
    assert sales.transition(auth,adapter)=='active',auth.error
    assert baseline==drafts.evidence(row)['variant_maps']
    actual={v['id']:v for v in adapter.remote['variants']['nodes']}
    for wanted in json.loads(auth.evidence_json)['variants']:
        assert actual[wanted['id']]['price']==wanted['sale'] and actual[wanted['id']]['compareAtPrice']==wanted['regular']
    clock[0]=datetime(2026,11,2,10)
    assert sales.transition(auth,adapter)=='completed',auth.error
    assert baseline==drafts.evidence(row)['variant_maps']


def test_migration_0014_upgrade_and_scoped_uniqueness(tmp_path):
    from sqlalchemy.exc import IntegrityError
    path=tmp_path/'upgrade.db';config=_alembic_config('sqlite:///'+str(path));command.upgrade(config,'0013_scheduled_tasks')
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO shopify_attribute_identity (store_id,source_key,state,evidence_json) VALUES ('shop','retained','verified','{}')")
    ensure_database('sqlite:///'+str(path))
    with sqlite3.connect(path) as connection:
        assert connection.execute('select version_num from alembic_version').fetchone()[0]=='0014_shopify_sales'
        assert connection.execute('select source_key from shopify_attribute_identity').fetchone()[0]=='retained'
        assert connection.execute("select enabled,interval_minutes from scheduled_task where key='shopify_sales'").fetchone()==(1,5)
        assert connection.execute('select count(*) from shopify_sale_schedule').fetchone()[0]==0


def test_sale_task_lease_and_single_transition_receipt(world,published,clock):
    auth,_=approve(world,published);clock[0]=datetime(2026,11,1,10)
    run_id=tasks.enqueue(tasks.SALE_KEY,manual=True)
    assert tasks.claim('one')==(tasks.SALE_KEY,run_id) and tasks.claim('two') is None
    tasks.run_slice(tasks.SALE_KEY,run_id,'one',adapter=published[2])
    assert auth.state=='active'
    receipts=[op for op in CatalogueOperation.query.all() if json.loads(op.scope).get('mode')=='sale_activate']
    assert len(receipts)==1 and receipts[0].status=='succeeded'
    assert receipts[0].id==drafts.digest([auth.id,auth.generation,'activate'])[:32]
    assert tasks.snapshot(db.session.get(ScheduledTask,tasks.SALE_KEY))['interval']==5


def test_explicit_deny_and_shipping_false_survive_defaults(world,published,monkeypatch):
    product,row,adapter,_=published
    monkeypatch.setenv('DEFAULT_REQUIRES_SHIPPING','true')
    monkeypatch.setenv('DEFAULT_MANAGE_STOCK','true')
    monkeypatch.setenv('DEFAULT_BACKORDERS','yes')
    change(world,requires_shipping=False,manage_stock=False,backorders='no',country_of_origin='CN')
    plan,_=drafts.preview(product,adapter)
    managed=plan['managed']['variant']
    assert managed['inventoryPolicy']=='DENY' and managed['inventoryItem']['tracked'] is False
    assert managed['inventoryItem']['requiresShipping'] is False and managed['inventoryItem']['countryCodeOfOrigin']=='CN'


def test_environment_defaults_invalidate_only_inheritors(world,published,monkeypatch):
    product,row,adapter,_=published
    before=source_fingerprint(product,[])
    monkeypatch.setenv('DEFAULT_REQUIRES_SHIPPING','true')
    assert source_fingerprint(product,[])!=before
    change(world,requires_shipping=False)
    before=source_fingerprint(product,[])
    monkeypatch.setenv('DEFAULT_REQUIRES_SHIPPING','false')
    assert source_fingerprint(product,[])==before


def test_status_change_back_to_draft_is_compared_without_channel_selections(world,published):
    product,row,adapter,_=published
    adapter.remote['status']='ACTIVE'
    plan,token=drafts.preview(product,adapter)
    assert plan['publication']['pending'] and plan['classification']=='update' and token
    assert any(f['field']=='publication.status' and f['after']=='DRAFT' for f in plan['fields'])
    _,token=publication.preview(product,adapter)
    drafts.confirm(token,True,adapter)
    assert adapter.remote['status']=='DRAFT'


def test_publication_requires_complete_native_and_attribute_evidence(world,published):
    product,row,adapter,_=published
    data=drafts.evidence(row);data['verification']['attributes']='unverified';drafts.save(row,data)
    with pytest.raises(ShopifyError,match='fully verified'): publication.preview(product,adapter)
    assert 'publication_write' not in adapter.calls


def test_sale_authorisation_is_separate_expiring_and_source_bound(world,published):
    product,row,adapter,_=published
    change(world,sale_price='3.00',sale_start_date='2026-11-01')
    _,token=sales.review(product,adapter)
    with pytest.raises(ShopifyError,match='Explicit'): sales.authorise(product,token,False,adapter)
    with pytest.raises(ShopifyError,match='invalid or expired'): sales.authorise(product,token+'broken',True,adapter)
    change(world,sale_price='2.50')
    with pytest.raises(ShopifyError,match='changed'): sales.authorise(product,token,True,adapter)
    assert ShopifySaleSchedule.query.count()==0 and 'variant_write' not in adapter.calls


def test_restart_can_reconcile_accepted_price_without_replaying(world,published,clock):
    product,row,adapter,_=published;auth,_=approve(world,published)
    row_id,auth_id=row.id,auth.id;clock[0]=datetime(2026,11,1,10)
    adapter.sale_timeout='after'
    assert sales.transition(auth,adapter)=='recovery_required'
    writes=adapter.calls.count('variant_write')
    db.session.remove()  # New connection/session; original durable authority only.
    retained=db.session.get(ShopifyProductIdentity,row_id)
    drafts.reconcile(retained,adapter=adapter)
    assert db.session.get(ShopifySaleSchedule,auth_id).state=='active'
    assert adapter.calls.count('variant_write')==writes


def test_interrupted_transition_read_failure_stays_recovery_required(world,published,clock):
    product,row,adapter,_=published;auth,_=approve(world,published)
    clock[0]=datetime(2026,11,1,10);adapter.sale_timeout='before'
    sales.transition(auth,adapter)
    auth.state='activating';db.session.commit()  # Simulate death after durable pending record.
    run_id=tasks.enqueue(tasks.SALE_KEY,manual=True)
    assert tasks.claim('restarted')==(tasks.SALE_KEY,run_id)
    writes=adapter.calls.count('variant_write')
    tasks.run_slice(tasks.SALE_KEY,run_id,'restarted',adapter=adapter)
    assert auth.state=='recovery_required' and adapter.calls.count('variant_write')==writes


def test_pause_never_silently_restores_active_prices(world,published,clock):
    auth,_=approve(world,published);clock[0]=datetime(2026,11,1,10)
    sales.transition(auth,published[2]);writes=published[2].calls.count('variant_write')
    sales.pause(auth);clock[0]=datetime(2026,11,3,10)
    assert sales.transition(auth,published[2])=='not_due'
    assert published[2].calls.count('variant_write')==writes
    assert published[2].remote['simpleVariants']['nodes'][0]['price']=='3.00'


def test_interactive_capacity_defers_before_consuming_price_receipt(world,published,clock):
    from app.shopify_capacity import BackgroundYield
    product,row,adapter,_=published;auth,_=approve(world,published);clock[0]=datetime(2026,11,1,10)
    normal=adapter.test_connection
    adapter.test_connection=lambda c:(_ for _ in ()).throw(BackgroundYield('Interactive priority'))
    with pytest.raises(BackgroundYield): sales.transition(auth,adapter)
    assert auth.state=='authorised' and not db.session.get(CatalogueOperation,drafts.digest([auth.id,auth.generation,'activate'])[:32])
    adapter.test_connection=normal
    assert sales.transition(auth,adapter)=='active'


def test_background_authority_allows_only_price_mutations(world,published):
    from app.shopify_capacity import background_work,before,BackgroundYield
    from app.shopify_connection import PRICE_QUERY,VARIANTS_UPDATE,UPDATE_QUERY
    from app.models import ShopifyApiCapacity
    ShopifyApiCapacity.query.delete();db.session.commit()  # No interactive owner in this case.
    with background_work(lambda:None,price_transitions=True):
        for query in (PRICE_QUERY,VARIANTS_UPDATE): before(drafts.configuration(),writing=True,query=query)
        for query in (PUBLICATION_PUBLISH,UPDATE_QUERY):
            with pytest.raises(BackgroundYield): before(drafts.configuration(),writing=True,query=query)
    with background_work(lambda:None):
        with pytest.raises(BackgroundYield): before(drafts.configuration(),writing=True,query=PRICE_QUERY)


def test_publication_transport_keeps_normal_version_and_fixed_inputs(monkeypatch):
    from test_shopify_foundation import Session,Response,TOKEN
    from app.shopify_connection import ShopifyConfiguration
    config=ShopifyConfiguration('fictional.myshopify.com',TOKEN,'2026-10')
    session=Session(Response({'data':{'publishablePublish':{'userErrors':[]}}}))
    monkeypatch.setattr('requests.Session',lambda:session)
    ShopifyAdapter().set_publication(config,'gid://shopify/Product/1','gid://shopify/Publication/2',True)
    url,options=session.calls[0]
    assert '/2026-10/graphql.json' in url and options['timeout']==(5,15)
    assert options['json']=={'query':PUBLICATION_PUBLISH,'variables':{'id':'gid://shopify/Product/1','input':[{'publicationId':'gid://shopify/Publication/2'}]}}


def test_publication_discovery_is_bounded_and_handles_actual_channels(monkeypatch):
    adapter=ShopifyAdapter();calls=[]
    def read(config,query,variables):
        calls.append(variables['after'])
        return {'publications':{'nodes':[{'id':'gid://shopify/Publication/1'}],'pageInfo':{'hasNextPage':True,'endCursor':'same'}}}
    monkeypatch.setattr(adapter,'_read',read)
    with pytest.raises(ShopifyError,match='incomplete'): adapter.publications(None)
    assert calls==[None,'same']
    assert 'channels(first:' in PUBLICATIONS_QUERY and 'catalogType: APP' in PUBLICATIONS_QUERY


def test_migration_fresh_upgrade_uniqueness_and_restart(tmp_path):
    path=tmp_path/'fresh.db';uri='sqlite:///'+str(path)
    ensure_database(uri)
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO shopify_sale_schedule(id,store_id,source_identity,identity_id,generation,state,authorised_at,updated_at,evidence_json) VALUES('first','shop','source',99,'generation','authorised',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'{}')")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO shopify_sale_schedule(id,store_id,source_identity,identity_id,generation,state,authorised_at,updated_at,evidence_json) VALUES('duplicate','shop','source',99,'generation','authorised',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'{}')")
    ensure_database(uri)  # Restart does not replace authorisations or reset task state.
    with sqlite3.connect(path) as connection:
        assert connection.execute('select id,state from shopify_sale_schedule').fetchall()==[('first','authorised')]
        assert connection.execute('select count(*) from shopify_product_identity').fetchone()[0]==0
    with pytest.raises(RuntimeError,match='downgrade refused'):
        command.downgrade(_alembic_config(uri),'0013_scheduled_tasks')


def test_new_controls_are_admin_only_csrf_guarded_and_local_on_get(world,published,monkeypatch):
    from flask import current_app
    product,row,adapter,_=published
    from app.models import User
    client=current_app.test_client()
    with client.session_transaction() as session:
        session['_user_id']=str(User.query.first().id);session['_fresh']=True
    monkeypatch.setattr('app.shopify_connection.ShopifyAdapter._request',lambda *a,**k:pytest.fail('Rendering made an API request'))
    for suffix in ('publication','sale'):
        url=f'/products/{product.id}/shopify/{suffix}'
        assert client.get(url).status_code==200
        current_app.config['WTF_CSRF_ENABLED']=True
        assert client.post(url,data={'action':'preview'}).status_code==400
        current_app.config['WTF_CSRF_ENABLED']=False
    assert ShopifySaleSchedule.query.count()==0
