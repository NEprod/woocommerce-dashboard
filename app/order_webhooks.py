"""Private outbound delivery consumer using the existing scheduler, never Admin API."""
import hashlib
import json
import re
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

import requests
from flask import current_app
from sqlalchemy import or_, update
from sqlalchemy.dialects.sqlite import insert
from app import db
from app.order_models import ShopifyOrderEvent, ShopifyOrderDelivery, ShopifyOrderSync, ShopifyOrder
from app.order_webhook_security import verified, unseal
from app.order_webhook_payload import adapt, identity, gid
from app.shopify_orders import now, stamp, persist


def settings():
    return {k:current_app.config.get('SHOPIFY_ORDERS_WEBHOOK_'+k,'') for k in ('URL','TOKEN','KEY','SECRET','DOMAIN','STORE_ID')}


def configured():
    config=settings()
    return all(config.values())


def receive(envelope,config):
    """Commit authenticated encrypted receipt BEFORE acknowledging the gateway."""
    body,headers=unseal(envelope,config['KEY'])
    raw,topic,delivery=verified(body,headers,secret=config['SECRET'],domain=config['DOMAIN'])
    source=db.session.get(ShopifyOrderSync,config['STORE_ID'])
    if not source or source.domain!=config['DOMAIN']:
        raise ValueError('A prior authenticated store association is required; run Sync Now.')
    digest=hashlib.sha256(body).hexdigest()
    event_id=headers.get('x-shopify-event-id')
    if event_id and not re.fullmatch(r'[A-Za-z0-9-]{8,128}',event_id):event_id=None
    try:triggered=stamp(headers['x-shopify-triggered-at']) if headers.get('x-shopify-triggered-at') else None
    except ValueError:triggered=None
    existing=ShopifyOrderEvent.query.filter_by(store_id=source.store_id,delivery_id=delivery).first()
    if existing:
        if existing.digest!=digest or existing.topic!=topic:raise ValueError('Delivery identity collision.')
        db.session.commit()
        return delivery
    order_gid=None;version=None
    try:
        order_gid=gid(raw['order_id'],'Order') if topic=='refunds/create' else identity(raw,'Order')
        value=raw.get('updated_at') or raw.get('created_at')
        version=stamp(value) if value else None
    except (KeyError,TypeError,ValueError,RuntimeError):pass
    db.session.execute(insert(ShopifyOrderEvent).values(store_id=source.store_id,delivery_id=delivery,
        topic=topic,event_id=event_id,triggered_at=triggered,order_gid=order_gid,source_updated_at=version,received_at=now(),
        digest=digest,payload=envelope,state='pending',attempts=0).on_conflict_do_nothing(
            index_elements=['store_id','delivery_id']))
    channel=db.session.get(ShopifyOrderDelivery,'orders');channel.received_at=now()
    db.session.commit()
    return delivery


def fence(owner):
    count=db.session.execute(update(ShopifyOrderDelivery).where(ShopifyOrderDelivery.key=='orders',
        ShopifyOrderDelivery.owner==owner,ShopifyOrderDelivery.lease_until>now()).values(owner=owner)).rowcount
    if count!=1:raise RuntimeError('Delivery lease lost.')


def process(event_id,config,*,owner):
    event=db.session.get(ShopifyOrderEvent,event_id)
    if not event or event.state!='pending':return
    try:
        body,headers=unseal(event.payload,config['KEY'])
        raw,topic,_=verified(body,headers,secret=config['SECRET'],domain=config['DOMAIN'])
        order,lines=adapt(raw,topic)
    except Exception:
        # No untrusted payload/exception text enters logs or diagnostics.
        fence(owner);event.state='incomplete';event.attempts+=1
        event.error='Payload lacks a safe complete order snapshot. Use Sync Now; saved order is unchanged.'
        db.session.commit();return
    try:
        fence(owner)
        existing=ShopifyOrder.query.filter_by(store_id=event.store_id,remote_id=order['remote_id']).first()
        if existing and existing.updated_at>=order['updated_at']:
            # Same revision can carry different completeness across topics. Never
            # let arrival order choose between conflicting snapshots.
            same=all(getattr(existing,k)==v for k,v in order.items() if k!='api_version')
            previous=[l for l in existing.lines if l.is_current]
            same=same and len(previous)==len(lines) and all(
                all(getattr(a,k)==v for k,v in b.items()) for a,b in zip(previous,lines))
            event.state='stale' if existing.updated_at>order['updated_at'] else 'applied' if same else 'incomplete'
            if event.state=='incomplete':event.error='Conflicting same-revision event; use Sync Now. Saved snapshot preserved.'
        else:
            persist(event.store_id,order,lines,fence=lambda:fence(owner));event.state='applied'
        event.processed_at=now();event.attempts+=1
        if event.state in {'applied','stale'}:event.payload=None
        db.session.get(ShopifyOrderDelivery,'orders').processed_at=now()
        db.session.commit()
    except Exception:
        db.session.rollback()
        fence(owner);event=db.session.get(ShopifyOrderEvent,event_id);event.attempts+=1
        event.error='Local event persistence failed; retained payload will retry. Check appdata storage.'
        if event.attempts>=5:event.state='dead_letter'
        db.session.commit()


def pull(config):
    """Bounded encrypted queue transfer. Bearer is never exposed to browser routes."""
    parsed=urlsplit(config['URL'])
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('A dedicated HTTPS gateway URL is required.')
    if len(config['TOKEN'])<32:raise ValueError('A strong gateway credential is required.')
    session=requests.Session();session.trust_env=False
    headers={'Authorization':'Bearer '+config['TOKEN']}
    response=None
    try:
        response=session.get(config['URL'].rstrip('/')+'/events',headers=headers,timeout=(3,5),allow_redirects=False,stream=True)
        if response.status_code!=200:raise ValueError('Gateway access unavailable.')
        chunks=[];length=0
        for chunk in response.iter_content(65536):
            length+=len(chunk)
            if length>4*1024*1024:raise ValueError('Gateway batch exceeds safe bound.')
            chunks.append(chunk)
        events=json.loads(b''.join(chunks))
        if not isinstance(events,list) or len(events)>1:raise ValueError('Invalid gateway batch.')
        ids=[]
        for event in events:
            if event.get('envelope') is None:
                # Trusted queue expiry metadata is not a complete Shopify event.
                # Record the loss explicitly before ack; never update commerce.
                from app.order_webhook_security import TOPICS
                if (not re.fullmatch(r'[A-Za-z0-9-]{8,128}',event['id']) or event['topic'] not in TOPICS
                    or not re.fullmatch(r'[a-f0-9]{64}',event['digest'])):raise ValueError('Invalid expiry marker.')
                source=db.session.get(ShopifyOrderSync,config['STORE_ID'])
                if not source or source.domain!=config['DOMAIN']:raise ValueError('Store association required.')
                db.session.execute(insert(ShopifyOrderEvent).values(store_id=source.store_id,delivery_id=event['id'],
                    topic=event['topic'],order_gid=gid(event['order_gid'],'Order') if event.get('order_gid') else None,
                    source_updated_at=stamp(event['updated_at']) if event.get('updated_at') else None,
                    received_at=now(),digest=event['digest'],state='expired',attempts=0,
                    error='Gateway payload expired; use recent-history reconciliation.').on_conflict_do_nothing(index_elements=['store_id','delivery_id']))
                db.session.commit();delivery=event['id']
            else:delivery=receive(event['envelope'],config)
            if delivery!=event['id']:raise ValueError('Gateway receipt identity mismatch.')
            ids.append(delivery)
        if ids:
            ack=session.post(config['URL'].rstrip('/')+'/ack',headers=headers,json={'ids':ids},timeout=(3,5),allow_redirects=False)
            if ack.status_code!=200:raise ValueError('Gateway acknowledgement pending; durable deduplication protects retries.')
    finally:
        if response is not None:response.close()
        session.close()


def tick(owner=None):
    config=settings()
    if not all(config.values()):return
    owner=owner or uuid.uuid4().hex
    stamp_now=now()
    claimed=db.session.execute(update(ShopifyOrderDelivery).where(ShopifyOrderDelivery.key=='orders',
        or_(ShopifyOrderDelivery.lease_until.is_(None),ShopifyOrderDelivery.lease_until<stamp_now),
        or_(ShopifyOrderDelivery.next_pull.is_(None),ShopifyOrderDelivery.next_pull<=stamp_now)
        ).values(owner=owner,lease_until=stamp_now+timedelta(seconds=60),next_pull=stamp_now+timedelta(seconds=15))).rowcount
    db.session.commit()
    if not claimed:return
    try:
        pull(config)
        channel=db.session.get(ShopifyOrderDelivery,'orders');fence(owner);channel.error=None;db.session.commit()
    except Exception:
        db.session.rollback();fence(owner)
        db.session.get(ShopifyOrderDelivery,'orders').error='Webhook gateway unavailable or invalid. Check approved delivery setup; Sync Now remains available.'
        db.session.commit()
    try:
        # Bounded local inbox processing and explicit sensitive-payload retention.
        fence(owner)
        ShopifyOrderEvent.query.filter(ShopifyOrderEvent.payload.isnot(None),
            ShopifyOrderEvent.received_at<now()-timedelta(days=7)).update({
                'payload':None,'state':'expired','error':'Encrypted payload retention expired; manual reconciliation required.'},synchronize_session=False)
        db.session.commit()
        ids=[r.id for r in ShopifyOrderEvent.query.filter_by(state='pending',store_id=config['STORE_ID']).order_by(ShopifyOrderEvent.id).limit(10)]
        for event_id in ids:process(event_id,config,owner=owner)
    finally:
        db.session.rollback()
        db.session.execute(update(ShopifyOrderDelivery).where(ShopifyOrderDelivery.key=='orders',ShopifyOrderDelivery.owner==owner).values(owner=None,lease_until=None))
        db.session.commit()
