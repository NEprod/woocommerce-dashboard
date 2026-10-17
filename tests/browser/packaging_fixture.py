"""Loopback-only fictional editor/packages; no outgoing Shopify or Discord calls."""
import os
import sys
import tempfile
from pathlib import Path
for key in list(os.environ):
    if key.startswith('SHOPIFY_') or key in {'PRODUCT_FOLDER','OUTPUT_FOLDER','INTAKE_ROOT'}: os.environ.pop(key)
os.environ['SECRET_KEY']='fictional-packaging-browser-only-secret-00000000000000'
os.environ['DISCORD_ENABLED']='false'
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
requests.sessions.Session.request=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Outbound network denied'))
patch=pytest.MonkeyPatch();directory=Path(tempfile.mkdtemp(prefix='tlc-solo-packaging-browser-'))
fixture=core.__wrapped__(directory,patch)
app,_,root,_,ids,*_=next(fixture)
with app.app_context():
    user=User.query.one().id
    save(dict(package('fixture-envelope',('24','16.5','1'),type='ENVELOPE',max_contents_thickness_mm='3'),name='Fictional measured C5+'))
@app.before_request
def fixture_login():
    if request.remote_addr not in {'127.0.0.1','::1'}: abort(403)
    session['_user_id']=str(user);session['_fresh']=True
if __name__=='__main__':
    print({'fixture':str(directory),'products':ids},flush=True)
    app.run(host='127.0.0.1',port=7496,use_reloader=False)
