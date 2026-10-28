"""Durable opt-in publisher. Reuses the manual executors, never manual signatures.

All remote work is in the existing scheduler process. JSON remains authoritative;
jobs are coalesced desired work, not a replacement product configuration.
"""
import json
from datetime import timedelta

from sqlalchemy import update, case
from sqlalchemy.dialects.sqlite import insert

from app import db, shopify_drafts as drafts
from app.models import (AutomaticPublishing, ShopifyPublishJob, Product, User, ShopifyProductIdentity,
                        ScheduledTask, ScheduledTaskRun, CatalogueOperation)
from app.shopify_capacity import background_work, BackgroundYield
from app.shopify_connection import ShopifyAdapter, ShopifyError
from app.taxonomy_assignments import product_document
from app.utils.operation_control import CatalogueOperationActive, sanitize_operation_error

KEY = 'shopify_publisher'
REQUIRED_FIELDS = frozenset({'title', 'description', 'short_description', 'price', 'weight',
    'dimensions', 'meta_title', 'meta_description', 'customisation', 'country_of_origin', 'images'})
DEFINITION = {'name': 'Automatic Shopify Publisher',
    'description': 'Opt-in hourly publication of scanned Live changes; priority Draft transitions and manual Sync Now use the same queue.'}


def policy():
    return db.session.get(AutomaticPublishing, 'shopify', populate_existing=True)


def require_admin(actor_id):
    actor = db.session.get(User, actor_id)
    if not actor or not actor.is_admin:
        raise ValueError('An authenticated administrator must authorise publishing.')


def configure(*, enabled, actor_id, confirmation=None, required_fields=None, adapter=None):
    require_admin(actor_id)
    if type(enabled) is not bool:
        raise ValueError('Choose an explicit automatic publishing state.')
    row = policy()
    if required_fields is not None:
        if (not isinstance(required_fields, list) or any(type(k) is not str or k not in REQUIRED_FIELDS for k in required_fields)
                or len(set(required_fields)) != len(required_fields)):
            raise ValueError('Choose only supported TLC Live-required fields.')
    if enabled:
        config = drafts.configuration()
        if confirmation != config.domain:
            raise ValueError('Explicitly confirm the connected store domain before enabling automatic publishing.')
        shop = (adapter or ShopifyAdapter()).test_connection(config)
        if 'write_products' not in shop['scopes']:
            raise ValueError('Shopify write_products is required. No authority was granted.')
        row.config_digest, row.store_id, row.domain = config.fingerprint(), shop['shop_id'], config.domain
    # Pause always works, even if the Shopify connection is unavailable.
    row.enabled, row.actor_id, row.updated_at = enabled, actor_id, drafts.now()
    row.revision += 1
    if required_fields is not None:
        row.required_fields_json = json.dumps(required_fields)
    task = db.session.get(ScheduledTask, KEY)
    task.enabled = enabled
    if enabled:
        task.next_due = drafts.now()
        db.session.execute(update(ShopifyPublishJob).where(ShopifyPublishJob.owner.is_(None),
            ShopifyPublishJob.state.notin_({'recovery_required', 'verified'}),
            ShopifyPublishJob.requested_revision != 'unscanned').values(state='pending', next_attempt=drafts.now()))
    if not task.active_run_id:
        task.state = 'idle' if enabled else 'disabled'
    db.session.commit()
    return row


def local_revision(product):
    """Existing canonical managed fingerprint plus all resolved variant sources.

    This runs on ingestion or in a bounded worker, never on catalogue navigation.
    JSON whitespace/order is immaterial. The signed executor still hashes bytes.
    """
    from app.catalogue_images import product_image_diagnostics, variation_image_diagnostics
    from app.shopify_comparison import fingerprint
    images = product_image_diagnostics(product)
    # Match the approved variable gallery order, not SQL relationship load order.
    for variant in sorted(product.variations, key=lambda v: (v.menu_order or 0, v.id)):
        if variant.catalogue_status == 'active':
            images += variation_image_diagnostics(variant, include_parent_fallback=False)
    references = list(dict.fromkeys(i['source_reference'] for i in images
                                   if i.get('source_exists') and i.get('source_reference')))
    return fingerprint(product, [{'source': ref} for ref in references])


def queue_product(product, *, trigger='scan', priority=0):
    """Upsert one desired revision; never rewrite the revision already executing."""
    from app.shopify_publication import intent
    stamp = drafts.now()
    try:
        revision = local_revision(product)
        wanted = intent(product_document(product))
        state, error = 'pending', None
        identities = ShopifyProductIdentity.query.filter_by(product_id=product.id).all()
        if wanted['status'] != 'ACTIVE':
            state = 'paused'
            if len(identities) == 1 and identities[0].shopify_product_id:
                actual = drafts.evidence(identities[0]).get('observed_status')
                if actual != 'DRAFT' and wanted['status'] == 'DRAFT':
                    state, priority = 'pending', max(priority, 3)
    except (ShopifyError, ValueError, OSError, RuntimeError) as exc:
        # Persist an actionable invalid-source observation, never a verified revision.
        revision = drafts.digest({'invalid': product.resolved_row_json, 'source': product.source_relpath})
        state, error = 'attention', sanitize_operation_error(exc)
    table = ShopifyPublishJob.__table__
    values = dict(product_id=product.id, source_identity=product.source_relpath or '', requested_revision=revision,
        generation=1, state=state, trigger=trigger, priority=priority, attempts=0, next_attempt=stamp,
        updated_at=stamp, error=error, view_pending=True)
    db.session.execute(insert(table).values(**values).on_conflict_do_update(index_elements=[table.c.product_id],
        set_={**{k: v for k, v in values.items() if k not in {'product_id', 'generation'}},
              'generation': table.c.generation + 1,
              'state': case((table.c.owner.isnot(None), table.c.state),
                            (table.c.state == 'recovery_required', table.c.state), else_=state),
              'manual_actor_id': None, 'manual_revision': None, 'manual_config': None},
        where=(table.c.requested_revision != revision) | (table.c.state == 'attention')))
    if error is None:
        from app.publishing_due import index_product
        index_product(product)
    # Caller owns the source/ingestion transaction.
    return revision


def sync_now(product, *, actor_id, acknowledged):
    require_admin(actor_id)
    if acknowledged is not True:
        raise ValueError('Explicit Sync Now acknowledgement is required.')
    job = db.session.get(ShopifyPublishJob, product.id, populate_existing=True)
    if not job or local_revision(product) != job.requested_revision:
        raise ValueError('Local source has no current completed scan revision. Run its containing-collection update scan before Sync Now.')
    if job.state == 'recovery_required':
        raise ValueError('Resolve the existing uncertain operation before Sync Now.')
    job.manual_actor_id, job.manual_revision = actor_id, job.requested_revision
    job.manual_config = drafts.configuration().fingerprint()
    job.priority, job.trigger, job.next_attempt = 2, 'manual', drafts.now()
    if not job.owner:
        job.state = 'pending'
    db.session.commit()
    from app.scheduled_tasks import enqueue
    enqueue(KEY, manual=True, product_ids=[product.id])
    return job


def authority(job, config, *, expected_policy=None):
    """Rechecked before every mutation; ordinary task enablement grants nothing."""
    db.session.refresh(job)
    if (job.manual_actor_id and job.manual_revision == job.running_revision
            and job.manual_config == config.fingerprint()):
        require_admin(job.manual_actor_id)
        return {'kind': 'manual_sync_now', 'actor': job.manual_actor_id}
    row = policy()
    if (not row or not row.enabled or row.config_digest != config.fingerprint()
            or expected_policy is not None and row.revision != expected_policy):
        raise BackgroundYield('Automatic publishing is paused or its store/configuration authority changed.')
    require_admin(row.actor_id)
    return {'kind': 'automatic', 'revision': row.revision, 'actor': row.actor_id, 'store_id': row.store_id}


def live_readiness(product, authored):
    row = policy()
    missing = []
    for key in json.loads(row.required_fields_json or '[]'):
        value = authored.get(key)
        if key == 'images':
            from app.catalogue_images import resolve_product_catalogue_image
            value = resolve_product_catalogue_image(product)
        if value is None or value == '' or isinstance(value, (list, dict)) and not value:
            missing.append(key)
    return missing


def settle_matching_revision(job, revision, *, verified=True):
    """CAS: a read/no-op cannot consume newer scan work or another owner's job."""
    values = dict(state='verified', priority=0, manual_actor_id=None, manual_revision=None,
                  manual_config=None, error=None, updated_at=drafts.now())
    if verified:
        values['verified_revision'] = revision
    return db.session.execute(update(ShopifyPublishJob).where(
        ShopifyPublishJob.product_id == job.product_id, ShopifyPublishJob.owner.is_(None),
        ShopifyPublishJob.state != 'recovery_required',
        ShopifyPublishJob.requested_revision == revision).values(**values)).rowcount == 1


def no_changes_settlement(job):
    """Retained no-op evidence, NOT a scanned or freshly verified revision."""
    if not job or job.owner or job.state != 'verified':
        return None
    try:
        data = json.loads(job.view_json or '{}').get('queue_no_changes') or {}
    except (ValueError, TypeError, AttributeError):
        return None
    if not isinstance(data, dict):
        return None
    return data if (data.get('requested_revision') == job.requested_revision
        and data.get('generation') == job.generation and data.get('source') == job.source_identity
        and data.get('operation_id') == job.operation_id) else None


def settle_obsolete_attention(product):
    """Worker/read-comparison only: consume obsolete work, never adopt scan authority.

    Old fingerprints need not be equated to new ones. Instead the *current*
    complete source must equal an identity-bound full comparison. Remote evidence
    may be old, but its timestamps/receipts and the original queue revision stay
    untouched. Ordinary GETs and the idle publisher never call this function.
    """
    from sqlalchemy import select, or_
    from app.models import ShopifyProductComparison, ShopifyCollectionIdentity, ShopifyAttributeIdentity
    from app.shopify_comparison import current, identity_revision
    job = db.session.get(ShopifyPublishJob, product.id)
    if (not job or job.owner or job.state != 'attention' or product.catalogue_status != 'active'
            or job.source_identity != product.source_relpath):
        return False
    bootstrap = job.requested_revision == 'unscanned' and job.trigger == 'bootstrap' and not job.operation_id
    source_guard = (job.error or '').startswith('Local source changed after its completed scan.')
    if not bootstrap and not source_guard:
        return False
    # Do not race a scan or an accepted manual operation whose identity has not
    # yet been advanced. The existing operation slot is authoritative.
    if CatalogueOperation.query.filter(CatalogueOperation.status.in_({'running', 'pending'})).first():
        return False
    # Only an explicitly proven BEFORE-WRITE prerequisite can be superseded.
    # Never infer the outcome of a failed/ambiguous mutation from matching prices.
    if not bootstrap:
        operation = db.session.get(CatalogueOperation, job.operation_id) if job.operation_id else None
        scope = json.loads(operation.scope or '{}') if operation else {}
        if (not operation or operation.status != 'failed' or operation.recovery_state != 'none'
                or scope.get('mode') != 'automatic_preflight' or scope.get('product_id') != product.id
                or scope.get('write_attempted') is not False
                or (scope.get('operation_summary') or {}).get('write_attempted') is not False):
            return False
    identities = ShopifyProductIdentity.query.filter(or_(ShopifyProductIdentity.product_id == product.id,
        ShopifyProductIdentity.source_identity == product.source_relpath)).all()
    if len(identities) != 1:
        return False
    identity = identities[0]
    if (identity.product_id != product.id or identity.source_identity != product.source_relpath
            or identity.state != 'verified' or identity.verification_state != 'verified'
            or not identity.shopify_product_id):
        return False
    evidence = drafts.evidence(identity)
    stages = [evidence.get(key, {}) for key in ('native_dimensions', 'publication_update',
        'customisation_update', 'sale_transition', 'inventory_update', 'verification_wait')]
    stages += list(evidence.get('variant_native', {}).values()) + list(evidence.get('package_assignments', {}).values())
    if any(p.get('state') in {'pending', 'uncertain', 'processing', 'waiting_authority'} for p in stages):
        return False
    operation = db.session.get(CatalogueOperation, identity.operation_id) if identity.operation_id else None
    if operation and (operation.status in {'running', 'pending', 'interrupted'} or operation.recovery_state != 'none'):
        return False
    comparison = ShopifyProductComparison.query.filter_by(store_id=identity.store_id,
        source_identity=product.source_relpath).order_by(ShopifyProductComparison.compared_at.desc()).first()
    if not comparison:
        return False
    mappings = {(r.store_id, r.source_key): r for model in (ShopifyCollectionIdentity, ShopifyAttributeIdentity)
        for r in model.query.filter_by(store_id=identity.store_id)}
    try:
        data = current(product, comparison, identity, mappings, freshness=False)
        if (not data or data.get('read_complete') is not True or data.get('scope') != 'full'
                or data.get('classification') != 'no_change' or data.get('blockers')
                or data.get('change_count') != 0 or data.get('local_package_changes')
                or data.get('refresh_failure') or data.get('conflicting_changes')
                or data.get('equality_local') != data.get('local') or not data.get('equality_at')
                or local_revision(product) != data.get('local')):
            return False
    except (ValueError, OSError, RuntimeError, ShopifyError):
        return False
    old_view, original_receipt = job.view_json, identity.media_evidence_json
    view = json.loads(old_view or '{}')
    view['queue_no_changes'] = dict(requested_revision=job.requested_revision, generation=job.generation,
        source=job.source_identity, operation_id=job.operation_id, local=data['local'],
        comparison=drafts.digest(comparison.summary_json), identity=identity_revision(identity),
        compared_at=comparison.compared_at.isoformat(), settled_at=drafts.now().isoformat())
    from app.shopify_capacity import fence_owner
    fence_owner()
    changed = db.session.execute(update(ShopifyPublishJob).where(
        ShopifyPublishJob.product_id == product.id, ShopifyPublishJob.owner.is_(None),
        ShopifyPublishJob.state == 'attention', ShopifyPublishJob.requested_revision == job.requested_revision,
        ShopifyPublishJob.source_identity == job.source_identity,
        ShopifyPublishJob.generation == job.generation, ShopifyPublishJob.error == job.error,
        ShopifyPublishJob.operation_id == job.operation_id, ShopifyPublishJob.view_json == old_view,
        ~select(CatalogueOperation.id).where(CatalogueOperation.status.in_({'running', 'pending'})).exists(),
        select(ShopifyProductIdentity.id).where(ShopifyProductIdentity.id == identity.id,
            ShopifyProductIdentity.media_evidence_json == original_receipt, ShopifyProductIdentity.state == 'verified',
            ShopifyProductIdentity.operation_id == identity.operation_id,
            ShopifyProductIdentity.shopify_product_id == identity.shopify_product_id,
            ShopifyProductIdentity.product_id == product.id, ShopifyProductIdentity.store_id == identity.store_id,
            ShopifyProductIdentity.source_identity == product.source_relpath,
            ShopifyProductIdentity.verification_state == 'verified').exists(),
        select(ShopifyProductComparison.id).where(ShopifyProductComparison.id == comparison.id,
            ShopifyProductComparison.summary_json == comparison.summary_json).exists()
        ).values(state='verified', priority=0, manual_actor_id=None, manual_revision=None, manual_config=None,
                 view_json=json.dumps(view), view_pending=True, updated_at=drafts.now())).rowcount
    # Keep requested/verified revisions, scan receipt, original error and operation.
    return changed == 1


def observe_comparison(product, plan, *, expected_revision, complete):
    """Conclusive equality settles scanned work; drift reopens it.

    It cannot adopt unscanned JSON, clear recovery, or turn Draft metadata into
    executable work. The next hourly run still builds and verifies a fresh plan.
    """
    if not complete:
        return
    job = db.session.get(ShopifyPublishJob, product.id)
    if not job or job.owner or job.state == 'recovery_required':
        return
    if job.requested_revision != expected_revision:
        if no_changes_settlement(job):
            job.state = 'attention'  # Re-evaluate evidence; a no-op is never write authority.
        db.session.flush()  # Include the just-recorded comparison in the same transaction.
        settle_obsolete_attention(product)
        return
    from app.shopify_publication import intent
    if (plan.get('classification') == 'no_change' and not plan.get('blockers')
            and not plan.get('local_package_changes')):
        # This exact scanned revision has now been compared/verified elsewhere
        # (including manual publication). Do not schedule a second publication.
        settle_matching_revision(job, expected_revision)
    elif plan.get('classification') == 'update' and intent(product_document(product))['status'] == 'ACTIVE':
        job.verified_revision, job.state = None, 'pending'
        job.trigger, job.next_attempt = 'reconciliation', drafts.now()


def execution_proof(product, plan):
    """Internal plan binding, not a signed manual approval or a forged token."""
    if product.product_type == 'variable':
        return {'mode': 'variable', **{key: plan.get(key) for key in (
            'product_id', 'digest', 'store_id', 'config_digest', 'nonce', 'remote_id',
            'remote_digest', 'identity_digest', 'taxonomy_review_digest', 'classification', 'identity_id', 'scope')}}
    if plan['classification'] == 'update':
        return {'mode': 'update', 'scope': 'full', **{key: plan[key] for key in (
            'product_id', 'digest', 'identity_id', 'remote_id', 'store_id', 'domain',
            'config_digest', 'remote_digest', 'identity_digest', 'taxonomy_review_digest')}}
    return {key: plan[key] for key in ('product_id', 'digest', 'store_id', 'store_key',
                                     'domain', 'config_digest', 'nonce', 'taxonomy_review_digest')}


def _settle_job(job, *, owner, revision, state, operation_id=None, error=None, fence=None):
    from app.shopify_capacity import fence_owner
    (fence or fence_owner)()
    db.session.refresh(job)
    if job.owner != owner or job.running_revision != revision:
        raise BackgroundYield('Publishing job ownership changed; stale completion was discarded.')
    newer = ShopifyPublishJob.requested_revision != revision
    values = dict(state=case((newer, 'pending'), else_=state) if state in {'verified', 'paused', 'attention', 'retry'} else state, owner=None,
        operation_id=operation_id or job.operation_id,
        error=case((newer, None), else_=error) if state in {'verified','paused','attention','retry'} else error,
        updated_at=drafts.now())
    if state == 'verified':
        values['verified_revision'] = revision
        values.update(priority=case((newer, ShopifyPublishJob.priority), else_=0),
            manual_actor_id=case((newer, ShopifyPublishJob.manual_actor_id), else_=None),
            manual_revision=case((newer, ShopifyPublishJob.manual_revision), else_=None),
            manual_config=case((newer, ShopifyPublishJob.manual_config), else_=None))
    changed = db.session.execute(update(ShopifyPublishJob).where(
        ShopifyPublishJob.product_id == job.product_id, ShopifyPublishJob.owner == owner,
        ShopifyPublishJob.running_revision == revision).values(**values)).rowcount
    if changed != 1:
        db.session.rollback()
        raise BackgroundYield('Publishing job ownership changed; stale completion was discarded.')
    db.session.commit()
    from app.catalogue_views import capture
    product = db.session.get(Product, job.product_id, populate_existing=True)
    if product:
        capture(product)
        db.session.commit()
    from app.publishing_logs import event
    event(job, state)


def _preflight_receipt(job, product, error, *, selection=None):
    """Retain one real blocked attempt per scanned revision; never a write claim."""
    from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation
    if job.operation_id:
        previous = db.session.get(CatalogueOperation, job.operation_id)
        if previous:
            source = json.loads(previous.scope or '{}').get('automatic_publishing', {})
            if source.get('revision') == job.requested_revision and previous.error == error:
                return previous.id
    operation = acquire_catalogue_operation('shopify_draft_publish', {
        'product_id': product.id, 'sku': product.sku, 'title': product.title, 'mode': 'automatic_preflight',
        'automatic_publishing': {'revision': job.requested_revision, 'trigger': event_trigger(job)},
        'write_attempted': False, 'selection': selection})
    finish_catalogue_operation(operation.id, status='failed', products_attempted=1, products_failed=1,
        error=error, operation_summary={'write_attempted': False, 'stage': 'publishing_preflight'})
    return operation.id


def event_trigger(job):
    return 'hourly' if job.trigger in {'scan', 'bootstrap'} else job.trigger


def processing_readback(row):
    """Only the executor's known accepted media-processing state, never uncertainty."""
    data = drafts.evidence(row)
    from app.shopify_variable_readback import pending
    if pending(row):return True
    return bool(row.state == 'processing' and row.verification_state == 'processing' and row.shopify_product_id
        and data.get('verification', {}).get('identity') == 'verified'
        and data.get('verification', {}).get('media') == 'processing' and data.get('created_media')
        and not any(data.get(key, {}).get('state') in {'pending', 'uncertain'} for key in
            ('native_dimensions','publication_update','customisation_update','sale_transition','inventory_update'))
        and not any(p.get('state') in {'pending', 'uncertain'} for p in data.get('variant_native', {}).values())
        and not any(p.get('state') in {'pending', 'uncertain'} for p in data.get('package_assignments', {}).values()))


def _needs_attention(job, product, error, *, fence=lambda: None, selection=None):
    try:
        job.operation_id = _preflight_receipt(job, product, error, selection=selection)
    except CatalogueOperationActive:
        job.state, job.error = 'pending', 'Another operation owns the slot; publishing validation is retained.'
        fence(); db.session.commit(); return 'deferred'
    job.state, job.error = 'attention', error
    fence(); db.session.commit()
    from app.publishing_logs import event
    event(job, 'attention')
    return 'attention'


def publish_one(job, *, owner, guard, fence, adapter=None, metrics=None):
    adapter = adapter or ShopifyAdapter()
    guard()
    if job.owner:
        raise BackgroundYield('Another worker owns this product.')
    product = db.session.get(Product, job.product_id)
    if not product or product.catalogue_status != 'active' or product.source_relpath != job.source_identity:
        job.state, job.error = 'attention', 'Product source/identity is absent or changed; inspect the catalogue.'
        fence(); db.session.commit()
        return 'attention'
    config = drafts.configuration()
    selection = {'reason': 'due_' + job.state, 'trigger': event_trigger(job),
                 'requested_revision': job.requested_revision,
                 'verified_revision': job.verified_revision, 'generation': job.generation,
                 'scan_operation_id': job.scan_operation_id}
    from app.models import ShopifyProductComparison
    comparison = ShopifyProductComparison.query.filter_by(source_identity=job.source_identity).first()
    if comparison:
        summary = json.loads(comparison.summary_json)
        selection.update(comparison_revision=summary.get('local'), comparison_classification=summary.get('classification'),
                         comparison_at=comparison.compared_at.isoformat())
    if metrics is not None:
        metrics['selection'] = selection
    if job.state in {'attention', 'recovery_required'}:
        return job.state
    row = None
    candidates = ShopifyProductIdentity.query.filter_by(product_id=product.id).all()
    if len(candidates) > 1 or any(not processing_readback(r) and not drafts.upload_pending(r) and (r.state not in {
            'verified', 'remote_deleted', 'failed_before_create','attribute_review_required','scoped_verified'} or
            r.state != 'failed_before_create' and r.verification_state not in {
                'verified', 'remote_absent','base_verified','inventory_verified'}) for r in candidates):
        job.state, job.error = 'recovery_required', 'Existing publishing identity requires safe read-only recovery; no automatic replay.'
        fence(); db.session.commit()
        return job.state
    if candidates:
        row = candidates[0]
    # Use exactly the catalogue's persisted status rules. No JSON/gallery walk
    # for idle matching/blocked products; source guards belong to executable work.
    from app.catalogue_views import saved_states
    saved = saved_states([product], url_builder=lambda *a, **kw: None)[product.id]
    selection['catalogue_state'] = saved['state']
    continuing = row and (processing_readback(row) or drafts.upload_pending(row))
    if saved['state'] in {'processing', 'recovery_required'} and not continuing:
        selection.update(outcome='blocked', write_attempted=False)
        if saved['state'] == 'processing':
            return 'deferred'
        job.state, job.error = 'recovery_required', saved.get('message') or 'Retained remote evidence requires safe recovery.'
        fence(); db.session.commit(); return 'recovery_required'
    if saved['state'] in {'attention', 'unavailable'} and not continuing:
        selection.update(outcome='blocked', write_attempted=False)
        job.state, job.error = 'attention', saved.get('message') or 'Resolve the saved catalogue finding before publication.'
        fence(); db.session.commit(); return 'attention'
    if not continuing and (saved['state'] == 'no_change' or job.state == 'verified'):
        revision = job.requested_revision
        selection.update(outcome='no_changes', write_attempted=False)
        fence()
        # A display checkpoint may match a newer manual comparison than an old
        # scan hash. Consume obsolete work without adopting that newer revision.
        exact = bool(comparison and summary.get('local') == revision and summary.get('read_complete') is True
                     and summary.get('classification') == 'no_change')
        if not settle_matching_revision(job, revision, verified=exact):
            db.session.rollback()
            raise BackgroundYield('Queue revision or owner changed; no-op settlement discarded.')
        db.session.commit()
        return 'verified' if selection['verified_revision'] == revision else 'matching'
    # A newer unscanned JSON/image edit must not be combined with old indexed
    # variants or identities. Only a supported completed scan advances this job.
    try:
        current_revision = local_revision(product)
    except (ShopifyError, ValueError, OSError, RuntimeError) as exc:
        return _needs_attention(job, product, 'Local publishing source requires correction: ' + sanitize_operation_error(exc), fence=fence, selection=selection)
    selection['current_revision'] = current_revision
    if current_revision != job.requested_revision:
        selection.update(outcome='scan_required', write_attempted=False)
        return _needs_attention(job, product, 'Local source changed after its completed scan. Run its collection update before publication.', fence=fence, selection=selection)
    from app.shopify_publication import intent
    authored = product_document(product)
    wanted = intent(authored)
    from app.sale_pricing import instant
    release = instant(authored.get('shopify_release_at'))
    if release and release > drafts.now() and wanted['status'] == 'ACTIVE':
        return _needs_attention(job, product, 'A future release must remain Draft until due. Save Draft intent with the release schedule.', fence=fence)
    if wanted['status'] != 'ACTIVE':
        if not row or not row.shopify_product_id or wanted['status'] != 'DRAFT':
            job.state, job.error = 'paused', 'Draft remains local; ordinary Draft metadata is paused.'
            fence(); db.session.commit(); return 'paused'
        if row.state == 'verified' and drafts.evidence(row).get('observed_status') == 'DRAFT':
            job.state, job.error = 'paused', 'Shopify remains Draft; local metadata changes are retained.'
            fence(); db.session.commit(); return 'paused'
    elif live_readiness(product, authored):
        return _needs_attention(job, product, 'TLC Live-required fields missing: ' + ', '.join(live_readiness(product, authored)), fence=fence)
    elif job.verified_revision == job.requested_revision:
        selection.update(outcome='no_changes', write_attempted=False)
        fence()
        if not settle_matching_revision(job, current_revision):
            db.session.rollback()
            raise BackgroundYield('Queue revision or owner changed; no-op settlement discarded.')
        db.session.commit()
        return 'verified'  # No full Shopify fetch/mutation for unchanged local revision.
    revision = job.requested_revision
    claimed = db.session.execute(update(ShopifyPublishJob).where(ShopifyPublishJob.product_id == product.id,
        ShopifyPublishJob.owner.is_(None), ShopifyPublishJob.requested_revision == revision).values(
            owner=owner, running_revision=revision, state='processing', attempts=ShopifyPublishJob.attempts + 1)).rowcount
    fence(); db.session.commit(); db.session.refresh(job)
    if claimed != 1:
        raise BackgroundYield('Another worker owns this product.')
    try:
        initial_authority = authority(job, config)
    except (BackgroundYield, ValueError):
        _settle_job(job, owner=owner, revision=revision, state='paused', error='Automatic publishing is paused or not authorised.', fence=fence)
        return 'paused'
    epoch = initial_authority.get('revision')

    def write_guard():
        guard()
        proof = authority(job, drafts.configuration(), expected_policy=epoch)
        if proof.get('store_id') and proof['store_id'] != initial_authority.get('store_id'):
            raise BackgroundYield('Publishing store changed; no further write permitted.')
        if job.owner != owner or job.running_revision != revision:
            raise BackgroundYield('Publishing product ownership changed.')

    context = {'product_id': product.id, 'revision': revision, 'trigger': event_trigger(job),
               'authority': initial_authority, 'owner': owner}
    if row and (processing_readback(row) or drafts.upload_pending(row)):
        context['processing_operation'] = row.operation_id
        context['completion_of_creation'] = not drafts.evidence(row).get('update_attempted')
    budget = None
    try:
        with background_work(guard, fence=fence, requests=180, seconds=110,
                             publisher_authority=write_guard, publishing_context=context) as budget:
            write_guard()
            if row and processing_readback(row):
                proof = drafts.evidence(row)
                polls = proof.get('automatic_readback_attempts', 0) if proof.get('automatic_readback_operation') == row.operation_id else 0
                proof.update(automatic_readback_operation=row.operation_id, automatic_readback_attempts=polls+1)
                drafts.save(row, proof)
                # Existing finaliser is deliberately restricted to reads here.
                with background_work(guard, fence=fence, requests=40, seconds=45) as read_budget:
                    try:
                        drafts.finalize_processing(row, adapter)
                    finally:
                        budget['requests'] += read_budget['requests']
                        budget['throttled'] += read_budget['throttled']
                from app.shopify_variable_readback import pending as verification_pending,complete as finish_readback
                if verification_pending(row):
                    write_guard()
                    finish_readback(row,adapter,allow_writes=True)
                if proof.get('verification_wait'):
                    from app.shopify_notifications import complete as fully_verified
                    from app.shopify_variables import local_plan
                    if fully_verified(row,drafts.evidence(row)) and local_plan(product)['digest']==proof['plan']['digest']:
                        _settle_job(job,owner=owner,revision=revision,state='verified',operation_id=row.operation_id,fence=fence)
                        return 'verified'
                if processing_readback(row):
                    if polls+1 >= 12:
                        raise ShopifyError('Shopify media remains processing after bounded readback retries. Inspect its original receipt and use read-only reconciliation; no Create is retried.')
                    _settle_job(job, owner=owner, revision=revision, state='retry', operation_id=row.operation_id,
                        error='Shopify media is still processing; readback retained, no product mutation will be replayed.', fence=fence)
                    job.next_attempt,job.priority=drafts.now()+timedelta(seconds=30),max(job.priority,1)
                    fence();db.session.commit()
                    return 'retry'
                if row.state not in {'verified','attribute_review_required','scoped_verified'}:
                    raise ShopifyError('Processing readback could not prove the retained identity; safe recovery is required.')
                if wanted['status'] == 'DRAFT' and drafts.evidence(row).get('observed_status') == 'DRAFT':
                    _settle_job(job,owner=owner,revision=revision,state='paused',operation_id=row.operation_id,
                        error='Draft independently observed; metadata remains paused.',fence=fence)
                    return 'paused'
            if wanted['status'] == 'DRAFT':
                from app.shopify_publication import preview, confirm
                plan, _ = preview(product, adapter, status_only=True)
                if plan['classification'] == 'no_change' and not plan['blockers']:
                    row = db.session.get(ShopifyProductIdentity, plan['identity_id'])
                    evidence = drafts.evidence(row)
                    evidence['observed_status'] = plan['actual_status']
                    drafts.save(row, evidence)
                    _settle_job(job, owner=owner, revision=revision, state='paused', fence=fence,
                        error='Shopify Draft independently observed; accumulated metadata remains paused.')
                    return 'paused'  # no invented publication event or full-equality checkpoint
                row = confirm({'product_id': product.id, 'digest': plan['digest']}, adapter, status_only=True)
                proof = drafts.evidence(row).get('publication_update', {})
                state = 'paused' if proof.get('state') == 'verified' and row.state == 'verified' else 'recovery_required'
            else:
                plan, _unused_manual_token = drafts.preview(product, adapter)
                if plan['blockers']:
                    raise ShopifyError('; '.join(plan['blockers']))
                if initial_authority.get('store_id') and plan['store_id'] != initial_authority['store_id']:
                    raise ShopifyError('Connected Shopify store differs from the durable automatic authority.')
                if plan['classification'] == 'no_change':
                    # This is comparison evidence, not a claim of a new publication.
                    guard(); _settle_job(job, owner=owner, revision=revision, state='verified', fence=fence)
                    return 'matching'
                row = drafts.execute_reviewed(execution_proof(product, plan), adapter, expected_product_id=product.id)
                from app.shopify_notifications import complete
                state = 'verified' if complete(row, drafts.evidence(row)) else 'retry' if processing_readback(row) or drafts.upload_pending(row) else 'recovery_required'
            guard()
            _settle_job(job, owner=owner, revision=revision, state=state, operation_id=row.operation_id, fence=fence,
                error=None if state == 'verified' else 'Shopify Draft verified; metadata remains paused.' if state == 'paused'
                else 'Acknowledged gallery uploads retained; next bounded slice will continue.' if drafts.upload_pending(row)
                else 'Shopify media processing; awaiting read-only verification.' if state == 'retry'
                else 'Publishing is not completely verified. Inspect the original receipt before any retry.')
            if state == 'retry':
                job.next_attempt,job.priority=drafts.now()+timedelta(seconds=30),max(job.priority,1)
                fence();db.session.commit()
            return state
    except CatalogueOperationActive:
        db.session.rollback(); db.session.refresh(job)
        _settle_job(job, owner=owner, revision=revision, state='pending', error='Another catalogue operation owns the slot; queued work retained.', fence=fence)
        return 'deferred'
    except BackgroundYield:
        db.session.rollback()
        # The task owner decides recovery after expiry; an old worker cannot finalize.
        raise
    except (ShopifyError, ValueError, OSError, RuntimeError) as exc:
        db.session.rollback(); guard(); db.session.refresh(job)
        identities = ShopifyProductIdentity.query.filter_by(product_id=product.id).all()
        uncertain = any(not processing_readback(r) and r.state not in {'verified', 'remote_deleted', 'failed_before_create'} for r in identities)
        transient = not uncertain and getattr(exc, 'category', None) in {'network', 'rate_limited'} and job.attempts < 6
        operation_id = identities[0].operation_id if identities else None
        if not uncertain and not transient:
            operation = db.session.get(CatalogueOperation, operation_id) if operation_id else None
            context = json.loads(operation.scope or '{}').get('automatic_publishing', {}) if operation else {}
            if context.get('revision') != revision or operation.status == 'succeeded':
                try:
                    operation_id = _preflight_receipt(job, product, sanitize_operation_error(exc))
                except CatalogueOperationActive:
                    _settle_job(job, owner=owner, revision=revision, state='pending', error='Preflight receipt deferred by the current operation owner.', fence=fence)
                    return 'deferred'
        _settle_job(job, owner=owner, revision=revision, state='recovery_required' if uncertain else 'retry' if transient else 'attention', fence=fence,
                    operation_id=operation_id, error=sanitize_operation_error(exc))
        if transient:
            job.next_attempt = drafts.now() + timedelta(seconds=max(getattr(exc, 'delay', 0), min(3600, 30 * 2 ** job.attempts)))
            job.priority = max(job.priority, 1)
            fence(); db.session.commit()
        return 'recovery_required' if uncertain else 'retry' if transient else 'attention'
    finally:
        if metrics is not None and budget is not None:
            metrics['api_requests'] = budget['requests']
            metrics['throttled'] = budget['throttled']


def enqueue_priority():
    """Existing coordinator, not a second scheduler. Queued manual grants stay scoped."""
    from app import scheduled_tasks as tasks
    stamp = drafts.now()
    jobs = ShopifyPublishJob.query.filter(ShopifyPublishJob.state.in_({'pending', 'retry'}),
        ShopifyPublishJob.priority > 0, ShopifyPublishJob.next_attempt <= stamp).order_by(
            ShopifyPublishJob.priority.desc(), ShopifyPublishJob.product_id).limit(25).all()
    if not jobs:
        return None
    row = policy()
    if not row.enabled:
        ids = [j.product_id for j in jobs if j.manual_actor_id and j.manual_revision == j.requested_revision]
        return tasks.enqueue(KEY, manual=True, product_ids=ids) if ids else None
    task = db.session.get(ScheduledTask, KEY)
    if task.active_run_id:
        return task.active_run_id
    if task.next_due <= stamp:
        return tasks.enqueue(KEY)  # the real hourly batch is already due
    return tasks.enqueue(KEY, product_ids=[j.product_id for j in jobs], priority=True)


def recover_jobs(owner, *, guard=lambda: None, fence=lambda: None):
    """Only called under the existing exclusive publisher task lease."""
    for job in ShopifyPublishJob.query.filter(ShopifyPublishJob.owner.isnot(None)).all():
        guard()
        identities = ShopifyProductIdentity.query.filter_by(product_id=job.product_id).all()
        pending_media = len(identities) == 1 and (processing_readback(identities[0]) or drafts.upload_pending(identities[0]))
        unsafe = any(not processing_readback(r) and not drafts.upload_pending(r) and r.state not in {'verified', 'remote_deleted', 'failed_before_create'} for r in identities)
        operation = db.session.get(CatalogueOperation, job.operation_id) if job.operation_id else None
        if operation and operation.recovery_state not in {None, 'none'} and not pending_media:
            unsafe = True
        # A process can stop after the executor's durable terminal receipt but
        # before queue completion. Reuse only that exact, verified revision.
        completed = None
        for identity in identities:
            candidate = db.session.get(CatalogueOperation, identity.operation_id) if identity.operation_id else None
            if candidate:
                context = json.loads(candidate.scope or '{}').get('automatic_publishing', {})
                if context.get('revision') == job.running_revision and context.get('owner') == job.owner:
                    if candidate.status == 'succeeded' and identity.state == 'verified':
                        completed = candidate
                    elif candidate.status in {'running', 'pending'} or candidate.recovery_state not in {None, 'none'} and not pending_media:
                        unsafe = True
        if unsafe:
            job.state, job.error = 'recovery_required', 'Interrupted publishing retains uncertain evidence; inspect its original receipt.'
        elif completed:
            job.operation_id = completed.id
            status_only = json.loads(completed.scope or '{}').get('mode') == 'publication'
            if not status_only:
                job.verified_revision = job.running_revision
            job.state = ('paused' if status_only else 'verified') if job.requested_revision == job.running_revision else 'pending'
        else:
            job.state = 'pending'  # no durable product mutation was attempted
        job.owner = None
        fence(); db.session.commit()


def run_slice(run_id, owner, *, adapter=None):
    from app import scheduled_tasks as tasks
    task = db.session.get(ScheduledTask, KEY)
    run = db.session.get(ScheduledTaskRun, run_id)
    data = json.loads(run.data_json)
    guard = lambda: tasks.renew(KEY, run_id, owner)
    fence = lambda: tasks.fence(KEY, run_id, owner)
    try:
        guard()
        recover_jobs(run_id + ':' + owner, guard=guard, fence=fence)
        # Upgrade/bootstrap only existing database identities in bounded slices.
        # This does not discover folders, run a scanner, or create local products.
        seeded = 0
        if policy().enabled and data.get('scope') is None:
            missing = Product.query.outerjoin(ShopifyPublishJob, Product.id == ShopifyPublishJob.product_id).filter(
                Product.catalogue_status == 'active', ShopifyPublishJob.product_id.is_(None)).order_by(Product.id).limit(8).all()
            for product in missing:
                # An old installation has no persisted scan revision for this
                # queue. Do not silently adopt a possibly unscanned source tree
                # as automatic write authority. Its next normal supported scan
                # replaces this explicit unknown marker with a real fingerprint.
                db.session.add(ShopifyPublishJob(product_id=product.id, source_identity=product.source_relpath or '',
                    requested_revision='unscanned', state='attention', trigger='bootstrap', generation=1,
                    priority=0, attempts=0, next_attempt=drafts.now(), updated_at=drafts.now(),
                    error='Automatic publishing needs a completed scan revision. Run the existing containing-collection update scan.'))
                seeded += 1
            fence(); db.session.commit()
        jobs = ShopifyPublishJob.query.filter(ShopifyPublishJob.state.in_({'pending', 'retry'})).order_by(
            ShopifyPublishJob.priority.desc(), ShopifyPublishJob.product_id)
        if data.get('scope') is not None:
            jobs = jobs.filter(ShopifyPublishJob.product_id.in_(data['scope']))
        if not policy().enabled:
            jobs = jobs.filter(ShopifyPublishJob.manual_actor_id.isnot(None),
                               ShopifyPublishJob.manual_revision == ShopifyPublishJob.requested_revision)
        outcomes = data.setdefault('outcomes', {})
        if len(outcomes) >= tasks.MAX_PRODUCTS:
            # Per-run resource bound, not a catalogue/product publishing limit.
            # Finish this run's readbacks; other durable jobs get the next run.
            jobs = jobs.filter(ShopifyPublishJob.product_id.in_([int(key) for key in outcomes]))
        selected = jobs.filter(ShopifyPublishJob.next_attempt <= drafts.now()).limit(3).all()
        for job in selected:
            guard()
            metrics = {}
            # Ownership also fences preflight/local-only branches. This outer
            # scope grants NO transport calls; remote work has the inner bounded,
            # explicitly authorised scope in publish_one.
            with background_work(guard, fence=fence, requests=0, seconds=110,
                    publishing_context={'product_id': job.product_id, 'revision': job.requested_revision,
                                        'trigger': event_trigger(job), 'owner': run_id + ':' + owner}):
                result = publish_one(job, owner=run_id + ':' + owner, guard=guard, fence=fence,
                                     adapter=adapter, metrics=metrics)
            data['counts']['api_requests'] += metrics.get('api_requests', 0)
            data['counts']['throttled'] += metrics.get('throttled', 0)
            outcomes[str(job.product_id)] = 'v' if result in {'verified','matching'} else 'p' if result in {'deferred','retry','paused'} else 'a'
            data['counts']['attempts'] = data['counts'].get('attempts', 0) + 1
            data['counts'].update(considered=len(outcomes), checked=len(outcomes),
                up_to_date=sum(value=='v' for value in outcomes.values()),
                deferred=sum(value=='p' for value in outcomes.values()),
                attention=sum(value=='a' for value in outcomes.values()))
            data['items'] = (data.get('items', []) + [{'id': job.product_id, 'state': result,
                'operation_id': job.operation_id, 'reason': (job.error or '')[:180],
                'selection': metrics.get('selection')}])[-100:]
        remains = jobs.filter(ShopifyPublishJob.owner.is_(None)).count()
        data['reason'] = 'Automatic publishing paused; pending work retained.' if not policy().enabled else None
        if remains:
            data['reason'] = 'Pending retry/readback work retained in this run; no product success is inferred before complete verification.'
        elif len(outcomes) >= tasks.MAX_PRODUCTS:
            data['reason'] = 'Bounded run completed; additional durable catalogue jobs remain eligible for the next run.'
        if remains or seeded == 8:
            tasks.save_progress(KEY, run_id, owner, data, pause=True)
        else:
            # Unlike a read-only status inspection, this run requested actual
            # publishing. Blocked/uncertain targets must not imply all published.
            tasks.save_progress(KEY, run_id, owner, data,
                terminal='partially_completed' if data['counts']['attention'] else 'completed')
    except BackgroundYield:
        db.session.rollback()
        try:
            tasks.save_progress(KEY, run_id, owner, data, pause=True)
        except BackgroundYield:
            db.session.rollback()
    except Exception as exc:
        db.session.rollback()
        try:
            tasks.save_progress(KEY, run_id, owner, data, terminal='failed', error=sanitize_operation_error(exc))
        except BackgroundYield:
            db.session.rollback()
