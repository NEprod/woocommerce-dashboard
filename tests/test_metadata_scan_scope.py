"""Save dispatch uses the existing collection scan; no live catalogue or Shopify."""
import json
import time
from pathlib import Path

import pytest

from app import db
from app.models import Product
from app.utils.scan_runner import build_scan_scope
from test_metadata_editor_core import core, payload, save


@pytest.mark.parametrize('name,kind', [
    ('Simple Collection/Blue Token', 'override'),
    ('Variable Collection/Badge One', 'override'),
    ('Simple Collection/Blue Token', 'shared'),
    ('Variable Collection/Badge One', 'shared'),
    ('Single Variable', 'shared')])
def test_both_save_owners_dispatch_one_existing_collection_scan(core, name, kind):
    result = save(core, name, kind=kind, data={'description': 'Scoped edit'})
    assert result.status_code == 200, result.json
    calls = core[5]
    assert len(calls) == 1
    call = calls[0]
    collection = name.split('/')[0]
    assert call['scan_mode'] == 'shared_collection'
    assert call['collection_relpath'] == collection
    assert call['scope']['scope_kind'] == 'collection'
    assert call['scope']['kind'] == kind
    plan = build_scan_scope(str(core[2]), call['scan_mode'], collection_relpath=call['collection_relpath'])
    assert plan.complete and plan.collection_paths == (str(core[2]/collection),)
    assert str(core[2]) not in plan.collection_paths


@pytest.mark.parametrize('name', ['Simple Collection/Blue Token', 'Variable Collection/Badge One'])
def test_actual_override_save_reprojects_only_its_collection(core, monkeypatch, name):
    from app.utils import scan_runner
    from app.taxonomy_assignments import product_document
    from app.shopify_products import source_fingerprint
    app, client, root, _, ids, *_ = core
    collection = name.split('/')[0]
    staging = root/'ZZ_STAGING'; staging.mkdir()
    (staging/'product_info.json').write_text('Not valid catalogue JSON')
    untouched = {path: path.read_bytes() for path in root.rglob('*')
                 if path.is_file() and path.relative_to(root).parts[0] != collection}
    shared = root/collection/'product_info.json'; before_shared = shared.read_bytes()
    with app.app_context():
        product = db.session.get(Product, ids[name])
        before_ids = [(p.id, p.sku, [(v.id, v.sku) for v in p.variations]) for p in Product.query.all()]
        before_fingerprint = source_fingerprint(product, [])
        inherited_weight = product_document(product)['weight']
    calls = []
    original = scan_runner.scan_collection
    def bounded(folder, *args, **kwargs):
        calls.append(str(Path(folder).resolve()))
        assert Path(folder).resolve() == root/collection
        return original(folder, *args, **kwargs)
    monkeypatch.setattr(scan_runner, 'scan_collection', bounded)
    monkeypatch.setattr('app.routes.start_scan', scan_runner.start_scan)
    monkeypatch.setattr('app.shopify_connection.ShopifyAdapter._request',
                        lambda *a, **k: pytest.fail('Local scan contacted Shopify'))
    result = save(core, name, data={'description': 'Collection-scoped product edit'})
    assert result.status_code == 200, result.json
    deadline = time.monotonic()+15
    while time.monotonic() < deadline:
        with app.app_context(): progress = scan_runner.get_progress(result.json['run_id'])
        if progress['status'] in {'done', 'error'}: break
        time.sleep(.03)
    assert progress['status'] == 'done', progress
    assert calls == [str(root/collection)]
    assert progress['summary']['folders'] == 1
    assert all(path.read_bytes() == content for path, content in untouched.items())
    assert shared.read_bytes() == before_shared
    with app.app_context():
        db.session.expire_all()
        product = db.session.get(Product, ids[name])
        assert product.description == 'Collection-scoped product edit'
        assert product_document(product)['weight'] == inherited_weight
        assert source_fingerprint(product, []) != before_fingerprint
        assert before_ids == [(p.id, p.sku, [(v.id, v.sku) for v in p.variations]) for p in Product.query.all()]
    assert client.get(f'/edit_products/{ids[name]}/edit/override').status_code == 200
    assert json.loads((root/name/'product_info.json').read_text())['description'] == 'Collection-scoped product edit'


@pytest.mark.parametrize('bad_context', ['other_collection', 'catalogue_root', 'missing', 'traversal', 'outside_symlink'])
def test_invalid_collection_context_never_saves_or_falls_back_to_full_scan(core, bad_context):
    app, client, root, _, ids, *_ = core
    sku, body = payload(core, 'Simple Collection/Blue Token', data={'description': 'Must not save'})
    target = root/'Simple Collection/Blue Token/product_info.json'; before = target.read_bytes()
    if bad_context == 'other_collection':
        folder = root/'Unrelated'; folder.mkdir()
        (folder/'product_info.json').write_bytes((root/'Simple Collection/product_info.json').read_bytes())
        reference = 'Unrelated/product_info.json'
    elif bad_context == 'catalogue_root':
        (root/'product_info.json').write_bytes((root/'Simple Collection/product_info.json').read_bytes())
        reference = 'product_info.json'
    elif bad_context == 'missing': reference = 'Missing/product_info.json'
    elif bad_context == 'outside_symlink':
        outside = root.parent/'outside'; outside.mkdir()
        (outside/'product_info.json').write_bytes((root/'Simple Collection/product_info.json').read_bytes())
        (root/'Escaped').symlink_to(outside, target_is_directory=True)
        reference = 'Escaped/product_info.json'
    else: reference = '../outside/product_info.json'
    with app.app_context():
        product = db.session.get(Product, ids['Simple Collection/Blue Token'])
        product.shared_json_relpath = reference
        db.session.commit()
    # For safe but wrong existing sources, obtain a fresh token to exercise the
    # scope validation rather than relying only on stale-review protection.
    if bad_context in {'other_collection', 'catalogue_root'}:
        sku, body = payload(core, 'Simple Collection/Blue Token', data={'description': 'Must not save'})
    response = client.post(f'/edit_products/{sku}/save', json=body)
    assert response.status_code in {400, 409}, response.json
    assert response.json.get('message')
    assert not core[5] and target.read_bytes() == before


def test_active_collection_operation_prevents_duplicate_save_scan(core):
    from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation
    app, client, root, *_ = core
    sku, body = payload(core, 'Simple Collection/Blue Token', data={'description': 'Wait for scan'})
    target = root/'Simple Collection/Blue Token/product_info.json'; before = target.read_bytes()
    with app.app_context():
        lease = acquire_catalogue_operation('shared_collection_update', {'collection_relpath': 'Simple Collection'})
    try:
        response = client.post(f'/edit_products/{sku}/save', json=body)
        assert response.status_code == 409 and response.json['error'] == 'catalogue_operation_active'
        assert not core[5] and target.read_bytes() == before
    finally:
        with app.app_context(): finish_catalogue_operation(lease.id, status='succeeded')
