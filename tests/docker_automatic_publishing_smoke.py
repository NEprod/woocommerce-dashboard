"""Offline0018→0019, persistent queue/events, 2/3/4 workers and restart.

Uses only disposable mounts and --network none. No automatic authority is granted.
"""
import sys
import subprocess
import docker_customisation_review_smoke as previous

smoke = previous.smoke
workers = int(sys.argv[2]) if len(sys.argv) > 2 else 2
assert workers in {2, 3, 4}
smoke.SEED_OLD = smoke.SEED_OLD.replace('0017_packaging', '0018_orders_webhooks')
smoke.CHECK = smoke.CHECK.replace('0018_orders_webhooks', '0019_automatic_publishing').replace(
    '*0017*0018*', '*0018*0019*').replace('read_text().split())==2', f'read_text().split())=={workers}').replace(
        "'workers':2", "'workers':" + str(workers))
original_docker = smoke.docker


def docker(*args, **kwargs):
    if args and args[0] == 'run' and 'gunicorn' in args:
        args = list(args)
        args[args.index('--workers') + 1] = str(workers)
    return original_docker(*args, **kwargs)


smoke.docker = docker
smoke.IMPORT += '''
from app.models import AutomaticPublishing,ShopifyPublishJob,ShopifyPublishingEvent,ShopifyDueEvent,CatalogueOperation
from app.automatic_publishing import queue_product
from app.catalogue_views import capture
from datetime import timedelta
with app.app_context():
 assert not db.session.get(AutomaticPublishing,'shopify').enabled
 product=Product.query.filter_by(sku=sku).one()
 queue_product(product);capture(product);db.session.commit()
 job=db.session.get(ShopifyPublishJob,product.id)
 job.trigger='offline-restart-fixture'
 fixture=CatalogueOperation(id='publishing-offline-event',operation_type='shopify_draft_publish',status='succeeded',
  scope='{"fixture_only":true}',started_at=orders.now(),finished_at=orders.now(),recovery_state='none')
 db.session.add(fixture);db.session.flush()
 db.session.add(ShopifyPublishingEvent(id='offline:fixture',operation_id=fixture.id,product_id=product.id,
  event_type='fixture_only',trigger='offline',local_revision=job.requested_revision,occurred_at=orders.now(),
  receipt_json=json.dumps({'fixture_only':True,'changes':[],'remote_writes':0})))
 db.session.add(ShopifyDueEvent(product_id=product.id,event_type='release',schedule_revision='fixture',snapshot_guard='fixture',
  due_at=orders.now()+timedelta(days=100),state='pending',attempts=0))
 db.session.commit()
 from app import publishing_logs
 handler=publishing_logs.configure(app)
 publishing_logs.event(job,'offline-fixture-no-remote-writes')
 handler.close()
'''
smoke.CHECK += '''
import hashlib,time
with sqlite3.connect('/app/instance/site.db') as connection:
 assert connection.execute("select enabled from automatic_publishing where key='shopify'").fetchone()==(0,)
 jobs=connection.execute('select product_id,requested_revision,verified_revision,state,trigger,source_identity from shopify_publish_job order by product_id').fetchall()
 assert any(r[4]=='offline-restart-fixture' for r in jobs)
 assert connection.execute("select state from shopify_due_event where schedule_revision='fixture'").fetchone()==('pending',)
 event=connection.execute("select receipt_json from shopify_publishing_event where id='offline:fixture'").fetchone()
 assert json.loads(event[0])['remote_writes']==0
 assert connection.execute('pragma foreign_key_check').fetchall()==[]
assert 'offline-fixture-no-remote-writes' in Path('/app/instance/logs/publishing.log').read_text()
children=Path('/proc/1/task/1/children').read_text().split()
scheduler=next(pid for pid in children if 'scheduler_worker' in Path('/proc/'+pid+'/cmdline').read_text())
# Runtime grace is unit-tested at its exact ten-minute boundary. Verify its
# observable effect here without requesting access to another process's secrets.
with sqlite3.connect('/app/instance/site.db') as connection:
 assert connection.execute("select count(*) from scheduled_task_run where trigger='scheduled'").fetchone()[0]==0
assert opener.open('http://127.0.0.1:7485/settings/publishing',timeout=5).status==200
for page in ('shopify_publisher','catalogue_views'):
 assert opener.open('http://127.0.0.1:7485/settings/tasks?task='+page,timeout=5).status==200
'''

if __name__ == '__main__':
    try:
        smoke.main(sys.argv[1])
    except subprocess.CalledProcessError as exc:
        print(exc.stderr, file=sys.stderr)
        raise SystemExit(exc.returncode)
