"""Product evidence must not require readback of a write-only package field."""
import copy
import json

import pytest

from app import db, shopify_drafts as drafts, shopify_status, scheduled_tasks
from app.models import ShopifyProductComparison
from test_phase3_woo_publish_preview import preview_app
from test_shopify_drafts import world
from test_shopify_package_assignments import existing, linked, VariantAdapter
from test_shopify_simple_updates import change
from test_shopify_variables import variable_world
from test_shopify_status_reconciliation import displayed
from app.shopify_connection import ShopifyError


@pytest.mark.parametrize('accepted', [False, True])
def test_package_limitation_does_not_block_complete_product_checkpoint(world, accepted):
    product, row, adapter = existing(world)
    pkg = linked()
    change(world, packaging={'selected_package_id': pkg.id})
    if accepted:
        _, token = drafts.preview(product, adapter)
        drafts.confirm(token, True, adapter)
    else:
        from app import package_links
        package_links.set_id(pkg, None, ['gid://shopify/ShippingPackage/123'], unlink=True, confirmed=True)
    remote = copy.deepcopy(adapter.remote)
    adapter.calls.clear()
    result = shopify_status.refresh([product], force=True, adapter=adapter)
    saved = json.loads(ShopifyProductComparison.query.one().summary_json)
    assert result['errors'] == [] and result['full_comparisons'] == 1
    assert saved['package_assignments_unverified'] is True  # not invented readback
    assert saved['read_complete'] is True
    assert scheduled_tasks.recent_success(product, drafts.configuration())
    assert displayed(product)['label'] == 'Up to Date'
    assert adapter.remote == remote and not any(isinstance(c, tuple) for c in adapter.calls)
    assert drafts.preview(product, adapter)[1] is None


def test_variable_package_acceptance_does_not_poison_product_evidence(variable_world):
    product, path, *_ = variable_world
    pkg = linked()
    raw = json.loads(path.read_text())
    raw['packaging'] = {'selected_package_id': pkg.id}
    path.write_text(json.dumps(raw))
    adapter = VariantAdapter()
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    mappings = copy.deepcopy(drafts.evidence(row)['variant_maps'])
    assignments = copy.deepcopy(drafts.evidence(row)['package_assignments'])
    adapter.calls.clear()
    result = shopify_status.refresh([product], force=True, adapter=adapter)
    assert result['errors'] == []
    assert json.loads(ShopifyProductComparison.query.one().summary_json)['read_complete'] is True
    assert scheduled_tasks.recent_success(product, drafts.configuration())
    assert displayed(product)['label'] == 'Up to Date'
    assert drafts.evidence(row)['variant_maps'] == mappings
    assert drafts.evidence(row)['package_assignments'] == assignments
    assert not any(isinstance(c, tuple) for c in adapter.calls)


def test_legacy_incomplete_package_checkpoint_requires_real_refresh(world):
    product, row, adapter = existing(world)
    pkg = linked()
    change(world, packaging={'selected_package_id': pkg.id})
    _, token = drafts.preview(product, adapter)
    drafts.confirm(token, True, adapter)
    drafts.preview(product, adapter)
    saved = ShopifyProductComparison.query.one()
    old = json.loads(saved.summary_json)
    old['read_complete'] = False  # retained8f83e6e summary, no global reset
    old.pop('package_readback_policy', None)
    saved.summary_json = json.dumps(old)
    db.session.commit()
    assert displayed(product)['label'] != 'Up to Date'
    adapter.product_revisions = lambda config, ids: {i: {'id': i,
        'updatedAt': adapter.remote.get('updatedAt', '2026-10-12T00:00:00Z'), 'status': adapter.remote['status']} for i in ids}
    result = shopify_status.refresh([product], adapter=adapter)
    assert result['full_comparisons'] == 1
    assert displayed(product)['label'] == 'Up to Date'
    assert shopify_status.refresh([product], adapter=adapter)['full_comparisons'] == 0


def test_pending_assignment_stays_update_and_unknown_stays_recovery(world):
    product, row, adapter = existing(world)
    pkg = linked()
    change(world, packaging={'selected_package_id': pkg.id})
    plan, token = drafts.preview(product, adapter)
    assert plan['classification'] == 'update' and token
    assert displayed(product)['label'] == 'Update Available'
    adapter.assignment_failure = 'timeout'
    drafts.confirm(token, True, adapter)
    before = copy.deepcopy(drafts.evidence(row)['package_assignments'])
    shopify_status.refresh([product], force=True, adapter=adapter)
    assert displayed(product)['label'] == 'Recovery Required'
    assert drafts.evidence(row)['package_assignments'] == before
    assert not scheduled_tasks.recent_success(product, drafts.configuration())


def test_matching_values_with_missing_definition_or_access_are_review_changes(world):
    from test_shopify_dimension_sync import existing as sized
    product, row, adapter = sized(world)
    data = drafts.evidence(row)
    data.pop('dimension_definitions', None)  # historical value-only receipt
    drafts.save(row, data)
    fields = adapter.dimension_definitions(drafts.configuration(), 'tlc_dashboard', 'PRODUCT')
    fields['length'] = None
    fields['width']['access']['storefront'] = 'NONE'
    adapter.dimension_fields['PRODUCT'] = fields
    result = shopify_status.refresh([product], force=True, adapter=adapter)
    saved = json.loads(ShopifyProductComparison.query.one().summary_json)
    assert result['errors'] == [] and saved['read_complete'] is True
    assert scheduled_tasks.recent_success(product, drafts.configuration())
    assert displayed(product)['label'] == 'Update Available'
    assert {f['field'] for f in saved['changes']} == {
        'dimension_definitions.PRODUCT.product_length', 'dimension_definitions.PRODUCT.product_width'}
    assert not adapter.inputs  # read-only, not repairing definitions automatically


@pytest.mark.parametrize('component', ['publication', 'informational_attributes', 'native_packed_dimensions'])
def test_incomplete_components_preserve_checkpoint_and_explain_cause(world, component):
    from app import shopify_comparison as comparison
    product, row, adapter = existing(world)
    plan, _ = drafts.preview(product, adapter)
    saved = ShopifyProductComparison.query.one()
    stamp = json.loads(saved.summary_json)['full_compared_at']
    reason = 'Synthetic required read denied; inspect granted permissions.'
    if component == 'publication':
        plan['publication'] = {'read_complete': False, 'reason': reason}
    elif component == 'informational_attributes':
        plan['attribute_diagnostics'] = [{'action': reason}]
    else:
        plan['native'] = {'enabled': True, 'classification': 'blocked', 'reason': reason}
    plan['comparison_started_at'] = drafts.now().isoformat()
    comparison.record(product, plan, row)
    state = json.loads(saved.summary_json)
    assert state['read_complete'] is False and state['full_compared_at'] == stamp
    assert state['refresh_failure']['components'] == [{'component': component, 'reason': reason}]
    assert component in displayed(product)['message']
    assert not scheduled_tasks.recent_success(product, drafts.configuration())


def test_access_denial_preserves_old_checkpoint_and_actual_drift_stays_visible(world, monkeypatch):
    product, row, adapter = existing(world)
    saved = ShopifyProductComparison.query.one()
    stamp = json.loads(saved.summary_json)['full_compared_at']
    original = adapter.read_simple
    def denied(*args, **kwargs):
        raise ShopifyError('Synthetic product read denied.', category='access_denied')
    monkeypatch.setattr(adapter, 'read_simple', denied)
    result = shopify_status.refresh([product], force=True, adapter=adapter)
    assert result['errors'] and json.loads(saved.summary_json)['full_compared_at'] == stamp
    assert displayed(product)['label'] == 'Verification Unavailable'
    monkeypatch.setattr(adapter, 'read_simple', original)
    adapter.remote['title'] = 'Synthetic real remote drift'
    shopify_status.refresh([product], force=True, adapter=adapter)
    assert displayed(product)['label'] == 'Update Available'
    assert json.loads(saved.summary_json)['read_complete'] is True


@pytest.mark.parametrize('count', [0, 21])
def test_gallery_safety_reports_production_reason_not_selection_instruction(world, monkeypatch, count):
    product, row, adapter = existing(world)
    images = copy.deepcopy(adapter.remote['media'])
    monkeypatch.setattr(drafts, 'product_image_diagnostics', lambda p: [{} for _ in range(count)])
    result = shopify_status.refresh([product], force=True, adapter=adapter)
    message = result['errors'][0]['message']
    assert 'Gallery evidence' in message and 'Select a product' not in message
    assert ('no original' if count == 0 else '21 images; limit 20') in message
    assert adapter.remote['media'] == images
    assert displayed(product)['label'] == 'Verification Unavailable'


def test_scheduler_renews_package_product_and_groups_error_summary(world):
    from test_scheduled_tasks import run
    product, row, adapter = existing(world)
    pkg = linked()
    change(world, packaging={'selected_package_id': pkg.id})
    _, token = drafts.preview(product, adapter)
    drafts.confirm(token, True, adapter)
    result, data = run(product, adapter, manual=True)
    assert result.state == 'completed' and data['counts']['up_to_date'] == 1
    assert data['counts']['unavailable'] == 0 and data['counts']['checked'] == 1
    assert scheduled_tasks.recent_success(product, drafts.configuration())
    summary = {}
    scheduled_tasks.group_errors(summary, [{'message': 'Same missing component'}] * 10)
    assert summary['errors'] == ['Same missing component (10 products)']
    scheduled_tasks.group_errors(summary, [{'message': 'Same missing component'}])
    assert summary['errors'] == ['Same missing component (11 products)']


def test_unconfigured_packed_size_does_not_make_optional_api_required(world, monkeypatch):
    from test_shopify_native_dimensions import NativeAdapter
    monkeypatch.setenv('SHOPIFY_NATIVE_DIMENSIONS_ENABLED', 'true')
    product = world[0]
    adapter = NativeAdapter()
    adapter.capable = False
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    assert row.state == 'verified'
    result = shopify_status.refresh([product], force=True, adapter=adapter)
    assert result['errors'] == []
    assert json.loads(ShopifyProductComparison.query.one().summary_json)['read_complete'] is True
    assert displayed(product)['label'] == 'Up to Date'
    assert scheduled_tasks.recent_success(product, drafts.configuration())
    assert 'native_write' not in adapter.calls


def test_verification_diagnostic_is_read_only_and_contains_no_values(world):
    from app.shopify_attribute_diagnostic import verification
    product, row, adapter = existing(world)
    original = ShopifyProductComparison.query.one().summary_json
    report = verification(row)
    assert report['api_requests'] == report['database_writes'] == 0
    assert report['gallery']['count'] == 2 and report['gallery']['unresolved'] == 0
    assert report['comparison']['read_complete'] is True
    assert ShopifyProductComparison.query.one().summary_json == original
    assert 'description' not in json.dumps(report) and 'nonce' not in json.dumps(report)


def test_authored_packed_size_still_requires_capability_evidence(world, monkeypatch):
    from test_shopify_native_dimensions import NativeAdapter
    monkeypatch.setenv('SHOPIFY_NATIVE_DIMENSIONS_ENABLED', 'true')
    product = world[0]
    change(world, packaging={'packed_dimensions_cm': {'length': '16', 'width': '16', 'height': '.4'}, 'measured': True})
    adapter = NativeAdapter()
    adapter.capable = False
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    result = shopify_status.refresh([product], force=True, adapter=adapter)
    saved = json.loads(ShopifyProductComparison.query.one().summary_json)
    assert result['errors'] and saved['read_complete'] is False
    assert saved['missing_evidence'][0]['component'] == 'native_packed_dimensions'
    assert not scheduled_tasks.recent_success(product, drafts.configuration())
    assert displayed(product)['label'] == 'Verification Unavailable'
    assert 'native_write' not in adapter.calls


def test_unconfigured_variable_capability_failure_is_advisory(variable_world, monkeypatch):
    monkeypatch.setenv('SHOPIFY_NATIVE_DIMENSIONS_ENABLED', 'true')
    product = variable_world[0]
    adapter = VariantAdapter()
    def unavailable(*args):
        raise ShopifyError('Synthetic native capability unavailable.')
    monkeypatch.setattr(adapter, 'packed_capability', unavailable)
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    result = shopify_status.refresh([product], force=True, adapter=adapter)
    assert result['errors'] == [] and row.state == 'verified'
    assert displayed(product)['label'] == 'Up to Date'
    assert scheduled_tasks.recent_success(product, drafts.configuration())
    assert 'native_write' not in adapter.calls
