"""Existing network-isolated two-worker upgrade/restart plus alert observations."""
import sys
import docker_operations_health_smoke as previous

smoke = previous.smoke
smoke.IMPORT += '''
with app.app_context():
 db.session.add(CatalogueOperation(id='refresh-action-observation',operation_type='shopify_status_refresh',
  status='succeeded',recovery_state='none',started_at=now(),finished_at=now(),
  scope=json.dumps({'action_observations':{'fixture:product:1':'unchanged-condition'},
   'operation_summary':{'execution_complete':True,'findings':[{'code':'publication_configuration'}]}})))
 db.session.commit()
'''
smoke.CHECK += '''
with sqlite3.connect('/app/instance/site.db') as connection:
 row=connection.execute("select status,scope from catalogue_operation where id='refresh-action-observation'").fetchone()
 assert row[0]=='succeeded'
 assert json.loads(row[1])['action_observations']=={'fixture:product:1':'unchanged-condition'}
 assert connection.execute("select status,recovery_state from catalogue_operation where id='reporting-retained-recovery'").fetchone()==('partial','review_required')
assert probe['dashboard']['local_catalogue']['label'] in {'Healthy','Needs attention'}
assert 'active_issue_counts' in probe['dashboard']
assert 'incident_evidence' in probe and 'historical_partial_refresh_evidence_groups' in probe
'''

if __name__ == '__main__':
    smoke.main(sys.argv[1])
