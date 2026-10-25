"""Existing offline two-worker smoke plus real collection-scoped override save."""
import sys
import subprocess
import docker_customisation_inheritance_smoke as previous

smoke = previous.smoke
original_docker = smoke.docker


def docker(*args, **kwargs):
    if args and args[0] == 'run':
        args = (args[0], '-e', 'URL_PREFIX=https://invalid.example/', *args[1:])
    return original_docker(*args, **kwargs)


smoke.docker = docker
smoke.IMPORT += '''
import time
from app.utils import scan_runner
staging=Path('/catalogue/ZZ_STAGING');staging.mkdir()
(staging/'product_info.json').write_text('Intentionally invalid unrelated staging JSON')
unrelated=staging.joinpath('product_info.json').read_bytes()
with app.app_context():
 product=Product.query.filter_by(sku=sku).one()
 parent_bytes=(collection/'product_info.json').read_bytes()
 body={'kind':'override','revision':review_token(product,'override'),
       'data':{'description':'Scoped offline override save'}}
original=scan_runner.scan_collection
scanned=[]
def bounded(folder,*args,**kwargs):
 assert Path(folder)==collection,(folder,collection)
 scanned.append(str(folder))
 return original(folder,*args,**kwargs)
with patch('app.routes.start_scan',scan_runner.start_scan),patch('app.utils.scan_runner.scan_collection',bounded),patch('app.routes.notify_editor_saved'):
 response=client.post('/edit_products/'+sku+'/save',json=body)
 assert response.status_code==200,response.json
 deadline=time.monotonic()+20
 while time.monotonic()<deadline:
  with app.app_context():progress=scan_runner.get_progress(response.json['run_id'])
  if progress['status'] in {'done','error'}:break
  time.sleep(.05)
 assert progress['status']=='done',progress
 assert progress['summary']['folders']==1
 assert scanned==[str(collection)]
assert (collection/'product_info.json').read_bytes()==parent_bytes
assert (staging/'product_info.json').read_bytes()==unrelated
with app.app_context():
 db.session.expire_all()
 assert Product.query.filter_by(sku=sku).one().description=='Scoped offline override save'
'''
smoke.CHECK += '''
assert bootstrap['authored']['description']=='Scoped offline override save'
assert bootstrap['resolved']['description']=='Scoped offline override save'
assert Path('/catalogue/ZZ_STAGING/product_info.json').read_text()=='Intentionally invalid unrelated staging JSON'
with sqlite3.connect('/app/instance/site.db') as connection:
 assert connection.execute("select description from product where source_relpath='Shipping Fixture/Fictional Card'").fetchone()[0]=='Scoped offline override save'
'''

if __name__ == '__main__':
    try:
        smoke.main(sys.argv[1])
    except subprocess.CalledProcessError as error:
        print(error.stderr, file=sys.stderr)
        raise SystemExit(error.returncode)
