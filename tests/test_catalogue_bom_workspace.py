"""Authoritative JSON BOM workspace/import regressions; isolated business data."""
import copy
import json
from decimal import Decimal

import pytest
from app import db
from app.inventory import product_bom as bom
from app.inventory.catalogue_bom import import_scenario, effective_costing, document
from app.inventory.models import BomScenario, CostingSettings, Equipment, Movement, Item, ProductCostingProfile
from app.inventory.profitability import estimate
from app.models import Product
from app.product_info import validate_product_info
from test_inventory import item, manufactured, workshop, client_for
from test_setup_flow import setup_app
from test_metadata_editor_core import core, save


def add(item_id, quantity='1', unit='piece', **extras):
    return dict(op='add', item_id=item_id, quantity=quantity, unit=unit, **extras)


def seed(app, client):
    pla = item(client, 'material', 'SANTA-PLA', 'Santa PLA', unit='g', pack_qty='1', pack_unit='kg', pack_price='13.99')
    body = manufactured(client, [(pla, '40', 'g')])
    box = item(client, 'purchased', 'SANTA-BOX', 'Santa box', pack_qty='50', pack_unit='piece', pack_price='15')
    with app.app_context():
        equipment = Equipment(name='X2D', category='3D Printer', profile='PLA provisional', watts='250', source='estimated', active=True)
        db.session.add(equipment)
        settings = db.session.get(CostingSettings, 1)
        config = dict(tariff='.24226', labour_rate='30', fee_percent='2', fee_fixed='.25', buffer='10', print_waste_rate='11', markup='100')
        if settings: settings.document=json.dumps(config)
        else: db.session.add(CostingSettings(id=1, document=json.dumps(config), version=1))
        db.session.commit()
        eid = equipment.id
    return pla, body, box, eid


def production(pla, body, box, equipment):
    return dict(lines=dict(body=add(body,'2'), pla=add(pla,'34','g',print_material='yes'),
                           box=add(box,purpose='packaging')),
                costing=dict(equipment=[dict(equipment_id=equipment,minutes='250',timing_mode='item')],
                             labour_minutes='2', print_waste_rate='11', markup='100', buffer='10'))


def test_full_collection_and_sparse_product_json_roundtrip_validator(core):
    app, client, root, _, parents, *_ = core
    ids=seed(app, client); source=production(*ids)
    path='Simple Collection/Blue Token'; pid=parents[path]
    response=save(core,path,kind='shared',data={'production':source,'meta_title':'Santa fixture','meta_description':'Isolated Santa metadata validation fixture.'})
    assert response.status_code==200, response.json
    raw=root/'Simple Collection/product_info.json'
    before=raw.read_bytes()
    metadata=json.loads(before)
    result=validate_product_info(metadata,'collection')
    assert result.valid, result.errors
    assert not [w for w in result.warnings if w.path.startswith('$.production')]
    assert metadata['production']==source
    with app.app_context():
        from app.collections_workspace import collection_metadata_source
        validation=collection_metadata_source(db.session.get(Product,pid).collection,root)
        assert validation['state']=='healthy',validation
    child=dict(mode='add',lines={'gift':add(ids[1],'1')},costing=dict(labour_minutes='1', other='.10',print_waste_rate='0'))
    assert save(core,path,data={'production':child}).status_code==200
    assert raw.read_bytes()==before
    assert json.loads((root/path/'product_info.json').read_text())['production']==child
    with app.app_context():
        p=db.session.get(Product,pid); price=p.regular_price
        assert bom.product_costs(p)[p.sku]['count']==4
        assert Movement.query.count()==0
    preview=client.post(f'/api/products/{pid}/production-costing',json={'kind':'override','production':child})
    assert preview.status_code==200,preview.json
    r=preview.json['parent']; profit=r['profitability']
    assert profit['print_waste']=='0.00'
    assert profit['labour']=='1.50'
    assert 'Estimated' in r['profitability_html']
    assert r['shopify']!=profit['cost']  # no full-cost export regression
    with app.app_context():
        from app.inventory.profitability import finished_product_estimate
        p=db.session.get(Product,pid)
        assert p.regular_price==price
        assert finished_product_estimate(p,p.sku)['cost']==profit['cost']


@pytest.mark.parametrize('mode,quantity',[('inherit','1'),('add','3'),('replace','2')])
def test_three_base_modes_preserve_collection_and_no_duplicate_inherited_copy(workshop,mode,quantity):
    c=client_for(workshop); iid=item(c,'purchased','INHERIT','Piece',pack_qty='1',pack_price='1')
    shared={'lines':{'shared':add(iid)},'costing':{'labour_minutes':'1'}}
    local={'mode':mode}
    if mode!='inherit':local.update(lines={'local':add(iid,'2')},costing={'labour_minutes':'2'})
    before=copy.deepcopy(shared)
    with workshop.app_context():
        bom.validate(local)
        resolved=bom.resolve(shared,local)
        assert sum(Decimal(r['quantity']) for r in resolved['rows'])==Decimal(quantity)
        cfg=effective_costing(shared,local)
        assert cfg['labour_minutes']=={'inherit':'1','add':'3','replace':'2'}[mode]
    assert shared==before
    assert ('lines' in local)==(mode!='inherit')


def test_shared_engine_costs_and_explicit_zero_precision(workshop):
    ids=seed(workshop,client_for(workshop)); p=production(*ids)
    with workshop.app_context():
        from app.inventory.catalogue_bom import settings
        resolved=bom.resolve(p)
        doc=document(resolved,p,{},price='6')
        result=estimate(doc,settings(),direct_materials=True)
        assert result['complete'] and result['state']=='provisional'
        assert Decimal(result['print_waste_exact'])==Decimal('.0523226')
        assert result['subtotals']['packaging']=='0.30'
        assert result['electricity']=='0.25'
        assert result['labour']=='1.00'
        assert result['buffer']=='0.03'
        assert result==estimate(doc,settings(),direct_materials=True)
        assert Movement.query.count()==0


def scenario(app, source):
    with app.app_context():
        s=BomScenario(name='Saved Santa test',document=json.dumps(source),version=4)
        db.session.add(s);db.session.commit();return s.id


def test_inventory_import_all_fields_snapshot_and_price_preserved(core):
    app,client,root,_,parents,*_=core
    p=production(*seed(app,client))
    source={**p['costing'],'lines':[{k:v for k,v in row.items() if k!='op'} for row in p['lines'].values()],
            'selling_price':'123.45','_snapshot':{'result':{'cost':'old historical'}}}
    sid=scenario(app,source); pid=parents['Simple Collection/Blue Token']
    before=(root/'Simple Collection/product_info.json').read_bytes()
    response=client.post(f'/api/products/{pid}/bom-import',json={'kind':'shared','scenario_id':sid,'production':{}})
    assert response.status_code==200,response.json
    result=response.json
    assert result['eligible'],result['problems']
    assert result['production']['costing']==p['costing']
    assert 'selling_price' not in result['production']['costing']
    assert len(result['production']['lines'])==3
    assert result['result']['selling_price'] is None
    assert (root/'Simple Collection/product_info.json').read_bytes()==before
    with app.app_context():
        assert json.loads(db.session.get(BomScenario,sid).document)==source
        assert Movement.query.count()==0
    assert save(core,'Simple Collection/Blue Token',kind='shared',data={'production':result['production']}).status_code==200


@pytest.mark.parametrize('problem',['hypothetical','missing_inventory','missing_equipment','custom_field','incompatible','empty','bad_fee','packaging_manual'])
def test_import_rejections_explain_entire_failure(workshop,problem):
    ids=seed(workshop,client_for(workshop)); p=production(*ids)
    source={**p['costing'],'lines':[{k:v for k,v in row.items() if k!='op'} for row in p['lines'].values()]}
    if problem=='hypothetical':source['lines'].append(dict(source='manual',name='Hypothetical PLA',quantity='1',unit='g',unit_cost='1'))
    if problem=='packaging_manual':source['lines'][-1]=dict(source='manual',purpose='packaging',name='Box',quantity='1',unit='piece',unit_cost='1')
    if problem=='missing_inventory':source['lines'][0]['item_id']=9999999
    if problem=='missing_equipment':source['equipment'][0]['equipment_id']=9999999
    if problem=='custom_field':source['unrepresented_assumption']='do not silently drop'
    if problem=='incompatible':source['lines'][0]['unit']='ml'
    if problem=='empty':source['lines']=[]
    if problem=='bad_fee':source['fee_profile']='missing-profile'
    sid=scenario(workshop,source)
    with workshop.app_context():
        before=json.loads(db.session.get(BomScenario,sid).document)
        result=import_scenario(db.session.get(BomScenario,sid))
        assert not result['eligible'] and result['problems'] and result['production'] is None
        assert json.loads(db.session.get(BomScenario,sid).document)==before
        assert Movement.query.count()==0


def test_import_inherit_readonly_and_duplicate_conflict(workshop):
    c=client_for(workshop); iid=item(c,'purchased','IMPORT','Piece',pack_qty='1',pack_price='1')
    sid=scenario(workshop,dict(lines=[dict(item_id=iid,quantity='2',unit='piece')]))
    with workshop.app_context():
        s=db.session.get(BomScenario,sid)
        assert not import_scenario(s,current={'mode':'inherit'})['eligible']
        existing={'mode':'add','lines':{'same':add(iid)}}
        original=copy.deepcopy(existing)
        assert not import_scenario(s,current=existing,append=True)['eligible']
        assert existing==original


def test_workspace_permissions_and_no_hypothetical_ui(core):
    app,client,_,_,parents,*_=core
    ids=seed(app,client);pid=parents['Simple Collection/Blue Token']
    with app.app_context():
        db.session.get(Item,ids[2]).active=False
        db.session.commit()
    r=client.get(f'/api/products/{pid}/bom-workspace')
    assert r.status_code==200
    assert b'Add Packaging Item' in r.data and b'Add Production Process' in r.data
    assert b'manual_name' not in r.data and b'data-row-template="manual"' not in r.data
    assert b'Santa box (archived)' in r.data  # retain old references in selects
    assert r.headers['Cache-Control']=='private, no-store'


def test_validator_is_pure_and_supports_new_main_rewrite():
    value=dict(mode='replace',lines={'part':add(1)},costing=dict(equipment=[dict(equipment_id=1,minutes='250',timing_mode='batch',usable_units='7',allocation='identical')]))
    assert validate_product_info({'production':value},'override').valid
    value['costing']['selling_price']='6'
    assert not validate_product_info({'production':value},'override').valid


def test_costing_merges_variant_requirements_without_mutating_option_rules(workshop):
    ids=seed(workshop,client_for(workshop)); parent=production(*ids)
    parent['options']={'Finish':{'Red':dict(mode='add',lines={'extra':add(ids[1],'2')})}}
    parent['variants']={'SANTA-RED':dict(mode='add',costing=dict(labour_minutes='1',buffer='0'))}
    child=dict(mode='add',variants={'SANTA-RED':dict(mode='add',costing=dict(other='.50',markup='120'))})
    original=copy.deepcopy(parent)
    with workshop.app_context():
        resolved=bom.resolve(parent,child,'SANTA-RED',matches=[('Finish','Red')])
        assert resolved['count']==4
        doc=document(resolved,parent,child,'SANTA-RED',price='6')
        assert doc['labour_minutes']=='3' and doc['buffer']=='0' and doc['markup']=='120'
        assert len(doc['equipment'])==1
        child['variants']['SANTA-RED']={'mode':'replace','lines':{'solo':add(ids[1])},'costing':{'labour_minutes':'0'}}
        resolved=bom.resolve(parent,child,'SANTA-RED',matches=[('Finish','Red')])
        assert resolved['count']==1
        assert document(resolved,parent,child,'SANTA-RED')['labour_minutes']=='0'
    assert parent==original
    with workshop.app_context():
        rewrite={'mode':'replace','lines':{'replacement':add(ids[1])},'costing':{'labour_minutes':'0'}}
        independent=bom.resolve(parent,rewrite,'SANTA-RED',matches=[('Finish','Red')])
        assert independent['count']==1 and independent['rows'][0]['line_id']=='replacement'
        assert 'equipment' not in effective_costing(parent,rewrite,'SANTA-RED')


def test_missing_costing_refs_block_new_writes_but_preserve_old_evidence(core):
    app,client,_,_,parents,*_=core
    ids=seed(app,client);source=production(*ids)
    source['costing']['equipment'][0]['equipment_id']=999999
    r=save(core,'Simple Collection/Blue Token',kind='shared',data={'production':source})
    assert r.status_code==400 and 'equipment' in str(r.json).lower()
    # Pure contract accepts a retained ID: the read-only preview reports missing
    # evidence rather than deleting historical references during metadata load.
    assert validate_product_info({'production':source},'override').valid


def test_cross_section_packaging_duplicate_cannot_be_saved(core):
    app,client,root,_,_,*_=core
    ids=seed(app,client)
    before=(root/'Simple Collection/product_info.json').read_bytes()
    source=production(*ids)
    source['lines']['duplicate']=add(ids[2])
    response=save(core,'Simple Collection/Blue Token',kind='shared',data={'production':source})
    assert response.status_code==400
    assert 'double-counting' in str(response.json)
    assert (root/'Simple Collection/product_info.json').read_bytes()==before


def test_import_review_rejects_duplicate_inherited_requirements(core):
    app,client,_,_,parents,*_=core
    ids=seed(app,client);source=production(*ids)
    assert save(core,'Simple Collection/Blue Token',kind='shared',data={'production':source}).status_code==200
    sid=scenario(app,dict(lines=[dict(item_id=ids[1],quantity='1',unit='piece')]))
    pid=parents['Simple Collection/Blue Token']
    response=client.post(f'/api/products/{pid}/bom-import',json=dict(kind='override',scenario_id=sid,production={'mode':'add'}))
    assert response.status_code==200 and not response.json['eligible']
    assert 'Duplicate' in response.json['problems'][0]


def test_legacy_three_field_variant_conflict_resolution_remains_valid(workshop):
    c=client_for(workshop); iid=item(c,'purchased','CONFLICT','Part',pack_qty='1',pack_price='1')
    shared={'lines':{'part':add(iid)}}
    local={'lines':{'part':add(iid,'2')},'variants':{'SKU':{'lines':{'part':dict(op='modify',item_id=iid,quantity='3',unit='piece')}}}}
    with workshop.app_context():
        assert bom.resolve(shared,local)['conflicts']
        result=bom.resolve(shared,local,'SKU')
        assert not result['conflicts'] and result['shopify']=='3.00'


def test_variant_only_json_costing_is_available_to_shared_finished_product_engine(core):
    app,client,_,_,parents,*_=core
    ids=seed(app,client);path='Variable Collection/Badge One'
    with app.app_context():
        p=db.session.get(Product,parents[path]);sku=p.variations[0].sku
    value=dict(lines={'body':add(ids[1])},variants={sku:dict(costing=dict(labour_minutes='2'))})
    response=save(core,path,kind='shared',data={'production':value})
    assert response.status_code==200,response.json
    with app.app_context():
        from app.inventory.profitability import finished_product_estimate
        result=finished_product_estimate(db.session.get(Product,parents[path]),sku)
        assert result['complete'] and result['labour']=='1.00' and result['cost']=='1.15'


def test_explicit_inherit_all_ignores_but_preserves_legacy_local_profile(core):
    app,client,_,_,parents,*_=core
    ids=seed(app,client);pid=parents['Simple Collection/Blue Token']
    assert save(core,'Simple Collection/Blue Token',kind='shared',data={'production':{'lines':{'body':add(ids[1])}}}).status_code==200
    legacy=json.dumps(dict(labour_minutes='5',selling_price='50'))
    with app.app_context():
        p=db.session.get(Product,pid)
        db.session.add(ProductCostingProfile(product_id=pid,sku=p.sku,document=legacy));db.session.commit();sku=p.sku
    implicit=client.post(f'/api/products/{pid}/production-costing',json=dict(kind='override',production={})).json['parent']
    assert implicit['legacy_costing'] and implicit['profitability']['selling_price']=='50.00'
    explicit=client.post(f'/api/products/{pid}/production-costing',json=dict(kind='override',production={'mode':'inherit'})).json['parent']
    assert not explicit['legacy_costing'] and explicit['profitability']['selling_price']!='50.00'
    assert explicit['profitability']['labour']=='0.00'
    with app.app_context():assert db.session.get(ProductCostingProfile,(pid,sku)).document==legacy
