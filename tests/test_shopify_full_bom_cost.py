"""Full BOM cost authority shared by manual and automatic Shopify publishing."""
import json
from decimal import Decimal, ROUND_HALF_UP

import pytest
from flask import current_app
from app import db, shopify_drafts as drafts, automatic_publishing as publisher
from app.inventory import product_bom as bom
from app.inventory.models import CostingSettings, Equipment, Movement
from test_inventory import client_for, item, manufactured
from test_phase3_woo_publish_preview import preview_app
from test_shopify_drafts import world
from test_shopify_variables import variable_world
from test_product_bom import SimpleCost, VariableCost, edit, add
from test_automatic_publishing import authorise, execute, notices


def settings(**overrides):
    values=dict(buffer='11', print_waste_rate='20', tariff='.24226', labour_rate='30',
        markup='100', fee_percent='2', fee_fixed='.25', overhead='.20')
    values.update(overrides)
    row=db.session.get(CostingSettings,1)
    if row: row.document=json.dumps(values)
    else: db.session.add(CostingSettings(id=1,document=json.dumps(values),version=1))
    db.session.commit()
    return values


def source(product, shared):
    client=client_for(current_app._get_current_object())
    raw=item(client,'material','PLA','PLA',unit='g',pack_qty='1000',pack_price='13.99')
    part=manufactured(client,[(raw,'52','g')])  # 13 g per component
    box=item(client,'purchased','BOX','Box',pack_qty='50',pack_price='15')
    printer=Equipment(name='Printer',profile='PLA',watts='250',source='estimated',active=True)
    db.session.add(printer);db.session.commit()
    definition=dict(cost={'mode':'calculated'},lines={'part':add(part),'box':{**add(box),'purpose':'packaging'}},
        costing=dict(labour_minutes='2',other='.10',equipment=[dict(equipment_id=printer.id,minutes='250')]))
    edit(shared,live=True,production=definition)
    settings()
    return definition


@pytest.mark.parametrize('automatic',[False,True])
def test_full_cost_written_and_verified_by_both_publishers(world,notices,automatic):
    product,_,shared=world
    source(product,shared)
    # Independent equation: recipe + one waste allowance + box + power + labour + other/overhead.
    base=Decimal(13)*Decimal('13.99')/1000
    exact=base*(1+Decimal('.11'))+Decimal('.30')+Decimal(250)/1000*Decimal(250)/60*Decimal('.24226')+Decimal(2)/60*30+Decimal('.10')+Decimal('.20')
    expected=format(exact.quantize(Decimal('.01'),rounding=ROUND_HALF_UP),'.2f')
    assert expected=='2.05'
    assert bom.contract(product)[product.sku]['value']==expected
    adapter=SimpleCost()
    if automatic:
        authorise(adapter)
        assert execute(product,adapter)=='verified'
    else:
        plan,token=drafts.preview(product,adapter)
        assert token,plan['blockers']
        assert plan['production_costs'][product.sku]['value']==expected
        row=drafts.confirm(token,True,adapter)
        assert row.state=='verified',drafts.evidence(row).get('error')
    assert [amount for _,amount in adapter.cost_writes]==[expected]
    # Rate-only changes alter the cost fingerprint and update without changing selling price.
    config=db.session.get(CostingSettings,1);values=json.loads(config.document);values['labour_rate']='60';config.document=json.dumps(values);db.session.commit()
    if automatic:
        publisher.refresh_production_costs(fence=lambda:None)
        assert execute(product,adapter)=='verified'
    else:
        plan,token=drafts.preview(product,adapter)
        assert token and plan['production_costs'][product.sku]['value']=='3.05'
        assert drafts.confirm(token,True,adapter).state=='verified'
    assert [amount for _,amount in adapter.cost_writes]==['2.05','3.05']
    assert Movement.query.count()==0


def test_missing_processing_cost_blocks_partial_cost_and_manual_remains_explicit(world):
    product,_,shared=world
    definition=source(product,shared)
    config=db.session.get(CostingSettings,1);values=json.loads(config.document);values['tariff']=None;config.document=json.dumps(values);db.session.commit()
    cost=bom.contract(product)[product.sku]
    assert cost['value'] is None and 'tariff' in cost['problem']
    plan,token=drafts.preview(product,SimpleCost())
    assert not token and any('tariff' in reason for reason in plan['blockers'])
    definition['cost']={'mode':'manual','amount':'7.50'};edit(shared,production=definition)
    assert bom.contract(product)[product.sku]['value']=='7.50'
    definition['cost']={'mode':'unconfigured'};edit(shared,production=definition)
    assert bom.contract(product)=={}


@pytest.mark.parametrize('variable_world',['Single Variable'],indirect=True)
def test_variant_quantities_and_labour_use_full_inherited_costs(variable_world):
    product,shared,*_=variable_world
    client=client_for(current_app._get_current_object())
    component=item(client,'purchased','PART','Part',pack_qty='1',pack_price='1')
    first=product.variations[0].sku
    definition=dict(cost={'mode':'calculated'},lines={'part':add(component)},costing={'labour_minutes':'1'},
        variants={first:dict(lines={'part':dict(op='modify',quantity='2')},costing={'labour_minutes':'2'})})
    edit(shared,production=definition)
    settings(overhead='0')
    costs=bom.contract(product)
    assert {sku:row['value'] for sku,row in costs.items()}=={v.sku:('3.50' if v.sku==first else '1.50') for v in product.variations}
    adapter=VariableCost();plan,token=drafts.preview(product,adapter)
    assert token,plan['blockers']
    assert drafts.confirm(token,True,adapter).state=='verified'
    assert sorted(value for _,value in adapter.cost_writes)==sorted(row['value'] for row in costs.values())
    assert Movement.query.count()==0
