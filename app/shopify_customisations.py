"""Reviewed product-owned public configuration on the existing Shopify transport.

Theme installation is deliberately separate. A verified configuration is not proof
of theme activation, checkout validation, customer upload or a completed order.
"""
import json
import re

from app import db
from app.models import Product, ShopifyProductIdentity
from app.shopify_connection import ShopifyAdapter, ShopifyError, _gid
from app.storefront_customisations import canonical, public_configuration

NAMESPACE = '$app:tlc_customisations'
KEY = 'configuration'


def desired(product):
    from app.taxonomy_assignments import product_document
    try:
        return public_configuration(product_document(product).get('customisation'))
    except (ValueError, KeyError, TypeError):
        raise ShopifyError('Storefront customisation definition is invalid, empty or contains an enabled file/photo field. Secure uploads are not implemented; correct the definition at its owning collection/template before publication.') from None


def _definition(adapter, config):
    value = adapter.customisation_definition(config, NAMESPACE, KEY)
    if value is not None:
        _gid(value.get('id'), 'MetafieldDefinition')
        if (value.get('key') != KEY or value.get('ownerType') != 'PRODUCT'
                or value.get('type', {}).get('name') != 'json'
                or not re.fullmatch(r'app--[0-9]+--tlc_customisations', value.get('namespace', ''))
                or not {'admin','storefront'}.issubset(value.get('access', {}))):
            raise ShopifyError('Customer configuration definition identity/type/access is untrusted; no replacement is permitted.')
    return value


def _value(adapter, config, product_id):
    value = adapter.customisation_value(config, product_id, NAMESPACE, KEY) if product_id else None
    if value is not None:
        _gid(value.get('id'), 'Metafield')
        if (value.get('key') != KEY or value.get('type') != 'json'
                or not re.fullmatch(r'app--[0-9]+--tlc_customisations', value.get('namespace', ''))
                or not isinstance(value.get('value'), str) or not isinstance(value.get('compareDigest'), str)):
            raise ShopifyError('Customer configuration value identity/type/CAS evidence is incomplete.')
        try:
            parsed = json.loads(value['value'])
            if not isinstance(parsed, dict): raise ValueError()
        except (TypeError, ValueError):
            raise ShopifyError('Existing customer configuration is not valid JSON; explicit recovery is required.') from None
    return value


def snapshot(adapter, config, product_id):
    field, value = _definition(adapter, config), _value(adapter, config, product_id)
    if value and (not field or value['namespace'] != field['namespace']):
        raise ShopifyError('Customer value does not belong to its verified app-owned definition.')
    return {'definition':field, 'value':value}


def attach(product, plan, config, shop, adapter, product_id=None, *, force=False):
    from app import shopify_drafts as drafts
    definition = desired(product)
    row = drafts.matching_identity(shop['shop_id'], product)
    retained = drafts.evidence(row) if row else {}
    managed = force or definition['enabled'] or bool(retained.get('storefront_configuration')) or bool(retained.get('plan', {}).get('customisations'))
    if not managed:
        return  # Existing local-only definitions have no new API calls or side effects.
    before = snapshot(adapter, config, product_id)
    proof = retained.get('storefront_configuration', {})
    if any(proof.get(proof_key) and proof[proof_key] != (before[remote_key] or {}).get('id')
           for proof_key,remote_key in (('definition_id','definition'),('metafield_id','value'))):
        raise ShopifyError('Retained customer definition/value identity was removed or replaced. Explicit recovery is required; no recreation or automatic adoption.')
    field = before['definition']
    access = {'admin':'MERCHANT_READ', 'storefront':'PUBLIC_READ'}
    manage_value = definition['enabled'] or before['value'] is not None
    settings_changed = manage_value and (not field or field['access'] != access)
    previous = json.loads(before['value']['value']) if before['value'] else None
    value_changed = manage_value and previous != definition
    # An explicit disabled configuration revokes the renderer; no remote deletion.
    spec = {'version':1, 'desired':definition, 'before':before,
        'access':access, 'definition_changed':settings_changed, 'value_changed':value_changed,
        'changed':settings_changed or value_changed}
    plan['customisations'] = spec
    plan['fields'].append({'field':'customisations.configuration',
        'before':_summary(previous), 'after':_summary(definition), 'changed':value_changed})
    plan['fields'].append({'field':'customisations.access', 'before':field['access'] if field else None,
        'after':access, 'changed':settings_changed})
    if spec['changed'] and 'write_products' not in shop['scopes']:
        plan['blockers'].append('Missing write_products for reviewed customer configuration/definition changes.')
    plan['warnings'].append('Public field definitions only. Theme app block installation/activation and real cart/order acceptance remain separate; file uploads, proof delivery and server-side checkout enforcement are not implemented.')
    if settings_changed:
        plan['warnings'].append('Definition access is shared by this app-owned configuration field across products. Only the displayed product value is changed; no other product is enabled.')
    plan['taxonomy_review_digest'] = drafts.digest({'previous':plan.get('taxonomy_review_digest'), 'customisations':spec})
    plan['unsupported'] = [v for v in plan.get('unsupported', []) if v != 'Customer-facing customisation']


def _summary(value):
    if value is None: return 'Not published'
    return {'enabled':value.get('enabled'), 'fields':len(value.get('fields', [])), 'revision':value.get('revision')}


def reserve(plan):
    """Budget the duplicated durable target before any write, not just the review."""
    spec = plan.get('customisations')
    return {'desired':spec['desired'], 'version':1, 'state':'pending', 'namespace':'x'*100,
        'definition_id':'x'*100,'metafield_id':'x'*100,'checked_at':'x'*100} if spec else {}


def apply(row, data, config, adapter):
    from app import shopify_drafts as drafts
    spec = data['plan'].get('customisations')
    if not spec or not spec['changed']: return
    current = snapshot(adapter, config, row.shopify_product_id)
    if current != spec['before']:
        raise ShopifyError('Customer configuration/definition changed after Review; no unreviewed overwrite is allowed.')
    proof = data['storefront_configuration'] = {'version':1, 'state':'pending',
        'desired':spec['desired'], 'config':config.fingerprint(), 'operation_id':row.operation_id,
        'definition_id':(current['definition'] or {}).get('id'),
        'metafield_id':(current['value'] or {}).get('id'), 'value_attempted':False}
    def fresh():
        product = db.session.get(Product, row.product_id)
        if (not product or product.source_relpath != row.source_identity or product.sku != row.sku
                or drafts.configuration().fingerprint() != config.fingerprint()
                or desired(product) != spec['desired']):
            raise ShopifyError('Public source/configuration changed during execution. Stop and reconcile successful earlier stages before another review.')
    if spec['definition_changed']:
        fresh()
        data['stage'] = 'customisation_definition'
        drafts.save(row, data)  # committed before the external request, including on Create
        if current['definition']:
            response = adapter.update_attribute_access(config, 'field', current['definition']['id'],
                {'namespace':NAMESPACE,'key':KEY,'access':spec['access']})
            drafts.payload_result(response, 'metafieldDefinitionUpdate')
        else:
            response = adapter.create_attribute_resource(config, 'field', {
                'name':'TLC customer customisations','namespace':NAMESPACE,'key':KEY,'type':'json',
                'ownerType':'PRODUCT','access':spec['access']})
            drafts.payload_result(response, 'metafieldDefinitionCreate')
        field = _definition(adapter, config)
        if not field or field['access'] != spec['access']:
            raise ShopifyError('Customer configuration definition readback differs; reconcile without retry.')
        proof['definition_id'], proof['namespace'] = field['id'], field['namespace']
        drafts.save(row, data)
    if spec['value_changed']:
        fresh()
        proof['value_attempted'] = True
        data['stage'] = 'customisation_configuration'
        drafts.save(row, data)
        response = adapter.set_attributes(config, [{'ownerId':row.shopify_product_id,
            'namespace':NAMESPACE, 'key':KEY, 'type':'json', 'value':canonical(spec['desired']),
            'compareDigest':(current['value'] or {}).get('compareDigest')}])
        drafts.payload_result(response, 'metafieldsSet')
    verify(row, data, config, adapter)


def verify(row, data, config, adapter):
    from app import shopify_drafts as drafts
    spec = data['plan'].get('customisations')
    scoped = data.get('customisation_update', {})
    if scoped.get('state') == 'verified' and scoped.get('operation_id') == row.operation_id:
        spec = scoped['plan']['customisations']
    if not spec: return
    current = snapshot(adapter, config, row.shopify_product_id)
    field, value = current['definition'], current['value']
    proof = data.get('storefront_configuration', {})
    if (not field or not value or field['access'] != spec['access']
            or json.loads(value['value']) != spec['desired']
            or proof.get('definition_id') not in (None, field['id'])
            or proof.get('metafield_id') not in (None, value['id'])):
        raise ShopifyError('Customer configuration identity/access/value verification differs. No automatic retry; reconcile the retained operation.')
    data['storefront_configuration'] = {**proof, 'version':1, 'state':'verified',
        'revision':spec['desired']['revision'], 'enabled':spec['desired']['enabled'],
        'checked_at':drafts.now().isoformat(), 'definition_id':field['id'],
        'metafield_id':value['id'], 'namespace':value['namespace']}
    drafts.save(row, data)


def preview(product, adapter=None):
    """Scoped configuration review for a retained product; never a second catalogue."""
    from app import shopify_drafts as drafts
    from app.taxonomy_assignments import product_document
    adapter = adapter or ShopifyAdapter()
    config = drafts.configuration()
    shop = adapter.test_connection(config)
    row = drafts.matching_identity(shop['shop_id'], product)
    if (product.catalogue_status != 'active' or product.product_type not in {'simple','variable'}
            or not row or row.state != 'verified' or row.verification_state != 'verified'
            or row.source_identity != product.source_relpath or row.sku != product.sku):
        raise ShopifyError('Customer-config-only Review requires the verified retained product; reconcile unresolved operations first. New products use the normal full Create review.')
    retained = drafts.evidence(row)
    proofs = [retained.get(k, {}) for k in ('native_dimensions','publication_update','inventory_update','sale_transition','customisation_update')]
    proofs += list(retained.get('variant_native', {}).values())
    if any(p.get('state') in {'pending','uncertain'} for p in proofs):
        raise ShopifyError('Recovery required for a retained stage. Reconcile it before reviewing customer configuration.')
    _remote_identity(row, retained, config, adapter)
    plan = {'product_id':product.id, 'sku':product.sku, 'title':product.title,
        'scope':'customisations', 'kind':'customisations',
        'source_identity':row.source_identity, 'identity_id':row.id, 'remote_id':row.shopify_product_id,
        'store_id':row.store_id, 'config_digest':config.fingerprint(), 'identity_digest':drafts.digest(retained),
        'digest':drafts.digest(product_document(product)), 'fields':[], 'blockers':[], 'warnings':[],
        'unsupported':['Metadata, pricing, inventory, media, collections, channels and proof delivery are deferred.'],
        'images':[], 'managed':{'product':{},'variant':{}}, 'price':'Deferred (configuration-only scope)', 'collections':[], 'intent':product_document(product).get('live'),
        'native':{'enabled':False}, 'publication':{},
        'actual_status':retained.get('observed_status', 'See retained verification'), 'handle':retained['plan']['handle']}
    attach(product, plan, config, shop, adapter, row.shopify_product_id, force=True)
    plan['classification'] = 'update' if plan['customisations']['changed'] else 'no_change'
    drafts.encode({**retained,'plan':plan,'original_product_plan':retained['plan'],
        'customisation_update':{'plan':plan},'storefront_configuration':reserve(plan),'reserve':'x'*12000})
    token = drafts.serializer().dumps({'mode':'customisations', **{k:plan[k] for k in (
        'product_id','scope','identity_id','remote_id','store_id','config_digest','identity_digest','digest','taxonomy_review_digest')}})
    return plan, token if plan['classification']=='update' and not plan['blockers'] else None


def _remote_identity(row, retained, config, adapter):
    from app import shopify_drafts as drafts
    namespace = retained['plan']['managed']['namespace']
    if retained.get('kind') == 'variable':
        from app.shopify_variables import remote_identity
        remote_identity(row, retained, adapter.read_variable(config, row.shopify_product_id, namespace))
    else:
        drafts.simple_identity(row, retained, adapter.read_simple(config, row.shopify_product_id, namespace))


def confirm(reviewed, adapter=None):
    from app import shopify_drafts as drafts
    from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation
    adapter = adapter or ShopifyAdapter()
    product = db.session.get(Product, reviewed['product_id'])
    row = db.session.get(ShopifyProductIdentity, reviewed['identity_id'])
    if not product or not row: raise ShopifyError('Reviewed product identity is unavailable.')
    lease = acquire_catalogue_operation('shopify_draft_publish', {'product_id':product.id,'mode':'customisations','scope':'customisations'})
    attempted = False
    try:
        plan, _ = preview(product, adapter)
        if plan['blockers'] or plan['classification'] != 'update' or any(plan.get(k) != v for k,v in reviewed.items() if k != 'mode'):
            raise ShopifyError('Reviewed source, store, identity, definition or configuration changed. Rebuild Review; no write attempted.')
        data = drafts.evidence(row)
        data['customisation_update'] = {'version':1, 'state':'pending', 'plan':plan,
            'config':plan['config_digest'], 'operation_id':lease.id}
        data['original_product_plan'] = data['plan']
        row.operation_id = lease.id
        drafts.save(row, data, 'updating')
        from app.shopify_comparison import invalidate
        invalidate(row.store_id, row.source_identity)
        scoped = {**data, 'plan':plan}
        attempted = True
        apply(row, scoped, drafts.configuration(), adapter)
        # Preserve the original full-product plan and its identities/media evidence.
        data['storefront_configuration'] = scoped['storefront_configuration']
        data['customisation_update']['state'] = 'verified'
        data['stage'] = 'customisation_verified'
        data.pop('original_product_plan', None)
        data.pop('error', None)
        drafts.save(row, data, 'verified')
        finish_catalogue_operation(lease.id,status='succeeded',products_attempted=1,products_succeeded=1,
            recovery_state='none',operation_summary={'identity_id':row.id,'scope':'customisations',
                'stage':'customisation_verified','deferred':'Full product comparison and theme activation are separate.'})
        return row
    except Exception as exc:
        db.session.rollback()
        message = str(exc) if isinstance(exc, ShopifyError) else 'Customer configuration outcome is uncertain; reconcile using reads only.'
        if attempted:
            data = drafts.evidence(row)
            data['customisation_update']['state'] = 'uncertain'
            data['error'] = message
            drafts.save(row, data, 'uncertain')
        finish_catalogue_operation(lease.id,status='partial' if attempted else 'failed',error=message,
            recovery_state='review_required' if attempted else 'none')
        if not attempted: raise ShopifyError(message) from None
        return row


def reconcile(row, data, config, adapter):
    """Read only. Never repeats definition creation or metafieldsSet after a timeout."""
    from app import shopify_drafts as drafts
    proof = data['customisation_update']
    if config.fingerprint() != proof['config']:
        raise ShopifyError('Restore the reviewed Shopify configuration before reconciliation.')
    retained = {**data, 'plan':data.get('original_product_plan', data['plan'])}
    # A crash during the scoped apply may have persisted the scoped plan.
    original = data['original_product_plan'] if 'original_product_plan' in data else data['plan']
    _remote_identity(row, {**retained, 'plan':original}, config, adapter)
    scoped = {**data, 'plan':proof['plan']}
    try:
        verify(row, scoped, config, adapter)
    except ShopifyError:
        current = snapshot(adapter, config, row.shopify_product_id)
        receipt = data.get('storefront_configuration', {})
        # Resolve a lost definition-only response without replaying it. A value
        # request was never sent, so a NEW signed review may authorise that stage.
        # If value_attempted is true, absence/old values cannot prove failure.
        if (receipt.get('value_attempted') is not False or not current['definition']
                or current['definition']['access'] != proof['plan']['customisations']['access']
                or current['value'] != proof['plan']['customisations']['before']['value']):
            raise
        proof['state'] = 'review_required'
        data.update(plan=original,stage='customisation_review_required',
            error='Definition identity/access resolved; configuration was never sent. Build a fresh customer-configuration-only Review to authorise it.')
        data.pop('original_product_plan', None)
        data['storefront_configuration'].update(state='review_required',definition_id=current['definition']['id'])
        row.verification_state = 'verified'
        drafts.save(row,data,'verified')
        return row
    data.update(plan=original, storefront_configuration=scoped['storefront_configuration'], stage='customisation_verified')
    data.pop('original_product_plan', None)
    data.pop('error', None)
    proof['state'] = 'verified'
    row.verification_state = 'verified'  # previous managed-field proof retained, not refreshed
    drafts.save(row, data, 'verified')
    return row
