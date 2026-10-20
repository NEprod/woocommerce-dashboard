"""Signed per-product status/channel stages using the existing transport and receipts."""
import copy
import json
import re
from collections.abc import Mapping
from datetime import datetime, UTC

from app import db
from app.models import Product, ShopifyProductIdentity, CatalogueOperation
from app.shopify_connection import ShopifyAdapter, ShopifyError, ShopifyReadinessFinding, _gid
from app.taxonomy_assignments import product_document
from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation

STATUSES = {'DRAFT','ACTIVE','ARCHIVED'}


def intent(authored, *, required=True):
    status = authored.get('shopify_status')
    if status is None:
        live = authored.get('live')
        status = 'ACTIVE' if live is True else 'DRAFT' if live is False else None
    if status not in STATUSES and (status is not None or required):
        raise ShopifyError('Resolve Draft, Active or Archived intent before publication.')
    if 'shopify_status' in authored and 'live' in authored and authored['live'] != (status == 'ACTIVE'):
        raise ShopifyError('Shopify status conflicts with legacy live intent. Review both authored values.')
    channels = authored.get('shopify_channels',{})
    if not isinstance(channels,Mapping) or len(channels)>20 or any(not isinstance(k,str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,95}',k) or type(v) is not bool for k,v in channels.items()):
        raise ShopifyError('Channel intent must contain discovered handles and explicit booleans, not remote IDs.')
    return {'status':status,'channels':dict(channels)}


def discovered(adapter,config,shop):
    if not {'read_publications','write_publications'}.intersection(shop['scopes']):
        raise ShopifyError('Missing read_publications for channel discovery/readback. Saved evidence remains historical, not unpublished.')
    result, handles = [], set()
    for publication in adapter.publications(config):
        pid = _gid(publication.get('id'),'Publication')
        if type(publication.get('autoPublish')) is not bool:
            raise ShopifyError('Publication automatic-availability evidence is incomplete; no status/channel write allowed.')
        channels = publication.get('channels')
        if not isinstance(channels,dict) or channels.get('pageInfo',{}).get('hasNextPage') is not False:
            raise ShopifyError('Incomplete channel discovery; no selectable guesses.')
        for channel in channels.get('nodes',[]):
            key, name = channel.get('handle'), channel.get('name')
            if not isinstance(key,str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,95}',key) or key in handles or not isinstance(name,str) or not name.strip():
                raise ShopifyError('Ambiguous/unsupported channel handles; inspect the actual Shopify publications.')
            handles.add(key)
            result.append({'key':key,'name':name[:255],'id':pid,'channel_id':_gid(channel.get('id'),'Channel'),
                           'auto_publish':publication.get('autoPublish') is True})
    if len(result)>20: raise ShopifyError('Review supports at most twenty actual channels.')
    return result


def read(row,data,adapter,config):
    remote = adapter.publication_state(config,row.shopify_product_id)
    if not isinstance(remote,dict) or remote.get('id') != row.shopify_product_id or remote.get('status') not in STATUSES or (remote.get('metafield') or {}).get('value') != data['plan']['nonce']:
        raise ShopifyError('Product/store/correlation identity is untrusted; no publication write is allowed.')
    connection = remote.get('resourcePublicationsV2')
    if not isinstance(connection,dict) or connection.get('pageInfo',{}).get('hasNextPage') is not False or not isinstance(connection.get('nodes'),list):
        raise ShopifyError('Channel membership readback is incomplete, not unpublished.')
    memberships, seen = {}, set()
    for value in connection['nodes']:
        pid = _gid((value.get('publication') or {}).get('id'),'Publication')
        if pid in seen or type(value.get('isPublished')) is not bool: raise ShopifyError('Ambiguous channel membership evidence.')
        seen.add(pid)
        # Existing future publication schedules are not immediate eligibility.
        date = value.get('publishDate')
        if date:
            try: future = datetime.fromisoformat(date.replace('Z','+00:00')).astimezone(UTC).replace(tzinfo=None) > __import__('app.shopify_drafts',fromlist=['now']).now()
            except (ValueError,TypeError): raise ShopifyError('Invalid channel publication date.') from None
            if future: raise ShopifyError('A remote future publication schedule exists; resolve it explicitly before this immediate-publication workflow.')
        memberships[pid] = value['isPublished']
    return {'status':remote['status'],'memberships':memberships}


def cached_channels(config=None):
    from app import shopify_drafts as drafts
    try: config = config or drafts.configuration()
    except ValueError: return []
    for op in CatalogueOperation.query.filter_by(operation_type='shopify_status_refresh').order_by(CatalogueOperation.started_at.desc()).limit(40):
        scope = json.loads(op.scope)
        if scope.get('mode')=='read_only_publications' and scope.get('config')==config.fingerprint() and op.status=='succeeded':
            return scope.get('operation_summary',{}).get('channels',[])
    return []


def discover():
    from app import shopify_drafts as drafts
    config, adapter = drafts.configuration(), ShopifyAdapter()
    op = acquire_catalogue_operation('shopify_status_refresh',{'mode':'read_only_publications','config':config.fingerprint()})
    try:
        shop = adapter.test_connection(config)
        rows = discovered(adapter,config,shop)
        finish_catalogue_operation(op.id,status='succeeded',operation_summary={'store_id':shop['shop_id'],'channels':rows})
        return rows
    except Exception:
        finish_catalogue_operation(op.id,status='failed',error='Read-only channel discovery unavailable; saved intent unchanged.')
        raise


def preview(product,adapter=None):
    from app import shopify_drafts as drafts
    adapter,config = adapter or ShopifyAdapter(), drafts.configuration()
    shop = adapter.test_connection(config)
    row = drafts.matching_identity(shop['shop_id'],product)
    from app.shopify_notifications import complete
    if not row or not complete(row, drafts.evidence(row)):
        raise ShopifyError('Publication requires a fully verified retained product. Create/verify its draft first or reconcile outstanding recovery.')
    data = drafts.evidence(row)
    wanted = intent(product_document(product))
    channels = discovered(adapter,config,shop)
    observed = read(row,data,adapter,config)
    for channel in channels:
        channel['current']=observed['memberships'].get(channel['id'],False)
        channel['managed']=channel['key'] in wanted['channels']
        channel['desired']=wanted['channels'].get(channel['key'])
    by_key = {c['key']:c for c in channels}
    if set(wanted['channels']) - set(by_key): raise ShopifyError('An authored channel is no longer available; no automatic removal or substitution.')
    actions, targets = [], {}
    for key,published in wanted['channels'].items():
        channel = by_key[key]
        pid = channel['id']
        if pid in targets and targets[pid] != published:
            raise ShopifyError('Channels share one publication but have conflicting selections. Review their shared availability together.')
        targets[pid]=published
    if wanted['status'] != 'ACTIVE' and any(targets.values()):
        raise ShopifyError('Draft/Archived intent cannot request channel publication. Unselect it or use Active intent explicitly.')
    for pid,published in targets.items():
        before = observed['memberships'].get(pid,False)
        actions.append({'id':pid,'name':', '.join(c['name'] for c in channels if c['id']==pid),
                        'before':before,'after':published,'changed':before!=published})
    fields = [{'field':'publication.status','label':'Shopify lifecycle status','before':observed['status'],'after':wanted['status'],'changed':observed['status']!=wanted['status']}]
    fields += [{'field':'publication.'+a['id'],'label':a['name'],'before':a['before'],'after':a['after'],'changed':a['changed']} for a in actions]
    blockers=[]
    if fields[0]['changed'] and 'write_products' not in shop['scopes']: blockers.append('Missing write_products for product status.')
    if any(a['changed'] for a in actions) and 'write_publications' not in shop['scopes']: blockers.append('Missing write_publications for selected channel changes.')
    plan={'product_id':product.id,'title':product.title,'sku':product.sku,'scope':'publication','classification':'update' if any(f['changed'] for f in fields) else 'no_change',
        'identity_id':row.id,'store_id':row.store_id,'remote_id':row.shopify_product_id,'config':config.fingerprint(),
        'intent':wanted,'channels':channels,'observed':observed,'actions':actions,'fields':fields,'blockers':blockers,
        'source':drafts.digest(product_document(product)),'identity':drafts.digest(data),'actual_status':observed['status']}
    plan['digest']=drafts.digest(plan)
    token=drafts.serializer().dumps({'mode':'publication','product_id':product.id,'digest':plan['digest']})
    return plan, token if plan['classification']=='update' and not blockers else None


def confirm(reviewed,adapter=None):
    from app import shopify_drafts as drafts
    product=db.session.get(Product,reviewed['product_id'])
    adapter=adapter or ShopifyAdapter()
    lease=acquire_catalogue_operation('shopify_draft_publish',{'product_id':product.id,'mode':'publication','scope':'publication'})
    attempted=False
    row=None
    try:
        plan,_=preview(product,adapter)
        if plan['digest']!=reviewed['digest'] or plan['blockers'] or plan['classification']!='update':
            raise ShopifyError('Publication/source/identity changed since Review. No write attempted; review again.')
        row=db.session.get(ShopifyProductIdentity,plan['identity_id'])
        data=copy.deepcopy(drafts.evidence(row))
        data['publication_update']={'state':'pending','plan':plan,'operation_id':lease.id,'completed':[]}
        row.operation_id=lease.id
        config=drafts.configuration()
        from time import monotonic
        deadline=monotonic()+60
        if sum(f['changed'] for f in plan['fields'])>6:
            raise ShopifyError('Review at most five channel changes plus status in one bounded operation.')
        for field in plan['fields']:
            if not field['changed']: continue
            if monotonic()>deadline: raise ShopifyError('Publication deadline reached; retain completed stages and reconcile before another review.')
            data['stage']='publication_update';data['publication_update']['attempted']=field['field']
            drafts.save(row,data,'updating')  # durable before EVERY mutation
            attempted=True
            if field['field']=='publication.status':
                result=adapter.update_simple_product(config,{'id':row.shopify_product_id,'status':field['after']})
                drafts.payload_result(result,'productUpdate')
            else:
                pid=field['field'].removeprefix('publication.')
                result=adapter.set_publication(config,row.shopify_product_id,pid,field['after'])
                drafts.payload_result(result,'publishablePublish' if field['after'] else 'publishableUnpublish')
            data['publication_update']['completed'].append(field['field'])
            drafts.save(row,data)
        verify(row,data,adapter)
        finish_catalogue_operation(lease.id,status='succeeded',products_attempted=1,products_succeeded=1,
            operation_summary={'publication':'verified','mode':'publication'})
        return row
    except Exception as exc:
        db.session.rollback()
        if row and attempted:
            data=drafts.evidence(row);data['publication_update']['state']='uncertain'
            data['error']='Publication may have partially completed. Reconcile read-only; never replay automatically.'
            drafts.save(row,data,'uncertain')
        finish_catalogue_operation(lease.id,status='partial' if attempted else 'failed',recovery_state='review_required' if attempted else 'none',
            error=str(exc) if isinstance(exc,ShopifyError) else 'Publication could not be independently verified.')
        if not attempted: raise
        return row


def verify(row,data,adapter):
    from app import shopify_drafts as drafts
    proof=data['publication_update']; plan=proof['plan']; config=drafts.configuration()
    shop=adapter.test_connection(config)
    if shop['shop_id']!=row.store_id or config.fingerprint()!=plan['config']: raise ShopifyError('Restore reviewed store/configuration before publication recovery.')
    remote=read(row,data,adapter,config)
    if remote['status']!=plan['intent']['status'] or any(remote['memberships'].get(a['id'],False)!=a['after'] for a in plan['actions']):
        raise ShopifyError('Publication readback differs or is partial. Keep recovery; no automatic retry.')
    proof.update(state='verified',observed=remote,checked_at=drafts.now().isoformat())
    data['observed_status']=remote['status'];data['stage']='verified';data.pop('error',None)
    if proof.get('unified'):
        # Publication readback alone cannot verify metadata, variants or media.
        drafts.save(row,data,'updating')
        return row
    # Publication does not renew unrelated field verification timestamps.
    drafts.save(row,data,'verified')
    from app.shopify_comparison import invalidate
    invalidate(row.store_id,row.source_identity)
    return row


def proof_matches(product,data):
    try:
        proof=data.get('publication_update',{})
        return proof.get('state')=='verified' and proof['plan']['intent']==intent(product_document(product))
    except (ShopifyError,KeyError,TypeError): return False


def annotate(product,plan,row,config,shop,adapter):
    """Bind discovered availability and current memberships to the main review.

    A blocked availability stage does not grant authority to change it. Metadata
    can still be reviewed, with the deferred scope retained in the comparison.
    """
    from app import shopify_drafts as drafts
    wanted=intent(product_document(product))
    if wanted['status']=='DRAFT' and (not row or plan.get('actual_status', 'DRAFT')=='DRAFT') and not wanted['channels'] and not (row and drafts.evidence(row).get('publication_update')):
        return
    result={'intent':wanted,'read_complete':False,'pending':True}
    if not row or not row.shopify_product_id:
        try:
            channels=discovered(adapter,config,shop)
            keys={c['key']:c for c in channels}
            if set(wanted['channels'])-set(keys): raise ShopifyError('An intended channel is unavailable; no replacement inferred.')
            targets={}
            for key,value in wanted['channels'].items():
                pid=keys[key]['id']
                if pid in targets and targets[pid]!=value: raise ShopifyError('Shared publication has conflicting channel selections.')
                targets[pid]=value
            if wanted['status']!='ACTIVE' and any(targets.values()):
                raise ShopifyReadinessFinding('Draft/Archived intent cannot request channel publication.', code='publication_configuration')
            blockers=[]
            if wanted['status']=='ACTIVE' and any(c['auto_publish'] and c['key'] not in wanted['channels'] for c in channels):
                blockers.append('Select explicit intent for every automatically assigned channel before activating a newly created product.')
            if targets and 'write_publications' not in shop['scopes']: blockers.append('Missing write_publications for channel changes.')
            if len(targets)>5: blockers.append('Review at most five channel changes in one operation.')
            result.update(config=config.fingerprint(),store_id=shop['shop_id'],channels=channels,new_product=True,
                observed={'status':'DRAFT','memberships':{}},actions=[{'id':pid,'before':None,'after':value,'changed':True} for pid,value in targets.items()],
                blockers=blockers,read_complete=True,checked_at=drafts.now().isoformat())
            plan['fields'].append({'field':'publication.status','label':'Status after independently verified draft creation',
                'before':'Not yet created','after':wanted['status'],'changed':wanted['status']!='DRAFT','publication_only':True})
            for key,value in wanted['channels'].items():
                plan['fields'].append({'field':'publication.'+key,'label':keys[key]['name'],'before':'Not yet created',
                    'after':value,'changed':True,'publication_only':True})
            result['reason']='Create as Draft first. Apply these reviewed availability stages only after complete product/media readback; processing defers availability to a fresh main review.'
        except ShopifyReadinessFinding as exc:
            result.update(reason=str(exc), configuration_error=exc.code)
        except ShopifyError as exc: result['reason']=str(exc)
    else:
        try:
            data=drafts.evidence(row)
            channels=discovered(adapter,config,shop)
            observed=read(row,data,adapter,config)
            keys={c['key']:c for c in channels}
            if set(wanted['channels'])-set(keys): raise ShopifyError('An intended channel is unavailable; no replacement inferred.')
            rows=[{'field':'publication.status','label':'Shopify lifecycle status',
                   'before':observed['status'],'after':wanted['status'],'changed':observed['status']!=wanted['status'],'publication_only':True}]
            targets={}
            for key,value in wanted['channels'].items():
                channel=keys[key]
                if channel['id'] in targets and targets[channel['id']] != value:
                    raise ShopifyError('Channels share a publication with conflicting intent; resolve the selections before publication.')
                targets[channel['id']]=value
                rows.append({'field':'publication.'+key,'label':channel['name'],
                    'before':observed['memberships'].get(channel['id'],False),'after':value,
                    'changed':observed['memberships'].get(channel['id'],False)!=value,'publication_only':True})
            if wanted['status']!='ACTIVE' and any(targets.values()):
                raise ShopifyReadinessFinding('Draft/Archived intent cannot request channel publication.', code='publication_configuration')
            actions=[{'id':pid,'before':observed['memberships'].get(pid,False),'after':value,
                'changed':observed['memberships'].get(pid,False)!=value} for pid,value in targets.items()]
            blockers=[]
            if rows[0]['changed'] and 'write_products' not in shop['scopes']: blockers.append('Missing write_products for status changes.')
            if any(a['changed'] for a in actions) and 'write_publications' not in shop['scopes']: blockers.append('Missing write_publications for channel changes.')
            if sum(a['changed'] for a in actions)>5: blockers.append('Review at most five channel changes in one bounded operation.')
            result.update(config=config.fingerprint(),store_id=shop['shop_id'],observed=observed,channels=channels,actions=actions,
                blockers=blockers,read_complete=True,pending=any(r['changed'] for r in rows),checked_at=drafts.now().isoformat())
            plan['fields']+=rows
            if result['pending']: plan['classification']='update'
        except ShopifyReadinessFinding as exc:
            result.update(reason=str(exc), configuration_error=exc.code)
        except ShopifyError as exc: result['reason']=str(exc)
    plan['publication']=result
    if result['pending']:
        if result.get('read_complete') and not result.get('blockers'):
            plan['warnings'].append('This confirmation includes the displayed status/channel changes. Unmanaged channels are retained; Active status can expose already assigned channels.')
        else:
            plan['warnings'].append('Publication is deferred: '+(result.get('reason') or '; '.join(result.get('blockers',[]))))
    plan['taxonomy_review_digest']=drafts.digest({'previous':plan['taxonomy_review_digest'],
        'publication':review_binding(result)})


def review_binding(result):
    return {k:v for k,v in result.items() if k!='checked_at'}


def has_reviewed_changes(plan):
    publication=plan.get('publication') or {}
    return has_metadata_changes(plan) or bool(publication.get('pending') and publication.get('read_complete') and not publication.get('blockers'))


def apply_reviewed(row,data,config,adapter):
    """Run only signed, eligible stages within the existing product operation."""
    from app import shopify_drafts as drafts
    plan=data['plan'].get('publication') or {}
    if not plan.get('pending') or not plan.get('read_complete') or plan.get('blockers'):
        return False
    if plan.get('new_product'):
        if row.state!='verified' or row.verification_state!='verified': return False
        actual=read(row,data,adapter,config)
        if actual['status']!='DRAFT': raise ShopifyError('Newly created draft changed status before reviewed availability; reconcile before another write.')
        if plan['intent']['status']=='ACTIVE' and any(value and pid not in {a['id'] for a in plan['actions']} for pid,value in actual['memberships'].items()):
            raise ShopifyError('New Draft has unreviewed assigned channels. Retain the Draft and reconcile, then review actual availability before activation.')
    data['publication_review_operation_id']=row.operation_id
    data['publication_update']={'state':'pending','unified':True,'plan':copy.deepcopy(plan),
        'operation_id':row.operation_id,'completed':[]}
    # Unknown/dropped channels never turn into unpublish requests. Unpublish
    # before lowering status; activate before explicitly publishing a channel.
    actions=[('publication.'+a['id'],a) for a in plan['actions'] if a['changed'] and not a['after']]
    if plan['observed']['status']!=plan['intent']['status']:
        actions.append(('publication.status',{'after':plan['intent']['status']}))
    actions += [('publication.'+a['id'],a) for a in plan['actions'] if a['changed'] and a['after']]
    from time import monotonic
    deadline=monotonic()+60
    for field,action in actions:
        product=db.session.get(Product,row.product_id)
        if monotonic()>deadline or drafts.local_plan(product)['digest']!=data['plan']['digest'] or config.fingerprint()!=drafts.configuration().fingerprint():
            raise ShopifyError('Publication source/configuration or deadline changed; reconcile retained stages before another write.')
        data['stage']='publication_update'
        data['publication_update']['attempted']=field
        data['update_attempted']=True
        drafts.save(row,data,'updating')
        if field=='publication.status':
            result=adapter.update_simple_product(config,{'id':row.shopify_product_id,'status':action['after']})
            drafts.payload_result(result,'productUpdate')
        else:
            result=adapter.set_publication(config,row.shopify_product_id,action['id'],action['after'])
            drafts.payload_result(result,'publishablePublish' if action['after'] else 'publishableUnpublish')
        data['publication_update']['completed'].append(field)
        drafts.completed_step(row,data,field)
    verify(row,data,adapter)
    return True


def expected_status(data):
    proof=data.get('publication_update') or {}
    if proof.get('unified') and data['plan'].get('publication') and proof.get('operation_id')==data.get('publication_review_operation_id'):
        return proof['plan']['intent']['status']
    actual=data['plan'].get('actual_status','DRAFT')
    return 'DRAFT' if actual=='Not yet created' else actual


def complete(data):
    planned=data.get('plan',{}).get('publication') or {}
    proof=data.get('publication_update') or {}
    return not planned.get('pending') or bool(proof.get('state')=='verified' and
        proof.get('plan',{}).get('intent')==planned.get('intent') and
        proof.get('operation_id')==data.get('publication_review_operation_id') and proof.get('operation_id'))


def has_metadata_changes(plan):
    return any(f['changed'] and not f.get('publication_only') for f in plan['fields']) or any(
        r['action'] in {'Join','Create & Join'} for r in plan.get('collections',[]))


def comparison_after_write(product, result, row, adapter):
    """Metadata readback cannot silently establish channel equality.

    Keep the signed historical plan intact; refresh only the comparison index.
    Failed channel reads remain incomplete rather than borrowing old equality.
    """
    from app import shopify_drafts as drafts
    if result.get('publication') or result.get('actual_status') != 'DRAFT' or intent(product_document(product))['channels']:
        result['fields'] = [f for f in result['fields'] if not f.get('publication_only')]
        try:
            config = drafts.configuration()
            shop = adapter.test_connection(config)
            if shop['shop_id'] != row.store_id:
                raise ShopifyError('Shopify store changed during publication readback.')
            annotate(product, result, row, config, shop, adapter)
        except ShopifyError:
            result['publication'] = {'pending':True, 'read_complete':False,
                'reason':'Publication readback unavailable; metadata verification is separate.'}
    return result
