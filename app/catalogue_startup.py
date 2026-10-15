"""One durable incremental local job on managed container startup; no Shopify I/O.

Uses the existing scheduled worker, catalogue lease, scanner and marker journal.
The run checkpoint is derived operational evidence, not a second catalogue.
"""
import hashlib
import json
import os
import uuid
from pathlib import Path

from sqlalchemy import update
from app import db
from app.models import ScheduledTask, ScheduledTaskRun, CatalogueOperation, Product, Settings

KEY='catalogue_startup'
DEFINITION={'name':'Startup Catalogue Reconciliation',
    'description':'Incremental local source reconciliation after container startup. No Shopify publishing.'}
MAX_COLLECTIONS=256


def scanner_options(root):
    from app.utils.reconstruction import _database_identity_overlays
    return {'reset_sku_index':False,'identity_overrides':_database_identity_overlays(Path(root))}


def enqueue():
    from app import scheduled_tasks as tasks
    # Called once by the managed runtime, never by a GET or a web worker.
    task=db.session.get(ScheduledTask,KEY)
    if not task:
        db.session.add(ScheduledTask(key=KEY,enabled=False,interval_minutes=1440,next_due=tasks.now(),state='idle'))
        db.session.commit()
    run_id=uuid.uuid4().hex
    changed=db.session.execute(update(ScheduledTask).where(ScheduledTask.key==KEY,ScheduledTask.active_run_id.is_(None)).values(
        active_run_id=run_id,state='queued',owner=None,lease_until=None,resume_at=None)).rowcount
    if changed:
        db.session.add(ScheduledTaskRun(id=run_id,task_key=KEY,trigger='startup',state='queued',created_at=tasks.now(),
            data_json=tasks.encoded({'version':1,'counts':{k:0 for k in tasks.COUNTERS},'items':[]})))
    db.session.commit()
    return db.session.get(ScheduledTask,KEY).active_run_id


def signature(path):
    """Content-hash JSON; image identity/stat manifest. Ignore scanner outputs."""
    result=hashlib.sha256()
    for folder,dirs,files in os.walk(path,followlinks=False):
        if any((Path(folder)/d).is_symlink() for d in dirs):
            raise ValueError('Symlink product folders require explicit catalogue review.')
        dirs[:]=sorted(d for d in dirs if not d.startswith(('.','_')))
        for name in sorted(files):
            source=Path(folder)/name
            if name!='product_info.json' and source.suffix.lower() not in {'.png','.jpg','.jpeg','.webp','.gif'}:
                continue
            if source.is_symlink(): raise ValueError('Symlink source requires explicit catalogue review.')
            stat=source.stat()
            result.update(str(source.relative_to(path)).encode())
            if name=='product_info.json':
                if stat.st_size>4*1024*1024: raise ValueError('Source JSON exceeds its safe bound.')
                result.update(source.read_bytes())
            else: result.update(f'{stat.st_size}:{stat.st_mtime_ns}'.encode())
    return result.hexdigest()


def selection():
    from app.utils.scan_runner import build_scan_scope
    settings=Settings.query.first()
    if not settings or not all((settings.product_folder,settings.output_folder,settings.url_prefix)):
        raise ValueError('Configure catalogue/output folders and URL prefix before local startup reconciliation.')
    root=Path(settings.product_folder).resolve()
    if not root.is_dir(): raise ValueError('Catalogue mount is unavailable; existing database preserved.')
    paths=sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(('.','_')))
    if len(paths)>MAX_COLLECTIONS: raise ValueError('Startup collection scope exceeds 256; use the existing scoped scanner.')
    previous=ScheduledTaskRun.query.filter_by(task_key=KEY,state='completed').order_by(ScheduledTaskRun.completed_at.desc()).first()
    baseline=json.loads(previous.data_json).get('manifest',{}) if previous else {}
    manifest={}; changed=[]; sources=set(); missing=[]
    for path in paths:
        if path.is_symlink(): raise ValueError('Symlink collection requires explicit review.')
        plan=build_scan_scope(str(root),'shared_collection',collection_relpath=path.name)
        if not plan.complete: raise ValueError(plan.error or 'Incomplete collection scope; existing database preserved.')
        sources.update(plan.seen_source_relpaths)
        value=signature(path); manifest[path.name]=value
        marker_missing=any(not (root/source/'.scanned').is_file() or (root/source/'.update').exists() or
            (root/source/'.scanned.pending').exists() for source in plan.seen_source_relpaths)
        indexed={p.source_relpath for p in Product.query.filter(Product.source_relpath.in_(plan.seen_source_relpaths)).all()}
        if plan.seen_source_relpaths and (baseline.get(path.name)!=value or marker_missing or indexed!=set(plan.seen_source_relpaths)):
            changed.append(path.name)
    missing=[p.source_relpath for p in Product.query.filter_by(catalogue_status='active').all() if p.source_relpath not in sources]
    return changed,manifest,missing


def run_slice(key,run_id,owner):
    from app import scheduled_tasks as tasks
    from app.utils.scan_runner import start_scan
    from app.utils.operation_control import CatalogueOperationActive
    from flask import current_app
    run=db.session.get(ScheduledTaskRun,run_id); data=json.loads(run.data_json)
    try:
        tasks.renew(key,run_id,owner)
        if 'queue' not in data:
            data['queue'],data['manifest'],data['missing_sources']=selection()
            data['counts']['considered']=len(data['manifest'])
            data['counts']['skipped_recent']=len(data['manifest'])-len(data['queue'])
            tasks.save_progress(key,run_id,owner,data)
        if data.get('scan_operation'):
            op=db.session.get(CatalogueOperation,data['scan_operation'])
            if op and op.status=='running':
                return tasks.save_progress(key,run_id,owner,data,pause=True)
            summary=json.loads(op.scope).get('operation_summary',{}) if op else {}
            if op and op.status in {'succeeded','partial'} and not op.products_failed and op.recovery_state=='none' and not summary.get('errors') and op.products_succeeded>0:
                data['counts']['checked']+=1
                data['items'].append({'sku':data['queue'].pop(0),'state':'Local projection completed','operation_id':op.id})
            elif op and op.status=='interrupted':
                # Marker/database journal + stable index are reused, never reset.
                data['reason']='Interrupted local scan will resume with retained identities.'
            else:
                raise ValueError('Startup scan failed or requires marker/database recovery. Inspect catalogue operation '+str(data['scan_operation']))
            data.pop('scan_operation',None)
        if not data['queue']:
            data['reason']='Missing source folders retained without deletion; inspect catalogue mounts.' if data['missing_sources'] else 'Local catalogue current; no Shopify writes.'
            return tasks.save_progress(key,run_id,owner,data,terminal='partially_completed' if data['missing_sources'] else 'completed',
                error=data['reason'] if data['missing_sources'] else None)
        operation=start_scan(current_app._get_current_object(),uuid.uuid4().hex,scan_mode='startup',
            collection_relpath=data['queue'][0],scope={'scan_mode':'startup','collection_relpath':data['queue'][0],'trigger':'startup'})
        data['scan_operation']=operation
        tasks.save_progress(key,run_id,owner,data,pause=True)
    except CatalogueOperationActive:
        data['reason']='Waiting for the active catalogue operation; no concurrent local scan.'
        tasks.save_progress(key,run_id,owner,data,pause=True)
    except Exception as exc:
        from app.shopify_capacity import BackgroundYield
        if isinstance(exc,BackgroundYield):
            db.session.rollback()
            return  # expired workers cannot finalize a newer owner's run
        # A malformed/oversized manifest must still leave a bounded durable
        # failure, not repeatedly fail to encode the very error being retained.
        try: tasks.encoded(data)
        except ValueError:
            data={'version':1,'counts':{k:0 for k in tasks.COUNTERS},'items':[],
                'reason':'Startup source checkpoint exceeds its 64 KiB bound; use scoped scanner operations.'}
        tasks.save_progress(key,run_id,owner,data,terminal='failed',error=exc)
