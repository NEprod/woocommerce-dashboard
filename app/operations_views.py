"""Bounded merchant-facing queue/events. Persisted data only; no catalogue walks."""
import json
from flask import request, url_for
from sqlalchemy import or_, func, select, case
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
    if q: query = query.filter(or_(Product.title.contains(q, autoescape=True), Product.sku.contains(q, autoescape=True), ShopifyPublishJob.error.contains(q, autoescape=True)))
    page = query.order_by(ShopifyPublishJob.priority.desc(), ShopifyPublishJob.updated_at.desc(), ShopifyPublishJob.product_id).paginate(
        page=max(1, request.args.get('page', 1, type=int)), per_page=10, error_out=False)
    ids = [job.product_id for job in page.items]
    products = {p.id: p for p in Product.query.filter(Product.id.in_(ids))}
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
        evidence = document(identity.media_evidence_json) if identity else {}
        actual = evidence.get('observed_status') if identity and identity.verification_state == 'verified' else None
        operation = operations.get(job.operation_id)
        collection = collections.get(product.collection_id)
        rows.append({'job': job, 'product': product, 'title': view.get('display_title') or product.title,
            'intended': intended, 'actual': actual or 'Not verified', 'operation': operation,
            'collection': collection, 'scan_required': job.requested_revision == 'unscanned' or 'scan' in (job.error or '').lower(),
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
    page = query.order_by(func.coalesce(CatalogueOperation.finished_at, CatalogueOperation.started_at).desc(), CatalogueOperation.id.desc()).paginate(
        page=max(1, request.args.get('page', 1, type=int)), per_page=10, error_out=False)
    return {'rows': [event_row(op, e) for op, e in page.items], 'page': page, 'filters': filters}
