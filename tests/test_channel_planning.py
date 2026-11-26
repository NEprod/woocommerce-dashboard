"""Focused Decimal channel assumptions and independent product-copy regressions."""
import copy
import json
from decimal import Decimal
from werkzeug.datastructures import MultiDict
import pytest
from app import db
from app.inventory.channels import DEFAULTS, round_49_99, validate
from app.inventory.profitability import estimate as production_estimate
from app.inventory.models import BomScenario, CostingSettings, Movement, ProductCostingProfile
from app.inventory.what_if import load
from app.models import Product
from test_inventory import workshop, client_for
from test_setup_flow import setup_app
from test_metadata_editor_core import core, save
from test_catalogue_bom_workspace import seed, production


def estimate(document, config):
    return production_estimate(document, config, direct_materials=True)


def manual(price='10', **extra):
    return dict(lines=[dict(source='manual',name='Test',quantity='1',unit='g',unit_cost='3')],selling_price=price,**extra)


@pytest.mark.parametrize('source,expected',[('0','.49'),('5','5.49'),('5.49','5.49'),('5.49001','5.99'),('5.50','5.99'),('5.99','5.99'),('6','6.49')])
def test_rounding_is_decimal_upwards_and_stable(source,expected):
    assert round_49_99(Decimal(source)) == Decimal(expected)


@pytest.mark.parametrize('price,expected',[('5','6.49'),('10','11.99'),('20','23.49')])
def test_uplift_and_full_fee_breakdown(price,expected):
    result=estimate(manual(price),dict(fee_percent='2',fee_fixed='.25'))
    channel=result['channels'];etsy=channel['rows'][1]
    assert channel['etsy_suggested']==expected and etsy['price']==expected
    p=Decimal(expected)
    fees=(p*Decimal('.065')+p*Decimal('.04')+Decimal('.20')+p*Decimal('.0048')+Decimal('.15'))*Decimal('1.20')
    assert etsy['fees']==f'{fees:.2f}'
    assert etsy['profit']==f'{p-Decimal(3)-fees:.2f}'
    assert result['fees']==f'{Decimal(price)*Decimal(".02")+Decimal(".25"):.2f}'
    assert channel['rows'][0]['profit']==result['profit']
    assert result['recommended_price'].endswith(('.49','.99'))


def test_zero_overrides_vat_scope_optional_ads_and_unknowns():
    config=dict(fee_percent='0',fee_fixed='0',channel_pricing=validate(dict(DEFAULTS,etsy_uplift_percent='0',etsy_minimum_uplift='0',vat_percent='0',offsite_percent='15')))
    result=estimate(manual('10',etsy_price='0'),config)
    assert result['channels']['rows'][1]['price']=='0.00'
    assert result['channels']['rows'][1]['fees']=='0.35'
    assert result['channels']['rows'][1]['profit']=='-3.35'
    no_price=estimate(manual(''),config)
    assert no_price['channels']['rows'][0]['basis']=='Suggested Shopify price'
    assert no_price['channels']['base']==no_price['recommended_price']
    partial=manual();partial['lines'][0]['unit_cost']=''
    assert estimate(partial,config)['channels']['complete'] is False
    assert estimate(manual(),{})['channels']['complete'] is False
    with pytest.raises(ValueError): estimate(manual(etsy_price='-1'), {})
    config['channel_pricing'].update(vat_percent='20',vat_listing=True)
    assert estimate(manual(etsy_price='0'),config)['channels']['vat']=='0.07'


def test_explicit_old_rounding_and_fee_profiles_remain_supported():
    config=dict(fee_percent='2',fee_fixed='.25',fee_profiles=[dict(id='custom',percent='3',fixed='.10')])
    source=manual(price_rounding='exact',fee_profile='custom')
    before=copy.deepcopy(source)
    result=estimate(source,config)
    assert result['fees']=='0.40' and result['recommended_price']=='3.20'
    assert source==before
    for value in ('NaN','-1','Infinity'):
        with pytest.raises(ValueError): validate(dict(DEFAULTS,payment_percent=value))
    with pytest.raises(ValueError):validate(dict(DEFAULTS,transaction_percent='90'))


def test_channel_settings_preserve_existing_profiles_and_equipment(workshop):
    client=client_for(workshop)
    with workshop.app_context():
        row=db.session.get(CostingSettings,1)
        if row: config=json.loads(row.document);version=row.version
        else: config={};version=0
    fields={f'channel_{key}':('yes' if val is True else str(val)) for key,val in DEFAULTS.items()}
    response=client.post('/production/costing/settings',data=dict(fields,action='channel_pricing',version=str(version)))
    assert response.status_code==303
    with workshop.app_context():
        stored=json.loads(db.session.get(CostingSettings,1).document)
        assert {k:v for k,v in stored.items() if k!='channel_pricing'}==config
        assert stored['channel_pricing']['listing_fixed']=='0.15'
        assert Movement.query.count()==0
    assert client.post('/production/costing/settings',data=dict(fields,action='channel_pricing',version=str(version))).status_code==400


def test_product_copy_preserves_full_inputs_but_saves_only_independent_scenario(core):
    app,client,root,_,parents,*_=core
    ids=seed(app,client);source=production(*ids);path='Simple Collection/Blue Token';pid=parents[path]
    assert save(core,path,kind='shared',data={'production':source}).status_code==200
    additions=dict(mode='add',lines={'extra':dict(op='add',item_id=ids[1],quantity='1',unit='piece')},costing=dict(labour_minutes='1',other='.10',print_waste_rate='0',etsy_price='14.99'))
    assert save(core,path,data={'production':additions}).status_code==200
    files={p:p.read_bytes() for p in root.rglob('product_info.json')}
    with app.app_context():
        product=db.session.get(Product,pid);sku=product.sku;price=product.regular_price
        document=load(product,sku)
        assert len(document['lines'])==4
        assert document['labour_minutes']=='3' and document['print_waste_rate']=='0'
        assert document['equipment']==source['costing']['equipment']
        assert any(l.get('purpose')=='packaging' for l in document['lines'])
        assert document['etsy_price']=='14.99'
    page=client.get(f'/production/bom?load_product={pid}&load_sku={sku}')
    assert page.status_code==200 and b'Independent copy' in page.data and b'Shopify &amp; Etsy' in page.data
    form=MultiDict(dict(name='Independent test',selling_price=document['selling_price'],labour_minutes=document['labour_minutes'],other=document['other'],print_waste_rate='0',etsy_price='14.99',action='save'))
    for line in document['lines']:
        if line.get('purpose')=='packaging':
            for k in ('name','quantity','unit','cost','cost_mode','purchase_cost','purchase_quantity','purchase_unit','conversion_factor','item_id'):
                form.add('pack_'+k,str(line.get(k,'')))
        else:
            for k in ('item_id','quantity','unit','print_material'):form.add(k,str(line.get(k,'')))
    for process in document['equipment']:
        for k,v in process.items():form.add(k,str(v))
    assert client.post('/production/bom',data=form).status_code==303
    with app.app_context():
        scenario=BomScenario.query.one();stored=json.loads(scenario.document)
        assert scenario.product_id is None and len(stored['lines'])==4
        assert stored['_snapshot']['result']['channels']['rows'][1]['price']=='14.99'
        snapshot=scenario.document;sid=scenario.id
        assert ProductCostingProfile.query.count()==0 and Movement.query.count()==0
        assert db.session.get(Product,pid).regular_price==price
        settings=db.session.get(CostingSettings,1);config=json.loads(settings.document);config['channel_pricing']=dict(DEFAULTS,etsy_uplift_percent='90');settings.document=json.dumps(config);db.session.commit()
    assert client.get(f'/production/bom/{sid}').status_code==200
    with app.app_context():assert db.session.get(BomScenario,sid).document==snapshot
    assert all(p.read_bytes()==content for p,content in files.items())
    assert client.get(f'/production/bom?load_product={pid}&load_sku=missing').status_code==400


def test_variable_copy_resolves_selected_variant_requirements(core):
    app,client,root,_,parents,*_=core
    ids=seed(app,client);path='Variable Collection/Badge One';pid=parents[path]
    with app.app_context():
        product=db.session.get(Product,pid);sku=product.variations[0].sku
    source=production(*ids)
    source['variants']={sku:dict(mode='add',lines={'variant-extra':dict(op='add',item_id=ids[1],quantity='3',unit='piece')},costing=dict(labour_minutes='4'))}
    assert save(core,path,kind='shared',data={'production':source}).status_code==200
    with app.app_context():
        product=db.session.get(Product,pid);copy_doc=load(product,sku)
        assert len(copy_doc['lines'])==4 and copy_doc['labour_minutes']=='6'
        assert sum(Decimal(l['quantity']) for l in copy_doc['lines'] if l['item_id']==ids[1])==5
        assert Movement.query.count()==0 and BomScenario.query.count()==0
