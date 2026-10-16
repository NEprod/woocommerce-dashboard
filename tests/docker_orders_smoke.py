"""Offline two-worker Docker smoke. Disposable synthetic mounts; no Shopify network.

Run with the existing test Python: python tests/docker_orders_smoke.py IMAGE
The only container removed is the exact disposable ID created by this script.
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_shopify_orders import order,STORE

SECRET='orders-offline-smoke-secret-000000000000000000'
PASSWORD='fictional-docker-only-password'
SEED_OLD='''
import sqlite3
from alembic import command
from app.database import _alembic_config
from werkzeug.security import generate_password_hash
command.upgrade(_alembic_config('sqlite:////app/instance/site.db'),'0015_customisation_templates')
with sqlite3.connect('/app/instance/site.db') as connection:
 connection.execute('INSERT INTO user (id,email,username,password,is_admin) VALUES (1,?,?,?,1)',
  ('fixture@example.test','orders-smoke',generate_password_hash('fictional-docker-only-password')))
 connection.execute('UPDATE scheduled_task SET enabled=0')
print('synthetic_0015_seeded')
'''
IMPORT='''
import json,sys
from app import create_app,db
from app.models import ScheduledTask,ShopifyOrderSync,ShopifyOrder,ShopifyOrderLine
from app import shopify_orders as orders
payload=json.load(sys.stdin)
app=create_app(recover=False)
with app.app_context():
 for task in ScheduledTask.query.all():task.enabled=False
 raw=payload['order'];raw['_lines']=raw['lineItems']['nodes']
 header,lines=orders.normalise(raw,api_version='2026-10')
 orders.persist(payload['store'],header,lines)
 db.session.add(ShopifyOrderSync(store_id=payload['store'],domain='fictional.myshopify.com',state='completed',successful_at=orders.now(),attempted_at=orders.now()))
 db.session.commit()
 assert ShopifyOrder.query.count()==1 and ShopifyOrderLine.query.count()==2
 print('synthetic_order_committed')
'''
CHECK='''
import html,json,re,sqlite3,urllib.request,urllib.parse,http.cookiejar
from pathlib import Path
from decimal import Decimal
with sqlite3.connect('/app/instance/site.db') as connection:
 assert connection.execute('select version_num from alembic_version').fetchone()[0]=='0016_shopify_orders'
 assert connection.execute('select username from user where id=1').fetchone()[0]=='orders-smoke'
 assert connection.execute('pragma foreign_key_check').fetchall()==[]
 assert connection.execute('select count(*) from shopify_order').fetchone()[0]==1
 rows=connection.execute('select id,remote_id,personalisation_json from shopify_order_line order by id').fetchall()
 assert len(rows)==2 and rows[0][1]!=rows[1][1]
 answers=[json.loads(row[2])['fields'] for row in rows]
 assert [(a[0]['value'],a[1]['value']) for a in answers]==[('Ellie',6),('Freddie',5)]
 children=Path('/proc/1/task/1/children').read_text().split()
 gunicorn=next(pid for pid in children if 'gunicorn' in Path('/proc/'+pid+'/cmdline').read_text() and 'scheduler_worker' not in Path('/proc/'+pid+'/cmdline').read_text())
 assert len(Path('/proc/'+gunicorn+'/task/'+gunicorn+'/children').read_text().split())==2
 assert 'Uid:\\t0\\t' not in Path('/proc/'+gunicorn+'/status').read_text()
jar=http.cookiejar.CookieJar()
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(jar))
page=opener.open('http://127.0.0.1:7485/login',timeout=5).read().decode()
token=re.search(r'name="csrf_token"[^>]*value="([^"]+)"',page).group(1)
form=urllib.parse.urlencode({'csrf_token':html.unescape(token),'username':'orders-smoke','password':'fictional-docker-only-password'}).encode()
opener.open('http://127.0.0.1:7485/login',data=form,timeout=5).read()
listing=opener.open('http://127.0.0.1:7485/orders/',timeout=5)
assert listing.status==200 and listing.headers['Cache-Control']=='no-store, private'
body=listing.read().decode();assert '#TEST-1001' in body and 'Test order' in body
detail=opener.open('http://127.0.0.1:7485/orders/1',timeout=5).read().decode()
assert 'Ellie' in detail and 'Freddie' in detail and 'Anniversary Years' in detail
assert 'line-1' in detail and 'line-2' in detail
status=json.load(opener.open('http://127.0.0.1:7485/orders/status',timeout=5))
assert status['count']==1 and 'synthetic@example.test' not in json.dumps(status)
dashboard=opener.open('http://127.0.0.1:7485/?include_test=1',timeout=5).read().decode()
assert 'Items awaiting fulfilment' in dashboard and 'built-in method' not in dashboard
assert '<strong>2</strong>' in dashboard
assert len(list(Path('/app/instance/backups').glob('*0015*0016*.sqlite3')))>=1
print(json.dumps({'migration':'0016_shopify_orders','orders':1,'lines':2,'workers':2,'local_pages':True,'answers_separate':True,'private_cache_headers':True,'backup_present':True,'line_ids':[r[0] for r in rows]}))
'''


def docker(*args,input=None):
    result=subprocess.run(['docker',*args],input=input,text=True,capture_output=True,check=True)
    return result.stdout.strip()


def wait_ready(container):
    for _ in range(45):
        try:
            docker('exec',container,'python','-c',"import urllib.request;urllib.request.urlopen('http://127.0.0.1:7485/login',timeout=2)")
            return
        except subprocess.CalledProcessError:time.sleep(1)
    raise RuntimeError('Offline smoke server did not become ready')


def main(image):
    # The image must be the one about to be published, not an old develop cache.
    meta=json.loads(docker('image','inspect',image))[0]
    assert meta['Architecture']=='amd64' and meta['Os']=='linux'
    container=None
    with tempfile.TemporaryDirectory(prefix='tlc-orders-docker-') as temporary:
        root=Path(temporary)
        for name in ('instance','catalogue','output'):(root/name).mkdir()
        mounts=[]
        for source,target in [('instance','/app/instance'),('catalogue','/catalogue'),('output','/output')]:
            mounts.extend(['--mount',f'type=bind,src={root/source},dst={target}'])
        env=['-e','SECRET_KEY='+SECRET,'-e','DISCORD_ENABLED=false']
        docker('run','--rm','--network','none',*env,*mounts,'--entrypoint','python',image,'-c',SEED_OLD)
        try:
            container=docker('run','-d','--network','none',*env,*mounts,image,
                'python','-m','app.runtime','gunicorn','--workers','2','--threads','2','--bind','0.0.0.0:7485','run:app')
            wait_ready(container)
            assert docker('inspect','--format','{{.HostConfig.NetworkMode}}',container)=='none'
            docker('exec','-i',container,'python','-c',IMPORT,input=json.dumps({'store':STORE,'order':order()}))
            before=json.loads(docker('exec',container,'python','-c',CHECK))
            docker('restart',container);wait_ready(container)
            after=json.loads(docker('exec',container,'python','-c',CHECK))
            assert before==after
            print(json.dumps({'offline':True,'restart_persistence':True,'image':image,
                'revision':meta['Config']['Labels'].get('org.opencontainers.image.revision'),**after}))
        finally:
            if container:docker('rm','-f',container)


if __name__=='__main__':main(sys.argv[1])
