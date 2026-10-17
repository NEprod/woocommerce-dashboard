"""Synthetic purchases only. All Shopify/Discord traffic is forbidden or mocked."""
import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import requests
from alembic import command
from sqlalchemy import text

from app import db, scheduled_tasks as tasks, shopify_orders as service, order_queries as queries
from app.models import ShopifyOrder, ShopifyOrderLine, ShopifyOrderSync, ScheduledTask, ScheduledTaskRun, User
from app.shopify_connection import ShopifyConfiguration, ShopifyAdapter, ShopifyError, READ_QUERIES, WRITE_QUERIES
from app.order_personalisation import parse
from app.orders_workspace import overview, listing
from app.database import _alembic_config, ensure_database, migration_head
from test_setup_flow import setup_app

STORE='gid://shopify/Shop/42'
ORDER='gid://shopify/Order/1001'
PRODUCT='gid://shopify/Product/100'
VARIANT='gid://shopify/ProductVariant/200'
REV='a'*64


def bag(amount='3.50'):
    return {kind:{'amount':amount,'currencyCode':'GBP'} for kind in ('shopMoney','presentmentMoney')}


def properties(name, years):
    snapshot={'schema_version':1,'product_id':'100','revision':REV,'visible_encoding':'html_entities',
        'fields':[{'id':'recipient','label':'Recipients Name','type':'text','property_key':'Recipients Name','value':name,'display':name},
                  {'id':'anniversary_length','label':'Anniversary Years','type':'number','property_key':'Anniversary Years','value':years,'display':years}]}
    return [{'key':'Recipients Name','value':name},{'key':'Anniversary Years','value':str(years)},
            {'key':'_tlc_configuration','value':REV},{'key':'_tlc_personalisation','value':json.dumps(snapshot)}]


def line(number=1,name='Ellie',years=6):
    return {'id':f'gid://shopify/LineItem/{number}','title':'Fictional Anniversary Card','variantTitle':'Blue',
        'sku':'SAMPLE-1','quantity':1,'currentQuantity':1,'unfulfilledQuantity':1,'refundableQuantity':1,
        'product':{'id':PRODUCT},'variant':{'id':VARIANT},'customAttributes':properties(name,years),
        **{key:bag() for key in queries.LINE_MONEY}}


def order(number=1001):
    return {'id':f'gid://shopify/Order/{number}','name':f'#TEST-{number}',
        'createdAt':'2026-10-16T10:00:00Z','updatedAt':'2026-10-16T10:01:00Z','test':True,'currencyCode':'GBP',
        'displayFinancialStatus':'PAID','displayFulfillmentStatus':'UNFULFILLED','cancelledAt':None,'cancelReason':None,
        'email':'synthetic@example.test','phone':None,'customer':{'id':'gid://shopify/Customer/50','displayName':'Fictional Buyer'},
        'shippingAddress':{'name':'Fictional Buyer','address1':'1 Fictional Test Road','city':'Example Town','zip':'TEST ONLY','country':'United Kingdom'},
        **{key:bag('7.00' if key in {'totalPriceSet','currentTotalPriceSet','subtotalPriceSet','currentSubtotalPriceSet'} else '0.00') for key in queries.ORDER_MONEY},
        'shippingLines':{'nodes':[{'id':'gid://shopify/ShippingLine/1','title':'Fictional delivery','code':'TEST',
            'originalPriceSet':bag('0.00'),'discountedPriceSet':bag('0.00')}],'pageInfo':{'hasNextPage':False}},
        'lineItems':{'nodes':[line(),line(2,'Freddie',5)],'pageInfo':{'hasNextPage':False,'endCursor':'line-end'}}}


class Adapter:
    def __init__(self, rows=None):
        self.rows={o['id']:o for o in (rows if rows is not None else [order()])}
        self.scopes=['read_orders'];self.calls=[];self.error=None;self.pages={};self.index_pages={}

    def test_connection(self,config):
        self.calls.append(('connection',None))
        return {'shop_id':STORE,'domain':config.domain,'scopes':self.scopes}

    def _read(self,config,query,variables):
        assert query in queries.READ_QUERIES and query not in WRITE_QUERIES
        assert config.api_version=='2026-10'
        self.calls.append((query,copy.deepcopy(variables)))
        if self.error: raise self.error
        if query==queries.INDEX:
            if variables['after'] in self.index_pages: return {'orders':copy.deepcopy(self.index_pages[variables['after']])}
            return {'orders':{'nodes':[{'id':r['id'],'updatedAt':r['updatedAt']} for r in self.rows.values()],
                'pageInfo':{'hasNextPage':False,'endCursor':'order-end'}}}
        if query==queries.REVISION:
            row=self.rows.get(variables['id'])
            return {'order':{'id':row['id'],'updatedAt':row['updatedAt']} if row else None}
        result=copy.deepcopy(self.rows.get(variables['id']))
        if result and variables['after'] in self.pages: result['lineItems']=copy.deepcopy(self.pages[variables['after']])
        return {'order':result}


@pytest.fixture
def orders_app(setup_app,monkeypatch):
    app,path=setup_app
    monkeypatch.setattr(requests.Session,'request',lambda *a,**k:pytest.fail('Unexpected network'))
    from app import shopify_drafts
    monkeypatch.setattr(shopify_drafts,'configuration',lambda:ShopifyConfiguration('fictional.myshopify.com','fictional_access_token_for_tests'))
    with app.app_context():
        db.session.add(User(id=1,email='admin@example.test',username='fictional-admin',password='unused',is_admin=True));db.session.commit()
        yield app,path


def client(app):
    value=app.test_client()
    with value.session_transaction() as session: session['_user_id']='1';session['_fresh']=True
    return value


def run(adapter):
    run_id=tasks.enqueue(tasks.ORDERS_KEY,manual=True)
    task=db.session.get(ScheduledTask,tasks.ORDERS_KEY)
    if task.resume_at: task.resume_at=tasks.now()-timedelta(seconds=1);db.session.commit()
    assert tasks.claim('owner')==(tasks.ORDERS_KEY,run_id)
    tasks.run_slice(tasks.ORDERS_KEY,run_id,'owner',adapter=adapter)
    return db.session.get(ScheduledTaskRun,run_id)


def save(raw=None):
    raw=copy.deepcopy(raw or order());raw['_lines']=raw['lineItems']['nodes']
    header,lines=service.normalise(raw,api_version='2026-10')
    changed=service.persist(STORE,header,lines);db.session.commit();db.session.expire_all()
    return changed


def test_first_import_and_independent_same_sku_personalisations(orders_app):
    assert run(Adapter()).state=='completed'
    row=ShopifyOrder.query.one(); assert row.name=='#TEST-1001' and row.test and row.quantity==2
    a,b=row.lines; assert a.sku==b.sku=='SAMPLE-1' and a.id!=b.id
    assert [(json.loads(l.personalisation_json)['fields'][0]['value'],json.loads(l.personalisation_json)['fields'][1]['value']) for l in row.lines]==[('Ellie',6),('Freddie',5)]
    assert all(json.loads(l.personalisation_json)['revision']==REV for l in row.lines)
    assert row.total==Decimal('7.00') and json.loads(row.shipping_json)[0]['title']=='Fictional delivery'
    assert row.customer_name=='Fictional Buyer' and row.email.endswith('.test')
    assert row.lines[0].product_id==PRODUCT and row.lines[0].variant_id==VARIANT
    assert ShopifyOrderSync.query.one().successful_at


def test_idempotent_repeat_restart_and_offline_readability(orders_app):
    app,path=orders_app;run(Adapter());ids=[l.id for l in ShopifyOrderLine.query.all()]
    db.session.remove();assert run(Adapter()).state=='completed'
    assert ShopifyOrder.query.count()==1 and [l.id for l in ShopifyOrderLine.query.all()]==ids
    adapter=Adapter();adapter.error=ShopifyError('secret/customer must never be stored')
    assert run(adapter).state=='failed'
    assert 'secret/customer' not in ShopifyOrderSync.query.one().error
    assert client(app).get('/orders/').status_code==200
    assert client(app).get('/orders/1').status_code==200


def test_permission_gate_stops_automatic_retry(orders_app):
    adapter=Adapter();adapter.scopes=['read_products']
    assert run(adapter).state=='failed' and len(adapter.calls)==1
    state=ShopifyOrderSync.query.one();assert state.state=='authorisation_required' and state.successful_at is None
    assert tasks.enqueue(tasks.ORDERS_KEY) is None
    app,_=orders_app;assert b'permission required' in client(app).get('/orders/').data
    adapter.scopes=['read_orders'];assert run(adapter).state=='completed'


@pytest.mark.parametrize('field,value',[
    ('displayFinancialStatus','REFUNDED'),('displayFulfillmentStatus','FULFILLED'),('cancelledAt','2026-10-16T11:00:00Z')])
def test_commerce_update(orders_app,field,value):
    save();raw=order();raw['updatedAt']='2026-10-16T11:01:00Z';raw[field]=value
    if field=='displayFulfillmentStatus':
        for l in raw['lineItems']['nodes']:l['unfulfilledQuantity']=0
    assert save(raw)
    row=ShopifyOrder.query.one()
    assert row.updated_at.hour==11 and ShopifyOrder.query.count()==1
    assert row.fulfillment_status=='FULFILLED' if field=='displayFulfillmentStatus' else True


def test_older_response_never_overwrites_newer(orders_app):
    raw=order();raw['updatedAt']='2026-10-16T12:00:00Z';raw['displayFinancialStatus']='REFUNDED';save(raw)
    assert not save(order())
    assert ShopifyOrder.query.one().financial_status=='REFUNDED'


def test_reorder_removed_lines_and_quantity(orders_app):
    save();ids={l.remote_id:l.id for l in ShopifyOrderLine.query.all()}
    raw=order();raw['updatedAt']='2026-10-16T12:00:00Z';raw['lineItems']['nodes'].reverse()
    raw['lineItems']['nodes'][0].update(quantity=3,currentQuantity=2,unfulfilledQuantity=2)
    save(raw);assert ShopifyOrder.query.one().quantity==3 and ShopifyOrder.query.one().outstanding_quantity==3
    assert {l.remote_id:l.id for l in ShopifyOrderLine.query.all()}==ids
    raw['updatedAt']='2026-10-16T13:00:00Z';raw['lineItems']['nodes']=raw['lineItems']['nodes'][:1];save(raw)
    assert ShopifyOrderLine.query.count()==2 and ShopifyOrderLine.query.filter_by(is_current=False).count()==1


def test_paginated_line_readback_and_midread_revision(orders_app):
    adapter=Adapter();first=adapter.rows[ORDER]['lineItems'];first['nodes']=first['nodes'][:1];first['pageInfo']={'hasNextPage':True,'endCursor':'next-line'}
    adapter.pages['next-line']={'nodes':[line(2,'Freddie',5)],'pageInfo':{'hasNextPage':False,'endCursor':'end'}}
    assert run(adapter).state=='completed' and ShopifyOrderLine.query.count()==2
    adapter.pages['next-line']['pageInfo']={'hasNextPage':True,'endCursor':'next-line'}
    assert run(adapter).state=='failed' and ShopifyOrderLine.query.count()==2


def test_partial_index_page_interruption_and_restart(orders_app):
    app,_=orders_app;app.config['SHOPIFY_ORDERS_BATCH_SIZE']=1
    second=order(1002)
    for l in second['lineItems']['nodes']:l['id']=l['id']+'0'
    adapter=Adapter([order(),second]);result=run(adapter)
    assert result.state=='paused' and ShopifyOrder.query.count()==1
    assert tasks.snapshot(db.session.get(ScheduledTask,tasks.ORDERS_KEY))['progress']['remaining']==1
    sync=ShopifyOrderSync.query.one();assert len(json.loads(sync.pending_json))==1 and sync.checkpoint is None
    db.session.remove();adapter.calls.clear();assert run(adapter).state=='completed'
    assert ShopifyOrder.query.count()==2 and not any(q==queries.INDEX for q,_ in adapter.calls)


def test_multiple_index_pages_cursor_checkpoint(orders_app):
    adapter=Adapter();adapter.index_pages={None:{'nodes':[{'id':ORDER,'updatedAt':order()['updatedAt']}],
        'pageInfo':{'hasNextPage':True,'endCursor':'next-order'}},'next-order':{'nodes':[],'pageInfo':{'hasNextPage':False,'endCursor':None}}}
    assert run(adapter).state=='paused';assert ShopifyOrderSync.query.one().cursor=='next-order'
    assert run(adapter).state=='completed' and ShopifyOrderSync.query.one().checkpoint


@pytest.mark.parametrize('failure',[ShopifyError('timeout synthetic@example.test'),ShopifyError('throttled')])
def test_incomplete_remote_preserves_records_and_checkpoint(orders_app,failure):
    run(Adapter());previous=ShopifyOrderSync.query.one().successful_at
    adapter=Adapter();adapter.error=failure
    assert run(adapter).state=='failed'
    assert ShopifyOrder.query.count()==1 and ShopifyOrderSync.query.one().successful_at==previous
    assert 'synthetic@example.test' not in ScheduledTaskRun.query.order_by(ScheduledTaskRun.created_at.desc()).first().error


def test_missing_or_deleted_products_do_not_lose_order_history(orders_app):
    raw=order();raw['lineItems']['nodes'][0].update(product=None,variant=None);save(raw)
    assert ShopifyOrderLine.query.first().product_id is None
    assert json.loads(ShopifyOrderLine.query.first().personalisation_json)['fields'][0]['value']=='Ellie'


@pytest.mark.parametrize('private',[None,'bad json','{"schema_version":99}',json.dumps({'schema_version':1,'fields':None})])
def test_missing_malformed_snapshot_preserves_public_answers(orders_app,private):
    raw=order();props=raw['lineItems']['nodes'][0]['customAttributes'];props[:]=[p for p in props if p['key']!='_tlc_personalisation']
    if private is not None:props.append({'key':'_tlc_personalisation','value':private})
    save(raw);parsed=json.loads(ShopifyOrderLine.query.first().personalisation_json)
    assert parsed['warnings'] and parsed['public'][0]['value']=='Ellie'


def test_legacy_readable_properties_and_purchase_time_labels(orders_app):
    raw=order();raw['lineItems']['nodes'][0]['customAttributes']=[{'key':'Legacy Name','value':'Old label answer'}];save(raw)
    parsed=json.loads(ShopifyOrderLine.query.first().personalisation_json)
    assert not parsed['fields'] and parsed['public']==[{'label':'Legacy Name','value':'Old label answer'}]


@pytest.mark.parametrize('change',[{'type':[]},{'value':True},{'id':'BAD-ID'},{'choice_ids':{'bad':1}}])
def test_invalid_typed_field_warning_and_raw_retention(change):
    props=properties('Synthetic',5);data=json.loads(props[-1]['value']);data['fields'][1].update(change);props[-1]['value']=json.dumps(data)
    parsed=parse(props,PRODUCT)
    assert parsed['warnings'] and parsed['fields'][0]['value']=='Synthetic'


def test_choices_boolean_types_revision_and_identity_warnings():
    props=properties('Synthetic',5);data=json.loads(props[-1]['value']);data['revision']='b'*64;data['product_id']='999'
    data['fields']+=[{'id':'choice','label':'Colour','type':'dropdown','value':'red','display':'Ruby','choice_ids':['red']},
                    {'id':'gift','label':'Gift','type':'checkbox','value':True}]
    props[-1]['value']=json.dumps(data);parsed=parse(props,PRODUCT)
    assert len(parsed['warnings'])==2 and parsed['fields'][2]['choice_ids']==['red'] and parsed['fields'][3]['value'] is True


@pytest.mark.parametrize('identity',[{},['wrong'],None])
def test_malformed_snapshot_product_identity_preserves_customer_answers(identity):
    props=properties('Synthetic',5);snapshot=json.loads(props[-1]['value'])
    snapshot['product_id']=identity;props[-1]['value']=json.dumps(snapshot)
    parsed=parse(props,PRODUCT)
    assert parsed['warnings'] and parsed['fields'][0]['value']=='Synthetic'


def test_large_typed_integer_does_not_break_snapshot_parsing():
    props=properties('Synthetic',10**350)
    parsed=parse(props,PRODUCT)
    assert parsed['fields'][1]['value']==10**350


def test_protected_html_pages_and_independent_line_items(orders_app):
    app,_=orders_app;raw=order();props=properties('<img src=x onerror=alert(1)>',6)
    raw['lineItems']['nodes'][0]['customAttributes']=props;save(raw)
    response=client(app).get('/orders/1');assert response.status_code==200
    assert b'<img src=x onerror' not in response.data and b'&lt;img src=x' in response.data
    assert b'Freddie' in response.data and b'Anniversary Years' in response.data
    assert response.headers['Cache-Control']=='no-store, private'
    assert b'fictional.myshopify.com' not in response.data # No verified source domain was imported by save().
    # A fresh app context models a different request's Flask-Login cache.
    with app.app_context():
        assert app.test_client().get('/orders/').status_code in {401,302}
    db.session.get(User,1).is_admin=False;db.session.commit()
    for url in ('/orders/','/orders/1','/orders/status'):
        assert client(app).get(url).status_code==403


def test_sync_now_csrf_and_local_only_gets(orders_app,monkeypatch):
    app,_=orders_app;save()
    monkeypatch.setattr(ShopifyAdapter,'_read',lambda *a,**k:pytest.fail('Page load must not query Shopify'))
    c=client(app)
    assert c.get('/orders/').status_code==200 and c.get('/orders/1').status_code==200
    assert c.get('/orders/status').status_code==200
    assert ScheduledTaskRun.query.count()==0
    assert c.post('/orders/sync').status_code==302 and ScheduledTaskRun.query.count()==1
    app.config['WTF_CSRF_ENABLED']=True
    assert c.post('/orders/sync').status_code==400


def test_read_only_fixed_queries_reuse_transport(orders_app,monkeypatch):
    from test_shopify_foundation import Response,Session
    session=Session(Response({'data':{'orders':{'nodes':[],'pageInfo':{'hasNextPage':False}}}}))
    monkeypatch.setattr('app.shopify_connection.requests.Session',lambda:session)
    config=ShopifyConfiguration('fictional.myshopify.com','fictional_access_token_for_tests')
    ShopifyAdapter()._read(config,queries.INDEX,{'query':'updated_at:>=2026','after':None,'first':10})
    url,params=session.calls[0]
    assert url.endswith('/2026-10/graphql.json') and params['timeout']==(5,15)
    assert queries.READ_QUERIES<=READ_QUERIES and not queries.READ_QUERIES&WRITE_QUERIES
    assert all('mutation' not in q for q in queries.READ_QUERIES)


def test_filters_search_pagination_and_dashboard_metrics(orders_app):
    save();assert overview(as_of=datetime(2026,10,16,12,tzinfo=timezone.utc))['unfulfilled']==0
    current=overview(include_test=True,as_of=datetime(2026,10,16,12,tzinfo=timezone.utc))
    assert current['unfulfilled']==1 and current['items']==2 and current['today']==1
    assert listing({'q':'SAMPLE-1','test':'yes','personalised':'yes'})['pagination'].total==1
    assert listing({'test':'no'})['pagination'].total==0 and listing({'q':'missing'})['pagination'].total==0
    raw=order();raw['test']=False;raw['displayFinancialStatus']='PENDING';save(raw)
    assert overview()['unfulfilled']==0
    raw['displayFinancialStatus']='PAID';save(raw);assert overview()['items']==2
    raw['cancelledAt']='2026-10-16T11:00:00Z';save(raw);assert overview()['items']==0


def test_uk_midnight_boundary_in_bst(orders_app):
    raw=order();raw['test']=False;raw['createdAt']='2026-10-15T23:15:00Z';save(raw)
    assert overview(as_of=datetime(2026,10,16,0,tzinfo=timezone.utc))['today']==1


def test_dashboard_template_displays_units_not_dictionary_method(orders_app):
    from flask import render_template
    save()
    with orders_app[0].test_request_context('/'):
        rendered=render_template('orders/overview.html',orders_overview=overview(include_test=True))
    assert 'built-in method' not in rendered and 'Items awaiting fulfilment' in rendered
    assert '<strong>2</strong>' in rendered


def test_two_workers_coalesce_and_expired_lease_resumes(orders_app):
    app,path=orders_app;run_id=tasks.enqueue(tasks.ORDERS_KEY,manual=True)
    assert tasks.enqueue(tasks.ORDERS_KEY,manual=True)==run_id
    assert tasks.claim('first')==(tasks.ORDERS_KEY,run_id) and tasks.claim('second') is None
    task=db.session.get(ScheduledTask,tasks.ORDERS_KEY);task.lease_until=tasks.now()-timedelta(seconds=1);db.session.commit()
    assert tasks.claim('second')==(tasks.ORDERS_KEY,run_id)
    with pytest.raises(Exception):tasks.fence(tasks.ORDERS_KEY,run_id,'first')
    tasks.run_slice(tasks.ORDERS_KEY,run_id,'second',adapter=Adapter())
    assert ShopifyOrder.query.count()==1


def test_redaction_tombstone_cannot_be_rehydrated(orders_app):
    save();row=ShopifyOrder.query.one();service.redact_local_order(row);db.session.commit()
    raw=order();raw['updatedAt']='2026-10-16T15:00:00Z';save(raw)
    row=ShopifyOrder.query.one();assert row.redacted_at and row.email is None and row.customer_name is None
    assert all(l.properties_json=='[]' for l in row.lines)


def test_migration_upgrade_preserves_previous_tables_and_blocks_destructive_downgrade(tmp_path):
    path=tmp_path/'upgrade.db';config=_alembic_config(f'sqlite:///{path}')
    command.upgrade(config,'0015_customisation_templates')
    with sqlite3.connect(path) as conn:conn.execute("INSERT INTO user (id,email,username,password,is_admin) VALUES (1,'fixture@example.test','fixture','unused',1)")
    result=ensure_database(f'sqlite:///{path}')
    assert result.revision==migration_head() and result.backup_path
    with sqlite3.connect(path) as conn:
        assert conn.execute('select username from user').fetchone()[0]=='fixture'
        assert conn.execute("select enabled,interval_minutes from scheduled_task where key='shopify_orders'").fetchone()==(0,5)
        assert conn.execute('pragma foreign_key_check').fetchall()==[]
    assert ensure_database(f'sqlite:///{path}').revision==migration_head()


def test_no_discord_or_scanner_side_effects(orders_app,monkeypatch):
    from app.utils import scan_runner
    monkeypatch.setattr(scan_runner,'start_scan',lambda *a,**k:pytest.fail('Orders must not scan'))
    assert run(Adapter()).state=='completed'
    c=client(orders_app[0]);assert c.get('/orders/').status_code==200 and c.get('/orders/status').status_code==200


@pytest.mark.parametrize('field',['email','customer','shippingAddress','displayFinancialStatus','cancelledAt','subtotalPriceSet'])
def test_omission_is_not_confirmed_null_or_removal(orders_app,field):
    save();raw=order();raw.pop(field);raw['_lines']=raw['lineItems']['nodes']
    with pytest.raises(ShopifyError,match='omitted'):
        service.normalise(raw,api_version='2026-10')
    assert ShopifyOrder.query.one().email=='synthetic@example.test'


def test_null_nullable_shopify_fields_are_not_invented(orders_app):
    raw=order()
    for key in ('subtotalPriceSet','totalDiscountsSet','totalTaxSet','displayFinancialStatus','customer','shippingAddress'):raw[key]=None
    save(raw);row=ShopifyOrder.query.one()
    assert row.financial_status=='UNKNOWN' and row.customer_id is None and row.delivery_json=='null'
    assert json.loads(row.money_json)['subtotalPriceSet'] is None


def test_final_revision_change_never_commits_partial_read(orders_app):
    save()
    class Changing(Adapter):
        def _read(self,config,query,variables):
            result=super()._read(config,query,variables)
            if query==queries.REVISION:result['order']['updatedAt']='2026-10-16T20:00:00Z'
            return result
    assert run(Changing()).state=='failed'
    assert ShopifyOrder.query.one().updated_at.hour==10 and ShopifyOrderLine.query.count()==2


def test_inaccessible_remote_order_is_not_deletion(orders_app):
    save()
    class Missing(Adapter):
        def _read(self,config,query,variables):
            if query==queries.DETAIL:return {'order':None}
            return super()._read(config,query,variables)
    assert run(Missing()).state=='failed'
    assert ShopifyOrderLine.query.filter_by(is_current=True).count()==2


def test_mid_batch_failure_retains_prior_commits_and_resumes(orders_app):
    second=order(1002)
    for l in second['lineItems']['nodes']:l['id']=l['id']+'0'
    class Interrupted(Adapter):
        interrupt=True
        def _read(self,config,query,variables):
            if self.interrupt and query==queries.DETAIL and variables['id']==second['id']:raise ShopifyError('Do not persist customer payload')
            return super()._read(config,query,variables)
    adapter=Interrupted([order(),second]);assert run(adapter).state=='failed'
    assert ShopifyOrder.query.count()==1 and len(json.loads(ShopifyOrderSync.query.one().pending_json))==1
    receipt=json.loads(ScheduledTaskRun.query.order_by(ScheduledTaskRun.created_at.desc()).first().data_json)
    assert receipt['counts']['checked']==1 and receipt['pending_orders']==1
    assert ShopifyOrderSync.query.one().checkpoint is None
    adapter.interrupt=False;adapter.calls.clear();assert run(adapter).state=='completed'
    assert ShopifyOrder.query.count()==2 and not any(q==queries.INDEX for q,_ in adapter.calls)


def test_stale_worker_cannot_commit_import(orders_app):
    class LoseOwner(Adapter):
        def _read(self,config,query,variables):
            result=super()._read(config,query,variables)
            if query==queries.REVISION:
                with db.engine.begin() as connection:
                    connection.execute(text("update scheduled_task set owner='replacement' where key='shopify_orders'"))
            return result
    run(LoseOwner());assert ShopifyOrder.query.count()==0


def test_commerce_and_line_graph_rollback_together(orders_app):
    from sqlalchemy import event
    from sqlalchemy.exc import SQLAlchemyError
    def fail(connection,cursor,statement,params,context,many):
        if statement.startswith('INSERT INTO shopify_order_line'):raise RuntimeError('synthetic insert failure')
    event.listen(db.engine,'before_cursor_execute',fail)
    try:assert run(Adapter()).state=='failed'
    finally:event.remove(db.engine,'before_cursor_execute',fail)
    assert ShopifyOrder.query.count()==ShopifyOrderLine.query.count()==0


def test_independent_connections_observe_import_and_task_receipt(orders_app):
    app,path=orders_app;assert run(Adapter()).state=='completed'
    with sqlite3.connect(path) as connection:
        assert connection.execute('select count(*) from shopify_order').fetchone()[0]==1
        assert connection.execute('select count(*) from shopify_order_line').fetchone()[0]==2
        assert connection.execute("select state from scheduled_task_run where task_key='shopify_orders'").fetchone()[0]=='completed'


def test_concurrent_enqueue_and_claim_single_owner(orders_app):
    app,_=orders_app;db.session.remove()
    def queue(_):
        with app.app_context():return tasks.enqueue(tasks.ORDERS_KEY,manual=True)
    with ThreadPoolExecutor(max_workers=3) as pool:ids=list(pool.map(queue,range(3)))
    assert len(set(ids))==1
    def take(number):
        with app.app_context():return tasks.claim('owner'+str(number))
    with ThreadPoolExecutor(max_workers=3) as pool:claims=list(pool.map(take,range(3)))
    assert sum(c is not None for c in claims)==1


def test_multiple_orders_same_customer_and_indexed_pagination(orders_app):
    for number in range(1001,1028):
        raw=order(number)
        for l in raw['lineItems']['nodes']:l['id']=l['id']+str(number)
        save(raw)
    assert ShopifyOrder.query.count()==27 and ShopifyOrderLine.query.count()==54
    assert listing({'page':'1'})['pagination'].has_next
    assert len(listing({'page':'2'})['orders'])==2
    assert listing({'q':'#TEST-1005'})['pagination'].total==1


def test_imported_source_link_is_fixed_verified_shopify_host(orders_app):
    run(Adapter());response=client(orders_app[0]).get('/orders/1')
    assert b'https://fictional.myshopify.com/admin/orders/1001' in response.data


def test_nonempty_orders_downgrade_requires_approval(orders_app):
    app,path=orders_app;save();db.session.remove()
    with pytest.raises(RuntimeError,match='Orders contain historical|Packaging contains retained|Retained order events'):
        command.downgrade(_alembic_config(f'sqlite:///{path}'),'0015_customisation_templates')
    assert ShopifyOrder.query.count()==1


def test_order_foreign_key_boundary_even_on_legacy_sqlite_connections(orders_app):
    app,path=orders_app;save();db.session.remove()
    with sqlite3.connect(path) as connection:
        assert connection.execute('pragma foreign_keys').fetchone()[0]==0
        with pytest.raises(sqlite3.IntegrityError,match='parent deletion'):
            connection.execute('delete from shopify_order where id=1')
        with pytest.raises(sqlite3.IntegrityError,match='parent is required'):
            connection.execute('update shopify_order_line set order_id=999 where id=1')
    assert ShopifyOrder.query.count()==1 and ShopifyOrderLine.query.count()==2


def test_current_scope_diff_and_unchanged_extension_identity():
    import tomllib
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    app=tomllib.loads((root/'shopify/shopify.app.toml').read_text())
    scopes=set(app['access_scopes']['scopes'].split(','))
    assert 'read_orders' in scopes and 'write_orders' not in scopes and 'read_all_orders' not in scopes
    assert len(scopes)==12
    extension=tomllib.loads((root/'shopify/extensions/tlc-customisations/shopify.extension.toml').read_text())
    assert extension['uid']=='32a543f0-9ad7-6dfe-9a1e-d4437bf366175766d7e5'
