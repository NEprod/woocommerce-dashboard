"""Restart AFTER acknowledged variant writes, BEFORE conclusive verification."""
import inspect,sys,subprocess
import docker_variable_recovery_smoke as recovery
from test_variable_verification_completion import DelayedVariants

smoke=recovery.smoke
old_mock=recovery.previous.MOCK
setup=old_mock.split('\nfrom app import shopify_variables as variable\n',1)[0]
prefix=recovery.SCENARIO.split(' adapter=InterruptedBeforeVariants()',1)[0]
prefix=prefix.replace('with app.app_context():',inspect.getsource(DelayedVariants)+'\nwith app.app_context():',1)
SCENARIO=prefix+'''
 adapter=DelayedVariants();adapter.interrupt=False;adapter.delay=3
 original_id=Path('/app/instance/verification-original-operation')
 if not variable_state.exists():
  _,token=drafts.preview(product,adapter);assert token
  from flask_login import login_user
  with app.test_request_context():
   login_user(db.session.get(User,1));accepted=manual.accept(product.id,token,True)
  original_id.write_text(accepted.id)
 else:adapter.__dict__.update(json.loads(variable_state.read_text()))
 for _ in range(20):
  task=db.session.get(ScheduledTask,manual.KEY);task.resume_at=tasks.now();db.session.commit()
  claimed=tasks.claim('offline-verification-owner');assert claimed and claimed[0]==manual.KEY
  tasks.run_slice(*claimed,'offline-verification-owner',adapter=adapter)
  row=ShopifyProductIdentity.query.filter_by(sku=fixture_sku).one();data=drafts.evidence(row)
  if not variable_state.exists() and row.state=='processing' and data.get('verification_wait'):break
  if variable_state.exists() and row.state=='verified':break
 assert row.operation_id==original_id.read_text()
 assert len(data['variant_maps'])==len(adapter.remote['variants']['nodes'])==14
 assert len(data['upload_inputs'])==len(adapter.remote['media']['nodes'])==53
 assert adapter.calls.count('product_create')==1 and adapter.calls.count('binary_upload')==53
 assert adapter.calls.count('variants_create')==1 and adapter.calls.count('variants_update')==1
 op=db.session.get(CatalogueOperation,row.operation_id)
 if not variable_state.exists():
  assert row.state=='processing' and op.status=='pending' and op.products_failed==0
  task=db.session.get(ScheduledTask,manual.KEY);task.resume_at=tasks.now()+timedelta(days=1);db.session.commit()
  variable_state.write_text(json.dumps(adapter.__dict__))
  Path('/app/instance/verification-product-id').write_text(row.shopify_product_id)
 else:
  assert row.state=='verified' and op.status=='succeeded' and op.products_succeeded==1
  assert row.shopify_product_id==Path('/app/instance/verification-product-id').read_text()
  from app.models import ShopifyProductComparison
  result=json.loads(ShopifyProductComparison.query.filter_by(source_identity=product.source_relpath).one().summary_json)
  assert result['identity']==drafts.digest(row.media_evidence_json)
  assert result['classification']=='no_change' and result['read_complete']
  # Old pending queue work must settle from the saved catalogue projection,
  # without inspecting source images or replaying any acknowledged mutation.
  from app import automatic_publishing as publisher
  from unittest.mock import patch
  from app.catalogue_views import capture
  from app.models import ShopifyPublishJob
  job=db.session.get(ShopifyPublishJob,product.id)
  job.state='pending';job.verified_revision=None;capture(product);db.session.commit()
  calls=list(adapter.calls);comparison=ShopifyProductComparison.query.filter_by(source_identity=product.source_relpath).one()
  checkpoint=comparison.summary_json
  with patch.object(publisher,'local_revision',side_effect=AssertionError('No-op walked source')):
   assert publisher.publish_one(job,owner='offline-noop',guard=lambda:None,fence=lambda:None,adapter=adapter)=='matching'
  assert job.state=='verified' and adapter.calls==calls and comparison.summary_json==checkpoint
  Path('/app/instance/verification-completed').write_text('same-operation-read-only-no-reconcile')
'''

recovery.previous.MOCK=setup+SCENARIO
smoke.IMPORT=smoke.IMPORT.replace(old_mock,recovery.previous.MOCK)
smoke.CHECK=smoke.CHECK.split("\nwith sqlite3.connect('/app/instance/site.db') as connection:\n raw=connection.execute",1)[0]
smoke.CHECK+='''
with sqlite3.connect('/app/instance/site.db') as connection:
 row=connection.execute("select state,shopify_product_id,operation_id from shopify_product_identity where source_identity='Variable Worker/Partial Wall Print'").fetchone()
 assert row[1]==Path('/app/instance/verification-product-id').read_text()
 assert row[2]==Path('/app/instance/verification-original-operation').read_text()
 assert row[0]==('verified' if Path('/app/instance/verification-completed').exists() else 'processing')
'''
if __name__=='__main__':
 try:
  smoke.main(sys.argv[1])
  print('verification: 14 variants/53 images acknowledged before restart; same operation verifies afterward; no repeated Create/upload/variant write and no manual Reconcile')
 except subprocess.CalledProcessError as exc:
  print(exc.stderr,file=sys.stderr);raise SystemExit(exc.returncode)
