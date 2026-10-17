"""Bounded merchant-assisted package comparison, never a Shopify mutation engine.

The public API has no verified catalogue readback contract. Supplied observations
are explicitly manual evidence, not remote truth. Existing Package/PackageLink and
PackagingEvidence records hold identities, signed reviews and atomic local receipts.
"""
import copy
import json
import re
import uuid
from datetime import datetime
from decimal import Decimal
from itsdangerous import URLSafeTimedSerializer, BadSignature
from flask import current_app
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from app import db
from app.packaging_models import Package, PackageLink, PackagingEvidence
from app import packaging_catalogue as catalogue, shopify_packaging
from app.shopify_drafts import digest

LIMIT=50
FIELDS=('name','type','external_cm.length','external_cm.width','external_cm.height','tare_g')


def value(item,path):
    for part in path.split('.'):
        if not isinstance(item,dict):return None
        item=item.get(part)
    return item


def native(package):
    """Only documented update-input equivalents; never TLC-only restrictions."""
    result={k:package[k] for k in ('name','type','external_cm','tare_g')}
    if result['type']=='TUBE':result['type']=None  # no invented Shopify cylinder representation
    return copy.deepcopy(result)


def normalise(raw):
    if not isinstance(raw,dict):raise ValueError('Enter a package observation.')
    name=raw.get('name')
    if not isinstance(name,str) or not 1<=len(name.strip())<=191:raise ValueError('Enter the actual Shopify package name.')
    kind=raw.get('type')
    if kind not in {'BOX','ENVELOPE','SOFT_PACK','FLAT_RATE'}:raise ValueError('Choose an actual Shopify package type. Tubes remain local.')
    ident=raw.get('id') or None
    if ident and (not isinstance(ident,str) or not re.fullmatch(r'gid://shopify/ShippingPackage/[0-9]{1,30}',ident)):
        raise ValueError('Paste an actual ShippingPackage GID if available; leave it blank rather than inventing one.')
    dimensions=raw.get('dimensions') or {}
    weight=raw.get('weight') or {}
    if not isinstance(dimensions,dict) or not isinstance(weight,dict):raise ValueError('Invalid package measurement object.')
    lu={'CENTIMETERS':'cm','MILLIMETERS':'mm','INCHES':'in','cm':'cm','mm':'mm','in':'in'}
    wu={'GRAMS':'g','KILOGRAMS':'kg','OUNCES':'oz','POUNDS':'lb','g':'g','kg':'kg','oz':'oz','lb':'lb'}
    try:
        local=catalogue.from_display_units({'dimension_unit':lu[dimensions.get('unit','cm')],
            'weight_unit':wu[weight.get('unit','g')],
            'external_cm':{k:dimensions.get(k) for k in ('length','width','height')},'tare_g':weight.get('value')})
    except (KeyError,TypeError):raise ValueError('Use mm/cm/in and g/kg/oz/lb for package observations.') from None
    for axis,number in local['external_cm'].items():
        if number is not None and Decimal(number)<=0:raise ValueError('Unknown dimensions stay blank, not zero.')
    return {'id':ident,'name':name.strip(),'type':kind,**local}


def differences(local,remote):
    rows=[]
    for field in FIELDS:
        before,after=value(local,field),value(remote,field)
        equal=before==after
        if field not in {'name','type'} and before is not None and after is not None:
            equal=Decimal(str(before))==Decimal(str(after))
        rows.append({'field':field,'local':before,'remote':after,'changed':not equal,
            'supported':field!='type' or after in {'BOX','ENVELOPE','SOFT_PACK'}})
    return rows


def compare(records):
    if not isinstance(records,list) or not 1<=len(records)<=LIMIT:raise ValueError('Compare 1–50 Shopify package observations at a time.')
    remote=[normalise(r) for r in records]
    ids=[r['id'] for r in remote if r['id']]
    if len(ids)!=len(set(ids)):raise ValueError('The same Shopify package ID was supplied twice.')
    packages=[catalogue.view(p) for p in Package.query.filter_by(archived=False).order_by(Package.name).all()]
    capability=shopify_packaging.current();store=capability.get('store_id')
    links={r.remote_id:r.package_id for r in PackageLink.query.filter_by(store_id=store).all()} if store else {}
    result=[]
    for index,item in enumerate(remote):
        linked=links.get(item['id'])
        candidates=[]
        for package in packages:
            same_name=' '.join(package['name'].casefold().split())==' '.join(item['name'].casefold().split())
            if package['id']!=linked and not same_name:continue
            fields=differences(native(package),item)
            candidates.append({'id':package['id'],'name':package['name'],'fields':fields,
                'identity_match':package['id']==linked,'compatible':not any(f['changed'] for f in fields),
                'reason':'Retained ID — manual evidence still requires review' if package['id']==linked else 'Name candidate only — confirm type, measurements and identity'})
        result.append({'index':index,'remote':item,'candidates':candidates,'ambiguous':len(candidates)>1})
    return {'records':result,'packages':[{'id':p['id'],'name':p['name']} for p in packages],
        'notice':'Merchant-supplied observations only; no Shopify package read or verification is claimed.'}


def serializer():return URLSafeTimedSerializer(current_app.config['SECRET_KEY'],salt='tlc-package-review-v1')


def preview(records,choices):
    comparison=compare(records)
    if not isinstance(choices,list) or len(choices)!=len(records):raise ValueError('Choose a direction for each observed package.')
    capability=shopify_packaging.current()
    store={k:capability.get(k) for k in ('store_id','config')}
    rows=[];targets=set()
    for candidate,choice in zip(comparison['records'],choices):
        if not isinstance(choice,dict):raise ValueError('Invalid reconciliation choice.')
        direction=choice.get('direction','skip')
        if direction not in {'skip','import','export','link'}:raise ValueError('Choose Import, manual Shopify update, Keep Local/link or Skip.')
        if direction=='skip':continue
        package_id=choice.get('package_id')
        package=db.session.get(Package,package_id) if isinstance(package_id,str) and package_id else None
        if not package or package.archived:raise ValueError('Select an existing active TLC package; no duplicate local record is created.')
        if package.id in targets:raise ValueError('One TLC package cannot be matched to several observations in one review.')
        targets.add(package.id)
        remote=candidate['remote'];local=catalogue.view(package)
        retain=choice.get('retain_link') is True
        if retain and (not remote['id'] or not store['store_id'] or not store['config']):
            raise ValueError('Recording a relationship requires an actual Shopify GID and a current verified store capability check. You can import measurements without recording a link.')
        existing=PackageLink.query.filter_by(package_id=package.id,store_id=store['store_id']).first() if store['store_id'] else None
        collision=PackageLink.query.filter_by(store_id=store['store_id'],remote_id=remote['id']).first() if store['store_id'] and remote['id'] else None
        if retain and (collision and collision.package_id!=package.id or existing and existing.remote_id!=remote['id']):
            raise ValueError('A retained Shopify identity conflicts with this match. No replacement or duplicate link is allowed.')
        fields=differences(native(local),remote)
        after=dict(local)
        if direction=='import':
            if local['type']=='TUBE' or remote['type']=='FLAT_RATE':
                raise ValueError('Tube/carrier-specific geometry cannot be safely converted. Keep it local and use manual Shopify controls.')
            after['name']=remote['name'];after['type']=remote['type']
            # Missing manual values are not evidence of clearing. Local-only
            # interiors/restrictions survive; imports do not prove measurement.
            after['external_cm']={k:remote['external_cm'][k] if remote['external_cm'][k] is not None else local['external_cm'][k] for k in ('length','width','height')}
            if remote['tare_g'] is not None:after['tare_g']=remote['tare_g']
            actual=differences(native(local),native(after))
            if any(r['changed'] and r['field'].startswith('external_cm.') for r in actual):after['dimensions_measured']=False
            if any(r['changed'] and r['field']=='tare_g' for r in actual):after['tare_measured']=False
            catalogue.clean(after)  # reject contradictions with retained usable measurements
        for field in fields:field['proposed']=value(after,field['field']) if direction=='import' else field['local']
        if direction=='import':
            for key in ('dimensions_measured','tare_measured'):
                if local[key]!=after[key]:fields.append({'field':key,'local':local[key],'remote':'No physical measurement proof',
                    'proposed':after[key],'changed':True,'supported':False})
        rows.append({'package_id':package.id,'version':package.version,'direction':direction,
            'before':{k:v for k,v in local.items() if k!='links'},'after':{k:v for k,v in after.items() if k!='links'},
            'remote':remote,'retain_link':retain,'fields':fields,
            'link_before':digest(existing.observed) if existing else None,
            'result':'Manual Shopify action required — no remote write' if direction=='export' else 'Local import only' if direction=='import' else 'Keep local values; record only an explicitly checked relationship'})
    if not rows:raise ValueError('Select at least one package to review.')
    key='package-review:'+uuid.uuid4().hex
    plan={'version':1,'state':'reviewed','store':store,'rows':rows,'created_at':datetime.utcnow().isoformat()+'Z','remote_writes':0}
    row=PackagingEvidence(key=key,value=json.dumps(plan,sort_keys=True),checked_at=datetime.utcnow())
    db.session.add(row);db.session.commit()
    return plan,serializer().dumps({'key':key,'digest':digest(plan)})


def confirm(token,approved):
    if approved is not True:raise ValueError('Explicit confirmation is required. No changes were applied.')
    if not isinstance(token,str) or not 1<=len(token)<=8192:raise ValueError('Build a signed package preview first.')
    try:ticket=serializer().loads(token,max_age=900)
    except BadSignature:raise ValueError('Package review expired or changed; build a new preview.') from None
    receipt=db.session.get(PackagingEvidence,ticket.get('key'))
    if not receipt:raise ValueError('Package review is unavailable.')
    plan=json.loads(receipt.value)
    if plan['state']!='reviewed':return plan  # idempotent receipt, not repeat import
    if digest(plan)!=ticket.get('digest'):raise ValueError('Package review changed; no changes were applied.')
    capability=shopify_packaging.current()
    if plan['store']!={k:capability.get(k) for k in ('store_id','config')}:
        raise ValueError('Store configuration changed after review; rebuild the preview.')
    original=receipt.value
    try:
        claimed=db.session.execute(update(PackagingEvidence).where(PackagingEvidence.key==receipt.key,
            PackagingEvidence.value==original).values(value=json.dumps(dict(plan,state='applying'),sort_keys=True)),
            execution_options={'synchronize_session':False}).rowcount
        if claimed!=1:raise ValueError('Another request completed this review; reload its receipt.')
        for entry in plan['rows']:
            package=db.session.get(Package,entry['package_id'])
            if not package or package.version!=entry['version'] or package.archived:
                raise ValueError('A local package changed after review; rebuild before overwriting.')
            link=PackageLink.query.filter_by(package_id=package.id,store_id=plan['store']['store_id']).first() if plan['store']['store_id'] else None
            if (digest(link.observed) if link else None)!=entry['link_before']:
                raise ValueError('Package relationship evidence changed after review.')
            if entry['direction']=='import' and any(r['changed'] for r in differences(native(entry['before']),native(entry['after']))):
                spec=catalogue.clean(entry['after'])
                count=db.session.execute(update(Package).where(Package.id==package.id,Package.version==entry['version']).values(
                    name=entry['after']['name'],specification=catalogue.dumps(spec),version=package.version+1,updated_at=datetime.utcnow()),
                    execution_options={'synchronize_session':False}).rowcount
                if count!=1:raise ValueError('Concurrent package edit; no import applied.')
            if entry['retain_link']:
                remote=entry['remote'];stamp=datetime.utcnow()
                proof={'version':1,'source':'merchant','api_verified':False,'values':remote,
                    'local_baseline':native(entry['after']),'recorded_at':stamp.isoformat()+'Z'}
                if link:
                    if link.remote_id!=remote['id']:raise ValueError('Retained Shopify ID differs; no automatic replacement.')
                    # A manual observation must never replace a proven API receipt.
                    try:prior=json.loads(link.observed)
                    except ValueError:raise ValueError('Retained relationship evidence needs inspection; it was not replaced.') from None
                    if not isinstance(prior,dict):raise ValueError('Retained relationship evidence needs inspection; it was not replaced.')
                    if prior.get('api_verified') is True:raise ValueError('Retained API proof requires a fresh remote read, not a manual replacement.')
                    link.observed=json.dumps(proof,sort_keys=True);link.checked_at=stamp
                else:db.session.add(PackageLink(package_id=package.id,store_id=plan['store']['store_id'],remote_id=remote['id'],
                    observed=json.dumps(proof,sort_keys=True),checked_at=stamp))
            entry['state']='manual_action_required' if entry['direction']=='export' else 'saved_locally'
        plan.update(state='completed',completed_at=datetime.utcnow().isoformat()+'Z')
        db.session.execute(update(PackagingEvidence).where(PackagingEvidence.key==receipt.key).values(value=json.dumps(plan,sort_keys=True)),execution_options={'synchronize_session':False})
        db.session.commit();db.session.expire_all()
    except IntegrityError:
        db.session.rollback();raise ValueError('Package ID is already linked. No duplicate or partial local import was saved.') from None
    except Exception:
        db.session.rollback();raise
    return plan


def status(package,capability):
    links=[r for r in package['links'] if r.store_id==capability.get('store_id')]
    if not links:return {'label':'Not linked','verified_at':None,'message':'Create in Shopify Admin if missing, then reconcile. No remote creation is implied.'}
    link=links[0]
    try:proof=json.loads(link.observed)
    except ValueError:proof={}
    if not isinstance(proof,dict):proof={}
    if proof and proof.get('source')!='merchant':
        return {'label':'Conflict — review needed','verified_at':None,'remote_id':link.remote_id,
            'message':'Historical relationship evidence is retained, not assumed to prove current Shopify values. Inspect before replacing it.'}
    baseline=proof.get('local_baseline');observed=proof.get('values')
    local_changed=baseline is not None and any(r['changed'] for r in differences(native(package),baseline))
    remote_changed=baseline is not None and isinstance(observed,dict) and any(r['changed'] for r in differences(baseline,observed))
    label='Conflict — review needed' if local_changed and remote_changed else 'Local changes pending' if local_changed else 'Shopify changes detected' if remote_changed else 'Shopify API unavailable'
    return {'label':label,'verified_at':None,'remote_id':link.remote_id,
        'message':'Retained relationship; current API readback is unavailable. Manual observations are not verified synchronisation.'}


def sync_preview(package_ids=None):
    """Explicit bounded read probe, then honest skipped/manual per-package results."""
    failure=None
    try:shopify_packaging.refresh()
    except (ValueError,shopify_packaging.ShopifyError):failure='Shopify API unavailable during this capability check. Check the existing Connection; retained package relationships are unchanged.'
    capability=shopify_packaging.current()
    query=Package.query.filter_by(archived=False).order_by(Package.name)
    if package_ids is not None:query=query.filter(Package.id.in_(package_ids))
    packages=query.limit(LIMIT+1).all()
    if len(packages)>LIMIT:raise ValueError('Review at most 50 packages at once.')
    result=[]
    for package in packages:
        local=catalogue.view(package);state=status(local,capability)
        try:
            proof=next((json.loads(link.observed) for link in local['links'] if link.store_id==capability.get('store_id')),None)
            remote=proof.get('values') if isinstance(proof,dict) else None
        except ValueError:remote=None
        rows=differences(native(local),remote) if isinstance(remote,dict) else []
        result.append({'package':local,'status':state,'fields':rows,
            'result':'Skipped — no verified Shopify link' if state['label']=='Not linked' else 'Skipped — independent Shopify package readback unavailable; use the manual update checklist',
            'copy_values':native(local)})
    return {'rows':result,'remote_writes':0,'capability':capability,'failure':failure}
