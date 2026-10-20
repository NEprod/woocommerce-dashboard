"""Existing offline migration/restart chain plus persistent reporting evidence."""
import sys
import docker_variant_sync_smoke as previous

smoke = previous.smoke
smoke.IMPORT += '''
from app.models import CatalogueOperation
from app.shopify_drafts import now
with app.app_context():
 db.session.add(CatalogueOperation(id='reporting-retained-recovery',operation_type='shopify_draft_publish',
  status='partial',recovery_state='review_required',error='Synthetic uncertain mutation',started_at=now()))
 db.session.add(CatalogueOperation(id='reporting-complete-findings',operation_type='append',
  status='succeeded',recovery_state='none',started_at=now(),
  scope=json.dumps({'operation_summary':{'execution_complete':True,'information':1}})))
 db.session.commit()
'''
smoke.CHECK += '''
dashboard=opener.open('http://127.0.0.1:7485/',timeout=5).read().decode()
assert 'Local Catalogue Health' in dashboard and 'Operations Health' in dashboard
assert 'Shopify Synchronisation' in dashboard
assert '/operations/issues?category=recovery' in dashboard
issues=opener.open('http://127.0.0.1:7485/operations/issues?category=recovery',timeout=5).read().decode()
assert 'reporting-retained-recovery' in issues and 'Synthetic uncertain mutation' in issues
receipt=opener.open('http://127.0.0.1:7485/operations/reporting-complete-findings',timeout=5).read().decode()
assert 'Completed with findings' in receipt
with sqlite3.connect('/app/instance/site.db') as connection:
 assert connection.execute("select status,recovery_state from catalogue_operation where id='reporting-retained-recovery'").fetchone()==('partial','review_required')
import subprocess,sys
probe=json.loads(subprocess.check_output([sys.executable,'-m','app.operations_probe'],text=True))
assert probe['database_writes']==0 and probe['shopify_requests']==0
assert any(row['id']=='reporting-retained-recovery' for row in probe['retained_recovery_cases'])
'''

if __name__ == '__main__':
    smoke.main(sys.argv[1])
