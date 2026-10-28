"""Standalone disposable before/after UI fixture; loopback only, no scheduler/API."""
import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
import json

repo=Path(os.environ.get('TLC_FIXTURE_REPO',Path(__file__).resolve().parents[2]))
sys.path[:0]=[str(repo),str(repo/'tests')]
for key in list(os.environ):
    if key.startswith(('SHOPIFY_','WOO_','DISCORD_')):os.environ.pop(key)
os.environ['DISCORD_ENABLED']='false'
import pytest
import requests
from flask import request,session,abort
from app import db
from app.models import Product,User,ShopifyPublishJob,ShopifyPublishingEvent,CatalogueOperation,ScheduledTask,ScheduledTaskRun
from test_phase3_woo_publish_preview import preview_app
from test_shopify_drafts import world

requests.sessions.Session.request=lambda *a,**k:(_ for _ in ()).throw(AssertionError('Network forbidden'))
patch=pytest.MonkeyPatch()
fixture=preview_app.__wrapped__(Path(tempfile.mkdtemp(prefix='tlc-operations-acceptance-')),patch)
app=next(fixture)
product_fixture=world.__wrapped__(app,patch)
product,folder,shared=next(product_fixture)
with app.app_context():
    db.session.get(User,1).is_admin=True
    for i in range(30):
        if i:
            p=Product(sku=f'FIXTURE-{i:03}',title=f'Fixture Card {i:03}',product_type='simple',catalogue_status='active',
                collection_id=product.collection_id,source_relpath=f'Preview Cards/Fixture {i}')
            db.session.add(p);db.session.flush()
        else:p=product
        stamp=datetime(2026,10,27,10)-timedelta(minutes=i)
        op=CatalogueOperation(id=f'fixture-event-{i}',operation_type='shopify_draft_publish',status='succeeded' if i%3 else 'partial',
            recovery_state='none' if i%3 else 'review_required',started_at=stamp,finished_at=stamp+timedelta(seconds=2),
            scope=json.dumps({'product_id':p.id,'sku':p.sku,'fixture_only':True,'automatic_publishing':{'trigger':'hourly'},
                'shopify_discord_events':{'verified_success':{'state':'sent'}}}))
        db.session.add(op)
        db.session.add(ShopifyPublishJob(product_id=p.id,source_identity=p.source_relpath,requested_revision='unscanned',
            trigger='bootstrap',state='attention',next_attempt=stamp,updated_at=stamp,
            error='A completed collection scan is required before automatic publishing.',
            view_json=json.dumps({'display_title':p.title,'authored':{'live':True,'shopify_status':'ACTIVE'}})))
        db.session.add(ShopifyPublishingEvent(id=f'fixture-publishing-{i}',operation_id=op.id,product_id=p.id,event_type='verified',trigger='hourly',
            occurred_at=stamp,receipt_json=json.dumps({'fixture_only':True,'title':p.title,'sku':p.sku,'mode':'update',
                'verification_scope':'Synthetic offline fixture only','changes':[{'field':'price','label':'Price','before':'£4.50','after':'£5.00'}]})))
    for task in ScheduledTask.query.all():
        task.last_started=datetime(2026,10,26,12,15);task.last_completed=datetime(2026,10,26,12,15,3)
        task.next_due=datetime(2026,10,28,12);task.duration_seconds=3;task.last_outcome='completed'
    for key in ('shopify_publisher','shopify_products'):
        db.session.add(ScheduledTaskRun(id='fixture-cycle-'+key, task_key=key, trigger='scheduled',
            state='completed',created_at=datetime(2026,10,28,14),started_at=datetime(2026,10,28,14),
            completed_at=datetime(2026,10,28,14,0,1),duration_seconds=1,
            data_json=json.dumps({'counts':{'considered':0,'checked':0,'api_requests':0},
                'items':[], 'eligibility':{'missing_checkpoint':4,'already_matched':2,'draft_paused':9},
                'read_only':key=='shopify_products','reason':'No eligible publishing work; checkpoint validation required.'})))
    db.session.commit()


@app.before_request
def login():
    if request.remote_addr not in {'127.0.0.1','::1'}:abort(403)
    session['_user_id']='1';session['_fresh']=True


if __name__=='__main__':
    app.run(host='127.0.0.1',port=int(os.environ.get('TLC_FIXTURE_PORT','7518')),use_reloader=False)
