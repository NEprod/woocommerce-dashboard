"""Disposable loopback browser fixture; all outbound requests denied."""
import json
import os
from pathlib import Path
import sys
import tempfile

for key in list(os.environ):
    if key.startswith('SHOPIFY_') or key in {'PRODUCT_FOLDER','OUTPUT_FOLDER','INTAKE_ROOT'}:
        os.environ.pop(key)
os.environ['SECRET_KEY']='fictional-royal-mail-browser-fixture-00000000000'
os.environ['DISCORD_ENABLED']='false'
repo=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(repo),str(repo/'tests')]
import pytest
from flask import abort,request,session
from test_metadata_editor_core import core
from test_royal_mail_export import export_world
from app.utils.scan_runner import start_scan

patch=pytest.MonkeyPatch();directory=Path(tempfile.mkdtemp(prefix='tlc-royal-mail-browser-')).resolve()
fixture=core.__wrapped__(directory,patch)
values=next(fixture)
export_world.__wrapped__(values,patch)
app=values[0]
patch.setattr('app.routes.start_scan',start_scan)
patch.setattr('app.scheduled_tasks.queue_projected',lambda op:None)

@app.before_request
def fixture_login():
    if request.remote_addr not in {'127.0.0.1','::1'}:abort(403)
    session['_user_id']='1';session['_fresh']=True

if __name__=='__main__':
    print(json.dumps({'fixture':str(directory),'products':values[4]}),flush=True)
    app.run(host='127.0.0.1',port=7524,use_reloader=False)
