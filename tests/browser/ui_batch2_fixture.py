"""Disposable Batch 2 browser states; no external transport or write actions."""
import copy
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2]
baseline_value=os.environ.get('TLC_UI_BASELINE','/private/tmp/tlc-ui-batch1-release.RqBrt1')
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
for key in list(os.environ):
    if key.startswith(('SHOPIFY_','WOO_','TLC_','DISCORD_')) or key in {'PRODUCT_FOLDER','OUTPUT_FOLDER','INTAKE_ROOT'}:
        os.environ.pop(key)
os.environ['DISCORD_ENABLED']='false'
import pytest
import requests
from flask import abort, request, session, jsonify, send_from_directory
import app as module
from app import db, shopify_drafts as drafts, shopify_products
from app.models import Product, ProductImage, ShopifyProductIdentity, CatalogueOperation
from test_phase3_woo_publish_preview import preview_app
from test_shopify_drafts import world
from test_shopify_simple_updates import created
from test_shopify_variables import variable_world, VariableAdapter
from test_variable_creation_recovery import large_variable

patch=pytest.MonkeyPatch()
temporary=Path(tempfile.mkdtemp(prefix='tlc-ui-batch2-browser-'))
real_flask=module.Flask
module.Flask=lambda *a,**kw:real_flask(*a,instance_path=str(temporary/'instance'),**kw)
fixture=preview_app.__wrapped__(temporary,patch);app=next(fixture)
product_fixture=world.__wrapped__(app,patch);w=next(product_fixture)
# Normal mocked creation seeds the healthy receipt; never a live Shopify request.
p=w[0];p.sku='16bit-0001';db.session.commit()
shared=json.loads(w[2].read_text());shared.update(title='Anniversary Level Complete',categories=[])
w[2].write_text(json.dumps(shared))
healthy,row,adapter=created(w)
plans={healthy.id:drafts.preview(healthy,adapter)[0]}
healthy_identity=row.id
ids={'healthy':healthy.id}
for ident,title,sku in [(2,'Girl Birthday — Lego Inspired Character Card','LCBC-0002'),
                        (3,'Boy Birthday — Lego Inspired Character Card','LCBC-0001')]:
    p=db.session.get(Product,ident);folder=w[1].parent/('Existing' if ident==2 else 'Link')
    shutil.copytree(w[1],folder,dirs_exist_ok=True)
    (folder/'product_info.json').write_text(json.dumps({'title':title}))
    p.title=title;p.sku=sku;p.images.clear()
    for image in healthy.images:p.images.append(ProductImage(url=image.url,position=image.position,alt_text=image.alt_text))
    db.session.commit()
    if ident==2:
        plan,_=drafts.preview(p,VariableAdapter())
        plan['blockers']=['Collection recovery required: Cards > Birthday Cards']
        plan['collections']=[dict(key='categories:birthday',label='Cards > Birthday Cards',name='Birthday Cards',
            action='Resolve mapping',mapping_state='uncertain',remote_id='gid://shopify/Collection/909',member=None)]
        plans[p.id]=plan;ids['creation']=p.id
    else:
        data=copy.deepcopy(drafts.evidence(row));data['plan'].update(product_id=p.id,sku=sku,title=title,
            source_identity=p.source_relpath)
        data['error']='Fictional uncertain response. Reconcile the retained product before another write.'
        db.session.add(CatalogueOperation(id='batch2-recovery',operation_type='shopify_draft_publish',status='partial',scope='{}'))
        db.session.flush()
        recovery=ShopifyProductIdentity(product_id=p.id,source_identity=p.source_relpath,sku=sku,
            store_key=row.store_key,store_id=row.store_id,store_domain=row.store_domain,
            shopify_product_id='gid://shopify/Product/102',state='uncertain',verification_state='unverified',
            operation_id='batch2-recovery',reviewed_digest='a'*64,media_evidence_json=json.dumps(data))
        db.session.add(recovery);db.session.commit();ids['recovery']=p.id;recovery_identity=recovery.id
variable=variable_world.__wrapped__(w,SimpleNamespace(param='Variable Collection'))
p=large_variable(variable)
plan,_=drafts.preview(p,VariableAdapter())
plan.update(classification='no_change',actual_status='ACTIVE',remote_id='gid://shopify/Product/103',title='3D Christmas Ornament Set',sku='3DCO-0001')
for field in plan['fields']:field.update(before=copy.deepcopy(field['after']),changed=False)
plans[p.id]=plan;ids['variable']=p.id

requests.sessions.Session.request=lambda *a,**k:(_ for _ in ()).throw(AssertionError('External transport forbidden'))
reads=[]
def preview(product,*args,**kwargs):
    reads.append({'id':product.id,'scope':kwargs.get('scope','full')})
    if product.id==ids['recovery']:
        from app.shopify_connection import ShopifyError
        raise ShopifyError('Recovery required for retained Shopify identity. Reconcile before any write; another create is blocked.')
    plan=copy.deepcopy(plans[product.id])
    if request.args.get('fixture_change') and product.id==ids['healthy']:
        plan['classification']='update'
        next(f for f in plan['fields'] if f['field']=='descriptionHtml').update(
            before='<p>Previous description</p>',after='<p>Reviewed description update</p>',changed=True)
        return plan,'synthetic-ui-token-not-valid-for-execution'
    return plan,None
patch.setattr(drafts,'preview',preview)
real_states=shopify_products.states_for
def states(products,*args,**kwargs):
    result=real_states(products,*args,**kwargs)
    for ident,state in result.items():
        if ident==ids['healthy']:state.update(state='no_change',label='Up to Date')
        if ident==ids['creation']:state.update(state='create',label='Needs Creating',message='Collection recovery required: Cards > Birthday Cards',blockers=['Collection recovery required: Cards > Birthday Cards'])
        if ident==ids['variable']:state.update(state='no_change',label='Up to Date',remote_status='Active (last compared)')
    return result
patch.setattr(shopify_products,'states_for',states)
patch.setattr('app.product_presentation.product_issue_links',lambda product,messages:[dict(label='Cards > Birthday Cards',url='/taxonomy/shopify?source=categories%3Abirthday#individual-mapping')] if product.id==ids['creation'] else [])

from jinja2 import ChoiceLoader,FileSystemLoader
current_loader=app.jinja_loader
baseline=Path(baseline_value)
@app.before_request
def isolate():
    if request.remote_addr!='127.0.0.1':abort(403)
    if request.method!='GET' and not (request.path.endswith('/shopify/review') and request.form.get('action')=='preview'):
        abort(403,'Browser fixture permits only the read-only preview action')
    session['_user_id']='1';session['_fresh']=True
    app.jinja_loader=ChoiceLoader([FileSystemLoader(str(baseline/'app/templates')),current_loader]) if request.args.get('ui_before') else current_loader
    app.jinja_env.cache.clear()

@app.after_request
def before_assets(response):
    if request.args.get('ui_before') and response.mimetype=='text/html':
        response.set_data(response.get_data().replace(b'/static/assets/',b'/__fixture__/before-static/assets/'))
    return response

@app.get('/__fixture__/before-static/<path:name>')
def before_static(name):return send_from_directory(baseline/'app/static',name)

@app.get('/__fixture__/state')
def fixture_state():return jsonify(ids=ids,healthy_identity=healthy_identity,recovery_identity=recovery_identity,
    preview_reads=reads,external_requests=0,write_actions_allowed=False)

if __name__=='__main__':
    print(json.dumps({'synthetic':True,'ids':ids,'recovery_identity':recovery_identity,'temporary':str(temporary)}),flush=True)
    app.run(host='127.0.0.1',port=7543,threaded=False,use_reloader=False)
