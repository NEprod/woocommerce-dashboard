"""Checkpoint helpers for reviewed media changes on retained Shopify products.

These helpers run only inside the existing signed product publisher.  Their
receipts belong to that publisher's identity/operation, not a separate queue.
"""
import hashlib

from app.shopify_connection import ShopifyError, _gid


def _gallery(adapter, config, row):
    remote = adapter.read_draft(config, row.shopify_product_id)
    if not isinstance(remote, dict) or remote.get('id') != row.shopify_product_id:
        raise ShopifyError('Retained Shopify product is unavailable; no media write is safe.')
    gallery = remote.get('media') or {}
    if gallery.get('pageInfo', {}).get('hasNextPage') is not False or not isinstance(gallery.get('nodes'), list):
        raise ShopifyError('Shopify gallery readback is incomplete; no media write is safe.')
    nodes = gallery['nodes']
    ids = [_gid(m.get('id'), 'MediaImage') for m in nodes]
    if len(ids) != len(set(ids)):
        raise ShopifyError('Shopify gallery identities are ambiguous; no media write is safe.')
    return {m['id']: m for m in nodes}


def _known(data):
    sync = data.setdefault('media_sync', {'uploaded': {}, 'added': {}, 'removed': []})
    sync.setdefault('uploaded', {})
    sync.setdefault('added', {})
    sync.setdefault('removed', [])
    return sync, {m['id'] for m in data['media']} | {m['id'] for m in sync['added'].values()}


def _attached_gallery(row, data, observed, known):
    """Wait for acknowledged additions without ever reissuing the mutation."""
    from app import shopify_drafts as drafts
    from app.shopify_capacity import BackgroundYield
    actual = set(observed)
    if actual == known:
        return
    sync = data['media_sync']
    retained = {item['id'] for item in data['media']}
    added = {item['id'] for item in sync['added'].values()}
    if retained <= actual and actual < known and known - actual <= added:
        sync['addition_wait'] = sync.get('addition_wait', 0) + 1
        drafts.save(row, data)
        if sync['addition_wait'] <= 12:
            raise BackgroundYield('Acknowledged image addition is awaiting Shopify readback.', code='media_readback')
    raise ShopifyError('Shopify gallery changed or an acknowledged image is missing; no automatic media retry.')


def _simple_variant_media(adapter, config, variant_id):
    """Read the complete Simple variant association before retiring its media."""
    remote = adapter.inspect_variant(config, variant_id)
    connection = remote.get('media') or {}
    if connection.get('pageInfo', {}).get('hasNextPage') is not False:
        raise ShopifyError('Simple variant image association is incomplete; managed media is retained.')
    edges = connection.get('edges')
    if not isinstance(edges, list) or len(edges) > 2:
        raise ShopifyError('Simple variant image association is ambiguous; managed media is retained.')
    ids = [_gid((edge.get('node') or {}).get('id'), 'MediaImage') for edge in edges]
    if len(ids) != len(set(ids)):
        raise ShopifyError('Simple variant image identities are ambiguous; managed media is retained.')
    return ids


def simple_association_review(plan, data, config, adapter, variant_id):
    """Bind a Simple variant's current image link to the signed review."""
    linked = _simple_variant_media(adapter, config, variant_id)
    retained = {item['id']: item for item in data['media']}
    if len(linked) > 1 or any(media_id not in retained for media_id in linked):
        raise ShopifyError('Simple variant uses unreviewed media; reconcile before image retirement.')
    plan['simple_variant_media'] = linked
    retiring = {item['id'] for item in plan['media_actions']['retire']}
    if not linked or linked[0] not in retiring:
        return []
    source = retained[linked[0]]['source']
    replacement = next((image for image in plan['images'] if image['source'] == source), None)
    return [{'field': 'variant.mediaId', 'before': linked,
             'after': replacement['source'] if replacement else None,
             'changed': True, 'action': 'Replace' if replacement else 'Remove'}]


def update_simple_association(row, data, config, adapter, resolved):
    """Rebind/detach only a signed Simple variant link before old-media deletion."""
    from app import shopify_drafts as drafts
    from app.shopify_capacity import BackgroundYield
    from app.shopify_continuation import effect

    plan = data['plan']
    old = plan.get('simple_variant_media') or []
    if len(old) != 1 or old[0] not in {item['id'] for item in plan['media_actions']['retire']}:
        return
    source = next(item['source'] for item in data['media'] if item['id'] == old[0])
    desired = next((media_id for image, media_id in zip(plan['images'], resolved)
                    if image['source'] == source), None)
    current = _simple_variant_media(adapter, config, data['variant_id'])
    if desired and current == old:
        name = 'simple_media_attach'
        data.update(stage=name, update_attempted=True)
        effect(row, data, name,
            lambda: adapter.update_simple_variant(config, row.shopify_product_id,
                {'id': data['variant_id'], 'mediaId': desired}),
            lambda result: drafts.payload_result(result, 'productVariantsBulkUpdate'), state='updating')
        current = _simple_variant_media(adapter, config, data['variant_id'])
    if desired and desired not in current:
        sync = data.setdefault('media_sync', {})
        sync['simple_association_wait'] = sync.get('simple_association_wait', 0) + 1
        drafts.save(row, data)
        if sync['simple_association_wait'] <= 12:
            raise BackgroundYield('Simple variant image association is awaiting Shopify readback.', code='media_association')
        raise ShopifyError('Simple variant image association could not be verified; old media retained.')
    if old[0] in current:
        if set(current) != ({old[0], desired} if desired else {old[0]}):
            raise ShopifyError('Simple variant media differs from the reviewed association; old media retained.')
        name = 'simple_media_detach'
        data.update(stage=name, update_attempted=True)
        def accept(result):
            payload = drafts.payload_result(result, 'productVariantDetachMedia')
            if (payload.get('product') or {}).get('id') != row.shopify_product_id:
                raise ShopifyError('Simple variant media detachment acknowledgement is incomplete.')
        effect(row, data, name,
            lambda: adapter.detach_variant_media(config, row.shopify_product_id,
                data['variant_id'], old[0]), accept, state='updating')
    current = _simple_variant_media(adapter, config, data['variant_id'])
    if current != ([desired] if desired else []):
        sync = data.setdefault('media_sync', {})
        sync['simple_association_wait'] = sync.get('simple_association_wait', 0) + 1
        drafts.save(row, data)
        if sync['simple_association_wait'] <= 12:
            raise BackgroundYield('Simple variant image association is awaiting Shopify readback.', code='media_association')
        raise ShopifyError('Simple variant image association could not be verified; old media retained.')


def prepare(row, data, product, config, adapter):
    """Upload reviewed originals, attach once, and wait for verified READY media."""
    from app import shopify_drafts as drafts
    from app.shopify_capacity import RequestNotSent, BackgroundYield
    from app.shopify_continuation import effect

    actions = data['plan']['media_actions']
    sync, known = _known(data)
    if sync['removed']:
        # A continuation can resume after one acknowledged deletion. All
        # additions necessarily preceded deletion; let retire() verify the
        # post-deletion gallery instead of expecting deleted old IDs again.
        if any(image['source'] not in sync['added'] for image in actions['add']):
            raise ShopifyError('Media checkpoint order is incomplete; no upload or deletion replay.')
        return [sync['added'][image['source']]['id'] if image['source'] in sync['added']
            else next(m['id'] for m in data['media'] if m['source'] == image['source'])
            for image in data['plan']['images']]
    for image in actions['add']:
        source = image['source']
        if source not in sync['added']:
            if source not in sync['uploaded']:
                content, filename, _ = drafts.source_bytes(product, source)
                if len(content) != image['size'] or hashlib.sha256(content).hexdigest() != image['sha256']:
                    raise ShopifyError('Reviewed original image changed; no media was attached.')
                stage_name = 'media_stage:' + source
                data.update(stage=stage_name, update_attempted=True)
                if data.get('continuation'):
                    data['dispatch'] = stage_name
                drafts.save(row, data)
                try:
                    targets = drafts.stage_gallery(adapter, config, [image])
                except RequestNotSent:
                    data.pop('dispatch', None)
                    drafts.save(row, data)
                    raise
                if len(targets) != 1 or not isinstance(targets[0], dict) or not isinstance(targets[0].get('resourceUrl'), str):
                    raise ShopifyError('Shopify returned incomplete staged media evidence.')
                data.pop('dispatch', None)
                data['upload_attempt'] = {'source': source, 'state': 'pending'}
                drafts.save(row, data)
                try:
                    adapter.upload_image(config, targets[0], image, content)
                except RequestNotSent:
                    data.pop('upload_attempt', None)
                    drafts.save(row, data)
                    raise
                sync['uploaded'][source] = {'source': source, 'sha256': image['sha256'],
                    'filename': filename, 'input': {'originalSource': targets[0]['resourceUrl'],
                    'mediaContentType': 'IMAGE', 'alt': image['alt']}}
                data['upload_attempt'] = {'source': source, 'state': 'completed'}
                drafts.save(row, data)
            if sync['uploaded'][source]['sha256'] != image['sha256']:
                raise ShopifyError('Retained staged upload does not match the reviewed source.')
            before = _gallery(adapter, config, row)
            _attached_gallery(row, data, before, known)
            name = 'media_add:' + source
            data.update(stage=name, update_attempted=True)

            def accept(result):
                payload = drafts.payload_result(result, 'productUpdate')
                remote = payload.get('product') or {}
                media = remote.get('media') or {}
                if (remote.get('id') != row.shopify_product_id or media.get('pageInfo', {}).get('hasNextPage') is not False
                        or not isinstance(media.get('nodes'), list)):
                    raise ShopifyError('Shopify media attachment response is incomplete; no automatic retry.')
                nodes = media['nodes']
                ids = [_gid(m.get('id'), 'MediaImage') for m in nodes]
                new = set(ids) - known
                if (len(ids) != len(set(ids)) or set(ids) - new != known or len(new) != 1
                        or next(m for m in nodes if m['id'] in new).get('mediaContentType') != 'IMAGE'):
                    raise ShopifyError('Shopify media attachment identity is uncertain; no automatic retry.')
                sync['added'][source] = {'id': new.pop(), 'source': source,
                    'sha256': image['sha256'], 'filename': image['filename']}

            effect(row, data, name,
                lambda: adapter.add_product_media(config, row.shopify_product_id, sync['uploaded'][source]['input']),
                accept, state='updating')
            known.add(sync['added'][source]['id'])
    observed = _gallery(adapter, config, row)
    _attached_gallery(row, data, observed, known)
    for item in sync['added'].values():
        node = observed[item['id']]
        if node.get('status') == 'FAILED':
            raise ShopifyError('Shopify rejected processing a replacement image; original media is retained.')
        if node.get('status') != 'READY' or not (node.get('image') or {}).get('url', '').startswith('https://'):
            sync['processing_wait'] = sync.get('processing_wait', 0) + 1
            drafts.save(row, data)
            if sync['processing_wait'] > 24:
                raise ShopifyError('Replacement media did not become ready within the bounded readback window; original media is retained.')
            raise BackgroundYield('New Shopify media is still processing; original media is retained.', code='media_processing')
    resolved = [sync['added'][image['source']]['id'] if image['source'] in sync['added']
        else next(m['id'] for m in data['media'] if m['source'] == image['source'])
        for image in data['plan']['images']]
    return resolved


def retire(row, data, config, adapter, resolved):
    """Delete only signed, retained media after replacements and links are safe."""
    from app import shopify_drafts as drafts
    from app.shopify_continuation import effect

    sync, known = _known(data)
    for old in data['plan']['media_actions']['retire']:
        media_id = _gid(old['id'], 'MediaImage')
        if media_id in sync['removed']:
            continue
        observed = _gallery(adapter, config, row)
        expected = known - set(sync['removed'])
        actual = set(observed)
        if actual != expected and expected <= actual and actual - expected <= set(sync['removed']):
            from app.shopify_capacity import BackgroundYield
            sync['removal_wait'] = sync.get('removal_wait', 0) + 1
            drafts.save(row, data)
            if sync['removal_wait'] <= 12:
                raise BackgroundYield('Acknowledged managed image removal is awaiting Shopify readback.', code='media_readback')
        if actual != expected or media_id not in observed or media_id in resolved:
            raise ShopifyError('Managed media removal no longer matches the reviewed gallery.')
        name = 'media_remove:' + media_id
        data.update(stage=name, update_attempted=True)

        def accept(result):
            payload = result.get('productDeleteMedia') or {}
            if payload.get('mediaUserErrors') or payload.get('deletedMediaIds') != [media_id]:
                raise ShopifyError('Shopify media removal was rejected or uncertain; no automatic retry.')
            sync['removed'].append(media_id)

        effect(row, data, name, lambda: adapter.remove_product_media(config, row.shopify_product_id, media_id),
            accept, state='updating')
    final = set(_gallery(adapter, config, row))
    if final != set(resolved):
        if final == set(resolved) | (set(sync['removed']) & final):
            from app.shopify_capacity import BackgroundYield
            sync['removal_wait'] = sync.get('removal_wait', 0) + 1
            drafts.save(row, data)
            if sync['removal_wait'] <= 12:
                raise BackgroundYield('Acknowledged managed image removal is awaiting Shopify readback.', code='media_readback')
        raise ShopifyError('Final Shopify media identities differ from the reviewed gallery.')
    data['plan']['media_actions']['ids'] = resolved
    data['media'] = [{'source': image['source'], 'filename': image['filename'],
        'sha256': image['sha256'], 'id': media_id}
        for image, media_id in zip(data['plan']['images'], resolved)]
    data['created_media'] = [{'id': media_id} for media_id in resolved]
    # Temporary staged resource URLs are not part of durable media identity.
    sync['uploaded'].clear()
    data.pop('upload_attempt', None)
    drafts.save(row, data)
