"""Loopback synthetic variable review and real acknowledgement-receipt template."""
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

for key in list(os.environ):
    if key.startswith('SHOPIFY_'):
        os.environ.pop(key)
os.environ['SECRET_KEY'] = 'fictional-variant-sync-browser-secret-000000000000'
os.environ['DISCORD_ENABLED'] = 'false'
repo = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(repo), str(repo / 'tests')]
import pytest
import requests
from flask import abort, render_template, request, session
from flask_wtf import FlaskForm
from app import shopify_drafts as drafts
from app.shopify_simple_fields import review_rows
from test_phase3_woo_publish_preview import preview_app
from test_shopify_drafts import world
from test_shopify_variables import variable_world
from test_shopify_package_assignments import VariantAdapter, linked

requests.sessions.Session.request = lambda *a, **k: (_ for _ in ()).throw(AssertionError('Outbound network denied'))
patch = pytest.MonkeyPatch()
directory = Path(tempfile.mkdtemp(prefix='tlc-variant-sync-browser-'))
app_fixture = preview_app.__wrapped__(directory, patch)
app = next(app_fixture)
catalogue_fixture = world.__wrapped__(app, patch)
catalogue = next(catalogue_fixture)
with app.app_context():
    multiple = variable_world.__wrapped__(catalogue, SimpleNamespace(param='Variable Collection'))
    package = linked()
    doc = json.loads(multiple[1].read_text())
    doc['packaging'] = {'selected_package_id': package.id}
    multiple[1].write_text(json.dumps(doc))
    adapter = VariantAdapter()
    _, token = drafts.preview(multiple[0], adapter)
    row = drafts.confirm(token, True, adapter)
    row_id = row.id
    plan = drafts.preview(multiple[0], adapter)[0]


@app.before_request
def fixture_login():
    if request.remote_addr not in {'127.0.0.1', '::1'}:
        abort(403)
    session['_user_id'] = '1'
    session['_fresh'] = True


@app.get('/fixture/variant-review')
def review():
    return render_template('shopify_drafts.html', form=FlaskForm(), plan=plan, token=None,
        error=None, review_rows=review_rows(plan), return_to='/products', products=[], identities=[],
        scoped_product=None, selected_id=plan['product_id'])


@app.get('/fixture/package-receipt')
def receipt():
    from app import db
    from app.models import ShopifyProductIdentity
    row = db.session.get(ShopifyProductIdentity, row_id)
    return render_template('shopify_draft_identity.html', form=FlaskForm(), row=row,
        data=drafts.evidence(row), error=None, approval=None)


if __name__ == '__main__':
    print(json.dumps({'fixture': str(directory), 'pages': ['variant-review', 'package-receipt']}), flush=True)
    app.run(host='127.0.0.1', port=7499, use_reloader=False)
