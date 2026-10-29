"""Existing offline upgrade smoke + restart after a durable package acknowledgement."""
import inspect
import subprocess
import sys
import docker_manual_worker_smoke as previous
from test_packaging import package

smoke=previous.smoke
old_mock=previous.MOCK
setup=old_mock.split("with app.app_context():\n product=Product.query.filter_by(source_relpath='Worker Fixture/Manual Card')",1)[0]
SCENARIO='''
from app import shopify_package_assignments as packages,package_links
from app.models import ShopifyPublishJob
from app.packaging_catalogue import save as save_package
from unittest.mock import patch
class PackageAdapter(AcceptanceAdapter):
 def test_connection(self,config):
  result=super().test_connection(config);result['scopes'].append('write_inventory');return result
 def assign_package(self,config,inventory_id,package_id):
  durable=ShopifyProductIdentity.query.filter_by(sku=fixture_sku).one()
  proof=drafts.evidence(durable)['package_assignments']['product']
  assert proof['state']=='pending' and proof['inventory_id']==inventory_id
  path=Path('/app/instance/package-write-count')
  assert not path.exists(),'Package assignment was replayed'
  path.write_text('1')
  return {'inventoryItemUpdate':{'inventoryItem':{'id':inventory_id},'userErrors':[]}}
state_file=Path('/app/instance/package-correction-adapter.json')
with app.app_context():
 from app.utils.scanner import scan_collection
 from app.utils.ingest import ingest_rows_to_db
 folder=Path('/catalogue/Worker Fixture/Manual Card');shared=folder.parent/'product_info.json'
 product=Product.query.filter_by(source_relpath='Worker Fixture/Manual Card').first()
 if product is None:
  folder.mkdir(parents=True)
  shared.write_text(json.dumps({'sku_prefix':'PACKAGE-DOCKER-','collection_type':'Simple','price':'3.50','live':False,'variation_attributes':[],'attributes':{},'weight':'50'}))
  Image.new('RGB',(8,8),'red').save(folder/'original.png')
  rows=scan_collection(folder.parent,'https://invalid.example/','/output',log=lambda *a,**k:None)
  assert ingest_rows_to_db(rows,log=lambda *a,**k:None)['products_failed']==0
  product=Product.query.filter_by(source_relpath='Worker Fixture/Manual Card').one()
  db.session.add(ShopifyCollectionIdentity(store_id='gid://shopify/Shop/42',source_key='collection:Worker Fixture',remote_id='gid://shopify/Collection/800',state='verified',evidence_json='{}'));db.session.commit()
 fixture_sku=product.sku;adapter=PackageAdapter()
 if state_file.exists():adapter.__dict__.update(json.loads(state_file.read_text()))
 if not state_file.exists():
  _,token=drafts.preview(product,adapter);row=drafts.confirm(token,True,adapter)
  old=db.session.get(CatalogueOperation,row.operation_id)
  old.status='partial';old.recovery_state='review_required';old.error='Synthetic missing package acknowledgement'
  data=drafts.evidence(row);data['verification']['operation_id']=old.id;drafts.save(row,data)
  Path('/app/instance/package-old-operation').write_text(old.id)
  Path('/app/instance/package-old-receipt').write_text(old.scope)
  pkg=save_package(PACKAGE_SPEC)
  package_links.set_id(pkg,'gid://shopify/ShippingPackage/123',[])
  raw=json.loads(shared.read_text());raw['packaging']={'selected_package_id':pkg.id};shared.write_text(json.dumps(raw))
  rows=scan_collection(folder.parent,'https://invalid.example/','/output',log=lambda *a,**k:None)
  assert ingest_rows_to_db(rows,log=lambda *a,**k:None)['products_failed']==0
  from app.automatic_publishing import queue_product
  queue_product(product)
  job=db.session.get(ShopifyPublishJob,product.id)
  job.state='recovery_required';job.operation_id=old.id;job.scan_operation_id='completed-offline-source-scan';db.session.commit()
  reviewed,token=packages.correction_review(product,adapter)
  from flask_login import login_user
  with app.test_request_context():
   login_user(db.session.get(User,1));accepted=manual.accept(product.id,token,True)
  Path('/app/instance/package-new-operation').write_text(accepted.id)
  with patch.object(packages,'finish_correction',side_effect=SystemExit('Simulated termination after durable acknowledgement')):
   claimed=tasks.claim('package-before-restart',manual_only=True);assert claimed and claimed[0]==manual.KEY
   try:tasks.run_slice(*claimed,'package-before-restart',adapter=adapter)
   except SystemExit:pass
  row=ShopifyProductIdentity.query.filter_by(sku=fixture_sku).one()
  assert drafts.evidence(row)['package_assignments']['product']['state']=='accepted'
  task=db.session.get(ScheduledTask,manual.KEY);task.lease_until=tasks.now()-timedelta(seconds=1)
  task.resume_at=tasks.now()+timedelta(days=1);db.session.commit()
  state_file.write_text(json.dumps(adapter.__dict__))
 else:
  task=db.session.get(ScheduledTask,manual.KEY);task.resume_at=tasks.now();db.session.commit()
  claimed=tasks.claim('package-after-restart',manual_only=True);assert claimed and claimed[0]==manual.KEY,repr(claimed)
  tasks.run_slice(*claimed,'package-after-restart',adapter=adapter)
  op=db.session.get(CatalogueOperation,Path('/app/instance/package-new-operation').read_text())
  old=db.session.get(CatalogueOperation,Path('/app/instance/package-old-operation').read_text())
  assert op.status=='succeeded' and old.scope==Path('/app/instance/package-old-receipt').read_text()
  assert old.recovery_state=='review_required' and old.id in packages.superseded_operations([old,op])
  row=ShopifyProductIdentity.query.filter_by(sku=fixture_sku).one()
  assert row.state=='verified' and adapter.calls.count('product_create')==1
  from app.shopify_notifications import product_result
  assert product_result(op)['outcome']=='shopify_accepted'
  Path('/app/instance/package-correction-completed').write_text('accepted-without-replay')
 assert Path('/app/instance/package-write-count').read_text()=='1'
'''.replace('PACKAGE_SPEC',repr(dict(package(),name='Offline correction mailer')))
previous.MOCK=setup+'\n'+SCENARIO
smoke.IMPORT=smoke.IMPORT.replace(old_mock,previous.MOCK)
smoke.CHECK=smoke.CHECK.split("\nwith sqlite3.connect('/app/instance/site.db') as connection:\n op_id=Path('/app/instance/manual-operation-id')",1)[0]
smoke.CHECK=smoke.CHECK.replace("select count(*) from packaging_package').fetchone()[0]==6", "select count(*) from packaging_package').fetchone()[0]==7")
smoke.CHECK=smoke.CHECK.replace('len(packages)==6 and len({p[0] for p in packages})==6','len(packages)==7 and len({p[0] for p in packages})==7')
smoke.CHECK=smoke.CHECK.replace("'packages':6", "'packages':7")
smoke.CHECK+='''
assert Path('/app/instance/package-write-count').read_text()=='1'
with sqlite3.connect('/app/instance/site.db') as connection:
 op=connection.execute('select status from catalogue_operation where id=?',(Path('/app/instance/package-new-operation').read_text(),)).fetchone()
 assert op[0]==('succeeded' if Path('/app/instance/package-correction-completed').exists() else 'running')
'''
if __name__=='__main__':
 try:
  smoke.main(sys.argv[1])
  print('PASS: package acknowledgement retained across container restart; one assignment; unchanged original Recovery Required receipt; local finalisation only.')
 except subprocess.CalledProcessError as exc:
  print(exc.stderr,file=sys.stderr);raise SystemExit(exc.returncode)
