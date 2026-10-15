"""Retire the M3A.9.3.3 automatic local scan; retain historical evidence.

No source traversal, scanning, ingestion or notifications. Interrupted catalogue
operations/markers remain with existing explicit recovery. Live task owners keep
their lease until it expires; new workers never execute this retired task type.
"""
import json

from sqlalchemy import update, or_
from app import db
from app.models import ScheduledTask, ScheduledTaskRun

KEY='catalogue_startup'
DEFINITION={'name':'Retired Startup Catalogue Reconciliation',
    'description':'Historical automatic local-scan records only. Startup now queues read-only Shopify Products reconciliation.'}


def retire():
    from app import scheduled_tasks as tasks
    task=db.session.get(ScheduledTask,KEY)
    if not task: return
    stamp=tasks.now()
    run_id=task.active_run_id
    if not run_id and not task.enabled: return
    db.session.execute(update(ScheduledTask).where(ScheduledTask.key==KEY).values(enabled=False))
    if not run_id:
        db.session.commit()
        return
    # Never alter a live owner, scanner operation, recovery evidence or files.
    changed=db.session.execute(update(ScheduledTask).where(ScheduledTask.key==KEY,
        ScheduledTask.active_run_id==run_id,
        or_(ScheduledTask.owner.is_(None),ScheduledTask.lease_until.is_(None),ScheduledTask.lease_until<=stamp)
    ).values(state='disabled',last_outcome='cancelled',last_completed=stamp,
        active_run_id=None,owner=None,lease_until=None,resume_at=None)).rowcount
    if changed:
        run=db.session.get(ScheduledTaskRun,run_id)
        if run and not run.completed_at:
            try:
                data=json.loads(run.data_json)
                data['retirement']='Automatic local startup scanning removed in M3A.9.3.4. Pending operations/markers were not completed or replayed; use explicit recovery.'
                run.data_json=tasks.encoded(data)
            except (ValueError,TypeError): pass  # retain original/corrupt/fully bounded evidence
            run.state='cancelled';run.completed_at=stamp
            run.duration_seconds=max(0,(stamp-(run.started_at or run.created_at)).total_seconds())
    db.session.commit()
