"""Real local editor and disposable registry; outbound requests forbidden."""
import json
import os
from pathlib import Path
import sys
import tempfile

for key in list(os.environ):
    if key.startswith('SHOPIFY_') or key in {'PRODUCT_FOLDER','OUTPUT_FOLDER','INTAKE_ROOT'}:
        os.environ.pop(key)
os.environ['SECRET_KEY']='organisation-browser-fictional-secret-000000000000'
os.environ['DISCORD_ENABLED']='false'
root=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(root),str(root/'tests')]
import pytest
import requests
from flask import abort, request, session
from app.taxonomy_workspace import empty_registry, validate
from app.utils.atomic_files import atomic_write_json
from test_metadata_editor_core import core
from test_organisation_inheritance import choices, parent

requests.sessions.Session.request=lambda *a,**k:(_ for _ in ()).throw(AssertionError('Outbound request denied'))
patch=pytest.MonkeyPatch();directory=Path(tempfile.mkdtemp(prefix='tlc-organisation-browser-')).resolve()
fixture=core.__wrapped__(directory,patch)
app,_,catalogue,_,ids,*_=next(fixture)
app.instance_path=str(directory/'private-instance');Path(app.instance_path).mkdir()
atomic_write_json(Path(app.instance_path)/'commerce.json',{'version':1,'active_platform':'shopify','shopify':{}})
taxonomy=directory/'taxonomy';taxonomy.mkdir();app.config['TAXONOMY_ROOT']=str(taxonomy)
document=empty_registry()
for kind in ('categories','storefront_collections','tags','attributes'):
    for row in choices()[kind]:
        item={key:row[key] for key in ('key','name','state','aliases')}
        item['slug']=row['key'];item['name']=item['name'].split(' > ')[-1]
        if kind=='categories':item['parent']=row['parent']
        if kind=='attributes':
            item.update(navigation=False,visible_default=True,
                terms=[{key:term[key] for key in ('key','name','state','aliases')} | {'slug':term['key']} for term in row['terms']])
        document[kind].append(item)
(taxonomy/'registry.json').write_bytes(validate(document))
source=catalogue/'Simple Collection/product_info.json'
shared=json.loads(source.read_text());shared.update(parent());shared['variation_attributes']=[]
source.write_text(json.dumps(shared))

@app.before_request
def fixture_login():
    if request.remote_addr not in {'127.0.0.1','::1'}:abort(403)
    session['_user_id']='1';session['_fresh']=True

if __name__=='__main__':
    print(json.dumps({'products':ids,'fixture':str(directory)}),flush=True)
    app.run(host='127.0.0.1',port=7503,use_reloader=False)
