"""Loopback-only partial Draft review/approval; fictional catalogue and transport."""
import os,sys,tempfile,threading,time
from pathlib import Path
from types import SimpleNamespace
sys.path[:0]=[str(Path(__file__).parents[2]),str(Path(__file__).parents[1])]
for key in list(os.environ):
    if key.startswith(('SHOPIFY_','WOO_','DISCORD_')):os.environ.pop(key)
os.environ.update(DISCORD_ENABLED='false',TLC_SCHEDULER_READY_AT='99999999999')
import pytest,requests
from flask import session,request,abort,jsonify
from app import db,scheduled_tasks as tasks,shopify_drafts as drafts
from app.models import ScheduledTask,ShopifyProductIdentity
from test_phase3_woo_publish_preview import preview_app
from test_shopify_drafts import world
from test_shopify_variables import variable_world
from test_variable_creation_recovery import large_variable,finish_uploads,InterruptedBeforeVariants

patch=pytest.MonkeyPatch();root=Path(tempfile.mkdtemp(prefix='tlc-variable-browser-'))
fixture=preview_app.__wrapped__(root,patch);app=next(fixture)
product_fixture=world.__wrapped__(app,patch);w=next(product_fixture)
with app.app_context():
    variable=variable_world.__wrapped__(w,SimpleNamespace(param='Variable Collection'))
    product=large_variable(variable);adapter=InterruptedBeforeVariants();row=finish_uploads(product,adapter)
    identity_id=row.id;retained=row.shopify_product_id;adapter.interrupt=False
patch.setattr('app.shopify_variables.ShopifyAdapter',lambda:adapter)
patch.setattr('app.shopify_drafts.ShopifyAdapter',lambda:adapter)
patch.setattr('app.utils.discord.publishing_message',lambda data:(False,'disabled'))
requests.sessions.Session.request=lambda *a,**k:(_ for _ in ()).throw(AssertionError('External network forbidden'))

@app.before_request
def login():
    if request.remote_addr!='127.0.0.1':abort(403)
    session['_user_id']='1';session['_fresh']=True

@app.get('/__fixture__/state')
def state():
    row=db.session.get(ShopifyProductIdentity,identity_id)
    return jsonify(identity_id=identity_id,product_id=row.product_id,retained_id=retained,remote_id=row.shopify_product_id,
        state=row.state,operation_id=row.operation_id,uploads=adapter.calls.count('binary_upload'),
        creates=adapter.calls.count('product_create'),variant_writes=adapter.calls.count('variants_update')+adapter.calls.count('variants_create'),
        variants=len(adapter.remote['variants']['nodes']),media=len(adapter.remote['media']['nodes']))

def worker():
    while True:
        with app.app_context():
            try:
                task=db.session.get(ScheduledTask,tasks.MANUAL_KEY)
                if task.resume_at:task.resume_at=tasks.now();db.session.commit()
                tasks.tick('isolated-variable-worker',adapter=adapter)
            finally:db.session.remove()
        time.sleep(.1)

if __name__=='__main__':
    threading.Thread(target=worker,daemon=True).start()
    app.run(host='127.0.0.1',port=7531,use_reloader=False,threaded=True)
