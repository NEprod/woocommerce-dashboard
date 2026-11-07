"""Isolated editor, signed publishing and exact 2027-01 Y081 transport contract."""
import copy
import json
import time

import pytest

from app import db, shopify_drafts as drafts
from app.models import Product, ShopifyProductIdentity, ShopifyPublishJob
from app.shopify_capacity import background_work
from app.shopify_connection import EU_IDENTIFIER_READ, EU_IDENTIFIER_WRITE, ShopifyAdapter, ShopifyError
from app.shopify_eu_identifier import KEY
from app.shopify_simple_fields import review_rows
from app.taxonomy_assignments import product_document
from test_metadata_editor_core import core, save
from test_phase3_woo_publish_preview import _client, preview_app
from test_shopify_drafts import world
from test_shopify_markets import saved_state
from test_shopify_simple_updates import UpdateAdapter, change
from test_shopify_variables import VariableAdapter, variable_world


class ComplianceFixture:
    read_eu_identifier = ShopifyAdapter.read_eu_identifier
    write_eu_identifier = ShopifyAdapter.write_eu_identifier

    def __init__(self):
        super().__init__()
        self.enabled = set()
        self.standardized = set()
        self.requests = []
        self.writes = []
        self.problem = None

    def _transport(self, config, query, variables=None, *, api_version=None, **kwargs):
        assert config.api_version == '2026-10' and api_version == '2027-01'
        self.requests.append(query)
        if query == EU_IDENTIFIER_READ:
            if self.problem == 'unsupported':
                raise ShopifyError('GraphQL schema not available.')
            assert variables['hasVariants'] is bool(variables['ids'])
            result = {'nodes': []} if variables['hasVariants'] else {}
            if variables['probe']:
                result.update(bulk={'inputFields': [{'name': 'compliance'}]},
                    declaration={'inputFields': [{'name': 'key'}, {'name': 'value'}]},
                    keys={'enumValues': [{'name': 'Y081'}]})
            for vid in variables['ids']:
                assert vid in {node['id'] for node in self.remote['variants']['nodes']}
                compliance = [{'key': 'M_PID', 'value': vid}, {'key': 'NS_PID', 'value': 'fixture'}]
                if vid in self.enabled and self.problem != 'stale':
                    compliance.append({'key': 'Y081', 'value': 'true'})
                if vid in self.standardized:
                    compliance.append({'key': 'S_PID', 'value': '9780262033848'})
                result['nodes'].append({'id': vid,
                    'product': {k: self.remote[k] for k in ('id', 'metafield')}, 'compliance': compliance})
            return result
        assert query == EU_IDENTIFIER_WRITE and 'allowPartialUpdates: false' in query
        row = ShopifyProductIdentity.query.filter_by(shopify_product_id=variables['id']).one()
        assert drafts.evidence(row)['eu_identifier_attempt']['state'] == 'pending'
        self.writes.append(copy.deepcopy(variables))
        if self.problem == 'rejected':
            return {'productVariantsBulkUpdate': {'userErrors': [{'message': 'Rejected'}]}}
        for v in variables['variants']:
            assert set(v) == {'id', 'compliance'}
            assert v['compliance'][0]['key'] == 'Y081'
            (self.enabled.add if v['compliance'][0]['value'] == 'true' else self.enabled.discard)(v['id'])
        return {'productVariantsBulkUpdate': {'userErrors': [],
            'productVariants': [{'id': v['id']} for v in variables['variants']]}}


class SimpleCompliance(ComplianceFixture, UpdateAdapter):
    pass


class VariableCompliance(ComplianceFixture, VariableAdapter):
    pass


@pytest.mark.parametrize('kind,variable_world', [('simple', 'Single Variable'), ('variable', 'Single Variable')], indirect=['variable_world'])
def test_signed_create_disable_and_unmanage(world, variable_world, kind):
    product = world[0] if kind == 'simple' else variable_world[0]
    path = world[-1] if kind == 'simple' else variable_world[1]
    adapter = SimpleCompliance() if kind == 'simple' else VariableCompliance()

    def author(value):
        data = json.loads(path.read_text())
        if value is None:
            data.pop(KEY)
        else:
            data[KEY] = value
        path.write_text(json.dumps(data))

    author(True)
    plan, token = drafts.preview(product, adapter)
    assert token and all(t['id'] is None for t in plan['eu_identifier']['targets'])
    assert not adapter.writes
    row = drafts.confirm(token, True, adapter)
    assert row.state == 'verified', drafts.evidence(row).get('error')
    assert drafts.evidence(row)['eu_identifier_attempt']['state'] == 'verified'
    ids = {n['id'] for n in adapter.remote['variants']['nodes']}
    assert len(ids) == (1 if kind == 'simple' else len(product.variations))
    assert ids == adapter.enabled
    assert {v['id'] for v in adapter.writes[0]['variants']} == ids
    assert all(v['compliance'] == [{'key': 'Y081', 'value': 'true'}] for v in adapter.writes[0]['variants'])
    previous = copy.deepcopy(adapter.remote)
    author(False)
    plan, token = drafts.preview(product, adapter)
    changes = [r for r in review_rows(plan) if r['label'].startswith('No EU standardized identifier')]
    assert len(changes) == len(ids)
    assert all((r['before'], r['after'], r['action']) == ('Enabled', 'Disabled', 'Change') for r in changes)
    row = drafts.confirm(token, True, adapter)
    assert row.state == 'verified' and adapter.remote == previous and adapter.remote['status'] == 'DRAFT'
    assert not adapter.enabled and len(adapter.writes) == 2
    assert all(v['compliance'] == [{'key': 'Y081', 'value': 'false'}] for v in adapter.writes[-1]['variants'])
    assert saved_state(product) == 'no_change'
    assert drafts.preview(product, adapter)[1] is None
    author(None)
    adapter.enabled.update(ids)  # Remote declaration stays unmanaged, even when true.
    count = len(adapter.requests)
    plan, token = drafts.preview(product, adapter)
    assert 'eu_identifier' not in plan and token is None and len(adapter.requests) == count
    assert adapter.enabled == ids and len(adapter.writes) == 2


def test_signed_enable_late_readback_reconciles_without_replay(world, preview_app, monkeypatch):
    product = world[0]
    adapter = SimpleCompliance()
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    assert row.state == 'verified' and not adapter.requests
    change(world, **{KEY: True})
    with background_work(lambda: None, publishing_context={'authority': {'kind': 'manual_sync_now'}}):
        plan, token = drafts.preview(product, adapter)
        assert token is None and any('explicit Review & Sync' in b for b in plan['blockers'])
    plan, token = drafts.preview(product, adapter)
    assert token and saved_state(product) == 'update'
    monkeypatch.setattr(drafts, 'ShopifyAdapter', lambda: adapter)
    response = _client(preview_app).post(f'/products/{product.id}/shopify/review', data={'action': 'preview'})
    assert response.status_code == 200
    assert b'No EU standardized identifier' in response.data and b'>Enabled<' in response.data
    adapter.problem = 'stale'
    row = drafts.confirm(token, True, adapter)
    assert row.state == 'uncertain' and saved_state(product) == 'recovery_required'
    assert drafts.evidence(row)['eu_identifier_attempt']['state'] == 'acknowledged'
    assert len(adapter.writes) == 1
    adapter.problem = None
    drafts.reconcile(row, adapter=adapter)
    assert row.state == 'verified' and saved_state(product) == 'no_change'
    assert len(adapter.writes) == 1


def test_rejected_declaration_keeps_recovery_and_is_not_replayed(world):
    product = world[0]
    adapter = SimpleCompliance()
    _, token = drafts.preview(product, adapter)
    drafts.confirm(token, True, adapter)
    change(world, **{KEY: True})
    _, token = drafts.preview(product, adapter)
    adapter.problem = 'rejected'
    row = drafts.confirm(token, True, adapter)
    assert row.state == 'uncertain' and not adapter.enabled
    assert 'Shopify rejected fields' in drafts.evidence(row)['error']
    drafts.reconcile(row, adapter=adapter)
    assert row.state == 'uncertain' and len(adapter.writes) == 1


def test_unsupported_schema_and_existing_standardized_id_are_blockers(world):
    product = world[0]
    adapter = SimpleCompliance()
    change(world, **{KEY: True})
    adapter.problem = 'unsupported'
    with pytest.raises(ShopifyError, match='2027-01 Y081 readback is unavailable'):
        drafts.preview(product, adapter)
    assert adapter.remote is None and not adapter.writes
    adapter.problem = None
    change(world, **{KEY: False})
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    assert row.state == 'verified' and not adapter.writes
    adapter.standardized.add(drafts.evidence(row)['variant_id'])
    change(world, **{KEY: True})
    plan, token = drafts.preview(product, adapter)
    assert token is None and any('standardized identifier' in blocker for blocker in plan['blockers'])
    assert not adapter.writes


def test_collection_checkbox_scoped_save_projection_and_override(core, monkeypatch):
    from app.utils import scan_runner
    app, client, root, _, ids, *_ = core
    name = 'Simple Collection/Blue Token'
    product_id = ids[name]
    monkeypatch.setattr('app.routes.start_scan', scan_runner.start_scan)
    monkeypatch.setattr('app.scheduled_tasks.queue_projected', lambda op: None)
    monkeypatch.setattr('app.shopify_connection.ShopifyAdapter._transport',
                        lambda *a, **k: pytest.fail('The editor contacted Shopify'))
    page = client.get(f'/edit_products/{product_id}/edit/shared')
    assert page.status_code == 200
    assert b'type="checkbox" data-field="no_eu_standardized_identifier"' in page.data
    assert b'data-clear-eu-identifier' in page.data
    shared = root/'Simple Collection/product_info.json'
    override = root/name/'product_info.json'

    def assert_saved(kind, value, expected):
        result = save(core, name, kind=kind, data={KEY: value} if value is not None else None,
                      removals=[[KEY]] if value is None else None)
        assert result.status_code == 200, result.json
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with app.app_context():
                progress = scan_runner.get_progress(result.json['run_id'])
            if progress['status'] in {'done', 'error'}:
                break
            time.sleep(.03)
        assert progress['status'] == 'done', progress
        doc = json.loads((shared if kind == 'shared' else override).read_text())
        assert (KEY not in doc) if value is None else (doc[KEY] is value)
        with app.app_context():
            product = db.session.get(Product, product_id)
            job = db.session.get(ShopifyPublishJob, product_id)
            authored = json.loads(job.view_json)['authored']
            assert authored.get(KEY) is expected and product_document(product).get(KEY) is expected
            if expected is None:
                assert KEY not in authored

    assert_saved('shared', True, True)
    assert_saved('override', False, False)
    assert_saved('override', None, True)
    assert_saved('shared', None, None)
    invalid = save(core, name, kind='shared', data={KEY: 'false'})
    assert invalid.status_code == 400
