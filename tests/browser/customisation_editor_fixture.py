"""Disposable loopback-only CUA fixture. Never run against a real catalogue.

Run with the project's test Python, then use customisation-editor-ux.cua.js.
No Shopify/Discord access, production mounts, persistent user DB or app worker.
"""
import os
import sys
import json
import tempfile
from pathlib import Path

for key in list(os.environ):
    if key.startswith('SHOPIFY_') or key in {'PRODUCT_FOLDER','OUTPUT_FOLDER','INTAKE_ROOT'}:
        os.environ.pop(key)
os.environ['SECRET_KEY']='fictional-local-browser-tests-only-0000000000000000'
os.environ['DISCORD_ENABLED']='false'
repo=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(repo),str(repo/'tests')]
import pytest
import requests
from flask import session,request,abort
from app import db
from app.models import User
from app.customisation_templates import save
from app.utils.scan_runner import start_scan
from test_metadata_editor_core import core

requests.sessions.Session.request=lambda *a,**kw: (_ for _ in ()).throw(AssertionError('Outbound request blocked in browser fixture'))
patch=pytest.MonkeyPatch();directory=Path(tempfile.mkdtemp(prefix='tlc-field-ux-'))
fixture=core.__wrapped__(directory,patch)
app,_,root,_,ids,*_=next(fixture)
patch.setattr('app.routes.start_scan',start_scan)
app.jinja_env.auto_reload=True
with app.app_context():
    user=User.query.one().id
    template=save({'name':'Fictional Pinned Fields','definition':{'fields':{'template_note':{'type':'text','label':'Template note'}}}})
source=root/'Simple Collection/product_info.json'
data=json.loads(source.read_text())
data['customisation']={'template':{'id':template['id'],'revision':1},'fields':{
    'name_on_card':{'type':'text','label':'Name','required':True,'enabled':True,'max_length':30,'default':'Example','help_text':'Check spelling','placeholder':'Your name','future':{'keep':True}},
    'message_on_card':{'type':'multiline','label':'Message','required':False,'enabled':True,'default':'Happy birthday'},
    'style':{'type':'choice','label':'Style','choices':[{'id':'script','label':'Script','future':{'keep':True}},{'id':'block','label':'Block'}],'default':'script'},
    'extras':{'type':'checkbox_group','label':'Extras','choices':[{'id':'star','label':'Star'},{'id':'heart','label':'Heart'}],'default':['star']},
    'age':{'type':'number','label':'Age','minimum':0,'maximum':120,'whole_number':True,'default':4},
    'delivery_date':{'type':'date','label':'Date','min_date':'2026-01-01','default':'2026-10-16'},
    'colour':{'type':'colour','label':'Colour','choices':[{'id':'red','label':'Red','value':'#ff0000'},{'id':'blue','label':'Blue','value':'#0000ff'}],'default':'#ff0000'},
    'gift_message':{'type':'checkbox','label':'Gift message','default':False},
    'photo':{'type':'photo','label':'Photo','allowed_extensions':['.png','.jpg'],'allowed_media_categories':['image'],'max_file_size_bytes':5000000,'max_files':1}}}
source.write_text(json.dumps(data))

@app.before_request
def fixture_login():
    if request.remote_addr not in {'127.0.0.1','::1'}:abort(403)
    session['_user_id']=str(user);session['_fresh']=True

if __name__=='__main__':
    print('Fictional browser fixture; root: '+str(directory),flush=True)
    app.run(host='127.0.0.1',port=7495,use_reloader=False)
