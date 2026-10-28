"""Rebuildable scan/worker presentation in the publishing queue, never JSON authority.

GET readers do not walk source folders, hash images, mutate evidence or queue work.
Unknown pre-upgrade health stays explicitly incomplete until bounded projection.
"""
import json
from functools import wraps
from datetime import datetime

from flask import current_app, g, has_request_context, request, url_for
from sqlalchemy import or_, update, func, case

from app import db, shopify_drafts as drafts
from app.models import Product, ShopifyPublishJob, ShopifyProductIdentity, ScheduledTask, ScheduledTaskRun, CatalogueOperation

KEY = 'catalogue_views'
DEFINITION = {'name': 'Saved Catalogue Views', 'description': 'Bounded derived status/source health for existing indexed products. Never scans folders or publishes.'}


def active():
    return has_request_context() and request.method == 'GET' and bool(getattr(g, '_saved_catalogue_read', False))


def saved_read_view(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not has_request_context() or request.method != 'GET':
            return function(*args, **kwargs)
        previous = getattr(g, '_saved_catalogue_read', False)
        g._saved_catalogue_read = True
        try:
            return function(*args, **kwargs)
        finally:
            g._saved_catalogue_read = previous
    return wrapped


def context_digest():
    """One registry snapshot and bounded mapping reads, never product folders."""
    if has_request_context() and hasattr(g, '_catalogue_context'):
        return g._catalogue_context
    from app.taxonomy_registry import load_configured_registry
    from app.models import ShopifyCollectionIdentity, ShopifyAttributeIdentity
    registry = load_configured_registry()
    from app.shopify_comparison import mapping_digest, attribute_mapping_digest
    try: config = drafts.configuration().fingerprint()
    except ValueError: config = None
    value = drafts.digest({'registry': registry.snapshot.digest if registry.available else registry.status,
        'config': config,
        'collections': sorted((r.store_id, r.source_key, mapping_digest(r)) for r in ShopifyCollectionIdentity.query.all()),
        'attributes': sorted((r.store_id, r.source_key, attribute_mapping_digest(r)) for r in ShopifyAttributeIdentity.query.all())})
    if has_request_context(): g._catalogue_context = value
    return value


def refresh_context():
    """Worker-only invalidation after registry/connection/mapping changes."""
    value = context_digest()
    db.session.execute(update(ShopifyPublishJob).where(ShopifyPublishJob.view_pending.is_(False),
        or_(func.json_extract(ShopifyPublishJob.view_json, '$.context').is_(None),
            func.json_extract(ShopifyPublishJob.view_json, '$.context') != value)).values(view_pending=True))
    db.session.commit()


def jobs_for(products):
    products = list(products)
    cache = getattr(g, '_catalogue_jobs', None) if has_request_context() else None
    if cache is None:
        cache = {}
        if has_request_context(): g._catalogue_jobs = cache
    ids = [p.id for p in products if p.id not in cache]
    if ids:
        cache.update({key: None for key in ids})
        cache.update({j.product_id: j for j in ShopifyPublishJob.query.filter(ShopifyPublishJob.product_id.in_(ids)).all()})
    return cache


def document(product):
    job = jobs_for([product]).get(product.id)
    try:
        return json.loads(job.view_json)['authored'] if job and job.view_json else {}
    except (ValueError, TypeError, KeyError):
        return {}


def display_title(product):
    job = jobs_for([product]).get(product.id)
    try:
        return (json.loads(job.view_json).get('display_title') if job and job.view_json else None) or product.title
    except (ValueError, TypeError):
        return product.title


def thumbnail(product):
    job = jobs_for([product]).get(product.id)
    return url_for('main.catalogue_product_image', product_id=product.id) if (
        job and job.image_available is True and product.catalogue_status == 'active') else None


def pending_count():
    return Product.query.outerjoin(ShopifyPublishJob, Product.id == ShopifyPublishJob.product_id).filter(
        Product.catalogue_status == 'active', or_(ShopifyPublishJob.projected_at.is_(None),
        ShopifyPublishJob.image_available.is_(None))).count()


def capture(product, *, scan_operation=None, context=None):
    from app.taxonomy_assignments import product_document
    from app.catalogue_images import resolve_product_catalogue_image
    from app.shopify_products import states_for
    job = db.session.get(ShopifyPublishJob, product.id, populate_existing=True)
    if not job:
        return False
    expected = job.requested_revision
    try:
        image = resolve_product_catalogue_image(product) is not None
    except (ValueError, OSError, RuntimeError):
        image = None
    try:
        authored = product_document(product)
        from app.metadata_workspace import current_title
        title = current_title(product, resolved=authored)
        adapter = current_app.url_map.bind('')
        state = states_for([product], fresh=True, url_builder=lambda endpoint, **values: adapter.build(endpoint, values))[product.id]
        try: config = drafts.configuration().fingerprint()
        except ValueError: config = None
        data = {'version': 1, 'authored': authored, 'display_title': title, 'state': state, 'config': config,
            'context': context or context_digest()}
        from app.automatic_publishing import settle_obsolete_attention, no_changes_settlement
        if state['state'] == 'no_change':
            settle_obsolete_attention(product)
        settlement = no_changes_settlement(job)
        if settlement and state['state'] == 'no_change':
            data['queue_no_changes'] = settlement
        elif settlement:
            # A historical no-op never turns subsequent differences into scanned work.
            job.state = 'attention'
    except (ValueError, OSError, RuntimeError) as exc:
        from app.utils.operation_control import sanitize_operation_error
        data = {'version': 1, 'authored': {}, 'state': {'state': 'unavailable',
            'label': 'Verification Unavailable', 'message': sanitize_operation_error(exc)}, 'config': None}
    values = dict(view_json=json.dumps(data, ensure_ascii=False, default=str), image_available=image,
        projected_at=drafts.now(), view_revision=expected, view_pending=False)
    if scan_operation:
        values['scan_operation_id'] = scan_operation
    changed = db.session.execute(update(ShopifyPublishJob).where(ShopifyPublishJob.product_id == product.id,
        ShopifyPublishJob.requested_revision == expected).values(**values)).rowcount
    return changed == 1


def invalidate(*, product_id=None, source=None, all_products=False):
    query = update(ShopifyPublishJob)
    if not all_products:
        if product_id is not None: query = query.where(ShopifyPublishJob.product_id == product_id)
        elif source is not None: query = query.where(ShopifyPublishJob.source_identity == source)
        else: return
    db.session.execute(query.values(view_pending=True))
    db.session.execute(update(ScheduledTask).where(ScheduledTask.key == KEY).values(next_due=drafts.now()))


def saved_states(products, *, url_builder=None):
    url_builder = url_builder or url_for
    products = list(products)
    current_context = context_digest()
    jobs = jobs_for(products)
    rows = ShopifyProductIdentity.query.filter(ShopifyProductIdentity.product_id.in_([p.id for p in products])).all()
    active_operations = {op_id for op_id, in db.session.query(CatalogueOperation.id).filter(
        CatalogueOperation.id.in_([r.operation_id for r in rows if r.operation_id]),
        CatalogueOperation.status.in_({'running', 'pending'}))}
    try: config = drafts.configuration().fingerprint()
    except ValueError: config = None
    result = {}
    for product in products:
        job = jobs.get(product.id)
        try: saved = json.loads(job.view_json) if job and job.view_json else {}
        except (ValueError, TypeError): saved = {}
        state = {'product_id': product.id, 'state': 'unknown', 'label': 'Status projection pending',
            'remote_id': None, 'remote_status': 'Unknown', 'publication': 'Not verified', 'verified_at': None,
            'identity_url': None, 'store_id': None, 'shopify_url': None, 'intent': 'Unresolved',
            'intent_value': None, 'compared_at': None, 'change_count': None, 'change_summary': [],
            'blockers': [], 'revision_checked_at': None,
            'message': 'Saved source/status health is incomplete. Existing background projection or a completed collection scan is required.'}
        state.update(saved.get('state', {}))
        state['review_url'] = url_builder('commerce.drafts', product_id=product.id) if product.catalogue_status == 'active' else None
        state['projected_at'] = job.projected_at.isoformat() + 'Z' if job and job.projected_at else None
        identities = [row for row in rows if row.product_id == product.id]
        if len(identities) == 1:
            identity = identities[0]
            evidence = drafts.evidence(identity)
            state.update(remote_id=identity.shopify_product_id, store_id=identity.store_id,
                identity_url=url_builder('commerce.draft_identity', identity_id=identity.id))
            uncertain = (any(p.get('state') in {'pending', 'uncertain'} for p in evidence.get('package_assignments', {}).values())
                or any(p.get('state') in {'pending', 'uncertain'} for p in evidence.get('variant_native', {}).values())
                or evidence.get('native_dimensions', {}).get('state') in {'pending', 'uncertain'})
            if identity.operation_id in active_operations:
                state.update(state='processing', label='Processing', execution_pending=True)
            elif drafts.upload_pending(identity):
                state.update(state='processing', label='Uploading gallery', execution_pending=True,
                    message='Acknowledged uploads retained for the next authorised slice; no product Create attempted.')
            elif uncertain or identity.state not in {'verified', 'remote_deleted', 'failed_before_create', 'processing', 'scoped_verified', 'attribute_review_required'}:
                state.update(state='recovery_required', label='Recovery Required', message='Retained remote operation requires safe recovery; no automatic retry.')
            elif identity.state == 'processing':
                state.update(state='processing', label='Processing', execution_pending=True)
        elif len(identities) > 1:
            state.update(state='recovery_required', label='Recovery Required', message='Multiple retained Shopify identities require review.')
        protected = state['state'] in {'recovery_required', 'processing'}
        view_current = bool(job and not job.view_pending and job.view_revision == job.requested_revision
            and saved.get('config') == config and saved.get('context') == current_context)
        compared_matching = state['state'] == 'no_change' and view_current
        if not protected and job:
            if job.state == 'recovery_required':
                state.update(state='recovery_required', label='Recovery Required', message=job.error or 'Original publishing receipt requires recovery.')
            elif job.state == 'attention' and job.requested_revision != 'unscanned' and config is not None:
                state.update(state='attention', label='Scan Required' if (job.error or '').startswith(
                    'Local source changed after its completed scan.') else 'Needs Attention', message=job.error or state['message'])
            elif job.owner:
                state.update(state='processing', label='Processing', execution_pending=True)
            elif job.state == 'paused' and not identities:
                state.update(state='draft', label='Local Draft', message='Draft remains local; no automatic creation is requested.')
            elif (job.verified_revision != job.requested_revision and job.requested_revision != 'unscanned'
                    and not compared_matching and not (view_current and state['state'] == 'update')
                    and state['state'] not in {'attention', 'unavailable', 'local_changes'}):
                state.update(state='update' if identities else 'create', label='Update Needed' if identities else 'Needs Creating',
                    message='Scanned local changes are pending.' if job.state != 'paused' else 'Draft metadata is paused; accumulated local changes remain pending.')
            if (job.view_pending or job.view_revision != job.requested_revision or saved.get('config') != config
                    or saved.get('context') != current_context):
                if state['state'] == 'no_change':
                    state.update(state='local_changes', label='Status refresh pending', message='Current saved status requires refresh; no new remote equality is claimed.')
            timestamp = state.get('compared_at') or state.get('verified_at')
            try:
                from app.shopify_comparison import MAX_AGE
                if timestamp and drafts.now() - datetime.fromisoformat(timestamp.removesuffix('Z')) > MAX_AGE:
                    state['evidence_stale'] = True
                    if state['state'] == 'no_change': state['label'] = 'Last check matched'
            except (ValueError, TypeError):
                pass
        result[product.id] = state
    return result


def run_slice(run_id, owner):
    from app import scheduled_tasks as tasks
    run = db.session.get(ScheduledTaskRun, run_id)
    data = json.loads(run.data_json)
    tasks.renew(KEY, run_id, owner)
    context = context_digest()
    missing = Product.query.outerjoin(ShopifyPublishJob, Product.id == ShopifyPublishJob.product_id).filter(
        Product.catalogue_status == 'active', ShopifyPublishJob.product_id.is_(None)).limit(25).all()
    for product in missing:
        db.session.add(ShopifyPublishJob(product_id=product.id, source_identity=product.source_relpath or '',
            requested_revision='unscanned', state='attention', trigger='bootstrap', generation=1, priority=0, attempts=0,
            next_attempt=drafts.now(), updated_at=drafts.now(), view_pending=True,
            error='A completed collection scan is required before automatic publishing.'))
    tasks.fence(KEY, run_id, owner); db.session.commit()
    jobs = ShopifyPublishJob.query.filter_by(view_pending=True).order_by(ShopifyPublishJob.product_id).limit(25).all()
    for job in jobs:
        tasks.renew(KEY, run_id, owner)
        product = db.session.get(Product, job.product_id)
        if product: capture(product, context=context)
        tasks.fence(KEY, run_id, owner); db.session.commit()
        data['counts']['checked'] += 1
    # One bounded pass over retained Attention entries, including pre-upgrade
    # projections already marked current. This is the existing view worker, not
    # publisher eligibility or GET work; no scan or remote request is performed.
    attention = ShopifyPublishJob.query.filter(ShopifyPublishJob.state == 'attention',
        ShopifyPublishJob.owner.is_(None), ShopifyPublishJob.product_id > data.get('attention_cursor', 0),
        or_((ShopifyPublishJob.requested_revision == 'unscanned') & (ShopifyPublishJob.trigger == 'bootstrap'),
            ShopifyPublishJob.error.startswith('Local source changed after its completed scan.')),
        func.json_extract(case((func.json_valid(ShopifyPublishJob.view_json), ShopifyPublishJob.view_json),
                               else_='{}'), '$.state.state') == 'no_change'
        ).order_by(ShopifyPublishJob.product_id).limit(25).all()
    for job in attention:
        tasks.renew(KEY, run_id, owner)
        product = db.session.get(Product, job.product_id)
        if product: capture(product, context=context)
        data['attention_cursor'] = job.product_id
        tasks.fence(KEY, run_id, owner); db.session.commit()
    remains = ShopifyPublishJob.query.filter_by(view_pending=True).count()
    tasks.save_progress(KEY, run_id, owner, data, pause=True) if remains or len(missing) == 25 or len(attention) == 25 else tasks.save_progress(
        KEY, run_id, owner, data, terminal='completed')
