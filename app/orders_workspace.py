"""Indexed local Orders views. Rendering never calls Shopify or the scanner."""
import json
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import func, or_
from app import db
from app.models import ShopifyOrder, ShopifyOrderLine, ShopifyOrderSync, ScheduledTask
from app.shopify_orders import KEY

UK = ZoneInfo('Europe/London')


def uk_time(value):
    return value.replace(tzinfo=timezone.utc).astimezone(UK).strftime('%d/%m/%Y %H:%M %Z') if value else 'Not yet'


def sync_status():
    from app.scheduled_tasks import snapshot
    task = db.session.get(ScheduledTask,KEY)
    sync = ShopifyOrderSync.query.order_by(ShopifyOrderSync.attempted_at.desc()).first()
    state = snapshot(task)
    state['imported_orders']=ShopifyOrder.query.count()
    state['last_successful']=sync.successful_at if sync else None
    state['last_attempted']=sync.attempted_at if sync else None
    from app.order_models import ShopifyOrderEvent, ShopifyOrderDelivery
    from app.order_webhooks import configured
    delivery=db.session.get(ShopifyOrderDelivery,'orders')
    state.update(last_startup=sync.startup_successful_at if sync else None,
        last_manual=sync.manual_successful_at if sync else None,
        webhook_received=delivery.received_at if delivery else None,
        webhook_processed=delivery.processed_at if delivery else None,
        webhook_pending=ShopifyOrderEvent.query.filter_by(state='pending').count(),
        webhook_incomplete=ShopifyOrderEvent.query.filter(ShopifyOrderEvent.state.in_(['incomplete','dead_letter','expired'])).count(),
        webhook_configured=configured(),webhook_error=delivery.error if delivery else None,
        error_category=sync.error_category if sync else None,error_phase=sync.error_phase if sync else None)
    if task.active_run_id:
        state['label']='Orders import running'
    elif task.state=='authorisation_required' or sync and sync.state=='authorisation_required':
        state['label']='Shopify Orders permission required'
    elif state['error'] or sync and sync.state=='failed':
        state['label']='Last import failed'
    elif sync and sync.successful_at:
        state['label']='Orders imported — local snapshot'
    else:
        state['label']='No orders imported yet'
    if not task.active_run_id and state['webhook_incomplete']:state['label']='Needs manual reconciliation'
    elif not task.active_run_id and not state['webhook_configured'] and not (state['error'] or sync and sync.state in {'failed','authorisation_required'}):
        state['label']='Webhook delivery not configured'
    elif not task.active_run_id and state['webhook_error']:state['label']='Webhook delivery unavailable'
    elif (not task.active_run_id and state['webhook_processed'] and not state['webhook_pending']
          and sync and sync.state=='completed' and not state['error']):
        state['label']='Latest received webhooks processed — local snapshot'
    state['message']=(sync.error if sync else None) or state['error'] or state['reason'] or 'Sync Now queues a bounded read-only import. No Shopify order is changed.'
    return state


def listing(args):
    query = ShopifyOrder.query
    search = (args.get('q') or '').strip()[:100]
    fulfillment = args.get('fulfillment','')
    financial = args.get('payment','')
    test = args.get('test','')
    personalised = args.get('personalised','')
    if search:
        escaped=search.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        pattern='%'+escaped+'%'
        skus=db.select(ShopifyOrderLine.order_id).where(ShopifyOrderLine.sku.ilike(pattern,escape='\\'))
        query=query.filter(or_(ShopifyOrder.name.ilike(pattern,escape='\\'),ShopifyOrder.id.in_(skus)))
    if fulfillment=='fulfilled': query=query.filter_by(fulfillment_status='FULFILLED')
    elif fulfillment=='unfulfilled': query=query.filter(ShopifyOrder.outstanding_quantity>0,ShopifyOrder.cancelled_at.is_(None))
    allowed={'PENDING','AUTHORIZED','PARTIALLY_PAID','PAID','PARTIALLY_REFUNDED','REFUNDED','VOIDED','EXPIRED'}
    if financial in allowed: query=query.filter_by(financial_status=financial)
    else: financial=''
    if test in {'yes','no'}: query=query.filter_by(test=test=='yes')
    if personalised=='yes': query=query.filter_by(personalised=True)
    try: page=max(1,min(int(args.get('page',1)),100000))
    except (TypeError,ValueError): page=1
    pagination=db.paginate(query.order_by(ShopifyOrder.created_at.desc(),ShopifyOrder.id.desc()),page=page,per_page=25,error_out=False)
    return {'pagination':pagination,'orders':pagination.items,
            'filters':{'q':search,'fulfillment':fulfillment,'payment':financial,'test':test,'personalised':personalised},
            'financial_options':sorted(allowed),'sync':sync_status()}


def line_view(line):
    # Re-read the immutable captured properties, not today's catalogue. This
    # also renders previously imported orders without rewriting stored receipts.
    from app.order_personalisation import parse
    properties=json.loads(line.properties_json)
    parsed=(parse(properties, line.product_id) if any(p['key']=='Production time' for p in properties)
            else json.loads(line.personalisation_json))
    return {'row':line,'personalisation':parsed,'money':json.loads(line.money_json),
            'properties':properties}


def overview(*, include_test=False, as_of=None):
    reference=as_of or datetime.now(timezone.utc)
    local_day=reference.astimezone(UK).date()
    start=datetime.combine(local_day,time(),UK).astimezone(timezone.utc).replace(tzinfo=None)
    end=datetime.combine(local_day+timedelta(days=1),time(),UK).astimezone(timezone.utc).replace(tzinfo=None)
    base=ShopifyOrder.query
    if not include_test: base=base.filter_by(test=False)
    eligible=base.filter(ShopifyOrder.cancelled_at.is_(None),
        ShopifyOrder.financial_status.in_({'PAID','PARTIALLY_REFUNDED'}),ShopifyOrder.outstanding_quantity>0)
    return {'unfulfilled':eligible.count(),
        'items':eligible.with_entities(func.coalesce(func.sum(ShopifyOrder.outstanding_quantity),0)).scalar(),
        'today':base.filter(ShopifyOrder.created_at>=start,ShopifyOrder.created_at<end).count(),
        'recent':base.order_by(ShopifyOrder.created_at.desc(),ShopifyOrder.id.desc()).limit(5).all(),
        'sync':sync_status(),'include_test':include_test}
