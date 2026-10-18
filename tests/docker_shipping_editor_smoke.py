"""Offline upgrade/two-worker restart plus actual sparse shipping save and scan."""
import sys
import docker_package_types_smoke as previous

smoke=previous.smoke
smoke.IMPORT+='''
from pathlib import Path
from PIL import Image
from unittest.mock import patch
from app.models import Settings,Product
from app.metadata_save import review_token
from app.taxonomy_assignments import product_document
from app.packaging_contract import effective,packed_cm
from app.utils.scanner import scan_collection
from app.utils.ingest import ingest_rows_to_db
from app.utils.operation_control import finish_catalogue_operation
collection=Path('/catalogue/Shipping Fixture');child=collection/'Fictional Card'
child.mkdir(parents=True,exist_ok=True)
parent={'collection_type':'Simple','sku_prefix':'SMOKE-SHIP-','title':'Fictional shipping collection',
 'price':'3.50','weight':'50','dimensions':{'length':155,'width':155,'height':3}}
(collection/'product_info.json').write_text(json.dumps(parent))
(child/'product_info.json').write_text(json.dumps({'title':'Fictional Card','future':{'preserved':True}}))
Image.new('RGB',(3,4),'navy').save(child/'card.png')
def project():
 rows=scan_collection(collection,'https://invalid.example/','/output',force_update=True,update_csv=True,log=lambda *a,**k:None)
 assert ingest_rows_to_db(rows,log=lambda *a,**k:None)['products_failed']==0
with app.app_context():
 settings=Settings.query.first()
 if not settings:settings=Settings();db.session.add(settings)
 assert settings.product_folder=='/catalogue' and settings.output_folder=='/output'
 db.session.commit();project()
 product=Product.query.filter_by(source_relpath='Shipping Fixture/Fictional Card').one()
 package_id=Package.query.filter_by(seed_key='c5-plus').one().id
 before=(collection/'product_info.json').read_bytes()
 body={'kind':'override','revision':review_token(product,'override'),
  'data':{'packaging':{'selected_package_id':package_id,'assignment_kind':'manual',
    'packed_dimensions':{'length':155,'width':155,'height':3,'unit':'mm'}}}}
 sku=product.sku
client=app.test_client();app.config['WTF_CSRF_ENABLED']=False
with client.session_transaction() as session:session['_user_id']='1';session['_fresh']=True
def completed(*a,**k):finish_catalogue_operation(k['operation_id'],status='succeeded')
with patch('app.routes.start_scan',completed),patch('app.routes.notify_editor_saved'):
 response=client.post('/edit_products/'+sku+'/save',json=body)
 assert response.status_code==200 and response.json['json_saved'],response.json
with app.app_context():
 project();db.session.expire_all()
 product=Product.query.filter_by(sku=sku).one();packing=effective(product_document(product))
 assert packing['selected_package_id']==package_id and packed_cm(packing)['height']=='0.3'
 assert (collection/'product_info.json').read_bytes()==before
'''
smoke.CHECK+='''
parent=json.loads(Path('/catalogue/Shipping Fixture/product_info.json').read_text())
child=json.loads(Path('/catalogue/Shipping Fixture/Fictional Card/product_info.json').read_text())
assert parent['dimensions']=={'length':155,'width':155,'height':3} and 'packaging' not in parent
assert child['future']=={'preserved':True}
assert child['packaging']['selected_package_id']==c5[0]
assert child['packaging']['packed_dimensions']=={'length':155,'width':155,'height':3,'unit':'mm'}
with sqlite3.connect('/app/instance/site.db') as connection:
 product_id=connection.execute("select id from product where source_relpath='Shipping Fixture/Fictional Card'").fetchone()[0]
page=opener.open('http://127.0.0.1:7485/edit_products/'+str(product_id)+'/edit/override',timeout=5).read().decode()
bootstrap=json.loads(re.search(r'<script id="metadata-editor-bootstrap" type="application/json">(.*?)</script>',page,re.S)[1])
assert bootstrap['authored']['packaging']['selected_package_id']==c5[0]
assert 'data-shipping-configurations' in page and 'Weight' in page
'''

# Exercise filesystem persistence with the same unprivileged identity as the web
# workers, not root-owned 0600 test JSON that the real editor could not reopen.
smoke.IMPORT='''
import os,pwd
if os.geteuid()==0:
 user=pwd.getpwnam('app');os.setgroups([]);os.setgid(user.pw_gid);os.setuid(user.pw_uid)
'''+smoke.IMPORT

if __name__=='__main__':smoke.main(sys.argv[1])
