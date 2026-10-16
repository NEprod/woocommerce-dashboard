"""Isolated fictional catalogue for native browser checks; all outbound calls denied."""
import os
import sys
import tempfile
from pathlib import Path
for key in list(os.environ):
    if key.startswith('SHOPIFY_') or key in {'PRODUCT_FOLDER','OUTPUT_FOLDER','INTAKE_ROOT'}:os.environ.pop(key)
os.environ['SECRET_KEY']='fictional-packaging-browser-only-secret-00000000000000'
os.environ['DISCORD_ENABLED']='false'
repo=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(repo),str(repo/'tests')]
import pytest
import requests
from flask import session,request,abort
from test_metadata_editor_core import core
from app import db
from app.models import User,Product
from app.packaging_catalogue import save
from test_packaging import package
requests.sessions.Session.request=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Outbound network denied'))
patch=pytest.MonkeyPatch();directory=Path(tempfile.mkdtemp(prefix='tlc-packaging-browser-'))
fixture=core.__wrapped__(directory,patch)
app,_,root,_,ids,*_=next(fixture)
with app.app_context():
    user=User.query.one().id
    save(dict(package('fixture-c3',('45','32','2')),name='Fictional measured C3'))
    for key,title in [('card','Fictional greeting card'),('a5','Fictional A5 print'),('a3','Fictional A3 print')]:
        db.session.add(Product(sku='PACKFIX-'+key,title=title,product_type='simple',catalogue_status='active'))
    db.session.commit()
from app.taxonomy_assignments import product_document as original_document
from test_packaging import item
def fictional_document(product,**kwargs):
    if product.sku.startswith('PACKFIX-'):
        key=product.sku[8:];sizes={'card':('15.5','15.5','.3'),'a5':('21','14.8','.2'),'a3':('42','29.7','.3')}
        physical=item(key,sizes[key],weight_g={'card':'50','a5':'100','a3':'150'}[key])
        for name in ('identity','sku','title','quantity','requires_shipping'):physical.pop(name,None)
        return {'requires_shipping':True,'packaging':physical}
    return original_document(product,**kwargs)
patch.setattr('app.taxonomy_assignments.product_document',fictional_document)
@app.before_request
def fixture_login():
    if request.remote_addr not in {'127.0.0.1','::1'}:abort(403)
    session['_user_id']=str(user);session['_fresh']=True
if __name__=='__main__':
    print({'fixture':str(directory),'products':ids},flush=True)
    app.run(host='127.0.0.1',port=7496,use_reloader=False)
