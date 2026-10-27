"""Isolated fresh variable Create; pause server after acknowledged variants."""
import os,sys,tempfile,threading,time
from pathlib import Path
from types import SimpleNamespace
sys.path[:0]=[str(Path(__file__).parents[2]),str(Path(__file__).parents[1])]
for key in list(os.environ):
    if key.startswith(('SHOPIFY_','WOO_','DISCORD_')):os.environ.pop(key)
os.environ.update(DISCORD_ENABLED='false',TLC_SCHEDULER_READY_AT='99999999999')
import pytest,requests
from flask import request,session,abort,jsonify
from app import db,scheduled_tasks as tasks,shopify_drafts as drafts
from app.models import ScheduledTask,ShopifyProductIdentity,CatalogueOperation
from test_phase3_woo_publish_preview import preview_app
from test_shopify_drafts import world
from test_shopify_variables import variable_world
from test_variable_creation_recovery import large_variable
from test_variable_verification_completion import DelayedVariants

patch=pytest.MonkeyPatch();root=Path(tempfile.mkdtemp(prefix='tlc-verification-browser-'))
fixture=preview_app.__wrapped__(root,patch);app=next(fixture)
product_fixture=world.__wrapped__(app,patch);w=next(product_fixture)
with app.app_context():product=large_variable(variable_world.__wrapped__(w,SimpleNamespace(param='Variable Collection')))
product_id=product.id;adapter=DelayedVariants();adapter.interrupt=False;adapter.delay=1
gate=threading.Event();waiting=threading.Event()
patch.setattr('app.shopify_variables.ShopifyAdapter',lambda:adapter)
patch.setattr('app.shopify_drafts.ShopifyAdapter',lambda:adapter)
patch.setattr('app.utils.discord.publishing_message',lambda data:(False,'disabled'))
requests.sessions.Session.request=lambda *a,**k:(_ for _ in ()).throw(AssertionError('External network forbidden'))

@app.before_request
def login():
    if request.remote_addr!='127.0.0.1':abort(403)
    session['_user_id']='1';session['_fresh']=True

@app.get('/__fixture__/state')
def snapshot():
    row=ShopifyProductIdentity.query.first();op=db.session.get(CatalogueOperation,row.operation_id) if row else None
    return jsonify(product_id=product_id,state=row.state if row else 'local',operation_id=op.id if op else None,
        operation_status=op.status if op else None,waiting=waiting.is_set(),uploads=adapter.calls.count('binary_upload'),
        creates=adapter.calls.count('product_create'),variant_creates=adapter.calls.count('variants_create'),
        variants=len(adapter.remote['variants']['nodes']) if adapter.remote else 0)

@app.post('/__fixture__/continue')
def proceed():gate.set();return jsonify(ok=True)

def worker():
    while True:
        if waiting.is_set() and not gate.is_set():time.sleep(.1);continue
        with app.app_context():
            try:
                task=db.session.get(ScheduledTask,tasks.MANUAL_KEY)
                if task.resume_at:task.resume_at=tasks.now();db.session.commit()
                tasks.tick('isolated-verification-worker',adapter=adapter)
                row=ShopifyProductIdentity.query.first()
                if row and drafts.evidence(row).get('verification_wait') and row.state=='processing':waiting.set()
            finally:db.session.remove()
        time.sleep(.1)

if __name__=='__main__':
    threading.Thread(target=worker,daemon=True).start()
    app.run(host='127.0.0.1',port=7532,use_reloader=False,threaded=True)
