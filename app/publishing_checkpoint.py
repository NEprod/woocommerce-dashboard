"""Explicit legacy validation, never a scan or a recurring source adopter."""
import json
from datetime import UTC
from pathlib import Path

from sqlalchemy import update

from app import db, automatic_publishing as publisher, shopify_drafts as drafts
from app.models import CatalogueOperation, CatalogueOperationItem, ShopifyPublishJob, ShopifyProductIdentity
from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation

SCAN_TYPES = {'append', 'product_update', 'shared_collection_update', 'full', 'reconstruction'}


def validate(product, *, actor_id, acknowledged):
    """Grant a current checkpoint only after explicit, confined source validation.

    Old dates alone grant nothing. Every current JSON/image must predate the
    committed scan; the existing publisher validates bytes and variant projection.
    Changed/unprovable sources require the normal targeted scan instead. The new
    receipt records validation NOW, referencing (not rewriting) the old scan.
    """
    publisher.require_admin(actor_id)
    if acknowledged is not True:
        raise ValueError('Explicit legacy checkpoint validation acknowledgement is required.')
    lease = acquire_catalogue_operation('publishing_checkpoint_validation', {
        'product_id': product.id, 'sku': product.sku, 'mode': 'read_only_source_validation',
        'actor_id': actor_id, 'write_attempted': False})
    try:
        job = db.session.get(ShopifyPublishJob, product.id, populate_existing=True)
        if (not job or job.owner or job.state != 'attention' or job.requested_revision != 'unscanned'
                or job.trigger != 'bootstrap' or job.operation_id or job.source_identity != product.source_relpath
                or product.catalogue_status != 'active'):
            raise ValueError('Only an unowned legacy bootstrap checkpoint can be validated here.')
        from app.shopify_publication import intent
        if intent(publisher.product_document(product))['status'] != 'ACTIVE':
            raise ValueError('Local/linked Draft publication is paused; no checkpoint adoption is needed.')
        identities = ShopifyProductIdentity.query.filter(
            (ShopifyProductIdentity.product_id == product.id) |
            (ShopifyProductIdentity.source_identity == product.source_relpath)).all()
        if identities:
            if len(identities) != 1:
                raise ValueError('Conflicting Shopify identities require safe recovery.')
            identity = identities[0]
            from app.shopify_notifications import complete
            prior = db.session.get(CatalogueOperation, identity.operation_id) if identity.operation_id else None
            if (identity.product_id != product.id or identity.source_identity != product.source_relpath
                    or not complete(identity, drafts.evidence(identity)) or
                    prior and (prior.status != 'succeeded' or prior.recovery_state != 'none')):
                raise ValueError('Incomplete or uncertain Shopify identity requires safe recovery, not checkpoint validation.')
        scan = db.session.query(CatalogueOperation).join(CatalogueOperationItem).filter(
            CatalogueOperation.operation_type.in_(SCAN_TYPES),
            CatalogueOperation.status.in_({'succeeded', 'partial'}),
            CatalogueOperation.recovery_state == 'none', CatalogueOperation.finished_at.isnot(None),
            CatalogueOperationItem.sku == product.sku,
            CatalogueOperationItem.source_path == product.source_relpath,
            CatalogueOperationItem.status == 'succeeded', CatalogueOperationItem.database_state == 'committed'
        ).order_by(CatalogueOperation.finished_at.desc()).first()
        if not scan or not product.resolved_row_json:
            raise ValueError('No committed source-bound historical scan/snapshot exists. Run the containing-collection update.')
        snapshot = json.loads(product.resolved_row_json)
        if not isinstance(snapshot, dict) or snapshot.get('SKU') != product.sku or snapshot.get('Type') != product.product_type:
            raise ValueError('Persisted scan snapshot does not match this product identity/type. Run the targeted update.')
        from app.metadata_workspace import metadata_source, _catalogue_root
        root = _catalogue_root()
        if not root:
            raise ValueError('Authoritative catalogue mount is unavailable.')
        files = []
        for kind in ('shared', 'override'):
            source = metadata_source(product, kind)
            if kind == 'override' and not source['exists'] and not product.override_json_relpath:
                continue
            if source['error'] or not source['validation']['valid']:
                details = source['error'] or '; '.join(issue['path'] + ': ' + issue['message']
                    for issue in source['validation']['errors'][:3])
                raise ValueError(f'{kind.title()} JSON is missing or invalid: {details}. Correct it and run the targeted update.')
            files.append(source['path'])
        # No remote calls, staging, marker writes, scanner dispatch or SKU allocation.
        plan = drafts.common_plan(product, variable=product.product_type == 'variable')
        if product.product_type == 'variable':
            from app.shopify_variables import projection
            projection(product)  # existing scanner-versus-current-input safety check
        files += [(root / image['source']).resolve(strict=True) for image in plan['images']]
        cutoff = scan.finished_at.replace(tzinfo=UTC).timestamp()
        stamps = []
        for path in dict.fromkeys(files):
            path = Path(path).resolve(strict=True)
            path.relative_to(root)
            info = path.stat()
            if not path.is_file() or max(info.st_mtime_ns, info.st_ctime_ns) / 1e9 > cutoff:
                raise ValueError('Source changed since its retained completed scan: ' + path.relative_to(root).as_posix() + '. Run the containing-collection update.')
            stamps.append((path, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
        revision = publisher.local_revision(product)
        if any((path.stat().st_size, path.stat().st_mtime_ns, path.stat().st_ctime_ns) != tuple(values)
               for path, *values in stamps):
            raise ValueError('Source changed during validation. No checkpoint established.')
        proof = {'validated_at': drafts.now().isoformat(), 'scan_operation_id': scan.id,
                 'revision': revision, 'files_validated': len(stamps), 'actor_id': actor_id,
                 'source_plan_digest': plan['digest'],
                 'catalogue_snapshot_digest': drafts.digest([product.resolved_row_json,
                     [(v.source_identity, v.resolved_row_json) for v in product.variations]]),
                 'images': len(plan['images']), 'source': product.source_relpath,
                 'validation_operation_id': lease.id, 'shopify_requests': 0, 'scans': 0}
        view = json.loads(job.view_json or '{}')
        view['checkpoint_validation'] = proof
        changed = db.session.execute(update(ShopifyPublishJob).where(
            ShopifyPublishJob.product_id == product.id, ShopifyPublishJob.owner.is_(None),
            ShopifyPublishJob.state == 'attention', ShopifyPublishJob.requested_revision == 'unscanned',
            ShopifyPublishJob.generation == job.generation, ShopifyPublishJob.operation_id.is_(None)
        ).values(requested_revision=revision, scan_operation_id=scan.id, state='pending', error=None,
                 trigger='checkpoint_validation', generation=job.generation + 1, verified_revision=None,
                 view_json=json.dumps(view), view_pending=True, updated_at=drafts.now(), next_attempt=drafts.now())).rowcount
        if changed != 1:
            raise ValueError('Queue ownership/revision changed. No checkpoint established.')
        db.session.commit()
        finish_catalogue_operation(lease.id, status='succeeded', products_attempted=1,
            products_succeeded=1, operation_summary=proof)
        return lease.id
    except Exception as exc:
        db.session.rollback()
        finish_catalogue_operation(lease.id, status='failed', error=exc, products_attempted=1,
            products_failed=1, operation_summary={'write_attempted': False, 'scans': 0, 'shopify_requests': 0})
        raise
