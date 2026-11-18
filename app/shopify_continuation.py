"""Operation-bound continuation of the original reviewed Shopify plan.

The existing identity receipt holds the cursor and a single in-flight effect.
An ambiguous effect is never executable, even after the worker lease expires.
"""
import json

from app import db
from app.models import CatalogueOperation, Product
from app.shopify_capacity import BackgroundYield, RequestNotSent, background
from app.shopify_connection import ShopifyError


def origin(operation):
    scope = json.loads(operation.scope) if operation else {}
    if scope.get('manual_review'):
        return 'manual', scope['manual_review'].get('proof')
    context = scope.get('automatic_publishing') or {}
    if context.get('revision') and context.get('authority'):
        return 'publisher', context['revision']
    return None, None


def live_manual(operation):
    if not operation or origin(operation)[0] != 'manual':
        return False
    manual = json.loads(operation.scope)['manual_review']
    return (operation.status in {'pending','running'} or operation.status=='partial' and
            operation.recovery_state in {None,'none'} and manual.get('state') in {'queued','running','waiting'})


def binding(row, data, cursor, operation):
    lane, revision = origin(operation)
    return {'operation': row.operation_id, 'store': row.store_id, 'product': row.product_id,
            'remote': row.shopify_product_id, 'digest': row.reviewed_digest,
            'configuration': data['plan']['config_digest'], 'lane': lane,
            'authority_revision': revision, 'cursor': cursor}


def begin(row, data, cursor, *, commit=True):
    from app import shopify_drafts as drafts
    # Do not start an SQLite write transaction before the caller's lease
    # renewal (which may use its own connection).
    with db.session.no_autoflush:
        op = db.session.get(CatalogueOperation, row.operation_id)
        if not (background() and background().get('publisher_authority') and origin(op)[0]):
            return False
        data['continuation'] = binding(row, data, cursor, op)
    if commit:
        drafts.save(row, data)
    return True


def pending(row, lane=None):
    from app import shopify_drafts as drafts
    if not row or row.state in {'uncertain', 'failed_before_create', 'remote_deleted'}:
        return False
    data = drafts.evidence(row)
    mark = data.get('continuation') or {}
    op = db.session.get(CatalogueOperation, row.operation_id)
    return bool(mark.get('cursor') in {'simple_create', 'simple_update', 'variable_create', 'variable_update', 'variable_tail', 'variable_completion'}
                and mark.get('lane') and lane in (None, mark['lane']) and not data.get('dispatch')
                and data.get('upload_attempt', {}).get('state') != 'pending'
                and not data.get('attribute_assignment_attempt')
                and not any((data.get(k) or {}).get('state') == 'uncertain' for k in
                            ('publication_update', 'native_dimensions', 'storefront_configuration'))
                and all(p.get('state') != 'uncertain' for k in ('variant_native', 'package_assignments')
                        for p in data.get(k, {}).values())
                and row.reviewed_digest == data.get('plan', {}).get('digest')
                and op and op.status in {'pending', 'running'} and op.recovery_state in {None, 'none'}
                and mark == binding(row, data, mark['cursor'], op))


def effect(row, data, name, invoke, accept, *, state=None):
    """Commit intent before dispatch; acknowledge only with the validated result.

    `accept` must only validate and edit the in-memory receipt; it must not
    commit or make a remote request. A failed commit leaves the old reservation.
    """
    from app import shopify_drafts as drafts
    active = bool(data.get('continuation'))
    if active:
        if data.get('dispatch'):
            raise ShopifyError('Retained request has no acknowledgement; read-only reconciliation required.')
        data['dispatch'] = name
    drafts.save(row, data, state)
    try:
        result = invoke()
    except RequestNotSent:
        if active:
            data.pop('dispatch')
            drafts.save(row, data)
        raise
    accept(result)
    if active:
        data.pop('dispatch')
        # Creation binds the new remote identity in the acknowledgement commit.
        data['continuation']['remote'] = row.shopify_product_id
    drafts.completed_step(row, data, name)


def resume(row, adapter, *, lane):
    from app import shopify_drafts as drafts
    from app import shopify_variables as variables
    from app.manual_publishing import resume as reserve
    from app.utils import operation_control as control
    from app.shopify_capacity import fence_owner, guard_owner

    if not pending(row, lane):
        raise ShopifyError('No safe checkpoint for this publishing workflow; reconcile the retained operation.')
    data = drafts.evidence(row)
    mark = data['continuation']
    product = db.session.get(Product, row.product_id)
    config = drafts.configuration()
    guard_owner()
    if (not product or product.source_relpath != row.source_identity or product.sku != row.sku
            or drafts.local_plan(product)['digest'] != row.reviewed_digest
            or config.fingerprint() != mark['configuration']):
        raise ShopifyError('Checkpoint source, configuration or identity changed; no further write is authorised.')
    acquired = not control._active_operation or control._active_operation['id'] != row.operation_id
    if acquired:
        reserve(control.OperationLease(row.operation_id, 'shopify_draft_publish'))
    op = db.session.get(CatalogueOperation, row.operation_id)
    try:
        op.status = 'running'
        fence_owner(); db.session.commit()
        cursor = mark['cursor']
        if cursor.startswith('simple'):
            return drafts.run_simple_checkpoint(row, data, config, adapter, updating=cursor == 'simple_update')
        wait = data.get('verification_wait') or {}
        if wait.get('operation_id') == row.operation_id:
            from app.shopify_variable_readback import complete
            return complete(row,adapter,allow_writes=True)
        if cursor == 'variable_completion':
            from app.shopify_variable_readback import begin, complete
            begin(row,data,'base')
            return complete(row,adapter,allow_writes=True)
        if cursor == 'variable_tail':
            return variables.finish_registration_tail(row, data, config, adapter)
        return variables.run_variable_checkpoint(row, data, config, adapter, creating=cursor == 'variable_create')
    except BackgroundYield:
        db.session.rollback()
        if pending(row, lane):
            raise
        return fail(row, 'An interrupted mutation has no durable acknowledgement.')
    except drafts.GalleryPending as exc:
        drafts.defer_upload(row,data,exc)
    except Exception as exc:
        db.session.rollback()
        # Bounded variable readback can already have recorded a terminal
        # recovery receipt (acknowledged writes, not a failed product).
        if op.status == 'partial' and op.recovery_state == 'review_required':
            return row
        defer_read_failure(row,exc)
        return fail(row, exc)
    finally:
        if acquired:
            control.release_local_operation(row.operation_id)


def fail(row, exc):
    from app import shopify_drafts as drafts
    from app.utils.operation_control import finish_catalogue_operation, sanitize_operation_error
    data = drafts.evidence(row)
    data['failure_diagnostic'] = drafts.failure_diagnostic(exc, data.get('stage'), row.operation_id)
    if data.get('dispatch') or data.get('upload_attempt',{}).get('state')=='pending':
        data['failure_diagnostic']['mutation_outcome'] = 'unacknowledged'
    data['error'] = sanitize_operation_error(exc)
    drafts.save(row, data, 'uncertain')
    finish_catalogue_operation(row.operation_id, status='partial', recovery_state='review_required',
        error=data['error'], products_attempted=1, products_failed=1)
    return row


def defer_read_failure(row, exc):
    """A transport failure can be retried here only without a dispatched write."""
    if (isinstance(exc,ShopifyError) and exc.category in {'network','rate_limited','invalid_response'}
            and pending(row)):
        raise BackgroundYield('Read-only Shopify response was unavailable; verification will resume.',
                              code='shopify_read_failure') from None
