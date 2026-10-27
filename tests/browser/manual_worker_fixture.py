"""Loopback-only, disposable 248 MiB catalogue and gated mock Shopify worker."""
import os,sys,tempfile,threading,time
from pathlib import Path
sys.path[:0]=[str(Path(__file__).parents[2]),str(Path(__file__).parents[1])]
for key in list(os.environ):
    if key.startswith(('SHOPIFY_','WOO_','DISCORD_')):os.environ.pop(key)
os.environ.update(DISCORD_ENABLED='false',TLC_SCHEDULER_READY_AT='99999999999')
import pytest,requests
from flask import session,request,abort,jsonify
from app import db,scheduled_tasks as tasks
from app.models import CatalogueOperation,ScheduledTask,ShopifyProductIdentity
from app.publishing_progress import view
from test_phase3_woo_publish_preview import preview_app
from test_shopify_drafts import world
from test_shopify_gallery_bytes import originals,ByteAdapter

patch=pytest.MonkeyPatch();root=Path(tempfile.mkdtemp(prefix='tlc-manual-browser-'))
fixture=preview_app.__wrapped__(root,patch);app=next(fixture)
product_fixture=world.__wrapped__(app,patch);w=next(product_fixture)
with app.app_context():product,_=originals(w,size=(1280,1280))
gates={key:threading.Event() for key in ('start','upload','batch','processing','readback')}
state={'phase':'accepted'}


class Adapter(ByteAdapter):
    processing=True
    def __init__(self):super().__init__();self.processing=True
    def stage_images(self,config,images):
        if self.calls.count('binary_upload')==6 and not gates['batch'].is_set():
            state['phase']='batch_acknowledged';assert gates['batch'].wait(60)
        return super().stage_images(config,images)
    def upload_image(self,*args):
        if not self.calls.count('binary_upload') and not gates['upload'].is_set():
            state['phase']='upload_started';assert gates['upload'].wait(60)
        return super().upload_image(*args)
    def read_simple(self,*args,**kwargs):
        if self.remote and gates['processing'].is_set() and not gates['readback'].is_set():
            state['phase']='readback';assert gates['readback'].wait(60)
        result=super().read_simple(*args,**kwargs)
        if self.remote and not gates['processing'].is_set():state['phase']='media_processing'
        return result


adapter=Adapter()
patch.setattr('app.shopify_drafts.ShopifyAdapter',lambda:adapter)
patch.setattr('app.utils.discord.publishing_message',lambda data:(False,'disabled'))
requests.sessions.Session.request=lambda *a,**k:(_ for _ in ()).throw(AssertionError('External network forbidden'))


@app.before_request
def login():
    if request.remote_addr!='127.0.0.1':abort(403)
    session['_user_id']='1';session['_fresh']=True


@app.get('/__fixture__/state')
def snapshot():
    op=CatalogueOperation.query.filter_by(operation_type='shopify_draft_publish').order_by(CatalogueOperation.started_at.desc()).first()
    return jsonify(phase=state['phase'],uploads=adapter.calls.count('binary_upload'),creates=adapter.calls.count('product_create'),
        operation_id=op.id if op else None,progress=view(op) if op else None)


@app.post('/__fixture__/allow/<gate>')
def allow(gate):
    if gate not in gates:abort(404)
    if gate=='readback':
        for i,m in enumerate(adapter.remote['media']['nodes']):m.update(status='READY',image={'url':f'https://cdn.shopify.com/{i}.png'})
    gates[gate].set();return jsonify(ok=True)


def worker():
    while True:
        if gates['start'].is_set() and not (state['phase']=='media_processing' and not gates['processing'].is_set()):
            with app.app_context():
                try:
                    task=db.session.get(ScheduledTask,tasks.MANUAL_KEY)
                    if task.resume_at:task.resume_at=tasks.now();db.session.commit()
                    tasks.tick('isolated-browser-worker',adapter=adapter)
                except Exception as exc:state['worker_error']=str(exc)
                finally:db.session.remove()
        time.sleep(.1)


if __name__=='__main__':
    threading.Thread(target=worker,daemon=True).start()
    app.run(host='127.0.0.1',port=7529,use_reloader=False,threaded=True)
