"""Offline release image startup/0027→0029/restart audit; disposable mounts only.

Usage: PYTHONPATH=tests:. python tests/docker_order_fulfilment_smoke.py IMAGE SHA
No host credentials are passed, no ports published, network=none throughout.
"""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
from werkzeug.security import generate_password_hash
from test_order_release_migrations import _create_phase0_database, backup, verify


def docker(*args,input=None):
    return subprocess.run(['docker',*args],input=input,text=True,capture_output=True,check=True,timeout=120).stdout.strip()


CHECK=r'''
import html,http.cookiejar,json,os,re,sqlite3,urllib.parse,urllib.request
assert os.environ['APP_GIT_REVISION']==os.environ['EXPECTED_REVISION']
assert open('/proc/1/status').read().split('Uid:')[1].split()[0]=='100'
with sqlite3.connect('/app/instance/site.db') as c:
 assert c.execute('select version_num from alembic_version').fetchone()[0]=='0030_order_workflow'
 c.execute('update settings set include_test_orders=1')
 assert c.execute('pragma integrity_check').fetchone()==('ok',)
 assert not c.execute('pragma foreign_key_check').fetchall()
 oid=c.execute("select id from shopify_order where remote_id='gid://shopify/Order/987654321'").fetchone()[0]
 before={t:c.execute('select count(*) from '+t).fetchone()[0] for t in ['inventory_item','inventory_movement','shopify_order','shopify_order_line','order_work_line','order_work_outbound','order_webhook_setting','order_integration_settings']}
 assert before['inventory_item']==before['inventory_movement']==before['shopify_order']==1 and before['shopify_order_line']==2
 assert all(before[t]==0 for t in ['order_webhook_setting','order_integration_settings'])
jar=http.cookiejar.CookieJar()
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(jar))
base='http://127.0.0.1:7485'
page=opener.open(base+'/login',timeout=10).read().decode()
token=html.unescape(re.search(r'name="csrf_token"[^>]*value="([^"]+)"',page).group(1))
opener.open(base+'/login',data=urllib.parse.urlencode(dict(csrf_token=token,username='release-smoke-only',password='synthetic-docker-password-only')).encode(),timeout=10).read()
for path,text in [('/production/orders/?test=yes','Order Fulfilment'),('/settings/shopify-webhooks','Shopify Webhooks'),('/settings/tasks','Shopify Order Synchronisation'),('/production/orders/replenishment','Stock &amp; Replenishment Tracker'),('/orders/','TEST-987654321')]:
 response=opener.open(base+path,timeout=15)
 assert response.status==200 and text in response.read().decode(),path
targets=[]
for reference in ['#TEST-987654321','TLC-ORDER-'+str(oid)]:
 response=opener.open(base+'/production/orders/?'+urllib.parse.urlencode(dict(reference=reference)),timeout=15)
 assert response.geturl().endswith('/production/orders/'+str(oid))
 assert 'Needs Configuration' in response.read().decode()
 targets.append(response.geturl())
assert targets[0]==targets[1]
assert b'<svg' in opener.open(base+'/production/orders/'+str(oid)+'/barcode.svg',timeout=10).read()
assert 'Production sheet' in opener.open(base+'/production/orders/'+str(oid)+'/sheet',timeout=10).read().decode()
with sqlite3.connect('/app/instance/site.db') as c:
 after={t:c.execute('select count(*) from '+t).fetchone()[0] for t in before}
 assert all(after[t]==v for t,v in before.items() if t not in ['order_work_line','order_work_outbound'])
 assert after['order_work_line']==2 and after['order_work_outbound']==2
print(json.dumps(dict(head='0030_order_workflow',revision=os.environ['APP_GIT_REVISION'],uid=100,counts=after,pages=True,lookup_same_job=True,outbound_disabled=True)))
'''


def ready(container):
    for _ in range(50):
        try:
            docker('exec',container,'python','-c',"import urllib.request;urllib.request.urlopen('http://127.0.0.1:7485/login',timeout=2)")
            return
        except subprocess.CalledProcessError:time.sleep(1)
    raise RuntimeError('Disposable release image failed startup')


def main(image,revision):
    meta=json.loads(docker('image','inspect',image))[0]
    assert (meta['Os'],meta['Architecture'])==('linux','amd64')
    assert meta['Config']['Labels']['org.opencontainers.image.revision']==revision
    container=None
    with tempfile.TemporaryDirectory(prefix='tlc-fulfilment-release-smoke-') as temporary:
        root=Path(temporary)
        for name in ['instance','catalogue','output']:(root/name).mkdir()
        source=root/'synthetic-source.db';_create_phase0_database(source)
        verify(source,root/'audit')
        backup(root/'audit/existing-copy.db',root/'instance/site.db')
        with sqlite3.connect(root/'instance/site.db') as c:
            c.execute("UPDATE scheduled_task SET enabled=0,active_run_id=NULL,owner=NULL,lease_until=NULL")
            c.execute("INSERT INTO user(email,username,password,is_admin) VALUES (?,?,?,1)",('smoke@example.invalid','release-smoke-only',generate_password_hash('synthetic-docker-password-only')))
            c.execute("UPDATE settings SET product_folder='/catalogue',output_folder='/output'")
        mounts=[]
        for name in ['instance','catalogue','output']:mounts.extend(['--mount',f'type=bind,src={root/name},dst=/app/{name}' if name=='instance' else f'type=bind,src={root/name},dst=/{name}'])
        env=['-e','SECRET_KEY=disposable-release-smoke-key-000000000000000000',
             '-e','DISCORD_ENABLED=false','-e','SHOPIFY_ORDERS_STARTUP_ENABLED=false',
             '-e','PUID=100','-e','PGID=100','-e','UMASK=002','-e','TZ=Europe/London','-e','EXPECTED_REVISION='+revision]
        try:
            container=docker('run','-d','--network','none',*mounts,*env,image)
            assert docker('inspect','--format','{{.HostConfig.NetworkMode}}',container)=='none'
            ready(container)
            first=json.loads(docker('exec',container,'python','-c',CHECK))
            docker('restart',container);ready(container)
            second=json.loads(docker('exec',container,'python','-c',CHECK))
            assert first==second
            print(json.dumps(dict(image=image,architecture='linux/amd64',offline=True,restart_persistent=True,**second),sort_keys=True))
        except Exception:
            if container:print(docker('logs','--tail','40',container),file=sys.stderr)
            raise
        finally:
            if container:docker('rm','-f',container)


if __name__=='__main__':main(sys.argv[1],sys.argv[2])
