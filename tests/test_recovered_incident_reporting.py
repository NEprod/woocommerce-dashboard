"""Only explicit verified repair receipts resolve historical write incidents."""
import copy
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
import pytest
from app.models import CatalogueOperation
from app.operation_reporting import verified_recovery_links, operation_issues, publisher_run_resolved
from test_phase2_dashboard import dashboard_app


def receipts():
    stamp = datetime(2026, 11, 27, 12)
    old = CatalogueOperation(id='original',operation_type='shopify_draft_publish',status='partial',
        recovery_state='review_required',error='Managed Simple fields differ from the reviewed plan. Reconcile.',
        scope=json.dumps({'product_id':1,'operation_summary':{'identity_id':7}}),
        started_at=stamp,finished_at=stamp+timedelta(seconds=10))
    repaired = CatalogueOperation(id='repair',operation_type='shopify_draft_publish',status='succeeded',
        recovery_state='none',scope=json.dumps({'mode':'reviewed_seo_repair','identity_id':7,
            'original_operation':'original','operation_summary':{'mode':'reviewed_seo_repair','identity_id':7,'state':'verified'}}),
        started_at=stamp+timedelta(minutes=10),finished_at=stamp+timedelta(minutes=11))
    return old,repaired


def test_exact_verified_repair_removes_active_case_preserves_history(dashboard_app):
    app,_ = dashboard_app
    old,repaired = receipts()
    original = old.status,old.recovery_state,old.error,old.scope
    with app.test_request_context('/'):
        assert len(operation_issues([old])['recovery']) == 1
        result=operation_issues([old,repaired])
        assert not result['recovery'] and old.id in result['resolved']
        assert verified_recovery_links([old,repaired]) == {'original':'repair'}
    assert original == (old.status,old.recovery_state,old.error,old.scope)


@pytest.mark.parametrize('problem',['partial','wrong_identity','wrong_original','read_only','not_verified','earlier'])
def test_unproven_or_unrelated_repair_cannot_hide_recovery(problem):
    old,repaired = receipts()
    data=json.loads(repaired.scope)
    if problem=='partial': repaired.status='partial'
    if problem=='wrong_identity': data['identity_id']=9
    if problem=='wrong_original': data['original_operation']='different'
    if problem=='read_only': data['mode']='read_only_reconciliation'
    if problem=='not_verified': data['operation_summary']['state']='uncertain'
    if problem=='earlier': repaired.started_at=old.started_at-timedelta(seconds=1)
    repaired.scope=json.dumps(data)
    assert not verified_recovery_links([old,repaired])


@pytest.mark.parametrize('problem',[None,'unrecovered_target','worker_error','deferred','unknown_scope'])
def test_publisher_incident_requires_every_exact_failed_target(problem):
    old,repaired=receipts()
    data={'outcomes':{'1':'r'},'counts':{'attention':1,'considered':1,'checked':1,'deferred':0},
        'items':[{'id':1,'state':'recovery_required','operation_id':'original'}]}
    run=SimpleNamespace(task_key='shopify_publisher',state='partially_completed',error=None,data_json='')
    if problem=='unrecovered_target':
        data['outcomes']['2']='r';data['counts'].update(attention=2,considered=2,checked=2)
    if problem=='worker_error': run.error='Unattributed worker failure'
    if problem=='deferred': data['counts']['deferred']=1
    if problem=='unknown_scope': data['items']=[]
    run.data_json=json.dumps(data)
    assert publisher_run_resolved(run,[old,repaired],verified_recovery_links([old,repaired])) == (problem is None)


def test_original_detail_links_successful_repair_without_rewriting(dashboard_app):
    from app import db
    from app.operations_workspace import operation_detail_workspace
    app,_=dashboard_app
    with app.test_request_context('/'):
        old,repaired=receipts()
        db.session.add_all([old,repaired]);db.session.commit()
        view=operation_detail_workspace(old)['operation']
        assert view['resolved_by']=='repair' and view['status_label'].startswith('Resolved')
        assert view['original_status']=='partial' and not view['recoverable']
        assert view['resolved_error']==old.error and old.recovery_state=='review_required'
