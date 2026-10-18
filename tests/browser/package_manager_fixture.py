"""Loopback-only synthetic package settings and manual reconciliation; zero network."""
import os
import sys
import tempfile
from pathlib import Path
for key in list(os.environ):
    if key.startswith('SHOPIFY_'):os.environ.pop(key)
os.environ['SECRET_KEY']='fictional-package-manager-browser-secret-000000000000'
os.environ['DISCORD_ENABLED']='false'
repo=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(repo),str(repo/'tests')]
import pytest
import requests
from flask import abort,request,session
from app import db,shopify_packaging
from app.models import User
from test_setup_flow import setup_app
requests.sessions.Session.request=lambda *a,**k:(_ for _ in ()).throw(AssertionError('Outbound network denied'))
patch=pytest.MonkeyPatch();root=Path(tempfile.mkdtemp(prefix='tlc-package-manager-browser-'))
fixture=setup_app.__wrapped__(root,patch);app,path=next(fixture)
app.config['WTF_CSRF_ENABLED']=True
os.environ['SHOPIFY_STORE_DOMAIN']='fictional.myshopify.com'
os.environ['SHOPIFY_ADMIN_ACCESS_TOKEN']='fictional_browser_token_0000000000'
with app.app_context():
    db.session.add(User(username='package-browser',email='fixture@example.test',password='not-for-login',is_admin=True));db.session.commit()
    # Synthetic existing installation with historical evidence, not a seed default.
    import json
    from app.packaging_models import Package
    c5=Package.query.filter_by(seed_key='c5-plus').one()
    spec=json.loads(c5.specification);spec['max_contents_thickness_mm']='3'
    c5.specification=json.dumps(spec);db.session.commit()
cap={'store_id':'gid://shopify/Shop/42','config':'synthetic-only','package_sync_enabled':False,
     'limitation':'Synthetic fixture: package API readback unavailable. No Shopify connection or mutation occurs.'}
patch.setattr(shopify_packaging,'current',lambda:dict(cap))
patch.setattr(shopify_packaging,'refresh',lambda:dict(cap))
@app.before_request
def fixture_login():
    if request.remote_addr not in {'127.0.0.1','::1'}:abort(403)
    session['_user_id']='1';session['_fresh']=True
if __name__=='__main__':
    print({'fixture':str(root),'database':str(path)},flush=True)
    app.run(host='127.0.0.1',port=7499,use_reloader=False)
