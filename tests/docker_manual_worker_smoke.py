"""Existing offline data checks plus real manual worker resume across container restart.

Mock adapter source is reused from tests, not installed in or shipped by the image.
Only this disposable container's scheduler is paused while injecting mock transport.
"""
import inspect,subprocess,sys
import docker_gallery_bytes_smoke as previous
from test_shopify_drafts import FakeDraftAdapter
from test_shopify_simple_updates import UpdateAdapter
from test_shopify_publication_sales import SafetyMixin,AcceptanceAdapter

smoke=previous.smoke
smoke.SEED_OLD=smoke.SEED_OLD.replace('0019_automatic_publishing','0020_operations_acceptance')
smoke.CHECK=smoke.CHECK.replace("=='0020_operations_acceptance'","=='0021_manual_review_worker'").replace('*0019*0020*','*0020*0021*')
smoke.CHECK=smoke.CHECK.replace("'migration':'0020_operations_acceptance'","'migration':'0021_manual_review_worker'")
smoke.CHECK=smoke.CHECK.replace("if key not in {'Fictional/Smoke','Fictional/Readable'}", "if key not in {'Fictional/Smoke','Fictional/Readable','Worker Fixture/Manual Card'}")

CLASSES='\n'.join(inspect.getsource(c) for c in (FakeDraftAdapter,UpdateAdapter,SafetyMixin,AcceptanceAdapter))
CLASSES=CLASSES.replace('ShopifyProductIdentity.query.one()',"ShopifyProductIdentity.query.filter_by(sku=fixture_sku).one()")
MOCK='''
import os,json,copy,hashlib
from pathlib import Path
from datetime import timedelta
from types import SimpleNamespace
from sqlalchemy import text
from PIL import Image
from app import create_app,db,shopify_drafts as drafts,scheduled_tasks as tasks,manual_publishing as manual
from app.models import Product,ShopifyProductIdentity,CatalogueOperation,ScheduledTask,User,ShopifyCollectionIdentity
from app.shopify_connection import ShopifyError
os.environ['SHOPIFY_STORE_DOMAIN']='fictional.myshopify.com'
os.environ['SHOPIFY_ADMIN_ACCESS_TOKEN']='shpat_'+'x'*32
app=create_app(recover=False)
'''+CLASSES+'''
class LedgerAdapter(AcceptanceAdapter):
 def upload_image(self,config,target,image,content):
  assert image['source'] not in getattr(self,'sources',[])
  assert len(content)==image['size'] and hashlib.sha256(content).hexdigest()==image['sha256']
  super().upload_image(config,target,image,content)
  self.sources=getattr(self,'sources',[])+[image['source']]
state_file=Path('/app/instance/manual-mock-state.json')
with app.app_context():
 product=Product.query.filter_by(source_relpath='Worker Fixture/Manual Card').first()
 if product is None:
  from app.utils.scanner import scan_collection
  from app.utils.ingest import ingest_rows_to_db
  folder=Path('/catalogue/Worker Fixture/Manual Card');folder.mkdir(parents=True)
  (folder.parent/'product_info.json').write_text(json.dumps({'sku_prefix':'MANUAL-DOCKER-','collection_type':'Simple','price':'3.50','live':False,'variation_attributes':[],'attributes':{},'weight':'50'}))
  for i in range(7):Image.new('RGB',(1280,1280),'red').save(folder/f'{i}.png','PNG',compress_level=0)
  rows=scan_collection(folder.parent,'https://invalid.example/','/output',log=lambda *a,**k:None)
  assert ingest_rows_to_db(rows,log=lambda *a,**k:None)['products_failed']==0
  product=Product.query.filter_by(source_relpath='Worker Fixture/Manual Card').one()
  db.session.add(ShopifyCollectionIdentity(store_id='gid://shopify/Shop/42',source_key='collection:Worker Fixture',remote_id='gid://shopify/Collection/800',state='verified',evidence_json='{}'));db.session.commit()
 fixture_sku=product.sku
 adapter=LedgerAdapter()
 if state_file.exists():adapter.__dict__.update(json.loads(state_file.read_text()))
 if not state_file.exists():
  _,token=drafts.preview(product,adapter)
  from flask_login import login_user
  with app.test_request_context():
   login_user(db.session.get(User,1));accepted=manual.accept(product.id,token,True)
  Path('/app/instance/manual-operation-id').write_text(accepted.id)
 task=db.session.get(ScheduledTask,manual.KEY);task.resume_at=tasks.now();db.session.commit()
 claimed=tasks.claim('offline-mock-owner');assert claimed and claimed[0]==manual.KEY
 tasks.run_slice(*claimed,'offline-mock-owner',adapter=adapter)
 task=db.session.get(ScheduledTask,manual.KEY)
 if task.active_run_id:task.resume_at=tasks.now()+timedelta(days=1);db.session.commit()
 state_file.write_text(json.dumps(adapter.__dict__))
 op=db.session.get(CatalogueOperation,Path('/app/instance/manual-operation-id').read_text())
 if len(adapter.sources)==6:
  assert op.status=='pending' and adapter.calls.count('product_create')==0
 else:
  assert len(adapter.sources)==7 and adapter.calls.count('product_create')==1
  assert op.status=='succeeded',op.error
  Path('/app/instance/manual-worker-completed').write_text('verified-after-container-restart')
'''

smoke.IMPORT+='\n'+MOCK
smoke.CHECK+='''
with sqlite3.connect('/app/instance/site.db') as connection:
 op_id=Path('/app/instance/manual-operation-id').read_text()
 op=connection.execute('select status,scope from catalogue_operation where id=?',(op_id,)).fetchone()
 proof=json.loads(op[1]);assert proof['manual_review']['state'] in ('waiting','completed')
 assert proof['live_state']['publishing']['uploads_acknowledged'] in (6,7)
 assert len(proof['live_state']['logs'])<100
 if Path('/app/instance/manual-worker-completed').exists():assert op[0]=='succeeded'
'''
original=smoke.docker


def scheduler_signal(container, signal):
    code="from pathlib import Path;import os,signal; children=Path('/proc/1/task/1/children').read_text().split(); pid=next(p for p in children if 'scheduler_worker' in Path('/proc/'+p+'/cmdline').read_text());os.kill(int(pid),signal."+signal+")"
    original('exec',container,'python','-c',code)


def docker(*args,**kwargs):
    if args and args[0]=='restart':scheduler_signal(args[1],'SIGCONT')
    result=original(*args,**kwargs)
    if args and (args[0]=='restart' or args[0]=='run' and '-d' in args):
        container=args[1] if args[0]=='restart' else result
        smoke.wait_ready(container);scheduler_signal(container,'SIGSTOP')
        if args[0]=='restart':
            original('exec',container,'python','-c',MOCK)
            scheduler_signal(container,'SIGCONT')
    return result


smoke.docker=docker
if __name__=='__main__':
    try:
        smoke.main(sys.argv[1])
        print('manual worker: accepted once; 6 original uploads before container restart, 1 after; one verified mocked Create')
    except subprocess.CalledProcessError as exc:
        print(exc.stderr,file=sys.stderr);raise SystemExit(exc.returncode)
