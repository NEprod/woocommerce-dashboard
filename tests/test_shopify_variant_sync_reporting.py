"""Variant metadata and acceptance-only completion: synthetic Shopify evidence."""
import copy
import json

import pytest

from app import db, shopify_drafts as drafts, shopify_notifications as notices
from app.models import CatalogueOperation
from app.utils import discord
from test_phase3_woo_publish_preview import preview_app
from test_shopify_drafts import world
from test_shopify_simple_updates import change
from test_shopify_package_assignments import existing, linked, VariantAdapter
from test_shopify_variables import variable_world, rescan
from test_shopify_taxonomy_products import registry


def test_accepted_package_finishes_operation_without_false_verification(world):
    product, row, adapter = existing(world)
    package = linked()
    change(world, packaging={'selected_package_id': package.id})
    _, token = drafts.preview(product, adapter)
    drafts.confirm(token, True, adapter)
    operation = db.session.get(CatalogueOperation, row.operation_id)
    assert operation.status == 'succeeded'
    result = notices.product_result(operation)
    assert result['outcome'] == 'shopify_accepted'
    assert not result['deferred']
    assert not drafts.evidence(row)['package_assignments']['product']['independently_verified']
    assert notices.enqueue(operation) is None  # durable terminal event already exists
    count = len([c for c in adapter.calls if isinstance(c, tuple)])
    assert drafts.preview(product, adapter)[1] is None
    assert len([c for c in adapter.calls if isinstance(c, tuple)]) == count


def test_variable_acceptance_finishes_all_exact_inventory_items(variable_world):
    product, path, *_ = variable_world
    package = linked()
    rescan(variable_world, packaging={'selected_package_id': package.id})
    adapter = VariantAdapter()
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    operation = db.session.get(CatalogueOperation, row.operation_id)
    assert operation.status == 'succeeded'
    assert notices.product_result(operation)['outcome'] == 'shopify_accepted'
    data = drafts.evidence(row)
    assert set(data['package_assignments']) == set(data['variant_maps'])
    assert all(p['inventory_id'] == data['variant_maps'][key]['inventory_id']
               for key, p in data['package_assignments'].items())


@pytest.mark.parametrize('failure,outcome', [('userErrors', 'failed'), ('timeout', 'recovery_required')])
def test_real_package_failures_never_report_completed(world, failure, outcome):
    product, row, adapter = existing(world)
    package = linked()
    change(world, packaging={'selected_package_id': package.id})
    adapter.assignment_failure = failure
    _, token = drafts.preview(product, adapter)
    drafts.confirm(token, True, adapter)
    operation = db.session.get(CatalogueOperation, row.operation_id)
    assert operation.status != 'succeeded'
    assert notices.product_result(operation)['outcome'] == outcome


def test_accepted_discord_uses_info_not_attention(monkeypatch):
    sent = []
    monkeypatch.setattr(discord, 'send_discord_message', lambda **kw: sent.append(kw) or (True, 'sent'))
    summary = notices.aggregate([{'title': 'Synthetic card', 'sku': 'SYN-1',
        'operation': 'Updated', 'outcome': 'shopify_accepted', 'completed_at': '2026-10-20T10:00:00Z',
        'deferred': []}])
    discord.publishing_message(summary)
    assert sent[0]['channels'] == ['scans_info']
    embed = sent[0]['embeds'][0]
    assert embed['title'] == 'TLC Shopify Sync — Completed (Shopify Accepted)'
    assert 'Outstanding' not in json.dumps(embed)


def test_finished_modifier_sizes_remain_variant_owned_without_generic_parent(variable_world):
    product, path, *_ = variable_world
    doc = json.loads(path.read_text())
    for index, size in enumerate(doc['attributes']['Size']):
        doc['variation_modifiers']['Size=' + size] = {
            **doc['variation_modifiers'].get('Size=' + size, {}),
            'dimensions': {'length': 210 + index * 87, 'width': 148 + index * 62, 'height': 3}}
    rescan(variable_world, **doc)
    numbers = {v.sku: (v.regular_price, v.weight, v.id) for v in product.variations}
    adapter = VariantAdapter()
    plan, token = drafts.preview(product, adapter)
    assert not plan['managed']['product'].get('metafields')
    assert {d['spec']['ownerType'] for d in plan['dimension_metadata']['definitions']} == {'PRODUCTVARIANT'}
    row = drafts.confirm(token, True, adapter)
    for variant in plan['variants']:
        mapping = drafts.evidence(row)['variant_maps'][variant['identity']]
        remote = next(v for v in adapter.remote['variants']['nodes'] if v['id'] == mapping['id'])
        for field in variant['managed']['metafields']:
            assert remote[field['key'].removeprefix('product_')]['value'] == field['value']
    assert numbers == {v.sku: (v.regular_price, v.weight, v.id) for v in product.variations}
    assert drafts.preview(product, adapter)[0]['classification'] == 'no_change'


def test_variant_matrix_is_read_only_and_separates_value_access_and_receipt(variable_world, monkeypatch):
    from app.shopify_attribute_diagnostic import variant_matrix
    product, path, *_ = variable_world
    package = linked()
    rescan(variable_world, packaging={'selected_package_id': package.id,
        'packed_dimensions': {'length': 16, 'width': 16, 'height': .5, 'unit': 'cm'}})
    monkeypatch.setenv('SHOPIFY_NATIVE_DIMENSIONS_ENABLED', 'true')
    adapter = VariantAdapter()
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    before = row.media_evidence_json
    originals = path.read_bytes()
    definitions = adapter.dimension_definitions(drafts.configuration(), 'tlc_dashboard', 'PRODUCTVARIANT')
    definitions['length']['access']['storefront'] = 'NONE'
    adapter.dimension_fields = {'PRODUCTVARIANT': definitions}
    adapter.remote['media']['pageInfo']['hasNextPage'] = True  # metadata audit is not a gallery/freshness verification
    adapter.calls.clear()
    report = variant_matrix(row, adapter)
    assert report['database_writes'] == 0 and len(report['variants']) == 4
    assert all(v['packed_state'] == 'matching' for v in report['variants'])
    assert all(v['receipt_matches_current_target'] for v in report['variants'])
    assert all(v['assignment_independent_readback'] == 'unsupported' for v in report['variants'])
    for variant in report['variants']:
        length = next(f for f in variant['finished_metafields'] if f['key'] == 'product_length')
        assert length['value_state'] == 'matching' and length['storefront_access'] == 'NONE'
        assert length['definition_action'] == 'Update storefront access through Review & Sync'
    assert row.media_evidence_json == before and path.read_bytes() == originals
    assert not any('write' in str(c) or isinstance(c, tuple) for c in adapter.calls)


def test_accepted_receipt_progress_does_not_claim_independent_verification(world, monkeypatch):
    from flask import current_app
    from test_phase3_woo_publish_preview import _client
    product, row, adapter = existing(world)
    package = linked()
    change(world, packaging={'selected_package_id': package.id})
    _, token = drafts.preview(product, adapter)
    monkeypatch.setattr(drafts, 'ShopifyAdapter', lambda: adapter)
    client = _client(current_app._get_current_object())
    response = client.post(f'/products/{product.id}/shopify/review',
        data={'action': 'confirm', 'review': token, 'acknowledge': 'yes'}, headers={'Accept': 'application/json'})
    assert response.json['state'] == 'completed'
    assert response.json['completion_label'] == 'Completed — Shopify Accepted'
    assert response.json['pending_stages'] == []
    assert 'unavailable' in response.json['message']
    receipt = client.get(response.json['history_url'])
    assert receipt.status_code == 200 and b'Saved-package acknowledgements' in receipt.data


def test_missing_or_mismatched_acknowledgement_cannot_complete(world):
    from app.shopify_package_assignments import completion
    product, row, adapter = existing(world)
    package = linked()
    change(world, packaging={'selected_package_id': package.id})
    _, token = drafts.preview(product, adapter)
    drafts.confirm(token, True, adapter)
    data = drafts.evidence(row)
    assert completion(data) == 'accepted'
    data['package_assignments']['product']['inventory_id'] = 'gid://shopify/InventoryItem/999'
    assert completion(data) == 'outstanding' and not notices.complete(row, data)
    data['package_assignments'] = {}
    assert completion(data) == 'outstanding' and not notices.complete(row, data)


def test_navigation_matrix_reuses_existing_public_metadata_without_writes(world, registry):
    from test_shopify_attribute_policy import created
    from app.shopify_attribute_diagnostic import navigation_matrix
    row, adapter = created(world, registry)
    before = row.media_evidence_json
    source = world[2].read_bytes()
    adapter.calls.clear()
    report = navigation_matrix(row, adapter)
    assert report['database_writes'] == 0
    attribute = report['informational_attributes'][0]
    assert attribute['name'] == 'Occasion'  # synthetic registry, not claimed merchant vocabulary
    assert attribute['type'] == 'list.metaobject_reference' and attribute['owner'] == 'PRODUCT'
    assert attribute['namespace'] == '$app:tlc_attributes'
    assert attribute['storefront_access'] == 'PUBLIC_READ'
    assert attribute['proposed_action'] == 'Already matching'
    assert row.media_evidence_json == before and world[2].read_bytes() == source
    assert not any('create' in c or 'update' in c or 'write' in c for c in adapter.calls)


def test_old_accepted_partial_settles_once_without_resending_assignment(world, monkeypatch):
    product, row, adapter = existing(world)
    package = linked()
    change(world, packaging={'selected_package_id': package.id})
    _, token = drafts.preview(product, adapter)
    drafts.confirm(token, True, adapter)
    operation = db.session.get(CatalogueOperation, row.operation_id)
    scope = notices.scope_of(operation)
    scope[notices.KEY] = {'partial_completion': {'state': 'sent', 'attempts': 1}}
    operation.scope = json.dumps(scope)
    operation.status = 'partial'
    db.session.commit()
    deliveries = []
    monkeypatch.setattr(discord, 'publishing_message', lambda value: deliveries.append(value) or (True, 'sent'))
    proof = copy.deepcopy(drafts.evidence(row)['package_assignments'])
    adapter.calls.clear()
    notices.settle(row)
    notices.settle(row)
    assert operation.status == 'succeeded' and len(deliveries) == 1
    assert deliveries[0]['shopify_accepted'] == 1
    assert drafts.evidence(row)['package_assignments'] == proof and adapter.calls == []


def test_enabled_native_api_without_authored_targets_does_not_prevent_completion(world):
    _, row, _ = existing(world)
    data = drafts.evidence(row)
    data['plan']['native']['enabled'] = True
    assert not data['plan']['native'].get('desired')
    assert notices.complete(row, data)
    data['plan']['native'].pop('unconfigured', None)
    data['plan']['native']['measurement_source'] = 'authored_packed_size'
    data['plan']['native']['reason'] = 'Missing packed height'
    assert not notices.complete(row, data)  # invalid authored data still blocks completion
