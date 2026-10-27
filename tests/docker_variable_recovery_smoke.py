"""Offline restart of retained 14-variant/53-media Draft; no production mounts/API."""
import inspect,sys,subprocess
import docker_manual_worker_smoke as previous
from test_shopify_variables import VariableAdapter
from test_variable_creation_recovery import RealPagedVariable,InterruptedBeforeVariants

smoke=previous.smoke
classes='\n'.join(inspect.getsource(c) for c in (VariableAdapter,RealPagedVariable,InterruptedBeforeVariants))
classes=classes.replace('ShopifyProductIdentity.query.one()',"ShopifyProductIdentity.query.filter_by(sku=fixture_sku).one()")
classes=classes.replace('class RealPagedVariable(', 'class VariableAcceptanceAdapter(SafetyMixin,VariableAdapter):pass\n\nclass RealPagedVariable(')
SCENARIO='''
from app import shopify_variables as variable
from app.shopify_connection import ShopifyAdapter,VARIABLE_READ,GALLERY_QUERY
'''+classes+'''
with app.app_context():
 product=Product.query.filter_by(source_relpath='Variable Worker/Partial Wall Print').first()
 if product is None:
  from app.utils.scanner import scan_collection
  from app.utils.ingest import ingest_rows_to_db
  folder=Path('/catalogue/Variable Worker/Partial Wall Print');folder.mkdir(parents=True)
  (folder.parent/'product_info.json').write_text(json.dumps({'sku_prefix':'VAR-DOCKER-','collection_type':'Variable Collection','price':'10.00','weight':50,'live':False,
    'attributes':{'Colour':['Red','Blue'],'Size':['S'+str(i) for i in range(7)]},'variation_attributes':['Colour','Size'],
    'dimensions':{'length':155,'width':100,'height':3}}))
  for i in range(53):Image.new('RGB',(512,512),'red').save(folder/f'image-{i:03}.png','PNG',compress_level=0)
  rows=scan_collection(folder.parent,'https://invalid.example/','/output',log=lambda *a,**k:None)
  assert ingest_rows_to_db(rows,log=lambda *a,**k:None)['products_failed']==0
  product=Product.query.filter_by(source_relpath='Variable Worker/Partial Wall Print').one()
  db.session.add(ShopifyCollectionIdentity(store_id='gid://shopify/Shop/42',source_key='collection:Variable Worker',remote_id='gid://shopify/Collection/801',state='verified',evidence_json='{}'));db.session.commit()
 fixture_sku=product.sku
 variable_state=Path('/app/instance/variable-mock-state.json')
 adapter=InterruptedBeforeVariants()
 if not variable_state.exists():
  for _ in range(15):
   _,token=drafts.preview(product,adapter);assert token
   row=drafts.confirm(token,True,adapter)
   if not drafts.upload_pending(row):break
  assert row.state=='uncertain' and len(adapter.remote['variants']['nodes'])==1
  assert len(drafts.evidence(row)['upload_inputs'])==53 and len(drafts.evidence(row)['plan']['variants'])==14
  assert adapter.calls.count('product_create')==1 and adapter.calls.count('binary_upload')==53
  variable_state.write_text(json.dumps(adapter.__dict__))
  Path('/app/instance/variable-original-id').write_text(row.shopify_product_id)
 else:
  adapter.__dict__.update(json.loads(variable_state.read_text()));adapter.interrupt=False
  row=ShopifyProductIdentity.query.filter_by(sku=fixture_sku).one()
  _,token=variable.preview(product,adapter,resume_creation=True);assert token
  from flask_login import login_user
  with app.test_request_context():
   login_user(db.session.get(User,1));accepted=manual.accept(product.id,token,True)
  task=db.session.get(ScheduledTask,manual.KEY);task.resume_at=tasks.now();db.session.commit()
  claimed=tasks.claim('offline-variable-owner');assert claimed and claimed[0]==manual.KEY
  tasks.run_slice(*claimed,'offline-variable-owner',adapter=adapter)
  proof=drafts.evidence(row)
  assert row.state=='verified',proof.get('error')
  assert row.shopify_product_id==Path('/app/instance/variable-original-id').read_text()
  assert len(proof['variant_maps'])==len(adapter.remote['variants']['nodes'])==14
  assert len(adapter.remote['media']['nodes'])==53
  assert adapter.calls.count('binary_upload')==53 and adapter.calls.count('product_create')==1
  assert adapter.remote['status']=='DRAFT'
  assert db.session.get(CatalogueOperation,accepted.id).status=='succeeded'
  Path('/app/instance/variable-recovery-completed').write_text('retained-id-14-variants-53-media-no-duplicate')
'''

old_mock=previous.MOCK
# This scenario owns the global catalogue slot. Do not also leave the separate
# seven-image manual fixture pending; their mutual exclusion is intentional.
setup=old_mock.split("with app.app_context():\n product=Product.query.filter_by(source_relpath='Worker Fixture/Manual Card')",1)[0]
previous.MOCK=setup+'\n'+SCENARIO
smoke.IMPORT=smoke.IMPORT.replace(old_mock,previous.MOCK)
smoke.CHECK=smoke.CHECK.split("\nwith sqlite3.connect('/app/instance/site.db') as connection:\n op_id=Path('/app/instance/manual-operation-id')",1)[0]
smoke.CHECK=smoke.CHECK.replace("'Worker Fixture/Manual Card'}", "'Worker Fixture/Manual Card','Variable Worker/Partial Wall Print'}")
smoke.CHECK+='''
with sqlite3.connect('/app/instance/site.db') as connection:
 raw=connection.execute("select state,shopify_product_id,media_evidence_json from shopify_product_identity where source_identity='Variable Worker/Partial Wall Print'").fetchone()
 assert raw[1]==Path('/app/instance/variable-original-id').read_text()
 if Path('/app/instance/variable-recovery-completed').exists():assert raw[0]=='verified'
 else:assert raw[0]=='uncertain'
'''

if __name__=='__main__':
 try:
  smoke.main(sys.argv[1])
  print('variable recovery: original Draft/53 acknowledgements persisted across restart; fresh signed continuation; 14 verified variants; one ProductCreate, 53 uploads')
 except subprocess.CalledProcessError as exc:
  print(exc.stderr,file=sys.stderr);raise SystemExit(exc.returncode)
