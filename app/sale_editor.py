"""Saved-editor handoff only. Existing sale authorisation/execution is unchanged."""
import json
from datetime import UTC, datetime

from itsdangerous import BadSignature

from app import shopify_drafts as drafts, shopify_sales as sales
from app.models import CatalogueOperation, CatalogueOperationItem
from app.metadata_edit import MetadataEditConflict
from app.metadata_save import source, verify_revision
from app.metadata_workspace import metadata_source, resolved_metadata, _catalogue_root
from app.product_info import validate_product_info
from app.sale_pricing import LONDON
from app.shopify_connection import ShopifyError
from app.utils.operation_control import get_active_operation


def uk_time(value):
    if not value:
        return 'Not set / ongoing'
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(LONDON).strftime('%d/%m/%Y %H:%M %Z')


def same_json(left, right):
    """Browser JSON roundtrips 10.0 as 10; booleans are NOT numeric values."""
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(same_json(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(same_json(a, b) for a, b in zip(left, right))
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    return left == right


def saved_state(product, payload):
    """Reject browser drafts, stale sources and pending scans BEFORE Shopify reads."""
    kind = payload.get('kind')
    saved = verify_revision(product, kind, payload.get('revision'))
    _, text, _ = source(product, kind)
    document = json.loads(text)
    if not isinstance(payload.get('data'), dict) or not same_json(payload['data'], document):
        raise MetadataEditConflict('Unsaved changes: Save Locally and wait for the scanner before Review Sale.')
    shared = metadata_source(product, 'shared')
    override = metadata_source(product, 'override')
    effective = resolved_metadata(product, shared['data'], override['data'] if override['exists'] else {})
    if shared['error'] or override['exists'] and override['error'] or not validate_product_info(
            document, 'collection' if kind == 'shared' else 'override').valid or not validate_product_info(effective, 'collection').valid:
        raise MetadataEditConflict('Saved JSON is invalid or unavailable. Correct it and complete the local scan first.')
    if product.catalogue_status != 'active' or get_active_operation():
        raise MetadataEditConflict('Catalogue projection is unavailable or an operation is active. Wait for scanner completion before reviewing.')
    root = _catalogue_root()
    try:
        folder = (root / product.source_relpath).resolve(strict=True)
        folder.relative_to(root)
        marker = folder / '.scanned'
        if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 1048576:
            raise ValueError()
        if json.loads(marker.read_text()).get('sku') != product.sku:
            raise ValueError()
        sources = {source(product, 'shared')[0], source(product, kind)[0]}
        if override['exists']:
            sources.add(source(product, 'override')[0])
        if any((p / name).exists() for p in {folder, shared['path'].parent if shared.get('path') else source(product, 'shared')[0].parent}
               for name in ('.update', '.scanned.pending')) or any(p.exists() and p.stat().st_mtime_ns > marker.stat().st_mtime_ns for p in sources):
            raise ValueError()
    except (OSError, ValueError, TypeError, AttributeError):
        raise MetadataEditConflict('Scanner projection is pending or older than the saved JSON. Complete the affected local scan before Review Sale.') from None
    # A failed editor handoff must not be treated as complete merely because
    # source files are valid. A later committed/finalized item can repair it.
    shared_scope = source(product, 'shared')[0].parent.relative_to(root).as_posix()
    for operation in CatalogueOperation.query.filter(CatalogueOperation.scope.contains('"local_save"')).order_by(CatalogueOperation.started_at.desc()).limit(100):
        scope = json.loads(operation.scope)
        if scope.get('sku') != product.sku and scope.get('collection_relpath') != shared_scope:
            continue
        if scope.get('operation_summary', {}).get('json_saved') is False:
            continue
        if operation.status != 'succeeded':
            repaired = CatalogueOperationItem.query.join(CatalogueOperation).filter(
                CatalogueOperationItem.sku == product.sku, CatalogueOperationItem.database_state == 'committed',
                CatalogueOperationItem.marker_state == 'finalized', CatalogueOperationItem.error.is_(None),
                CatalogueOperation.started_at >= operation.started_at).first()
            if not repaired:
                raise MetadataEditConflict('Local scan/projection needs completion or recovery. Inspect Scanner history before Review Sale.')
        break
    return drafts.digest(saved)


def status(product, payload):
    binding = saved_state(product, payload)
    return {'ready': True, 'message': 'Saved current JSON and local projection are ready for read-only sale review.',
            'binding': binding, 'schedule': sales.summary(product)}


def review(product, payload, adapter=None):
    binding = saved_state(product, payload)
    plan, inner = sales.review(product, adapter)
    # Recheck after potentially slow remote reads; source edits during comparison
    # cannot borrow the earlier ready state.
    if saved_state(product, payload) != binding:
        raise MetadataEditConflict('Source changed during Review. Reload and save before reviewing again.')
    token = drafts.serializer().dumps({'mode': 'sale_editor', 'product_id': product.id,
        'binding': binding, 'review': inner})
    return {'review': token, 'product': {'title': product.title, 'sku': product.sku},
        'variants': plan['variants'], 'start': uk_time(plan['start']) if plan['start'] else 'When due / no start boundary',
        'end': uk_time(plan['end']) if plan['end'] else 'Ongoing / no end boundary',
        'schedule': sales.summary(product), 'action': 'Authorise these saved prices for due activation and expiry',
        'warnings': ['Store currency; existing per-variant modifier amounts are preserved.',
            'This confirmation saves future-price authority only; it does not write Shopify prices now.',
            'Later source/default/identity changes invalidate authority. Shared editing does not authorise other products.',
            'Closing this modal does not pause or cancel an already saved authorisation.']}


def authorise(product, payload, adapter=None):
    binding = saved_state(product, payload)
    try:
        signed = drafts.serializer().loads(payload.get('review', ''), max_age=900)
    except (BadSignature, TypeError):
        raise MetadataEditConflict('Sale review is invalid or expired. Build a new review; no authorisation was saved.') from None
    if signed.get('mode') != 'sale_editor' or signed.get('product_id') != product.id or signed.get('binding') != binding:
        raise MetadataEditConflict('Saved source changed after Review. Save/scan and build a new review before authorising.')
    row = sales.authorise(product, signed['review'], payload.get('acknowledge') is True, adapter)
    return {'authorised': True, 'message': 'Sale authorisation saved. Only approved due transitions may execute.',
            'schedule': sales.summary(product)}
