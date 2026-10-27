"""Offline 0019→0020, retained schedules/queues/events and 2/3/4-worker restart."""
import sys
import subprocess
import docker_automatic_publishing_smoke as previous

smoke=previous.smoke
smoke.SEED_OLD=smoke.SEED_OLD.replace('0018_orders_webhooks','0019_automatic_publishing')
smoke.SEED_OLD+='''
with sqlite3.connect('/app/instance/site.db') as connection:
 connection.execute("update scheduled_task set interval_minutes=60 where key='shopify_products'")
 connection.commit()
'''
smoke.CHECK=smoke.CHECK.replace('0019_automatic_publishing','0020_operations_acceptance').replace('*0018*0019*','*0019*0020*')
smoke.CHECK+='''
with sqlite3.connect('/app/instance/site.db') as connection:
 assert connection.execute("select interval_minutes from scheduled_task where key='shopify_products'").fetchone()==(60,)
 assert connection.execute("select interval_minutes from scheduled_task where key='shopify_releases'").fetchone()==(5,)
 assert connection.execute("select count(*) from sqlite_master where type='index' and name in ('ix_publishing_events_time','ix_scheduled_history_key_time')").fetchone()==(2,)
for page in ('/settings/tasks','/settings/tasks?task=shopify_releases','/publishing/queue','/operations/events','/operations/events/publishing-offline-event'):
 assert opener.open('http://127.0.0.1:7485'+page,timeout=5).status==200,page
'''

if __name__=='__main__':
 try:smoke.main(sys.argv[1])
 except subprocess.CalledProcessError as exc:
  print(exc.stderr,file=sys.stderr)
  raise SystemExit(exc.returncode)
