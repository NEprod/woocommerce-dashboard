"""Reviewed2026-10 InventoryItem package writes with acknowledgement, not readback.

Uses the existing product operation/identity evidence. An unknown outcome cannot be
reconciled by a measurement read that does not expose shippingPackageId.
"""
from app import db, shopify_drafts as drafts, package_links
from app.models import Product, CatalogueOperation
from app.shopify_connection import ShopifyError, _gid

POLICY=1


def completion(data):
    """Operation completion is not independent package readback verification.

    Require an acknowledgement for every reviewed assignment, bound to the
    exact inventory/package target. Historical unrelated receipts cannot prove it.
    """
    proofs = data.get('package_assignments', {})
    if any(p.get('state') in {'pending', 'uncertain'} for p in proofs.values()):
        return 'uncertain'
    accepted = False
    for entry in data.get('plan', {}).get('optional_packages', []):
        proof = proofs.get(entry['identity'], {})
        required = entry.get('action') == 'assign'
        same = (entry.get('shipping_package_id') and
                proof.get('shipping_package_id') == entry['shipping_package_id'] and
                proof.get('inventory_id') == (entry.get('inventory_id') or
                    data.get('variant_maps', {}).get(entry['identity'], {}).get('inventory_id') or
                    data.get('inventory_item_id')))
        if required and (not same or proof.get('state') != 'accepted'):
            return 'rejected' if same and proof.get('state') == 'rejected' else 'outstanding'
        accepted = accepted or bool(same and proof.get('state') == 'accepted')
    return 'accepted' if accepted else 'none'


def persist(row,data,key,proof):
    import json
    operation=db.session.get(CatalogueOperation,proof['operation_id'])
    if not operation or operation.status!='running':raise ShopifyError('Package assignment requires the active durable product operation.')
    scope=json.loads(operation.scope or '{}')
    scope.setdefault('package_assignments',{})[key]=dict(proof)
    operation.scope=json.dumps(scope,sort_keys=True)
    drafts.save(row,data)


def guard(data):
    if any(p.get('state') in {'pending','uncertain'} for p in data.get('package_assignments',{}).values()):
        raise ShopifyError('Recovery Required: a saved-package assignment has an uncertain outcome. Shopify has no assignment readback. Inspect the exact variant in Shopify Admin; do not replay the write.')


def attempted(data,operation_id):
    return any(p.get('operation_id')==operation_id and p.get('state') in {'pending','uncertain','accepted'} for p in data.get('package_assignments',{}).values())


def binding(plan):
    return [{k:e.get(k) for k in ('identity','selected_id','shipping_package_id','link_revision','inventory_id','action','permission')}
        for e in plan.get('optional_packages',[])]


def attach(plan,row,config,shop):
    data=drafts.evidence(row) if row else {}
    guard(data)
    receipts=data.get('package_assignments',{})
    variants={v['identity']:v for v in plan.get('variants',[])}
    pending=False;incomplete=False
    for entry in plan.get('optional_packages',[]):
        key=entry['identity'];variant=variants.get(key,{})
        inventory=variant.get('inventory_id') if variants else plan.get('inventory_item_id')
        prior=receipts.get(key,{})
        entry.update(shipping_package_id=None,inventory_id=inventory,action='none',permission='write_inventory' in shop['scopes'])
        if entry['selected_id']:
            try:
                link=package_links.find(entry['selected_id'],config,shop['shop_id'])
                if link:
                    entry['shipping_package_id']=package_links.validate(link.remote_id)
                    entry['link_revision']=drafts.digest([link.id,link.store_id,link.remote_id,link.observed])
            except ValueError:
                entry['state']='Package link conflict — review needed'
            if not entry['shipping_package_id']:
                entry['state']='Shopify Package ID required'
                entry['remote_state']='Selected locally. No fabricated ID or package mutation; other fields can publish.'
                incomplete=True
            else:
                same=prior.get('state')=='accepted' and prior.get('shipping_package_id')==entry['shipping_package_id'] and prior.get('inventory_id')==inventory
                entry['state']='Shopify accepted — not independently verified' if same else 'Selected — Pending Shopify acceptance'
                entry['remote_state']='No public shippingPackageId readback exists. Acknowledgement is not independent remote verification.'
                if not same and entry['permission']:
                    entry['action']='assign';pending=True
                    plan['fields'].append({'field':'package_assignment.'+entry['sku'],'before':prior.get('shipping_package_id') or 'Not readable',
                        'after':entry['shipping_package_id'],'changed':True,'package_assignment':True})
                elif not same:
                    entry['state']='Missing write_inventory — assignment deferred'
                    incomplete=True
                incomplete=True  # accepted is not an independently verified value
        elif prior.get('shipping_package_id'):
            entry['state']='Explicitly Unassigned — manual Shopify clearing needed'
            entry['remote_state']='Local no-package state retained. Nullable ID input does not document clearing semantics; no null mutation is sent.'
            incomplete=True
    plan['package_assignments_pending']=pending
    plan['package_assignments_unverified']=incomplete
    plan['package_assignment_policy']=POLICY
    if pending and plan.get('classification')=='no_change':plan['classification']='update'
    plan['taxonomy_review_digest']=drafts.digest({'previous':plan['taxonomy_review_digest'],'package_assignments':binding(plan)})


def apply(row,adapter,operation_id):
    data=drafts.evidence(row);plan=data['plan']
    entries=[e for e in plan.get('optional_packages',[]) if e.get('action')=='assign']
    if not entries:return 'none'
    guard(data)
    if data.get('native_dimensions',{}).get('state') in {'pending','uncertain'} or any(p.get('state') in {'pending','uncertain'} for p in data.get('variant_native',{}).values()):
        return 'blocked'
    if row.state!='verified':return 'blocked'  # media must finish; no background write
    config=drafts.configuration();shop=adapter.test_connection(config)
    if config.api_version!='2026-10' or shop['shop_id']!=row.store_id or 'write_inventory' not in shop['scopes']:
        return 'blocked'
    product=db.session.get(Product,row.product_id)
    if drafts.local_plan(product)['digest']!=plan['digest'] or config.fingerprint()!=plan['config_digest']:
        return 'blocked'
    proofs=data.setdefault('package_assignments',{})
    for entry in entries:
        key=entry['identity']
        mapping=data.get('variant_maps',{}).get(key,{}) if data.get('kind')=='variable' else {
            'id':data.get('variant_id'),'inventory_id':data.get('inventory_item_id'),'sku':row.sku}
        inventory=_gid(mapping.get('inventory_id'),'InventoryItem')
        _gid(mapping.get('id'),'ProductVariant')
        if mapping.get('sku')!=entry['sku'] or entry.get('inventory_id') not in (None,inventory):
            raise ShopifyError('Reviewed package variant/InventoryItem identity differs. No assignment attempted.')
        link=package_links.find(entry['selected_id'],config,row.store_id)
        if not link or entry.get('link_revision')!=drafts.digest([link.id,link.store_id,link.remote_id,link.observed]) or link.remote_id!=entry['shipping_package_id']:
            return 'blocked'
        previous=proofs.get(key,{})
        if previous.get('state')=='accepted' and previous.get('inventory_id')==inventory and previous.get('shipping_package_id')==entry['shipping_package_id']:
            continue
        result=submit(row,data,entry,mapping,config,adapter,operation_id,plan['digest'])
        if result!='accepted':return result
    data['stage']='package_assignment_accepted'
    row.state='verified';row.verification_state='verified'
    drafts.save(row,data)
    return 'accepted'


def submit(row,data,entry,mapping,config,adapter,operation_id,source_revision):
    """The same one-assignment acknowledgement path for publishing and correction."""
    inventory=_gid(mapping['inventory_id'],'InventoryItem');key=entry['identity']
    proof={'version':1,'state':'pending','inventory_id':inventory,'variant_id':mapping['id'],'sku':entry['sku'],
            'local_package_id':entry['selected_id'],'shipping_package_id':entry['shipping_package_id'],'source_revision':source_revision,
            'operation_id':operation_id,'api_version':'2026-10','independently_verified':False}
    data.setdefault('package_assignments',{})[key]=proof;data['stage']='package_assignment'
    row.state='updating';row.verification_state='unverified'
    persist(row,data,key,proof)  # committed BEFORE Shopify, independently visible to workers
    try:
        response=adapter.assign_package(config,inventory,entry['shipping_package_id'])
        payload=response.get('inventoryItemUpdate')
        if isinstance(payload,dict) and isinstance(payload.get('userErrors'),list) and payload['userErrors']:
            from app.utils.redaction import redact_diagnostic
            errors = [{'field': redact_diagnostic('.'.join(str(part) for part in
                (error.get('field') if isinstance(error.get('field'), list) else [])[:8]), limit=200),
                'message': redact_diagnostic(error.get('message', 'Package input rejected'), limit=350)}
                for error in payload['userErrors'][:5] if isinstance(error, dict)]
            proof.update(state='rejected',errors=errors,
                message='Shopify rejected the package input. Inspect the retained safe errors; no automatic retry.')
            row.state='verified';row.verification_state='verified'
            persist(row,data,key,proof);return 'blocked'
        if not isinstance(payload,dict) or payload.get('userErrors')!=[] or (payload.get('inventoryItem') or {}).get('id')!=inventory:
            raise ShopifyError('Incomplete package mutation acknowledgement.')
        proof.update(state='accepted',acknowledged_at=drafts.now().isoformat(),message='Shopify accepted this input. Assigned package is not independently readable.')
        persist(row,data,key,proof)
        drafts.completed_step(row,data,'package_assignment_accepted')
    except Exception as error:
        definite=isinstance(error,ShopifyError) and error.category=='authentication'
        proof.update(state='rejected' if definite else 'uncertain',message='Shopify rejected authentication before the assignment.' if definite else
            'Assignment outcome uncertain; no automatic replay or false readback verification. Inspect the exact variant in Shopify Admin.')
        if not definite:row.state='uncertain';row.verification_state='unverified'
        else:row.state='verified';row.verification_state='verified'
        persist(row,data,key,proof)
        return 'blocked' if definite else 'uncertain'
    return 'accepted'


def correction_review(product, adapter=None):
    """Fresh, read-only approval for ONE package on a retained Simple product.

    This is not recovery of the missing response. The merchant deliberately
    authorises a new assignment after inspecting the exact variant in Admin.
    All other domains must already have conclusive managed-product evidence.
    """
    import json
    from app.models import ShopifyPublishJob
    from app.automatic_publishing import local_revision
    from app.shopify_connection import ShopifyAdapter
    adapter = adapter or ShopifyAdapter()
    config = drafts.configuration()
    shop = adapter.test_connection(config)
    row = drafts.matching_identity(shop['shop_id'], product)
    if (product.product_type != 'simple' or not row or not row.shopify_product_id
            or row.source_identity != product.source_relpath or row.sku != product.sku
            or config.api_version != '2026-10' or 'write_inventory' not in shop['scopes']):
        raise ShopifyError('Package correction requires one retained Simple product, trusted source identity and write_inventory on API2026-10.')
    data = drafts.evidence(row)
    verification = data.get('verification', {})
    if any(verification.get(k) != 'verified' for k in ('identity','fields','media','memberships','attributes')):
        raise ShopifyError('Complete managed-product verification is required before a package-only correction; no write was attempted.')
    for key in ('native_dimensions','publication_update','inventory_update','customisation_update','sale_transition','verification_wait'):
        if data.get(key, {}).get('state') in {'pending','uncertain','recovery_required'}:
            raise ShopifyError('Another unresolved '+key+' stage blocks package-only correction.')
    from copy import deepcopy
    from types import SimpleNamespace
    from app.shopify_notifications import complete
    managed = deepcopy(data)
    managed['package_assignments'] = {}
    managed['plan']['optional_packages'] = []
    if not complete(SimpleNamespace(state='verified',verification_state='verified'),managed):
        raise ShopifyError('Unresolved non-package verification blocks this package-only correction.')
    original = db.session.get(CatalogueOperation, row.operation_id)
    if not original or original.status in {'running','pending','interrupted'}:
        raise ShopifyError('A completed retained operation is required; active/interrupted ownership cannot be bypassed.')
    # A package correction cannot settle an uncertain product mutation. Require
    # the readback to be bound to, or later than, the retained failed operation.
    product_operation = original
    # Earlier package-only corrections never renewed the product readback.
    # Follow their explicit history links, not a blanket age exemption.
    for _ in range(10):
        prior_scope = json.loads(product_operation.scope or '{}')
        if prior_scope.get('mode') != 'package_correction': break
        prior_id = prior_scope.get('manual_review', {}).get('reviewed', {}).get('original_operation')
        previous = db.session.get(CatalogueOperation, prior_id) if prior_id else None
        if not previous or json.loads(previous.scope or '{}').get('product_id') != product.id:
            raise ShopifyError('Retained package correction history is incomplete; no new assignment is allowed.')
        product_operation = previous
    try:
        from datetime import datetime
        bound = (verification.get('operation_id') == product_operation.id or
            datetime.fromisoformat(verification.get('checked_at','').removesuffix('Z')) >= product_operation.started_at)
    except (ValueError, TypeError):
        bound = False
    if not bound:
        raise ShopifyError('Managed-product evidence does not cover the retained operation; inspect identity/readback first.')
    revision = local_revision(product)
    job = db.session.get(ShopifyPublishJob, product.id)
    if job and (job.owner or job.requested_revision != revision or not job.scan_operation_id
            or job.state == 'recovery_required' and job.operation_id not in {None,original.id}):
        raise ShopifyError('Current completed source checkpoint and unowned queue are required; no scan or write was started.')
    plan = drafts.local_plan(product)
    if plan['blockers']:
        raise ShopifyError('Current local validation blocks package correction: '+str(plan['blockers'][0]))
    from app.shopify_optional_packages import attach as attach_packages
    attach_packages(product, plan, row)
    entries = plan.get('optional_packages', [])
    if len(entries) != 1 or not entries[0].get('selected_id'):
        raise ShopifyError('Select one authoritative local package before reviewing its assignment.')
    entry = dict(entries[0])
    link = package_links.find(entry['selected_id'], config, shop['shop_id'])
    if not link:
        raise ShopifyError('The selected local package has no trusted Shopify package mapping.')
    remote = adapter.read_simple(config, row.shopify_product_id, plan['managed']['namespace'])
    variant = drafts.simple_identity(row, data, remote)
    if variant.get('sku') != row.sku or variant['inventoryItem'].get('sku') != row.sku:
        raise ShopifyError('Exact variant/InventoryItem SKU differs; package correction is blocked.')
    entry.update(shipping_package_id=package_links.validate(link.remote_id), inventory_id=variant['inventoryItem']['id'],
        variant_id=variant['id'], link_revision=drafts.digest([link.id,link.store_id,link.remote_id,link.observed]), action='assign')
    previous = data.get('package_assignments', {}).get(entry['identity'], {})
    if (previous.get('state') == 'accepted' and previous.get('inventory_id') == entry['inventory_id']
            and previous.get('shipping_package_id') == entry['shipping_package_id']):
        raise ShopifyError('This unchanged assignment already has Shopify Accepted evidence. No duplicate assignment is needed.')
    reviewed = {'mode':'package_correction','scope':'package_only','product_id':product.id,'identity_id':row.id,
        'digest':plan['digest'],'intent':plan['intent'],'config_digest':config.fingerprint(),'source_revision':revision,
        'identity_digest':drafts.digest(data),'remote_digest':drafts.digest(remote),
        'original_operation':original.id,'original_scope_digest':retained_binding(original),
        'store_id':row.store_id,'remote_id':row.shopify_product_id,'package':entry}
    return reviewed, drafts.serializer().dumps(reviewed)


def correction_confirm(reviewed, adapter=None):
    """Existing manual executor/lease; never an automatic replay or new engine."""
    import json
    from app.models import ShopifyProductIdentity, ShopifyPublishJob
    from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation
    from app.shopify_connection import ShopifyAdapter
    adapter = adapter or ShopifyAdapter()
    product = db.session.get(Product, reviewed['product_id'])
    # This obtains the already accepted manual lease inside its fenced worker.
    lease = acquire_catalogue_operation('shopify_draft_publish', {'product_id':product.id,'mode':'package_correction'})
    row = db.session.get(ShopifyProductIdentity, reviewed['identity_id'])
    if row and row.operation_id == lease.id:
        # A committed acknowledgement can be finalised after a worker crash;
        # a pending/unknown response is NEVER submitted again.
        return finish_correction(row, reviewed, lease.id)
    fresh, _ = correction_review(product, adapter)
    if fresh != reviewed:
        raise ShopifyError('Package/source/mapping/identity or Shopify revision changed after approval. Fresh review required; no assignment attempted.')
    data = drafts.evidence(row)
    entry = reviewed['package']
    data['package_correction'] = {'operation_id':lease.id,'original_operation':reviewed['original_operation'],
        'original_scope_digest':reviewed['original_scope_digest'],'digest':reviewed['digest'],
        'package':entry,'state':'approved'}
    # Original scope, summary and receipts stay immutable. The new operation
    # owns only this assignment; retained metadata timestamps are not renewed.
    row.operation_id = lease.id
    operation = db.session.get(CatalogueOperation,lease.id)
    scope = json.loads(operation.scope)
    scope['package_correction'] = data['package_correction']
    operation.scope = json.dumps(scope)
    job = db.session.get(ShopifyPublishJob, product.id)
    if job:
        job.operation_id = lease.id
    data['completed_steps'] = []
    data['plan']['optional_packages'] = [entry]
    data.pop('error', None)
    drafts.save(row, data)
    mapping = {'id':entry['variant_id'],'inventory_id':entry['inventory_id'],'sku':entry['sku']}
    result = submit(row,data,entry,mapping,drafts.configuration(),adapter,lease.id,reviewed['digest'])
    if result != 'accepted':
        data['package_correction']['state'] = data['package_assignments'][entry['identity']]['state']
        scope = json.loads(operation.scope)
        scope['package_correction'] = data['package_correction']
        operation.scope = json.dumps(scope)
        drafts.save(row,data)
        finish_catalogue_operation(lease.id,status='partial' if result == 'uncertain' else 'failed',
            recovery_state='review_required' if result == 'uncertain' else 'none',error=data['package_assignments'][entry['identity']]['message'],
            products_attempted=1,products_failed=1,
            operation_summary={'mode':'package_correction','stage':'package_assignment','write_attempted':True})
        return row
    return finish_correction(row, reviewed, lease.id)


def finish_correction(row, reviewed, operation_id):
    """Local finalisation from an EXACT persisted acknowledgement; no API call."""
    import json
    from app.models import ShopifyPublishJob
    from app.utils.operation_control import finish_catalogue_operation
    data = drafts.evidence(row)
    entry = reviewed['package']
    proof = data.get('package_assignments', {}).get(entry['identity'], {})
    if (proof.get('state') != 'accepted' or proof.get('operation_id') != operation_id
            or not proof.get('acknowledged_at') or proof.get('source_revision') != reviewed['digest']
            or any(proof.get(k) != value for k,value in {'variant_id':entry['variant_id'],
                'inventory_id':entry['inventory_id'],'shipping_package_id':entry['shipping_package_id']}.items())):
        raise ShopifyError('Interrupted package correction has no exact acknowledgement. Recovery Required; no automatic replay.')
    row.state = row.verification_state = 'verified'
    data['package_correction']['state'] = 'accepted'
    data['stage'] = 'package_assignment_accepted'
    op = db.session.get(CatalogueOperation, operation_id)
    scope = json.loads(op.scope)
    scope['package_correction'] = data['package_correction']
    op.scope = json.dumps(scope)
    job = db.session.get(ShopifyPublishJob, row.product_id)
    if job and not job.owner and job.operation_id in {None,reviewed['original_operation'],operation_id}:
        # Re-open comparison, not an invented verified revision/checkpoint.
        job.state = 'pending' if (reviewed['intent'].upper() == 'ACTIVE'
            or reviewed['intent'].upper() == 'DRAFT' and data.get('observed_status') != 'DRAFT') else 'paused'
        job.operation_id = operation_id
        job.error = None; job.verified_revision = None; job.view_pending = True
        job.updated_at = drafts.now()
    drafts.save(row,data)
    finish_catalogue_operation(operation_id,status='succeeded',recovery_state='none',
        products_attempted=1,products_succeeded=1,products_failed=0,
        operation_summary={'mode':'package_correction','stage':'package_assignment_accepted','write_attempted':True,
            'package_assignment':'accepted','supersedes':reviewed['original_operation']})
    return row


def superseded_operations(rows):
    """Derive active recovery resolution; never rewrite the original receipt."""
    from app.shopify_notifications import scope_of
    by_id = {row.id:row for row in rows}
    resolved = set()
    for row in rows:
        if row.status != 'succeeded' or row.recovery_state not in {None,'none'}:
            continue
        scope = scope_of(row)
        correction = scope.get('package_correction')
        if not isinstance(correction,dict): continue
        old = by_id.get(correction.get('original_operation'))
        if not old or correction.get('state') != 'accepted' or retained_binding(old) != correction.get('original_scope_digest'):
            continue
        reviewed = scope.get('manual_review', {}).get('reviewed') or {}
        entry = reviewed.get('package') or {}
        proof = scope.get('package_assignments', {}).get(entry.get('identity'), {})
        if (scope.get('mode') == reviewed.get('mode') == 'package_correction'
                and scope.get('product_id') == scope_of(old).get('product_id') == reviewed.get('product_id')
                and reviewed.get('original_operation') == old.id and proof.get('operation_id') == row.id
                and proof.get('state') == 'accepted' and proof.get('acknowledged_at')
                and proof.get('source_revision') == reviewed.get('digest')
                and all(entry.get(k) and proof.get(k) == entry[k] for k in ('variant_id','inventory_id','shipping_package_id'))):
            resolved.add(old.id)
            # A later explicitly approved correction can also supersede an
            # earlier package-only uncertain correction and its original issue.
            previous = old
            for _ in range(10):
                prior = scope_of(previous)
                if prior.get('mode') != 'package_correction': break
                review = prior.get('manual_review', {}).get('reviewed') or {}
                previous = by_id.get(review.get('original_operation'))
                if (not previous or review.get('original_scope_digest') != retained_binding(previous)
                        or scope_of(previous).get('product_id') != reviewed.get('product_id')):
                    break
                resolved.add(previous.id)
    return resolved


def retained_binding(operation):
    """Stable mutation history, excluding heartbeat/Discord delivery bookkeeping."""
    from app.shopify_notifications import scope_of
    scope = scope_of(operation)
    return drafts.digest({k:scope.get(k) for k in ('product_id','mode','operation_summary','package_assignments','package_correction','manual_review')})


def fingerprint(product,authored):
    """Cheap central-ID invalidation; never calls Shopify or calculates prices."""
    from app.packaging_contract import effective
    try:config=drafts.configuration()
    except ValueError:config=None
    variants=product.variations if product.product_type=='variable' else [None]
    result=[]
    for variant in variants:
        value=effective(authored,{a.name:a.value for a in variant.attributes} if variant else None)
        if 'selected_package_id' not in value:continue
        selected=value['selected_package_id'];link=None
        if selected and config:
            try:link=package_links.find(selected,config)
            except ValueError:pass
        result.append([variant.sku if variant else product.sku,selected,link.remote_id if link else None])
    return result
