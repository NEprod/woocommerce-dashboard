"""Real offline Intake HTTP workflow, process death and 2/4-worker restarts.

Only disposable, script-owned mounts/containers are used. No host catalogue,
Shopify credentials or network. Run: python3 tests/docker_intake_direct_smoke.py IMAGE
"""
import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

from docker_orders_smoke import docker, wait_ready

SEED = r'''
import hashlib,json
from pathlib import Path
from PIL import Image
from werkzeug.security import generate_password_hash
from app import create_app,db
from app.models import Settings,User,ScheduledTask
app=create_app(recover=False)
with app.app_context():
 db.session.add(User(username='intake',email='intake@example.test',password=generate_password_hash('fictional-intake-password'),is_admin=True))
 db.session.add(Settings(_product_folder='/catalogue',_output_folder='/output',_url_prefix='https://invalid.example/images/'))
 for task in ScheduledTask.query.all():task.enabled=False
 db.session.commit()
source=Path('/intake/Dogs');source.mkdir()
for n,c in [('hound 1.png','red'),('hound 2.png','blue'),('beagle 1.png','green'),('beagle 2.png','yellow')]:Image.new('RGB',(10,8),c).save(source/n)
Path('/app/instance/originals.json').write_text(json.dumps(sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir())))
other=Path('/catalogue/Unrelated');other.mkdir();(other/'product_info.json').write_text('{"invalid":"must not scan"}')
print('seeded')
'''

HOLD = r'''
import time
from pathlib import Path
from flask import Flask
from app.intake_file_tools import changing_files
app=Flask('lock_probe',instance_path='/app/instance')
with app.app_context(),changing_files():
 Path('/app/instance/lock-ready').touch()
 deadline=time.monotonic()+120
 while not Path('/app/instance/lock-release').exists() and time.monotonic()<deadline:time.sleep(.1)
 assert Path('/app/instance/lock-release').exists(),'Contention probe expired before explicit release'
'''

CONTEND = r'''
from flask import Flask
from app.intake_file_tools import changing_files
app=Flask('lock_probe',instance_path='/app/instance')
with app.app_context():
 try:
  with changing_files():raise AssertionError('Competing process acquired active Intake')
 except ValueError:print('safely_excluded')
'''

INTERRUPT = r'''
import os
from pathlib import Path
from app import create_app
from app import intake_direct as service,intake_file_tools as files
app=create_app(recover=False);app.config['INTAKE_RUN_INLINE']=True
move=files.rename
def terminate(source,target,**kwargs):
 move(source,target,**kwargs)
 os._exit(78)  # real death after first original move; no journal or finally
files.rename=terminate
with app.app_context():
 proposal=service.plan('group','Dogs');assert proposal['ready'],proposal['issues']
 service.accept('group','Dogs',{},proposal['digest'])
'''

HTTP = r'''
import html,http.cookiejar,json,re,time,urllib.parse,urllib.request
from pathlib import Path
jar=http.cookiejar.CookieJar()
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(jar))
base='http://127.0.0.1:7485'
def get(path):return opener.open(base+path,timeout=20).read().decode()
def token(body):return html.unescape(re.search(r'name="csrf_token"[^>]*value="([^"]+)"',body).group(1))
def post(path,data):
 response=opener.open(base+path,data=urllib.parse.urlencode(data).encode(),timeout=30)
 return response.url,response.read().decode()
login=get('/login');post('/login',{'csrf_token':token(login),'username':'intake','password':'fictional-intake-password'})
'''

WORKFLOW = HTTP + r'''
import hashlib,sqlite3
assert len(list(Path('/intake/Dogs').glob('*.png')))==3
assert len(list(Path('/intake/Dogs').rglob('*.png')))==4
def stage(name,settings):
 page=get('/image-preparation/stage/'+name+'?path=Dogs')
 csrf=token(page)
 _,body=post('/image-preparation/'+name+'/preview?live=1',{'csrf_token':csrf,'path':'Dogs','options':json.dumps(settings)})
 preview=json.loads(body);assert preview['ready'],preview['html']
 url,body=post('/image-preparation/'+name+'/confirm',{'csrf_token':csrf,'path':'Dogs','options':json.dumps(preview['options']),'digest':preview['digest']})
 if name!='handoff':
  assert 'Changes applied' in body and 'Actual files now' in body,(url,body)
  return
 assert '/image-preparation/result/' in url,url
 op=url.rsplit('/',1)[-1]
 for _ in range(100):
  with sqlite3.connect('/app/instance/site.db') as connection:row=connection.execute('select status,error,scope from catalogue_operation where id=?',(op,)).fetchone()
  assert row[0] not in ('failed','partial'),row
  if row[0]=='succeeded':return op
  time.sleep(.1)
 raise AssertionError('File operation did not finish')
stage('group',{})  # fresh proposal of actual remaining files, not automatic replay
assert not list(Path('/intake/Dogs').glob('*.png'))
stage('folders',{'renames':{'hound':'Hound','beagle':'Beagle'}})
stage('rename',{'prefix':'DOG'})
stage('metadata',{'document':{'collection_type':'Simple','sku_prefix':'DOG-','title':'Dogs','live':False,'price':'3.50','variation_attributes':[],'attributes':{}}})
assert sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/intake/Dogs').rglob('*.png'))==json.loads(Path('/app/instance/originals.json').read_text())
handoff=stage('handoff',{})
for _ in range(200):
 with sqlite3.connect('/app/instance/site.db') as connection:
  scope=json.loads(connection.execute('select scope from catalogue_operation where id=?',(handoff,)).fetchone()[0])
  transaction=scope['intake_file_transaction'];scan=transaction.get('registration_operation_id')
  row=connection.execute('select status,error from catalogue_operation where id=?',(scan,)).fetchone() if scan else None
 if row and row[0]=='succeeded':break
 assert not row or row[0] in ('running','pending'),row
 time.sleep(.1)
else:raise AssertionError('Registration did not complete')
assert not Path('/intake/Dogs').exists()
assert not Path('/intake/Prepared').exists()
assert not list(Path('/catalogue').glob('.woocommerce-dashboard-staging/intake-*'))
assert sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/catalogue/Dogs').rglob('*.png'))==json.loads(Path('/app/instance/originals.json').read_text())
assert 'DOG-0001' in get('/api/edit_products')
with sqlite3.connect('/app/instance/site.db') as connection:
 assert connection.execute('select count(*) from product').fetchone()[0]==2
 assert connection.execute('select count(*) from shopify_publish_job where scan_operation_id=?',(scan,)).fetchone()[0]==2
 assert connection.execute('select enabled from automatic_publishing').fetchone()==(0,)
 assert connection.execute('select count(*) from catalogue_operation where operation_type in ("full","update","shared_collection")').fetchone()[0]==0
 assert connection.execute('select count(*) from catalogue_operation where operation_type="append"').fetchone()[0]==1
 assert connection.execute('select count(*) from catalogue_operation where operation_type in ("intake_group","intake_folder_edit","intake_image_rename","intake_metadata_save")').fetchone()[0]==0
assert not list(Path('/intake').rglob('.tlc-intake-txn-*'))
assert not list(Path('/intake').rglob('.tlc-rename-*'))
snapshot={'handoff':handoff,'scan':scan,'cross_filesystem':bool(transaction.get('cross_filesystem'))}
Path('/app/instance/accepted.json').write_text(json.dumps(snapshot))
print(json.dumps(snapshot))
'''

CHECK = HTTP + r'''
import sqlite3
snapshot=json.loads(Path('/app/instance/accepted.json').read_text())
with sqlite3.connect('/app/instance/site.db') as connection:
 assert connection.execute('select count(*) from product').fetchone()==(2,)
 assert connection.execute('select count(*) from catalogue_operation where operation_type="append"').fetchone()==(1,)
 assert connection.execute('select status from catalogue_operation where id=?',(snapshot['scan'],)).fetchone()==('succeeded',)
 assert connection.execute('select enabled from automatic_publishing').fetchone()==(0,)
 assert connection.execute('pragma foreign_key_check').fetchall()==[]
assert 'DOG-0002' in get('/api/edit_products')
assert 'Files installed' in get('/image-preparation/result/'+snapshot['handoff'])
assert Path('/catalogue/Unrelated/product_info.json').read_text()=='{"invalid":"must not scan"}'
children=Path('/proc/1/task/1/children').read_text().split()
gunicorn=next(p for p in children if 'gunicorn' in Path('/proc/'+p+'/cmdline').read_text() and 'scheduler_worker' not in Path('/proc/'+p+'/cmdline').read_text())
print(json.dumps({'workers':len(Path('/proc/'+gunicorn+'/task/'+gunicorn+'/children').read_text().split()),**snapshot}))
'''


def main(image):
    meta=json.loads(docker('image','inspect',image))[0]
    assert meta['Architecture']=='amd64' and meta['Os']=='linux'
    for workers in (2,4):
        container=None
        instance='tlc-intake-instance-'+uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix='tlc-intake-docker-') as temporary:
            # Mac Docker Desktop bind mounts do not reliably enforce flock.
            # Linux-native persistent appdata matches the real Unraid appdata
            # contract; source/catalogue/output still use separate bind mounts.
            docker('volume','create',instance)
            root=Path(temporary);mounts=['--mount',f'type=volume,src={instance},dst=/app/instance']
            for name,target in [('intake','/intake'),('catalogue','/catalogue'),('output','/output')]:
                (root/name).mkdir(mode=0o777);(root/name).chmod(0o777)
                mounts.extend(['--mount',f'type=bind,src={root/name},dst={target}'])
            env=['-e','SECRET_KEY=intake-docker-isolated-000000000000000000000','-e','DISCORD_ENABLED=false','-e','URL_PREFIX=https://invalid.example/images/']
            try:
                docker('run','--rm','--platform','linux/amd64','--network','none',*env,*mounts,image,'python','-c',SEED)
                container=docker('run','-d','--platform','linux/amd64','--network','none',*env,*mounts,image,'python','-m','app.runtime','gunicorn','--workers',str(workers),'--threads','2','--bind','0.0.0.0:7485','run:app')
                wait_ready(container)
                def execute(code):return docker('exec','--user','100:100',container,'python','-c',code)
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers+1) as pool:
                    holder=pool.submit(execute,HOLD)
                    for _ in range(100):
                        try:
                            docker('exec',container,'test','-f','/app/instance/lock-ready')
                            break
                        except subprocess.CalledProcessError:pass
                        time.sleep(.1)
                    else:raise AssertionError('Lock holder did not start')
                    contenders=list(pool.map(execute,[CONTEND]*workers))
                    assert all('safely_excluded' in value for value in contenders)
                    docker('exec',container,'touch','/app/instance/lock-release');holder.result(timeout=20)
                try:execute(INTERRUPT)
                except subprocess.CalledProcessError as exc:assert exc.returncode==78,exc.stderr
                else:raise AssertionError('Expected actual process interruption')
                docker('restart',container);wait_ready(container)
                execute(WORKFLOW)
                before=json.loads(execute(CHECK).splitlines()[-1]);assert before['workers']==workers
                docker('restart',container);wait_ready(container)
                after=json.loads(execute(CHECK).splitlines()[-1]);assert before==after
                print(json.dumps({'offline':True,'workers':workers,'process_death_recovered':True,'concurrent_owners_excluded':workers,'http_workflow':True,'restart_persistence':True,**after}),flush=True)
            except subprocess.CalledProcessError as exc:
                print(exc.stdout,exc.stderr,file=sys.stderr)
                raise
            finally:
                if container:docker('rm','-f',container)
                docker('volume','rm',instance)


if __name__=='__main__':main(sys.argv[1])
