"""Existing durable worker: routine reads and separately authorised sale prices."""
import json
import uuid
from datetime import timedelta

from flask import current_app
from sqlalchemy import update, or_
from app import db
from app.models import (ScheduledTask, ScheduledTaskRun, Product, ShopifyProductComparison,
    ShopifyProductIdentity, ShopifyCollectionIdentity, ShopifyAttributeIdentity, CatalogueOperation)
from app import shopify_comparison as comparison, shopify_drafts as drafts
from app.shopify_capacity import BackgroundYield, background_work
from app.utils.operation_control import CatalogueOperationActive, sanitize_operation_error

KEY = "shopify_products"
INTERVALS = (10, 20, 30, 60)
LEASE = timedelta(seconds=120)
MAX_PRODUCTS = 1000
MAX_SLICES = 60
FULL_PER_SLICE = 8
DEFINITION = {"name":"Shopify Product Reconciliation",
    "description":"Read-only verification of product, variant, inventory, media, taxonomy and attribute evidence."}
SALE_KEY='shopify_sales'
SALE_DEFINITION={'name':'Scheduled Sale Transitions','description':'Only explicitly authorised due price transitions; no due sale means no Shopify request.'}
ORDERS_KEY='shopify_orders'


def definition(key):
    from app.catalogue_startup import KEY as local_key, DEFINITION as local_definition
    if key==local_key: return local_definition
    if key==ORDERS_KEY:
        from app.shopify_orders import DEFINITION as orders_definition
        return orders_definition
    return SALE_DEFINITION if key==SALE_KEY else DEFINITION


def intervals(key):
    return (5,10,20,30,60) if key in {SALE_KEY,ORDERS_KEY} else INTERVALS
COUNTERS = ("considered", "skipped_recent", "checked", "up_to_date", "updates", "attention", "unavailable", "deferred", "api_requests", "throttled")


def now():
    return drafts.now()


def encoded(data):
    value = json.dumps(data, sort_keys=True)
    if len(value.encode()) > 65536:
        raise ValueError("Task progress exceeds its bound.")
    return value


def snapshot(task):
    run = db.session.get(ScheduledTaskRun, task.active_run_id) if task.active_run_id else ScheduledTaskRun.query.filter_by(task_key=task.key).order_by(ScheduledTaskRun.created_at.desc()).first()
    data=json.loads(run.data_json) if run else {}
    return {"key":task.key, **definition(task.key), "enabled":task.enabled,"interval":task.interval_minutes,
        'intervals':intervals(task.key),
        "state":task.state,"outcome":task.last_outcome,
        "duration":max(0,(now()-run.started_at).total_seconds()) if task.active_run_id and run and run.started_at else None if task.active_run_id else task.duration_seconds,
        **{key:getattr(task,key).isoformat()+"Z" if getattr(task,key) and not (task.key==ORDERS_KEY and key in {'next_due','last_scheduled'}) else None for key in
            ("next_due","last_scheduled","last_started","last_completed","last_successful")},
        "run_id":task.active_run_id,"progress":{**data.get("counts",{}),"remaining":data.get('pending_orders',len(data.get("queue",[])))} if run else {},
        "reason":json.loads(run.data_json).get("reason") if run else None,"error":run.error if run else None}


def configure(key, *, enabled, interval):
    if key==ORDERS_KEY:
        raise ValueError('Orders use startup/manual catch-up and webhooks, not recurring API polling.')
    if key not in {KEY,SALE_KEY,ORDERS_KEY} or type(enabled) is not bool or type(interval) is not int or interval not in intervals(key):
        raise ValueError("Choose an enabled state and 10, 20, 30 or 60 minutes.")
    task = db.session.get(ScheduledTask, key)
    changed = task.interval_minutes != interval or task.enabled != enabled
    values = {"enabled":enabled,"interval_minutes":interval}
    if changed:
        values["next_due"] = now()+timedelta(minutes=interval)
    if not task.active_run_id:
        values["state"] = "idle" if enabled else "disabled"
    db.session.execute(update(ScheduledTask).where(ScheduledTask.key==key).values(**values))
    db.session.commit()
    db.session.expire_all()
    return snapshot(db.session.get(ScheduledTask,key))


def enqueue(key=KEY, *, manual=False, product_ids=None, startup=False, orders_rescan=False):
    if key not in {KEY,SALE_KEY,ORDERS_KEY}:
        raise ValueError("Unknown task.")
    if orders_rescan and (key!=ORDERS_KEY or not manual):raise ValueError('Recent Orders rescan requires explicit manual authority.')
    if startup and (key not in {KEY,ORDERS_KEY} or manual or product_ids is not None):
        raise ValueError('Startup accepts only full read-only Products or Orders catch-up tasks.')
    if product_ids is not None and (key in {SALE_KEY,ORDERS_KEY} or not manual or not isinstance(product_ids,list) or not 1 <= len(product_ids) <= 25
            or any(type(i) is not int or i < 1 for i in product_ids) or len(set(product_ids)) != len(product_ids)):
        raise ValueError("An explicit manual scope accepts one to 25 distinct product IDs.")
    task = db.session.get(ScheduledTask,key)
    if key==ORDERS_KEY and not (manual or startup):
        return None  # No recurring Orders Admin API polling, even with an old enabled flag.
    run_id, stamp = uuid.uuid4().hex, now()
    conditions = [ScheduledTask.key==key, ScheduledTask.active_run_id.is_(None)]
    if not manual:
        if not (startup and key==ORDERS_KEY): conditions += [ScheduledTask.enabled.is_(True)]
        if not startup: conditions += [ScheduledTask.next_due<=stamp]
    values = {"active_run_id":run_id,"state":"queued","owner":None,"lease_until":None,"resume_at":None}
    if not manual:
        values.update(next_due=stamp+timedelta(minutes=task.interval_minutes))
        if not startup: values['last_scheduled']=stamp
    claimed = db.session.execute(update(ScheduledTask).where(*conditions).values(**values)).rowcount
    if claimed:
        db.session.add(ScheduledTaskRun(id=run_id, task_key=key, trigger="startup" if startup else "manual" if manual else "scheduled",
            state="queued",created_at=stamp,data_json=encoded({"version":1,"scope":product_ids,"counts":{k:0 for k in COUNTERS},"slices":0,"items":[],"rescan_recent":bool(orders_rescan)})))
    db.session.commit()
    db.session.expire_all()
    return db.session.get(ScheduledTask,key).active_run_id


def enqueue_startup():
    """Supervisor-only boot handoff; no network or catalogue traversal here."""
    from app.catalogue_startup import retire
    retire()
    return enqueue(startup=True)


def renew(key, run_id, owner):
    stamp = now()
    # Separate short transaction; never hold a write transaction over network I/O.
    with db.engine.begin() as conn:
        changed = conn.execute(update(ScheduledTask).where(ScheduledTask.key==key,
            ScheduledTask.active_run_id==run_id,ScheduledTask.owner==owner,ScheduledTask.lease_until>stamp
        ).values(lease_until=stamp+LEASE)).rowcount
    if changed != 1:
        raise BackgroundYield("Scheduled execution ownership expired or changed; old worker cannot save evidence.")


def claim(owner):
    stamp=now()
    candidate=ScheduledTask.query.filter(ScheduledTask.key.in_({KEY,SALE_KEY,ORDERS_KEY}),ScheduledTask.active_run_id.isnot(None),
        or_(ScheduledTask.resume_at.is_(None),ScheduledTask.resume_at<=stamp),
        or_(ScheduledTask.owner.is_(None),ScheduledTask.lease_until<=stamp)).order_by((ScheduledTask.key==SALE_KEY).desc(),ScheduledTask.next_due).first()
    if not candidate:
        return None
    run_id,key=candidate.active_run_id,candidate.key
    previous_owner=candidate.owner
    changed=db.session.execute(update(ScheduledTask).where(ScheduledTask.key==key,ScheduledTask.active_run_id==run_id,
        or_(ScheduledTask.owner.is_(None),ScheduledTask.lease_until<=stamp)
    ).values(owner=owner,lease_until=stamp+LEASE,state="running",resume_at=None)).rowcount
    if changed != 1:
        db.session.rollback(); return None
    db.session.commit(); db.session.expire_all()
    run=db.session.get(ScheduledTaskRun,run_id)
    data=json.loads(run.data_json)
    if previous_owner:
        data["restarts"]=data.get("restarts",0)+1
        # Only abandoned read-only operations from this exact expired owner.
        for op in CatalogueOperation.query.filter_by(operation_type="shopify_status_refresh",status="running").all():
            scope=json.loads(op.scope)
            if scope.get("scheduled_run")==run_id and scope.get("scheduled_owner")==previous_owner:
                op.status="interrupted";op.finished_at=stamp;op.recovery_state="none"
                op.error="Expired scheduled read-only owner; retained comparisons preserved."
        if key==SALE_KEY:
            for op in CatalogueOperation.query.filter_by(operation_type='shopify_draft_publish',status='running').all():
                scope=json.loads(op.scope)
                if scope.get('scheduled_run')==run_id and scope.get('scheduled_owner')==previous_owner:
                    op.status='interrupted';op.finished_at=stamp;op.recovery_state='review_required'
                    op.error='Scheduled price owner expired. Read-only reconciliation required; never replay the mutation.'
    run.started_at=run.started_at or stamp
    run.state="running";run.data_json=encoded(data)
    guarded=db.session.execute(update(ScheduledTask).where(ScheduledTask.key==key,ScheduledTask.active_run_id==run_id,
        ScheduledTask.owner==owner,ScheduledTask.lease_until>now()).values(last_started=run.started_at)).rowcount
    if guarded!=1:
        db.session.rollback();return None
    db.session.commit()
    return key,run_id


def fence(key,run_id,owner):
    stamp=now()
    changed=db.session.execute(update(ScheduledTask).where(ScheduledTask.key==key,
        ScheduledTask.active_run_id==run_id,ScheduledTask.owner==owner,ScheduledTask.lease_until>stamp
    ).values(lease_until=stamp+LEASE).execution_options(autoflush=False)).rowcount
    if changed!=1:
        db.session.rollback();raise BackgroundYield("Execution owner changed before evidence commit.")


def save_progress(key,run_id,owner,data,*,pause=False,terminal=None,error=None):
    renew(key,run_id,owner)
    stamp=now()
    values={"state":"paused" if pause else "running"}
    if pause:
        values.update(owner=None,lease_until=None,resume_at=stamp+timedelta(seconds=15))
    run=db.session.get(ScheduledTaskRun,run_id)
    if terminal:
        values.update(state=terminal,last_outcome=terminal,last_completed=stamp,
            duration_seconds=max(0,(stamp-run.started_at).total_seconds()),active_run_id=None,owner=None,lease_until=None,resume_at=None)
        if terminal=="completed": values["last_successful"]=stamp
        elif data.get("queue") and data.get("scope") is None:
            values["selection_cursor"]=max(0,min(data["queue"])-1)
        run.completed_at=stamp;run.duration_seconds=values["duration_seconds"]
        if not db.session.get(ScheduledTask,key).enabled:values["state"]="disabled"
    changed=db.session.execute(update(ScheduledTask).where(ScheduledTask.key==key,ScheduledTask.active_run_id==run_id,
        ScheduledTask.owner==owner,ScheduledTask.lease_until>stamp).values(**values)).rowcount
    if changed!=1:
        db.session.rollback();raise BackgroundYield("Task ownership lost before progress commit.")
    run.state=terminal or ("paused" if pause else "running")
    run.data_json=encoded(data);run.error=sanitize_operation_error(error) if error else None
    db.session.commit();db.session.expire_all()
    if terminal:
        prune_history()


def prune_history():
    # Latest 100 completed runs retained, but older-than-90-day history expires.
    rows=ScheduledTaskRun.query.filter(ScheduledTaskRun.completed_at.isnot(None)).order_by(ScheduledTaskRun.completed_at.desc()).all()
    for index,row in enumerate(rows):
        if index>=100 or row.completed_at<now()-timedelta(days=90):
            db.session.delete(row)
    db.session.commit()


def recent_success(product,config,*,mappings=None,as_of=None):
    rows=ShopifyProductComparison.query.filter_by(source_identity=product.source_relpath).all()
    mappings=mappings if mappings is not None else {
        (r.store_id,r.source_key):r for model in (ShopifyCollectionIdentity,ShopifyAttributeIdentity) for r in model.query.all()}
    for saved in rows:
        data=json.loads(saved.summary_json)
        if data.get("config")!=config.fingerprint():continue
        identity=drafts.matching_identity(saved.store_id,product)
        if identity and (identity.state!="verified" or identity.verification_state!="verified"):continue
        try:valid=comparison.current(product,saved,identity,mappings,freshness=False)
        except (OSError,ValueError,KeyError,TypeError,RuntimeError):return False
        if not valid or valid.get("read_complete") is not True or valid.get("scope","full")!="full" or valid.get("refresh_failure") or valid.get("blockers"):
            continue
        timestamps=[valid.get(k) for k in ("full_compared_at","related_checked_at","revision_checked_at")]
        if valid.get("classification")=="create":timestamps=[valid.get("absence_checked_at")]
        native=valid.get("native") or {}
        if comparison.native_required(native):
            if native.get("classification") in {"blocked","pending","uncertain"}:continue
            timestamps.append(native.get("checked_at"))
        from datetime import datetime
        try:
            reference=as_of or now()
            parsed=[datetime.fromisoformat(t) for t in timestamps if t]
            # Later slices retain the run's eligibility cutoff. Complete proof
            # acquired after the run started also supersedes its queued reads.
            if len(parsed)==len(timestamps) and all(t<=now() and max(timedelta(0),reference-t)<timedelta(minutes=10) for t in parsed):
                return True
        except (ValueError,TypeError):pass
    return False


def protected(product):
    identities=ShopifyProductIdentity.query.filter_by(source_identity=product.source_relpath).all()
    if len(identities)>1:return True
    for identity in identities:
        from app.shopify_connection import ShopifyError
        try:data=drafts.evidence(identity)
        except ShopifyError:return True  # corrupt/untrusted operational evidence is protected, never overwritten
        if identity.state not in {"verified","processing","failed_before_create","remote_deleted"}:return True
        if data.get("native_dimensions",{}).get("state") in {"pending","uncertain"} or any(
                v.get("state") in {"pending","uncertain"} for v in data.get("variant_native",{}).values()):return True
    return False


def select_products(task,data):
    query=Product.query.filter(Product.product_type.in_({"simple","variable"}),Product.catalogue_status=="active")
    if data["scope"] is not None:query=query.filter(Product.id.in_(data["scope"]))
    elif task.selection_cursor:
        query=query.filter(Product.id>task.selection_cursor)
        if not query.first():
            query=Product.query.filter(Product.product_type.in_({"simple","variable"}),Product.catalogue_status=="active")
    total=query.count()
    ids=[p.id for p in query.order_by(Product.id).limit(MAX_PRODUCTS).all()]
    data.update(queue=ids, initialized=True, overflow=max(0,total-len(ids)))
    data["counts"]["considered"]=total
    data["counts"]["deferred"]=data["overflow"]
    # A one-product/manual scope must not reset the persistent whole-catalogue
    # sweep. Frequent scoped checks otherwise repeatedly restart at product one.
    if data["scope"] is None:
        task.selection_cursor=ids[-1] if ids and data["overflow"] else 0
    return data


def item_result(data,product,state):
    counts=data["counts"];counts["checked"]+=1
    bucket="up_to_date" if state["state"]=="no_change" and state["label"]=="Up to Date" else "updates" if state["state"]=="update" else "unavailable" if state["state"] in {"unavailable","unknown","local_changes"} else "attention"
    counts[bucket]+=1
    item = {"id":product.id,"sku":product.sku[:64],"state":state["label"]}
    if bucket in {'unavailable', 'attention'}:
        item['reason'] = sanitize_operation_error('; '.join(state.get('blockers', [])) or state.get('message', ''))[:220]
    data["items"]=(data["items"]+[item])[-100:]


def group_errors(data, errors):
    """Bounded grouped summary; SKU-specific reasons remain in recent outcomes."""
    if 'error_counts' not in data:
        errors = [{'message': message} for message in data.get('errors', [])] + errors
    groups = data.setdefault('error_counts', {})
    for error in errors:
        message = sanitize_operation_error(error['message'])[:500]
        if message not in groups and len(groups) >= 10:
            message = 'Additional distinct failures; expand recent product outcomes.'
        groups[message] = groups.get(message, 0) + 1
    data['errors'] = [message + (f' ({count} products)' if count > 1 else '')
        for message, count in groups.items()]


def run_slice(key,run_id,owner,*,adapter=None):
    if key not in {KEY,SALE_KEY,ORDERS_KEY}: raise ValueError('Unknown or retired task; history is read-only.')
    if key==ORDERS_KEY:
        from app.shopify_orders import run_slice as orders_slice
        return orders_slice(run_id,owner,adapter=adapter)
    if key==SALE_KEY:
        return sale_slice(key,run_id,owner,adapter=adapter)
    from app.shopify_connection import ShopifyAdapter,ShopifyError
    from app import shopify_status
    from app.commerce_config import CommerceConfigError
    task=db.session.get(ScheduledTask,key);run=db.session.get(ScheduledTaskRun,run_id)
    data=json.loads(run.data_json)
    try:
        renew(key,run_id,owner)
        if not data.get("initialized"):
            data=select_products(task,data)
            save_progress(key,run_id,owner,data)
        if not task.enabled and run.trigger in {"scheduled","startup"}:
            data["reason"]="Disabled by administrator; outstanding products retained for future runs."
            data["counts"]["deferred"]+=len(data["queue"])
            return save_progress(key,run_id,owner,data,terminal="partially_completed")
        if not data["queue"]:
            return save_progress(key,run_id,owner,data,terminal="completed")
        config=drafts.configuration()
        adapter=adapter or ShopifyAdapter()
        pending=data["queue"][:25]
        products=Product.query.filter(Product.id.in_(pending)).order_by(Product.id).all()
        mappings={(r.store_id,r.source_key):r for model in (ShopifyCollectionIdentity,ShopifyAttributeIdentity) for r in model.query.all()}
        eligible=[];consumed=set()
        for product in products:
            if protected(product):
                consumed.add(product.id);data["counts"]["attention"]+=1;data["counts"]["deferred"]+=1
                data["items"]=(data["items"]+[{"id":product.id,"sku":product.sku,"state":"Recovery / scoped review protected"}])[-100:]
            elif run.trigger!="manual" and recent_success(product,config,mappings=mappings,as_of=run.started_at):
                consumed.add(product.id);data["counts"]["skipped_recent"]+=1
            else:eligible.append(product)
        consumed.update(set(pending)-{p.id for p in products})
        result={"checked":[],"pending":[],"errors":[]}
        if eligible:
            with background_work(lambda:renew(key,run_id,owner),fence=lambda:fence(key,run_id,owner)) as budget:
                try:
                    # Selection already applies the exact <10 minute rule.
                    # Eligible scheduled reads also bypass browser backoff/cache.
                    result=shopify_status.refresh(eligible,force=True,adapter=adapter,
                        full_limit=FULL_PER_SLICE,scheduled_run=(run_id,owner))
                except CatalogueOperationActive:
                    result.update(pending=[p.id for p in eligible],deferred_reason="Interactive catalogue operation owns the execution slot.")
                finally:
                    data["counts"]["api_requests"]+=budget["requests"]
                    data["counts"]["throttled"]+=budget["throttled"]
            from app.shopify_products import states_for
            # UI URLs need an inert request context; this does not invoke routes.
            with current_app.test_request_context():
                states=states_for([p for p in eligible if p.id in result["checked"]])
            for product in eligible:
                if product.id in result["checked"]:
                    consumed.add(product.id);item_result(data,product,states[product.id])
            data["reason"]=result.get("deferred_reason")
            if result["errors"]:
                group_errors(data, result['errors'])
        data["queue"]=[i for i in data["queue"] if i not in consumed]
        # A costly parent must not monopolise the front of the queue. Do not
        # manufacture unavailable evidence from a local budget/priority deferral.
        if result.get("deferred_reason") and result.get("pending"):
            first=result["pending"][0]
            if first in data["queue"]:
                data["queue"].remove(first);data["queue"].append(first)
                reason=result["deferred_reason"]
                if "budget" in reason.lower() or "capacity" in reason.lower():
                    attempts=data.setdefault("budget_attempts",{})
                    attempts[str(first)]=attempts.get(str(first),0)+1
                    if attempts[str(first)]>=3:
                        data["queue"].remove(first);data["counts"]["deferred"]+=1
                        data["deferred_ids"]=(data.get("deferred_ids",[])+[first])[-100:]
        data["slices"]+=1
        global_failure=any(not e.get("product_id") for e in result["errors"])
        exhausted=data["slices"]>=MAX_SLICES or now()-run.started_at>=timedelta(minutes=20)
        if global_failure or exhausted or not data["queue"]:
            data["counts"]["deferred"]+=len(data["queue"])
            terminal="failed" if global_failure and not data["counts"]["up_to_date"] and not data["counts"]["updates"] else "partially_completed" if data["counts"]["deferred"] or data["counts"]["unavailable"] or data["counts"]["attention"] or data.get("errors") else "completed"
            return save_progress(key,run_id,owner,data,terminal=terminal,error="; ".join(data.get("errors",[])))
        save_progress(key,run_id,owner,data,pause=bool(data.get("reason")))
        if not data.get("reason"):
            # Bounded continuation, same durable run; no competing browser worker.
            save_progress(key,run_id,owner,data,pause=True)
    except BackgroundYield:
        # Lost ownership cannot finalize history or rewrite comparison evidence.
        db.session.rollback()
    except Exception as exc:
        data["counts"]["deferred"]+=len(data.get("queue",[]))
        message=str(exc) if isinstance(exc,(ShopifyError,CommerceConfigError,ValueError)) else "Task failed safely before complete comparison; retained product evidence remains."
        try:save_progress(key,run_id,owner,data,terminal="failed",error=message)
        except BackgroundYield:db.session.rollback()


def tick(owner,*,adapter=None):
    from app.catalogue_startup import retire
    retire()  # database-only retirement after any historical lease expires
    enqueue()
    enqueue(SALE_KEY)
    from app.order_webhooks import tick as webhook_tick
    webhook_tick(owner)  # Encrypted inbox only; never Orders Admin API.
    claimed=claim(owner)
    if claimed:
        run_slice(*claimed,owner,adapter=adapter)
    return claimed


def queue_projected(operation_id):
    """Local projection handoff: retain evidence, queue bounded reads, no writes.

    The existing single reconciliation lease coalesces concurrent requests. If
    occupied, the next fair sweep is made due; valid unaffected rows are skipped.
    """
    from app.models import CatalogueOperationItem
    try: config=drafts.configuration()
    except (ValueError,RuntimeError): return None
    sources=[r.source_path for r in CatalogueOperationItem.query.filter_by(operation_id=operation_id,database_state='committed').all()]
    if not sources: return None
    products=Product.query.filter(Product.source_relpath.in_(sources),Product.catalogue_status=='active').all()
    affected=[p.id for p in products if not recent_success(p,config)]
    if not affected: return None
    task=db.session.get(ScheduledTask,KEY)
    if not task: return None
    if task.active_run_id or len(affected)>25:
        # No active run is cancelled or rewritten. Fresh unaffected entries
        # will not make API requests when the next durable sweep selects them.
        db.session.execute(update(ScheduledTask).where(ScheduledTask.key==KEY).values(next_due=now()))
        db.session.commit()
    return enqueue(KEY,manual=True,product_ids=affected[:25])


def activity():
    """Attribute real task failures; routine deferral is not a global alarm."""
    from app.catalogue_startup import KEY as local_key
    values=[]
    for key in (KEY,SALE_KEY,ORDERS_KEY,local_key):
        task=db.session.get(ScheduledTask,key)
        if not task: continue
        view=snapshot(task)
        run=ScheduledTaskRun.query.filter_by(task_key=key).order_by(ScheduledTaskRun.created_at.desc()).first()
        data=json.loads(run.data_json) if run else {}
        real_issue=bool(run and (run.state=='failed' or run.error or
            key==SALE_KEY and data.get('counts',{}).get('attention') or
            any('Recovery' in str(i.get('state','')) for i in data.get('items',[]))))
        if real_issue:
            values.append({'key':key,'run_id':run.id,'attention':True,'label':definition(key)['name']+' — needs attention',
                'reason':run.error or data.get('reason') or 'Retained recovery/approved transition requires review.'})
        elif task.active_run_id:
            values.append({'key':key,'run_id':task.active_run_id,'attention':False,
                'label':definition(key)['name']+' — '+task.state.replace('_',' '),'reason':view['reason']})
    return next((v for v in values if v['attention']),values[0] if values else None)


def sale_slice(key,run_id,owner,*,adapter=None):
    from app import shopify_sales as sales
    from app.models import ShopifySaleSchedule
    from app.shopify_connection import ShopifyError
    task=db.session.get(ScheduledTask,key);run=db.session.get(ScheduledTaskRun,run_id)
    data=json.loads(run.data_json)
    try:
        renew(key,run_id,owner)
        if not task.enabled and run.trigger=='scheduled':
            data['reason']='Sale task disabled; existing remote prices are unchanged.'
            return save_progress(key,run_id,owner,data,terminal='partially_completed')
        due=ShopifySaleSchedule.query.filter(ShopifySaleSchedule.state.in_({'authorised','active','activating','ending'}),
            ShopifySaleSchedule.next_due<=now()).order_by(ShopifySaleSchedule.next_due).limit(4).all()
        data['counts']['considered']+=len(due)
        if not due: return save_progress(key,run_id,owner,data,terminal='completed')
        for schedule in due[:3]:
            with background_work(lambda:renew(key,run_id,owner),fence=lambda:fence(key,run_id,owner),price_transitions=True) as budget:
                try:
                    state=sales.transition(schedule,adapter,run_id=run_id,owner=owner)
                    data['counts']['checked']+=1
                    if state in {'active','completed','reconciled'}: data['counts']['up_to_date']+=1
                    else: data['counts']['attention']+=1
                    data['items']=(data['items']+[{'id':schedule.product_id,'sku':json.loads(schedule.evidence_json)['sku'],'state':state}])[-100:]
                except CatalogueOperationActive:
                    data['reason']='Interactive catalogue operation owns the write slot; sale remains due.'
                    data['counts']['deferred']+=1
                    break
                except ShopifyError as exc:
                    # A failed recovery read cannot downgrade an accepted or
                    # possibly accepted mutation to an ordinary validation issue.
                    schedule.state='recovery_required' if schedule.state in {'activating','ending','recovery_required'} else 'needs_attention'
                    schedule.error=str(exc)[:1000];schedule.updated_at=now()
                    fence(key,run_id,owner);db.session.commit()
                    data['counts']['attention']+=1
                    data['items']=(data['items']+[{'id':schedule.product_id,'sku':json.loads(schedule.evidence_json)['sku'],'state':schedule.state}])[-100:]
                finally:
                    data['counts']['api_requests']+=budget['requests'];data['counts']['throttled']+=budget['throttled']
        if len(due)>3: data['counts']['deferred']+=len(due)-3
        save_progress(key,run_id,owner,data,terminal='partially_completed' if data['counts']['attention'] or data['counts']['deferred'] else 'completed')
    except BackgroundYield:
        db.session.rollback()
        try:
            data['reason']='Ownership/capacity deferred; pending writes are never retried.'
            save_progress(key,run_id,owner,data,pause=True)
        except BackgroundYield: db.session.rollback()
    except Exception:
        db.session.rollback()
        try: save_progress(key,run_id,owner,data,terminal='failed',error='Scheduled sale requires investigation; retained write receipts remain authoritative.')
        except BackgroundYield: db.session.rollback()
