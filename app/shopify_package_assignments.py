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
        proof={'version':1,'state':'pending','inventory_id':inventory,'variant_id':mapping['id'],'sku':entry['sku'],
            'local_package_id':entry['selected_id'],'shipping_package_id':link.remote_id,'source_revision':plan['digest'],
            'operation_id':operation_id,'api_version':'2026-10','independently_verified':False}
        proofs[key]=proof;data['stage']='package_assignment'
        row.state='updating';row.verification_state='unverified'
        persist(row,data,key,proof)  # committed BEFORE Shopify, independently visible to workers
        try:
            response=adapter.assign_package(config,inventory,link.remote_id)
            payload=response.get('inventoryItemUpdate')
            if isinstance(payload,dict) and isinstance(payload.get('userErrors'),list) and payload['userErrors']:
                proof.update(state='rejected',message='Shopify rejected the package input. Inspect the saved ID and inventory permission; no automatic retry.')
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
    data['stage']='package_assignment_accepted'
    row.state='verified';row.verification_state='verified'
    drafts.save(row,data)
    return 'accepted'


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
