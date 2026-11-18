"""Loopback-only live-progress UI fixture; no worker, Shopify or real data."""
import json
import os
import sys
import tempfile
from pathlib import Path

repo=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(repo),str(repo/'tests')]
for key in list(os.environ):
    if key.startswith(('SHOPIFY_','TLC_DEPLOYMENT_')):os.environ.pop(key)
os.environ['DISCORD_ENABLED']='false'
import pytest
import requests
from flask import abort,request,session
from app import db,scheduled_tasks as tasks
from app.models import ScheduledTaskRun,ScheduledTask
from test_phase3_woo_publish_preview import preview_app

patch=pytest.MonkeyPatch()
fixture=preview_app.__wrapped__(Path(tempfile.mkdtemp(prefix='tlc-task-progress-browser-')),patch)
app=next(fixture)
requests.sessions.Session.request=lambda *a,**k: (_ for _ in ()).throw(AssertionError('Outbound request forbidden'))


@app.before_request
def local_only():
    if request.remote_addr not in {'127.0.0.1','::1'}:abort(403)
    session['_user_id']='1';session['_fresh']=True


@app.post('/fixture/progress/<run_id>')
def advance(run_id):
    run=db.get_or_404(ScheduledTaskRun,run_id)
    value=request.get_json()
    run.state=value['state']
    run.started_at=run.started_at or tasks.now()
    run.data_json=json.dumps({'counts':{'checked':value['checked'],'attention':1},'queue':[],
        'reason':'Synthetic saved checkpoint','items':[{'sku':f'FIXTURE-{i}',
            'state':'Publishing prerequisite needed','reason':'Synthetic artwork finding'} for i in range(30)]})
    if run.state in {'completed','partially_completed','failed'}:
        run.completed_at=tasks.now();run.duration_seconds=12.3
        task=db.session.get(ScheduledTask,run.task_key)
        task.active_run_id=task.owner=task.lease_until=None
        task.state=run.state;task.last_outcome=run.state;task.last_completed=run.completed_at
    db.session.commit()
    return {'saved':True}


@app.get('/fixture/count')
def count():return {'runs':ScheduledTaskRun.query.count()}


if __name__=='__main__':app.run(host='127.0.0.1',port=7521,use_reloader=False)
