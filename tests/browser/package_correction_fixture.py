"""Disposable loopback-only correction preview, with a mock Shopify identity."""
import os
import sys
import tempfile
from pathlib import Path

repo=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(repo),str(repo/'tests')]
for key in list(os.environ):
    if key.startswith(('SHOPIFY_','WOO_','DISCORD_')):os.environ.pop(key)
os.environ['DISCORD_ENABLED']='false'
os.environ['TLC_SCHEDULER_READY_AT']='99999999999'
import pytest
import requests
from flask import request,session,abort
from test_phase3_woo_publish_preview import preview_app
from test_shopify_drafts import world
from test_package_correction import protected

requests.sessions.Session.request=lambda *a,**k:(_ for _ in ()).throw(AssertionError('Network forbidden'))
patch=pytest.MonkeyPatch()
fixture=preview_app.__wrapped__(Path(tempfile.mkdtemp(prefix='tlc-package-correction-browser-')),patch)
app=next(fixture)
products=world.__wrapped__(app,patch)
product,row,adapter,old,job=protected(next(products))
patch.setattr('app.shopify_connection.ShopifyAdapter',lambda:adapter)

@app.before_request
def login():
    if request.remote_addr not in {'127.0.0.1','::1'}:abort(403)
    session['_user_id']='1';session['_fresh']=True

if __name__=='__main__':app.run(host='127.0.0.1',port=7540,use_reloader=False)
