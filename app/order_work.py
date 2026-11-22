"""Order Fulfilment: local state machine, frozen BOMs and authoritative ledger issues.

All mutators share SQLite's existing inventory writer fence; caller owns commit.
No function in this module calls Shopify, sends mail or purchases shipping.
"""
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal, ROUND_CEILING
import json
import re
from uuid import uuid4

from sqlalchemy import func, select
from app import db
from app.models import Product, ShopifyOrder, ShopifyOrderLine, ShopifyProductIdentity
from app.order_work_models import OrderWorkLine, WorkRequirement, WorkEvent, WorkProof, WorkPick, WorkPlan, WorkOutbound
from app.inventory import service as stock, product_bom
from app.inventory.models import Item, Location, Movement, ProductionBatch, Transaction

TYPES={'card':'Greeting card','print':'Direct 3D print','assembly':'3D / component assembly',
       'laser':'Laser cutting / engraving','stock':'Ready-made stock','mixed':'Multi-process production'}
LABELS={k:v for k,v in [('new','New Order'),('needs_configuration','Needs Configuration'),('awaiting_proof','Awaiting Proof'),
    ('proof_preparing','Proof Preparing'),('proof_sent','Proof Sent'),('changes_requested','Changes Requested'),
    ('proof_approved','Proof Approved'),('ready','Ready for Production'),('picking','Picking Started'),
    ('waiting','Waiting for Components'),('production','Production Started'),('assembly','Assembly / Finishing'),
    ('quality','Quality Check'),('ready_pack','Ready to Pack'),('packing','Packing'),
    ('ready_dispatch','Ready for Dispatch'),('dispatched','Dispatched'),('hold','On Hold / Needs Attention'),('cancelled','Cancelled')]}
TERMINAL={'cancelled','dispatched'}
PAYMENT={'PAID','PARTIALLY_REFUNDED'}


def now(): return datetime.now(UTC).replace(tzinfo=None)
def dumps(value): return json.dumps(value,sort_keys=True,separators=(',',':'))
def actor_text(value): return stock.clean(str(value),'Operator',160,required=True)


def validate_policy(value):
    if not isinstance(value,dict) or set(value)-{'type','proof_required','personalised','needs_picking'}:
        raise ValueError('Workflow supports type, proof_required, personalised and needs_picking only.')
    if 'type' in value and value['type'] not in TYPES:
        raise ValueError('Choose a supported manufacturing workflow; do not infer it from the product name.')
    if any(type(v) is not bool for k,v in value.items() if k!='type'):
        raise ValueError('Workflow/proof flags must be true or false, not text.')


def fingerprint(line):
    order=line.order
    refund=json.loads(order.money_json).get('totalRefundedSet',{}).get('shopMoney',{})
    return stock.digest(dict(product=line.product_id,variant=line.variant_id,sku=line.sku,
        quantity=line.current_quantity,current=line.is_current,properties=json.loads(line.properties_json),
        payment=order.financial_status,cancelled=str(order.cancelled_at),redacted=str(order.redacted_at),
        refunded=(refund.get('currencyCode'),str(Decimal(refund.get('amount','0')).normalize()))))


def local_product(line):
    links=ShopifyProductIdentity.query.filter_by(store_id=line.order.store_id,shopify_product_id=line.product_id,
        state='verified',verification_state='verified').all() if line.product_id else []
    if len(links)!=1:raise ValueError('No unique verified store-bound local product mapping. Review Product Review & Sync.')
    link=links[0];product=db.session.get(Product,link.product_id)
    if not product or product.catalogue_status!='active':raise ValueError('The mapped local product is missing/inactive.')
    evidence=json.loads(link.media_evidence_json)
    if product.product_type=='variable':
        matches=[v for v in product.variations if v.catalogue_status=='active' and v.sku==line.sku
            and evidence.get('variant_maps',{}).get(v.source_identity,{}).get('id')==line.variant_id]
        if len(matches)!=1:raise ValueError('Retained Shopify variant identity does not resolve uniquely. Review identity reconciliation.')
    elif product.sku!=line.sku or evidence.get('variant_id')!=line.variant_id:
        raise ValueError('Retained Shopify variant/SKU differs from this purchased line. Review identity reconciliation.')
    return product


def preparation(line,*,proof_override=None):
    """Read-only preparation used by old imported orders too; never selects a BOM manually."""
    product=local_product(line)
    shared,local=product_bom.authored(product)
    policy={}
    for layer in (shared.get('production',{}),local.get('production',{}),
                  shared.get('production',{}).get('variants',{}).get(line.sku,{}),
                  local.get('production',{}).get('variants',{}).get(line.sku,{})):
        policy.update(layer.get('workflow',{}))
    validate_policy(policy)
    if policy.get('type') not in TYPES:raise ValueError('Configure the product/collection manufacturing workflow first.')
    policy.setdefault('needs_picking',policy['type'] in {'assembly','stock'})
    policy.setdefault('proof_required',False);policy.setdefault('personalised',False)
    personal=json.loads(line.personalisation_json)
    preference=personal.get('proof') or {}
    if preference.get('choice') in {'required','yes'}:policy['proof_required']=True
    if proof_override is not None:policy['proof_required']=proof_override
    if proof_override is None and any('proof' in str(w).lower() for w in personal.get('warnings',[])):
        raise ValueError('Purchase-time proof preference needs review; configure an audited proof override before proceeding.')
    projections=product_bom.product_costs(product,documents=(shared,local))
    resolved=projections.get(line.sku) or projections.get(product.sku)
    if resolved and resolved['conflicts']:raise ValueError('Effective BOM has conflicting option/variant requirements. Repair the source BOM.')
    if policy['needs_picking'] and (not resolved or not resolved['count']):
        raise ValueError('This picking workflow needs a configured effective inventory BOM.')
    requirements=defaultdict(int)
    for row in (resolved or {}).get('rows',[]):
        if row['excluded']:continue
        item=stock.item_for(row['item_id'])
        amount=stock.converted(stock.number(row['quantity'],'BOM quantity',positive=True),row['unit'],item.unit)
        requirements[item.id]+=amount*line.current_quantity
        if requirements[item.id]>stock.MAX_QTY:raise ValueError('Order requirement exceeds the inventory range.')
    return product,policy,dict(requirements),dict(projection=resolved,sku=line.sku,quantity=line.current_quantity,
        product_id=line.product_id,variant_id=line.variant_id,properties_digest=stock.digest(json.loads(line.properties_json)))


def eligible(line):
    if line.order.redacted_at:return 'Order personal data has been redacted; operator reconciliation is required.'
    if line.order.cancelled_at or not line.is_current or line.current_quantity<=0:return 'Shopify cancelled or removed this purchased line.'
    if line.order.financial_status not in PAYMENT:return 'Payment/refund state requires operator review in Shopify.'
    return ''


def latest_proof(line_id):
    return WorkProof.query.filter_by(line_id=line_id).order_by(WorkProof.version.desc()).first()


def picked(requirement):
    return db.session.scalar(select(func.coalesce(func.sum(WorkPick.quantity),0)).where(WorkPick.requirement_id==requirement.id))


def requirements(line_id):return WorkRequirement.query.filter_by(line_id=line_id,active=True).order_by(WorkRequirement.id).all()
def remaining(r):return max(0,r.quantity-picked(r))
def reserved(item_id):return db.session.scalar(select(func.coalesce(func.sum(WorkRequirement.reserved),0)).where(WorkRequirement.item_id==item_id))
def available(item_id):return max(0,stock.balance(item_id)-reserved(item_id))


def _event(work,key,action,previous,actor,reason,details,digest):
    event=WorkEvent(id=key,line_id=work.line_id,action=action,previous=previous,state=work.state,
        actor=actor,at=now(),reason=reason,details=dumps(details),digest=digest)
    db.session.add(event);db.session.flush()
    # Durable disabled status outbox; customer communications are out of scope.
    base=dict(order_id=work.order_id,line_id=work.line_id,state=work.state,version=work.version)
    db.session.add(WorkOutbound(event_id=key,channel='shopify',state='disabled',attempts=0,
        payload=dumps(base),error='Outbound production status sync is not activated.'))
    return event


def initialise(line,*,actor='order_import'):
    existing=db.session.get(OrderWorkLine,line.id)
    if existing:return existing
    reason='';policy={};bom={};req={};product=None
    try:product,policy,req,bom=preparation(line)
    except (ValueError,TypeError,KeyError) as exc:reason=str(exc)
    state='needs_configuration' if reason else 'awaiting_proof' if policy.get('proof_required') else 'ready'
    problem=eligible(line)
    if problem:state='hold';reason=problem
    work=OrderWorkLine(line_id=line.id,order_id=line.order_id,product_id=product.id if product else None,
        state=state,policy_json=dumps(policy),bom_json=dumps(bom),source_digest=fingerprint(line),
        version=1,proof_required=bool(policy.get('proof_required')),hold_reason=reason)
    db.session.add(work);db.session.flush()
    for item_id,amount in req.items():db.session.add(WorkRequirement(line_id=line.id,item_id=item_id,
        quantity=amount,reserved=0,unit=stock.item_for(item_id).unit))
    _event(work,uuid4().hex,'import','new',actor_text(actor),reason or 'Purchase requirements resolved locally.',{},work.source_digest)
    return work


def reconcile(order):
    """Called inside the existing complete-order import transaction, never from scans/printing."""
    for line in order.lines:
        work=initialise(line)
        changed=work.source_digest!=fingerprint(line)
        early_fulfilment=line.unfulfilled_quantity<line.current_quantity and work.state not in TERMINAL
        if changed or early_fulfilment and work.state!='hold':
            prior=work.state
            work.prior_state=prior;work.state='hold';work.version+=1
            work.hold_reason='Shopify order/quantity/refund/customisation changed; reconcile frozen requirements and picked stock.' if changed else 'Shopify fulfilment changed before local dispatch; reconcile the physical parcel.'
            work.source_digest=fingerprint(line)
            for r in requirements(work.line_id):r.reserved=0
            _event(work,uuid4().hex,'commerce_changed',prior,'order_import',work.hold_reason,{},work.source_digest)


def _guard(work,line):
    problem=eligible(line)
    if problem:raise ValueError(problem)
    if work.source_digest!=fingerprint(line):raise ValueError('Shopify snapshot changed. Reconcile this line before progressing.')
    if work.state in {'hold','needs_configuration','cancelled','dispatched'}:raise ValueError('This line is blocked or complete; use an explicit reconciliation/correction.')


def _release(work):
    for r in requirements(work.line_id):r.reserved=0


def _reserve(work):
    for r in requirements(work.line_id):
        extra=min(max(0,remaining(r)-r.reserved),available(r.item_id))
        r.reserved+=extra
        db.session.flush()


def scan(code):
    """Shared lookup only; no status transition, reservation or ledger write."""
    raw=str(code or '').strip()
    if len(raw)>160:return []
    if re.fullmatch(r'TLC-ORDER-[1-9][0-9]{0,17}',raw.upper()):
        row=db.session.get(ShopifyOrder,int(raw.rsplit('-',1)[-1]))
        return [row] if row else []
    return ShopifyOrder.query.filter(ShopifyOrder.name.in_([raw,'#'+raw.lstrip('#')])).order_by(ShopifyOrder.created_at.desc()).limit(25).all()


def token(order):return f'TLC-ORDER-{order.id}'


def act(line_id,action,data,key,*,actor,version):
    actor=actor_text(actor)
    if not re.fullmatch(r'[a-f0-9]{32}',str(key)):raise ValueError('A stable action reference is required.')
    signature=stock.digest(dict(line_id=line_id,action=action,data=data,actor=actor,version=version))
    stock.writer_lock()
    previous_event=db.session.get(WorkEvent,key)
    if previous_event:
        if previous_event.digest!=signature:raise ValueError('This action reference belongs to different submitted values.')
        return db.session.get(OrderWorkLine,line_id)
    line=db.session.get(ShopifyOrderLine,line_id)
    if not line:raise ValueError('Purchased line no longer exists.')
    work=db.session.get(OrderWorkLine,line_id)
    if work and version is None:raise ValueError('Reload this existing workflow to obtain its current revision before confirming.')
    work=work or initialise(line,actor=actor)
    if version is not None and work.version!=int(version):raise ValueError('This workflow changed in another session. Reload before confirming.')
    if data.get('confirmed')!='yes':raise ValueError('Explicit operator confirmation is required; scanning alone never starts work.')
    reason=stock.clean(data.get('reason'),'Reason / evidence',400,required=True)
    prior=work.state;details={};new_pick=None
    if action in {'refresh','replan'}:
        if action=='replan' and (work.state not in {'cancelled','hold'} or data.get('exception')!='yes'):
            raise ValueError('Re-planning requires an authorised held/cancelled-work correction.')
        if (work.committed_at and action!='replan') or any(picked(r) for r in requirements(line_id)):
            raise ValueError('Committed/picked work cannot be rebuilt. Resolve physical stock and use an audited reconciliation.')
        if eligible(line):raise ValueError(eligible(line))
        product,policy,req,bom=preparation(line)
        details=dict(previous_bom=json.loads(work.bom_json),previous_policy=json.loads(work.policy_json),
                     previous_commitment=str(work.committed_at),previous_operator=work.committed_by)
        _release(work)
        old={r.item_id:r for r in WorkRequirement.query.filter_by(line_id=line_id).all()}
        for r in old.values():r.active=r.item_id in req
        for iid,amount in req.items():
            if iid in old:old[iid].quantity=amount;old[iid].active=True
            else:db.session.add(WorkRequirement(line_id=line_id,item_id=iid,quantity=amount,reserved=0,active=True,unit=stock.item_for(iid).unit))
        work.product_id=product.id;work.policy_json=dumps(policy);work.bom_json=dumps(bom)
        work.proof_required=policy.get('proof_required',False);work.state='awaiting_proof' if work.proof_required else 'ready'
        work.hold_reason='';work.source_digest=fingerprint(line)
        if action=='replan':work.committed_at=None;work.committed_by=None
    elif action=='proof_override':
        if work.committed_at:raise ValueError('Cannot change proof requirements after production commitment.')
        if data.get('required') not in {'yes','no'}:raise ValueError('Choose an explicit proof requirement.')
        if work.state not in {'ready','awaiting_proof','needs_configuration','proof_approved'}:raise ValueError('Proof override is unavailable at this stage.')
        if not json.loads(work.policy_json).get('type'):
            product,policy,req,bom=preparation(line,proof_override=data['required']=='yes')
            work.product_id=product.id;work.policy_json=dumps(policy);work.bom_json=dumps(bom)
            for r in requirements(line_id):db.session.delete(r)
            db.session.flush()
            for iid,amount in req.items():db.session.add(WorkRequirement(line_id=line_id,item_id=iid,quantity=amount,reserved=0,unit=stock.item_for(iid).unit))
            work.source_digest=fingerprint(line)
        work.proof_required=data['required']=='yes';work.state='awaiting_proof' if work.proof_required else 'ready'
        details=dict(required=work.proof_required,method='Operator override',evidence=reason)
    elif action=='cancel':
        if work.state in TERMINAL:raise ValueError('Completed/cancelled work requires explicit reconciliation, not routine cancellation.')
        if work.committed_at or any(picked(r) for r in requirements(line_id)):
            if data.get('exception')!='yes' or len(reason)<12 or not data.get('reconciliation'):
                raise ValueError('Production/picking is committed. An authorised exception with stock, batch and refund reconciliation is required.')
            details=dict(exception=True,reconciliation=stock.clean(data['reconciliation'],'Stock/refund reconciliation',400,required=True))
        work.state='cancelled';_release(work)
        details['commercial_order_cancelled']=False
    elif action=='hold':
        if work.state in TERMINAL:raise ValueError('This work is already complete/cancelled.')
        work.prior_state=work.state;work.state='hold';work.hold_reason=reason;_release(work)
    elif action=='resume':
        if work.state!='hold' or eligible(line):raise ValueError(eligible(line) or 'Only a reconciled hold can be resumed.')
        if data.get('exception')!='yes':raise ValueError('An explicit authorised reconciliation acknowledgement is required.')
        if work.prior_state not in LABELS or work.prior_state in TERMINAL|{'needs_configuration','hold'}:raise ValueError('Refresh the uncommitted workflow instead.')
        # A changed purchase quantity/identity must not reuse an obsolete BOM.
        snapshot=json.loads(work.bom_json)
        if (snapshot.get('quantity')!=line.current_quantity or snapshot.get('sku')!=line.sku
                or snapshot.get('product_id')!=line.product_id or snapshot.get('variant_id')!=line.variant_id
                or snapshot.get('properties_digest')!=stock.digest(json.loads(line.properties_json))):
            raise ValueError('Frozen BOM differs from the current line. Return picked stock and cancel/re-plan explicitly.')
        work.state=work.prior_state;work.hold_reason='';work.source_digest=fingerprint(line)
    elif action=='return_pick':
        original=db.session.get(WorkPick,data.get('pick_id'))
        if not original or original.quantity<=0 or db.session.get(WorkRequirement,original.requirement_id).line_id!=line_id:
            raise ValueError('Choose a positive pick from this order line.')
        if WorkPick.query.filter_by(reverses_id=original.id).first():raise ValueError('This pick was already returned.')
        if work.state=='dispatched':raise ValueError('Dispatched stock requires a separate physical returns workflow.')
        payload=dict(original=original.transaction_id,reason=reason)
        plan=stock.plan('reversal',payload,order_pick_return=True)
        tx=stock.post('reversal',payload,key,stock.digest(plan),actor=actor,order_pick_return=True)
        new_pick=WorkPick(id=key,requirement_id=original.requirement_id,transaction_id=tx.id,quantity=-original.quantity,reverses_id=original.id)
        work.prior_state=prior;work.state='hold';work.hold_reason='Picked stock returned; reconcile the work before restarting.'
        _release(work);details=dict(original_pick=original.id,transaction=tx.id)
    else:
        _guard(work,line)
        proof=latest_proof(line_id)
        if action in {'prepare_proof','send_proof','approve_proof','changes','contact'}:
            if not work.proof_required:raise ValueError('This line does not require a proof.')
            if action=='prepare_proof':
                if prior not in {'awaiting_proof','changes_requested'}:raise ValueError('Prepare a proof only from Awaiting Proof or Changes Requested.')
                reference=str(data.get('reference') or '')
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{2,99}',reference):raise ValueError('Use an opaque private proof reference, not a URL, file path or customer token.')
                proof=WorkProof(line_id=line_id,version=(proof.version+1 if proof else 1),reference=reference,
                    state='preparing',actor=actor,created_at=now(),method='',evidence='')
                db.session.add(proof);work.state='proof_preparing'
            elif action=='send_proof':
                if prior!='proof_preparing' or not proof or data.get('reviewed')!='yes':raise ValueError('Review the prepared proof and explicitly confirm it was sent through an approved private channel.')
                proof.method=stock.clean(data.get('method'),'Approved communication method',100,required=True)
                proof.sent_at=now();proof.state='sent';proof.evidence=reason;work.state='proof_sent'
            elif action=='approve_proof':
                if prior!='proof_sent' or not proof:raise ValueError('A sent current proof is required; delivery/opening is never approval.')
                proof.method=stock.clean(data.get('method'),'Approval channel',100,required=True)
                proof.approved_at=now();proof.state='approved';proof.evidence=reason;proof.actor=actor;work.state='proof_approved'
            elif action=='changes':
                if prior!='proof_sent' or not proof:raise ValueError('Changes must refer to the currently sent proof.')
                proof.state='changes_requested';proof.evidence=reason;work.state='changes_requested'
            else:
                if prior not in {'proof_sent','proof_preparing'} or not proof:raise ValueError('Contact required applies to an active proof.')
                details['contact_required']=True
            details['proof_version']=proof.version
        else:
            if work.proof_required and (not proof or proof.state!='approved'):
                raise ValueError('Awaiting Proof Approval: the current proof must be explicitly approved before picking or production.')
            policy=json.loads(work.policy_json)
            if action=='ready':
                if prior!='proof_approved':raise ValueError('Only Proof Approved can advance to Ready for Production.')
                work.state='ready'
            elif action=='start_picking':
                if prior not in {'ready','proof_approved','waiting'}:raise ValueError('Picking can start only from a ready/waiting workflow.')
                if not requirements(line_id):raise ValueError('No inventory requirements are configured for picking.')
                _reserve(work);work.state='waiting' if any(r.reserved<remaining(r) for r in requirements(line_id)) else 'picking'
            elif action=='pick':
                if prior not in {'picking','waiting','production'}:raise ValueError('Start picking/production before confirming a stock issue.')
                r=db.session.get(WorkRequirement,int(data.get('requirement_id','0')))
                if not r or r.line_id!=line_id:raise ValueError('Choose a requirement from this purchased line.')
                code=str(data.get('code') or '').strip().upper()
                batch=ProductionBatch.query.filter_by(code=code).first()
                identity=stock.lookup(code)
                if (batch.component_id if batch else identity.id if identity and identity.kind=='item' else None)!=r.item_id:
                    raise ValueError('Wrong component/batch scanned; no stock was changed. Tub barcodes identify locations, not components.')
                item=stock.item_for(r.item_id)
                amount=stock.converted(stock.number(data.get('quantity'),'Picked quantity',positive=True),r.unit,item.unit)
                if amount>remaining(r):raise ValueError('Picked quantity exceeds the remaining order requirement.')
                if amount>available(r.item_id)+r.reserved:raise ValueError('Stock is reserved for another order; not enough available.')
                r.reserved=max(0,r.reserved-amount);db.session.flush()
                location_id=int(data.get('location_id','0'))
                if not batch and item.kind=='manufactured':
                    batched=db.session.scalar(select(func.coalesce(func.sum(Movement.qty),0)).where(
                        Movement.item_id==item.id,Movement.location_id==location_id,Movement.batch_id.isnot(None)))
                    if amount>stock.balance(item.id,location_id)-batched:
                        raise ValueError('Scan the specific production batch for batched stock; permanent codes can issue only unbatched opening stock.')
                if batch:
                    tx=stock.remove_from_batch(batch.id,stock.quantity_text(amount),location_id,f'Order {line.order.name}: {reason}'[:400],key,actor=actor)
                else:
                    payload=dict(item_id=str(item.id),qty=stock.quantity_text(amount),source=str(location_id),reason=f'Order {line.order.name}: {reason}'[:400])
                    planned=stock.plan('use',payload);tx=stock.post('use',payload,key,stock.digest(planned),actor=actor)
                new_pick=WorkPick(id=key,requirement_id=r.id,transaction_id=tx.id,quantity=amount)
                details=dict(requirement_id=r.id,item_id=item.id,batch_id=batch.id if batch else None,transaction=tx.id,quantity=amount)
            elif action=='finish_picking':
                if prior not in {'picking','waiting'} or any(remaining(r) for r in requirements(line_id)):
                    raise ValueError('Every required quantity must be confirmed; planned production is not picked stock.')
                work.state='quality' if policy['type']=='stock' else 'ready'
            elif action=='start_production':
                if prior not in {'ready','proof_approved'}:raise ValueError('Production starts only from a ready or approved line.')
                if policy.get('needs_picking') and any(remaining(r) for r in requirements(line_id)):
                    raise ValueError('Finish picking the actual component requirements first.')
                work.state='production'
                if not work.committed_at:work.committed_at=now();work.committed_by=actor
                details=dict(type=policy['type'],personalised=policy.get('personalised',False),
                             policy='Internal review lock only; statutory remedies and external Shopify authority remain unchanged.')
            elif action in {'assembly','quality','ready_pack','packing','ready_dispatch','dispatch'}:
                edges={'assembly':{'production'},'quality':{'production','assembly'},'ready_pack':{'quality'},
                       'packing':{'ready_pack'},'ready_dispatch':{'packing'},'dispatch':{'ready_dispatch'}}
                if prior not in edges[action]:raise ValueError('This transition skips a required production/quality/packing stage.')
                if action in {'assembly','quality'} and any(remaining(r) for r in requirements(line_id)):
                    raise ValueError('Confirm actual material/component issues before completing production.')
                work.state='dispatched' if action=='dispatch' else action
                if action=='dispatch':
                    details=dict(carrier=stock.clean(data.get('carrier'),'Carrier / handoff method',100,required=True),
                        tracking=stock.clean(data.get('tracking'),'Tracking / dispatch reference',160,required=True),
                        handoff_at=now().isoformat(),shopify_fulfilled=False)
                    _release(work)
            else:raise ValueError('Unknown order workflow action.')
    work.version+=1
    _event(work,key,action,prior,actor,reason,details,signature)
    if new_pick:db.session.add(new_pick)
    db.session.flush()
    return work


def summary(order):
    active=[l for l in order.lines if l.is_current and l.current_quantity>0]
    states=[(db.session.get(OrderWorkLine,l.id).state if db.session.get(OrderWorkLine,l.id) else line_view(l)['state']) for l in active]
    if not states:return 'No current lines'
    if all(s=='dispatched' for s in states):return 'Dispatched locally'
    if all(s=='cancelled' for s in states):return 'Local work cancelled — verify Shopify separately'
    if 'cancelled' in states:return 'Needs Attention'
    if any(s in {'hold','needs_configuration'} for s in states):return 'Needs Attention'
    if all(s in {'ready_dispatch','dispatched'} for s in states):return 'Ready for Dispatch'
    return LABELS[states[0]] if len(set(states))==1 else 'Mixed — see individual lines'


def requirement_view(r):
    item=db.session.get(Item,r.item_id)
    balance=stock.balance(item.id)
    return dict(row=r,item=item,required=r.quantity,picked=picked(r),remaining=remaining(r),reserved=r.reserved,
        on_hand=balance,available=available(item.id),shortage=max(0,remaining(r)-r.reserved-available(item.id)),
        locations=stock.item_balances(item.id),batches=ProductionBatch.query.filter_by(component_id=item.id,status='completed').all())


def line_view(line):
    work=db.session.get(OrderWorkLine,line.id)
    if not work:
        try:
            product,policy,req,bom=preparation(line)
            state='hold' if eligible(line) else 'awaiting_proof' if policy['proof_required'] else 'ready'
            issue=eligible(line)
        except (ValueError,TypeError,KeyError) as exc:state='needs_configuration';issue=str(exc);policy={}
        previews=[]
        if policy:
            for iid,amount in req.items():
                transient=WorkRequirement(line_id=line.id,item_id=iid,quantity=amount,reserved=0,active=True,unit=stock.item_for(iid).unit)
                previews.append(requirement_view(transient))
        return dict(line=line,work=None,state=state,issue=issue,policy=policy,requirements=previews,proofs=[],events=[],picks=[])
    return dict(line=line,work=work,state=work.state,issue=work.hold_reason,policy=json.loads(work.policy_json),
        requirements=[requirement_view(r) for r in requirements(line.id)],
        proofs=WorkProof.query.filter_by(line_id=line.id).order_by(WorkProof.version.desc()).all(),
        events=WorkEvent.query.filter_by(line_id=line.id).order_by(WorkEvent.at.desc()).limit(100).all(),
        picks=WorkPick.query.join(WorkRequirement).filter(WorkRequirement.line_id==line.id).all())


def stock_health():
    """Ledger physical balance, separate reservations/demand, and separately labelled plans."""
    rows=[];counts=dict(total=0,healthy=0,low=0,out=0,manufacture=0,order=0)
    for item in Item.query.filter_by(active=True).order_by(Item.name).all():
        on_hand=stock.balance(item.id);committed=reserved(item.id);avail=max(0,on_hand-committed)
        demands=WorkRequirement.query.join(OrderWorkLine).filter(WorkRequirement.item_id==item.id,WorkRequirement.active.is_(True),
            OrderWorkLine.state.notin_(['cancelled','dispatched','hold','needs_configuration'])).all()
        outstanding=sum(remaining(r) for r in demands)
        uncovered=max(0,outstanding-on_hand)
        planned=ProductionBatch.query.filter_by(component_id=item.id,status='planned').all()
        incoming=sum(b.planned_good*stock.SCALE for b in planned)
        reorders=WorkPlan.query.filter(WorkPlan.item_id==item.id,WorkPlan.kind=='purchase',WorkPlan.state!='cancelled').all()
        incoming+=sum(purchase_incoming(p) for p in reorders)
        target=item.reorder if item.reorder is not None else item.minimum
        need=max(0,target+outstanding-on_hand-incoming) if avail<item.minimum or uncovered else 0
        health='out' if avail==0 else 'low' if avail<item.minimum else 'healthy'
        action='manufacture' if item.kind=='manufactured' else 'order'
        counts['total']+=1;counts[health]+=1
        if need:counts[action]+=1
        batches=int((Decimal(need)/stock.SCALE/item.yield_qty).to_integral_value(rounding=ROUND_CEILING)) if need and item.yield_qty else None
        try:pack=stock.converted(item.pack_qty,item.pack_unit,item.unit) if item.pack_qty and item.pack_unit else None
        except ValueError:pack=None  # incompatible legacy units are unknown, never guessed
        packs=int((Decimal(need)/pack).to_integral_value(rounding=ROUND_CEILING)) if need and pack else None
        rows.append(dict(item=item,on_hand=on_hand,reserved=committed,available=avail,demand=outstanding,
            uncovered=uncovered,incoming=incoming,need=need,health=health,action=action,batches=batches,packs=packs))
    return dict(rows=rows,counts=counts)


def plan_replenishment(item_id,key,*,actor,requirement_id=None):
    if not re.fullmatch(r'[a-f0-9]{32}',str(key)):raise ValueError('A stable planning reference is required.')
    stock.writer_lock();item=stock.item_for(item_id)
    signature=stock.digest(dict(item_id=item.id,requirement_id=requirement_id,actor=actor))
    prior=db.session.get(WorkPlan,key)
    if prior:
        if prior.digest!=signature:raise ValueError('This planning reference belongs to another request.')
        return prior
    if requirement_id:
        requirement=db.session.get(WorkRequirement,requirement_id)
        if not requirement or requirement.item_id!=item.id:raise ValueError('Shortage must reference this exact requirement.')
    row=next(r for r in stock_health()['rows'] if r['item'].id==item.id)
    if not row['need']:raise ValueError('No uncovered replenishment is required after existing planned/incoming work.')
    if item.kind=='manufactured':
        if not row['batches']:raise ValueError('Configure an expected successful batch yield first.')
        count=min(40,row['batches']);batches=stock.reserve_batches(item.id,count,key)
        quantity=sum(b.planned_good for b in batches)*stock.SCALE
        details=dict(batch_ids=[b.id for b in batches],estimate=True)
        kind='manufacturing'
    else:
        quantity=row['packs']*stock.converted(item.pack_qty,item.pack_unit,item.unit) if row['packs'] else row['need']
        details=dict(packs=row['packs'],supplier=item.supplier,expected_only=True,received=False)
        kind='purchase'
    plan=WorkPlan(id=key,requirement_id=requirement_id,item_id=item.id,kind=kind,quantity=quantity,
        document=dumps(details),state='planned',actor=actor_text(actor),created_at=now(),digest=signature)
    db.session.add(plan);db.session.flush();return plan


def purchase_incoming(plan):
    received=0
    for identity in json.loads(plan.document).get('receipts',[]):
        tx=db.session.get(Transaction,identity)
        if tx and not Transaction.query.filter_by(reverses_id=tx.id).first():
            received+=sum(m.qty for m in tx.moves if m.item_id==plan.item_id and m.qty>0)
    return max(0,plan.quantity-received)


def reconcile_purchase(plan_id,receipt_id,key,*,actor,reason,cancel=False):
    stock.writer_lock();plan=db.session.get(WorkPlan,plan_id)
    if not plan or plan.kind!='purchase':raise ValueError('Choose a saved purchase plan.')
    if not re.fullmatch(r'[a-f0-9]{32}',str(key)):raise ValueError('A stable receipt-link action reference is required.')
    reason=stock.clean(reason,'Receiving/reconciliation evidence',400,required=True)
    document=json.loads(plan.document);signature=stock.digest(dict(receipt=receipt_id,actor=actor,reason=reason,cancel=cancel))
    prior=next((e for e in document.get('audit',[]) if e['id']==key),None)
    if prior:
        if prior['digest']!=signature:raise ValueError('This action reference was already used.')
        return plan
    if cancel:plan.state='cancelled'
    else:
        tx=db.session.get(Transaction,receipt_id)
        if not tx or tx.kind!='receive' or tx.item_id!=plan.item_id or Transaction.query.filter_by(reverses_id=receipt_id).first():
            raise ValueError('Choose an unreversed Receive Stock receipt for this exact item.')
        for other in WorkPlan.query.filter_by(item_id=plan.item_id,kind='purchase').all():
            if receipt_id in json.loads(other.document).get('receipts',[]):raise ValueError('That receipt is already linked to a purchase plan.')
        document.setdefault('receipts',[]).append(receipt_id)
        plan.document=dumps(document);plan.state='received' if purchase_incoming(plan)==0 else 'planned'
    document.setdefault('audit',[]).append(dict(id=key,digest=signature,actor=str(actor),at=now().isoformat(),reason=reason,receipt=receipt_id,cancel=cancel))
    plan.document=dumps(document);db.session.flush();return plan
