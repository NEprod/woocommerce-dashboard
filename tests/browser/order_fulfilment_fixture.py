"""Loopback disposable order/inventory fixture. All external requests forbidden."""
import os
from pathlib import Path
import sys
import tempfile
for key in list(os.environ):
    if key.startswith(('SHOPIFY_','TLC_')):os.environ.pop(key)
os.environ['SECRET_KEY']='isolated-order-fulfilment-browser-key-000000000'
os.environ['DISCORD_ENABLED']='false'
root=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(root),str(root/'tests')]
import pytest
from flask import abort,request,session
import app as module
from app import db
from app.models import ShopifyOrderSync
from app.order_work_models import WorkEvent,WorkPick,WorkPrint
from app.inventory.models import Transaction
from app.order_integration_models import OrderWebhookSetting
from app import order_integrations as integration
from test_metadata_editor_core import core
from test_order_fulfilment import fulfilment
from test_shopify_orders import STORE

directory=Path(tempfile.mkdtemp(prefix='tlc-order-fulfilment-browser-'));patch=pytest.MonkeyPatch()
flask=module.Flask
patch.setattr(module,'Flask',lambda *a,**kw:flask(*a,instance_path=str(directory/'instance'),**kw))
fixture=core.__wrapped__(directory,patch);world=next(fixture)
world,oid,lid,iid,tub=fulfilment.__wrapped__(world,patch)
app=world[0];app.config['TEMPLATES_AUTO_RELOAD']=True
if '--workflow' in sys.argv:
    from test_order_workflow_corrections import add_order
    with app.app_context():add_order((world,oid,lid,iid,tub))
patch.setattr(integration,'shopify_settings',lambda:dict(domain='fictional.myshopify.com',client_secret='private-test-key'))
with app.app_context():
    db.session.add(ShopifyOrderSync(store_id=STORE,domain='fictional.myshopify.com'));db.session.commit()
app._got_first_request=False

@app.before_request
def local_login():
    if request.remote_addr not in {'127.0.0.1','::1'}:abort(403)
    session['_user_id'],session['_fresh']='1',True

@app.get('/fixture')
def fixture_data():
    return dict(order=oid,line=lid,item=iid,tub=tub,events=WorkEvent.query.count(),picks=WorkPick.query.count(),
        transactions=Transaction.query.count(),webhooks=OrderWebhookSetting.query.count(),prints=WorkPrint.query.count())

if __name__=='__main__':app.run(host='127.0.0.1',port=7522,use_reloader=False)
