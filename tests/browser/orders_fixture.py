"""Disposable, loopback-only Orders browser fixture. No live configuration/network."""
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
os.environ['SECRET_KEY']='orders-browser-fictional-secret-000000000000'
os.environ['DISCORD_ENABLED']='false'
for key in ('SHOPIFY_STORE_DOMAIN','SHOPIFY_ADMIN_ACCESS_TOKEN','SHOPIFY_CLIENT_ID','SHOPIFY_CLIENT_SECRET','SHOPIFY_API_VERSION'):
    os.environ.pop(key,None)

import app as module
from app import db
from config import Config
from app.models import User,Settings,ShopifyOrderSync,ScheduledTask
from app import shopify_orders as orders, onboarding
from app.shopify_connection import ShopifyAdapter
from test_shopify_orders import order,STORE,REV
from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server


def create_fixture(directory):
    instance=directory/'instance';instance.mkdir()
    catalogue=directory/'catalogue';catalogue.mkdir()
    output=directory/'output';output.mkdir()
    original_flask=module.Flask
    module.Flask=lambda *a,**kw:original_flask(*a,instance_path=str(instance),**kw)
    Config.SQLALCHEMY_DATABASE_URI=f'sqlite:///{instance / "site.db"}'
    app=module.create_app(recover=False)
    app.config.update(TESTING=True)
    def forbidden(*args,**kwargs): raise AssertionError('No remote request in offline Orders fixture')
    ShopifyAdapter._request=forbidden
    onboarding.pending=lambda:False
    with app.app_context():
        db.session.add_all([User(id=1,email='fixture@example.test',username='orders-fixture',
            password=generate_password_hash('fictional-browser-only-password'),is_admin=True),
            Settings(product_folder=str(catalogue),output_folder=str(output),url_prefix='https://example.test/')])
        for task in ScheduledTask.query.all():task.enabled=False
        raw=order();raw['_lines']=raw['lineItems']['nodes']
        header,lines=orders.normalise(raw,api_version='2026-10')
        orders.persist(STORE,header,lines)
        db.session.add(ShopifyOrderSync(store_id=STORE,domain='fictional.myshopify.com',state='completed',successful_at=orders.now(),attempted_at=orders.now()))
        db.session.commit()
    return app


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='tlc-orders-browser-') as temporary:
        app=create_fixture(Path(temporary))
        server=make_server('127.0.0.1',0,app,threaded=True)
        print(json.dumps({'url':f'http://127.0.0.1:{server.server_port}','synthetic':True}),flush=True)
        server.serve_forever()
