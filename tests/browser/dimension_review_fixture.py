"""Loopback-only real Review template, synthetic catalogue and Shopify evidence."""
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

for key in list(os.environ):
    if key.startswith('SHOPIFY_'):
        os.environ.pop(key)
os.environ['SECRET_KEY'] = 'fictional-dimension-review-browser-secret-000000000000'
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
from test_shopify_simple_updates import created, change
from test_shopify_variables import variable_world, VariableAdapter
from app.packaging_catalogue import initial_rows

requests.sessions.Session.request = lambda *a, **k: (_ for _ in ()).throw(AssertionError('Outbound network denied'))
patch = pytest.MonkeyPatch()
directory = Path(tempfile.mkdtemp(prefix='tlc-dimension-review-browser-'))
app_fixture = preview_app.__wrapped__(directory, patch)
app = next(app_fixture)
catalogue_fixture = world.__wrapped__(app, patch)
catalogue = next(catalogue_fixture)
with app.app_context():
    product, row, adapter = created(catalogue)
    change(catalogue, dimensions={'length': '155', 'width': '155', 'height': '3'},
        packaging={'selected_package_id': next(initial_rows())['id'], 'assignment_kind': 'accepted',
            'packed_dimensions': {'length': '16', 'width': '16', 'height': '.4', 'unit': 'cm'}, 'measured': True})
    for axis in ('length', 'width', 'height'):
        adapter.remote[axis] = None
    changed = drafts.preview(product, adapter)[0]
    # The variant page uses the actual existing variation projection, not a UI mock.
    variable = variable_world.__wrapped__(catalogue, SimpleNamespace(param='Variable Collection'))
    multiple = drafts.preview(variable[0], VariableAdapter())[0]
    for axis in ('length', 'width', 'height'):
        adapter.remote[axis] = next(m for m in changed['managed']['product']['metafields'] if m['key'] == 'product_' + axis)
    change(catalogue, packaging={'selected_package_id': next(initial_rows())['id'], 'assignment_kind': 'accepted'})
    local = drafts.preview(product, adapter)[0]


@app.before_request
def fixture_login():
    if request.remote_addr not in {'127.0.0.1', '::1'}:
        abort(403)
    session['_user_id'] = '1'
    session['_fresh'] = True


@app.get('/fixture/dimension-review/<kind>')
def dimension_review(kind):
    plan = {'changed': changed, 'local': local, 'variable': multiple}.get(kind)
    if plan is None: abort(404)
    return render_template('shopify_drafts.html', form=FlaskForm(), plan=plan, token=None,
        error=None, review_rows=review_rows(plan), return_to='/products', products=[], identities=[],
        scoped_product=None, selected_id=plan['product_id'])


if __name__ == '__main__':
    print(json.dumps({'fixture': str(directory), 'pages': ['changed', 'local', 'variable']}), flush=True)
    app.run(host='127.0.0.1', port=7498, use_reloader=False)
