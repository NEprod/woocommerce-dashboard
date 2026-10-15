"""Small shared cost/priority gate. No credentials, Shopify writes or external broker."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from time import monotonic
from math import isfinite

from flask import current_app, has_app_context, has_request_context
from sqlalchemy.dialects.sqlite import insert
from app import db
from app.models import ShopifyApiCapacity

_background = ContextVar("shopify_background", default=None)


class BackgroundYield(RuntimeError):
    """Local scheduling deferral, NOT a failed Shopify verification."""


def now():
    from app.shopify_drafts import now as utcnow
    return utcnow()


def background():
    return _background.get()


@contextmanager
def background_work(guard, *, fence=None, requests=120, seconds=120, price_transitions=False):
    state = {"guard": guard, "fence":fence or guard, "remaining": requests, "deadline": monotonic()+seconds, "requests": 0, "throttled": 0,
             "price_transitions":price_transitions}
    token = _background.set(state)
    try:
        yield state
    finally:
        _background.reset(token)


def guard_owner():
    state = background()
    if state:
        state["guard"]()


def fence_owner():
    """Check ownership in the caller's commit transaction, not over network I/O."""
    state=background()
    if state:state["fence"]()


def signal_priority(priority=2):
    if background() or not has_app_context() or not current_app.config.get("SHOPIFY_CAPACITY_COORDINATION"):
        return
    from app.shopify_drafts import configuration
    try:
        key = configuration().fingerprint()
    except Exception:
        return  # no configured Shopify credentials; unrelated catalogue workflows are unchanged
    table = ShopifyApiCapacity.__table__
    from sqlalchemy.exc import SQLAlchemyError
    try:
        with db.engine.begin() as conn:
            conn.execute(insert(table).values(key=key,priority=priority,priority_until=now()+timedelta(seconds=30)).on_conflict_do_update(
                index_elements=[table.c.key],set_={"priority":priority,"priority_until":now()+timedelta(seconds=30)},
                where=(table.c.priority<=priority)|(table.c.priority_until<=now())|table.c.priority_until.is_(None)))
    except SQLAlchemyError:
        current_app.logger.warning("Shopify priority signal unavailable; durable operation locking remains active.")


def before(config, *, writing=False, query=None):
    state = background()
    if not state:
        if writing or has_request_context():
            signal_priority(2 if writing else 1)
        return
    guard_owner()
    if writing:
        from app.shopify_connection import PRICE_QUERY, VARIANTS_UPDATE
        permitted = state.get('price_transitions') is True and query in {PRICE_QUERY,VARIANTS_UPDATE}
    else: permitted = True
    if not permitted:
        raise BackgroundYield("Background Shopify mutations are forbidden.")
    if state["remaining"] <= 0 or monotonic() >= state["deadline"]:
        raise BackgroundYield("Bounded API/time budget reached; outstanding products retained.")
    row = db.session.get(ShopifyApiCapacity, config.fingerprint(), populate_existing=True)
    if row:
        if row.priority_until and row.priority_until > now():
            raise BackgroundYield("Yielding to interactive publishing/manual verification.")
        if row.observed_at and row.available is not None and row.restore_rate is not None:
            available = row.available + max(0, (now()-row.observed_at).total_seconds())*row.restore_rate
            if row.maximum is not None:
                available = min(available, row.maximum)
            cost=max(1,row.requested_cost or 1)
            reserve=min(100,max(0,(row.maximum-cost)/2),row.maximum*.1) if row.maximum is not None else 100
            if available < cost+reserve:
                raise BackgroundYield("Shopify capacity reserved for interactive work; resume after restoration.")
    state["remaining"] -= 1
    state["requests"] += 1


def observed(config, cost, *, throttled=False):
    state = background()
    if state and throttled:
        state["throttled"] += 1
    if not has_app_context() or not current_app.config.get("SHOPIFY_CAPACITY_COORDINATION") or not isinstance(cost, dict):
        return
    throttle = cost.get("throttleStatus")
    if not isinstance(throttle, dict):
        return
    values = {key: throttle.get(remote) for key, remote in (("available","currentlyAvailable"),("maximum","maximumAvailable"),("restore_rate","restoreRate"))}
    if any(type(v) not in (int,float) or not isfinite(v) or v < 0 for v in values.values()):
        return
    guard_owner()
    table = ShopifyApiCapacity.__table__
    values.update(observed_at=now(), requested_cost=cost.get("requestedQueryCost") if type(cost.get("requestedQueryCost")) in (int,float) else None)
    from sqlalchemy.exc import SQLAlchemyError
    try:
        with db.engine.begin() as conn:
            conn.execute(insert(table).values(key=config.fingerprint(), **values).on_conflict_do_update(index_elements=[table.c.key], set_=values))
    except SQLAlchemyError:
        if state:raise BackgroundYield("Shared capacity persistence unavailable; background work deferred.") from None
        current_app.logger.warning("Shared Shopify capacity observation unavailable; remote receipt preserved.")
