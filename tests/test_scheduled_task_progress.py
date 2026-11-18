"""Run observation only: isolated SQLite, no worker or Shopify execution."""
import json

import pytest
from sqlalchemy import event

from app import db, scheduled_tasks as tasks
from app.models import ScheduledTask, ScheduledTaskRun
from app.scheduled_task_routes import KEYS
from test_phase3_woo_publish_preview import preview_app, _client


def observed_run(key, state='running', trigger='manual'):
    run=ScheduledTaskRun(id='a'*32,task_key=key,trigger=trigger,state=state,
        created_at=tasks.now(),started_at=tasks.now(),data_json=json.dumps({
            'counts':{'checked':1,'deferred':0},'queue':[2],
            'reason':'First saved checkpoint','items':[]}))
    db.session.add(run);db.session.commit()
    return run


@pytest.mark.parametrize('key',KEYS)
def test_manual_list_and_settings_starts_follow_one_existing_run(preview_app,key):
    with preview_app.app_context():
        client=_client(preview_app)
        response=client.post(f'/settings/tasks/{key}/run')
        assert response.status_code==302
        run_id=response.location.rsplit('/',1)[-1]
        accepted=client.post(f'/settings/tasks/{key}/run',json={})
        assert accepted.status_code==202 and accepted.json['run_id']==run_id
        assert accepted.json['history_url']==response.location
        html=client.get(response.location).get_data(as_text=True)
        assert f'data-run-id="{run_id}"' in html and 'scheduled-task-run.js' in html
        assert 'data-terminal="false"' in html
        assert ScheduledTaskRun.query.filter_by(task_key=key).count()==1


@pytest.mark.parametrize('trigger',['manual','scheduled'])
@pytest.mark.parametrize('state,terminal',[
    ('queued',False),('running',False),('paused',False),('completed',True),
    ('partially_completed',True),('failed',True),('interrupted',True),('cancelled',True)])
def test_same_persisted_run_endpoint_for_manual_and_automatic(preview_app,trigger,state,terminal):
    with preview_app.app_context():
        run=observed_run(tasks.KEY,state,trigger)
        response=_client(preview_app).get(f'/settings/tasks/runs/{run.id}',headers={'Accept':'application/json'})
        assert response.status_code==200 and response.headers['Cache-Control']=='no-store'
        value=response.json
        assert value['id']==run.id and value['state']==state and value['terminal'] is terminal
        assert 'First saved checkpoint' in value['report_html']
        assert trigger.title() in value['report_html']
        if state=='paused':assert 'Deferred' in value['phase']


def test_multiple_viewers_observe_checkpoints_and_final_readiness_without_any_writes(preview_app,monkeypatch):
    with preview_app.app_context():
        run=observed_run(tasks.KEY)
        clients=[_client(preview_app),_client(preview_app)]
        def forbidden(*a,**k):pytest.fail('Progress polling attempted execution or remote work')
        for name in ('enqueue','claim','renew','fence','save_progress','activity'):
            monkeypatch.setattr(tasks,name,forbidden)
        monkeypatch.setattr('requests.sessions.Session.request',forbidden)
        monkeypatch.setattr('app.catalogue_views.capture',forbidden)
        writes=[]
        def sql(conn,cursor,statement,parameters,context,executemany):
            if statement.lstrip().split()[0].upper() in {'INSERT','UPDATE','DELETE','REPLACE','CREATE','ALTER','DROP'}:
                writes.append(statement)
        event.listen(db.engine,'before_cursor_execute',sql)
        try:
            for checked in (1,2,3):
                # Only the synthetic worker fixture advances persisted evidence.
                event.remove(db.engine,'before_cursor_execute',sql)
                run.data_json=json.dumps({'counts':{'checked':checked,'attention':1},'queue':[],
                    'items':[{'sku':'FIXTURE','state':'Publishing prerequisite needed',
                        'reason':'Artwork requires merchant review'}]})
                if checked==3:
                    run.state='completed';run.completed_at=tasks.now();run.duration_seconds=4.2
                db.session.commit()
                saved=(run.data_json,run.state,run.completed_at)
                event.listen(db.engine,'before_cursor_execute',sql)
                for client in clients:
                    value=client.get(f'/settings/tasks/runs/{run.id}',headers={'Accept':'application/json'}).json
                    assert value['counts']['checked']==checked
                    assert value['terminal'] is (checked==3)
                    assert 'Artwork requires merchant review' in value['report_html']
                    assert (run.data_json,run.state,run.completed_at)==saved
                    if checked==3:
                        assert value['status_label']=='Completed with findings'
                        assert '4.2 s' in value['report_html']
            assert not writes
        finally:event.remove(db.engine,'before_cursor_execute',sql)


def test_partial_error_escaping_and_authorization(preview_app):
    with preview_app.app_context():
        run=observed_run(tasks.KEY,'partially_completed')
        run.error='<script>alert("not executable")</script>'
        db.session.commit()
        url=f'/settings/tasks/runs/{run.id}'
        assert preview_app.test_client().get(url,headers={'Accept':'application/json'}).status_code==401
        from flask import g
        g.pop('_login_user',None)  # isolated fixture holds an app context across clients
        value=_client(preview_app).get(url,headers={'Accept':'application/json'}).json
        assert value['terminal'] and value['status_label']=='Partially Completed'
        assert '&lt;script&gt;' in value['report_html'] and '<script>' not in value['report_html']
        assert value['error']==run.error


def test_terminal_html_and_history_list_do_not_start_polling(preview_app):
    with preview_app.app_context():
        run=observed_run(tasks.KEY,'completed')
        client=_client(preview_app)
        html=client.get(f'/settings/tasks/runs/{run.id}').get_data(as_text=True)
        assert 'data-terminal="true"' in html
        history=client.get(f'/settings/tasks/{tasks.KEY}/history').get_data(as_text=True)
        assert 'data-task-run-view' not in history and 'scheduled-task-run.js' not in history


def test_progress_remains_administrator_only(preview_app):
    with preview_app.app_context():
        from app.models import User
        run=observed_run(tasks.KEY)
        user=db.session.get(User,1);user.is_admin=False;db.session.commit()
        assert _client(preview_app).get(f'/settings/tasks/runs/{run.id}',headers={'Accept':'application/json'}).status_code==403
