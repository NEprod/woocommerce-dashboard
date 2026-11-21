"""Fictional isolated inventory UI, loopback only, all outbound HTTP forbidden."""
import os
from pathlib import Path
import sys
import tempfile

for key in list(os.environ):
    if key.startswith(('SHOPIFY_', 'TLC_')):
        os.environ.pop(key)
os.environ['SECRET_KEY'] = 'isolated-production-browser-000000000000000000'
os.environ['DISCORD_ENABLED'] = 'false'
root = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(root), str(root / 'tests')]
import pytest
import requests
from flask import abort, request, session
from test_setup_flow import setup_app
from test_inventory import workshop, client_for, item, manufactured, location
from app import db
from app.inventory import service as stock

requests.sessions.Session.request = lambda *a, **k: (_ for _ in ()).throw(AssertionError('Outbound denied'))
directory = Path(tempfile.mkdtemp(prefix='tlc-production-browser-'))
patch = pytest.MonkeyPatch()
fixture = setup_app.__wrapped__(directory, patch)
configured = next(fixture)
app = workshop.__wrapped__(configured)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True


@app.before_request
def fixture_login():
    if request.remote_addr not in {'127.0.0.1', '::1'}:
        abort(403)
    session['_user_id'], session['_fresh'] = '1', True


client = client_for(app)
raw = item(client, 'material', 'PLA-TEST', 'PLA fictional', unit='g', pack_qty='1000', pack_price='20')
component = manufactured(client, [(raw, '220', 'g')])
location(client, 'TUB-TEST', 'Fictional tub')
with app.app_context():
    stock.reserve_batches(component, 2, 'a' * 32)
    db.session.commit()


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=7513, use_reloader=False)
