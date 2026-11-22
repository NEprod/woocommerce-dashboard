"""Existing Shopify client with in-memory remote state; no network permitted."""
import copy
from datetime import timedelta
from uuid import uuid4
import pytest
from app import db, order_integrations as integration, order_operation_queries as q, scheduled_tasks as tasks
from app.order_models import ShopifyOrderSync, ShopifyOrder
from app.order_integration_models import OrderWebhookSetting, OrderStatusProjection, OrderIntegrationSettings
from app.order_work_outbound import process
from app.shopify_connection import ShopifyConfiguration
from app.models import ScheduledTask
from test_shopify_orders import orders_app, client, save as import_order, STORE
from test_setup_flow import setup_app


class Remote:
    def __init__(self):self.rows=[];self.writes=[];self.value=None;self.scopes=['read_orders','write_orders','read_fulfillments'];self.fail_readback=False
    def test_connection(self,config):return dict(shop_id=STORE,scopes=self.scopes)
    def _read(self,config,query,variables):
        if query==q.SUBSCRIPTIONS:return {'webhookSubscriptions':dict(nodes=copy.deepcopy(self.rows),pageInfo=dict(hasNextPage=False,endCursor=None))}
        if query==q.SUBSCRIPTION:return {'webhookSubscription':next((r for r in self.rows if r['id']==variables['id']),None)}
        assert query==q.STATUS_READ
        if self.fail_readback and self.writes:raise RuntimeError('Synthetic readback interruption')
        field=None if self.value is None else dict(namespace='tlc_operations',key='production_status',type='single_line_text_field',value=self.value,compareDigest='retained-hash')
        return dict(shop=dict(id=STORE),order=dict(id=variables['id'],metafield=field))
    def _write(self,config,query,variables):
        self.writes.append((query,copy.deepcopy(variables)))
        if query==q.STATUS_SET:
            assert len(variables['metafields'])==1
            self.value=variables['metafields'][0]['value'];return {'metafieldsSet':dict(userErrors=[])}
        if query==q.SUB_DELETE:
            self.rows=[r for r in self.rows if r['id']!=variables['id']]
            return {'webhookSubscriptionDelete':dict(userErrors=[],deletedWebhookSubscriptionId=variables['id'])}
        assert query in {q.SUB_CREATE,q.SUB_UPDATE}
        row=dict(id=variables.get('id','gid://shopify/WebhookSubscription/100'),topic=variables.get('topic','ORDERS_UPDATED'),uri=variables['input']['uri'],apiVersion={'handle':'2026-10','supported':True})
        self.rows=[r for r in self.rows if r['id']!=row['id']]+[row]
        return {('webhookSubscriptionCreate' if query==q.SUB_CREATE else 'webhookSubscriptionUpdate'):dict(userErrors=[],webhookSubscription=row)}


@pytest.fixture
def connected(orders_app,monkeypatch):
    config=ShopifyConfiguration('fictional.myshopify.com','fictional_access_token_for_tests')
    monkeypatch.setattr(integration,'configuration',lambda:config)
    monkeypatch.setattr('app.order_work_outbound.configuration',lambda:config)
    monkeypatch.setattr(integration,'shopify_settings',lambda:dict(domain=config.domain,client_secret='not-displayed-test-secret'))
    db.session.add(ShopifyOrderSync(store_id=STORE,domain=config.domain));db.session.commit()
    return orders_app,Remote()


def row():return integration.save(dict(topic='orders/updated',endpoint='https://receiver.example/webhooks/shopify/orders',enabled='yes',name='Order updates'))


def test_settings_table_cancel_and_local_save_never_calls_shopify(connected):
    (app,_),remote=connected;c=client(app)
    assert c.get('/settings/shopify-webhooks').status_code==200
    assert OrderWebhookSetting.query.count()==0
    r=c.post('/settings/shopify-webhooks/save',data=dict(topic='orders/updated',endpoint='https://receiver.example/webhooks/shopify/orders',enabled='yes',name='Updates'))
    assert r.status_code==303 and OrderWebhookSetting.query.count()==1 and not remote.writes
    saved=OrderWebhookSetting.query.one();version=saved.version
    assert c.get('/settings/shopify-webhooks').status_code==200
    assert saved.version==version
    bad=c.post('/settings/shopify-webhooks/save',data=dict(id=saved.id,version=version,topic='orders/updated',endpoint='https://private.ts.net/webhooks/shopify/orders'))
    assert bad.status_code==400 and saved.endpoint=='https://receiver.example/webhooks/shopify/orders'
    assert c.post(f'/settings/shopify-webhooks/{saved.id}/verify',data={'version':'invalid'}).status_code==400


def test_subscription_register_verify_dedup_update_remove(connected):
    _,remote=connected;r=row();db.session.commit()
    integration.subscription_action(r,'register',adapter=remote);db.session.commit()
    assert r.state=='registered' and len(remote.writes)==1
    integration.subscription_action(r,'register',adapter=remote);db.session.commit()
    assert len(remote.writes)==1
    r.endpoint='https://other.example/webhooks/shopify/orders'
    integration.subscription_action(r,'register',adapter=remote);db.session.commit()
    assert remote.rows[0]['uri']==r.endpoint
    integration.subscription_action(r,'verify',adapter=remote)
    integration.subscription_action(r,'remove',adapter=remote);db.session.commit()
    assert r.state=='removed' and not remote.rows


def test_subscription_blocks_wrong_delivery_version_and_missing_scope(connected):
    _,remote=connected;r=row();db.session.commit()
    remote.rows=[dict(id='gid://shopify/WebhookSubscription/1',topic='ORDERS_UPDATED',uri=r.endpoint,apiVersion={'handle':'2026-07'})]
    with pytest.raises(ValueError,match='delivery version'):integration.subscription_action(r,'verify',adapter=remote)
    r.topic='fulfillments/create';remote.scopes=['read_orders']
    with pytest.raises(ValueError,match='read_fulfillments'):integration.subscription_action(r,'register',adapter=remote)
    assert not remote.writes


def test_projection_disabled_scope_failure_readback_and_retry(connected):
    _,remote=connected;import_order();order=ShopifyOrder.query.one()
    assert process(order.id,adapter=remote) is None and not remote.writes
    integration.enable_projection(True,actor='test-admin',adapter=remote);db.session.commit()
    remote.scopes=['read_orders']
    assert process(order.id,adapter=remote).state=='permission_required' and not remote.writes
    saved=db.session.get(OrderStatusProjection,order.id);saved.next_attempt=None;db.session.commit()
    remote.scopes=['read_orders','write_orders'];remote.fail_readback=True
    assert process(order.id,adapter=remote).state=='uncertain'
    assert len(remote.writes)==1
    saved.next_attempt=None;db.session.commit();remote.fail_readback=False
    assert process(order.id,adapter=remote).state=='verified'
    assert len(remote.writes)==1  # uncertain write read first, never blindly repeated
    data=remote.writes[0][1]['metafields'][0]
    assert data['namespace']=='tlc_operations' and data['key']=='production_status'
    assert order.fulfillment_status=='UNFULFILLED'


def test_fifteen_minute_task_enable_disable_run_now_and_overlap(connected):
    task=db.session.get(ScheduledTask,tasks.ORDERS_KEY)
    assert task.enabled and task.interval_minutes==15
    tasks.configure(tasks.ORDERS_KEY,enabled=False,interval=15)
    assert tasks.enqueue(tasks.ORDERS_KEY) is None
    first=tasks.enqueue(tasks.ORDERS_KEY,manual=True)
    assert first and tasks.enqueue(tasks.ORDERS_KEY,manual=True)==first
    assert tasks.enqueue(tasks.ORDERS_KEY,webhook=True)==first


def test_remote_removal_recovers_lost_success_without_deleting_another_subscription(connected):
    _,remote=connected;r=row();db.session.commit()
    integration.subscription_action(r,'register',adapter=remote);db.session.commit()
    saved=r.remote_json;remote.rows=[];remote.writes=[]  # successful deletion response was lost
    integration.subscription_action(r,'remove',adapter=remote);db.session.commit()
    assert r.state=='removed' and r.remote_id is None and r.remote_json==saved and not remote.writes


def test_projection_credential_change_is_visible_and_never_writes(connected,monkeypatch):
    _,remote=connected;import_order();order=ShopifyOrder.query.one()
    integration.enable_projection(True,actor='test-admin',adapter=remote);db.session.commit()
    monkeypatch.setattr('app.order_work_outbound.configuration',lambda:ShopifyConfiguration('fictional.myshopify.com','changed_test_token_000000000000'))
    assert process(order.id,adapter=remote).state=='reauthorisation_required'
    assert not remote.writes


def test_missing_projection_configuration_is_visible_without_secret_details(connected,monkeypatch):
    _,remote=connected;import_order();order=ShopifyOrder.query.one()
    integration.enable_projection(True,actor='test-admin',adapter=remote);db.session.commit()
    def unavailable():raise ValueError('sensitive-test-credential-is-not-a-diagnostic')
    monkeypatch.setattr('app.order_work_outbound.configuration',unavailable)
    result=process(order.id,adapter=remote)
    assert result.state=='configuration_required' and 'sensitive-test' not in result.error and not remote.writes


def test_management_routes_require_admin_and_dashboard_has_no_public_receiver(connected):
    (app,_),_=connected
    from app.models import User
    db.session.add(User(id=2,email='viewer@example.test',username='viewer',password='unused',is_admin=False));db.session.commit()
    c=client(app)
    with c.session_transaction() as session:session['_user_id']='2'
    assert c.get('/settings/shopify-webhooks').status_code==403
    assert c.get('/production/orders/').status_code==403
    assert c.post('/webhooks/shopify/orders',data=b'{}').status_code==404


def test_managed_read_only_orders_run_without_enabling_publishing(connected,monkeypatch):
    from types import SimpleNamespace
    (app,_),_=connected
    app.config['TLC_DEPLOYMENT_PROOF']=(SimpleNamespace(automation=False,writes=False),None,None)
    monkeypatch.setenv('TLC_SCHEDULER_READY_AT','0')
    monkeypatch.setattr('app.order_webhooks.tick',lambda owner:None)
    observed=[];monkeypatch.setattr(tasks,'run_slice',lambda key,run_id,owner,adapter=None:observed.append(key))
    for key in [tasks.ORDERS_KEY,tasks.KEY]:
        task=db.session.get(ScheduledTask,key);task.enabled=True;task.next_due=tasks.now()-timedelta(minutes=1)
    db.session.commit()
    tasks.enqueue(tasks.KEY)
    result=tasks.tick('readonly-orders-owner')
    assert result[0]==tasks.ORDERS_KEY and observed==[tasks.ORDERS_KEY]


def test_existing_persistent_database_survives_application_restart(connected):
    (app,path),remote=connected;r=row();db.session.commit();identity=r.id
    integration.enable_projection(True,actor='test-admin',adapter=remote);db.session.commit()
    tasks.configure(tasks.ORDERS_KEY,enabled=False,interval=30)
    import_order()
    from app import create_app
    from app.order_work_models import WorkEvent, OrderWorkLine
    before=WorkEvent.query.count(),OrderWorkLine.query.count()
    restarted=create_app(recover=False)
    with restarted.app_context():
        assert db.engine.url.database==str(path)
        assert db.session.get(OrderWebhookSetting,identity).endpoint=='https://receiver.example/webhooks/shopify/orders'
        assert db.session.get(OrderIntegrationSettings,STORE).projection_enabled
        assert (WorkEvent.query.count(),OrderWorkLine.query.count())==before
        task=db.session.get(ScheduledTask,tasks.ORDERS_KEY)
        assert not task.enabled and task.interval_minutes==30  # migration never resets operator settings on restart


def test_pending_webhook_operation_blocks_edits_and_invalid_topic_is_rejected(connected):
    r=row();r.operation_until=tasks.now()+timedelta(minutes=5);db.session.commit()
    with pytest.raises(ValueError,match='operation is pending'):
        integration.save(dict(id=r.id,version=r.version,name='Changed',topic=r.topic,endpoint=r.endpoint))
    db.session.rollback()
    with pytest.raises(ValueError,match='supported pinned'):
        integration.save(dict(topic='arbitrary/topic',endpoint=r.endpoint))


@pytest.mark.parametrize('address',['https://localhost/webhooks/shopify/orders','https://100.100.100.100/webhooks/shopify/orders','http://receiver.example/webhooks/shopify/orders','https://user:password@receiver.example/webhooks/shopify/orders','https://receiver.example/private?secret=unsafe'])
def test_receiver_destination_rejects_private_or_credential_bearing_urls(address):
    with pytest.raises(ValueError):integration.public_endpoint(address)
