import copy
import json
import pytest
from app import woo_taxonomy_sync as sync, taxonomy_workspace as registry
from app.models import CatalogueOperation, WooTaxonomyIdentity
from app.woocommerce_connection import WooConnectionError
from test_woo_taxonomy_sync import world, registry_app, entry, finish, field


def test_order_projection_reviewed_correction_and_scope(world):
    app, client, root, data, fake = world
    data['attributes'][0]['terms'] = [entry('first', order=3), entry('second')]
    (root/'registry.json').write_bytes(registry.validate(data))
    finish(client, ['attributes::finish'])
    assert fake.writes[-1][2]['order_by'] == 'menu_order'
    with app.app_context():
        plan = sync.plan(fake)
        terms = {r['key']: r for r in plan['rows'] if r['kind'] == 'terms'}
        assert terms['first']['payload']['menu_order'] == 3
        assert terms['second']['payload']['menu_order'] == 1
    finish(client, ['terms:finish:first', 'terms:finish:second'])
    fake.taxonomy['terms:11'][0]['menu_order'] = 99
    fake.taxonomy['terms:999'] = [dict(fake.taxonomy['terms:11'][0])]
    with app.app_context():
        row = next(r for r in sync.plan(fake)['rows'] if r['id'] == 'terms:finish:first')
        assert row['state'] == 'ordering_drift' and row['action'] == 'order'
    finish(client, ['terms:finish:first'])
    assert fake.writes[-1] == ('PUT', '/wp-json/wc/v3/products/attributes/11/terms/81', {'menu_order': 3})
    assert fake.taxonomy['terms:999'][0]['menu_order'] == 99
    with app.app_context():
        assert next(r for r in sync.plan(fake)['rows'] if r['id'] == 'terms:finish:first')['state'] == 'verified'


def test_attribute_order_correction_gates_terms(world):
    app, client, root, data, fake = world
    finish(client, ['attributes::finish'])
    fake.taxonomy['attributes'][0]['order_by'] = 'name'
    with app.app_context():
        plan = sync.plan(fake)
        assert any(r['action'] == 'order' for r in plan['rows'])
        assert any(r['kind'] == 'terms:finish' for r in plan['unavailable'])
    finish(client, ['attributes::finish'])
    assert fake.writes[-1][2] == {'order_by': 'menu_order'}


def test_parent_dependency_does_not_emit_false_import_and_hierarchy_stays_strict(world):
    app, client, root, data, fake = world
    fake.taxonomy['categories'] = [dict(id=31, name='Cards', slug='cards', parent=0),
                                   dict(id=32, name='Notelets', slug='notelets', parent=31)]
    with app.app_context():
        plan = sync.plan(fake)
        assert not any(r['id'] == 'remote:categories::32' for r in plan['rows'])
        child = next(r for r in plan['rows'] if r['key'] == 'notelets')
        assert 'parent cards' in child['reason'] and child['action'] is None
    finish(client, ['categories::cards'])
    with app.app_context():
        assert next(r for r in sync.plan(fake)['rows'] if r['key'] == 'notelets')['action'] == 'link'
    fake.taxonomy['categories'][1]['parent'] = 0
    with app.app_context():
        child = next(r for r in sync.plan(fake)['rows'] if r['key'] == 'notelets')
        assert child['state'] == 'conflict'
        assert 'parent mismatch: expected 31, observed 0' in child['reason']


def test_fifty_reviewed_all_attempted(world):
    app, client, root, data, fake = world
    data['categories'] = [entry(f'category-{i:02}', parent=None) for i in range(50)]
    (root/'registry.json').write_bytes(registry.validate(data))
    with app.app_context():
        plan = sync.plan(fake)
        ids = [r['id'] for r in plan['rows'] if r['kind'] == 'categories']
        sync.execute(plan['digest'], ids, fake)
        report = json.loads(CatalogueOperation.query.order_by(CatalogueOperation.id.desc()).first().scope)['operation_summary']
        assert len(report['succeeded']) == 50 and report['pending'] == []
        assert report['counts'] == dict(selected=50, attempted=50, succeeded=50, failed=0, uncertain=0, not_attempted=0)
        assert WooTaxonomyIdentity.query.count() == 50
    assert len(fake.writes) == 50


@pytest.mark.parametrize('method', ['GET', 'POST', 'PUT'])
def test_bounded_safe_retry_only_get(world, monkeypatch, method):
    app, client, root, data, fake = world
    calls, delays = [], []
    def fail(*args, **kwargs):
        calls.append(args[0])
        raise WooConnectionError('server_error', 'unsafe remote message', status_code=503)
    monkeypatch.setattr(fake, 'request_json', fail)
    monkeypatch.setattr(sync.time, 'sleep', delays.append)
    with pytest.raises(sync.SyncError):
        sync.API(fake).request(method, 'products/attributes/1' if method == 'PUT' else 'products/categories', body={'order_by': 'menu_order'} if method == 'PUT' else {'name': 'Fictional'})
    assert len(calls) == (2 if method == 'GET' else 1)
    assert delays == ([0.25] if method == 'GET' else [])


def test_later_uncertain_create_preserves_first_and_pending(world, monkeypatch):
    app, client, root, data, fake = world
    data['categories'] = [entry(f'category-{i}', parent=None) for i in range(3)]
    (root/'registry.json').write_bytes(registry.validate(data))
    request = fake.request_json
    def fail_second(method, url, **kwargs):
        if method == 'POST' and len(fake.writes) == 1:
            fake.uncertain = True
        return request(method, url, **kwargs)
    monkeypatch.setattr(fake, 'request_json', fail_second)
    with app.app_context():
        plan = sync.plan(fake)
        ids = [r['id'] for r in plan['rows'] if r['kind'] == 'categories']
        with pytest.raises(sync.SyncError, match='Uncertain'):
            sync.execute(plan['digest'], ids, fake)
        report = json.loads(CatalogueOperation.query.order_by(CatalogueOperation.id.desc()).first().scope)['operation_summary']
        assert report['succeeded'] == ids[:1] and report['uncertain'] == ids[1] and report['pending'] == ids[2:]
        assert report['counts'] == dict(selected=3, attempted=2, succeeded=1, failed=0, uncertain=1, not_attempted=1)
        assert WooTaxonomyIdentity.query.filter_by(state='verified').count() == 1
    assert len(fake.writes) == 2


def test_order_readback_must_match(world, monkeypatch):
    app, client, root, data, fake = world
    finish(client, ['attributes::finish'])
    finish(client, ['terms:finish:matte'])
    fake.taxonomy['terms:11'][0]['menu_order'] = 8
    request = fake.request_json
    def ignore_update(method, url, **kwargs):
        if method == 'PUT':
            return copy.deepcopy(fake.taxonomy['terms:11'][0]), object()
        return request(method, url, **kwargs)
    monkeypatch.setattr(fake, 'request_json', ignore_update)
    with app.app_context():
        plan = sync.plan(fake)
        with pytest.raises(sync.SyncError, match='ordering readback'):
            sync.execute(plan['digest'], ['terms:finish:matte'], fake)


def test_only_narrow_order_updates_allowed(world):
    fake = world[-1]
    for method, route, body in [('DELETE', 'products/attributes/1', {}),
                                ('PUT', 'products/categories/1', {'menu_order': 3}),
                                ('PUT', 'products/attributes/1', {'name': 'Changed'})]:
        with pytest.raises(sync.SyncError):
            sync.API(fake).request(method, route, body=body)
    assert not fake.methods


def test_import_review_document_includes_all_selected(world):
    app, client, root, data, fake = world
    fake.taxonomy['brands'] = [dict(id=i, name=f'Range {i}', slug=f'range-{i}') for i in range(1, 7)]
    ids = [f'remote:storefront_collections::{i}' for i in range(1, 7)]
    review = finish(client, ids)
    assert 'attempts all 6 reviewed definitions' in review.text
    actual, _, _ = None, None, None
    with app.app_context():
        actual, _, _ = registry.current_source()
    assert len(actual['storefront_collections']) == 6
    assert 'range-6' in review.text.split('Exact proposed registry replacement')[1].split('</pre>')[0]
    assert not fake.writes


def test_transient_get_recovers_once(world, monkeypatch):
    fake = world[-1]
    calls = []
    original = fake.request_json
    def transient(method, url, **kwargs):
        calls.append(method)
        if len(calls) == 1:
            raise WooConnectionError('connection_failed', 'reset')
        return original(method, url, **kwargs)
    monkeypatch.setattr(fake, 'request_json', transient)
    monkeypatch.setattr(sync.time, 'sleep', lambda delay: None)
    assert sync.API(fake).read('products/categories', 'categories') == []
    assert calls == ['GET', 'GET']


def test_precise_name_slug_ownership_and_stored_identity_diagnostics(world):
    app, client, root, data, fake = world
    finish(client, ['categories::cards'])
    fake.taxonomy['categories'].append(dict(id=32, name='Notelets', slug='notelets', parent=11))
    with app.app_context():
        plan = sync.plan(fake)
        child = next(r for r in plan['rows'] if r['key'] == 'notelets')
        assert child['action'] == 'link'
        fake.taxonomy['categories'][-1]['name'] = 'Different wording'
        reason = next(r for r in sync.plan(fake)['rows'] if r['key'] == 'notelets')['reason']
        assert 'name mismatch' in reason and 'parent mismatch' not in reason and 'ownership conflicts' not in reason
        fake.taxonomy['categories'][-1]['name'] = 'Notelets'
        fake.taxonomy['categories'][-1]['slug'] = 'different-slug'
        reason = next(r for r in sync.plan(fake)['rows'] if r['key'] == 'notelets')['reason']
        assert 'slug mismatch' in reason and 'parent mismatch' not in reason
        fake.taxonomy['categories'][-1]['slug'] = 'notelets'
        owner = copy.deepcopy(child)
        owner['key'] = 'another-local-key'
        sync.persist(owner, plan['store']['key'], fake.taxonomy['categories'][-1])
        reason = next(r for r in sync.plan(fake)['rows'] if r['key'] == 'notelets')['reason']
        assert 'Woo ID 32 is already linked/reserved to local key another-local-key' in reason
        assert 'parent mismatch' not in reason
        # A stale local mapping is distinct from remote ownership.
        sync.persist(child, plan['store']['key'], dict(id=88, name='Notelets', slug='notelets', parent=11))
        reason = next(r for r in sync.plan(fake)['rows'] if r['key'] == 'notelets')['reason']
        assert 'points to Woo ID 88; exact current match IDs: [32]' in reason
        assert 'missing from current discovery' in reason


def test_parent_reuse_does_not_remove_independent_child_readback(world, monkeypatch):
    app, client, root, data, fake = world
    data['attributes'][0]['terms'] = [entry(f'term-{i}') for i in range(7)]
    (root/'registry.json').write_bytes(registry.validate(data))
    finish(client, ['attributes::finish'])
    calls = []
    original = fake.request_json
    def record(method, url, **kwargs):
        calls.append((method, url.split('?')[0]))
        return original(method, url, **kwargs)
    monkeypatch.setattr(fake, 'request_json', record)
    with app.app_context():
        plan = sync.plan(fake)
        calls.clear()
        report = sync.execute(plan['digest'], [r['id'] for r in plan['rows'] if r['kind'] == 'terms'], fake, return_report=True)
        assert report['counts']['succeeded'] == 7
    assert sum(method == 'GET' and url.endswith('/products/attributes/11') for method, url in calls) == 1
    assert sum(method == 'GET' and '/terms/' in url for method, url in calls) == 7
    assert sum(method == 'POST' and url.endswith('/terms') for method, url in calls) == 7


def test_failure_summary_renders_earlier_success_and_not_attempted(world, monkeypatch):
    app, client, root, data, fake = world
    data['categories'] = [entry(f'category-{i}', parent=None) for i in range(3)]
    (root/'registry.json').write_bytes(registry.validate(data))
    page = client.get('/taxonomy/sync')
    preview = client.post('/taxonomy/sync/preview', data={'csrf_token': field(page,'csrf_token')})
    review = client.post('/taxonomy/sync/review', data={'csrf_token':field(preview,'csrf_token'), 'review':field(preview,'review'), 'selected':[f'categories::category-{i}' for i in range(3)]})
    original = fake.request_json
    def conflict_after_first(method, url, **kwargs):
        if len(fake.writes) == 1 and method == 'GET' and 'categories?' in url:
            fake.taxonomy['categories'].append(dict(id=99,name='Category-1',slug='category-1',parent=0))
        return original(method,url,**kwargs)
    monkeypatch.setattr(fake,'request_json',conflict_after_first)
    response = client.post('/taxonomy/sync/confirm', data={'csrf_token':field(review,'csrf_token'),'review':field(review,'review'),'acknowledge':'yes'})
    assert response.status_code == 409
    for label in ['Selected: 3','Attempted: 2','Succeeded: 1','Failed: 1','Uncertain: 0','Not Attempted: 1']:
        assert label in response.text
    assert 'Woo match appeared' in response.text
    assert len(fake.writes) == 1
