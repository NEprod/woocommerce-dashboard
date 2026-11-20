"""Inactive historical Promotions publisher; local planner uses document/receipt only."""
from datetime import datetime, UTC, timedelta
import json
from uuid import uuid4

from flask import current_app
from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import aliased

from app import db
from app.models import CatalogueOperation
from . import domain
from .models import Promotion
from .shopify import TYPES, Admin, Configuration, Rejected, ShopifyPromotionError, observed, same_identity


def now():
    return datetime.now(UTC)


def stamp():
    return now().replace(tzinfo=None)


def client():
    config = Configuration.from_env()
    return Admin(config) if config else None


def document(row):
    return json.loads(row.document)


def receipt(row):
    return json.loads(row.receipt or '{}')


def _verify_keys(expected):
    keys = {'title','starts','ends','combines'}
    if expected['method']=='code':
        keys.update({'code','usage','once'})
    if expected['kind']=='exact':
        keys.update({'function','function_config'})
    elif expected['kind']=='bxgy':
        keys.update({'variants','rewards','buy','get','value','limit','segment'})
    else:
        keys.update({'variants','scope','value','minimum','segment'})
    return keys


def _type(expected):
    return TYPES[(expected['method'],expected['kind'] if expected['kind'] in ('exact','bxgy') else 'basic')][0]


def _required_optional(definition, scopes):
    if definition['kind']=='exact' and 'read_markets' not in scopes:
        raise ShopifyPromotionError('Missing read_markets: exact-price offers require an independently verified UK market restriction.')
    if definition.get('segment') and 'read_customers' not in scopes:
        raise ShopifyPromotionError('Missing optional read_customers: the reviewed code’s Shopify customer segment cannot be verified.')


def _find_read(adapter, expected, ident, *, required=False):
    if not ident:
        matches = adapter.find(expected['marker'])
        if len(matches) > 1:
            raise ShopifyPromotionError('More than one Shopify discount bears this TLC ownership marker.')
        ident = matches[0]['id'] if matches else None
    if not ident:
        if required:
            raise ShopifyPromotionError('No owned discount can yet be verified. The attempt remains held; do not repeat Create.')
        return None
    node = adapter.read(ident,_type(expected))
    if not node or not same_identity(ident,node.get('id')):
        raise ShopifyPromotionError('The retained Shopify discount could not be read. No replacement will be created.')
    return observed(node,expected)


def _phase(e):
    current = domain.iso(now())
    return 'EXPIRED' if e['ends'] and e['ends'] <= current else 'SCHEDULED' if e['starts'] > current else 'ACTIVE'


def _matches(actual, expected, action):
    if actual is None:
        return False
    if action == 'disable':
        return actual['status'] == 'EXPIRED'
    return (all(actual.get(key) == expected[key] for key in _verify_keys(expected))
            and actual['status'] == _phase(expected))


def _overlaps(row, store, expected):
    ids = set(expected['variants']) | set(expected.get('rewards',[]))
    if not ids:
        return
    for peer in Promotion.query.filter(Promotion.id != row.id, Promotion.receipt.is_not(None)).all():
        proof = receipt(peer)
        if (proof.get('published') or {}).get('shop') not in (None,store):
            continue
        previous, pending = proof.get('published'), proof.get('pending')
        sources = []
        if previous and previous.get('action')!='disable':
            sources.append(previous['expected'])
        if pending and pending['shop']==store and pending['action']!='disable':
            sources.append(pending['expected'])
        for other in sources:
            if (expected['ends'] and expected['ends'] <= other['starts'] or
                    other['ends'] and other['ends'] <= expected['starts']):
                continue
            overlap = ids & (set(other['variants']) | set(other.get('rewards',[])))
            if overlap:
                raise ShopifyPromotionError(f'{len(overlap)} eligible variants overlap another TLC promotion in this schedule. Use disjoint groups or non-overlapping dates.')


def build(row, adapter):
    d, saved = document(row), receipt(row)
    if row.state == "migration_review":
        raise ShopifyPromotionError("Review the detached variant or customer selections in Edit Promotion before publishing.")
    if row.state in ('pending','uncertain'):
        raise ShopifyPromotionError('Read-only reconciliation is required before publishing another change.')
    if d['intent']=='draft':
        raise ShopifyPromotionError('Draft promotions stay local. Save Live intent to review a publication.')
    conn = adapter.connection()
    prior = saved.get('published')
    if prior and (prior['shop'],prior['app']) != (conn['shop'],conn['app']):
        raise ShopifyPromotionError('A promotion cannot switch its retained Shopify store or app identity.')
    if d['intent'] in ('disabled','archived'):
        if not prior:
            raise ShopifyPromotionError('This promotion is local-only; there is no Shopify discount to turn off.')
        expected = prior['expected']
        _required_optional(expected, conn['scopes'])
        remote = _find_read(adapter,expected,prior['result']['id'],required=True)
        action = 'disable'
        rows, rewards = [], []
    else:
        _required_optional(d,conn['scopes'])
        if prior:
            _required_optional(prior['expected'],conn['scopes'])
        if d['ends'] and d['ends'] <= domain.iso(now()):
            raise ShopifyPromotionError('This promotion has expired. Review a new schedule first.')
        rows = domain.resolve(d['targets'],conn['shop'],d.get('exclusions',[])) if d['scope']=='product' else []
        rewards = domain.resolve(d['rewards'],conn['shop']) if d['kind']=='bxgy' and d['rewards'] else rows if d['kind']=='bxgy' else []
        adapter.verify_variants(list({r['id']:r for r in [*rows,*rewards]}.values()))
        expected = {**d,'segment':d.get('segment'),'title':'TLC · ' + d['name'],'marker':'tlc-promotion-' + row.id,
                    'variants':[r['id'] for r in rows]}
        expected.pop('targets')
        expected.pop('exclusions', None)
        if d['kind']=='bxgy':
            expected['rewards'] = [r['id'] for r in rewards]
        if d['kind']=='exact':
            expected['function'] = adapter.function()
            expected['function_config'] = {'version':1,'currency':'GBP','country':'GB','variants':expected['variants'],
                                           'tiers':d['tiers'],'repeat':d['repeat']}
            if len(domain.canonical(expected['function_config']).encode()) > 9000:
                raise ShopifyPromotionError('Eligibility exceeds the safe Function configuration size.')
        if prior and (_type(prior['expected']) != _type(expected) or prior['expected'].get('scope') != d['scope'] or
                prior['expected'].get('code') != d.get('code') or prior['expected']['kind'] != d['kind']):
            raise ShopifyPromotionError('Discount type, scope and code are retained. Disable this discount and duplicate it for a different type, scope or code.')
        remote = _find_read(adapter,expected,prior['result']['id'] if prior else None)
        if remote and not prior:
            raise ShopifyPromotionError('Shopify already has a discount with this local marker. No duplicate will be created; reconcile its retained attempt.')
        if d['method']=='code':
            owner = adapter.code_owner(d['code'])
            if owner and not (remote and same_identity(owner,remote['id'])):
                raise ShopifyPromotionError('This code belongs to another Shopify discount and cannot be taken over.')
        _overlaps(row,conn['shop'],expected)
        if d['method']=='automatic' and (not remote or remote['status']=='EXPIRED') and adapter.automatic_count() >= 25:
            raise ShopifyPromotionError('The Shopify active/scheduled automatic discount budget is exhausted.')
        action = 'update' if remote else 'create'
    if _matches(remote,expected,action):
        action = 'verify_disabled' if action=='disable' else 'verify'
    return {'id':row.id,'revision':row.revision,'state':row.state,'shop':conn['shop'],'app':conn['app'],
            'scopes':conn['scopes'],'action':action,'expected':expected,'remote':remote,'rows':rows,'rewards':rewards}


def _serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='tlc-promotions-development-v1')


def approval(plan, actor):
    return _serializer().dumps({'id':plan['id'],'actor':str(actor),'digest':domain.fingerprint(plan)})


def _finish(row_id, expected_state, raw, *, state, proof):
    hit = db.session.execute(update(Promotion).where(Promotion.id==row_id,Promotion.state==expected_state,
        Promotion.receipt==raw).values(state=state,receipt=domain.canonical(proof),updated_at=stamp())).rowcount
    if hit != 1:
        db.session.rollback()
        raise ShopifyPromotionError('Another operation owns this promotion evidence. Reload Promotions before continuing.')
    db.session.commit()


def _complete(row, raw, proof, attempt, actual):
    action = 'disable' if attempt['action'] in ('disable','verify_disabled') else attempt['action']
    if not _matches(actual,attempt['expected'],action) or attempt.get('candidate') and not same_identity(attempt['candidate'],actual['id']):
        raise ShopifyPromotionError('Shopify did not read back the reviewed promotion. Keep the attempt for read-only reconciliation.')
    proof['published'] = {'shop':attempt['shop'],'app':attempt['app'],'action':action,'expected':attempt['expected'],
                          'result':actual,'fingerprint':domain.fingerprint(attempt['expected']),
                          'checked':domain.iso(now()),'revision':attempt['revision']}
    proof.pop('pending',None)
    proof.pop('error',None)
    proof.pop('shopify_user_errors',None)
    _finish(row.id,row.state,raw,state='verified',proof=proof)


def _record_rejection(row, attempt, exc):
    """Append a safe Event History record without affecting the retained refusal."""
    scope = {'promotion_id':row.id, 'shop_id':attempt['shop'], 'action':attempt['action'],
             'shopify_user_errors':exc.details}
    started = datetime.fromisoformat(attempt['began']).replace(tzinfo=None)
    db.session.add(CatalogueOperation(id=uuid4().hex, operation_type='shopify_promotion_publish',
        status='failed', scope=domain.canonical(scope), error=str(exc), started_at=started,
        finished_at=stamp()))
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.warning('Promotion rejection was retained, but its Event History record could not be saved.')


def confirm(row, token, actor, adapter):
    raise ShopifyPromotionError('Promotion publishing is retired. Manage discounts in Shopify Admin.')
    from app.deployment_guard import promotion_transport
    if hasattr(adapter, "config"):
        promotion_transport(adapter.config, writing=True)
    try:
        signed = _serializer().loads(token,max_age=600)
    except BadData:
        raise ShopifyPromotionError('Review approval expired; rebuild Review & Publish.') from None
    if not isinstance(signed,dict) or signed.get('id')!=row.id or signed.get('actor')!=str(actor):
        raise ShopifyPromotionError('This Review & Publish approval belongs to a different promotion or operator.')
    plan = build(row,adapter)
    if domain.fingerprint(plan) != signed.get('digest'):
        raise ShopifyPromotionError('Shopify or the local review has changed. Build a new preview before publishing.')
    proof = receipt(row)
    attempt = {k:plan[k] for k in ('action','expected','shop','app','revision')}
    attempt.update(began=domain.iso(now()),candidate=plan['remote']['id'] if plan['remote'] else None)
    proof.update(pending=attempt)
    previous = row.state
    query = update(Promotion).where(Promotion.id==row.id,Promotion.revision==plan['revision'],Promotion.state==previous,
        Promotion.receipt.is_(None) if row.receipt is None else Promotion.receipt==row.receipt)
    if plan['action'] not in ('verify','verify_disabled'):
        peer = aliased(Promotion)
        query = query.where(~select(peer.id).where(peer.state=='pending').exists())
    raw = domain.canonical(proof)
    if db.session.execute(query.values(state='pending',receipt=raw,updated_at=stamp())).rowcount != 1:
        db.session.rollback()
        raise ShopifyPromotionError('A publication is already outstanding or the definition changed. Reload Promotions.')
    if plan['action'] in ('create','update'):
        try:
            # Now holding the local SQLite writer reservation. Catch peers
            # that finished publication between read-only review and reserve.
            _overlaps(row,plan['shop'],plan['expected'])
        except ValueError:
            db.session.rollback()
            raise
    db.session.commit()
    accepted = False
    try:
        ident = attempt['candidate']
        if plan['action'] not in ('verify','verify_disabled'):
            ident = adapter.apply(plan['action'],plan['expected'],plan['remote'])
            accepted = True
            attempt['candidate'] = ident
            next_raw = domain.canonical(proof)
            _finish(row.id,'pending',raw,state='pending',proof=proof)
            raw = next_raw
        actual = _find_read(adapter,plan['expected'],ident,required=True)
        _complete(row,raw,proof,attempt,actual)
    except Rejected as exc:
        if accepted:
            raise
        proof.pop('pending',None)
        proof['error'] = str(exc)
        proof['shopify_user_errors'] = exc.details
        _finish(row.id,'pending',raw,state='attention',proof=proof)
        _record_rejection(row, attempt, exc)
        raise
    except ShopifyPromotionError as exc:
        proof['error'] = str(exc)
        _finish(row.id,'pending',raw,state='uncertain',proof=proof)
        raise


def reconcile(row, adapter):
    raise ShopifyPromotionError('Promotion reconciliation is retired. The planner cannot alter historical Shopify evidence.')
    proof = receipt(row)
    attempt = proof.get('pending')
    if row.state not in ('pending','uncertain') or not attempt:
        raise ShopifyPromotionError('No uncertain Promotion attempt is retained.')
    if row.state=='pending' and now() < datetime.fromisoformat(attempt['began']) + timedelta(minutes=2):
        raise ShopifyPromotionError('The original request may still be running. Wait two minutes before read-only reconciliation.')
    conn = adapter.connection()
    if (conn['shop'],conn['app']) != (attempt['shop'],attempt['app']):
        raise ShopifyPromotionError('Reconnect the original Promotions development store and app.')
    _required_optional(attempt['expected'],conn['scopes'])
    raw = row.receipt
    try:
        actual = _find_read(adapter,attempt['expected'],attempt.get('candidate'),required=True)
        _complete(row,raw,proof,attempt,actual)
    except ShopifyPromotionError as exc:
        proof['error'] = str(exc)
        _finish(row.id,row.state,raw,state='uncertain',proof=proof)
        raise
