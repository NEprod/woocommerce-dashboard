from decimal import Decimal

from app import db
from app.inventory.models import Equipment, Movement
from app.inventory.profitability import estimate
from test_inventory import workshop, client_for, item, manufactured  # noqa: F401
from test_setup_flow import setup_app  # noqa: F401
from test_metadata_editor_core import core, save  # noqa: F401


def test_finished_product_costing_material_buffer_equipment_and_fees(workshop):
    client = client_for(workshop)
    raw = item(client, 'material', 'COST-PLA', 'PLA', unit='g', pack_qty='1000', pack_price='20')
    component = manufactured(client, [(raw, '200', 'g')])
    purchased = item(client, 'purchased', 'COST-SWITCH', 'Switch', pack_qty='10', pack_price='5')
    with workshop.app_context():
        printer = Equipment(name='X2D', profile='PLA provisional', watts='250', source='estimated', notes='', active=True)
        db.session.add(printer)
        db.session.commit()
        document = dict(lines=[dict(item_id=component, quantity='1', unit='piece'),
                               dict(item_id=purchased, quantity='1', unit='piece')],
                        equipment=[dict(equipment_id=printer.id, minutes='30')], labour_minutes='6', selling_price='10')
        result = estimate(document, dict(tariff='.30', labour_rate='12', markup='50', fee_percent='2', fee_fixed='.25'))
        assert result['complete']
        assert result['material_component'] == '1.50'
        assert result['buffer'] == '0.10'
        assert result['electricity'] == '0.04'
        assert result['labour'] == '1.20'
        assert result['cost'] == '2.84'
        assert result['fees'] == '0.45'
        assert result['profit'] == '6.71'
        assert result['margin'] == '67.13'
        assert db.session.query(Movement).count() == 0


def test_unknown_equipment_is_not_free_and_direct_materials_are_planner_only(workshop):
    client = client_for(workshop)
    raw = item(client, 'material', 'COST-WOOD', 'Wood', unit='sheet', pack_qty='1', pack_price='5')
    with workshop.app_context():
        laser = Equipment(name='CO2 laser', profile='', watts=None, source='estimated', notes='', active=True)
        db.session.add(laser)
        db.session.commit()
        result = estimate(dict(lines=[dict(item_id=raw, quantity='1', unit='sheet')],
                               equipment=[dict(equipment_id=laser.id, minutes='30')]), direct_materials=True)
        assert not result['complete']
        assert result['cost'] is None and result['profit'] is None
        assert any('watts' in warning for warning in result['missing'])


def test_simultaneous_equipment_does_not_multiply_labour_or_component_quantity(workshop):
    client = client_for(workshop)
    purchased = item(client, 'purchased', 'MULTI-COMP', 'Component', pack_qty='1', pack_price='1')
    with workshop.app_context():
        one = Equipment(name='Test laser', profile='', watts='1000', source='measured', notes='', active=True)
        two = Equipment(name='Test cooler', profile='', watts='500', source='measured', notes='', active=True)
        db.session.add_all([one,two]); db.session.commit()
        result = estimate(dict(lines=[dict(item_id=purchased,quantity='5',unit='piece')],
            equipment=[dict(equipment_id=one.id,minutes='30'),dict(equipment_id=two.id,minutes='30')],
            labour_minutes='6',selling_price='10'),dict(tariff='.40',labour_rate='12',fee_percent='0',fee_fixed='0'))
        assert result['complete']
        assert result['electricity'] == '0.30'
        assert result['labour'] == '1.20'
        assert result['material_component'] == '5.00'


def test_costing_settings_page_persists_unknown_power_safely(workshop):
    client = client_for(workshop)
    assert client.get('/production/costing/settings').status_code == 200
    reply = client.post('/production/costing/settings', data=dict(action='equipment', name='CW-5200',
                                                                profile='Unverified', watts='', source='estimated'))
    assert reply.status_code == 303
    assert b'Unknown' in client.get('/production/costing/settings').data


def test_standalone_scenario_save_reopen_and_duplicate_are_stock_neutral(workshop):
    from app.inventory.models import BomScenario
    from app.models import Product
    client = client_for(workshop)
    raw = item(client, 'material', 'SCENARIO-PLA', 'PLA', unit='g', pack_qty='1000', pack_price='20')
    form = dict(name='New decoration', item_id=str(raw), quantity='50', unit='g',
                labour_minutes='0', selling_price='5', other='0', action='save')
    reply = client.post('/production/bom', data=form)
    assert reply.status_code == 303
    page = client.get(reply.headers['Location'])
    assert page.status_code == 200 and b'New decoration' in page.data and b'Profitability Preview' in page.data
    form.update(version='1', action='duplicate', name='Decoration copy')
    assert client.post(reply.headers['Location'], data=form).status_code == 303
    with workshop.app_context():
        assert BomScenario.query.count() == 2
        assert Movement.query.count() == 0
        assert Product.query.count() == 0


def test_product_profitability_reads_existing_bom_and_saves_only_planning_assumptions(core):
    import json
    from app.inventory.models import BomScenario
    app, client, root, _, parents, *_ = core
    stock_id = item(client, 'purchased', 'PLAN-BOX', 'Box', pack_qty='1', pack_unit='piece', pack_price='1')
    path = 'Simple Collection/Blue Token'
    product_id = parents[path]
    production = dict(lines={'box':dict(op='add', item_id=stock_id, quantity='2', unit='piece')}, cost=dict(mode='calculated'))
    assert save(core, path, kind='shared', data={'production':production}).status_code == 200
    source = root / 'Simple Collection/product_info.json'
    original = source.read_bytes()
    route = f'/production/product/{product_id}/bom'
    page = client.get(route)
    assert page.status_code == 200 and b'Finished-product BOM Profitability' in page.data
    reply = client.post(route, data=dict(name='Finished-product plan', selling_price='10',
                                        labour_minutes='0', other='0', action='save'))
    assert reply.status_code == 303
    assert b'Profitability Preview' in client.get(reply.headers['Location']).data
    assert source.read_bytes() == original
    preview = client.post(f'/api/products/{product_id}/production-costing', json=dict(kind='shared', production=production))
    assert preview.status_code == 200
    assert preview.json['parent']['profitability']['cost'] == '2.00'
    assert preview.json['parent']['profitability']['selling_price'] == '10.00'
    with app.app_context():
        scenario = BomScenario.query.one()
        assert scenario.product_id == product_id
        assert json.loads(scenario.document)['lines'][0]['quantity'] == '2'
        assert Movement.query.count() == 0
        from app.inventory.profitability import bundle_estimate, finished_product_estimate
        from app.models import Product
        product = db.session.get(Product, product_id)
        unit = finished_product_estimate(product, product.sku)
        assert unit['complete'] and unit['cost'] == '2.00'
        bundle = bundle_estimate([dict(product_id=product_id, sku=product.sku, quantity='2')],
                                 dict(lines=[], equipment=[], selling_price='10'),
                                 dict(fee_percent='2', fee_fixed='.25'))
        assert bundle['complete'] and bundle['cost'] == '4.00'
        assert bundle['fees'] == '0.45'
        assert bundle['profit'] == '5.55'
    bundle_page = client.get('/production/bundles')
    assert bundle_page.status_code == 200 and b'Bundle Profitability Planner' in bundle_page.data
    with app.app_context():
        product = db.session.get(Product, product_id)
        selected = f'{product_id}|{product.sku}'
    reply = client.post('/production/bundles', data=dict(name='Two product bundle', bundle_product=selected,
        bundle_quantity='2', selling_price='10', labour_minutes='0', other='0', action='save'))
    assert reply.status_code == 303
    assert b'Two product bundle' in client.get(reply.headers['Location']).data


def test_equipment_editing_and_fee_profiles_preserve_unknown_values(workshop):
    import json
    from app.inventory.models import CostingSettings
    client = client_for(workshop)
    assert client.post('/production/costing/settings', data=dict(action='equipment', name='X2D', profile='PLA',
        watts='250', source='estimated')).status_code == 303
    with workshop.app_context():
        equipment = Equipment.query.filter_by(name='X2D', profile='PLA').one()
        equipment_id = equipment.id
    reply = client.post('/production/costing/settings', data=dict(action='equipment', equipment_id=equipment_id,
        equipment_version='1', name='X2D', profile='PLA', watts='', source='measured', notes='Measurement pending'))
    assert reply.status_code == 303
    with workshop.app_context():
        assert db.session.get(Equipment, equipment_id).watts is None
    assert client.post('/production/costing/settings', data=dict(action='fee_profile', name='Online',
        percent='2', fixed='.25', notes='Example test rates only')).status_code == 303
    with workshop.app_context():
        config = json.loads(db.session.get(CostingSettings, 1).document)
        assert config['fee_profiles'][0]['fixed'] == '0.25'
