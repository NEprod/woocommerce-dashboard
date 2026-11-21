"""Local Santa acceptance: exact costs, explicit scope and repeatable row lifecycle."""
from decimal import Decimal, ROUND_HALF_UP
import json

import pytest
from app import db
from app.inventory.models import BomScenario, CostingSettings, Equipment, Item, Movement
from app.inventory.profitability import estimate, hypothetical_cost
from test_inventory import workshop, client_for, item, manufactured  # noqa: F401
from test_setup_flow import setup_app  # noqa: F401


def santa(eid):
    return dict(lines=[dict(source='manual', name=name, cost_mode='purchase', purchase_cost='13.99',
                           purchase_quantity='1', purchase_unit='kg', quantity=qty, unit='g', print_material='yes')
                       for name,qty in [('Red','13'),('Ivory','17'),('Beige','2'),('Black','2')]],
                equipment=[dict(equipment_id=eid, minutes='250', timing_mode='item')],
                labour_minutes='2', selling_price='6')


CONFIG=dict(tariff='.24226', labour_rate='30', fee_percent='2', fee_fixed='.25', markup='100')
def pennies(value):
    return format(value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP), '.2f')


@pytest.mark.parametrize('rate', ['0','10','11','20'])
def test_santa_full_precision_allowance_and_independent_profit(workshop, rate):
    with workshop.app_context():
        eid=Equipment.query.filter_by(profile='PLA provisional').one().id
        document=santa(eid);document['print_waste_rate']=rate
        result=estimate(document,CONFIG,direct_materials=True)
        material=Decimal('.47566');waste=material*Decimal(rate)/100
        electricity=Decimal('250')/1000*Decimal('250')/60*Decimal('.24226')
        cost=material+waste+electricity+Decimal(1)
        assert Decimal(result['print_waste_exact'])==waste
        assert result['buffer']=='0.00'
        assert result['cost']==pennies(cost)
        assert result['profit']==pennies(Decimal('6')-cost-Decimal('.37'))
        assert result['margin']==pennies((Decimal('6')-cost-Decimal('.37'))/6*100)
        assert result['fee_adjusted']==pennies((cost*2+Decimal('.25'))/Decimal('.98'))
        assert result['packaging_included'] is False
        assert Movement.query.count()==0


def test_packaging_scope_inheritance_zero_and_no_double_count(workshop):
    client=client_for(workshop)
    raw=item(client,'material','WASTE-RAW','Raw',unit='g',pack_qty='1000',pack_price='20')
    comp=manufactured(client,[(raw,'200','g')])
    purchased=item(client,'purchased','WASTE-BOX','Box',pack_qty='50',pack_price='15')
    with workshop.app_context():
        eid=Equipment.query.filter_by(profile='PLA provisional').one().id
        doc=santa(eid)
        # Explicitly mark manufactured/purchased/packaging: they must still never enter raw print waste.
        doc['lines'] += [dict(item_id=comp,quantity='1',unit='piece',print_material='yes'),
                         dict(item_id=purchased,quantity='1',unit='piece',purpose='packaging',print_material='yes'),
                         dict(source='manual',name='Laser sheet',quantity='1',unit='sheet',unit_cost='2'),
                         dict(source='manual',name='Wrap',purpose='packaging',quantity='1',unit='piece',unit_cost='.1',print_material='yes')]
        result=estimate(doc,CONFIG,direct_materials=True)
        assert Decimal(result['print_waste_exact'])==Decimal('.0523226')
        assert result['buffer']=='0.10'  # Legacy 10% of £1 manufactured recipe, not direct PLA.
        assert result['subtotals']['packaging']=='0.40'
        assert result['packaging_included']
        inventory_only=estimate(dict(lines=[dict(item_id=raw,quantity='100',unit='g',print_material='yes'),
                                           dict(item_id=comp,quantity='1',unit='piece',print_material='yes')]),CONFIG,direct_materials=True)
        assert Decimal(inventory_only['print_waste_exact'])==Decimal('.22')
        assert inventory_only['buffer']=='0.10'
        doc['print_waste_rate']='0'
        assert estimate(doc,CONFIG,direct_materials=True)['print_waste']=='0.00'
        doc.pop('print_waste_rate')
        assert estimate(doc,CONFIG|dict(print_waste_rate='20'),direct_materials=True)['print_waste']=='0.10'
        doc['lines'].append(dict(item_id=purchased,quantity='1',unit='piece'))
        with pytest.raises(ValueError,match='avoid double-counting'):
            estimate(doc,CONFIG,direct_materials=True)


def test_blank_zero_price_rounding_and_changed_inputs(workshop):
    with workshop.app_context():
        doc=santa(Equipment.query.filter_by(profile='PLA provisional').one().id)
        base=estimate(doc,CONFIG,direct_materials=True)
        for mode in ['exact','half','whole','95']:
            result=estimate(doc|dict(price_rounding=mode),CONFIG,direct_materials=True)
            assert Decimal(result['recommended_price'])>=Decimal(result['suggestion_exact'])
        blank=estimate(doc|dict(selling_price=''),CONFIG,direct_materials=True)
        assert blank['selling_price'] is None and blank['fees'] is None and blank['profit'] is None
        zero=estimate(doc|dict(selling_price='0'),CONFIG,direct_materials=True)
        assert zero['selling_price']=='0.00' and Decimal(zero['profit'])<0
        assert estimate(doc|dict(labour_minutes='4'),CONFIG,direct_materials=True)['cost']!=base['cost']
        assert estimate(doc|dict(selling_price='7'),CONFIG,direct_materials=True)['profit']!=base['profit']
        assert estimate(doc,CONFIG|dict(fee_percent='3'),direct_materials=True)['profit']!=base['profit']
        selected=estimate(doc|dict(fee_profile='online'),CONFIG|dict(fee_profiles=[dict(id='online',percent='3',fixed='.30')]),direct_materials=True)
        assert selected['fees']=='0.48'
        doc['equipment'][0]['minutes']='300'
        assert estimate(doc,CONFIG,direct_materials=True)['cost']!=base['cost']
        assert hypothetical_cost(dict(quantity='26',unit='g',unit_cost='.01399'))['amount']==Decimal('.36374')


def test_preview_save_reopen_no_placeholders_and_no_external_mutations(workshop,monkeypatch):
    import requests
    monkeypatch.setattr(requests.sessions.Session,'request',lambda *a,**k:pytest.fail('No external HTTP'))
    client=client_for(workshop)
    with workshop.app_context():
        eid=Equipment.query.filter_by(profile='PLA provisional').one().id
        db.session.add(CostingSettings(id=1,document=json.dumps(CONFIG),version=1));db.session.commit()
        count=Item.query.count()
    form=dict(name='Santa',action='preview',manual_name=['Red','Ivory','Beige','Black'],
              manual_quantity=['13','17','2','2'],manual_unit=['g']*4,manual_cost=['']*4,
              manual_cost_mode=['purchase']*4,manual_purchase_cost=['13.99']*4,
              manual_purchase_quantity=['1']*4,manual_purchase_unit=['kg']*4,
              manual_conversion_factor=['']*4,manual_print_material=['yes']*4,
              equipment_id=str(eid),minutes='250',timing_mode='item',usable_units='1',allocation='',share_percent='100',
              labour_minutes='2',selling_price='6',print_waste_rate='11')
    for rate in ['11','20','0','']:
        response=client.post('/production/bom',data=form|dict(print_waste_rate=rate))
        assert response.status_code==200
        assert b'3D-print material failure / waste allowance' in response.data
    for _ in range(5):
        assert client.post('/production/bom',data=form).status_code==200
    reply=client.post('/production/bom',data=form|dict(action='save'));assert reply.status_code==303
    url=reply.headers['Location']
    for version in ['1','2','3']:
        assert client.post(url,data=form|dict(action='save',version=version)).status_code==303
        assert client.get(url).status_code==200
    with workshop.app_context():
        scenario=BomScenario.query.one();doc=json.loads(scenario.document)
        assert len(doc['lines'])==4 and len(doc['equipment'])==1
        assert doc['print_waste_rate']=='11'
        assert Decimal(doc['_snapshot']['result']['print_waste_exact'])==Decimal('.0523226')
        assert Item.query.count()==count and Movement.query.count()==0
    pack=dict(pack_name='Box',pack_quantity='1',pack_unit='piece',pack_cost='',pack_cost_mode='purchase',
              pack_purchase_cost='15',pack_purchase_quantity='50',pack_purchase_unit='piece',pack_conversion_factor='',pack_item_id='')
    assert client.post(url,data=form|pack|dict(action='save',version='4')).status_code==303
    with workshop.app_context():
        doc=json.loads(BomScenario.query.one().document)
        assert len(doc['lines'])==5
        assert doc['_snapshot']['result']['subtotals']['packaging']=='0.30'
        snapshot=doc['_snapshot']
        db.session.get(CostingSettings,1).document=json.dumps(CONFIG|dict(print_waste_rate='20'));db.session.commit()
    assert client.get(url).status_code==200
    with workshop.app_context():
        assert json.loads(BomScenario.query.one().document)['_snapshot']==snapshot


def test_packaging_from_saved_inventory_is_read_only(workshop):
    client=client_for(workshop)
    box=item(client,'purchased','PACK-50','50 saved boxes',pack_qty='50',pack_price='15')
    reply=client.post('/production/bom/hypothetical-preview',json=dict(item_id=box,quantity='1',unit='piece'))
    assert reply.status_code==200 and reply.json['display_amount']=='0.30'
    form=dict(name='Inventory packaging',action='save',pack_item_id=str(box),pack_name='',pack_quantity='1',
              pack_unit='piece',pack_cost='',pack_cost_mode='purchase',pack_purchase_cost='',pack_purchase_quantity='',
              pack_purchase_unit='',pack_conversion_factor='')
    assert client.post('/production/bom',data=form).status_code==303
    with workshop.app_context():
        saved=json.loads(BomScenario.query.one().document)
        assert saved['lines']==[dict(item_id=box,quantity='1',unit='piece',purpose='packaging')]
        assert saved['_snapshot']['result']['subtotals']['packaging']=='0.30'
        assert Movement.query.count()==0
