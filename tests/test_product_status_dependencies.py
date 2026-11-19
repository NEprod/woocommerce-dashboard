"""Product-bound status evidence; fictional APIs and disposable databases only."""
import copy
import json
from datetime import timedelta
from unittest.mock import patch

import pytest

from app import db, catalogue_views as views, shopify_drafts as drafts
from app.models import (ShopifyPublishJob, ShopifyProductComparison, ShopifyProductIdentity,
                        ShopifyCollectionIdentity, ShopifyAttributeIdentity)
from app.shopify_products import states_for
from test_phase3_woo_publish_preview import preview_app
from test_shopify_drafts import world
from test_shopify_variables import variable_world, rescan
from test_shopify_simple_updates import change
from test_shopify_publication_sales import AcceptanceAdapter, VariableAcceptanceAdapter, SafetyMixin
from test_automatic_publishing import authorise, execute, queued
from test_manual_publishing_worker import accepted, step, products_badge
from test_shopify_native_dimensions import NativeAdapter, enable


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr('app.utils.discord.publishing_message', lambda *a, **k: (False, 'offline fixture'))
    monkeypatch.setenv('TLC_SCHEDULER_READY_AT', '99999999999')


def uploaded(world):
    product = world[0]
    change(world, live=True)
    adapter = AcceptanceAdapter()
    authorise(adapter)
    assert execute(product, adapter) == 'verified'
    drafts.preview(product, adapter)
    assert views.capture(product, settle_queue=False)
    db.session.commit()
    return product, adapter, db.session.get(ShopifyPublishJob, product.id)


def saved(product):
    return views.saved_states([product], url_builder=lambda *a, **k: '')[product.id]


def add_unrelated_resources():
    db.session.add(ShopifyCollectionIdentity(store_id='gid://shopify/Shop/42',
        source_key='categories:anniversary-cards', remote_id='gid://shopify/Collection/999',
        state='verified', evidence_json='{}'))
    db.session.add(ShopifyAttributeIdentity(store_id='gid://shopify/Shop/42',
        source_key='attribute:recipient:term:for-wife', remote_id='gid://shopify/Metaobject/999',
        state='verified', evidence_json='{}'))
    db.session.commit()


@pytest.mark.parametrize('variable_world', ['Single Variable'], indirect=True)
@pytest.mark.parametrize('sku,variable,verified', [
    ('3DCO-0001', True, None), ('ADOC-0001', True, None), ('HERO-0001', True, 'matching'),
    ('16bit-0001', False, None), ('16bit-0002', False, 'older'),
    ('CCCC-0001', False, None), ('CCCC-0002', False, None)])
def test_seven_reported_queue_shapes_keep_match_after_unrelated_publish(world, variable_world, sku, variable, verified):
    # Reproduce the seven observed simple/variable + queue-revision shapes,
    # without importing any real store identity, token or catalogue files.
    if variable:
        rescan(variable_world, live=True)
        product, adapter = variable_world[0], VariableAcceptanceAdapter()
    else:
        change(world, live=True)
        product, adapter = world[0], AcceptanceAdapter()
    product.sku = sku
    db.session.commit()
    authorise(adapter)
    assert execute(product, adapter) == 'verified'
    drafts.preview(product, adapter)
    views.capture(product, settle_queue=False)
    job = db.session.get(ShopifyPublishJob, product.id)
    job.verified_revision = job.requested_revision if verified == 'matching' else 'older' if verified else None
    job.state = 'verified' if verified == 'matching' else 'pending'
    db.session.commit()
    original = job.view_json
    assert saved(product)['label'] == 'Up to Date'
    add_unrelated_resources()
    with (patch('requests.Session.request', side_effect=AssertionError('GET/network')),
          patch('app.taxonomy_assignments.product_document', side_effect=AssertionError('GET/source'))):
        views.refresh_context()
        assert saved(product)['label'] == 'Up to Date'
    db.session.refresh(job)
    assert not job.view_pending and job.view_json == original
    assert products_badge(product)['label'] == 'Up to Date'
    assert states_for([product], fresh=True, url_builder=lambda *a, **k: '')[product.id]['label'] == 'Up to Date'
    plan, token = drafts.preview(product, adapter)
    assert plan['classification'] == 'no_change' and token is None


@pytest.mark.parametrize('kind,mutation', [('collection', 'change'), ('attribute', 'change'), ('attribute', 'create'), ('collection', 'delete')])
def test_used_dependency_still_invalidates_only_its_view(world, kind, mutation):
    product, adapter, job = uploaded(world)
    comparison = ShopifyProductComparison.query.filter_by(source_identity=product.source_relpath).one()
    summary = json.loads(comparison.summary_json)
    from app.shopify_comparison import mapping_digest, attribute_mapping_digest
    if kind == 'collection':
        row = ShopifyCollectionIdentity.query.first()
        summary['mappings'][row.source_key] = mapping_digest(row)
    if kind == 'attribute':
        key = 'attribute:recipient:field'
        summary['attribute_mappings'][key] = None
        if mutation == 'change':
            db.session.add(ShopifyAttributeIdentity(store_id=comparison.store_id, source_key=key,
                remote_id='gid://shopify/MetafieldDefinition/998', state='verified', evidence_json='{}'))
            db.session.flush()
            summary['attribute_mappings'][key] = attribute_mapping_digest(ShopifyAttributeIdentity.query.one())
        summary['attribute_capability_policy'] = 1
    comparison.summary_json = json.dumps(summary)
    db.session.commit()
    views.capture(product, settle_queue=False)
    db.session.commit()
    # The dependency may be missing at capture; its later creation matters too.
    before = views.product_contexts([product])[product.id]
    if kind == 'collection':
        row = ShopifyCollectionIdentity.query.first()
        if mutation == 'delete':
            db.session.delete(row)
        else:
            row.evidence_json = '{"changed_capability":true}'
    elif mutation == 'create':
        db.session.add(ShopifyAttributeIdentity(store_id=comparison.store_id, source_key=key,
            remote_id='gid://shopify/MetafieldDefinition/998', state='verified', evidence_json='{}'))
    else:
        ShopifyAttributeIdentity.query.one().state = 'uncertain'
    db.session.commit()
    assert views.product_contexts([product])[product.id] != before
    assert saved(product)['label'] == 'Status refresh pending'
    views.refresh_context()
    db.session.refresh(job)
    assert job.view_pending
    assert not adapter.calls.count('attribute_write')


@pytest.mark.parametrize('cause', ['queue', 'config', 'comparison_pending'])
def test_unproven_difference_is_refresh_pending_not_update(world, cause):
    product, adapter, job = uploaded(world)
    job.verified_revision = None
    if cause == 'queue':
        job.requested_revision = 'a-new-scan-revision'
    elif cause == 'config':
        data = json.loads(job.view_json)
        data['config'] = 'old-config'
        job.view_json = json.dumps(data)
    else:
        job.view_pending = True
    db.session.commit()
    state = saved(product)
    assert state['label'] == 'Status refresh pending'
    assert state['change_count'] == 0 and state['change_summary'] == []


def test_real_pending_change_stays_update_and_uncertainty_wins(world):
    product, adapter, job = uploaded(world)
    change(world, description='A genuine pending HTML description <strong>edit</strong>.')
    queued(product)
    plan, token = drafts.preview(product, adapter)
    assert plan['classification'] == 'update' and token
    views.capture(product, settle_queue=False)
    db.session.commit()
    assert saved(product)['label'] == 'Update Available'
    row = ShopifyProductIdentity.query.one()
    data = drafts.evidence(row)
    data['native_dimensions'] = {'state': 'uncertain'}
    row.media_evidence_json = drafts.encode(data)
    db.session.commit()
    assert saved(product)['label'] == 'Recovery Required'


def test_expiry_keeps_original_timestamp_and_does_not_invent_difference(world, monkeypatch):
    product, _, _ = uploaded(world)
    initial = saved(product)
    expired = drafts.now() + timedelta(minutes=11)
    monkeypatch.setattr(drafts, 'now', lambda: expired)
    state = saved(product)
    assert state['label'] == 'Last check matched' and state['evidence_stale']
    assert state['compared_at'] == initial['compared_at']


def test_shared_mapping_change_invalidates_both_dependents_but_not_third_product(world):
    from app.models import Product
    product, _, job = uploaded(world)
    comparison = ShopifyProductComparison.query.filter_by(source_identity=product.source_relpath).one()
    summary = json.loads(comparison.summary_json)
    mapping = ShopifyCollectionIdentity.query.first()
    from app.shopify_comparison import mapping_digest
    summary['mappings'][mapping.source_key] = mapping_digest(mapping)
    comparison.summary_json = json.dumps(summary)
    db.session.commit()
    views.capture(product, settle_queue=False)
    state = copy.deepcopy(json.loads(job.view_json))
    peers = []
    for number, uses_mapping in ((2, True), (3, False)):
        peer = Product(sku=f'SYNTHETIC-{number}', title='Synthetic dependent', product_type='simple',
            source_relpath=f'Synthetic/Peer-{number}', catalogue_status='active')
        db.session.add(peer)
        db.session.flush()
        value = {**summary, 'product_id': peer.id, 'source': peer.source_relpath,
            'mappings': summary['mappings'] if uses_mapping else {}}
        db.session.add(ShopifyProductComparison(source_identity=peer.source_relpath,
            store_id=comparison.store_id, summary_json=json.dumps(value), compared_at=drafts.now()))
        db.session.add(ShopifyPublishJob(product_id=peer.id, source_identity=peer.source_relpath,
            requested_revision='same', verified_revision='same', state='verified',
            next_attempt=drafts.now(), updated_at=drafts.now(), view_revision='same', view_pending=False))
        peers.append(peer)
    db.session.flush()
    for peer in peers:
        value = copy.deepcopy(state)
        value['context'] = views.product_contexts([peer])[peer.id]
        db.session.get(ShopifyPublishJob, peer.id).view_json = json.dumps(value)
    db.session.commit()
    mapping.evidence_json = '{"new_verified_settings":true}'
    db.session.commit()
    views.refresh_context()
    assert db.session.get(ShopifyPublishJob, product.id).view_pending
    assert db.session.get(ShopifyPublishJob, peers[0].id).view_pending
    assert not db.session.get(ShopifyPublishJob, peers[1].id).view_pending


def test_hourly_after_unrelated_registration_has_no_mutations(world):
    product, adapter, job = uploaded(world)
    job.state, job.verified_revision = 'pending', None
    db.session.commit()
    add_unrelated_resources()
    adapter.calls.clear()
    assert execute(product, adapter) in {'verified', 'matching'}
    assert not any(call in adapter.calls for call in ('product_create', 'stage_write', 'binary_upload',
        'variant_write', 'product_write', 'native_write', 'publication_write', 'status_write', 'alt_update', 'order_update'))
    assert db.session.get(ShopifyPublishJob, product.id).state == 'verified'
    assert saved(product)['state'] == 'no_change'


def test_final_create_readback_retains_verified_dimensions_without_replay(world, monkeypatch):
    from app import shopify_native_dimensions as native
    enable(monkeypatch)
    change(world, dimensions={'length': 155, 'width': 155, 'height': 3}, weight=50,
        packaging={'packed_dimensions_cm': {'length': '15.5', 'width': '15.5', 'height': '.3'}, 'measured': True})
    product, adapter = world[0], NativeAdapter()
    plan, token = drafts.preview(product, adapter)
    assert plan['native']['classification'] == 'create_stage'
    row = drafts.confirm(token, True, adapter)
    receipt = drafts.evidence(row)
    proof = copy.deepcopy(receipt['native_dimensions'])
    assert proof['state'] == 'verified'
    # Exactly the additional final readback used by shared continuation.
    remote = adapter.read_simple(drafts.configuration(), row.shopify_product_id, plan['managed']['namespace'])
    drafts.apply_readback(row, receipt, remote, adapter)
    summary = json.loads(ShopifyProductComparison.query.one().summary_json)
    assert summary['native'] == proof
    assert summary['classification'] == 'no_change' and summary['change_count'] == 0
    assert drafts.evidence(row)['native_dimensions'] == proof
    assert states_for([product], fresh=True, url_builder=lambda *a, **k: '')[product.id]['label'] == 'Up to Date'
    assert adapter.calls.count('native_write') == adapter.calls.count('product_create') == 1
    # Legacy summaries are rendered correctly too, without DB writes or a new read.
    assert native.local_status(product, row, proof['plan'])['state'] == 'verified'


@pytest.mark.parametrize('mode', ['manual', 'hourly'])
def test_both_publishers_complete_native_creation_then_only_changed_field(world, monkeypatch, mode):
    from app import scheduled_tasks as tasks
    from app.models import CatalogueOperation
    enable(monkeypatch)
    change(world, live=True, dimensions={'length': 155, 'width': 155, 'height': 3}, weight=50,
        shopify_channels={'online-store': True},
        packaging={'packed_dimensions_cm': {'length': '15.5', 'width': '15.5', 'height': '.3'}, 'measured': True})
    class Adapter(SafetyMixin, NativeAdapter):
        pass
    product, adapter = world[0], Adapter()
    tasks.configure(tasks.VIEW_KEY, enabled=False, interval=5)
    def publish():
        if mode == 'hourly':
            assert execute(product, adapter) == 'verified'
            views.capture(product, settle_queue=False)
            db.session.commit()
        else:
            op_id, _ = accepted(world, adapter)
            step(adapter)
            assert db.session.get(CatalogueOperation, op_id).status == 'succeeded'
    if mode == 'hourly':
        authorise(adapter)
    queued(product)
    publish()
    assert products_badge(product)['label'] == 'Up to Date'
    row = ShopifyProductIdentity.query.one()
    remote_id, media = row.shopify_product_id, copy.deepcopy(adapter.remote['media'])
    assert adapter.calls.count('product_create') == adapter.calls.count('native_write') == 1
    add_unrelated_resources()
    assert products_badge(product)['label'] == 'Up to Date'
    adapter.calls.clear()
    adapter.inputs.clear()
    change(world, description='A genuinely changed HTML <strong>description</strong>.')
    queued(product)
    publish()
    assert products_badge(product)['label'] == 'Up to Date'
    assert row.shopify_product_id == remote_id and adapter.remote['media'] == media
    assert not set(adapter.calls) & {'native_write', 'product_create', 'binary_upload', 'stage_write', 'variant_write', 'publication_write'}
    assert [set(value) for value in adapter.inputs] == [{'id', 'descriptionHtml'}]


@pytest.mark.parametrize('mismatch', ['uncertain', 'blocked', 'wrong_inventory', 'wrong_target', 'newer_comparison'])
def test_native_summary_never_borrows_untrusted_or_conflicting_proof(world, monkeypatch, mismatch):
    from app import shopify_native_dimensions as native
    enable(monkeypatch)
    change(world, packaging={'packed_dimensions_cm': {'length': '15.5', 'width': '15.5', 'height': '.3'}, 'measured': True})
    product, adapter = world[0], NativeAdapter()
    plan, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    data = drafts.evidence(row)
    if mismatch in {'uncertain', 'blocked'}:
        data['native_dimensions']['state'] = mismatch
    elif mismatch == 'wrong_inventory':
        data['native_dimensions']['inventory_id'] = 'gid://shopify/InventoryItem/999'
    elif mismatch == 'wrong_target':
        data['native_dimensions']['observed']['height'] = 8
    compared = copy.deepcopy(plan['native'])
    if mismatch == 'newer_comparison':
        compared.update(classification='update', before={'length': 0, 'width': 0, 'height': 0, 'unit': 'CENTIMETERS'})
    row.media_evidence_json = drafts.encode(data)
    db.session.commit()
    assert native.comparison_evidence(row, compared) == compared
    state = native.local_status(product, row, compared)
    assert state['state'] == ('uncertain' if mismatch == 'uncertain' else 'blocked' if mismatch == 'blocked' else 'update')
