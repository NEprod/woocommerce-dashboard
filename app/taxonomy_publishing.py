"""Reviewed artwork and all-channel publication of mapped taxonomy collections."""
import json

from itsdangerous import BadData
from app import db, shopify_drafts as drafts, shopify_taxonomy as taxonomy
from app.shopify_connection import ShopifyAdapter, ShopifyError, _gid
from app.taxonomy_images import read, ImageUploadError
from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation


def channels(adapter, config):
    result = []
    for row in adapter.publications(config):  # Existing complete APP-catalog discovery.
        channel = row.get('channels') or {}
        if channel.get('pageInfo', {}).get('hasNextPage') is not False or not isinstance(channel.get('nodes'), list):
            raise ShopifyError('Sales-channel discovery is incomplete. Review again before publishing.')
        result.append({'id': _gid(row.get('id'), 'Publication'),
            'name': ', '.join(c.get('name', '') for c in channel['nodes']) or row['id']})
    if not result or len({r['id'] for r in result}) != len(result):
        raise ShopifyError('No complete, unique sales-channel publications are available.')
    return sorted(result, key=lambda r: r['id'])


def state(adapter, config, remote_id):
    value = adapter.collection_live(config, remote_id)
    connection = value.get('resourcePublicationsV2') if isinstance(value, dict) else None
    if (not value or value.get('id') != remote_id or not isinstance(connection, dict)
            or connection.get('pageInfo', {}).get('hasNextPage') is not False
            or not isinstance(connection.get('nodes'), list)):
        raise ShopifyError('Collection publication readback is incomplete; no success is assumed.')
    for row in connection['nodes']:
        _gid((row.get('publication') or {}).get('id'), 'Publication')
        if type(row.get('isPublished')) is not bool:
            raise ShopifyError('Collection publication evidence is incomplete.')
    return value


def published(value):
    return {r['publication']['id'] for r in value['resourcePublicationsV2']['nodes'] if r['isPublished']}


def review(keys, adapter=None):
    adapter = adapter or ShopifyAdapter()
    config = drafts.configuration()
    shop = adapter.test_connection(config)
    if not {'read_publications', 'write_publications', 'write_products'} <= set(shop['scopes']):
        raise ShopifyError('Publishing collections needs read_publications, write_publications and write_products. No permissions are changed automatically.')
    lookup = {r['key']: r for r in taxonomy.sources()
        if r['key'].startswith(('categories:', 'storefront_collections:'))}
    if not 1 <= len(keys) <= 20 or len(set(keys)) != len(keys) or any(k not in lookup for k in keys):
        raise ShopifyError('Select 1–20 active taxonomy categories or storefront collections.')
    publications = channels(adapter, config)
    groups = []
    for key in keys:
        source = lookup[key]
        row = taxonomy.mapping(shop['shop_id'], key)
        if not row or row.state != 'verified' or not row.remote_id:
            raise ShopifyError(source['label'] + ': create or explicitly link its Shopify collection first, then review publishing.')
        taxonomy.validate_collection(adapter.collection_state(config, row.remote_id), row.remote_id)
        remote = state(adapter, config, row.remote_id)
        image = source.get('image_sha256')
        if image:
            try:
                read(image)
            except (OSError, ImageUploadError):
                raise ShopifyError('The saved collection image is unavailable. Upload it again before publishing.') from None
        groups.append({'source': source, 'remote_id': row.remote_id,
            'mapping': drafts.digest(row.evidence_json), 'remote_digest': drafts.digest(remote),
            'missing': [p['id'] for p in publications if p['id'] not in published(remote)],
            'upload_image': bool(image and ((remote.get('metafield') or {}).get('value') != image or not remote.get('image')))})
    plan = {'version': 1, 'store_id': shop['shop_id'], 'config': config.fingerprint(),
        'publications': publications, 'groups': groups}
    return plan, drafts.serializer().dumps({'taxonomy_publication': plan})


def confirm(token, acknowledged, adapter=None):
    if not acknowledged:
        raise ShopifyError('Explicit collection image and sales-channel publication approval is required.')
    try:
        plan = drafts.serializer().loads(token, max_age=900)['taxonomy_publication']
        keys = [g['source']['key'] for g in plan['groups']]
    except (BadData, KeyError, TypeError):
        raise ShopifyError('Publication review expired. Review again.') from None
    adapter = adapter or ShopifyAdapter()
    lease = acquire_catalogue_operation('shopify_draft_publish', {'mode': 'taxonomy_publication', 'sources': keys})
    attempted = False
    try:
        fresh, _ = review(keys, adapter)
        if fresh != plan:
            raise ShopifyError('Collection, image, store or available channels changed after review. Review again.')
        config = drafts.configuration()
        results = []
        for group in plan['groups']:
            source, remote_id = group['source'], group['remote_id']
            if group['upload_image']:
                content = read(source['image_sha256'])
                image = {'filename': source['image_sha256'] + '.webp', 'mime': 'image/webp', 'size': len(content)}
                attempted = True
                targets = drafts.payload_result(adapter.stage_collection_image(config, image), 'stagedUploadsCreate').get('stagedTargets')
                if not isinstance(targets, list) or len(targets) != 1:
                    raise ShopifyError('Shopify did not provide one complete image upload target.')
                adapter.upload_image(config, targets[0], image, content)
                drafts.payload_result(adapter.set_collection_image(config, remote_id, targets[0]['resourceUrl'],
                    source['name'], source['image_sha256']), 'collectionUpdate')
            if group['missing']:
                attempted = True
                drafts.payload_result(adapter.publish_collection(config, remote_id, group['missing']), 'publishablePublish')
            verified = state(adapter, config, remote_id)
            if not {p['id'] for p in plan['publications']} <= published(verified):
                raise ShopifyError('Not every reviewed sales channel was verified. Review current state before retrying.')
            if group['upload_image'] and (not verified.get('image') or
                    (verified.get('metafield') or {}).get('value') != source['image_sha256']):
                raise ShopifyError('Shopify has not verified the collection artwork yet. Review current state before retrying.')
            results.append({'source': source['key'], 'remote_id': remote_id, 'verified': True})
        finish_catalogue_operation(lease.id, status='succeeded', operation_summary={'collections': results, 'channels': plan['publications']})
        return results
    except Exception as exc:
        db.session.rollback()
        message = str(exc) if isinstance(exc, ShopifyError) else 'Collection publication did not complete. Inspect current Shopify state before a new review.'
        finish_catalogue_operation(lease.id, status='partial' if attempted else 'failed', error=message,
            recovery_state='review_required' if attempted else 'none')
        raise ShopifyError(message) from None
