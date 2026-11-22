"""Store-bound webhook settings and explicit subscription authority, no new app."""
import ipaddress
import json
from urllib.parse import urlsplit
from app import db
from app.commerce_config import shopify_settings
from app.order_models import ShopifyOrderSync
from app.order_integration_models import OrderWebhookSetting, OrderIntegrationSettings
from app.order_webhook_security import TOPICS
from app.shopify_connection import ShopifyAdapter, ShopifyConfiguration, ShopifyError, _gid
from app.inventory import service as stock
from app.order_work import now
from app import order_operation_queries as q

ENUMS={topic:topic.replace('/','_').upper() for topic in TOPICS}


def configuration():return ShopifyConfiguration.from_settings(shopify_settings()).validated()


def connected_store():
    # A saved authenticated Orders association, never a browser-supplied shop ID.
    domain=shopify_settings().get('domain','').strip().lower()
    return ShopifyOrderSync.query.filter_by(domain=domain).first() if domain else None


def public_endpoint(value):
    value=str(value or '').strip();parsed=urlsplit(value)
    host=(parsed.hostname or '').lower()
    if (parsed.scheme!='https' or not host or parsed.username or parsed.password or parsed.query or parsed.fragment
        or parsed.port not in (None,443) or not parsed.path.endswith('/webhooks/shopify/orders')
        or host in {'localhost','localhost.localdomain'} or host.endswith(('.local','.internal','.ts.net','.tailscale.net'))
        or len(value)>500):
        raise ValueError('Use a dedicated PUBLIC HTTPS receiver ending /webhooks/shopify/orders; a private Tailscale dashboard is not reachable by Shopify.')
    try:
        if not ipaddress.ip_address(host).is_global:raise ValueError('Receiver must use a public address.')
    except ValueError as exc:
        if 'Receiver' in str(exc):raise
        if '.' not in host:raise ValueError('Use a public HTTPS hostname.')
    return value


def save(data):
    store=connected_store()
    if not store:raise ValueError('Run authenticated Orders Sync Now first to establish the existing shop identity.')
    topic=data.get('topic')
    if topic not in TOPICS:raise ValueError('Choose a supported pinned Shopify order event topic.')
    endpoint=public_endpoint(data.get('endpoint'))
    name=stock.clean(data.get('name') or topic,'Webhook name',120,required=True)
    stock.writer_lock()
    row=db.session.get(OrderWebhookSetting,int(data['id'])) if data.get('id') else None
    if data.get('id') and (not row or row.store_id!=store.store_id):raise ValueError('This webhook belongs to another shop or no longer exists.')
    if row and row.version!=int(data.get('version','0')):raise ValueError('Webhook settings changed. Reload before saving.')
    if row and row.operation_until and row.operation_until>now():raise ValueError('A remote operation is pending. Verify its outcome before editing.')
    if row and row.remote_id and row.topic!=topic:raise ValueError('Registered topics are immutable. Explicitly remove the exact subscription before choosing a different topic.')
    duplicate=OrderWebhookSetting.query.filter_by(store_id=store.store_id,topic=topic,endpoint=endpoint).first()
    if duplicate and (not row or duplicate.id!=row.id):raise ValueError('This store/topic/endpoint is already configured.')
    if row is None:
        row=OrderWebhookSetting(store_id=store.store_id,topic=topic,name=name,endpoint=endpoint,enabled=data.get('enabled')=='yes',version=1,state='local_only',error='',remote_json='{}');db.session.add(row)
    else:
        if row.endpoint!=endpoint:row.state='configuration_changed'
        row.endpoint=endpoint;row.name=name;row.topic=topic;row.enabled=data.get('enabled')=='yes';row.version+=1
    db.session.flush();return row


def authorised(row,adapter=None):
    adapter=adapter or ShopifyAdapter();config=configuration();shop=adapter.test_connection(config)
    if shop.get('shop_id')!=row.store_id:raise ValueError('The authenticated app is connected to a different Shopify store.')
    if not ({'read_orders','write_orders'}&set(shop.get('scopes',[]))):raise ValueError('Approve read_orders for this existing app first. Scopes are not expanded automatically.')
    if row.topic.startswith('fulfillments/') and not {'read_fulfillments','read_marketplace_orders'}&set(shop.get('scopes',[])):
        raise ValueError('This fulfilment topic requires read_fulfillments or read_marketplace_orders on the existing app.')
    if config.api_version!='2026-10':raise ValueError('This webhook configuration is pinned to 2026-10; review the existing app API/webhook version.')
    return adapter,config,shop


def remote_list(adapter,config):
    rows=[];cursor=None
    for _ in range(20):
        page=adapter._read(config,q.SUBSCRIPTIONS,{'after':cursor})['webhookSubscriptions']
        rows.extend(page['nodes'])
        if not page['pageInfo']['hasNextPage']:return rows
        next_cursor=page['pageInfo']['endCursor']
        if not next_cursor or next_cursor==cursor:break
        cursor=next_cursor
    raise ValueError('Subscription list was incomplete; no mutation attempted.')


def subscription_action(row,action,*,adapter=None):
    adapter,config,_=authorised(row,adapter)
    expected=dict(topic=ENUMS[row.topic],uri=row.endpoint)
    rows=remote_list(adapter,config)
    matches=[r for r in rows if all(r.get(k)==v for k,v in expected.items())]
    existing=next((r for r in rows if r['id']==row.remote_id),None) if row.remote_id else None
    if action=='verify':
        if row.remote_id and existing is None:row.state='remote_missing';row.error='The retained subscription was not present in this complete app-scoped list.';return row
        if len(matches)==1:
            if matches[0].get('apiVersion',{}).get('handle')!='2026-10':raise ValueError('Existing app webhook delivery version must be 2026-10; registration is not verified.')
            if row.remote_id and row.remote_id!=matches[0]['id']:raise ValueError('Subscription identity differs; review before adopting another subscription.')
            row.remote_id=matches[0]['id'];row.remote_json=json.dumps(matches[0]);row.state='registered';row.verified_at=now();row.error=''
        else:row.state='not_registered' if not matches else 'duplicate_remote';row.error='No unique matching subscription verified.'
        return row
    if action=='remove':
        if not row.remote_id:raise ValueError('No retained remote subscription to remove; local settings are not a registration.')
        old=json.loads(row.remote_json)
        if existing is None:
            # A prior deletion may have succeeded before its response was lost.
            # Complete app-scoped absence is safe to acknowledge, never a reason
            # to delete another subscription. Keep the retained identity evidence.
            row.remote_id=None;row.state='removed';row.enabled=False;row.error='';row.verified_at=now();return row
        if not existing or any(existing.get(k)!=old.get(k) for k in ('id','topic','uri')):
            raise ValueError('Remote identity/destination changed; removal is blocked to protect other subscriptions.')
        result=adapter._write(config,q.SUB_DELETE,{'id':row.remote_id})['webhookSubscriptionDelete']
        if result['userErrors'] or result.get('deletedWebhookSubscriptionId')!=row.remote_id:raise ShopifyError('Subscription removal was not verified.')
        if any(r['id']==row.remote_id for r in remote_list(adapter,config)):raise ShopifyError('Subscription still present after removal.')
        row.remote_id=None;row.state='removed';row.enabled=False;row.error='';row.verified_at=now();return row
    if action!='register':raise ValueError('Unknown subscription action.')
    if len(matches)>1:raise ValueError('Duplicate matching remote subscriptions require manual review; no new subscription created.')
    if row.remote_id and existing is None:raise ValueError('Retained remote subscription is missing; verify/remove stale configuration explicitly before replacing it.')
    if matches:
        if row.remote_id and matches[0]['id']!=row.remote_id:raise ValueError('Another matching subscription exists; no duplicate update was attempted.')
        desired=matches[0]
    else:
        if existing and any(existing.get(k)!=json.loads(row.remote_json).get(k) for k in ('id','topic','uri')):
            raise ValueError('Remote subscription changed outside this reviewed configuration. Verify before updating.')
        query,variables,result_key=(q.SUB_UPDATE,dict(id=row.remote_id,input=dict(uri=row.endpoint,format='JSON')),'webhookSubscriptionUpdate') if row.remote_id else (
            q.SUB_CREATE,dict(topic=ENUMS[row.topic],input=dict(uri=row.endpoint,format='JSON')),'webhookSubscriptionCreate')
        result=adapter._write(config,query,variables)[result_key]
        if result['userErrors'] or not result.get('webhookSubscription'):raise ShopifyError('Shopify rejected the webhook configuration. Check approved topic scopes and app-managed subscription rules.')
        desired=result['webhookSubscription']
    identity=_gid(desired.get('id'),'WebhookSubscription')
    read=adapter._read(config,q.SUBSCRIPTION,{'id':identity}).get('webhookSubscription')
    if not read or read['id']!=identity or any(read.get(k)!=v for k,v in expected.items()):raise ShopifyError('Webhook registration readback did not verify.')
    if read.get('apiVersion',{}).get('handle')!='2026-10':raise ValueError('Subscription exists but its delivery version is not 2026-10. Correct the existing app configuration and verify again.')
    row.remote_id=identity;row.remote_json=json.dumps(read);row.state='registered';row.verified_at=now();row.error='';return row


def enable_projection(enabled,*,actor,adapter=None):
    store=connected_store()
    if not store:raise ValueError('Run Orders Sync Now first.')
    config=configuration()
    if enabled:
        adapter=adapter or ShopifyAdapter();shop=adapter.test_connection(config)
        if shop.get('shop_id')!=store.store_id or not {'read_orders','write_orders'}<=set(shop.get('scopes',[])):
            raise ValueError('Verified current store with read_orders AND write_orders is required. Approve existing app permissions; no scopes were changed.')
    stock.writer_lock();row=db.session.get(OrderIntegrationSettings,store.store_id)
    if row is None:row=OrderIntegrationSettings(store_id=store.store_id);db.session.add(row)
    row.projection_enabled=enabled;row.fingerprint=config.fingerprint();row.actor=str(actor)[:160];row.authorised_at=now()
    db.session.flush();return row
