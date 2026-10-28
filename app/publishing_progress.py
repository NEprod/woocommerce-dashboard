"""Publishing snapshots/events in the existing bounded operation live_state.

Capture joins existing acknowledgement commits. GETs derive health without writes.
"""
import json
from datetime import datetime, timedelta
from app import db
from app.models import CatalogueOperation, ScheduledTask, ShopifyProductIdentity, ShopifyPublishJob
from app.utils.operation_live import _bounded_logs, scope_dict
from app.utils.redaction import redact_diagnostic


def update(op, *, stage, message, status='running', summary=None):
    from app.shopify_drafts import now
    scope=scope_dict(op);live=scope.get('live_state') or {};logs=live.get('logs') or []
    stamp=now().isoformat()+'Z'
    if live.get('stage')!=stage:
        sequence=live.get('next_sequence',1)
        logs.append({'sequence':sequence,'severity':'error' if status in {'failed','recovery_required'} else 'info',
            'line':stamp+' · '+redact_diagnostic(message,limit=500)})
        live['next_sequence']=sequence+1
    live.update(version=1,stage=stage,status=status,latest_message=redact_diagnostic(message,limit=500),
        heartbeat_at=stamp,updated_at=stamp,logs=_bounded_logs(logs))
    live['current_item']=scope.get('sku') or 'Reviewed product operation'
    if summary:live['publishing']=summary
    if live.get('publishing') and live['publishing'].get('uploads_acknowledged') is not None:
        p=live['publishing'];live['progress']={'unit':'uploads acknowledged','completed':p['uploads_acknowledged'],'total':p['total_images'],
            'percent':100*p['uploads_acknowledged']/p['total_images'] if p['total_images'] else 0}
    scope['live_state']=live;op.scope=json.dumps(scope,ensure_ascii=False,separators=(',',':'))


def capture(row, data):
    if not row.operation_id:return
    op=db.session.get(CatalogueOperation,row.operation_id)
    if not op:return
    images=(data.get('plan') or {}).get('images') or []
    uploads=data.get('upload_inputs')
    acknowledged=len(uploads) if isinstance(uploads,list) else None
    # Old receipts without binary acknowledgements are UNKNOWN, not zero.
    if acknowledged is None:
        acknowledged=sum(m.get('upload')=='completed' for m in data.get('media',[])) if any('upload' in m for m in data.get('media',[])) else None
    total_bytes=sum(i['size'] for i in images) if all(type(i.get('size')) is int for i in images) else None
    ack_bytes=sum(i['size'] for i in images[:acknowledged]) if acknowledged is not None and total_bytes is not None else None
    summary={'total_images':len(images),'uploads_acknowledged':acknowledged,'total_bytes':total_bytes,
        'bytes_acknowledged':ack_bytes,'batch':data.get('upload_batch_number'),'total_batches':data.get('upload_batch_total'),
        'last_acknowledgement':data.get('last_upload_ack_at'),'product_create_attempted':data.get('create_attempted'),
        'shopify_product_id':row.shopify_product_id,'pending_stage':data.get('stage'),
        'retry_reason':data.get('error')}
    from app.shopify_notifications import operation_facts
    facts = operation_facts(op, row, data)
    if facts['action'] != 'Created':
        summary.update(product_create_attempted=False, uploads_acknowledged=None, bytes_acknowledged=None,
            batch=None, total_batches=None, last_acknowledgement=None)
    stage=data.get('stage') or row.state
    if stage=='product_create' and row.shopify_product_id:stage='product_identity_confirmed'
    labels={'upload_batch_prepared':'Upload batch prepared','upload_batch_started':'Upload batch started',
        'batch_acknowledged':'Batch acknowledged','product_create':'Shopify product creation attempted',
        'product_identity_confirmed':'Product identity confirmed','media_processing':'Awaiting Shopify processing',
        'verified':'Final readback verified','gallery_uploading':'Gallery uploads acknowledged; awaiting next server slice'}
    update(op,stage=stage,message=labels.get(stage,stage.replace('_',' ').title()),summary=summary)


def view(op):
    """Read-only, exact worker health; browser connectivity is not a heartbeat."""
    from app.shopify_drafts import now, evidence
    from app.scheduled_tasks import LEASE
    scope=scope_dict(op);live=dict(scope.get('live_state') or {})
    manual=scope.get('manual_review');worker=scope.get('manual_worker') or {}
    task=db.session.get(ScheduledTask,worker.get('task_key')) if worker.get('task_key') else None
    if not task and scope.get('automatic_publishing'):
        context=scope['automatic_publishing'];owner=context.get('owner','')
        if ':' in owner:
            run_id,worker_owner=owner.split(':',1)
            candidate=db.session.get(ScheduledTask,'shopify_publisher')
            if candidate and candidate.active_run_id==run_id:
                task=candidate;worker={'run_id':run_id,'owner':worker_owner}
    row=ShopifyProductIdentity.query.filter_by(operation_id=op.id).first()
    invalid=False
    try:proof=evidence(row) if row else {}
    except ValueError:proof={};invalid=True
    healthy=bool(task and task.active_run_id==worker.get('run_id') and task.owner==worker.get('owner') and task.lease_until and task.lease_until>now())
    waiting=bool(manual and manual.get('state')=='waiting' and task and task.active_run_id==worker.get('run_id') and task.state=='paused' and task.owner is None
        and task.resume_at and now()<=task.resume_at+LEASE)
    state=live.get('status') or op.status
    reason=op.error or ''
    if invalid:state='recovery_required';reason='Retained publishing evidence is invalid; inspect the protected identity receipt. No retry is authorised.'
    elif op.status=='succeeded':state='completed'
    elif manual and manual.get('state') in {'failed','recovery_required'}:state=manual['state']
    elif op.status=='failed':state='failed'
    elif manual and manual.get('state')=='queued':
        stamp=datetime.fromisoformat(manual['accepted_at'])
        state='stalled' if now()-stamp>LEASE else 'queued'
        reason='No worker has claimed the accepted operation within its lease threshold; inspect the scheduler.' if state=='stalled' else 'Waiting for the durable server task; no browser connection is required.'
    elif healthy:state='running'
    elif waiting:state='waiting';reason='Awaiting Shopify processing/readback.' if row and row.state=='processing' else 'Waiting for the next byte-bounded upload slice.'
    elif (manual and manual.get('state') not in {'completed','failed','recovery_required'} and worker or op.status=='running'):
        state='stalled';reason='Worker lease expired or no durable heartbeat was retained. Inspect receipts; browser polling does not authorize a retry.'
    elif not manual and (row and row.state=='uploading' or (scope.get('operation_summary') or {}).get('stage')=='gallery_uploading'):
        state='attention';reason='Legacy request-scoped upload stopped at a bounded slice. No continuing manual task was retained; inspect acknowledgements before a fresh review.'
    elif not manual and row and row.state=='processing':
        state='waiting';reason='Awaiting Shopify processing; this receipt does not prove that an active worker is polling readback.'
    elif op.recovery_state not in {None,'none'}:state='recovery_required'
    if not live.get('stage'):live['stage']=proof.get('stage') or state
    if state == 'recovery_required' and live.get('stage') == 'verified':
        live['stage']='recovery_required'  # prior product readback is not overall success
    diagnostic=(scope.get('operation_summary') or {}).get('diagnostic') or {}
    if diagnostic:
        live['diagnostic']=diagnostic
        if state in {'recovery_required','failed'}:
            live['stage']=diagnostic['stage']
            reason=(reason+' '+diagnostic['category']+' at '+diagnostic['stage']+': '+diagnostic['reason']).strip()
    live.update(status=state,latest_message=reason or live.get('latest_message',''),worker_health={
        'state':'healthy' if healthy else 'waiting' if waiting else 'no_active_lease',
        'lease_until':task.lease_until.isoformat()+'Z' if task and task.lease_until else None,
        'heartbeat_at':(task.lease_until-LEASE).isoformat()+'Z' if healthy else live.get('heartbeat_at'),
        'run_id':worker.get('run_id')})
    if healthy:live['heartbeat_at']=live['worker_health']['heartbeat_at']
    if not live.get('publishing') and row:
        images=(proof.get('plan') or {}).get('images') or [];uploads=proof.get('upload_inputs')
        live['publishing']={'total_images':len(images),'uploads_acknowledged':len(uploads) if isinstance(uploads,list) else None,
            'bytes_acknowledged':sum(i['size'] for i in images[:len(uploads)]) if isinstance(uploads,list) and all(type(i.get('size')) is int for i in images) else None,
            'total_bytes':sum(i['size'] for i in images) if all(type(i.get('size')) is int for i in images) else None,
            'product_create_attempted':proof.get('create_attempted'),'shopify_product_id':row.shopify_product_id,
            'pending_stage':proof.get('stage'),'last_acknowledgement':proof.get('last_upload_ack_at')}
    if not live.get('publishing') and (scope.get('operation_summary') or {}).get('stage')=='gallery_uploading':
        retained=scope['operation_summary']
        live['publishing']={'total_images':retained.get('images'),'uploads_acknowledged':retained.get('uploads_acknowledged'),
            'product_create_attempted':retained.get('create_attempted'),'pending_stage':'gallery_uploading'}
    if row and live.get('publishing'):
        from app.shopify_notifications import operation_facts
        facts=operation_facts(op,row,proof)
        if facts['action'] != 'Created':
            live['publishing']={**live['publishing'],'product_create_attempted':False,
                'uploads_acknowledged':None,'bytes_acknowledged':None,'batch':None,'total_batches':None,
                'last_acknowledgement':None}
        live['publishing']['pending_stage']=live['stage']
    return live
