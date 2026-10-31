"""Disposable real Intake + catalogue; every outbound HTTP request is denied."""
import json
import os
from pathlib import Path
import sys
import tempfile

for key in list(os.environ):
    if key.startswith('SHOPIFY_') or key in {'PRODUCT_FOLDER', 'OUTPUT_FOLDER', 'INTAKE_ROOT'}:
        os.environ.pop(key)
os.environ['DISCORD_ENABLED'] = 'false'
os.environ['SECRET_KEY'] = 'isolated-intake-browser-secret-not-production'
root = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(root), str(root / 'tests')]
import pytest
import requests
from flask import abort, request, session
from test_intake_direct import workspace, image

requests.sessions.Session.request = lambda *a, **k: (_ for _ in ()).throw(AssertionError('Outbound network denied'))
directory = Path(tempfile.mkdtemp(prefix='tlc-intake-browser-'))
patch = pytest.MonkeyPatch()
fixture = workspace.__wrapped__(directory, patch)
app, _, intake, catalogue, output = next(fixture)
app.config['INTAKE_RUN_INLINE'] = False
for name in ['Dogs Desktop', 'Dogs Mobile']:
    for index, filename in enumerate(['hound 1.png', 'hound 2.png', 'beagle 1.png', 'beagle 2.png']):
        image(intake / name / filename, ['red', 'blue', 'green', 'yellow'][index])

@app.before_request
def login():
    if request.remote_addr not in {'127.0.0.1', '::1'}: abort(403)
    session['_user_id'] = '1'; session['_fresh'] = True

if __name__ == '__main__':
    print(json.dumps({'fixture': str(directory)}), flush=True)
    app.run(host='127.0.0.1', port=7518, use_reloader=False, threaded=True)
