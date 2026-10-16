"""Bounded read-only Orders import through the existing Shopify transport and task lease.

Only complete, revision-consistent order snapshots commit. Queue/checkpoint commits
contain IDs/timestamps only. Commerce rows never own future production workflow data.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from flask import current_app
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from app import db
from app.models import ShopifyOrder, ShopifyOrderLine, ShopifyOrderSync, ScheduledTask
from app.shopify_connection import ShopifyAdapter, ShopifyError, _gid, _cursor
from app import order_queries as queries
from app.order_personalisation import parse

KEY = 'shopify_orders'
DEFINITION = {'name':'Shopify Orders Reconciliation',
              'description':'Read-only recent and updated orders; commerce and historical line-item personalisations only.'}
AUTH_ERROR = 'Shopify Orders requires authorisation: release and approve read_orders for the existing app, then use Sync Now.'
SAFE_ERROR = 'Orders sync could not obtain complete Shopify evidence. Previously imported orders remain available. Check connection, protected customer data access and task history, then retry Sync Now.'


class OrdersPermissionRequired(ShopifyError):
    pass


class OrdersImportLimit(ShopifyError):
    """Only fixed, non-PII policy explanations created by this importer."""


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def stamp(value):
    if not isinstance(value,str): raise ShopifyError('Order timestamp evidence is incomplete.')
    try:
        result = datetime.fromisoformat(value.replace('Z','+00:00'))
        if result.tzinfo is None: raise ValueError()
        return result.astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        raise ShopifyError('Order timestamp evidence is invalid.') from None


def dumps(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)


def connection(value, after=None, *, maximum=50):
    if (not isinstance(value,dict) or not isinstance(value.get('nodes'),list)
            or len(value['nodes'])>maximum or not isinstance(value.get('pageInfo'),dict)
            or type(value['pageInfo'].get('hasNextPage')) is not bool):
        raise ShopifyError('Order connection evidence is incomplete; no existing data was removed.')
    page = value['pageInfo']
    if page['hasNextPage'] and (not page.get('endCursor') or page['endCursor']==after):
        raise ShopifyError('Order pagination did not advance; saved progress remains resumable.')
    if page.get('endCursor') is not None: _cursor(page['endCursor'])
    return value['nodes'],page


def amount_bag(value):
    if not isinstance(value,dict): raise ShopifyError('Order monetary evidence is incomplete.')
    result = {}
    for key in ('shopMoney','presentmentMoney'):
        money = value.get(key)
        try:
            if not isinstance(money,dict) or not re.fullmatch(r'[A-Z]{3}',money['currencyCode']): raise ValueError()
            if not isinstance(money['amount'],str): raise ValueError()
            amount = Decimal(money['amount'])
            if not amount.is_finite() or abs(amount)>=Decimal('100000000000000') or amount.as_tuple().exponent < -4: raise ValueError()
        except (ValueError,TypeError,KeyError,InvalidOperation):
            raise ShopifyError('Order monetary evidence is invalid.') from None
        result[key] = {'amount':str(amount),'currencyCode':money['currencyCode']}
    return result


def text(value,limit,*,optional=False):
    if optional and value is None: return None
    if not isinstance(value,str) or len(value)>limit:
        raise ShopifyError('Order text evidence is incomplete or exceeds its safe bound.')
    return value


def quantity(value):
    if type(value) is not int or not 0<=value<=10000000:
        raise ShopifyError('Order quantity evidence is invalid.')
    return value


def require_keys(value, keys):
    if not isinstance(value,dict) or not set(keys)<=value.keys():
        raise ShopifyError('Order response omitted requested fields; retained snapshot is unchanged.')


def normalise(raw, *, api_version):
    """Explicit narrow allowlist, no payment instruments, IPs, browser data or full raw orders."""
    if not isinstance(raw,dict): raise ShopifyError('Order is unavailable; retained record is unchanged.')
    require_keys(raw,('id','name','createdAt','updatedAt','test','currencyCode',
        'displayFinancialStatus','displayFulfillmentStatus','cancelledAt','cancelReason',
        'email','phone','customer','shippingAddress','shippingLines','_lines',*queries.ORDER_MONEY))
    remote_id = _gid(raw.get('id'),'Order')
    nullable_money={'subtotalPriceSet','totalDiscountsSet','totalTaxSet'}
    money = {k:None if raw.get(k) is None and k in nullable_money else amount_bag(raw.get(k)) for k in queries.ORDER_MONEY}
    currency = text(raw.get('currencyCode'),3)
    if money['currentTotalPriceSet']['shopMoney']['currencyCode']!=currency:
        raise ShopifyError('Order currency evidence is inconsistent.')
    if type(raw.get('test')) is not bool: raise ShopifyError('Order test flag is missing.')
    shipping, page = connection(raw.get('shippingLines'),maximum=10)
    if page['hasNextPage']: raise OrdersImportLimit('More than 10 shipping services: order retained unchanged; a reviewed importer extension is required.')
    shipping = [{'id':_gid(s.get('id'),'ShippingLine'),'title':text(s.get('title'),1024),
                 'code':text(s.get('code'),255,optional=True),
                 **{k:amount_bag(s.get(k)) for k in ('originalPriceSet','discountedPriceSet')}} for s in shipping]
    address = raw.get('shippingAddress')
    if address is not None:
        if not isinstance(address,dict): raise ShopifyError('Order delivery evidence is invalid.')
        address = {k:text(address[k],1024,optional=True) for k in (
            'name','company','address1','address2','city','province','provinceCode','country','countryCodeV2','zip','phone') if k in address}
    customer = raw.get('customer')
    if customer is not None and not isinstance(customer,dict): raise ShopifyError('Order customer reference is invalid.')
    if customer is not None: require_keys(customer,('id','displayName'))
    rows = []
    for position, line in enumerate(raw['_lines']):
        require_keys(line,('id','title','variantTitle','sku','quantity','currentQuantity',
            'unfulfilledQuantity','refundableQuantity','product','variant','customAttributes',*queries.LINE_MONEY))
        product = _gid(line['product']['id'],'Product') if line.get('product') else None
        variant = _gid(line['variant']['id'],'ProductVariant') if line.get('variant') else None
        properties = line.get('customAttributes')
        if not isinstance(properties,list) or len(properties)>128:
            raise ShopifyError('Order properties evidence is incomplete or exceeds its bound.')
        properties = [{'key':text(p.get('key'),1024),'value':text(p.get('value'),131072)} for p in properties]
        if len(dumps(properties).encode())>262144: raise ShopifyError('Order properties exceed their safe storage bound.')
        personalisation = parse(properties,product)
        rows.append({'remote_id':_gid(line.get('id'),'LineItem'),'product_id':product,'variant_id':variant,
            'sku':text(line.get('sku'),255,optional=True),'title':text(line.get('title'),1024),
            'variant_title':text(line.get('variantTitle'),1024,optional=True),'position':position,
            **{target:quantity(line.get(source)) for source,target in (
                ('quantity','quantity'),('currentQuantity','current_quantity'),('unfulfilledQuantity','unfulfilled_quantity'),('refundableQuantity','refundable_quantity'))},
            'is_current':True,'money_json':dumps({k:amount_bag(line.get(k)) for k in queries.LINE_MONEY}),
            'properties_json':dumps(properties),'personalisation_json':dumps(personalisation),
            'personalised':personalisation['has_personalisation']})
    if len({l['remote_id'] for l in rows}) != len(rows): raise ShopifyError('Duplicate line identity in order readback.')
    return {'remote_id':remote_id,'name':text(raw.get('name'),100),
        'created_at':stamp(raw.get('createdAt')),'updated_at':stamp(raw.get('updatedAt')),
        'api_version':api_version,'customer_id':_gid(customer['id'],'Customer') if customer else None,
        'customer_name':text(customer.get('displayName'),255,optional=True) if customer else (address or {}).get('name'),
        'email':text(raw.get('email'),320,optional=True),'phone':text(raw.get('phone'),100,optional=True),
        'delivery_json':dumps(address),'financial_status':text(raw.get('displayFinancialStatus') or 'UNKNOWN',40),
        'fulfillment_status':text(raw.get('displayFulfillmentStatus'),40),
        'cancelled_at':stamp(raw['cancelledAt']) if raw.get('cancelledAt') else None,
        'cancel_reason':text(raw.get('cancelReason'),100,optional=True),'test':raw['test'],
        'currency':currency,'total':Decimal(money['currentTotalPriceSet']['shopMoney']['amount']),
        'money_json':dumps(money),'shipping_json':dumps(shipping),
        'quantity':sum(l['current_quantity'] for l in rows),'outstanding_quantity':sum(l['unfulfilled_quantity'] for l in rows),
        'personalised':any(l['personalised'] for l in rows)},rows


def read_order(adapter, config, remote_id):
    """A targeted interface also suitable for future authenticated webhook hints."""
    _gid(remote_id,'Order')
    after, version, raw, lines = None,None,None,[]
    for _ in range(40):
        page = adapter._read(config,queries.DETAIL,{'id':remote_id,'after':after}).get('order')
        if not isinstance(page,dict) or page.get('id')!=remote_id:
            raise ShopifyError('Order is missing or inaccessible. Retained data is unchanged; absence is not deletion.')
        if version is not None and page.get('updatedAt')!=version:
            raise ShopifyError('Order changed during pagination; retry read-only import on the next run.')
        version = page.get('updatedAt'); stamp(version)
        raw = raw or page
        nodes, info = connection(page.get('lineItems'),after,maximum=25)
        lines.extend(nodes)
        if not info['hasNextPage']: break
        after = info['endCursor']
    else: raise OrdersImportLimit('Order exceeds the bounded 1000-line read; retained snapshot is unchanged. A reviewed importer extension is required.')
    final = adapter._read(config,queries.REVISION,{'id':remote_id}).get('order')
    if not isinstance(final,dict) or final.get('id')!=remote_id or final.get('updatedAt')!=version:
        raise ShopifyError('Order revision changed during readback; retry read-only import.')
    raw = {**raw,'_lines':lines}
    return normalise(raw,api_version=config.api_version)


def persist(store_id, order, lines, *, fence=lambda:None):
    """Single atomic order graph; SQLite writer fence before reading the current revision."""
    _gid(store_id,'Shop')
    fence()
    # A conditional UPSERT also fences independent targeted imports. Never let an
    # older in-flight response replace newer commerce or customer snapshot data.
    values = {**order,'store_id':store_id,'synced_at':now()}
    table = ShopifyOrder.__table__
    existing = db.session.execute(select(table.c.redacted_at).where(
        table.c.store_id==store_id,table.c.remote_id==order['remote_id'])).first()
    if existing and existing.redacted_at:
        for k in ('customer_id','customer_name','email','phone'): values[k]=None
        values['delivery_json']='null'; values['personalised']=False
        lines = [{**l,'properties_json':'[]','personalisation_json':dumps({'fields':[],'public':[],'warnings':['Local personal data was redacted.']}),'personalised':False} for l in lines]
    statement = insert(table).values(**values).on_conflict_do_update(
        index_elements=['store_id','remote_id'],set_={k:v for k,v in values.items() if k not in {'store_id','remote_id'}},
        where=table.c.updated_at<=order['updated_at'])
    if db.session.execute(statement).rowcount!=1:
        return False
    row = db.session.execute(select(table.c.id).where(table.c.store_id==store_id,table.c.remote_id==order['remote_id'])).scalar_one()
    # Retain historical removed lines, but only a complete read can mark them absent.
    db.session.query(ShopifyOrderLine).filter_by(order_id=row).update({'is_current':False})
    lt = ShopifyOrderLine.__table__
    owners=db.session.execute(select(lt.c.order_id,lt.c.remote_id).where(
        lt.c.remote_id.in_([line['remote_id'] for line in lines]))).all() if lines else []
    if any(previous.order_id!=row for previous in owners):
        raise ShopifyError('Line identity already belongs to another order; import rejected.')
    for line in lines:
        db.session.execute(insert(lt).values(order_id=row,**line).on_conflict_do_update(
            index_elements=['order_id','remote_id'],set_=line))
    return True


def setting(key, default, low, high):
    value = current_app.config.get(key,default)
    if type(value) is not int or not low<=value<=high: raise ShopifyError('Orders import limits are invalid; review deployment configuration.')
    return value


def save_receipt(run, data, sync):
    """Persist non-PII progress in the same transaction as its imported order/queue."""
    from app.scheduled_tasks import encoded
    data['pending_orders']=len(json.loads(sync.pending_json or '[]'))
    data['more_pages']=bool(sync.page_more)
    run.data_json=encoded(data)


def run_slice(run_id, owner, *, adapter=None):
    from app import scheduled_tasks as tasks, shopify_drafts as drafts
    from app.models import ScheduledTaskRun
    from app.shopify_capacity import background_work, BackgroundYield
    adapter = adapter or ShopifyAdapter()
    run = db.session.get(ScheduledTaskRun,run_id)
    data = json.loads(run.data_json)
    sync = None
    fence = lambda:tasks.fence(KEY,run_id,owner)
    try:
        if not db.session.get(ScheduledTask,KEY).enabled and run.trigger=='scheduled':
            data['reason']='Orders schedule disabled; saved import cursor is retained.'
            return tasks.save_progress(KEY,run_id,owner,data,terminal='partially_completed')
        with background_work(lambda:tasks.renew(KEY,run_id,owner),fence=fence,requests=70,seconds=80) as budget:
            config = drafts.configuration()
            shop = adapter.test_connection(config)
            store_id = _gid(shop.get('shop_id'),'Shop')
            # Pin a continuing run to the same authenticated store, never reuse a
            # cursor if deployment credentials switch stores mid-import.
            if data.get('store_id') not in (None,store_id): raise ShopifyError('Orders run belongs to a different store.')
            data['store_id']=store_id
            run.data_json=tasks.encoded(data)
            sync = db.session.get(ShopifyOrderSync,store_id)
            if sync is None:
                sync=ShopifyOrderSync(store_id=store_id,domain=config.domain); db.session.add(sync)
            sync.domain=config.domain
            sync.attempted_at=now()
            if not ({'read_orders','write_orders'} & set(shop.get('scopes',[]))):
                sync.state='authorisation_required';sync.error=AUTH_ERROR
                save_receipt(run,data,sync);fence();db.session.commit()
                raise OrdersPermissionRequired(AUTH_ERROR)
            if sync.window_end is None:
                lookback = setting('SHOPIFY_ORDERS_INITIAL_DAYS',30,1,60)
                sync.window_end = now()-timedelta(seconds=30)
                sync.window_start = sync.checkpoint-timedelta(hours=2) if sync.checkpoint else sync.window_end-timedelta(days=lookback)
                # read_orders alone is limited to recent orders; don't send an
                # inaccessible lower bound after a long application outage.
                sync.window_start=max(sync.window_start,sync.window_end-timedelta(days=60))
                sync.cursor=None;sync.pending_json='[]';sync.page_cursor=None;sync.page_more=False
            sync.state='running';sync.error=None;fence();db.session.commit()
            pending=json.loads(sync.pending_json)
            if not pending:
                query=f"updated_at:>='{sync.window_start.isoformat()}Z' updated_at:<='{sync.window_end.isoformat()}Z'"
                result=adapter._read(config,queries.INDEX,{'after':sync.cursor,'query':query,'first':10})
                nodes,page=connection(result.get('orders'),sync.cursor,maximum=10)
                pending=[{'id':_gid(n.get('id'),'Order'),'updated_at':stamp(n.get('updatedAt')).isoformat()} for n in nodes]
                if len({n['id'] for n in pending})!=len(pending): raise ShopifyError('Duplicate order index evidence.')
                data['counts']['considered']+=len(pending)
                sync.pending_json=dumps(pending);sync.page_cursor=page.get('endCursor');sync.page_more=page['hasNextPage']
                save_receipt(run,data,sync)
                fence();db.session.commit()
            batch=setting('SHOPIFY_ORDERS_BATCH_SIZE',5,1,10)
            for item in pending[:batch]:
                order,lines=read_order(adapter,config,item['id'])
                if order['updated_at']<datetime.fromisoformat(item['updated_at']):
                    raise ShopifyError('Order detail is older than index evidence; checkpoint retained.')
                changed=persist(store_id,order,lines,fence=fence)
                pending=pending[1:];sync.pending_json=dumps(pending);sync.imported+=int(changed)
                data['counts']['checked']+=1;data['counts']['up_to_date']+=1
                save_receipt(run,data,sync)
                fence();db.session.commit()
            if not pending:
                if sync.page_more:
                    sync.cursor=sync.page_cursor
                else:
                    sync.checkpoint=sync.window_end;sync.window_start=None;sync.window_end=None
                    sync.cursor=None;sync.page_cursor=None
                    sync.state='completed';sync.successful_at=now();fence();db.session.commit()
                    data['counts']['api_requests']+=budget['requests'];data['counts']['throttled']+=budget['throttled']
                    return tasks.save_progress(KEY,run_id,owner,data,terminal='completed')
            sync.state='pending';fence();db.session.commit()
            data['counts']['api_requests']+=budget['requests'];data['counts']['throttled']+=budget['throttled']
            data['reason']='Bounded Orders batch saved; continuing from durable cursor.'
            tasks.save_progress(KEY,run_id,owner,data,pause=True)
    except BackgroundYield:
        db.session.rollback()
        data=json.loads(run.data_json)
        data['reason']='Orders sync deferred for Shopify capacity/ownership; durable cursor retained.'
        try: tasks.save_progress(KEY,run_id,owner,data,pause=True)
        except BackgroundYield: db.session.rollback()
    except Exception as exc:
        db.session.rollback()
        data=json.loads(run.data_json)
        permission=isinstance(exc,OrdersPermissionRequired)
        message=AUTH_ERROR if permission else str(exc) if isinstance(exc,OrdersImportLimit) else SAFE_ERROR
        # Never persist or log exception strings from customer/remote data.
        try:
            if sync is not None:
                sync.state='authorisation_required' if permission else 'failed';sync.error=message
                fence();db.session.commit()
            data['reason']=message;data['counts']['unavailable']+=1
            tasks.save_progress(KEY,run_id,owner,data,terminal='failed',error=message)
            if permission:
                db.session.execute(db.update(ScheduledTask).where(ScheduledTask.key==KEY).values(state='authorisation_required'))
                db.session.commit()
        except BackgroundYield: db.session.rollback()


def redact_local_order(order):
    """Explicit administrator maintenance hook, not an import or remote deletion action.

    Caller must authorise, back up/handle historical backups, and commit. The durable
    tombstone prevents scheduled re-import from restoring erased personal data.
    """
    order.redacted_at=now()
    order.customer_id=order.customer_name=order.email=order.phone=None
    order.delivery_json='null';order.personalised=False
    for line in order.lines:
        line.properties_json='[]';line.personalisation_json=dumps({'fields':[],'public':[],'warnings':['Local personal data was redacted.']});line.personalised=False
