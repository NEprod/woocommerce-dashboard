from decimal import Decimal
import json
import pytest
from app import db
from app.inventory.profitability import hypothetical_cost, estimate
from app.inventory.models import BomScenario, Identity, Item, Movement, Transaction
from test_inventory import workshop,client_for  # noqa: F401
from test_setup_flow import setup_app  # noqa: F401


def purchase(**changes):
    return dict(source='manual',name='Red PLA',cost_mode='purchase',purchase_cost='13.99',
                purchase_quantity='1',purchase_unit='kg',quantity='26',unit='g',**{}) | changes


@pytest.mark.parametrize('line,expected',[
    (purchase(),'0.36374'),
    (purchase(purchase_cost='20',purchase_quantity='2',quantity='150'),'1.5'),
    (purchase(purchase_cost='5',purchase_quantity='10',purchase_unit='piece',quantity='2',unit='piece'),'1'),
    (purchase(purchase_quantity='1000',purchase_unit='g',quantity='.026',unit='kg'),'0.36374'),
    (purchase(purchase_cost='4',purchase_quantity='2',purchase_unit='l',quantity='250',unit='ml'),'.5'),
    (purchase(purchase_cost='5',purchase_quantity='2',purchase_unit='pack',quantity='4',unit='piece',conversion_factor='10'),'1'),
    (purchase(purchase_cost='5',purchase_quantity='1',purchase_unit='sheet',quantity='2',unit='piece',conversion_factor='10'),'1'),
])
def test_purchase_costs(line,expected):
    assert hypothetical_cost(line)['amount']==Decimal(expected)


@pytest.mark.parametrize('changes',[
    dict(purchase_quantity=''),dict(purchase_quantity='0'),dict(purchase_quantity='-1'),
    dict(purchase_cost='-1'),dict(purchase_unit=''),dict(unit=''),dict(quantity='-1'),dict(quantity='0'),
    dict(purchase_unit='sheet'),dict(purchase_unit='pack',unit='piece'),
    dict(purchase_unit='sheet',unit='piece',conversion_factor='0'),
    dict(purchase_unit='sheet',unit='piece',conversion_factor='-1'),
    dict(unit='piece',quantity='1.5',purchase_unit='piece'),dict(conversion_factor='2'),
])
def test_invalid_purchase_fields_rejected(changes):
    with pytest.raises(ValueError): hypothetical_cost(purchase(**changes))


def test_unknown_and_zero_cost_are_distinct_and_legacy_still_works():
    assert hypothetical_cost(purchase(purchase_cost=''))['amount'] is None
    assert hypothetical_cost(purchase(purchase_cost='0'))['amount']==0
    assert hypothetical_cost(dict(quantity='26',unit='g',unit_cost='.01399'))['amount']==Decimal('.36374')


def test_line_costs_are_not_rounded_before_aggregation(workshop):
    with workshop.app_context():
        result=estimate(dict(lines=[purchase(),purchase(name='Second material')],selling_price='1'),
                        dict(fee_percent='0',fee_fixed='0'),direct_materials=True)
        assert result['cost']=='0.73'
        assert result['profit']=='0.27'
        assert [row['cost'] for row in result['lines']]==['0.36374','0.36374']


def test_purchase_rows_preview_save_reopen_and_edit_are_read_only_for_inventory(workshop,monkeypatch):
    import requests
    monkeypatch.setattr(requests.sessions.Session,'request',lambda *a,**k:pytest.fail('External HTTP forbidden'))
    client=client_for(workshop)
    with workshop.app_context():
        before=[model.query.count() for model in (Identity,Item,Movement,Transaction)]
    preview=client.post('/production/bom/hypothetical-preview',json=purchase())
    assert preview.status_code==200
    assert Decimal(preview.json['amount'])==Decimal('.36374')
    assert preview.json['display_amount']=='0.36374'
    assert Decimal(preview.json['normalized_unit_cost'])==Decimal('.01399')
    assert client.post('/production/bom/hypothetical-preview',json=purchase(purchase_quantity='0')).status_code==400
    form=dict(name='Red PLA test',action='save',manual_name='Red PLA',manual_quantity='26',manual_unit='g',
              manual_cost='',manual_cost_mode='purchase',manual_purchase_cost='13.99',manual_purchase_quantity='1',
              manual_purchase_unit='kg',manual_conversion_factor='',selling_price='5')
    saved=client.post('/production/bom',data=form);assert saved.status_code==303
    html=client.get(saved.headers['Location']).get_data(as_text=True)
    assert '0.36374' in html and 'Total purchase cost' in html and '13.99' in html
    with workshop.app_context():
        scenario=BomScenario.query.one();document=json.loads(scenario.document)
        assert document['lines'][0]['purchase_unit']=='kg'
        assert document['_snapshot']['result']['cost']=='0.36'
        assert document['_snapshot']['result']['lines'][0]['cost']=='0.36374'
    form.update(version='1',manual_quantity='52')
    assert client.post(saved.headers['Location'],data=form).status_code==303
    with workshop.app_context():
        line=json.loads(BomScenario.query.one().document)['_snapshot']['result']['lines'][0]
        assert line['cost']=='0.72748'
        assert [model.query.count() for model in (Identity,Item,Movement,Transaction)]==before
