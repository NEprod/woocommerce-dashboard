"""Offline restart between byte-bounded uploads; only disposable mounts/mock transport."""
import subprocess
import sys
import docker_operations_acceptance_smoke as previous

smoke = previous.smoke

UPLOAD = '''
import json,hashlib
from pathlib import Path
from flask import Flask
from config import Config
from app import db,shopify_drafts as drafts
from app.models import Product,ShopifyProductIdentity
from PIL import Image
view=Flask('offline-gallery-bytes')
view.config.from_object(Config)
view.config['SQLALCHEMY_DATABASE_URI']='sqlite:////app/instance/site.db'
db.init_app(view)
class Adapter:
 def stage_images(self,config,images):
  assert len(images)<=20 and sum(i['size'] for i in images)<=30*1024*1024
  return {'stagedUploadsCreate':{'stagedTargets':[{'resourceUrl':'https://storage.googleapis.com/'+i['filename']} for i in images],'userErrors':[]}}
 def upload_image(self,config,target,image,content):
  assert len(content)==image['size'] and hashlib.sha256(content).hexdigest()==image['sha256']
  path=Path('/app/instance/mock-upload-ledger.json')
  ledger=json.loads(path.read_text()) if path.exists() else []
  assert image['source'] not in ledger,'Acknowledged upload was replayed'
  path.write_text(json.dumps(ledger+[image['source']]))
with view.app_context():
 row=ShopifyProductIdentity.query.filter_by(sku='UPLOAD-OFFLINE').first()
 if row is None:
  product=Product(sku='UPLOAD-OFFLINE',title='Offline byte gallery',product_type='simple',source_relpath='Fictional/ByteGallery',catalogue_status='active')
  db.session.add(product);db.session.flush()
  folder=Path('/catalogue/Fictional/ByteGallery');folder.mkdir(parents=True)
  images=[]
  for index in range(5):
   name=f'image-{index}.png';path=folder/name
   Image.new('RGB',(1536,2048),'red').save(path,'PNG',compress_level=0)
   content=path.read_bytes()
   images.append({'source':'Fictional/ByteGallery/'+name,'filename':name,'sha256':hashlib.sha256(content).hexdigest(),'size':len(content),'alt':f'Art {index}'})
  assert sum(i['size'] for i in images)>30*1024*1024
  plan={'images':images};plan['digest']=drafts.digest(plan)
  row=ShopifyProductIdentity(product_id=product.id,source_identity=product.source_relpath,sku=product.sku,
   store_key='offline-mock',store_id='gid://shopify/Shop/999',store_domain='fictional.myshopify.com',
   state='pending',verification_state='unverified',reviewed_digest=plan['digest'],media_evidence_json='{}')
  db.session.add(row);db.session.flush()
  data={'version':1,'plan':plan,'create_attempted':False,'media':[]}
 else:
  assert drafts.upload_pending(row)
  product=db.session.get(Product,row.product_id);data=drafts.evidence(row);plan=data['plan']
 try:
  inputs=drafts.upload_gallery(product,row,data,plan,None,Adapter())
  assert len(inputs)==5
  data['stage']='mock_uploads_complete';drafts.save(row,data,'uploading')
  Path('/app/instance/byte-upload-resumed').write_text('all-five-acknowledged-no-product-create')
 except drafts.GalleryPending:
  assert len(data['upload_inputs'])==3
  data['stage']='gallery_uploading';drafts.save(row,data,'uploading')
 assert row.shopify_product_id is None and data['create_attempted'] is False
'''

smoke.IMPORT += "\n" + UPLOAD
smoke.CHECK += '''
with sqlite3.connect('/app/instance/site.db') as connection:
 row=connection.execute("select media_evidence_json,shopify_product_id from shopify_product_identity where sku='UPLOAD-OFFLINE'").fetchone()
 assert row[1] is None
 data=json.loads(row[0]);assert data['create_attempted'] is False
 expected=5 if Path('/app/instance/byte-upload-resumed').exists() else 3
 ledger=json.loads(Path('/app/instance/mock-upload-ledger.json').read_text())
 assert len(data['upload_inputs'])==len(ledger)==len(set(ledger))==expected
 assert [u['source'] for u in data['upload_inputs']]==ledger
'''
original_docker = smoke.docker


def docker(*args, **kwargs):
    result = original_docker(*args, **kwargs)
    if args and args[0] == 'restart':
        smoke.wait_ready(args[1])
        original_docker('exec', args[1], 'python', '-c', UPLOAD)
    return result


smoke.docker = docker
if __name__ == '__main__':
    try:
        smoke.main(sys.argv[1])
        print('byte-bounded upload: 3 acknowledged before restart; 2 after; no duplicate upload or product creation')
    except subprocess.CalledProcessError as exc:
        print(exc.stderr, file=sys.stderr)
        raise SystemExit(exc.returncode)
