"""Visible estimates reuse existing recipe/allowance rules without stock mutation."""
import copy
import json
from decimal import Decimal

import pytest
from app import db
from app.inventory import service as stock
from app.inventory.models import BomScenario, CostingSettings, Equipment, Item, Movement
from app.inventory.profitability import component_breakdown, estimate
from test_inventory import workshop, client_for, item, manufactured
from test_setup_flow import setup_app


def test_component_screen_displays_batch_piece_weights_and_configured_waste(workshop):
    client = client_for(workshop)
    pla = item(client, 'material', 'BLACK', 'Black PLA', unit='g', pack_qty='1000', pack_price='13.99')
    part = manufactured(client, [(pla, '10', 'g')])
    with workshop.app_context():
        row = db.session.get(Item, part)
        row.yield_qty = 10
        config = db.session.get(CostingSettings, 1)
        if config: config.document = json.dumps({'buffer':'11', 'print_waste_rate':'20'})
        else: db.session.add(CostingSettings(id=1, document=json.dumps({'buffer':'11', 'print_waste_rate':'20'}), version=1))
        db.session.commit()
        result = component_breakdown(row, {'buffer':'11'})
        assert result['rows'][0]['piece'] == dict(quantity='1.00', waste_quantity='0.11', total_quantity='1.11',
            base='0.01399', waste='0.0015389', total='0.0155289')
        assert result['totals']['batch'] == dict(base='0.1399', waste='0.015389', total='0.155289')
        assert row.materials[0].qty == 10 * stock.SCALE
        assert stock.recipe_estimate(row) == Decimal('.01399')
    page = client.get(f'/production/item/{part}')
    assert page.status_code == 200
    for label in [b'One good component', b'Whole batch (10 good components)', b'11%', b'0.0155289', b'0.155289', b'Waste allowance']:
        assert label in page.data
    with workshop.app_context(): assert Movement.query.count() == 0


@pytest.mark.parametrize('rate', ['0','10','11','20'])
def test_santa_components_add_waste_once_and_keep_full_precision(workshop, rate):
    client = client_for(workshop)
    lines = []
    # Four separately stocked recipes; helper retains legacy four-piece batch basis.
    for index, grams in enumerate([13,17,2,2]):
        pla = item(client, 'material', f'PLA-{index}', f'PLA {index}', unit='g', pack_qty='1000', pack_price='13.99')
        from werkzeug.datastructures import MultiDict
        data = MultiDict(dict(name=f'Part {index}', code=f'PART-{index}', active='yes', yield_qty='4',
            material_id=str(pla), material_qty=str(grams * 4), material_unit='g'))
        from test_inventory import row_id
        part = row_id(client.post('/production/item/new/manufactured', data=data))
        lines.append(dict(item_id=part, quantity='1', unit='piece'))
    with workshop.app_context():
        source = dict(lines=lines, selling_price='6')
        before = copy.deepcopy(source)
        config = dict(buffer=rate, print_waste_rate='99', fee_percent='2', fee_fixed='.25')
        result = estimate(source, config, direct_materials=True)
        expected = Decimal('.47566') * Decimal(rate) / 100
        assert Decimal(result['buffer_exact']) == expected
        assert result['print_waste_exact'] == '0'
        assert Decimal(result['suggestion_exact']) == ((Decimal('.47566') + expected + Decimal('.25')) / Decimal('.98'))
        assert sum(Decimal(line['allowance_exact']) for line in result['lines']) == expected
        assert source == before and Movement.query.count() == 0


def test_mixed_material_units_unknown_costs_packaging_process_and_labour(workshop):
    client = client_for(workshop)
    pla = item(client, 'material', 'PLA', 'PLA', unit='g', pack_qty='1000', pack_price='20')
    unknown = item(client, 'material', 'UNKNOWN', 'Unknown material', unit='g')
    part = manufactured(client, [(pla, '0.04', 'kg'), (unknown, '8', 'g')])
    box = item(client, 'purchased', 'BOX', 'Box', pack_qty='50', pack_price='15')
    with workshop.app_context():
        row = db.session.get(Item, part)
        result = component_breakdown(row, {'buffer':'11'})
        assert result['rows'][0]['batch']['quantity'] == '0.04'
        assert result['rows'][0]['batch']['waste_quantity'] == '0.0044'
        assert result['totals']['piece']['total'] is None
        row.materials[:] = [row.materials[0]]
        printer = Equipment(name='Printer', profile='PLA', watts='250', source='estimated', active=True)
        db.session.add(printer); db.session.commit()
        config = dict(buffer='11', print_waste_rate='20', tariff='.24226', labour_rate='30', fee_percent='2', fee_fixed='.25')
        result = estimate(dict(lines=[dict(item_id=part, quantity='2', unit='piece'),
            dict(item_id=box, quantity='1', unit='piece', purpose='packaging')],
            equipment=[dict(equipment_id=printer.id, minutes='250')], labour_minutes='2', selling_price='6'), config)
        assert result['buffer_exact'] == '0.044'
        assert result['subtotals']['packaging'] == '0.30'
        assert result['processes'][0]['electricity'] == '0.25235417'
        assert result['labour'] == '1.00'
        assert result['cost'] == '2.00'  # .4 + .044 + .3 + .2523541667 + 1
        assert Movement.query.count() == 0


def test_legacy_snapshot_reopens_without_repricing_or_new_fields(workshop):
    client = client_for(workshop)
    piece = item(client, 'purchased', 'PIECE', 'Piece', pack_qty='1', pack_price='1')
    with workshop.app_context():
        source = dict(lines=[dict(item_id=piece, quantity='1', unit='piece')], equipment=[], selling_price='5')
        result = estimate(source, dict(fee_percent='0', fee_fixed='0'))
        for key in ['buffer_exact','buffer_rate','processes','labour_minutes','labour_rate']:
            result.pop(key)
        for line in result['lines']:
            for key in ['allowance','allowance_exact','allowance_rate','with_allowance']: line.pop(key)
        source['_snapshot'] = dict(result=result, settings={})
        stored = json.dumps(source)
        scenario = BomScenario(name='Historical', document=stored, version=1)
        db.session.add(scenario); db.session.commit(); sid=scenario.id
    page=client.get(f'/production/bom/{sid}')
    assert page.status_code == 200 and b'Production cost breakdown' in page.data
    with workshop.app_context(): assert db.session.get(BomScenario, sid).document == stored
