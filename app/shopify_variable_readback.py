"""Acknowledged variable writes: bounded read-only continuation on the same receipt.

No new queue, authoring schema or retry of mutations. The durable marker is written
only AFTER required metadata mutations returned acknowledgements.
"""
import copy
import json
from datetime import datetime,timedelta
from app import db,shopify_drafts as drafts
from app.models import CatalogueOperation,Product
from app.shopify_connection import ShopifyError,ShopifySnapshotChanged,_gid
from app.shopify_capacity import BackgroundYield,fence_owner,guard_owner

class ReadbackLag(ShopifyError):pass


def _verified_base(verification,operation_id):
    return (verification.get('operation_id')==operation_id
        and all(verification.get(k)=='verified' for k in ('identity','fields','media','memberships','attributes')))


def acknowledge(row,data,payload,wanted,stage,*,commit=True):
    nodes=payload.get('productVariants')
    if not isinstance(nodes,list):raise ShopifyError('Variant acknowledgement missing returned identities; retain recovery, never replay.')
    mappings={};ids=set()
    for target in wanted:
        matches=[n for n in nodes if n.get('sku')==target['sku'] and n.get('selectedOptions')==target['options']]
        if len(matches)!=1:raise ShopifyError('Variant acknowledgement is incomplete/ambiguous for '+target['sku']+'; no mutation retry.')
        actual=matches[0];vid=_gid(actual.get('id'),'ProductVariant');iid=_gid((actual.get('inventoryItem') or {}).get('id'),'InventoryItem')
        if vid in ids or target.get('remote_id') not in {None,vid}:raise ShopifyError('Returned variant identity conflicts with its reviewed target.')
        ids.add(vid)
        before=next((f['before'] for f in data['plan']['fields'] if f.get('variant_identity')==target['identity'] and f.get('leaf')=='sku'),
            '' if stage=='variant_update' and not data.get('update_attempted') else target['sku'])
        mappings[target['identity']]={'id':vid,'inventory_id':iid,'sku':target['sku'],'options':target['options']}
        data.setdefault('variant_ack_before_sku',{})[vid]=before
    if any(m['id'] in ids for k,m in data.get('variant_maps',{}).items() if k not in mappings):
        raise ShopifyError('Returned variant IDs overlap another retained identity.')
    data.setdefault('variant_maps',{}).update(mappings)
    data.setdefault('variant_write_acknowledgements',[]).append({'operation_id':row.operation_id,'stage':stage,
        'at':drafts.now().isoformat(),'variants':copy.deepcopy(mappings)})
    if commit:
        drafts.completed_step(row,data,stage)  # acknowledgement AND mappings in the same fenced commit


def pending(row):
    data=drafts.evidence(row);w=data.get('verification_wait') or {};op=db.session.get(CatalogueOperation,row.operation_id)
    return bool(not data.get('dispatch') and data.get('kind')=='variable' and w.get('operation_id')==row.operation_id
        and w.get('source_digest')==row.reviewed_digest==data.get('plan',{}).get('digest')
        and op and op.recovery_state in {None,'none'} and op.status in {'running','pending','partial'}
        and ((row.state=='processing' and w.get('state') in {'pending','waiting_authority'})
             or row.state=='verified' and w.get('state')=='completed' and op.status in {'running','pending'}
             or row.state=='verified' and w.get('state')=='pending' and w.get('phase')=='base'
             and _verified_base(data.get('verification') or {},row.operation_id)
             or row.state=='updating' and w.get('state')=='pending' and w.get('phase')=='publication'
             and _completion_checkpoint(data,row.operation_id)=='completion'
             or row.state in {'verified','updating'} and w.get('state')=='writing'
             and w.get('phase')=='completion' and _completion_checkpoint(data,row.operation_id)))


def begin(row,data,phase):
    old=data.get('verification_wait') or {}
    if old.get('operation_id')!=row.operation_id:old={}
    base_receipt=old.get('base_receipt')
    verified=data.get('verification') or {}
    if not base_receipt and phase=='completion' and _verified_base(verified,row.operation_id):
        # Keep the original readback even when a later verification commits only
        # partial observations. This is audit evidence, not a substitute for a read.
        base_receipt=copy.deepcopy(verified)
    data['verification_wait']={'operation_id':row.operation_id,'source_digest':data['plan']['digest'],
        'state':'pending','phase':phase,'attempts':old.get('attempts',0) if old.get('phase')==phase else 0,
        'started_at':old.get('started_at') or drafts.now().isoformat()}
    if base_receipt:data['verification_wait']['base_receipt']=base_receipt
    data['stage']='verification_pending';row.verification_state='processing'
    drafts.save(row,data,'processing')


def _pause(row,data,reason,*,count=True):
    from app.publishing_progress import update
    from app.utils.operation_control import release_local_operation
    w=data['verification_wait'];w['last_reason']=str(reason)[:500];w['attempts']+=int(count)
    if count and (w['attempts']>=12 or drafts.now()-datetime.fromisoformat(w['started_at'])>timedelta(minutes=10)):
        message='Shopify writes were acknowledged but bounded verification is still incomplete. Read-only recovery required; no mutation was repeated.'
        w['state']='recovery_required';data['error']=message;row.verification_state='unverified';drafts.save(row,data,'uncertain')
        from app.utils.operation_control import finish_catalogue_operation
        finish_catalogue_operation(row.operation_id,status='partial',recovery_state='review_required',error=message,
            products_attempted=1,products_succeeded=0,products_failed=0)
        raise ShopifyError(message)
    if w['state']=='completed':w['state']='pending'  # A deferred final check remains a final read.
    data['error']=None;data['stage']='verification_pending';row.verification_state='processing'
    drafts.save(row,data,'processing')
    op=db.session.get(CatalogueOperation,row.operation_id)
    op.status='pending';op.finished_at=None;op.error=None;op.products_attempted=1;op.products_succeeded=0;op.products_failed=0
    update(op,stage='verification_pending',status='waiting',message='Shopify writes acknowledged; awaiting complete readback. No writes will be repeated. '+str(reason)[:250])
    from app.catalogue_views import invalidate
    invalidate(product_id=row.product_id)
    fence_owner();db.session.commit();release_local_operation(op.id)
    return row


def publication_acknowledged(data):
    p=data.get('publication_update') or {};plan=p.get('plan') or {}
    required={'publication.'+a['id'] for a in plan.get('actions',[]) if a['changed']}
    required.update('publication.'+a['id'] for a in (plan.get('markets') or {}).get('actions', []) if a['changed'])
    if (plan.get('observed') or {}).get('status')!=(plan.get('intent') or {}).get('status'):required.add('publication.status')
    return bool(p.get('unified') and p.get('operation_id')==data.get('publication_review_operation_id')
        and required.issubset(set(p.get('completed',[]))))


def _completion_checkpoint(data,operation_id):
    """A completion preflight can yield; a reserved mutation cannot be replayed."""
    # Native/package continuation has its own evidence requirements. Do not
    # restart those writers if this operation reserved or handled an item.
    if data.get('dispatch'):
        return None
    resumable = (data.get('continuation') or {}).get('operation')==operation_id
    if any((not resumable and proof.get('operation_id')==operation_id) or proof.get('state') in {'pending','uncertain'}
           for key in ('variant_native','package_assignments') for proof in (data.get(key) or {}).values()):
        return None
    proof=data.get('publication_update') or {}
    if not proof or proof.get('operation_id')!=operation_id and proof.get('state')=='verified':
        return 'completion'
    if (proof.get('operation_id')==operation_id and publication_acknowledged(data)
            and proof.get('plan')==data['plan'].get('publication')):
        if proof.get('state')=='pending':return 'publication'  # read acknowledgement only
        if proof.get('state')=='verified':return 'completion'  # writer will skip already verified publication
    if resumable and proof.get('operation_id')==operation_id and proof.get('unified') and proof.get('state')=='pending' and proof.get('plan')==data['plan'].get('publication'):
        return 'completion'  # individually acknowledged prefix; resume only unsent availability actions
    return None


def reconciled_completion_review(row,reviewed):
    """Legacy terminal yield: fresh manual review only after a later full read."""
    if (not row or reviewed.get('mode')!='variable' or reviewed.get('classification')!='update'
            or reviewed.get('scope')!='full' or row.id!=reviewed.get('identity_id')
            or row.product_id!=reviewed.get('product_id') or row.store_id!=reviewed.get('store_id')
            or not row.shopify_product_id or row.shopify_product_id!=reviewed.get('remote_id')
            or row.state!='verified' or row.verification_state!='verified'):
        return False
    op=db.session.get(CatalogueOperation,row.operation_id)
    if (not op or op.status!='partial' or op.recovery_state!='review_required' or not op.finished_at
            or not row.verified_at or row.verified_at<=op.finished_at):
        return False
    data=drafts.evidence(row);wait=data.get('verification_wait') or {}
    failure=data.get('failure_diagnostic') or {};verification=data.get('verification') or {}
    return (data.get('kind')=='variable' and data.get('observed_status')=='DRAFT'
        and reviewed.get('identity_digest')==drafts.digest(data)
        and wait.get('operation_id')==failure.get('operation_id')==verification.get('operation_id')==op.id
        and wait.get('source_digest')==row.reviewed_digest==data['plan'].get('digest')
        and wait.get('state')=='recovery_required' and wait.get('phase')=='completion'
        and failure.get('category')=='BackgroundYield' and failure.get('stage')=='variable_readback.completion'
        and _verified_base(verification,op.id)
        and _completion_checkpoint(data,op.id)=='completion')


def reconciled_attribute_review(row, reviewed):
    """Accept only a later, identity-bound read of an interrupted Draft creation."""
    if (not row or reviewed.get('mode') != 'variable' or reviewed.get('classification') != 'update'
            or reviewed.get('scope') != 'full' or row.id != reviewed.get('identity_id')
            or row.product_id != reviewed.get('product_id') or row.store_id != reviewed.get('store_id')
            or not row.shopify_product_id or row.shopify_product_id != reviewed.get('remote_id')
            or row.state != 'attribute_review_required' or row.verification_state != 'base_verified'):
        return False
    op = db.session.get(CatalogueOperation, row.operation_id)
    if (not op or op.operation_type != 'shopify_draft_publish' or op.status != 'partial'
            or op.recovery_state != 'review_required' or not op.finished_at):
        return False
    data = drafts.evidence(row)
    proof = data.get('verification') or {}
    failure = data.get('failure_diagnostic') or {}
    old_plan = data.get('plan') or {}
    try:
        checked_after_stop = datetime.fromisoformat(proof.get('checked_at')) > op.finished_at
    except (TypeError, ValueError):
        return False
    return (checked_after_stop and data.get('kind') == 'variable' and data.get('observed_status') == 'DRAFT'
            and data.get('stage') == 'attribute_review_required' and data.get('create_attempted') is True
            # Initial creation also sets update_attempted when it joins a collection.
            and old_plan.get('classification') == 'create' and not old_plan.get('remote_id')
            and 'product_create' in data.get('completed_steps', ())
            and proof.get('operation_id') == failure.get('operation_id') == op.id
            and failure.get('stage') == 'attribute_registration' and proof.get('attributes') == 'remaining_review'
            and all(proof.get(k) == 'verified' for k in ('identity', 'fields', 'media', 'memberships'))
            and old_plan.get('digest') == row.reviewed_digest and old_plan.get('store_id') == row.store_id
            and old_plan.get('config_digest') == reviewed.get('config_digest')
            and reviewed.get('identity_digest') == drafts.digest(data)
            # This stop precedes these writes. Do not subsume any other recovery.
            and not any(data.get(k) for k in ('verification_wait', 'attribute_assignment_attempt',
                'publication_update', 'package_assignments', 'variant_native', 'native_dimensions',
                'eu_identifier_attempt', 'item_cost_attempt', 'customisation_update', 'inventory_update', 'sale_transition'))
            and all(value.get('state') == 'verified' for value in data.get('dimension_definitions', {}).values())
            and (not data.get('storefront_configuration') or data['storefront_configuration'].get('state') == 'verified'))


def attribute_review_only(data, plan):
    """Recheck the rebuilt signed plan before entering just the remaining tail."""
    def intent(entries):
        return [{k: entry[k] for k in ('key', 'name', 'role', 'public', 'terms')} for entry in entries]

    attributes = plan.get('attributes', [])
    old = data['plan']
    publication = plan.get('publication') or {}
    media = plan['media_actions']
    native = plan['native']
    if (plan['actual_status'] != 'DRAFT' or intent(attributes) != intent(old['attributes'])
            or not any(a['changed'] for a in attributes)
            or any(a['changed'] and 'attribute:'+a['key'] in data.get('completed_steps', ()) for a in attributes)
            or publication and (not publication.get('read_complete') or publication.get('blockers'))
            or any(v['action'] == 'Create' for v in plan['variants'])
            or {v['identity']:v.get('remote_id') for v in plan['variants']} !=
               {key:v['id'] for key,v in data['variant_maps'].items()}
            or plan['images'] != old['images'] or media['ids'] != [m['id'] for m in data['created_media']]
            or media['alt'] or media['reorder']
            or any(c['action'] in {'Join','Create & Join'} for c in plan['collections'])
            or any(p.get('action') == 'assign' for p in plan.get('optional_packages', ()))
            or native['enabled'] and any(not p.get('unconfigured') for p in native['variants'].values())):
        return False
    allowed = set()
    for attr in attributes:
        if not attr['changed']:
            continue
        allowed.update(('attributes.'+attr['name'], 'attribute_registration.'+attr['key']))
        for item in attr['resources']:
            for change in item.get('settings_changes', ()):
                allowed.add('attribute_access.'+item['source'] if change['key'] == 'storefront' else
                    'attribute_capability.'+item['source']+'.'+change['key'])
    return all(not f['changed'] or f.get('publication_only') or f['field'] in allowed for f in plan['fields'])


def _transient(exc):
    return isinstance(exc,(ReadbackLag,BackgroundYield)) or isinstance(exc,ShopifyError) and (
        exc.category in {'network','invalid_response'} or isinstance(exc,ShopifySnapshotChanged) and exc.changed==['updatedAt'])


def _refresh_blocked_summary(row,data):
    """Keep verified metadata readable without renewing a blocked domain."""
    from app.models import ShopifyProductComparison
    from app.shopify_comparison import fingerprint,store_summary
    summary=ShopifyProductComparison.query.filter_by(store_id=row.store_id,source_identity=row.source_identity).first()
    if summary and row.state=='verified':
        value=json.loads(summary.summary_json)
        if fingerprint(db.session.get(Product,row.product_id),data['plan']['images'])==value.get('local'):
            value.update(identity=drafts.digest(row.media_evidence_json),native=data['plan']['native'])
            store_summary(summary,value)


def complete(row,adapter,*,allow_writes=False):
    """Read-only by default; writes only under the existing accepted task authority."""
    from app import shopify_variables as variable
    from app.shopify_publication import apply_reviewed,complete as publication_complete,verify as verify_publication
    from app.utils.operation_control import finish_catalogue_operation
    data=drafts.evidence(row);config=drafts.configuration();w=data['verification_wait']
    if not pending(row):raise ShopifyError('No conclusively acknowledged pending verification; retained uncertainty cannot auto-resume.')
    try:
        guard_owner()
        if allow_writes:
            from app.utils import operation_control as control
            if not control._active_operation or control._active_operation['id']!=row.operation_id:
                from app.manual_publishing import resume
                resume(control.OperationLease(row.operation_id,'shopify_draft_publish'))
        if w['state']=='writing':
            # Replacement worker after a crash during a provably safe boundary.
            begin(row,data,_completion_checkpoint(data,row.operation_id));w=data['verification_wait']
        if w['state']=='completed':
            # Crash AFTER the last verified receipt commit, BEFORE operation finalisation.
            variable.verify(row,data,config,adapter)
        else:
            if w['phase']=='base':
                variable.verify(row,data,config,adapter)
                if row.state=='processing':return _pause(row,data,'Shopify media is still processing.')
                begin(row,data,'completion');w=data['verification_wait']
            if w['phase']=='publication':
                if not publication_acknowledged(data):raise ShopifyError('Publication acknowledgement is incomplete; no replay.')
                try:verify_publication(row,data,adapter)
                except ShopifyError as exc:
                    if str(exc).startswith('Publication readback differs or is partial'):raise ReadbackLag(str(exc)) from None
                    raise
                begin(row,data,'completion');w=data['verification_wait']
            if w['phase']=='completion':
                native_needed=data['plan']['native']['enabled'] and any(not v['native'].get('unconfigured') for v in data['plan']['variants'])
                packages_needed=any(e.get('action')=='assign' for e in data['plan'].get('optional_packages',[]))
                publication=data['plan'].get('publication') or {}
                executable_publication=publication.get('read_complete') and not publication.get('blockers') and not publication_complete(data)
                writes_needed=executable_publication or native_needed or packages_needed
                if writes_needed and not allow_writes:
                    w['state']='waiting_authority';return _pause(row,data,'Metadata readback complete; awaiting the approved server continuation.',count=False)
                if writes_needed:
                    if variable.local_plan(db.session.get(Product,row.product_id))['digest']!=data['plan']['digest'] or config.fingerprint()!=data['plan']['config_digest']:
                        raise ShopifyError('Source/configuration changed before deferred availability; no further write permitted.')
                    w['state']='writing'
                    op=db.session.get(CatalogueOperation,row.operation_id);op.status='running'
                    row.state='verified';row.verification_state='verified';drafts.save(row,data)
                    if not publication_complete(data):
                        try:apply_reviewed(row,data,config,adapter)
                        except ShopifyError as exc:
                            if publication_acknowledged(data) and (str(exc).startswith('Publication readback differs or is partial') or _transient(exc)):
                                begin(row,data,'publication');raise ReadbackLag(str(exc)) from None
                            raise
                    row.state='verified';row.verification_state='verified';drafts.save(row,data)
                    native_state=variable._native_apply(row,data,config,adapter,row.operation_id)
                    from app.shopify_package_assignments import apply as packages
                    package_state=packages(row,adapter,row.operation_id)
                    # Package application owns its durable acknowledgement data.
                    data=drafts.evidence(row)
                    if native_state not in {'disabled','verified','unconfigured'} or package_state not in {'none','accepted'}:
                        if native_state=='uncertain':row.state='uncertain';row.verification_state='unverified'
                        data['verification_wait']['state']='blocked';drafts.save(row,data)
                        _refresh_blocked_summary(row,data)
                        finish_catalogue_operation(row.operation_id,status='partial',recovery_state='review_required' if row.state=='uncertain' else 'none',
                            products_attempted=1,products_succeeded=0,products_failed=0,
                            operation_summary={'identity_id':row.id,'stage':'native_or_package_blocked','native_dimensions':native_state,'package_assignment':package_state})
                        return row
                begin(row,data,'final');w=data['verification_wait']
            if w['phase']=='final':variable.verify(row,data,config,adapter)
        from app.shopify_notifications import complete as fully_verified
        if not fully_verified(row,drafts.evidence(row)):
            data=drafts.evidence(row);data['verification_wait']['state']='blocked';drafts.save(row,data)
            _refresh_blocked_summary(row,data)
            finish_catalogue_operation(row.operation_id,status='partial',recovery_state='none',products_attempted=1,products_succeeded=0,products_failed=0,
                operation_summary={'identity_id':row.id,'stage':'verified_metadata_deferred_availability','variants':len(data['plan']['variants'])})
            return row
        finish_catalogue_operation(row.operation_id,status='succeeded',recovery_state='none',products_attempted=1,products_succeeded=1,products_failed=0,
            operation_summary={'identity_id':row.id,'mode':'variable','stage':'verified','variants':len(data['plan']['variants'])})
        return row
    except Exception as exc:
        # Only READ phases are safely retryable. A timeout during a mutation is
        # never converted into a pending/read-only continuation.
        if isinstance(exc,BackgroundYield):
            # Look at committed receipts, not a pending in-memory write plan.
            # A lost owner cannot park the job: all saves below remain fenced.
            db.session.rollback();data=drafts.evidence(row);w=data['verification_wait']
            if pending(row):
                if w['state']=='writing':begin(row,data,_completion_checkpoint(data,row.operation_id))
                from app.shopify_capacity import diagnostic
                data['verification_wait']['last_deferral'] = diagnostic(exc)
                return _pause(row,data,diagnostic(exc)['deferral'],count=False)
        elif data['verification_wait'].get('state') in {'pending','waiting_authority','completed'} and _transient(exc):
            return _pause(row,data,exc)
        # Preserve the latest COMMITTED acknowledgement/readback on every error,
        # including non-Shopify exceptions and continuations outside confirm().
        # Unknown exceptions remain protected; they do not authorise write replay.
        diagnostic=drafts.failure_diagnostic(exc,'variable_readback.'+w['phase'],row.operation_id)
        db.session.rollback()
        data=drafts.evidence(row)
        from app.utils.operation_control import sanitize_operation_error
        message=sanitize_operation_error(exc) if isinstance(exc,ShopifyError) else (
            'Variable readback interrupted ('+diagnostic['category']+'). '+diagnostic['reason']+' No automatic retry.')
        data['error']=message;data['failure_diagnostic']=diagnostic
        data['verification_wait']['state']='recovery_required'
        row.verification_state='unverified';drafts.save(row,data,'uncertain')
        finish_catalogue_operation(row.operation_id,status='partial',recovery_state='review_required',error=message,
            products_attempted=1,products_succeeded=0,products_failed=0,
            operation_summary={'identity_id':row.id,'stage':diagnostic['stage'],'diagnostic':diagnostic})
        raise
