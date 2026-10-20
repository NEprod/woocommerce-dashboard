"""Loopback-only synthetic reporting history; no Shopify/Discord network."""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

for key in list(os.environ):
    if key.startswith('SHOPIFY_'):
        os.environ.pop(key)
os.environ['SECRET_KEY'] = 'fictional-operations-browser-secret-000000000000'
os.environ['DISCORD_ENABLED'] = 'false'
repo = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(repo), str(repo / 'tests')]
import requests
from flask import abort, request, session
from app import db
from app.models import CatalogueOperation, Product, Variation
from test_phase2_dashboard import dashboard_app

requests.sessions.Session.request = lambda *a, **k: (_ for _ in ()).throw(AssertionError('Outbound network denied'))
fixture = dashboard_app.__wrapped__(Path(tempfile.mkdtemp(prefix='tlc-operations-browser-')))
app, _ = next(fixture)
with app.app_context():
    Product.query.update({Product.catalogue_status: 'active'})
    Variation.query.update({Variation.catalogue_status: 'active'})
    stamp = datetime.now()-timedelta(hours=3)
    for index in range(320):
        db.session.add(CatalogueOperation(id=f'legacy-warning-{index}', operation_type='append',
            status='partial', started_at=stamp+timedelta(seconds=index), finished_at=stamp+timedelta(seconds=index+1),
            products_attempted=1, products_succeeded=1, products_failed=0, marker_state='finalized', recovery_state='none',
            scope=json.dumps({'operation_summary': {'warnings': 1, 'warning_entries': ['Optional parent image absent']}})))
    for index in range(32):
        db.session.add(CatalogueOperation(id=f'old-read-{index}', operation_type='shopify_status_refresh',
            status='failed', started_at=stamp+timedelta(minutes=10, seconds=index),
            error='Synthetic required page failed', scope='{"products":[1]}', recovery_state='none'))
    db.session.add(CatalogueOperation(id='complete-read', operation_type='shopify_status_refresh', status='succeeded',
        started_at=stamp+timedelta(minutes=20), scope='{"products":[1],"operation_summary":{"execution_complete":true}}', recovery_state='none'))
    for index in range(7):
        db.session.add(CatalogueOperation(id=f'uncertain-{index}', operation_type='shopify_draft_publish',
            status='partial', started_at=stamp+timedelta(minutes=30, seconds=index),
            error='Synthetic uncertain mutation: inspect the original receipt before retry.', recovery_state='review_required'))
    db.session.commit()


@app.before_request
def fixture_login():
    if request.remote_addr not in {'127.0.0.1', '::1'}:
        abort(403)
    session['_user_id'] = '1'
    session['_fresh'] = True


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=7499, use_reloader=False)
