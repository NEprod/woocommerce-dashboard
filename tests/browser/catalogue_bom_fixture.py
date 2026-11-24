"""Loopback-only catalogue JSON fixtures; outbound HTTP forbidden."""
import os
from pathlib import Path
import sys
import tempfile

for key in list(os.environ):
    if key.startswith(('SHOPIFY_', 'TLC_')):
        os.environ.pop(key)
os.environ['SECRET_KEY']='isolated-catalogue-bom-browser-000000000000000'
os.environ['DISCORD_ENABLED']='false'
root=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(root),str(root/'tests')]
import pytest
import requests
from flask import abort, request, session
import app as module
from app import db
from app.models import Product
from test_metadata_editor_core import core, save
from test_catalogue_bom_workspace import seed, production, scenario

requests.sessions.Session.request=lambda *a,**k: (_ for _ in ()).throw(AssertionError('Outbound HTTP forbidden'))
directory=Path(tempfile.mkdtemp(prefix='tlc-catalogue-bom-browser-'))
patch=pytest.MonkeyPatch()
original_flask=module.Flask
patch.setattr(module,'Flask',lambda *a,**kw: original_flask(*a,instance_path=str(directory/'instance'),**kw))
fixture=core.__wrapped__(directory,patch)
world=next(fixture)
app,client,_,_,parents,*_=world
app.config['TEMPLATES_AUTO_RELOAD']=True
app.jinja_env.auto_reload=True
ids=seed(app,client)
source=production(*ids)
assert save(world,'Simple Collection/Blue Token',kind='shared',data={'production':source}).status_code==200
if '--rewrite-fixture' in sys.argv:
    for path in ('Simple Collection/Blue Token', 'Simple Collection/Green Token'):
        assert save(world,path,data={'production':{'mode':'replace',**source}}).status_code==200
saved=scenario(app,dict(**source['costing'],lines=[{k:v for k,v in line.items() if k!='op'} for line in source['lines'].values()],selling_price='99.99',_snapshot={'result':{'cost':'historical'}}))
blocked=scenario(app,dict(lines=[dict(source='manual',name='Hypothetical',quantity='1',unit='g',unit_cost='1')]))
with app.app_context():
    p=db.session.get(Product,parents['Simple Collection/Blue Token'])
    pid,cid=p.id,p.collection_id

# Fixture construction used a test client before adding loopback-only helpers.
app._got_first_request=False

@app.before_request
def local_login():
    if request.remote_addr not in {'127.0.0.1','::1'}:abort(403)
    session['_user_id'],session['_fresh']='1',True


@app.get('/fixture')
def fixture_data():
    return dict(pid=pid,mobile_pid=parents['Simple Collection/Green Token'],cid=cid,items=ids,scenario=saved,blocked=blocked)


if __name__=='__main__':
    app.run(host='127.0.0.1',port=7516,use_reloader=False)
