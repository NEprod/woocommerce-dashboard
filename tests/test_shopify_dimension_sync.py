"""M3A.9.5.7: actual destination evidence, not local-save equality, owns sync."""
import copy
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from app import db, shopify_drafts as drafts, shopify_comparison as comparison
from app.models import ShopifyProductComparison, ShopifyProductIdentity
from app.shopify_connection import ShopifyError, ShopifyAdapter, DIMENSION_DEFINITIONS
from app.shopify_simple_fields import review_rows
from test_shopify_drafts import world
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_simple_updates import created, change, UpdateAdapter
from test_shopify_native_dimensions import NativeAdapter
from test_shopify_variables import variable_world, create, rescan, VariableAdapter
from test_shopify_status_reconciliation import RevisionAdapter, displayed
from test_shopify_foundation import isolated_transport, Session, Response


class DefinitionAdapter(UpdateAdapter):
    def __init__(self):
        super().__init__()
        self.dimension_fields = {}
        self.lose_definition_response = False

    def create_attribute_resource(self, config, kind, spec):
        assert kind == 'field' and spec['key'].startswith('product_')
        # A separate DB connection must see the write intent BEFORE Shopify.
        with db.engine.connect() as connection:
            raw = connection.execute(text('select media_evidence_json from shopify_product_identity')).scalar_one()
            retained = drafts.evidence(SimpleNamespace(media_evidence_json=raw))
            key = spec['ownerType'] + '.' + spec['key']
            assert retained['dimension_definitions'][key]['state'] == 'pending'
        self.calls.append('dimension_definition_create')
        field = {'id': 'gid://shopify/MetafieldDefinition/900', 'namespace': spec['namespace'],
            'key': spec['key'], 'ownerType': spec['ownerType'], 'type': {'name': 'dimension'}, 'access': spec['access']}
        self.dimension_fields[spec['ownerType']][spec['key'].removeprefix('product_')] = field
        if self.lose_definition_response:
            raise ShopifyError('Fictional lost definition response; reconcile, never retry.')
        return {'metafieldDefinitionCreate': {'createdDefinition': {'id': field['id']}, 'userErrors': []}}


def existing(world, adapter=None):
    change(world, dimensions={'length': '155', 'width': '155', 'height': '3'})
    adapter = adapter or DefinitionAdapter()
    plan, token = drafts.preview(world[0], adapter)
    row = drafts.confirm(token, True, adapter)
    assert row.state == 'verified'
    adapter.calls.clear()
    adapter.inputs.clear()
    return world[0], row, adapter


def test_unchanged_old_product_missing_actual_metafields_is_new(world):
    product, row, adapter = existing(world)
    before = world[2].read_bytes()
    retained_ids = (row.shopify_product_id, drafts.evidence(row)['variant_id'], drafts.evidence(row)['inventory_item_id'])
    for axis in ('length', 'width', 'height'): adapter.remote[axis] = None
    plan, token = drafts.preview(product, adapter)
    assert plan['classification'] == 'update' and token
    size = [r for r in review_rows(plan) if r['group'] == 'finished' and 'definition' not in r['label']]
    assert len(size) == 3 and all(r['action'] == 'New Shopify field — Finished Product Dimensions' for r in size)
    assert displayed(product)['label'] == 'Update Available'
    drafts.confirm(token, True, adapter)
    assert retained_ids == (row.shopify_product_id, drafts.evidence(row)['variant_id'], drafts.evidence(row)['inventory_item_id'])
    assert world[2].read_bytes() == before
    assert not any(c in adapter.calls for c in ('variant_write', 'stage_write', 'product_create'))
    assert drafts.preview(product, adapter)[0]['classification'] == 'no_change'


def test_missing_definition_and_access_repair_independent_of_matching_values(world):
    product, row, adapter = existing(world)
    retained = drafts.evidence(row)
    retained.pop('dimension_definitions', None)  # old receipt: values existed without managed definitions
    drafts.save(row, retained)
    public = adapter.dimension_definitions(drafts.configuration(), 'tlc_dashboard', 'PRODUCT')
    public['length'] = None
    public['width']['access']['storefront'] = 'NONE'
    old_width_id = public['width']['id']
    adapter.dimension_fields['PRODUCT'] = public
    plan, token = drafts.preview(product, adapter)
    assert plan['classification'] == 'update' and token
    assert not any(f['changed'] for f in plan['fields'] if f['field'].startswith('dimensions.'))
    drafts.confirm(token, True, adapter)
    assert adapter.calls.count('dimension_definition_create') == 1
    assert adapter.calls.count('dimension_definition_update') == 1
    assert 'product_update' not in adapter.calls and 'variant_write' not in adapter.calls
    assert adapter.dimension_fields['PRODUCT']['width']['id'] == old_width_id
    assert all(p['state'] == 'verified' for p in drafts.evidence(row)['dimension_definitions'].values())
    assert drafts.preview(product, adapter)[0]['classification'] == 'no_change'


def test_uncertain_definition_create_is_read_only_reconciled_without_duplicate(world):
    product, row, adapter = existing(world)
    retained = drafts.evidence(row)
    retained.pop('dimension_definitions', None)
    drafts.save(row, retained)
    public = adapter.dimension_definitions(drafts.configuration(), 'tlc_dashboard', 'PRODUCT')
    public['length'] = None
    adapter.dimension_fields['PRODUCT'] = public
    adapter.lose_definition_response = True
    _, token = drafts.preview(product, adapter)
    drafts.confirm(token, True, adapter)
    assert row.state == 'uncertain'
    assert adapter.calls.count('dimension_definition_create') == 1
    result = drafts.reconcile(row, adapter=adapter)
    assert result.state == 'verified'
    assert adapter.calls.count('dimension_definition_create') == 1


def test_changed_dimension_and_equivalent_remote_unit(world):
    product, row, adapter = existing(world)
    adapter.remote['length']['value'] = '{"value":15.5,"unit":"CENTIMETERS"}'
    assert drafts.preview(product, adapter)[0]['classification'] == 'no_change'
    adapter.remote['length']['value'] = '{"value":14,"unit":"CENTIMETERS"}'
    rows = review_rows(drafts.preview(product, adapter)[0])
    assert any(r['action'] == 'Changed Shopify field — Finished Product Dimensions' for r in rows)


@pytest.mark.parametrize('bad', ['wrong_type', 'wrong_owner', 'wrong_namespace', 'missing_access'])
def test_incompatible_definition_never_recreated(world, bad):
    product, row, adapter = existing(world)
    public = adapter.dimension_definitions(drafts.configuration(), 'tlc_dashboard', 'PRODUCT')
    value = public['length']
    if bad == 'wrong_type': value['type'] = {'name': 'json'}
    elif bad == 'wrong_owner': value['ownerType'] = 'PRODUCTVARIANT'
    elif bad == 'wrong_namespace': value['namespace'] = 'other'
    else: value['access'] = {}
    adapter.dimension_fields['PRODUCT'] = public
    with pytest.raises(ShopifyError, match='incompatible'): drafts.preview(product, adapter)
    assert 'dimension_definition_create' not in adapter.calls


def test_legacy_success_forces_bounded_full_read_without_local_edit(world):
    from app.shopify_status import refresh
    product, row, adapter = existing(world, RevisionAdapter())
    saved = ShopifyProductComparison.query.one()
    old = json.loads(saved.summary_json)
    old.pop('dimension_metadata_policy')
    saved.summary_json = json.dumps(old)
    db.session.commit()
    adapter.remote['height'] = None
    assert displayed(product)['label'] != 'Up to Date'
    result = refresh([product], adapter=adapter)
    assert result['full_comparisons'] == 1
    assert displayed(product)['label'] == 'Update Available'
    adapter.calls.clear()
    assert refresh([product], adapter=adapter)['full_comparisons'] == 0
    assert not adapter.calls


def test_local_only_package_change_visible_without_remote_write(world):
    from app.packaging_catalogue import initial_rows
    product, row, adapter = existing(world)
    package_id = next(initial_rows())['id']
    change(world, packaging={'selected_package_id': package_id, 'assignment_kind': 'accepted'})
    before = copy.deepcopy(adapter.remote)
    plan, token = drafts.preview(product, adapter)
    assert plan['classification'] == 'no_change' and token is None
    assert plan['local_package_changes'] == 1 and plan['optional_packages'][0]['selected_id'] == package_id
    assert plan['optional_packages'][0]['state'] == 'Shopify Package ID required'
    assert before == adapter.remote and not adapter.inputs
    change(world, packaging={'selected_package_id': None})
    plan, _ = drafts.preview(product, adapter)
    assert plan['optional_packages'][0]['state'] == 'Explicitly Unassigned'
    assert plan['classification'] == 'no_change'


def test_packed_disabled_is_visible_not_falsely_verified(world, monkeypatch):
    product, row, adapter = existing(world)
    monkeypatch.setenv('SHOPIFY_NATIVE_DIMENSIONS_ENABLED', 'false')
    change(world, packaging={'packed_dimensions': {'length': '16', 'width': '16', 'height': '.4', 'unit': 'cm'}, 'measured': True})
    plan, _ = drafts.preview(product, adapter)
    assert plan['native']['desired']['length'] == 16
    assert 'Awaiting Shopify API capability' in plan['native']['reason']
    assert not json.loads(ShopifyProductComparison.query.one().summary_json)['read_complete']
    assert displayed(product)['label'] != 'Up to Date'
    assert 'native_write' not in adapter.calls


def test_review_renders_three_groups_with_real_missing_values_and_local_choice(world, monkeypatch):
    product, row, adapter = existing(world)
    from app.packaging_catalogue import initial_rows
    change(world, packaging={'selected_package_id': next(initial_rows())['id'],
        'packed_dimensions_cm': {'length': '16', 'width': '16', 'height': '.4'}, 'measured': True})
    adapter.remote['length'] = None
    monkeypatch.setattr(drafts, 'ShopifyAdapter', lambda: adapter)
    from flask import current_app
    response = _client(current_app._get_current_object()).post('/products/1/shopify/review', data={'action': 'preview'})
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    for label in ('Product Dimensions — Customer-Facing', 'Packed Dimensions — Shopify Shipping', 'Optional Package',
                  'New Shopify field — Finished Product Dimensions', 'Local Configuration Changed', 'Awaiting Shopify API capability'):
        assert label in body
    assert 'Product dimensions are reused' not in body
    assert drafts.evidence(row)['inventory_item_id'] in body


def test_retained_definition_removal_requires_recovery_not_recreation(world):
    product, row, adapter = existing(world)
    public = adapter.dimension_definitions(drafts.configuration(), 'tlc_dashboard', 'PRODUCT')
    public['length'] = None
    adapter.dimension_fields['PRODUCT'] = public
    with pytest.raises(ShopifyError, match='removed or replaced'):
        drafts.preview(product, adapter)
    assert 'dimension_definition_create' not in adapter.calls


def test_supported_metadata_finishes_partial_when_authored_native_is_disabled(world, monkeypatch):
    from app.models import CatalogueOperation
    monkeypatch.setenv('SHOPIFY_NATIVE_DIMENSIONS_ENABLED', 'false')
    product, row, adapter = existing(world)
    change(world, description='Supported metadata correction', packaging={
        'packed_dimensions_cm': {'length': '16', 'width': '16', 'height': '.4'}, 'measured': True})
    plan, token = drafts.preview(product, adapter)
    assert token and plan['native']['pending_capability']
    drafts.confirm(token, True, adapter)
    assert row.state == 'verified' and adapter.remote['descriptionHtml'] == 'Supported metadata correction'
    assert db.session.get(CatalogueOperation, row.operation_id).status == 'partial'
    assert displayed(product)['label'] == 'Needs Attention'
    assert 'native_write' not in adapter.calls


def test_disabled_variable_native_never_claims_complete_operation(variable_world, monkeypatch):
    from app.models import CatalogueOperation
    monkeypatch.setenv('SHOPIFY_NATIVE_DIMENSIONS_ENABLED', 'false')
    rescan(variable_world, packaging={'packed_dimensions_cm': {'length': '16', 'width': '16', 'height': '.4'}, 'measured': True})
    row, adapter = create(variable_world)
    assert db.session.get(CatalogueOperation, row.operation_id).status == 'partial'
    assert displayed(variable_world[0])['label'] == 'Needs Attention'
    assert 'native_write' not in adapter.calls


def test_shared_access_change_after_signature_requires_fresh_review(world):
    product, row, adapter = existing(world)
    public = adapter.dimension_definitions(drafts.configuration(), 'tlc_dashboard', 'PRODUCT')
    public['width']['access']['storefront'] = 'NONE'
    adapter.dimension_fields['PRODUCT'] = public
    _, token = drafts.preview(product, adapter)
    adapter.dimension_fields['PRODUCT']['width']['access']['storefront'] = 'PUBLIC_READ'
    with pytest.raises(ShopifyError): drafts.confirm(token, True, adapter)
    assert 'dimension_definition_update' not in adapter.calls and not adapter.inputs


def test_review_mobile_cards_override_generic_table_minimum():
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1] / 'app/static/assets/css/custom.css').read_text()
    assert '.table.shopify-review-table { min-width: 0; }' in css


@pytest.mark.parametrize('variable_world', ['Variable Collection', 'Single Variable'], indirect=True)
def test_size_modifiers_use_variant_metadata_and_local_package_exceptions(variable_world):
    from app.packaging_catalogue import initial_rows
    product, shared, *_ = variable_world
    doc = json.loads(shared.read_text())
    size1, size2 = doc['attributes']['Size']
    doc['variation_modifiers']['Size=' + size1] = {'dimensions': {'length': '155', 'width': '100', 'height': '3'},
        'packaging': {'selected_package_id': next(initial_rows())['id'], 'assignment_kind': 'accepted'}}
    doc['variation_modifiers']['Size=' + size2] = {'dimensions': {'length': '297', 'width': '210', 'height': '3'},
        'packaging': {'selected_package_id': None}}
    rescan(variable_world, **doc)
    row, adapter = create(variable_world)
    ids = copy.deepcopy(drafts.evidence(row)['variant_maps'])
    for variant in adapter.remote['variants']['nodes']: variant['length'] = None
    plan, token = drafts.preview(product, adapter)
    assert plan['classification'] == 'update' and not plan['managed']['product'].get('metafields')
    assert {s['spec']['ownerType'] for s in plan['dimension_metadata']['definitions']} == {'PRODUCTVARIANT'}
    assert all('Variant ' in r['label'] for r in review_rows(plan) if r['group'] == 'finished' and 'definition' not in r['label'])
    assert any(p['selected_id'] for p in plan['optional_packages']) and any(p['state'] == 'Explicitly Unassigned' for p in plan['optional_packages'])
    drafts.confirm(token, True, adapter)
    assert drafts.evidence(row)['variant_maps'] == ids
    assert drafts.preview(product, adapter)[0]['classification'] == 'no_change'


def test_definition_batch_read_and_owner_update_use_normal_api(isolated_transport, monkeypatch):
    session = Session(Response({'data': {axis: {'nodes': [], 'pageInfo': {'hasNextPage': False}} for axis in ('length', 'width', 'height')}}))
    adapter = ShopifyAdapter()
    monkeypatch.setattr('requests.Session', lambda: session)
    from app.shopify_connection import ShopifyConfiguration
    from test_shopify_foundation import TOKEN
    config = ShopifyConfiguration('fictional.myshopify.com', TOKEN, '2026-10')
    assert adapter.dimension_definitions(config, 'tlc_dashboard', 'PRODUCTVARIANT') == dict.fromkeys(('length', 'width', 'height'))
    url, args = session.calls[0]
    assert '/2026-10/' in url and args['json']['query'] == DIMENSION_DEFINITIONS
    assert args['json']['variables']['owner'] == 'PRODUCTVARIANT'
