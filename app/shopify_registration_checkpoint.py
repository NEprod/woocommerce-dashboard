"""Resume only the attribute tail of an accepted manual Review & Sync."""
from app import db, shopify_drafts as drafts
from app.models import CatalogueOperation
from app.shopify_connection import ShopifyError


def binding(row, tail):
    return {'operation': row.operation_id, 'source': row.reviewed_digest,
            'store': row.store_id, 'product': row.shopify_product_id, 'tail': tail}


def pending(row):
    if not row or row.state not in {'created', 'pending', 'updating'} or not row.shopify_product_id:
        return False
    data = drafts.evidence(row)
    mark = data.get('registration_checkpoint') or {}
    tail = mark.get('tail')
    if not tail or data.get('continuation') or data.get('dispatch'):
        return False
    operation = db.session.get(CatalogueOperation, row.operation_id)
    return (tail in {'variable', 'simple_create', 'simple_update'}
            and data.get('plan', {}).get('digest') == row.reviewed_digest
            and data['plan'].get('store_id') == row.store_id
            and mark == binding(row, tail)
            and operation and operation.status in {'pending', 'running'}
            and operation.recovery_state in {None, 'none'})


def apply(row, data, config, adapter, tail):
    from app.manual_publishing import executing_lease
    from app.shopify_attributes import apply as attributes
    plan = data['plan']
    if not any(a['changed'] for a in plan.get('attributes', ())):
        return
    if data.get('continuation'):
        return attributes(row,data,config,adapter)
    lease = executing_lease()
    if lease and lease.id == row.operation_id and not pending(row):
        data['registration_checkpoint'] = binding(row, tail)
        drafts.save(row, data)
    attributes(row, data, config, adapter)
    if data.pop('registration_checkpoint', None):
        drafts.save(row, data)


def resume(row, adapter):
    from app.shopify_capacity import BackgroundYield
    if not pending(row):
        raise ShopifyError('Retained attribute checkpoint was not verified. Reconcile before any write.')
    data = drafts.evidence(row)
    config = drafts.configuration()
    try:
        if (config.fingerprint() != data['plan']['config_digest']
                or adapter.test_connection(config)['shop_id'] != row.store_id):
            raise ShopifyError('Retained attribute checkpoint belongs to a different store or connection.')
        tail = data['registration_checkpoint']['tail']
        if tail == 'variable':
            from app.shopify_variables import finish_registration_tail
            return finish_registration_tail(row, data, config, adapter)
        return drafts.finish_registration_tail(row, data, config, adapter, updating=tail == 'simple_update')
    except BackgroundYield:
        from app.shopify_variable_readback import pending as verification_pending
        if pending(row) or verification_pending(row):
            raise
        raise ShopifyError('Execution deferred beyond the safe registration checkpoint; reconcile retained work.') from None
