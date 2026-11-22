"""Bounded opt-in order-metafield worker on the existing scheduler/client.
Only tlc_operations.production_status is writable. No customer emails or native
fulfilment/cancellation/price/tag writes are implemented here.
"""
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select, update, or_
from app import db
from app.models import ShopifyOrder
from app.order_work_models import WorkOutbound, WorkEvent, OrderWorkLine
from app.order_integration_models import OrderIntegrationSettings, OrderStatusProjection
from app.order_integrations import configuration
from app.shopify_connection import ShopifyAdapter
from app.order_work import now, summary
from app import order_operation_queries as q


def projected_status(order):
    value=summary(order)
    return {'Dispatched locally':'Dispatched','Picking Started':'Picking','Production Started':'In Production',
        'Assembly / Finishing':'In Production','Ready for Production':'Ready for Production',
        'Mixed — see individual lines':'Mixed — production in progress',
        'Needs Attention':'On Hold / Needs Attention','Local work cancelled — verify Shopify separately':'Local work cancelled'}.get(value,value)


def process(order_id,*,adapter=None):
    adapter=adapter or ShopifyAdapter();owner=uuid4().hex;stamp=now()
    order=db.session.get(ShopifyOrder,order_id)
    if not order or order.redacted_at:return None
    from app.orders_workspace import order_allowed
    if not order_allowed(order):return None
    authority=db.session.get(OrderIntegrationSettings,order.store_id)
    if not authority or not authority.projection_enabled:return None
    try:config=configuration()
    except Exception:config=None
    from app.inventory.service import writer_lock
    writer_lock()
    row=db.session.get(OrderStatusProjection,order_id)
    if row is None:
        row=OrderStatusProjection(order_id=order_id,state='pending',attempts=0,error='');db.session.add(row);db.session.flush()
    if row.owner and row.lease_until and row.lease_until>stamp:db.session.rollback();return None
    if row.next_attempt and row.next_attempt>stamp:db.session.rollback();return None
    if config is None:
        row.state='configuration_required';row.error='Existing Shopify app configuration is unavailable. Review Connections; no status write was attempted.'
        row.next_attempt=stamp+timedelta(minutes=15);db.session.commit();return row
    if config.fingerprint()!=authority.fingerprint:
        row.state='reauthorisation_required';row.error='Existing app credentials changed. Explicitly reauthorise status projection for this store.'
        row.next_attempt=stamp+timedelta(minutes=15);db.session.commit();return row
    trusted=row.state=='verified'
    row.owner=owner;row.lease_until=stamp+timedelta(seconds=90);row.state='reading'
    watermark=db.session.scalar(select(db.func.max(WorkOutbound.id)).join(WorkEvent,WorkEvent.id==WorkOutbound.event_id)
        .join(OrderWorkLine,OrderWorkLine.line_id==WorkEvent.line_id).where(OrderWorkLine.order_id==order_id,WorkOutbound.channel=='shopify')) or 0
    pending_ids=select(WorkOutbound.id).join(WorkEvent,WorkEvent.id==WorkOutbound.event_id).join(OrderWorkLine,
        OrderWorkLine.line_id==WorkEvent.line_id).where(OrderWorkLine.order_id==order_id,WorkOutbound.id<=watermark,
            WorkOutbound.channel=='shopify',WorkOutbound.state!='delivered')
    WorkOutbound.query.filter(WorkOutbound.id.in_(pending_ids)).update(
        dict(state='pending',error='',attempts=WorkOutbound.attempts+1),synchronize_session=False)
    wanted=projected_status(order);previous_value=row.value;db.session.commit()
    def fence():
        changed=db.session.execute(update(OrderStatusProjection).where(OrderStatusProjection.order_id==order_id,
            OrderStatusProjection.owner==owner,OrderStatusProjection.lease_until>now()).values(owner=owner)).rowcount
        if changed!=1:raise RuntimeError('Order projection lease lost.')
    try:
        shop=adapter.test_connection(config)
        if shop.get('shop_id')!=order.store_id or not {'read_orders','write_orders'}<=set(shop.get('scopes',[])):
            fence();row.state='permission_required';row.error='Current store-bound read_orders and write_orders approval is required.'
            WorkOutbound.query.filter(WorkOutbound.id.in_(pending_ids)).update(dict(state='blocked',error=row.error),synchronize_session=False)
        else:
            if previous_value!=wanted or not trusted:
                remote=adapter._read(config,q.STATUS_READ,{'id':order.remote_id})
                node=remote.get('order')
                if remote.get('shop',{}).get('id')!=order.store_id or not node or node['id']!=order.remote_id:
                    raise ValueError('Store/order readback identity mismatch.')
                field=node.get('metafield')
                if not field or field.get('value')!=wanted:
                    fence();row.state='writing';row.attempts+=1;db.session.commit()
                    value=dict(ownerId=order.remote_id,namespace='tlc_operations',key='production_status',
                        type='single_line_text_field',value=wanted,compareDigest=field.get('compareDigest') if field else None)
                    result=adapter._write(config,q.STATUS_SET,{'metafields':[value]})['metafieldsSet']
                    if result.get('userErrors'):raise ValueError('Shopify rejected the status-only metafield.')
                remote=adapter._read(config,q.STATUS_READ,{'id':order.remote_id})
                node=remote.get('order') or {};field=node.get('metafield') or {}
                if (remote.get('shop',{}).get('id')!=order.store_id or node.get('id')!=order.remote_id
                    or any(field.get(k)!=v for k,v in {'namespace':'tlc_operations','key':'production_status',
                        'type':'single_line_text_field','value':wanted}.items())):
                    raise ValueError('Status metafield readback did not verify.')
            fence();row.value=wanted;row.state='verified';row.error='';row.verified_at=now()
            ids=select(WorkOutbound.id).join(WorkEvent,WorkEvent.id==WorkOutbound.event_id).join(OrderWorkLine,
                OrderWorkLine.line_id==WorkEvent.line_id).where(OrderWorkLine.order_id==order_id,WorkOutbound.id<=watermark,WorkOutbound.channel=='shopify')
            WorkOutbound.query.filter(WorkOutbound.id.in_(ids)).update(dict(state='delivered',error='',delivered_at=now()),synchronize_session=False)
        row.owner=None;row.lease_until=None;row.next_attempt=now()+timedelta(minutes=15 if row.state=='permission_required' else 1)
        db.session.commit()
    except Exception:
        db.session.rollback()
        row=db.session.get(OrderStatusProjection,order_id)
        if row and row.owner==owner:
            row.state='uncertain';row.error='Status update/readback unavailable. Next attempt reads current remote state before any write; native fulfilment is untouched.'
            WorkOutbound.query.filter(WorkOutbound.id.in_(pending_ids)).update(dict(state='retry',error=row.error),synchronize_session=False)
            row.owner=None;row.lease_until=None;row.next_attempt=now()+timedelta(minutes=2);db.session.commit()
    return row


def tick(*,adapter=None):
    """At most two orders per existing scheduler tick; disabled means no API calls."""
    from app.orders_workspace import include_test_orders
    ids=db.session.scalars(select(OrderWorkLine.order_id).join(WorkEvent,WorkEvent.line_id==OrderWorkLine.line_id)
        .join(WorkOutbound,WorkOutbound.event_id==WorkEvent.id).join(ShopifyOrder,ShopifyOrder.id==OrderWorkLine.order_id)
        .join(OrderIntegrationSettings,OrderIntegrationSettings.store_id==ShopifyOrder.store_id)
        .outerjoin(OrderStatusProjection,OrderStatusProjection.order_id==ShopifyOrder.id)
        .where(OrderIntegrationSettings.projection_enabled.is_(True),ShopifyOrder.redacted_at.is_(None),db.true() if include_test_orders() else ShopifyOrder.test.is_(False),WorkOutbound.channel=='shopify',
               WorkOutbound.state!='delivered',or_(OrderStatusProjection.next_attempt.is_(None),OrderStatusProjection.next_attempt<=now())).distinct().limit(2)).all()
    for order_id in ids:
        try:process(order_id,adapter=adapter)
        except Exception:db.session.rollback()  # bad credentials never block read-only polling
