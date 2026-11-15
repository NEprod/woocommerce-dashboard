"""Isolated product BOM inheritance, editor saves and cost-only Shopify writes."""
import copy
import json
from decimal import Decimal

import pytest

from flask import current_app
from app import db, automatic_publishing as publisher, shopify_drafts as drafts
from app.inventory import product_bom as bom, service as stock
from app.inventory.models import Item, Movement
from app.models import Product, ShopifyProductIdentity
from app.shopify_connection import ShopifyAdapter, ITEM_COST_WRITE, ShopifyError
from test_inventory import client_for, item, manufactured, transact, workshop
from test_setup_flow import setup_app
from test_metadata_editor_core import core, save
from test_phase3_woo_publish_preview import preview_app
from test_shopify_drafts import world
from test_shopify_variables import VariableAdapter, variable_world
from test_shopify_publication_sales import AcceptanceAdapter
from test_automatic_publishing import authorise, execute, notices, queued


def add(item, quantity='1', unit='piece'):
    return {'op':'add', 'item_id':item, 'quantity':quantity, 'unit':unit}


def calculate(lines):
    return {'lines':lines, 'cost':{'mode':'calculated'}}


def manual(amount):
    return {'cost':{'mode':'manual', 'amount':amount}}


def test_layered_resolution_reset_shared_growth_and_variant_identity(workshop):
    client = client_for(workshop)
    box = item(client, 'purchased', 'BOX', 'Box', pack_qty='10', pack_unit='piece', pack_price='6')
    sleeve = item(client, 'purchased', 'SLEEVE', 'Sleeve', pack_qty='20', pack_unit='piece', pack_price='3')
    paper = item(client, 'purchased', 'PAPER', 'Paper', pack_qty='20', pack_unit='piece', pack_price='1')
    insert = item(client, 'purchased', 'INSERT', 'Insert', pack_qty='1', pack_unit='piece', pack_price='0.15')
    parent = calculate({'box':add(box), 'sleeve':add(sleeve)})
    child = {'lines':{'insert':add(insert), 'sleeve':{'op':'modify','quantity':'2'}}, 'variants':{
        'B-01':{'lines':{'box':{'op':'remove'},'paper':add(paper,'3')}},
        'B-02':manual('0.00')}}
    original = copy.deepcopy(parent)
    with workshop.app_context():
        assert bom.resolve(parent, child)['shopify'] == '1.05'
        assert bom.resolve(parent, child, 'B-01')['shopify'] == '0.60'
        assert bom.resolve(parent, child, 'B-02')['shopify'] == '0.00'
        assert bom.resolve(parent, child, 'B-02')['estimated'] == '1.05'
        assert parent == original
        parent['lines']['thanks'] = add(paper)
        rows = {r['line_id']:r for r in bom.resolve(parent, child)['rows']}
        assert set(rows) == {'box','sleeve','insert','thanks'} and rows['sleeve']['quantity'] == '2'
        del child['lines']['sleeve']
        assert bom.resolve(parent, json.loads(json.dumps(child)))['shopify'] == '0.95'
        del parent['lines']['box']
        assert bom.resolve(parent, child, 'B-01')['state'] == 'incomplete'
        assert bom.resolve(parent, child, 'B-01')['shopify'] is None
        empty = bom.resolve({'lines':{'paper':add(paper)}}, {'lines':{'paper':{'op':'remove'}}})
        assert empty['state'] == 'empty' and empty['shopify'] is None
        assert bom.resolve({})['state'] == 'not_configured'
        assert Movement.query.count() == 0


def test_inventory_basis_precision_manufactured_and_unknown_cost(workshop):
    client = client_for(workshop)
    filament = item(client, 'material', 'PLA', 'PLA', unit='g', pack_qty='1', pack_unit='kg', pack_price='22.50')
    body = manufactured(client, [(filament,'40','g')])
    unknown = item(client, 'material', 'TAPE', 'Tape', unit='m')
    free = item(client, 'purchased', 'FREE', 'Free clip', pack_qty='1', pack_unit='piece', pack_price='0')
    parent = calculate({'component':add(body), 'direct':add(filament,'0.022','kg'), 'known-free':add(free)})
    with workshop.app_context():
        priced = bom.resolve(parent)
        assert Decimal(priced['estimated']) == Decimal('0.720')
        assert priced['shopify'] == '0.72'
        assert Decimal(priced['rows'][0]['cost']) == Decimal('0.225')
        assert bom.resolve(calculate({'component':add(body)}))['shopify'] == '0.23'
        parent['lines']['unknown'] = add(unknown, '0.5', 'm')
        assert bom.resolve(parent)['shopify'] is None
        assert bom.resolve(parent, manual('0'))['shopify'] == '0.00'
        parent['lines']['unknown'] = add(99999, '0.5', 'm')
        assert 'missing' in bom.resolve(parent)['warnings'][0]
        parent['lines']['unknown'] = add(unknown, '1', 'sheet')
        assert 'Cannot convert' in bom.resolve(parent)['warnings'][0]
    receipt = transact(client, 'receive', filament, qty='200', unassigned='yes', cost='8')
    with workshop.app_context():
        entry = Movement.query.filter_by(item_id=filament).one()
        assert Decimal(stock.rate(db.session.get(Item, filament))) == Decimal('0.04')
        assert entry.cost == 8 * stock.SCALE and receipt
        assert bom.resolve(calculate({'component':add(body)}))['shopify'] == '0.40'
        db.session.get(Item, filament).pack_price = 99 * stock.SCALE
        db.session.commit()
        assert bom.resolve(calculate({'component':add(body)}))['shopify'] == '0.40'
        assert db.session.get(Movement, entry.id).cost == 8 * stock.SCALE


def test_real_editor_save_sparse_override_and_readonly_preview(core):
    app, client, root, _, parents, *_ = core
    stock_id = item(client, 'purchased', 'BOX', 'Box', pack_qty='1', pack_unit='piece', pack_price='1')
    path = 'Simple Collection/Blue Token'
    pid = parents[path]
    with app.test_request_context():
        from flask import url_for
        editor = url_for('main.product_edit', product_id=pid, label='override')
    assert b'data-production-editor' in client.get(editor).data
    assert save(core, path, kind='shared', data={'production': calculate({'box':add(stock_id)})}).status_code == 200
    source = (root / 'Simple Collection/product_info.json').read_bytes()
    delta = {'lines':{'box':{'op':'modify','quantity':'2'}}}
    assert save(core, path, data={'production':delta}).status_code == 200
    override = root / path / 'product_info.json'
    assert json.loads(override.read_text())['production'] == delta
    assert (root / 'Simple Collection/product_info.json').read_bytes() == source
    r = client.post(f'/api/products/{pid}/production-costing', json={'kind':'override','production':delta})
    assert r.status_code == 200 and r.json['parent']['shopify'] == '2.00'
    assert json.loads(override.read_text())['production'] == delta
    assert save(core, path, removals=[['production']]).status_code == 200
    with app.app_context():
        product = db.session.get(Product, pid)
        assert bom.contract(product)[product.sku]['value'] == '1.00'
        assert Movement.query.count() == 0


class CostMock:
    def test_connection(self, config):
        result = super().test_connection(config)
        result['scopes'] += ['read_inventory'] + (['write_inventory'] if self.inventory_permission else [])
        if not self.inventory_permission:
            result['scopes'] = [s for s in result['scopes'] if s != 'write_inventory']
        return result

    def __init__(self):
        super().__init__()
        self.costs, self.cost_writes = {}, []
        self.currency, self.inventory_permission, self.lose_write = 'GBP', True, False
        self.ignore_cost_write = False

    def read_item_costs(self, config, ids):
        source = self.remote or {}
        actual = source.get('simpleVariants', source.get('variants', {})).get('nodes', [])
        nodes = []
        for vid in ids:
            node = next(v for v in actual if v['id'] == vid)
            iid = node['inventoryItem']['id']
            cost = self.costs.get(iid)
            nodes.append({'id':vid, 'product':{'id':source['id'], 'metafield':source['metafield']},
                          'inventoryItem':{'id':iid, 'unitCost':{'currencyCode':self.currency, 'amount':cost} if cost is not None else None}})
        return {'shop':{'currencyCode':self.currency}, 'nodes':nodes}

    def write_item_cost(self, config, iid, value):
        proof = drafts.evidence(ShopifyProductIdentity.query.one())['item_cost_attempt']
        assert any(entry['id'] == iid and entry['state'] == 'pending' for entry in proof['items'].values())
        self.cost_writes.append((iid, value))
        if not self.ignore_cost_write:
            self.costs[iid] = value
        if self.lose_write:
            raise ShopifyError('Mock connection lost after write.')
        return {'inventoryItemUpdate':{'inventoryItem':{'id':iid},'userErrors':[]}}


class SimpleCost(CostMock, AcceptanceAdapter):
    pass


class VariableCost(CostMock, VariableAdapter):
    pass


def edit(path, **changes):
    path.write_text(json.dumps({**json.loads(path.read_text()), **changes}))


def test_create_simple_cost_preview_matching_and_removing_management(world):
    product, _, shared = world
    adapter = SimpleCost()
    edit(shared, production=manual('0.00'))
    plan, token = drafts.preview(product, adapter)
    assert [r['after'] for r in plan['fields'] if r['field'].startswith('cost_per_item.')] == ['£0.00 (Manual cost)']
    row = drafts.confirm(token, True, adapter)
    assert row.state == 'verified', drafts.evidence(row).get('error')
    assert len(adapter.cost_writes) == 1 and adapter.cost_writes[0][1] == '0.00'
    assert drafts.evidence(row)['verification']['item_cost'] == 'verified'
    assert adapter.remote['simpleVariants']['nodes'][0]['inventoryItem']['tracked'] is False
    plan, token = drafts.preview(product, adapter)
    assert not token and not any(r['changed'] for r in plan['fields'])
    edit(shared, production={'cost':{'mode':'unconfigured'}})
    plan, token = drafts.preview(product, adapter)
    assert not token and not plan.get('item_cost')
    assert len(adapter.cost_writes) == 1


@pytest.mark.parametrize('variable_world', ['Single Variable'], indirect=True)
def test_variable_costs_target_retained_ids_and_track_different_costs(variable_world):
    product, shared, *_ = variable_world
    a, b = [v.sku for v in product.variations[:2]]
    edit(shared, production={**manual('1.15'), 'variants':{a:manual('1.852'), b:manual('2.35')}})
    adapter = VariableCost()
    plan, token = drafts.preview(product, adapter)
    assert token, plan['blockers']
    row = drafts.confirm(token, True, adapter)
    assert row.state == 'verified', drafts.evidence(row).get('error')
    correct = {v['sku']:adapter.costs[v['inventoryItem']['id']] for v in adapter.remote['variants']['nodes']}
    assert correct == {v.sku:('1.85' if v.sku == a else '2.35' if v.sku == b else '1.15') for v in product.variations}
    previous = copy.deepcopy(correct)
    adapter.remote['variants']['nodes'].reverse()
    plan, token = drafts.preview(product, adapter)
    assert not token
    assert correct == previous and len(adapter.cost_writes) == 4


def test_missing_write_permissions_and_incomplete_estimate_are_not_approvable(world):
    product, _, shared = world
    adapter = SimpleCost(); adapter.inventory_permission = False
    edit(shared, production=manual('1.20'))
    plan, token = drafts.preview(product, adapter)
    assert not token and any('write_inventory' in reason for reason in plan['blockers'])
    edit(shared, production=calculate({'missing':add(99999)}))
    plan, token = drafts.preview(product, adapter)
    assert not token and any('missing' in reason for reason in plan['blockers'])
    assert not adapter.cost_writes


def test_incomplete_remote_readback_keeps_recovery_and_reconciliation_never_rewrites(world):
    product, _, shared = world
    adapter = SimpleCost()
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    edit(shared, production=manual('0.91'))
    _, token = drafts.preview(product, adapter)
    adapter.lose_write = True
    try:
        drafts.confirm(token, True, adapter)
    except ShopifyError:
        pass
    db.session.refresh(row)
    assert row.state == 'uncertain'
    assert len(adapter.cost_writes) == 1
    drafts.reconcile(row, adapter=adapter)
    assert row.state == 'verified', drafts.evidence(row).get('error')
    assert len(adapter.cost_writes) == 1


def test_hourly_cost_only_advance_without_scan_does_not_accept_unscanned_metadata(world, notices):
    from app import scheduled_tasks as tasks
    from app.models import ScheduledTaskRun
    product, _, shared = world
    client = client_for(current_app._get_current_object())
    component = item(client, 'purchased', 'PRICE', 'Priced component', pack_qty='1', pack_unit='piece', pack_price='0.84')
    edit(shared, live=True, production=calculate({'item':add(component)}))
    adapter = SimpleCost()
    authorise(adapter)
    assert execute(product, adapter) == 'verified'
    job = queued(product)
    original_revision = job.requested_revision
    record = db.session.get(Item, component)
    record.pack_price = 95 * stock.SCALE // 100
    db.session.commit()
    run_id = tasks.enqueue(publisher.KEY)
    assert run_id and tasks.claim('cost-worker') == (publisher.KEY, run_id)
    tasks.run_slice(publisher.KEY, run_id, 'cost-worker', adapter=adapter)
    db.session.refresh(job)
    assert job.requested_revision != original_revision and job.trigger == 'inventory_cost'
    assert db.session.get(ScheduledTaskRun, run_id).state == 'completed'
    assert job.state == 'verified'
    assert [amount for _, amount in adapter.cost_writes] == ['0.84','0.95']
    edit(shared, title='Unscanned change')
    record.pack_price = stock.SCALE
    db.session.commit()
    publisher.refresh_production_costs(fence=lambda:None)
    assert job.requested_revision != publisher.local_revision(product)
    assert len(adapter.cost_writes) == 2 and Movement.query.count() == 0


def test_acknowledged_but_missing_cost_stays_in_recovery(world):
    product, _, shared = world
    adapter = SimpleCost()
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    edit(shared, production=manual('2.15'))
    _, token = drafts.preview(product, adapter)
    adapter.ignore_cost_write = True
    try:
        drafts.confirm(token, True, adapter)
    except ShopifyError:
        pass
    db.session.refresh(row)
    assert row.state == 'uncertain'
    assert 'Cost per item readback differs' in drafts.evidence(row)['error']
    try:
        drafts.reconcile(row, adapter=adapter)
    except ShopifyError:
        pass
    assert row.state == 'uncertain' and len(adapter.cost_writes) == 1


def test_transport_sends_cost_only():
    class Adapter(ShopifyAdapter):
        def _transport(self, config, query, variables):
            return query, variables
    query, variables = Adapter().write_item_cost(None, 'gid://shopify/InventoryItem/250', '0.95')
    assert query == ITEM_COST_WRITE
    assert variables == {'id':'gid://shopify/InventoryItem/250','input':{'cost':'0.95'}}
