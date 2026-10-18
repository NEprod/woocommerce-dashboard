"""Loopback-only fictional editor/packages; no outgoing Shopify or Discord calls."""
import os
import sys
import tempfile
import json
from pathlib import Path
for key in list(os.environ):
    if key.startswith('SHOPIFY_') or key in {'PRODUCT_FOLDER','OUTPUT_FOLDER','INTAKE_ROOT'}: os.environ.pop(key)
os.environ['SECRET_KEY']='fictional-packaging-browser-only-secret-00000000000000'
os.environ['DISCORD_ENABLED']='false'
os.environ['SHOPIFY_STORE_DOMAIN']='fictional.myshopify.com'
os.environ['SHOPIFY_ADMIN_ACCESS_TOKEN']='fictional_browser_only_token_00000000'
repo=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(repo),str(repo/'tests')]
import pytest
import requests
from flask import session,request,abort
from test_metadata_editor_core import core
from app import db
from app.models import User
from app.packaging_catalogue import save
from test_packaging import package
from app.packaging_models import Package
from app import package_links
requests.sessions.Session.request=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Outbound network denied'))
patch=pytest.MonkeyPatch();directory=Path(tempfile.mkdtemp(prefix='tlc-solo-packaging-browser-'))
fixture=core.__wrapped__(directory,patch)
app,_,root,_,ids,*_=next(fixture)
with app.app_context():
    user=User.query.one().id
    save(dict(package('fixture-envelope',('24','16.5','1'),type='ENVELOPE',max_contents_thickness_mm='3'),name='Fictional measured C5+'))
    c5=Package.query.filter_by(seed_key='c5-plus').one()
    save({'name':c5.name,'internal_cm':{'height':'.3'}},c5,version=c5.version)
    package_links.set_id(c5,'gid://shopify/ShippingPackageV2/116237992319',[])
parent=root/'Simple Collection/product_info.json'
source=json.loads(parent.read_text());source['dimensions']={'length':155,'width':155,'height':3}
parent.write_text(json.dumps(source))
@app.before_request
def fixture_login():
    if request.remote_addr not in {'127.0.0.1','::1'}: abort(403)
    session['_user_id']=str(user);session['_fresh']=True
if __name__=='__main__':
    print({'fixture':str(directory),'products':ids},flush=True)
    app.run(host='127.0.0.1',port=7496,use_reloader=False)
