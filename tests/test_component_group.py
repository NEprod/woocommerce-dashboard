"""Grouped entry creates only ordinary, independently editable stock masters."""
import json
from decimal import Decimal
from io import BytesIO

import pytest
from PIL import Image
from app import db
from app.inventory import service as stock
from app.inventory.models import Item, Movement, Identity
from test_inventory import workshop, client_for, item, location
from test_setup_flow import setup_app


def part(name,code,material,quantity='1',**extra):
    return dict(name=name,code=code,yield_qty='',materials=[dict(item_id=str(material),quantity=quantity,unit='g')],**extra)


def post(client,parts,**values):
    return client.post('/production/components/new-group',data=dict(group_name='Gingerbread Man',
        yield_qty='10',active='yes',components=json.dumps(parts),**values))


def test_group_creates_separate_recipes_shared_details_image_and_no_stock(workshop):
    c=client_for(workshop)
    raw=item(c,'material','PLA','PLA',unit='g',pack_qty='1000',pack_price='20')
    second=item(c,'material','WHITE','White',unit='g',pack_qty='1000',pack_price='10')
    tub=location(c,'TUB','Component tub')
    eyes=part('Eyes','GB-EYES',raw)
    feet=part('Feet','GB-FEET',raw,'2');feet['yield_qty']='20'
    feet['materials'].append(dict(item_id=str(second),quantity='0.5',unit='g'))
    photo=BytesIO();Image.new('RGB',(40,20),'green').save(photo,'PNG');photo.seek(0)
    response=post(c,[eyes,feet],category='Parts',specification='3D printed',description='Shared notes',
        minimum='5',reorder='10',default_location_id=str(tub),image=(photo,'group.png'))
    assert response.status_code==303,response.text[:500]
    with workshop.app_context():
        rows=Item.query.filter_by(kind='manufactured').order_by(Item.id).all()
        assert [r.name for r in rows]==['Gingerbread Man - Eyes','Gingerbread Man - Feet']
        assert [r.identity.code for r in rows]==['GB-EYES','GB-FEET']
        assert [r.yield_qty for r in rows]==[10,20]
        assert rows[0].materials[0].qty==10*stock.SCALE
        assert [r.qty for r in rows[1].materials]==[40*stock.SCALE,10*stock.SCALE]
        assert [stock.recipe_estimate(r) for r in rows]==[Decimal('.02'),Decimal('.045')]
        for row in rows:
            assert row.minimum==5*stock.SCALE and row.reorder==10*stock.SCALE
            assert row.category=='Parts' and row.description=='Shared notes' and row.default_location_id==tub
            assert row.image and row.unit=='piece' and stock.balance(row.id)==0
        assert rows[0].image==rows[1].image
        first=rows[0].id
        assert Movement.query.count()==0
    page=c.get(f'/production/item/{first}/edit')
    assert page.status_code==200 and b'Gingerbread Man - Eyes' in page.data and b'value="1.000000"' in page.data
    # Existing single-item workflow remains available and edits only that record.
    edited=c.post(f'/production/item/{first}/edit',data=dict(name='Gingerbread Man - Corrected Eyes',version='1',active='yes',
        yield_qty='10',recipe_quantity_basis='piece',material_id=str(raw),material_qty='1',material_unit='g'))
    assert edited.status_code==303
    with workshop.app_context(): assert Item.query.filter_by(name='Gingerbread Man - Feet').one().version==1


def test_group_save_is_atomic_and_repeat_never_duplicates_parts(workshop):
    c=client_for(workshop);raw=item(c,'material','PLA','PLA',unit='g')
    parts=[part('Eyes','EYES',raw),part('Feet','FEET',raw,'-2')]
    before=None
    with workshop.app_context(): before=Identity.query.count()
    response=post(c,parts)
    assert response.status_code==400 and b'Feet:' in response.data and b'EYES' in response.data
    with workshop.app_context(): assert Item.query.filter_by(kind='manufactured').count()==0 and Identity.query.count()==before
    parts[1]['materials'][0]['quantity']='2'
    assert post(c,parts).status_code==303
    assert post(c,parts).status_code==400
    with workshop.app_context(): assert Item.query.filter_by(kind='manufactured').count()==2


@pytest.mark.parametrize('problem',['duplicate_name','duplicate_code','missing_barcode','missing_material','incompatible_unit','invalid_yield'])
def test_group_validates_each_part_without_partial_creation(workshop,problem):
    c=client_for(workshop);raw=item(c,'material','PLA','PLA',unit='g')
    parts=[part('Eyes','EYES',raw),part('Feet','FEET',raw)]
    if problem=='duplicate_name':parts[1]['name']='eyes'
    if problem=='duplicate_code':parts[1]['code']='eyes'
    if problem=='missing_barcode':parts[1]['code']=''
    if problem=='missing_material':parts[1]['materials']=[]
    if problem=='incompatible_unit':parts[1]['materials'][0]['unit']='ml'
    if problem=='invalid_yield':parts[1]['yield_qty']='0'
    assert post(c,parts).status_code==400
    with workshop.app_context(): assert Item.query.filter_by(kind='manufactured').count()==0 and Movement.query.count()==0


def test_group_get_empty_form_permissions_and_csrf(workshop):
    c=client_for(workshop)
    assert c.get('/production/components/new-group').status_code==200
    assert b'Add component group' in c.get('/production/items/components').data
    assert b'Add manufactured component' in c.get('/production/items/components').data
    assert post(c,[]).status_code==400
    assert client_for(workshop,3).get('/production/components/new-group').status_code==403
    workshop.config['WTF_CSRF_ENABLED']=True
    assert post(c,[]).status_code==400
    with workshop.app_context(): assert Item.query.count()==0
