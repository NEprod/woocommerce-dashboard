"""Per-piece form entry with unchanged authoritative whole-batch recipes."""
from decimal import Decimal
from werkzeug.datastructures import MultiDict

from app import db
from app.inventory import service as stock
from app.inventory.models import Item, Movement
from test_inventory import workshop, client_for, item, manufactured, row_id
from test_setup_flow import setup_app


def fields(materials, good='10', **extra):
    result = MultiDict(dict(name='Printed part', active='yes', yield_qty=good,
        recipe_quantity_basis='piece', **extra))
    for material, qty, unit in materials:
        for key, value in zip(('material_id', 'material_qty', 'material_unit'), (material, qty, unit)):
            result.add(key, str(value))
    return result


def test_new_per_piece_multiple_materials_and_cost(workshop):
    client = client_for(workshop)
    red = item(client, 'material', 'RED', 'Red PLA', unit='g', pack_qty='1000', pack_price='13.99')
    white = item(client, 'material', 'WHITE', 'White PLA', unit='g', pack_qty='1000', pack_price='20')
    component = row_id(client.post('/production/item/new/manufactured',
        data=fields([(red, '13', 'g'), (white, '2', 'g')])))
    with workshop.app_context():
        row = db.session.get(Item, component)
        assert [(r.qty, r.unit) for r in row.materials] == [(130 * stock.SCALE, 'g'), (20 * stock.SCALE, 'g')]
        assert stock.recipe_estimate(row) == Decimal('0.22187')
        assert db.session.query(Movement).count() == 0
    page = client.get(f'/production/item/{component}/edit').text
    assert 'Expected quantity per good component' in page and '13.000000' in page
    assert 'data-recipe-total' in page and '130' in page


def test_legacy_edit_preserves_exact_totals_and_cost_then_allows_changes(workshop):
    client = client_for(workshop)
    pla = item(client, 'material', 'PLA', 'PLA', unit='g', pack_qty='1000', pack_price='13.99')
    component = manufactured(client, [(pla, '1', 'g')])  # legacy 1 g / 4 good pieces
    with workshop.app_context():
        row = db.session.get(Item, component)
        row.yield_qty = 3  # indivisible legacy quantity: must not become .999999 g
        db.session.commit()
        before = stock.recipe_estimate(row)
    page = client.get(f'/production/item/{component}/edit').text
    assert '0.333333' in page
    for version in ('1', '2'):
        assert client.post(f'/production/item/{component}/edit',
            data=fields([(pla, '0.333333', 'g')], good='3', version=version)).status_code == 303
    with workshop.app_context():
        row = db.session.get(Item, component)
        assert row.materials[0].qty == stock.SCALE
        assert stock.recipe_estimate(row) == before
    assert client.post(f'/production/item/{component}/edit',
        data=fields([(pla, '13', 'g')], version='3')).status_code == 303
    with workshop.app_context():
        row = db.session.get(Item, component)
        assert row.materials[0].qty == 130 * stock.SCALE
        assert stock.recipe_estimate(row) == Decimal('0.18187')


def test_add_line_and_validation_keep_per_piece_input(workshop):
    client = client_for(workshop)
    pla = item(client, 'material', 'PLA', 'PLA', unit='g')
    data = fields([(pla, '13', 'g')], action='add-line')
    page = client.post('/production/item/new/manufactured', data=data)
    assert page.status_code == 200 and b'value="13"' in page.data
    assert page.data.count(b'data-recipe-line ') == 2
    data.setlist('action', [''])
    data.setlist('material_qty', ['0'])
    assert client.post('/production/item/new/manufactured', data=data).status_code == 400
    data.setlist('material_qty', ['13'])
    data.setlist('yield_qty', ['0'])
    assert client.post('/production/item/new/manufactured', data=data).status_code == 400
    with workshop.app_context():
        assert db.session.query(Item).filter_by(kind='manufactured').count() == 0
