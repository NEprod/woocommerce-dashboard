"""Existing offline restart scenario plus a persisted, explicitly validated legacy source."""
import sys
import subprocess
import docker_verification_completion_smoke as previous

smoke = previous.smoke
legacy = '''
from app.publishing_checkpoint import validate
from app.utils.scanner import scan_collection
from app.utils.ingest import ingest_rows_to_db
from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation
from app.models import ShopifyPublishJob
with app.app_context():
 folder=Path('/catalogue/Legacy Checkpoint/Retained Card');folder.mkdir(parents=True,exist_ok=True)
 parent=folder.parent/'product_info.json'
 parent.write_text(json.dumps({'collection_type':'Simple','sku_prefix':'LEGACY-DOCKER-',
  'price':'3.50','live':True,'variation_attributes':[]}))
 Image.new('RGB',(5,5),'red').save(folder/'original.png')
 lease=acquire_catalogue_operation('append',{'fixture_only':True})
 rows=scan_collection(folder.parent,'https://invalid.example/','/output',log=lambda *a,**k:None)
 assert ingest_rows_to_db(rows,operation_id=lease.id,log=lambda *a,**k:None)['products_failed']==0
 finish_catalogue_operation(lease.id,status='succeeded')
 legacy_product=Product.query.filter_by(source_relpath='Legacy Checkpoint/Retained Card').one()
 job=db.session.get(ShopifyPublishJob,legacy_product.id)
 job.state='attention';job.requested_revision='unscanned';job.trigger='bootstrap'
 job.scan_operation_id=None;job.operation_id=None;db.session.commit()
 original={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.parent.rglob('*') if p.is_file()}
 validation=validate(legacy_product,actor_id=1,acknowledged=True)
 assert original=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.parent.rglob('*') if p.is_file()}
 db.session.refresh(job)
 assert job.state=='pending' and job.scan_operation_id==lease.id
 Path('/app/instance/checkpoint-restart.json').write_text(json.dumps({
  'product_id':legacy_product.id,'revision':job.requested_revision,'scan':lease.id,'validation':validation,'files':original}))
'''
# Insert after mock imports/app setup but BEFORE accepting the independent
# gallery operation: its durable pending state correctly owns the global slot.
marker = 'with app.app_context():\n product=Product.query.filter_by(source_relpath=\'Variable Worker/Partial Wall Print\').first()'
assert marker in smoke.IMPORT
smoke.IMPORT = smoke.IMPORT.replace(marker, legacy + '\n' + marker, 1)
smoke.CHECK = smoke.CHECK.replace("'Variable Worker/Partial Wall Print'}", "'Variable Worker/Partial Wall Print','Legacy Checkpoint/Retained Card'}")
smoke.CHECK += '''
import hashlib
checkpoint=json.loads(Path('/app/instance/checkpoint-restart.json').read_text())
with sqlite3.connect('/app/instance/site.db') as connection:
 job=connection.execute('select state,requested_revision,scan_operation_id,view_json from shopify_publish_job where product_id=?',(checkpoint['product_id'],)).fetchone()
 assert job[:3]==('pending',checkpoint['revision'],checkpoint['scan'])
 assert json.loads(job[3])['checkpoint_validation']['validation_operation_id']==checkpoint['validation']
 assert connection.execute('select status from catalogue_operation where id=?',(checkpoint['validation'],)).fetchone()==('succeeded',)
 assert connection.execute('select status from catalogue_operation where id=?',(checkpoint['scan'],)).fetchone()==('succeeded',)
 assert connection.execute('select enabled from automatic_publishing').fetchone()==(0,)
for path,digest in checkpoint['files'].items():assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest
events=opener.open('http://127.0.0.1:7485/operations/events',timeout=5).read().decode()
assert 'Event History' in events
'''

if __name__ == '__main__':
    try:
        smoke.main(sys.argv[1])
        print('legacy checkpoint: explicit local-only validation, original scan, unchanged source bytes and Pending revision persist across restart')
    except subprocess.CalledProcessError as exc:
        print(exc.stderr, file=sys.stderr)
        raise SystemExit(exc.returncode)
