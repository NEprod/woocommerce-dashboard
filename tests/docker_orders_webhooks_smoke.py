"""Offline0017→0018 upgrade, two web workers, Orders/event persistence and restart."""
import sys
import docker_orders_smoke as smoke

smoke.SEED_OLD=smoke.SEED_OLD.replace('0015_customisation_templates','0017_packaging').replace('synthetic_0015_seeded','synthetic_0017_seeded')
smoke.CHECK=smoke.CHECK.replace('0016_shopify_orders','0018_orders_webhooks').replace('*0015*0016*','*0017*0018*')+'''
with sqlite3.connect('/app/instance/site.db') as connection:
 assert connection.execute('select count(*) from packaging_package').fetchone()[0]==6
 assert connection.execute("select enabled from scheduled_task where key='shopify_orders'").fetchone()[0]==0
 assert connection.execute('select count(*) from shopify_order_event').fetchone()[0]==1
 assert connection.execute('select state from shopify_order_event').fetchone()[0]=='incomplete'
 assert 'Ellie' not in connection.execute('select payload from shopify_order_event').fetchone()[0]
assert 'Live webhook updates are not configured' in body
assert opener.open('http://127.0.0.1:7485/packaging/',timeout=5).status==200
'''
smoke.IMPORT+='''
from app.order_models import ShopifyOrderEvent
from cryptography.fernet import Fernet
with app.app_context():
 db.session.add(ShopifyOrderEvent(store_id=payload['store'],delivery_id='offline-synthetic-delivery',topic='orders/updated',
  received_at=orders.now(),digest='a'*64,payload=Fernet(Fernet.generate_key()).encrypt(b'synthetic only').decode(),
  state='incomplete',attempts=1,error='Incomplete fictional snapshot; use Sync Now.'))
 db.session.commit()
'''

if __name__=='__main__':smoke.main(sys.argv[1])
