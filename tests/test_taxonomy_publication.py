"""Taxonomy artwork and reviewed collection visibility, with fictional Shopify only."""
from io import BytesIO
import copy
import json

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage
from flask import current_app

from app import db, taxonomy_images as images, taxonomy_publishing as publishing
from app import shopify_taxonomy as taxonomy, taxonomy_workspace as workspace
from app.models import ShopifyCollectionIdentity
from app.shopify_connection import ShopifyError
from test_shopify_taxonomy_products import world, registry, TaxonomyAdapter
from test_phase3_woo_publish_preview import preview_app
from test_taxonomy_workspace import registry_app, entry, field


def photo():
    output = BytesIO()
    Image.new('RGBA', (1800, 900), (0, 100, 0, 128)).save(output, 'PNG')
    output.seek(0)
    return FileStorage(output, filename='collection.png')


class Adapter(TaxonomyAdapter):
    def __init__(self):
        super().__init__()
        self.channel_ids = ['gid://shopify/Publication/1', 'gid://shopify/Publication/2']
        self.visible = set()
        self.artwork = None
        self.image_hash = None
        self.fail_publish = False

    def test_connection(self, config):
        value = super().test_connection(config)
        value['scopes'] += ['read_publications', 'write_publications']
        return value

    def publications(self, config):
        return [{'id': p, 'channels': {'nodes': [{'name': 'Channel ' + p[-1]}],
            'pageInfo': {'hasNextPage': False}}} for p in self.channel_ids]

    def collection_live(self, config, remote_id):
        return {'id': remote_id, 'image': self.artwork, 'metafield': {'value': self.image_hash},
            'resourcePublicationsV2': {'nodes': [{'publication': {'id': p}, 'isPublished': p in self.visible}
                for p in self.channel_ids], 'pageInfo': {'hasNextPage': False}}}

    def publish_collection(self, config, remote_id, publications):
        self.calls.append('collection_publish')
        assert remote_id == 'gid://shopify/Collection/800'
        if not self.fail_publish:
            self.visible.update(publications)
        return {'publishablePublish': {'userErrors': []}}

    def stage_collection_image(self, config, image):
        self.calls.append('collection_stage')
        assert image['mime'] == 'image/webp'
        return {'stagedUploadsCreate': {'stagedTargets': [{'url': 'https://storage.googleapis.com/upload',
            'resourceUrl': 'https://storage.googleapis.com/artwork', 'parameters': []}], 'userErrors': []}}

    def set_collection_image(self, config, remote_id, source, alt, digest):
        self.calls.append('collection_image')
        self.artwork = {'url': source, 'altText': alt}
        self.image_hash = digest
        return {'collectionUpdate': {'userErrors': []}}


@pytest.fixture
def mapped(world, registry, tmp_path, monkeypatch):
    instance = tmp_path / 'image-instance'
    instance.mkdir()
    monkeypatch.setattr(current_app._get_current_object(), 'instance_path', str(instance))
    row = ShopifyCollectionIdentity.query.filter_by(store_id='gid://shopify/Shop/42',
        remote_id='gid://shopify/Collection/800').one()
    row.source_key = 'storefront_collections:personalised'
    db.session.commit()
    return Adapter(), registry


def test_artwork_and_all_channels_are_reviewed_and_verified(mapped):
    adapter, path = mapped
    data = json.loads(path.read_text())
    image = images.store(photo())
    data['storefront_collections'][0]['image_sha256'] = image
    path.write_bytes(workspace.validate(data))
    before = path.read_bytes()
    plan, token = publishing.review(['storefront_collections:personalised'], adapter)
    assert plan['groups'][0]['upload_image'] and len(plan['groups'][0]['missing']) == 2
    assert 'collection_publish' not in adapter.calls and 'collection_image' not in adapter.calls
    with pytest.raises(ShopifyError, match='approval'):
        publishing.confirm(token, False, adapter)
    publishing.confirm(token, True, adapter)
    assert adapter.visible == set(adapter.channel_ids)
    assert adapter.image_hash == image and path.read_bytes() == before
    assert Image.open(BytesIO(images.read(image))).size == (1600, 800)
    assert Image.open(BytesIO(images.read(image))).mode == 'RGBA'
    adapter.calls.clear()
    _, token = publishing.review(['storefront_collections:personalised'], adapter)
    publishing.confirm(token, True, adapter)
    assert not {'collection_publish', 'collection_image', 'collection_stage'} & set(adapter.calls)


def test_categories_publish_without_artwork(mapped):
    adapter, path = mapped
    row = taxonomy.mapping('gid://shopify/Shop/42', 'storefront_collections:personalised')
    row.source_key = 'categories:cards'
    db.session.commit()
    plan, token = publishing.review(['categories:cards'], adapter)
    assert not plan['groups'][0]['upload_image']
    publishing.confirm(token, True, adapter)
    assert adapter.visible == set(adapter.channel_ids)
    assert 'collection_stage' not in adapter.calls


def test_changed_channels_and_failed_readback_are_not_reported_success(mapped):
    adapter, _ = mapped
    _, token = publishing.review(['storefront_collections:personalised'], adapter)
    adapter.channel_ids.append('gid://shopify/Publication/3')
    with pytest.raises(ShopifyError, match='changed after review'):
        publishing.confirm(token, True, adapter)
    assert 'collection_publish' not in adapter.calls
    _, token = publishing.review(['storefront_collections:personalised'], adapter)
    adapter.fail_publish = True
    with pytest.raises(ShopifyError, match='Not every reviewed'):
        publishing.confirm(token, True, adapter)


def test_missing_mapping_and_image_path_escape_rejected(mapped):
    adapter, _ = mapped
    with pytest.raises(ShopifyError, match='create or explicitly link'):
        publishing.review(['categories:birthday'], adapter)
    with pytest.raises(ValueError, match='reference'):
        images.read('../private')


def test_missing_permission_and_incomplete_channels_block_writes(mapped):
    adapter, _ = mapped
    original = adapter.test_connection
    adapter.test_connection = lambda config: {**original(config), 'scopes': ['read_products']}
    with pytest.raises(ShopifyError, match='needs read_publications'):
        publishing.review(['storefront_collections:personalised'], adapter)
    adapter.test_connection = original
    adapter.publications = lambda config: [{'id': adapter.channel_ids[0], 'channels': {'nodes': []}}]
    with pytest.raises(ShopifyError, match='incomplete'):
        publishing.review(['storefront_collections:personalised'], adapter)
    assert 'collection_publish' not in adapter.calls


def test_publication_form_renders_and_uses_signed_confirmation(registry_app, monkeypatch):
    app, client, root, data = registry_app
    data['storefront_collections'] = [entry('seasonal')]
    (root / 'registry.json').write_bytes(workspace.validate(data))
    page = client.get('/taxonomy/shopify')
    assert page.status_code == 200 and b'Publish collections' in page.data
    calls = []
    plan = {'groups': [{'source': {'label': 'Seasonal'}, 'upload_image': False, 'missing': ['channel']}],
        'publications': [{'id': 'channel', 'name': 'Online Store'}]}
    monkeypatch.setattr(publishing, 'review', lambda keys: (calls.append(keys) or plan, 'signed-fixture'))
    response = client.post('/taxonomy/shopify', data={'csrf_token': field(page, 'csrf_token'),
        'action': 'publication_review', 'sources': ['storefront_collections:seasonal']})
    assert response.status_code == 200 and b'signed-fixture' in response.data
    assert b'Publish reviewed collections' in response.data and b'Online Store' in response.data
    assert calls == [['storefront_collections:seasonal']]


def test_upload_review_cancel_save_reopen_and_legacy_preservation(registry_app):
    app, client, root, data = registry_app
    data['storefront_collections'] = [entry('seasonal')]
    path = root / 'registry.json'
    path.write_bytes(workspace.validate(data))
    before = path.read_bytes()
    page = client.get('/taxonomy/edit/storefront_collections?key=seasonal')
    assert b'type="file"' in page.data and b'25 MB' in page.data
    result = client.post('/taxonomy/edit/storefront_collections?key=seasonal', data={
        'csrf_token': field(page, 'csrf_token'), 'base': field(page, 'base'),
        'name': 'Seasonal', 'slug': 'seasonal', 'state': 'active', 'action': 'save',
        'image': (photo().stream, 'collection.png')}, content_type='multipart/form-data')
    assert result.status_code == 200 and path.read_bytes() == before  # Cancel needs no rollback.
    document = field(result, 'document')
    digest = json.loads(document)['storefront_collections'][0]['image_sha256']
    response = client.post('/taxonomy/confirm', data={'csrf_token': field(result, 'csrf_token'),
        'review': field(result, 'review'), 'document': document, 'acknowledge': 'yes'})
    assert response.status_code == 302
    assert client.get('/taxonomy/images/' + digest + '.webp').mimetype == 'image/webp'
    assert digest.encode() in client.get('/taxonomy/edit/storefront_collections?key=seasonal').data
    reopened = json.loads(path.read_text())
    changed = workspace.edit_definition(copy.deepcopy(reopened), 'storefront_collections', 'seasonal', '',
        {'name': 'Renamed', 'slug': 'seasonal', 'state': 'active'})
    assert changed['storefront_collections'][0]['image_sha256'] == digest
    assert b'type="file"' not in client.get('/taxonomy/edit/categories?key=cards').data


def test_save_collection_persists_image_without_second_confirmation(registry_app):
    app, client, root, data = registry_app
    data['storefront_collections'] = [entry('seasonal')]
    path = root / 'registry.json'
    path.write_bytes(workspace.validate(data))
    url = '/taxonomy/edit/storefront_collections?key=seasonal'
    page = client.get(url)
    assert b'Save collection' in page.data
    response = client.post(url, data={'csrf_token': field(page, 'csrf_token'),
        'base': field(page, 'base'), 'name': 'Seasonal', 'slug': 'seasonal', 'state': 'active',
        'action': 'save_local', 'image': (photo().stream, 'collection.png')}, content_type='multipart/form-data')
    assert response.status_code == 302
    digest = json.loads(path.read_text())['storefront_collections'][0]['image_sha256']
    assert digest.encode() in client.get(url).data
    assert client.get('/taxonomy/images/' + digest + '.webp').status_code == 200
    page = client.get(url)
    # A later edit without selecting the file again preserves its saved reference.
    response = client.post(url, data={'csrf_token': field(page, 'csrf_token'), 'base': field(page, 'base'),
        'name': 'Seasonal renamed', 'slug': 'seasonal', 'state': 'active', 'action': 'save_local'})
    assert response.status_code == 302
    assert json.loads(path.read_text())['storefront_collections'][0]['image_sha256'] == digest
    before = path.read_bytes()
    page = client.get(url)
    response = client.post(url, data={'csrf_token': field(page, 'csrf_token'), 'base': field(page, 'base'),
        'name': 'Broken upload', 'slug': 'seasonal', 'state': 'active', 'action': 'save_local',
        'image': (BytesIO(b'not an image'), 'invalid.png')}, content_type='multipart/form-data')
    assert response.status_code == 422 and path.read_bytes() == before
