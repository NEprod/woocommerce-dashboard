"""Real-sized isolated originals; mocked Shopify, no live mutations."""
import io
import hashlib
import re
import json
import resource
import sys
from pathlib import Path
from unittest.mock import patch
from unittest.mock import Mock

import pytest
import requests
from PIL import Image

from app import db, shopify_drafts as drafts
from app.models import Product, ProductImage, ShopifyProductIdentity
from app.shopify_connection import ShopifyError
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_drafts import world
from test_shopify_publication_sales import AcceptanceAdapter, VariableAcceptanceAdapter
from test_automatic_publishing import authorise, execute
from test_shopify_simple_updates import change
from test_shopify_variables import variable_world


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr('app.utils.discord.publishing_message', lambda data: (False, 'disabled'))


class ByteAdapter(AcceptanceAdapter):
    def stage_images(self, config, images):
        assert len(images) <= drafts.UPLOAD_BATCH
        assert sum(i['size'] for i in images) <= drafts.MAX_UPLOAD_BYTES
        return super().stage_images(config, images)

    def upload_image(self, config, target, image, content):
        assert len(content) == image['size']
        assert hashlib.sha256(content).hexdigest() == image['sha256']
        super().upload_image(config, target, image, content)
        self.uploaded_bytes = getattr(self, 'uploaded_bytes', 0) + len(content)


def originals(world, count=53, size=(512, 512)):
    product, folder, _ = world
    product.images.clear()
    for path in folder.iterdir():
        if path.suffix.lower() in {'.png', '.jpg'}: path.unlink()
    image = Image.new('RGB', size, 'red')
    output = io.BytesIO(); image.save(output, 'PNG', compress_level=0)
    content = output.getvalue()
    for index in range(count):
        name = f'original-{index:03}.png'
        (folder / name).write_bytes(content)
        product.images.append(ProductImage(url=name, position=index, alt_text=f'Original {index}'))
    db.session.commit()
    return product, content


def test_real_53_image_gallery_over_30_mib_is_valid_for_batched_upload(world):
    product, content = originals(world)
    assert 53 * len(content) > 30 * 1024 * 1024
    plan = drafts.local_plan(product)
    assert len(plan['images']) == 53
    assert sum(i['size'] for i in plan['images']) == 53 * len(content)


def test_documented_probe_measures_selection_without_app_startup_or_database_writes(world, capsys):
    product, content = originals(world)
    database = Path(db.engine.url.database)
    before = database.read_bytes()
    document = (Path(__file__).parents[1] / 'docs/IMAGE_UPLOAD_BYTE_BATCHING.md').read_text()
    probe = document.split("python - <<'PY'\n", 1)[1].split('\nPY\n', 1)[0]
    probe = probe.replace("Path('/app/instance/site.db')", f'Path({str(database)!r})').replace("sku='3DCO-0001'", f'sku={product.sku!r}')
    exec(compile(probe, 'documented-read-only-gallery-probe', 'exec'), {})
    result = json.loads(capsys.readouterr().out)
    assert result['selected_count'] == result['measured_count'] == 53
    assert result['total_bytes'] == 53 * len(content)
    assert result['database_writes'] == result['shopify_requests'] == 0
    assert database.read_bytes() == before


@pytest.mark.parametrize('count,size', [(2, (512, 512)), (53, (512, 512)), (53, (1280, 1280)), (5, (1536, 2048))])
def test_manual_original_uploads_are_bounded_by_count_and_bytes(world, count, size, record_property):
    product, content = originals(world, count, size)
    adapter = ByteAdapter(); slices = []
    for _ in range(count + 1):
        before = getattr(adapter, 'uploaded_bytes', 0)
        _, token = drafts.preview(product, adapter)
        row = drafts.confirm(token, True, adapter)
        slices.append(getattr(adapter, 'uploaded_bytes', 0) - before)
        assert slices[-1] <= drafts.MAX_UPLOAD_BYTES
        if row.state == 'verified': break
        assert drafts.upload_pending(row) and adapter.remote is None
    assert row.state == 'verified', drafts.evidence(row).get('error')
    assert sum(slices) == count * len(content)
    assert adapter.calls.count('binary_upload') == count and adapter.calls.count('product_create') == 1
    assert [m['alt'] for m in adapter.remote['media']['nodes']] == [f'Original {i}' for i in range(count)]
    assert len(drafts.evidence(row)['created_media']) == count
    if count == 5:
        assert len(slices) == 2  # byte budget, not the 20-file count, split this gallery
    assert all(p.read_bytes() == content for p in world[1].glob('*.png'))
    assert drafts.preview(product, adapter)[0]['classification'] == 'no_change'
    assert adapter.calls.count('binary_upload') == count
    record_property('gallery_bytes', json.dumps({'images': count, 'each_bytes': len(content),
        'total_bytes': count * len(content), 'slice_bytes': slices,
        'peak_process_rss_mib': round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1048576 if sys.platform == 'darwin' else 1024), 2)}))


def test_manual_http_review_continues_only_with_fresh_signed_approval(world, preview_app, monkeypatch):
    product, _ = originals(world)
    adapter = ByteAdapter(); client = _client(preview_app)
    monkeypatch.setattr(drafts, 'ShopifyAdapter', lambda: adapter)
    route = f'/products/{product.id}/shopify/review'
    from itsdangerous.timed import TimestampSigner
    from flask import g
    clock = TimestampSigner.get_timestamp(TimestampSigner('fixture'))
    for index in range(3):
        # world keeps an outer app context for ORM access; real HTTP requests
        # have fresh g. Do not carry the prior request's accepted lease forward.
        g.pop('shopify_confirm_lease', None); g.pop('shopify_review_ticket', None)
        with patch.object(TimestampSigner, 'get_timestamp', return_value=clock + index * 5):
            response = client.post(route, data={'action': 'preview'})
            assert response.status_code == 200
            token = re.search(r'name="review" value="([^"]+)"', response.get_data(as_text=True)).group(1)
            result = client.post(route, data={'action': 'confirm', 'review': token, 'acknowledge': 'yes'}, headers={'Accept': 'application/json'})
            assert result.status_code == 200
            assert result.json['state'] in {'processing', 'completed'}, (result.json, drafts.evidence(ShopifyProductIdentity.query.one()))
            calls = adapter.calls.count('binary_upload')
            # Same approved ticket is idempotent, including pending readback.
            repeat = client.post(route, data={'action': 'confirm', 'review': token, 'acknowledge': 'yes'}, headers={'Accept': 'application/json'})
            assert repeat.status_code == 200 and adapter.calls.count('binary_upload') == calls
    assert ShopifyProductIdentity.query.one().state == 'verified'
    assert adapter.calls.count('binary_upload') == 53 and adapter.calls.count('product_create') == 1


@pytest.mark.parametrize('size', [(512, 512), (1280, 1280)])
def test_automatic_large_gallery_restart_and_async_readback_never_reupload(world, size):
    product, content = originals(world, size=size); change(world, live=True)
    adapter = ByteAdapter(); adapter.processing = True; authorise(adapter)
    assert execute(product, adapter) == 'retry'
    row = ShopifyProductIdentity.query.one()
    batch_count = min(20, drafts.MAX_UPLOAD_BYTES // len(content))
    assert len(drafts.evidence(row)['upload_inputs']) == batch_count
    # Session reconstruction of durable evidence between acknowledged batches.
    identity_id, product_id = row.id, product.id; db.session.remove()
    product = db.session.get(Product, product_id)
    row = db.session.get(ShopifyProductIdentity, identity_id)
    assert drafts.upload_pending(row)
    assert execute(product, adapter) == 'retry'
    assert len(drafts.evidence(row)['upload_inputs']) == batch_count * 2
    for _ in range(53):
        assert execute(product, adapter) == 'retry'
        if adapter.remote: break
    assert adapter.remote and adapter.calls.count('product_create') == 1
    assert execute(product, adapter) == 'retry'  # readback still processing
    assert adapter.calls.count('binary_upload') == 53
    for index, image in enumerate(adapter.remote['media']['nodes']):
        image.update(status='READY', image={'url': f'https://cdn.shopify.com/ready-{index}.png'})
    assert execute(product, adapter) == 'verified'
    assert adapter.calls.count('binary_upload') == 53 and adapter.uploaded_bytes == len(content) * 53
    assert adapter.calls.count('product_create') == 1


@pytest.mark.parametrize('failure', ['partial', 'timeout'])
def test_failed_batch_preserves_acks_and_blocks_blind_retry(world, failure):
    product, _ = originals(world); change(world, live=True)
    class Failing(ByteAdapter):
        def upload_image(self, config, target, image, content):
            if self.calls.count('binary_upload') == 22:
                if failure == 'timeout':
                    raise ShopifyError('Staged image upload outcome is uncertain. Do not blindly retry.')
                raise ShopifyError('Staged image upload failed. No automatic retry was attempted.')
            return super().upload_image(config, target, image, content)
    adapter = Failing(); authorise(adapter)
    assert execute(product, adapter) == 'retry'
    assert execute(product, adapter) == 'recovery_required'
    row = ShopifyProductIdentity.query.one(); data = drafts.evidence(row)
    assert len(data['upload_inputs']) == 22 and data['upload_attempt']['state'] == 'pending'
    assert not drafts.upload_pending(row) and adapter.remote is None
    assert execute(product, adapter) == 'recovery_required'
    assert adapter.calls.count('binary_upload') == 22 and 'product_create' not in adapter.calls


def test_individual_file_error_identifies_file_not_aggregate_gallery(world):
    product, content = originals(world, count=1)
    path = world[1] / 'original-000.png'
    path.write_bytes(content + b'\0' * drafts.MAX_IMAGE)
    with pytest.raises(ShopifyError, match=r'original-000.png.*individual-file'):
        drafts.local_plan(product)
    assert path.stat().st_size > drafts.MAX_IMAGE


def test_unsupported_resolution_is_identified_without_resizing(world):
    product, _ = originals(world, count=1, size=(5000, 4100))
    # Compressed original keeps the existing byte guard independent of pixels.
    path = world[1] / 'original-000.png'; Image.new('RGB', (5000, 4100), 'red').save(path)
    before = path.read_bytes()
    with pytest.raises(ShopifyError, match=r'original-000.png.*5000.*4100.*20 megapixels'):
        drafts.local_plan(product)
    assert path.read_bytes() == before


def test_batch_prefix_never_reorders_or_treats_unknown_size_as_zero():
    images = [{'filename': str(i), 'size': drafts.MAX_IMAGE} for i in range(5)]
    assert drafts.upload_batch(images) == images[:3]
    for size in (None, 0, -1, True, drafts.MAX_IMAGE + 1):
        with pytest.raises(ShopifyError, match='individual-file'):
            drafts.upload_batch([{'filename': 'invalid.png', 'size': size}])


def test_real_binary_transport_timeout_is_single_attempt_without_token_leak(world, monkeypatch):
    from app.shopify_connection import ShopifyAdapter
    session = Mock(); session.post.side_effect = requests.Timeout('secret signed target detail')
    monkeypatch.setattr('app.shopify_connection.requests.Session', lambda: session)
    target = {'url': 'https://storage.googleapis.com/upload', 'resourceUrl': 'https://storage.googleapis.com/original', 'parameters': []}
    with pytest.raises(ShopifyError, match='outcome is uncertain') as exc:
        ShopifyAdapter().upload_image(drafts.configuration(), target, {'filename': 'original.png', 'mime': 'image/png'}, b'original')
    assert 'secret' not in str(exc.value) and session.post.call_count == 1
    assert 'headers' not in session.post.call_args.kwargs
    session.close.assert_called_once()


def test_large_gallery_failed_readback_keeps_product_identity_and_never_recreates(world):
    product, _ = originals(world); change(world, live=True)
    class FailedReadback(ByteAdapter):
        def read_simple(self, *args, **kwargs):
            if self.remote: raise ShopifyError('Required readback unavailable')
            return super().read_simple(*args, **kwargs)
    adapter = FailedReadback(); authorise(adapter)
    for _ in range(4):
        state = execute(product, adapter)
        if state == 'recovery_required': break
    row = ShopifyProductIdentity.query.one()
    assert state == 'recovery_required' and row.shopify_product_id == adapter.remote['id']
    assert adapter.calls.count('binary_upload') == 53 and adapter.calls.count('product_create') == 1
    assert execute(product, adapter) == 'recovery_required'
    assert adapter.calls.count('binary_upload') == 53 and adapter.calls.count('product_create') == 1


def test_variable_over_30_mib_uses_same_bounded_uploader(variable_world):
    from app.catalogue_images import _source_context
    product = variable_world[0]; _, folder, _ = _source_context(product)
    initial = len(drafts.local_plan(product)['images'])
    output = io.BytesIO(); Image.new('RGB', (512, 512), 'red').save(output, 'PNG', compress_level=0)
    content = output.getvalue()
    for i in range(53 - initial):
        name = f'large-{i:03}.png'; (folder / name).write_bytes(content)
        product.images.append(ProductImage(url=name, position=100 + i))
    db.session.commit()
    assert sum(i['size'] for i in drafts.local_plan(product)['images']) > drafts.MAX_UPLOAD_BYTES
    class VariableBytes(VariableAcceptanceAdapter):
        def stage_images(self, config, images):
            assert len(images) <= 20 and sum(i['size'] for i in images) <= drafts.MAX_UPLOAD_BYTES
            return super().stage_images(config, images)
    adapter = VariableBytes()
    for _ in range(4):
        _, token = drafts.preview(product, adapter); row = drafts.confirm(token, True, adapter)
        if row.state == 'verified': break
    assert row.state == 'verified' and len(adapter.remote['media']['nodes']) == 53
    assert len({v['id'] for v in adapter.remote['variants']['nodes']}) == 4
    assert adapter.calls.count('product_create') == 1
