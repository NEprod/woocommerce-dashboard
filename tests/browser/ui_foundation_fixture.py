"""Batch 1 CUA fixture: isolated files/SQLite; all outbound requests forbidden."""
import os
import sys
import json
import tempfile
from pathlib import Path

for key in list(os.environ):
    if key.startswith(('SHOPIFY_', 'TLC_DEPLOYMENT_', 'TLC_PHOTO_', 'TLC_PROMOTIONS_')) or key in {'PRODUCT_FOLDER', 'OUTPUT_FOLDER', 'INTAKE_ROOT'}:
        os.environ.pop(key)
os.environ['SECRET_KEY'] = 'fictional-ui-foundation-browser-only-0000000000000'
os.environ['DISCORD_ENABLED'] = 'false'
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
import pytest
import requests
from flask import abort, request, session
import app as module
from app import db
from app.models import User
from test_metadata_editor_core import core

temporary = Path(tempfile.mkdtemp(prefix='tlc-ui-foundation-'))
instance = temporary / 'private-instance'
instance.mkdir()
real_flask = module.Flask
module.Flask = lambda *a, **kw: real_flask(*a, instance_path=str(instance), **kw)
requests.sessions.Session.request = lambda *a, **kw: (_ for _ in ()).throw(AssertionError('No outbound requests in UI fixture'))
patch = pytest.MonkeyPatch()
fixture = core.__wrapped__(temporary, patch)
app, _, catalogue, _, ids, *_ = next(fixture)
app.jinja_env.auto_reload = True
with app.app_context():
    user_id = User.query.one().id
source = catalogue / 'Simple Collection/product_info.json'
value = json.loads(source.read_text())
value.update(description='<p>A fictional full-width <strong>HTML description</strong> for responsive editing.</p>',
    shopify_channels={'online_store': True, 'shop-72': True, 'pos': True, 'unavailable-fixture-channel': True},
    shopify_market_ids=['gid://shopify/Market/999999'],
    customisation={'fields': {'recipient': {'type': 'text', 'label': 'Recipient name', 'required': True}}})
source.write_text(json.dumps(value))

@app.before_request
def login_fixture():
    if request.remote_addr not in {'127.0.0.1', '::1'}:
        abort(403)
    session['_user_id'] = str(user_id)
    session['_fresh'] = True

if __name__ == '__main__':
    print(json.dumps({'synthetic': True, 'products': ids, 'temporary': str(temporary)}), flush=True)
    app.run(host='127.0.0.1', port=7517, use_reloader=False)
