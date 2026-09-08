import pytest
from app.taxonomy_sync_view import sync_views
from app import woo_taxonomy_sync as sync
from test_woo_taxonomy_sync import world, registry_app, field


def row(key, kind='categories', parent=None, scope='', action='create', state='local_only'):
    return dict(id=f'{kind}:{scope}:{key}', key=key, kind=kind, name=key, scope=scope,
                local={'parent': parent}, action=action, state=state, reason='Reviewed status')


def test_hierarchy_scope_and_action_summaries():
    rows = [row('grandchild', parent='child', action=None, state='stale'),
            row('child', parent='parent', action='link', state='safe_match'), row('parent'),
            row('Size', 'attributes', action=None, state='verified'),
            row('Small', 'terms', scope='Size'), row('Finish', 'attributes'),
            row('Matte', 'terms', scope='Finish', action=None, state='conflict')]
    views = sync_views({'rows': rows})
    parent = views[0]['roots'][0]
    assert parent['row']['key'] == 'parent'
    assert parent['children'][0]['children'][0]['row']['key'] == 'grandchild'
    assert parent['counts'] == {'link': 1, 'create': 1, 'order': 0, 'verified': 0, 'issues': 1, 'import': 0}
    attrs = {n['row']['key']: n for n in views[1]['roots']}
    assert attrs['Size']['children'][0]['row']['key'] == 'Small'
    assert attrs['Finish']['children'][0]['bucket'] == 'issues'


def test_backend_cap_exactly_fifty():
    rows = [row(str(i)) for i in range(51)]
    assert len(sync.selected({'rows': rows}, [r['id'] for r in rows[:50]])) == 50
    with pytest.raises(sync.SyncError, match='50'):
        sync.selected({'rows': rows}, [r['id'] for r in rows])
    rows[0]['action'] = None
    with pytest.raises(sync.SyncError, match='unavailable'):
        sync.selected({'rows': rows}, [rows[0]['id']])


def test_pages_no_reads_preview_unavailable_and_review_ids(world):
    app, client, root, data, fake = world
    for view in ('categories', 'attributes', 'storefront_collections'):
        page = client.get('/taxonomy/sync/' + view)
        assert page.status_code == 200
        assert f'data-active-view="{view}"' in page.text
    assert not fake.methods
    fake.brands = False
    preview = client.post('/taxonomy/sync/preview', data={'csrf_token': field(page, 'csrf_token'), 'view': 'categories'})
    assert preview.status_code == 200
    assert 'data-limit="50"' in preview.text
    assert 'Needs Link' in preview.text and 'Needs Create' in preview.text
    assert 'Sync attribute first' in preview.text and 'Unavailable' in preview.text
    assert 'name="selected" value="categories::notelets"' not in preview.text
    assert 'data-group-action="create"' in preview.text
    review = client.post('/taxonomy/sync/review', data={'csrf_token': field(preview, 'csrf_token'),
                         'review': field(preview, 'review'), 'selected': ['categories::cards']})
    assert review.status_code == 200
    assert not fake.writes


def test_remote_hierarchy_and_import_separation():
    parent = row('parent', action='import', state='woo_only')
    child = row('child', action=None, state='conflict')
    parent.update(id='remote:1', remote={'id': 1, 'parent': 0})
    child.update(id='remote:2', remote={'id': 2, 'parent': 1})
    view = sync_views({'rows': [child, parent]})[0]
    assert view['roots'][0]['children'][0]['row']['id'] == 'remote:2'
    assert view['counts']['import'] == 1 and view['counts']['issues'] == 1
