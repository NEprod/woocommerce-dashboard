"""Synthetic order events only. No Shopify/Discord traffic or customer fixtures."""
import base64
import copy
import hashlib
import hmac
import json
import sqlite3
from datetime import datetime,timedelta
from cryptography.fernet import Fernet
import pytest
from app import db, scheduled_tasks as tasks, shopify_orders as service
from app.order_models import ShopifyOrderEvent,ShopifyOrderDelivery,ShopifyOrderSync,ShopifyOrder,ShopifyOrderLine
from app.models import ScheduledTask,ScheduledTaskRun
from app.shopify_connection import ShopifyError,ShopifyAdapter
from app.order_webhook_security import verified,seal,unseal
from app.order_webhook_payload import adapt
from app.order_webhook_gateway import create_gateway
from app import order_webhooks as inbox
from test_shopify_orders import orders_app,setup_app,Adapter,order,line,bag,STORE,ORDER,run,save,client

SECRET='fictional-shopify-signing-secret-000000000'
DOMAIN='fictional.myshopify.com'


def restbag(value):
    return {a:{'amount':v['amount'],'currency_code':v['currencyCode']} for a,v in zip(('shop_money','presentment_money'),value.values())}


def payload():
    raw=order()
    value={'id':1001,'admin_graphql_api_id':ORDER,'name':'#TLC-1001',
        'created_at':raw['createdAt'],'updated_at':raw['updatedAt'],'currency':'GBP','test':True,
        'financial_status':'paid','fulfillment_status':None,'cancelled_at':None,'cancel_reason':None,
        'email':None,'phone':None,'customer':None,'shipping_address':None,'refunds':[],
        'line_items':[], 'shipping_lines':[{'id':1,'title':'Synthetic delivery','code':'TRACKED48',
            'price_set':restbag(bag('2.29')),'discounted_price_set':restbag(bag('2.29'))}]}
    for name,amount in [('total_price','9.29'),('current_total_price','9.29'),('subtotal_price','7.00'),
        ('current_subtotal_price','7.00'),('total_discounts','0'),('current_total_discounts','0'),
        ('total_tax','0'),('current_total_tax','0'),('total_shipping_price','2.29')]:value[name+'_set']=restbag(bag(amount))
    for i,original in enumerate(raw['lineItems']['nodes'],1):
        value['line_items'].append({'id':i,'admin_graphql_api_id':original['id'],'product_id':100,'variant_id':200,
            'title':'Synthetic Anniversary Card','variant_title':None,'sku':'16bit-0001',
            'quantity':1,'current_quantity':1,'fulfillable_quantity':1,'price_set':restbag(bag('3.50')),
            'total_discount':'0.00','discount_allocations':[],
            'properties':[{'name':p['key'],'value':p['value']} for p in original['customAttributes']]})
    return value


def delivery(raw=None,*,topic='orders/updated',event='00000000-0000-0000-0000-000000000001',secret=SECRET):
    body=json.dumps(raw or payload()).encode()
    headers={'X-Shopify-Topic':topic,'X-Shopify-Shop-Domain':DOMAIN,'X-Shopify-API-Version':'2026-10',
        'X-Shopify-Webhook-Id':event,'X-Shopify-Hmac-Sha256':base64.b64encode(hmac.new(secret.encode(),body,hashlib.sha256).digest()).decode()}
    return body,headers


@pytest.fixture
def channel(orders_app):
    app,path=orders_app
    config={'URL':'https://gateway.example.test','TOKEN':'fictional-bearer-token-'+'x'*32,
        'KEY':Fernet.generate_key().decode(),'SECRET':SECRET,'DOMAIN':DOMAIN,'STORE_ID':STORE}
    app.config.update({'SHOPIFY_ORDERS_WEBHOOK_'+k:v for k,v in config.items()})
    db.session.add(ShopifyOrderSync(store_id=STORE,domain=DOMAIN));db.session.commit()
    return app,path,config


def ingest(config,raw=None,**kwargs):
    body,headers=delivery(raw,**kwargs)
    inbox.receive(seal(body,headers,config['KEY']),config)
    return ShopifyOrderEvent.query.order_by(ShopifyOrderEvent.id.desc()).first()


def process(event,config,owner='test-owner'):
    channel=db.session.get(ShopifyOrderDelivery,'orders');channel.owner=owner;channel.lease_until=service.now()+timedelta(seconds=60);db.session.commit()
    inbox.process(event.id,config,owner=owner)
    db.session.expire_all()


def test_complete_event_ellie_freddie_no_api(channel,monkeypatch):
    _,_,config=channel
    monkeypatch.setattr(ShopifyAdapter,'_read',lambda *a,**k:pytest.fail('Webhook must not fetch Shopify'))
    event=ingest(config);assert 'Ellie' not in event.payload and 'Freddie' not in event.payload
    process(event,config)
    row=ShopifyOrder.query.one();assert row.name=='#TLC-1001' and str(row.total)=='9.2900'
    assert row.test and row.financial_status=='PAID' and row.fulfillment_status=='UNFULFILLED'
    lines=ShopifyOrderLine.query.order_by(ShopifyOrderLine.id).all()
    assert [l.sku for l in lines]==['16bit-0001']*2 and lines[0].remote_id!=lines[1].remote_id
    assert [[f['value'] for f in json.loads(l.personalisation_json)['fields']] for l in lines]==[['Ellie',6],['Freddie',5]]
    assert json.loads(row.money_json)['currentShippingPriceSet']['shopMoney']['amount']=='2.29'
    assert db.session.get(ShopifyOrderEvent,event.id).state=='applied' and event.payload is None


def test_duplicate_event_and_restart_durable(channel):
    _,path,config=channel
    event=ingest(config);event_id=event.id
    with sqlite3.connect(path) as c:
        assert c.execute('select count(*) from shopify_order_event').fetchone()[0]==1
        assert 'Ellie' not in c.execute('select payload from shopify_order_event').fetchone()[0]
    db.session.remove();ingest(config);assert ShopifyOrderEvent.query.count()==1
    process(db.session.get(ShopifyOrderEvent,event_id),config)
    ids=[l.id for l in ShopifyOrderLine.query.all()]
    ingest(config);process(db.session.get(ShopifyOrderEvent,event_id),config)
    assert [l.id for l in ShopifyOrderLine.query.all()]==ids


@pytest.mark.parametrize('change',[lambda v:v.pop('line_items'),lambda v:v.update(refunds=[{'id':123}]),
    lambda v:v['line_items'][0].pop('properties'),lambda v:v.update(current_total_discounts_set=restbag(bag('1.00')))])
def test_partial_payload_preserves_complete_order(channel,change):
    _,_,config=channel;first=ingest(config);process(first,config)
    value=payload();value['updated_at']='2026-10-16T11:01:00Z';change(value)
    event=ingest(config,value,event='00000000-0000-0000-0000-000000000002');process(event,config)
    assert event.state=='incomplete' and event.payload
    assert ShopifyOrder.query.one().updated_at==datetime(2026,10,16,10,1)
    assert ShopifyOrderLine.query.count()==2


def test_refund_event_retained_for_explicit_reconciliation(channel):
    _,_,config=channel;event=ingest(config,{'id':8,'order_id':1001,'created_at':'2026-10-16T12:00:00Z'},topic='refunds/create')
    process(event,config);assert event.state=='incomplete' and event.order_gid==ORDER
    assert ShopifyOrder.query.count()==0


def test_out_of_order_and_same_revision_conflict(channel):
    _,_,config=channel;value=payload();value['updated_at']='2026-10-16T12:00:00Z'
    event=ingest(config,value);process(event,config)
    old=ingest(config,event='00000000-0000-0000-0000-000000000002');process(old,config);assert old.state=='stale'
    value['line_items'][0]['properties'][0]['value']='Conflicting'
    conflict=ingest(config,value,event='00000000-0000-0000-0000-000000000003');process(conflict,config)
    assert conflict.state=='incomplete' and 'Ellie' in ShopifyOrderLine.query.first().properties_json


def test_cancellation_complete_event(channel):
    _,_,config=channel;value=payload();value.update(cancelled_at='2026-10-16T12:00:00Z',cancel_reason='customer')
    event=ingest(config,value,topic='orders/cancelled');process(event,config)
    assert ShopifyOrder.query.one().cancel_reason=='CUSTOMER'


@pytest.mark.parametrize('change',[{'X-Shopify-Hmac-Sha256':'wrong'},{'X-Shopify-Shop-Domain':'wrong.myshopify.com'},
    {'X-Shopify-API-Version':'2027-01'},{'X-Shopify-Topic':'products/create'}])
def test_invalid_hmac_or_source_rejected(channel,change):
    _,_,config=channel;body,headers=delivery();headers.update(change)
    with pytest.raises(ValueError):inbox.receive(seal(body,headers,config['KEY']),config)
    assert ShopifyOrderEvent.query.count()==0


def test_delivery_identity_collision(channel):
    _,_,config=channel;ingest(config);value=payload();value['name']='Different'
    with pytest.raises(ValueError,match='collision'):ingest(config,value)


def test_existing_redaction_tombstone_preserved(channel):
    _,_,config=channel;save();row=ShopifyOrder.query.one();service.redact_local_order(row);db.session.commit()
    value=payload();value['updated_at']='2026-10-16T13:00:00Z'
    event=ingest(config,value);process(event,config)
    assert row.redacted_at and not row.personalised and all(l.properties_json=='[]' for l in row.lines)


def test_lease_lost_and_retry_deadletter(channel,monkeypatch):
    _,_,config=channel;event=ingest(config)
    with pytest.raises(RuntimeError):inbox.process(event.id,config,owner='wrong')
    db.session.rollback()
    monkeypatch.setattr(inbox,'persist',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('never log customer details')))
    for _ in range(5):process(event,config)
    assert event.state=='dead_letter' and event.attempts==5 and 'customer details' not in event.error


def test_queue_gateway_auth_dedupe_encrypted_and_ack(tmp_path):
    config={'DATABASE':str(tmp_path/'gateway.db'),'KEY':Fernet.generate_key().decode(),'TOKEN':'x'*40,'SECRET':SECRET,'DOMAIN':DOMAIN}
    gateway=create_gateway(config);c=gateway.test_client();body,headers=delivery()
    assert c.post('/webhooks/shopify/orders',data=body,headers={}).status_code==401
    assert c.post('/webhooks/shopify/orders',data=body,headers=headers).status_code==200
    assert c.post('/webhooks/shopify/orders',data=body,headers=headers).status_code==200
    assert c.get('/events').status_code==401
    auth={'Authorization':'Bearer '+config['TOKEN']};events=c.get('/events',headers=auth).json
    assert len(events)==1 and 'Ellie' not in json.dumps(events)
    assert unseal(events[0]['envelope'],config['KEY'])[0]==body
    assert c.post('/ack',json={'ids':[events[0]['id']]},headers=auth).status_code==200
    assert c.get('/events',headers=auth).json==[]
    assert c.get('/orders/').status_code==404 and c.get('/login').status_code==404


def test_gateway_offline_replay_expiry_marker_and_limits(tmp_path):
    config={'DATABASE':str(tmp_path/'gateway.db'),'KEY':Fernet.generate_key().decode(),'TOKEN':'x'*40,'SECRET':SECRET,'DOMAIN':DOMAIN}
    c=create_gateway(config).test_client();body,headers=delivery();c.post('/webhooks/shopify/orders',data=body,headers=headers)
    auth={'Authorization':'Bearer '+config['TOKEN']}
    c=create_gateway(config).test_client();assert len(c.get('/events',headers=auth).json)==1
    with sqlite3.connect(config['DATABASE']) as conn:conn.execute('update event set received=0')
    marker=c.get('/events',headers=auth).json[0]
    assert marker['envelope'] is None and marker['order_gid']==ORDER
    for _ in range(70):last=c.get('/events',headers=auth)
    assert last.status_code==429


@pytest.mark.parametrize('category',['authentication','access_denied','customer_data_denied','schema','network','rate_limited','invalid_response'])
def test_failed_request_accounting_and_safe_diagnostics(orders_app,category):
    adapter=Adapter();adapter.error=ShopifyError('private customer body/token',category=category)
    result=run(adapter);data=json.loads(result.data_json)
    assert result.state=='failed' and data['counts']['api_requests']==2
    assert data['diagnostic']['category']==category and data['diagnostic']['phase']=='index'
    assert 'private customer' not in result.error and 'private customer' not in result.data_json


def test_preflight_configuration_scope_categories(orders_app,monkeypatch):
    from app import shopify_drafts
    monkeypatch.setattr(shopify_drafts,'configuration',lambda:(_ for _ in ()).throw(ValueError('secret')))
    result=run(Adapter());data=json.loads(result.data_json)
    assert data['diagnostic']['category']=='configuration' and data['counts']['api_requests']==0


@pytest.mark.parametrize('errors,category',[([{'extensions':{'code':'undefinedField'}}],'schema'),
    ([{'message':'This app is not approved to access protected customer data.'}],'customer_data_denied'),
    ([{'extensions':{'code':'ACCESS_DENIED'}}],'access_denied')])
def test_transport_graphql_error_classification(orders_app,monkeypatch,errors,category):
    from test_shopify_foundation import Response,Session
    from app.shopify_drafts import configuration
    session=Session(Response({'errors':errors}))
    monkeypatch.setattr('app.shopify_connection.requests.Session',lambda:session)
    adapter=ShopifyAdapter()
    with pytest.raises(ShopifyError) as result:adapter._read(configuration(),service.queries.INDEX,{})
    assert result.value.category==category and adapter.request_count==1


def test_startup_checkpoint_overlap_no_recurring_poll(orders_app):
    run(Adapter());sync=ShopifyOrderSync.query.one();checkpoint=sync.checkpoint
    id=tasks.enqueue(tasks.ORDERS_KEY,startup=True)
    assert tasks.enqueue(tasks.ORDERS_KEY,startup=True)==id
    assert tasks.claim('startup-owner')==(tasks.ORDERS_KEY,id)
    adapter=Adapter();tasks.run_slice(tasks.ORDERS_KEY,id,'startup-owner',adapter=adapter)
    query=next(args['query'] for name,args in adapter.calls if name==service.queries.INDEX)
    assert (checkpoint-timedelta(hours=2)).isoformat() in query
    assert sync.startup_successful_at and sync.manual_successful_at
    assert tasks.enqueue(tasks.ORDERS_KEY) is None
    with pytest.raises(ValueError):tasks.configure(tasks.ORDERS_KEY,enabled=True,interval=5)
    assert not db.session.get(ScheduledTask,tasks.ORDERS_KEY).enabled


def test_manual_recent_rescan_and_no_duplicate_lines(orders_app):
    run(Adapter());ids=[l.id for l in ShopifyOrderLine.query.all()]
    id=tasks.enqueue(tasks.ORDERS_KEY,manual=True,orders_rescan=True)
    assert tasks.claim('rescan-owner')==(tasks.ORDERS_KEY,id)
    adapter=Adapter();tasks.run_slice(tasks.ORDERS_KEY,id,'rescan-owner',adapter=adapter)
    assert [l.id for l in ShopifyOrderLine.query.all()]==ids
    assert json.loads(db.session.get(ScheduledTaskRun,id).data_json)['rescan_started']


def test_inflight_api_cannot_replace_newer_event(channel):
    _,_,config=channel;before=service.now()
    event=ingest(config);process(event,config)
    header,lines=adapt(payload(),'orders/updated');header['name']='Older inflight name'
    assert not service.persist(STORE,header,lines,observed_before=before)
    db.session.commit();assert ShopifyOrder.query.one().name=='#TLC-1001'


def test_local_ui_and_safe_history_diagnostics(channel):
    app,_,config=channel;event=ingest(config,{'id':1001,'updated_at':'2026-10-16T10:01:00Z'});process(event,config)
    c=client(app);page=c.get('/orders/').data
    assert b'Needs manual reconciliation' in page and b'1 need reconciliation' in page
    assert c.get('/webhooks/shopify/orders').status_code==404
    result=run(Adapter());data=c.get('/settings/tasks/runs/'+result.id,headers={'Accept':'application/json'}).json
    assert 'diagnostic' in data and 'order_results' in data


def test_gateway_to_private_consumer_ack_after_durable_receipt(channel,tmp_path,monkeypatch):
    _,path,config=channel
    c=create_gateway({'DATABASE':str(tmp_path/'events.db'),'KEY':config['KEY'],'TOKEN':config['TOKEN'],
        'SECRET':SECRET,'DOMAIN':DOMAIN}).test_client()
    body,headers=delivery();assert c.post('/webhooks/shopify/orders',data=body,headers=headers).status_code==200
    class Response:
        def __init__(self,response):self.response=response;self.status_code=response.status_code
        def iter_content(self,size):yield self.response.data
        def close(self):pass
    class Session:
        def get(self,url,**kwargs):return Response(c.get('/events',headers=kwargs['headers']))
        def post(self,url,**kwargs):
            with sqlite3.connect(path) as conn:assert conn.execute('select count(*) from shopify_order_event').fetchone()[0]==1
            return Response(c.post('/ack',headers=kwargs['headers'],json=kwargs['json']))
        def close(self):pass
    monkeypatch.setattr(inbox.requests,'Session',Session)
    monkeypatch.setattr(ShopifyAdapter,'_read',lambda *a,**k:pytest.fail('No webhook API reads'))
    inbox.tick('worker-a');assert ShopifyOrder.query.count()==1
    db.session.get(ShopifyOrderDelivery,'orders').next_pull=None;db.session.commit()
    inbox.tick('worker-b');assert ShopifyOrder.query.count()==1 and ShopifyOrderLine.query.count()==2
    assert c.get('/events',headers={'Authorization':'Bearer '+config['TOKEN']}).json==[]


def test_queue_outage_still_processes_durable_inbox_and_retention(channel,monkeypatch):
    _,_,config=channel;event=ingest(config)
    monkeypatch.setattr(inbox,'pull',lambda c:(_ for _ in ()).throw(ValueError('credential must never appear')))
    inbox.tick('worker');assert event.state=='applied' and ShopifyOrder.query.count()==1
    assert 'credential' not in db.session.get(ShopifyOrderDelivery,'orders').error
    old=ingest(config,{'id':1001},event='00000000-0000-0000-0000-000000000002')
    old.received_at=service.now()-timedelta(days=8)
    db.session.get(ShopifyOrderDelivery,'orders').next_pull=None;db.session.commit()
    inbox.tick('worker');assert old.state=='expired' and old.payload is None


def test_channel_lease_prevents_two_workers_pulling(channel,monkeypatch):
    _,_,_=channel;calls=[]
    def pull(config):
        calls.append(1);inbox.tick('second-worker')
    monkeypatch.setattr(inbox,'pull',pull)
    inbox.tick('first-worker');assert calls==[1]


def test_manual_reconciliation_resolves_incomplete_event(channel):
    _,_,config=channel
    event=ingest(config,{'id':1001,'updated_at':'2026-10-16T09:00:00Z'});process(event,config)
    assert event.state=='incomplete'
    assert run(Adapter()).state=='completed'
    db.session.refresh(event);assert event.state=='reconciled' and event.payload is None


def test_backlog_continues_across_startup_slices_without_new_poll(orders_app):
    app,_=orders_app;app.config['SHOPIFY_ORDERS_BATCH_SIZE']=1
    rows=[]
    for number in range(1001,1005):
        raw=order(number)
        for i,line in enumerate(raw['lineItems']['nodes']):line['id']=f'gid://shopify/LineItem/{number*10+i}'
        rows.append(raw)
    adapter=Adapter(rows);run_id=tasks.enqueue(tasks.ORDERS_KEY,startup=True)
    for index in range(4):
        task=db.session.get(ScheduledTask,tasks.ORDERS_KEY)
        if task.resume_at:task.resume_at=tasks.now()-timedelta(seconds=1);db.session.commit()
        assert tasks.claim('startup')==(tasks.ORDERS_KEY,run_id)
        tasks.run_slice(tasks.ORDERS_KEY,run_id,'startup',adapter=adapter)
        sync=ShopifyOrderSync.query.one()
        if index<3:assert sync.checkpoint is None
    assert sync.checkpoint and ShopifyOrder.query.count()==4
    assert db.session.get(ScheduledTaskRun,run_id).state=='completed'
    assert len([q for q,_ in adapter.calls if q==service.queries.INDEX])==1


def test_tick_cannot_enqueue_orders_even_with_legacy_enabled_flag(orders_app,monkeypatch):
    for task in ScheduledTask.query.all():task.enabled=False
    db.session.get(ScheduledTask,tasks.ORDERS_KEY).enabled=True;db.session.commit()
    monkeypatch.setattr(tasks,'run_slice',lambda *a,**k:pytest.fail('No recurring Orders work'))
    assert tasks.tick('worker') is None and ScheduledTaskRun.query.count()==0


def test_incomplete_pagination_has_category_and_preserves_checkpoint(orders_app):
    adapter=Adapter();adapter.index_pages={None:{'nodes':[],'pageInfo':{'hasNextPage':True,'endCursor':'next'}}}
    result=run(adapter)
    assert json.loads(result.data_json)['diagnostic']['category']=='pagination'
    assert ShopifyOrderSync.query.one().checkpoint is None


def test_authentication_http_failure_counted_separately_from_graphql(orders_app,monkeypatch):
    from app import shopify_drafts
    from app.shopify_connection import ShopifyConfiguration
    from test_shopify_foundation import Response,Session
    config=ShopifyConfiguration(DOMAIN,None,client_id='f'*32,client_secret='fictional-secret-'+'k'*32)
    monkeypatch.setattr(shopify_drafts,'configuration',lambda:config)
    session=Session(Response({},status=401))
    monkeypatch.setattr('app.shopify_connection.requests.Session',lambda:session)
    result=run(ShopifyAdapter());data=json.loads(result.data_json)
    assert data['counts']['api_requests']==0 and data['auth_requests']==1
    assert data['diagnostic']['category']=='authentication' and data['diagnostic']['phase']=='preflight'


def test_transport_timeout_is_a_counted_request(orders_app,monkeypatch):
    import requests
    from test_shopify_foundation import Session
    monkeypatch.setattr('app.shopify_connection.requests.Session',lambda:Session(error=requests.Timeout()))
    result=run(ShopifyAdapter());data=json.loads(result.data_json)
    assert data['counts']['api_requests']==1 and data['diagnostic']['category']=='network'


def test_persistence_failure_categorised_without_advancing_checkpoint(orders_app,monkeypatch):
    from sqlalchemy.exc import OperationalError
    monkeypatch.setattr(service,'persist',lambda *a,**k:(_ for _ in ()).throw(OperationalError('private sql',{},Exception())))
    result=run(Adapter());data=json.loads(result.data_json)
    assert data['diagnostic']['category']=='persistence' and data['diagnostic']['phase']=='persist'
    assert data['counts']['api_requests']==4 and ShopifyOrderSync.query.one().checkpoint is None
    assert ShopifyOrder.query.count()==0 and 'private sql' not in result.data_json
