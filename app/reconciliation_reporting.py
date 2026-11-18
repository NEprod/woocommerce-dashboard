"""Current, read-only reconciliation attention; history is never rewritten.

Only saved catalogue evidence is used. No folder scans, Shopify calls, queue
settlement or publication authority are introduced by this projection.
"""
from datetime import datetime

from sqlalchemy import or_, func, case, select
from flask import url_for, current_app, has_request_context
from app.models import Product, ShopifyPublishJob, ScheduledTaskRun, ShopifyProductIdentity
from app.operation_reporting import document


def destination(endpoint, **values):
    return url_for(endpoint, **values) if has_request_context() else current_app.url_map.bind('').build(endpoint, values)


def resolved_after(state, stamp):
    try:
        checked = datetime.fromisoformat((state.get('compared_at') or '').removesuffix('Z'))
        return (state.get('state') in {'no_change', 'update', 'create', 'draft'} and
            not state.get('blockers') and stamp is not None and checked > stamp)
    except (ValueError, TypeError):
        return False


def attention():
    from app import scheduled_tasks as tasks
    from app.catalogue_views import saved_states
    runs = ScheduledTaskRun.query.filter_by(task_key=tasks.KEY).order_by(
        ScheduledTaskRun.created_at, ScheduledTaskRun.id).all()
    observations, unbound = {}, {}
    for run in runs:
        data = document(run.data_json)
        stamp = run.completed_at or run.started_at or run.created_at
        items = [item for item in data.get('items', []) if isinstance(item, dict) and type(item.get('id')) is int]
        failed = [i for i in items if 'Recovery' in str(i.get('state', '')) or
            i.get('state') in {'Verification Unavailable', 'Needs Attention', 'Publishing prerequisite needed'}]
        for item in items:
            observations[item['id']] = (run, stamp, item if item in failed else None)
        scope = tuple(sorted(data['scope'])) if isinstance(data.get('scope'), list) else None
        if (run.error or run.state == 'failed') and not failed:
            # Unattributed connection/worker failures cannot be cleared by a
            # successful check of a different product or an incomplete slice.
            unbound[scope] = run
        elif (run.state == 'completed' and not run.error and not data.get('errors')
                and not data.get('queue') and not data.get('counts', {}).get('deferred')):
            unbound.pop(scope, None)
            if scope is None:
                unbound.clear()

    # Include current source/recovery findings even if a newer narrow report
    # omits them. JSON predicates examine persisted views, never source files.
    safe_view = case((func.json_valid(ShopifyPublishJob.view_json), ShopifyPublishJob.view_json), else_='{}')
    raw = func.json_extract(safe_view, '$.state.state')
    retained_review = select(ShopifyProductIdentity.id).where(
        ShopifyProductIdentity.product_id == Product.id,
        ShopifyProductIdentity.state.notin_({'verified', 'remote_deleted', 'failed_before_create', 'processing', 'scoped_verified'})).exists()
    conflicting = select(ShopifyProductIdentity.product_id).group_by(
        ShopifyProductIdentity.product_id).having(func.count() > 1)
    targets = set(observations)
    for scope in unbound:
        if scope is not None:
            targets.update(scope)
    query = Product.query.outerjoin(ShopifyPublishJob, ShopifyPublishJob.product_id == Product.id).filter(
        Product.catalogue_status == 'active', or_(Product.id.in_(targets),
            ShopifyPublishJob.state.in_({'attention', 'recovery_required'}),
            raw.in_({'unavailable', 'attention', 'recovery_required'}), retained_review, Product.id.in_(conflicting)))
    products = query.all()
    from app.shopify_connection import ShopifyError
    try:
        states = saved_states(products, url_builder=destination) if products else {}
    except (ShopifyError, ValueError, TypeError, KeyError, RuntimeError):
        states = {}
        for product in products:
            try:
                states.update(saved_states([product], url_builder=destination))
            except (ShopifyError, ValueError, TypeError, KeyError, RuntimeError):
                states[product.id] = {'state':'recovery_required',
                    'message':'Retained product evidence could not be interpreted; inspect its original receipt.'}
    issues = []
    for product in products:
        state = states[product.id]
        previous = observations.get(product.id)
        run, stamp, item = previous if previous else (None, None, None)
        current_issue = state['state'] in {'unavailable', 'attention', 'recovery_required'}
        # Freshness expiry cannot resurrect a superseded read incident; retained
        # uncertain identities remain protected by saved_states above.
        resolved = resolved_after(state, stamp)
        if current_issue or item and not resolved:
            issues.append({'product_id': product.id, 'sku': product.sku,
                'reason': state.get('message') if current_issue else item.get('reason') or item.get('state'),
                'run_id': run.id if run else None})
    if issues:
        first = issues[0]
        return {'key': tasks.KEY, 'run_id': first['run_id'], 'attention': True,
            'label': tasks.DEFINITION['name'] + f' — needs attention ({len(issues)} '+('product)' if len(issues)==1 else 'products)'),
            'reason': first['sku'] + ': ' + (first['reason'] or 'Inspect saved product evidence.'),
            'destination': destination('main.operation_issues_page', category='shopify')}
    unbound = {scope:run for scope,run in unbound.items() if not (scope and all(
        resolved_after(states.get(product_id, {}), run.completed_at or run.started_at or run.created_at)
        for product_id in scope))}
    if unbound:
        run = max(unbound.values(), key=lambda r: (r.created_at, r.id))
        return {'key': tasks.KEY, 'run_id': run.id, 'attention': True,
            'label': tasks.DEFINITION['name'] + ' — execution needs attention',
            'reason': run.error or 'Required reconciliation execution was not completed.'}
    return None
