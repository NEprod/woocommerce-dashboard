"""Indexed due events. Reuses sale calculations and the existing local-save/scan path."""
import json
import uuid
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert

from app import db, shopify_drafts as drafts
from app.models import ShopifyDueEvent, ShopifyPublishJob, ShopifySaleSchedule, Product, Settings, CatalogueOperation
from app.taxonomy_assignments import product_document
from app.sale_pricing import instant, schedule


def source_guard(product):
    """Time-independent source guard, not a second product authoring format."""
    from app.catalogue_images import product_image_diagnostics, variation_image_diagnostics, _source_context
    from app.taxonomy_registry import load_configured_registry
    authored = product_document(product)
    context = _source_context(product)
    rows = product_image_diagnostics(product)
    for variant in product.variations:
        rows += variation_image_diagnostics(variant, include_parent_fallback=False)
    files = []
    if context:
        root, folder, _ = context
        for ref in dict.fromkeys(r['source_reference'] for r in rows if r.get('source_reference')):
            path = (root / ref).resolve(strict=True)
            path.relative_to(root); path.relative_to(folder)
            stat = path.stat(); files.append([ref, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns])
    registry = load_configured_registry()
    return drafts.digest({'authored': authored, 'registry': registry.snapshot.digest if registry.available else registry.status,
        'images': files, 'rows': json.loads(product.resolved_row_json or '{}'),
        'variants': [(v.sku, v.resolved_row_json, v.source_identity) for v in product.variations]})


def index_product(product):
    authored = product_document(product)
    start, end = schedule(authored, partial=True)
    release = instant(authored.get('shopify_release_at'))
    dates = {'release': release, 'sale_activate': start, 'sale_end': end}
    legacy = ShopifySaleSchedule.query.filter_by(product_id=product.id).first()
    if legacy and legacy.state in {'authorised', 'active', 'activating', 'ending', 'recovery_required'}:
        dates.update(sale_activate=None, sale_end=None)  # separately signed legacy price path retains ownership
    guard = source_guard(product) if any(dates.values()) else None
    table = ShopifyDueEvent.__table__
    for kind, due in dates.items():
        previous = ShopifyDueEvent.query.filter_by(product_id=product.id, event_type=kind).first()
        if due is None:
            if previous and previous.state != 'completed': previous.state = 'cancelled'
            continue
        revision = drafts.digest({'kind': kind, 'due': due.isoformat(), 'sale': authored.get('sale_price'),
            'modifiers': authored.get('variation_modifiers', {}) if kind != 'release' else None})
        if previous and previous.schedule_revision == revision:
            previous.snapshot_guard = guard
            continue
        db.session.execute(insert(table).values(product_id=product.id, event_type=kind, due_at=due,
            schedule_revision=revision, snapshot_guard=guard, state='pending', attempts=0).on_conflict_do_update(
                index_elements=[table.c.product_id, table.c.event_type], set_={
                    'due_at': due, 'schedule_revision': revision, 'snapshot_guard': guard, 'state': 'pending',
                    'attempts': 0, 'error': None, 'operation_id': None}))


def _release(product, event, guard):
    """Real source patch + the SAME existing containing-collection scan dispatch."""
    from flask import current_app
    from app.metadata_save import prepare_request, review_token, verify_revision
    from app.metadata_workspace import _catalogue_root
    from app.metadata_save import override_collection_relpath
    from app.utils.atomic_files import atomic_write_text
    from app.utils.backup_retention import create_metadata_backup
    from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation
    from app.utils.scan_runner import start_scan
    kind = 'shared' if product.collection_type == 'Single Variable' else 'override'
    payload = {'revision': review_token(product, kind), 'data': {'live': True, 'shopify_status': 'ACTIVE'},
               'remove_paths': [], 'acknowledge_shared': True}
    target, edit = prepare_request(product, kind, payload)
    if not edit.validation.valid:
        raise ValueError('Scheduled release source validation failed; correct metadata before retrying.')
    relative = target.parent.relative_to(_catalogue_root()).as_posix() if kind == 'shared' else override_collection_relpath(product, target)
    scope = {'product_id': product.id, 'release_event': event.id, 'scope_kind': 'collection',
             'collection_relpath': relative, 'exhaustive': True, 'local_save': True, 'sku': product.sku}
    operation = acquire_catalogue_operation('shared_collection_update', scope)
    event.operation_id = operation.id
    db.session.commit()  # durable before authored JSON changes
    saved = False
    try:
        guard()
        if source_guard(product) != event.snapshot_guard:
            raise ValueError('Scheduled source changed before its local-save reservation; no release was written.')
        locked_target, locked_edit = prepare_request(product, kind, payload)
        if locked_target != target or not locked_edit.validation.valid:
            raise ValueError('Release source changed before save; no automatic replacement was made.')
        if target.exists(): create_metadata_backup(target)
        atomic_write_text(target, locked_edit.text, before_replace=lambda: verify_revision(product, kind, payload['revision']))
        saved = True
        if kind == 'override': atomic_write_text(target.parent / '.update', '1')
        start_scan(current_app._get_current_object(), uuid.uuid4().hex, scan_mode='shared_collection',
            operation_id=operation.id, scope=scope, collection_relpath=relative)
    except Exception:
        db.session.rollback()
        finish_catalogue_operation(operation.id, status='failed', error='Scheduled release local save/scan requires recovery.',
            operation_summary={'json_saved': saved, 'scan_pending': saved})
        raise


def process_due(guard, *, event_types=None, event_ids=None):
    from app.automatic_publishing import policy, queue_product
    from app.utils.operation_control import CatalogueOperationActive, sanitize_operation_error
    if not policy().enabled:
        return 0  # due rows are retained, not consumed during pause
    count = 0
    # Poll only known in-flight release receipts, never rescan/rewrite after restart.
    types = event_types or {'release', 'sale_activate', 'sale_end'}
    query = ShopifyDueEvent.query.filter(ShopifyDueEvent.event_type.in_(types))
    if event_ids is not None: query = query.filter(ShopifyDueEvent.id.in_(event_ids))
    for event in query.filter_by(state='processing').limit(8).all():
        guard()
        operation = db.session.get(CatalogueOperation, event.operation_id) if event.operation_id else None
        if operation and operation.status in {'running', 'pending'}: continue
        if operation and operation.status == 'succeeded':
            job = db.session.get(ShopifyPublishJob, event.product_id)
            if job:
                job.trigger, job.priority, job.next_attempt = 'release', 2, drafts.now()
                event.state = 'completed'
        else:
            event.state, event.error = 'attention', 'Interrupted release requires the existing local-save/scan recovery workflow.'
        db.session.commit()
    due = query.filter_by(state='pending').filter(ShopifyDueEvent.due_at <= drafts.now()).order_by(
        ShopifyDueEvent.due_at, ShopifyDueEvent.id).limit(8).all()
    for event in due:
        guard()
        product = db.session.get(Product, event.product_id)
        if not product or product.catalogue_status != 'active':
            event.state, event.error = 'attention', 'Scheduled product is no longer available in the local catalogue.'
            db.session.commit(); continue
        try:
            if source_guard(product) != event.snapshot_guard:
                raise ValueError('Scheduled source changed after its completed scan; rescan the containing collection before release.')
            changed = db.session.execute(update(ShopifyDueEvent).where(ShopifyDueEvent.id == event.id,
                ShopifyDueEvent.state == 'pending', ShopifyDueEvent.schedule_revision == event.schedule_revision).values(
                    state='processing', attempts=ShopifyDueEvent.attempts + 1)).rowcount
            db.session.commit()
            if changed != 1: continue
            if event.event_type == 'release':
                _release(product, event, guard)
            else:
                queue_product(product, trigger=event.event_type, priority=2)
                event.state = 'completed'
                db.session.commit()
            count += 1
        except CatalogueOperationActive:
            db.session.rollback(); event.state = 'pending'; db.session.commit()
            break
        except (ValueError, OSError, RuntimeError) as exc:
            db.session.rollback(); event.state, event.error = 'attention', sanitize_operation_error(exc)
            db.session.commit()
    return count
