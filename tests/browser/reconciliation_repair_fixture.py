"""Loopback-only real task history/Products, synthetic evidence, no network."""
import json
import os
import sys
import tempfile
from pathlib import Path

for key in list(os.environ):
    if key.startswith('SHOPIFY_'):
        os.environ.pop(key)
os.environ['SECRET_KEY'] = 'fictional-reconciliation-browser-secret-000000000000'
os.environ['DISCORD_ENABLED'] = 'false'
repo = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(repo), str(repo / 'tests')]
import pytest
import requests
from flask import abort, request, session
from app import db, shopify_drafts as drafts, scheduled_tasks as tasks
from app.models import ScheduledTaskRun
from test_phase3_woo_publish_preview import preview_app
from test_shopify_drafts import world
from test_shopify_package_assignments import existing, linked
from test_shopify_simple_updates import change

requests.sessions.Session.request = lambda *a, **k: (_ for _ in ()).throw(AssertionError('Outbound network denied'))
patch = pytest.MonkeyPatch()
root = Path(tempfile.mkdtemp(prefix='tlc-reconciliation-browser-'))
fixture = preview_app.__wrapped__(root, patch)
app = next(fixture)
catalogue_fixture = world.__wrapped__(app, patch)
catalogue = next(catalogue_fixture)
with app.app_context():
    product, row, adapter = existing(catalogue)
    pkg = linked()
    change(catalogue, packaging={'selected_package_id': pkg.id})
    _, token = drafts.preview(product, adapter)
    drafts.confirm(token, True, adapter)
    drafts.preview(product, adapter)
    reason = 'Required managed evidence is incomplete: publication: Synthetic channel membership readback incomplete.'
    data = {'counts': dict.fromkeys(tasks.COUNTERS, 0), 'queue': [], 'items': [], 'reason': None}
    data['counts'].update(considered=15, checked=10, unavailable=10, skipped_recent=5)
    tasks.group_errors(data, [{'message': reason}] * 10)
    for i in range(10):
        data['items'].append({'id': i + 1, 'sku': f'FICTIONAL-{i+1}', 'state': 'Verification Unavailable', 'reason': reason})
    db.session.add(ScheduledTaskRun(id='a' * 32, task_key=tasks.KEY, trigger='manual', state='partially_completed',
        created_at=drafts.now(), started_at=drafts.now(), completed_at=drafts.now(), duration_seconds=1,
        data_json=json.dumps(data), error=data['errors'][0]))
    db.session.commit()


@app.before_request
def fixture_login():
    if request.remote_addr not in {'127.0.0.1', '::1'}:
        abort(403)
    session['_user_id'] = '1'
    session['_fresh'] = True


if __name__ == '__main__':
    print(json.dumps({'fixture': str(root), 'history': '/settings/tasks/runs/' + 'a' * 32}), flush=True)
    app.run(host='127.0.0.1', port=7497, use_reloader=False)
