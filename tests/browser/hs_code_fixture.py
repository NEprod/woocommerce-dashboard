"""Existing real editor/scanner on disposable data; loopback and no network."""
import json
import os
from pathlib import Path
import sys
import tempfile

for key in list(os.environ):
    if key.startswith('SHOPIFY_') or key in {'PRODUCT_FOLDER','OUTPUT_FOLDER','INTAKE_ROOT'}:
        os.environ.pop(key)
os.environ['SECRET_KEY']='fictional-hs-browser-fixture-00000000000000000'
os.environ['DISCORD_ENABLED']='false'
repo=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(repo),str(repo/'tests')]
import pytest
import requests
from flask import abort,request,session
from test_metadata_editor_core import core
from app.utils.scan_runner import start_scan

requests.sessions.Session.request=lambda *a,**k:(_ for _ in ()).throw(AssertionError('Outbound request denied'))
patch=pytest.MonkeyPatch();directory=Path(tempfile.mkdtemp(prefix='tlc-hs-browser-')).resolve()
fixture=core.__wrapped__(directory,patch)
app,_,catalogue,_,ids,*_=next(fixture)
patch.setattr('app.routes.start_scan',start_scan)
path=catalogue/'Simple Collection/product_info.json'
document=json.loads(path.read_text());document['harmonized_system_code']='490900';path.write_text(json.dumps(document))

@app.before_request
def fixture_login():
    if request.remote_addr not in {'127.0.0.1','::1'}:abort(403)
    session['_user_id']='1';session['_fresh']=True

if __name__=='__main__':
    print(json.dumps({'fixture':str(directory),'products':ids}),flush=True)
    app.run(host='127.0.0.1',port=7523,use_reloader=False)
