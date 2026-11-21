from io import BytesIO
import pytest
from PIL import Image
from app import db
from app.inventory.models import Identity, Item, Movement, ProductionBatch, Transaction
from test_setup_flow import setup_app  # noqa: F401
from test_inventory import workshop, client_for, item, manufactured, location  # noqa: F401


@pytest.mark.parametrize('kind', ['material','purchased','manufactured'])
def test_stock_cards_reuse_permanent_identity_without_stock_changes(workshop, kind):
    client = client_for(workshop)
    raw = item(client,'material','RAW-CARD','PLA Matte Charcoal',unit='g',filament_type='PLA',
               filament_colour='Charcoal',supplier='Fictional Supplier',supplier_url='https://supplier.invalid/item',minimum='500')
    record = manufactured(client,[(raw,'220','g')]) if kind == 'manufactured' else raw if kind == 'material' else item(client,'purchased','SWITCH-CARD','Mechanical switch',minimum='20')
    with workshop.app_context():
        saved = db.session.get(Item,record)
        code = saved.identity.code
        before = tuple(model.query.count() for model in (Identity,Item,Movement,ProductionBatch,Transaction))
    response = client.get(f'/production/item/{record}/stock-card')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert f'>{code}</div>' in html
    assert f'/production/label/{record}/code128.svg' in html
    assert f'Code 128 for {code}' in html
    assert 'BODY-0001' not in html and 'Total on hand' not in html
    assert 'Scan to reorder' not in html
    assert b'<svg' in client.get(f'/production/label/{record}/code128.svg').data
    assert client.get('/production/lookup',query_string={'code':code}).headers['Location'].endswith(f'/item/{record}')
    details = client.get(f'/production/item/{record}').get_data(as_text=True)
    assert 'View Stock Card' in details and 'Print Stock Card' in details and 'Total on hand' in details
    if kind == 'material':
        assert 'Charcoal' in html and '500' in html and 'Fictional Supplier' in html
        assert 'https://supplier.invalid/item' in details
    if kind == 'manufactured':
        assert 'PLA Matte Charcoal' in html and 'Expected per batch' in html
    with workshop.app_context():
        assert tuple(model.query.count() for model in (Identity,Item,Movement,ProductionBatch,Transaction)) == before


def test_card_image_long_name_optional_fields_and_print_css(workshop):
    client = client_for(workshop)
    record = item(client,'purchased','ABCDEFGHIJKLMNOPQRSTUVWX','Long component name ' * 7)
    html = client.get(f'/production/item/{record}/stock-card').get_data(as_text=True)
    assert 'stock-card-photo' not in html and '<dt>Supplier</dt>' not in html
    with workshop.app_context():
        output = BytesIO(); Image.new('RGB',(20,20),'red').save(output,format='JPEG')
        db.session.get(Item,record).image = output.getvalue(); db.session.commit()
    html = client.get(f'/production/item/{record}/stock-card').get_data(as_text=True)
    assert 'stock-card-photo' in html and f'/production/item/{record}/image.jpg' in html
    assert 'ABCDEFGHIJKLMNOPQRSTUVWX</div>' in html
    css = client.get('/static/assets/css/inventory-stock-card.css').get_data(as_text=True)
    assert 'size:2in 3.5in' in css and '@media print' in css and 'height:3.5in!important' in css
    assert 'width:2in!important' in css and 'object-fit:contain' in css
    assert 'Identifier too long' in html and 'Open larger inventory label' in html
    assert 'class="stock-card-barcode"' in html
    assert 'alt="Code 128 for ABCDEFGHIJKLMNOPQRSTUVWX"' not in html
    assert client.get(f'/production/label/{record}/code128.svg?layout=stock-card').status_code == 422
    # The ordinary label remains available, without a change of barcode identity.
    assert client.get(f'/production/label/{record}/code128.svg').status_code == 200
    assert client_for(workshop,3).get(f'/production/item/{record}/stock-card').status_code == 403


def test_missing_barcode_preview_never_generates_an_identity(workshop):
    from types import SimpleNamespace
    from flask import render_template
    from flask_login import login_user
    from app.models import User
    with workshop.test_request_context('/production/item/999/stock-card'):
        login_user(db.session.get(User,1))
        row = SimpleNamespace(id=999,name='Legacy item',kind='purchased',identity=None,unit='piece',minimum=0)
        before = Identity.query.count()
        html = render_template('inventory/stock_card.html',row=row,location=None,has_image=False)
        assert 'Permanent barcode missing' in html
        assert '/code128.svg' not in html
        assert Identity.query.count() == before


def test_portrait_barcode_geometry_and_original_encoding(workshop):
    import xml.etree.ElementTree as ET
    from app.inventory.stock_cards import barcode_geometry, MIN_MODULE_MM
    client=client_for(workshop)
    record=item(client,'purchased','KAI-BOX-WHITE','Kailh Box White V2')
    original=ET.fromstring(client.get(f'/production/label/{record}/code128.svg').data)
    portrait=ET.fromstring(client.get(f'/production/label/{record}/code128.svg?layout=stock-card').data)
    assert portrait.attrib['width']=='45.212mm'
    assert portrait.attrib['height']=='11.430mm'
    def bars(root):
        return [r for r in root.iter('{http://www.w3.org/2000/svg}rect') if r.get('x')]
    def value(rect,key):
        return float(rect.attrib[key].removesuffix('mm'))
    old,new=bars(original),bars(portrait)
    assert len(old)==len(new)
    module=min(value(bar,'width') for bar in new)
    assert module>=float(MIN_MODULE_MM)
    assert value(new[0],'x')>=10*module
    assert 45.212-max(value(bar,'x')+value(bar,'width') for bar in new)>=10*module
    # Same bar positions and widths, measured in modules: no identifier substitution.
    for a,b in zip(old,new):
        assert round(value(a,'width')/.29)==round(value(b,'width')/module)
        assert round((value(a,'x')-4)/.29)==round((value(b,'x')-value(new[0],'x'))/module)
    assert barcode_geometry('KAI-BOX-WHITE')['fits']
    assert barcode_geometry('123456789012345678901234')['fits']  # Code 128 numeric compaction is retained.
    assert not barcode_geometry('ABCDEFGHIJKLMNOPQRSTUVWX')['fits']
    assert not barcode_geometry(None)['fits']


def test_portrait_placeholder_and_saved_location(workshop):
    client=client_for(workshop)
    record=item(client,'material','PORTRAIT-RAW','Material without an image',unit='g')
    tub=location(client,'PORTRAIT-TUB','Drawer A')
    with workshop.app_context():
        db.session.get(Item,record).default_location_id=tub
        db.session.commit()
    html=client.get(f'/production/item/{record}/stock-card').get_data(as_text=True)
    assert 'stock-card-placeholder' in html and 'No item image' in html
    assert 'Drawer A' in html and '2 × 3.5-inch portrait' in html
    assert 'layout=stock-card' in html
