"""Bounded merchant-facing queue/events. Persisted data only; no catalogue walks."""
import json
from flask import request, url_for
from sqlalchemy import or_, func, select, case, literal, desc
from app import db
from app.models import Product, Collection, ShopifyPublishJob, ShopifyProductIdentity, ShopifyPublishingEvent, CatalogueOperation
from app.sale_editor import uk_time


def document(raw):
    try:
        value = json.loads(raw or '{}')
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError): return {}


def local_time(value):
    return uk_time(value.isoformat() if hasattr(value, 'isoformat') else value) if value else 'Not yet'


def queue():
    query = ShopifyPublishJob.query.join(Product, Product.id == ShopifyPublishJob.product_id)
    state, trigger, q = (request.args.get(k, '').strip()[:120] for k in ('state', 'trigger', 'q'))
    if state: query = query.filter(ShopifyPublishJob.state == state)
    if trigger: query = query.filter(ShopifyPublishJob.trigger == trigger)
    if q: query = query.filter(or_(Product.title.contains(q, autoescape=True), Product.sku.contains(q, autoescape=True), ShopifyPublishJob.source_identity.contains(q, autoescape=True), ShopifyPublishJob.error.contains(q, autoescape=True)))
    page = query.order_by(ShopifyPublishJob.priority.desc(), ShopifyPublishJob.updated_at.desc(), ShopifyPublishJob.product_id).paginate(
        page=max(1, request.args.get('page', 1, type=int)), per_page=10, error_out=False)
    ids = [job.product_id for job in page.items]
    products = {p.id: p for p in Product.query.filter(Product.id.in_(ids))}
    from app.catalogue_views import saved_states
    states = saved_states(products.values())
    identities = {}
    for row in ShopifyProductIdentity.query.filter(ShopifyProductIdentity.product_id.in_(ids)):
        identities.setdefault(row.product_id, []).append(row)
    operations = {op.id: op for op in CatalogueOperation.query.filter(CatalogueOperation.id.in_([j.operation_id for j in page.items if j.operation_id]))}
    collections = {c.id: c for c in Collection.query.filter(Collection.id.in_([p.collection_id for p in products.values() if p.collection_id]))}
    rows = []
    for job in page.items:
        product = products[job.product_id]
        view = document(job.view_json)
        authored = view.get('authored') or {}
        if not isinstance(authored, dict): authored = {}
        from app.shopify_publication import intent
        try: intended = intent(authored)['status'].title() if authored else 'Not projected'
        except ValueError: intended = 'Configuration conflict'
        links = identities.get(product.id, [])
        identity = links[0] if len(links) == 1 else None
        from app import shopify_drafts as drafts
        from app.automatic_publishing import no_changes_settlement
        evidence = drafts.evidence(identity) if identity else {}
        actual = evidence.get('observed_status') if identity and identity.verification_state == 'verified' else None
        settlement = no_changes_settlement(job)
        settled = bool(settlement and states[product.id]['state'] == 'no_change')
        operation = operations.get(job.operation_id)
        collection = collections.get(product.collection_id)
        rows.append({'job': job, 'product': product, 'title': view.get('display_title') or product.title,
            'intended': intended, 'actual': actual or 'Not verified', 'operation': operation,
            'no_changes': settled, 'draft_paused': intended == 'Draft',
            'settlement_label': 'No Changes' if settled else states[product.id]['label'] if settlement else None,
            'collection': collection, 'scan_required': not settled and intended != 'Draft' and (job.requested_revision == 'unscanned' or 'scan' in (job.error or '').lower()),
            'source': collection.source_relpath if collection else None})
    return {'rows': rows, 'page': page, 'filters': {'state': state, 'trigger': trigger, 'q': q}}


def event_query():
    # Original operations remain authoritative, including records predating the
    # event projection. At most one latest event projection per operation.
    event = ShopifyPublishingEvent
    latest = select(event.id).where(event.operation_id == CatalogueOperation.id).order_by(
        event.occurred_at.desc(), event.id.desc()).limit(1).correlate(CatalogueOperation).scalar_subquery()
    safe_scope = case((func.json_valid(CatalogueOperation.scope), CatalogueOperation.scope), else_='{}')
    query = db.session.query(CatalogueOperation, event).outerjoin(event, event.id == latest).filter(or_(
        CatalogueOperation.operation_type == 'shopify_draft_publish',
        func.json_extract(safe_scope, '$.release_event').isnot(None)))
    return query, event


def event_row(operation, event):
    scope, receipt = document(operation.scope), document(event.receipt_json) if event else {}
    context = scope.get('automatic_publishing') or {}
    if not isinstance(context, dict): context = {}
    from app.shopify_notifications import KEY
    delivery = scope.get(KEY) or {}
    if not isinstance(delivery, dict): delivery = {}
    from app.operation_reporting import interpretation
    report = interpretation(operation)
    if operation.operation_type=='shopify_draft_publish' and scope.get('manual_review'):
        from app.publishing_progress import view
        health=view(operation)['status']
        if health in {'queued','running','waiting','stalled','recovery_required'}:
            report={**report,'label':health.replace('_',' ').title()+' — server task'}
    summary = scope.get('operation_summary') or {}
    result = {'operation': operation, 'event': event, 'receipt': receipt,
        'product_id': event.product_id if event else scope.get('product_id'),
        'title': receipt.get('title') or scope.get('sku') or 'Historical product operation',
        'sku': receipt.get('sku') or scope.get('sku') or 'Not retained',
        'trigger': event.trigger if event else context.get('trigger') or ('release' if scope.get('release_event') else 'manual_review'),
        'kind': 'gallery_upload_continuation' if isinstance(summary,dict) and summary.get('stage')=='gallery_uploading' else receipt.get('mode') or scope.get('mode') or ('release' if scope.get('release_event') else 'create'),
        'result': report['label'],
        'verification': receipt.get('verification_scope') or 'Detailed verification scope unavailable; inspect original receipt.',
        'changes': [c for c in (receipt.get('changes') or []) if isinstance(c, dict) and {'label','before','after'}.issubset(c)],
        'discord': ', '.join(sorted({value.get('state', 'unknown') for value in delivery.values() if isinstance(value, dict)})) or 'Not recorded',
        'occurred_at': operation.finished_at or operation.started_at}
    return result


def events():
    from app.models import ScheduledTaskRun
    query, event = event_query()
    filters = {k: request.args.get(k, '').strip()[:120] for k in ('q', 'result', 'trigger')}
    if filters['q']:
        query = query.filter(or_(event.receipt_json.contains(filters['q'], autoescape=True), CatalogueOperation.scope.contains(filters['q'], autoescape=True), CatalogueOperation.id.contains(filters['q'], autoescape=True)))
    if filters['result'] == 'recovery_required': query = query.filter(CatalogueOperation.recovery_state.notin_(['none']))
    elif filters['result']: query = query.filter(CatalogueOperation.status == filters['result'])
    if filters['trigger']:
        safe_scope = case((func.json_valid(CatalogueOperation.scope), CatalogueOperation.scope), else_='{}')
        trigger = func.coalesce(event.trigger, func.json_extract(safe_scope, '$.automatic_publishing.trigger'),
            case((func.json_extract(safe_scope, '$.release_event').isnot(None), 'release'), else_='manual_review'))
        query = query.filter(trigger == filters['trigger'])
    # Merge existing ledgers at the SQL pagination boundary, not in an unbounded
    # Python list. Product receipts and scheduler executions remain distinct.
    runs = ScheduledTaskRun.query.filter(ScheduledTaskRun.task_key.in_({'shopify_publisher', 'shopify_products'}))
    if filters['q']:
        runs = runs.filter(or_(ScheduledTaskRun.id.contains(filters['q'], autoescape=True),
            ScheduledTaskRun.task_key.contains(filters['q'], autoescape=True),
            ScheduledTaskRun.data_json.contains(filters['q'], autoescape=True)))
    if filters['result']:
        runs = runs.filter(ScheduledTaskRun.state == {
            'succeeded': 'completed', 'partial': 'partially_completed'}.get(filters['result'], filters['result']))
    if filters['trigger']:
        if filters['trigger'] == 'hourly':
            runs = runs.filter(ScheduledTaskRun.task_key == 'shopify_publisher', ScheduledTaskRun.trigger == 'scheduled')
        elif filters['trigger'] == 'reconciliation':
            runs = runs.filter(ScheduledTaskRun.task_key == 'shopify_products')
        elif filters['trigger'] == 'manual':
            runs = runs.filter(ScheduledTaskRun.trigger == 'manual')
        else:
            runs = runs.filter(literal(False))
    combined = query.with_entities(literal('operation').label('kind'), CatalogueOperation.id.label('record_id'),
        func.coalesce(CatalogueOperation.finished_at, CatalogueOperation.started_at).label('occurred_at')).union_all(
        runs.with_entities(literal('task'), ScheduledTaskRun.id,
            func.coalesce(ScheduledTaskRun.completed_at, ScheduledTaskRun.started_at, ScheduledTaskRun.created_at)))
    page = combined.order_by(desc('occurred_at'), desc('record_id')).paginate(
        page=max(1, request.args.get('page', 1, type=int)), per_page=10, error_out=False)
    rows = []
    for item in page.items:
        if item.kind == 'task':
            run = db.session.get(ScheduledTaskRun, item.record_id)
            from app.scheduled_tasks import definition
            data = document(run.data_json)
            details = task_summary(run)
            rows.append({'title': definition(run.task_key)['name'], 'sku': 'Scheduler execution — not a product receipt',
                'url': url_for('tasks.run_detail', run_id=run.id), 'occurred_at': item.occurred_at,
                'trigger': 'hourly' if run.task_key == 'shopify_publisher' and run.trigger == 'scheduled' else run.trigger + ' reconciliation' if run.task_key == 'shopify_products' else run.trigger,
                'kind': 'publisher_cycle' if run.task_key == 'shopify_publisher' else 'read_only_reconciliation',
                'result': run.state.replace('_', ' ').title(), 'verification': details,
                'changes': [], 'task_run': True, 'discord': 'No routine cycle notification'})
        else:
            op, e = event_query()[0].filter(CatalogueOperation.id == item.record_id).one()
            row = event_row(op, e)
            row['url'] = url_for('publishing.event_detail', operation_id=op.id)
            rows.append(row)
    return {'rows': rows, 'page': page, 'filters': filters}


def task_summary(run):
    data = document(run.data_json)
    counts = data.get('counts', {})
    parts = [f'{key.replace("_", " ")}: {counts[key]}' for key in (
        'considered', 'checked', 'up_to_date', 'updates', 'unavailable', 'attention', 'deferred', 'skipped_recent', 'api_requests') if key in counts]
    if run.task_key == 'shopify_products':
        parts.append('Read-only Shopify comparison; no publication or local scan')
        if 'comparison_outcomes' in data:
            parts.append('Comparison outcomes: ' + ', '.join(key.replace('_', ' ') + ': ' + str(value)
                for key, value in data['comparison_outcomes'].items()))
        else:
            parts.append('Detailed historical comparison outcomes were not recorded')
    elif 'eligibility' in data:
        parts.append('Saved eligibility at cycle start: ' + ', '.join(
            key.replace('_', ' ') + ': ' + str(value) for key, value in data['eligibility'].items()))
        parts.append('Verified publication outcomes: ' + (', '.join(
            key.replace('_', ' ') + ': ' + str(value) for key, value in data.get('publication_counts', {}).items()) or 'none'))
    else:
        parts.append('Historical selection/exclusion details were not recorded; no counts inferred')
    if data.get('reason'): parts.append(data['reason'])
    if run.error: parts.append(run.error)
    return ' · '.join(parts)


def product_authority(product_id):
    job = db.session.get(ShopifyPublishJob, product_id) if product_id else None
    if not job: return None
    scan = db.session.get(CatalogueOperation, job.scan_operation_id) if job.scan_operation_id else None
    view = document(job.view_json)
    state = view.get('state', {})
    return {'job': job, 'scan': scan, 'validation': view.get('checkpoint_validation'),
        'comparison_at': state.get('compared_at'),
        'explanation': ('Local Draft — no automatic publication' if state.get('state') == 'draft' or job.state == 'paused' else
            'Up to Date — last complete Shopify comparison matched; no queued write' if job.state == 'verified' and state.get('state') == 'no_change' else
            'Publishing blocked — legacy checkpoint requires explicit validation' if job.requested_revision == 'unscanned' else
            job.error or 'Current source checkpoint retained; normal publisher safeguards still apply')}
