"""Fictional measured packages/baskets. No real Shopify/Discord calls or source edits."""
import copy
from datetime import datetime,timezone
from decimal import Decimal
import json
import sqlite3
import time
import uuid
import pytest
import requests
from app import db
from app.models import User,Product,Variation,VariationAttribute,ShopifyProductIdentity
from app.packaging_models import Package,PackingRecord,ShippingRates
from app import packaging_catalogue as catalogue,packaging_workspace as workspace,packing,shopify_packaging
from app.packaging_contract import effective,validate,merge
from app.database import ensure_database,migration_head,_alembic_config
from app.shopify_connection import READ_QUERIES,WRITE_QUERIES,ShopifyError
from app.utils.json_utils import merge_product_json,apply_variation_modifiers
from test_setup_flow import setup_app


def package(key='card',dims=('18','18','2'),**changes):
    raw={'type':'BOX','external_cm':dict(zip(('length','width','height'),dims)),
        'internal_cm':dict(zip(('length','width','height'),dims)),
        'tare_g':'40','max_contents_g':'2000','max_gross_g':'2100','clearance_cm':'0','protection_weight_g':'0',
        'cost_pence':20,'dimensions_measured':True,'tare_measured':True,'automatic':True,'flat':True,'stackable':True,
        'fragile':False,'protection':['rigid backing']}
    raw.update(changes)
    return dict(catalogue.clean(raw),id=key,name=key,version=1,archived=False)


def item(key='card',dims=('15.5','15.5','.3'),qty=1,**changes):
    raw={'identity':key,'sku':key,'title':key,'packed_dimensions_cm':dict(zip(('length','width','height'),dims)),
        'quantity':qty,'requires_shipping':True,'weight_g':'50','clearance_cm':'0','protection_weight_g':'0',
        'orientation':'flat','classification':'flat','may_share':True,'may_stack':True,'stack_limit':20,'measured':True}
    raw.update(changes);return raw


def rates():
    return {'currency':'GBP','country':'GB','source':'Fictional approved contract, not actual Royal Mail prices',
        'effective_from':'2026-01-01T00:00:00Z','effective_until':'2028-01-01T00:00:00Z',
        'handling_pence':5,'include_packaging':True,'version':'fixture-v1',
        'bands':[{'service':s,'format':f,'max_weight_g':'20000','max_length_cm':'61','max_width_cm':'46','max_height_cm':'46','price_pence':p}
            for s,p in [('tracked_24',500),('tracked_48',400)] for f in ['large_letter','small_parcel','medium_parcel']]}


@pytest.fixture
def packaging_app(setup_app,monkeypatch):
    app,path=setup_app
    monkeypatch.setattr(requests.Session,'request',lambda *a,**k:pytest.fail('Unexpected network'))
    with app.app_context():
        db.session.add(User(username='packing-admin',email='packing@example.test',password='synthetic',is_admin=True));db.session.commit()
    return app,path


def logged(app):
    client=app.test_client()
    with app.app_context():user=User.query.filter_by(username='packing-admin').one();ident=user.id
    with client.session_transaction() as session:session['_user_id']=str(ident);session['_fresh']=True
    return client


def test_seed_exact_seven_unknown_thickness_provisional_and_restart(packaging_app):
    app,path=packaging_app
    with app.app_context():
        rows=Package.query.all();assert len(rows)==7
        identities={p.id for p in rows}
        assert all(uuid.UUID(p.id) for p in rows)
        views=[catalogue.view(r) for r in rows]
        assert all(v['readiness']=='Measurements needed' and not v['tare_measured'] for v in views)
        assert all(v['external_cm']['height'] is None for v in views if v['type']=='ENVELOPE')
    ensure_database('sqlite:///'+str(path))
    with app.app_context():assert {p.id for p in Package.query.all()}==identities
    assert migration_head()=='0017_packaging'


@pytest.mark.parametrize('quantity,expected',[(1,1),(2,1),(4,2)])
def test_card_stacking_and_capacity(quantity,expected):
    p=package(dims=('18','18','1'));result=packing.plan([item(qty=quantity)], [p])
    assert result['state']=='verified' and len(result['parcels'])==expected
    assert sum(len(p['items']) for p in result['parcels'])==quantity


@pytest.mark.parametrize('dims,fit',[('21,14.8,.2','c4'),('29.7,21,.2','c4'),('42,29.7,.3','c3')])
def test_prints_only_suitably_measured_packages(dims,fit):
    packages=[package(),package('c4',('32','22','1')),package('c3',('45','32','2'))]
    result=packing.plan([item('print',tuple(dims.split(',')))],packages)
    assert result['parcels'][0]['package_id']==fit
    if fit=='c3':assert result['parcels'][0]['package_id']!='card'


def test_mixed_actual_geometry_consolidates_a3_a5_two_cards():
    lines=[item(qty=2),item('a5',('21','14.8','.2'),weight_g='100'),item('a3',('42','29.7','.3'),weight_g='150')]
    boxes=[package(),package('c4',('32','22','1')),package('c3',('45','32','2'),type='ENVELOPE')]
    result=packing.plan(lines,boxes,rate_configuration=rates())
    assert result['state']=='verified' and len(result['parcels'])==1
    parcel=result['parcels'][0];assert parcel['package_id']=='c3' and len(parcel['items'])==4
    assert Decimal(parcel['gross_weight_g'])==390
    assert all(q['available'] for q in result['quotes'])
    assert result['quotes'][1]['total_pence']==425
    assert result==packing.plan(lines,boxes,rate_configuration=rates())


def test_a3_and_fragile_ornament_two_parcels_all_postage_included():
    flat=package('flat',('45','32','2'));fragile=package('ornament',('18','13','6'),flat=False,fragile=True,protection=['bubble wrap'],protection_weight_g='10')
    lines=[item('a3',('42','29.7','.3')),item('ornament',('10','8','4'),classification='general',fragile=True,may_stack=False,
        ship_separately=True,requirements=['bubble wrap'])]
    result=packing.plan(lines,[flat,fragile],rate_configuration=rates())
    assert len(result['parcels'])==2
    quote=result['quotes'][1];assert quote['available'] and quote['total_pence']==845
    assert len(quote['parcel_charges_pence'])==2


@pytest.mark.parametrize('change',[
    {'packed_dimensions_cm':{}},{'measured':False},{'weight_g':None},{'clearance_cm':None},
    {'requires_shipping':None},{'orientation':None},{'classification':None}])
def test_missing_information_never_default_quote(change):
    result=packing.plan([item(**change)],[package()],rate_configuration=rates())
    assert result['state']=='insufficient_information' and result['quotes']==[]


@pytest.mark.parametrize('change',[
    {'type':'SOFT_PACK'},{'max_contents_g':'20'},{'max_gross_g':'60'},{'flat':False},
    {'internal_cm':{'length':'10','width':'10','height':'2'}}])
def test_unsuitable_weight_geometry_and_materials_rejected(change):
    result=packing.plan([item()],[package(**change)])
    assert result['state']=='no_suitable_package'


def test_volume_alone_never_proves_fit_no_rolling():
    result=packing.plan([item('a3',('42','29.7','.3'))],[package('large',('35','25','10'))])
    assert result['state']=='no_suitable_package'
    rolled=packing.plan([item(classification='rollable',rollable=True)],[package()])
    assert rolled['state']=='no_suitable_package'


def test_orientation_padding_and_non_stackable():
    assert packing.plan([item(dims=('16','10','1'),orientation='fixed')],[package(dims=('12','18','2'))])['state']=='no_suitable_package'
    assert packing.plan([item(dims=('16','10','1'))],[package(dims=('12','18','2'))])['state']=='verified'
    assert packing.plan([item(dims=('18','18','1'),clearance_cm='.1')],[package()])['state']=='no_suitable_package'
    result=packing.plan([item(qty=2,may_stack=False)],[package(dims=('18','18','2'))]);assert len(result['parcels'])==2


def test_unknown_mailer_interior_never_verified_even_in_estimate_mode():
    p=package('unmeasured',internal_cm={})
    assert packing.plan([item()],[p],allow_estimated=True)['state']=='no_suitable_package'
    p=package('estimated',dimensions_measured=False)
    assert packing.plan([item()],[p])['state']=='no_suitable_package'
    result=packing.plan([item()],[p],allow_estimated=True,rate_configuration=rates())
    assert result['state']=='estimated' and result['quotes']==[]


def test_manual_selection_preserved_and_separate_shipping():
    result=packing.plan([item(selected_package_id='chosen',qty=2,ship_separately=True)],[package('cheap'),package('chosen')])
    assert len(result['parcels'])==2 and all(p['package_id']=='chosen' for p in result['parcels'])


def test_decimal_tare_and_protection_included():
    result=packing.plan([item(weight_g='10.25',qty=2,protection_weight_g='.125')],
        [package(tare_g='40.1',protection_weight_g='2.2')])
    assert Decimal(result['parcels'][0]['gross_weight_g'])==Decimal('63.05')


def test_bounded_large_basket_and_limits():
    started=time.monotonic();result=packing.plan([item(qty=200)],[package(dims=('18','18','6'))])
    assert sum(len(p['items']) for p in result['parcels'])==200 and time.monotonic()-started<3
    with pytest.raises(ValueError):packing.plan([item(qty=201)],[package()])
    with pytest.raises(ValueError):packing.plan([item(qty=True)],[package()])


def test_nonphysical_only_and_mixed():
    result=packing.plan([item('digital',requires_shipping=False),item()],[package()])
    assert result['ignored_nonphysical']==['digital'] and len(result['parcels'][0]['items'])==1


def test_rate_expiry_missing_service_destination_and_overweight():
    from app.shipping_rates import quote
    parcels=packing.plan([item()],[package()])['parcels']
    assert not any(q['available'] for q in quote(parcels,None))
    assert not any(q['available'] for q in quote(parcels,rates(),at=datetime(2029,1,1,tzinfo=timezone.utc)))
    assert not any(q['available'] for q in quote(parcels,rates(),destination='FR'))
    config=rates();config['bands']=[b for b in config['bands'] if b['service']=='tracked_24']
    assert quote(parcels,config)[0]['available'] and not quote(parcels,config)[1]['available']
    config=rates();config['bands'][0]['price_pence']=1.5
    with pytest.raises(ValueError):quote(parcels,config)


def test_sparse_collection_product_variant_inheritance_false_and_modifier_prices():
    common={'collection_type':'Variable Collection','sku_prefix':'SYN','packaging':{'fragile':True,'may_share':True,'packed_dimensions_cm':{'length':'20','width':'15','height':'1'}},
        'price':'3.50','variation_modifiers':{'Size=A3':{'price':'6.50','weight':'100','packaging':{'packed_dimensions_cm':{'length':'42','width':'29.7'},'may_share':False}}}}
    child={'packaging':{'fragile':False,'selected_package_id':None}}
    resolved=merge_product_json(common,child)
    value=effective(resolved,{'Size':'A3'})
    assert value['fragile'] is False and value['may_share'] is False
    assert value['packed_dimensions_cm']=={'length':'42','width':'29.7','height':'1'}
    assert apply_variation_modifiers(resolved,{'Size':'A3'},log=lambda *a,**k:None)['price']=='6.50'
    assert child=={'packaging':{'fragile':False,'selected_package_id':None}}


def test_new_packaging_only_exact_rule_does_not_mask_legacy_partial_price():
    document={'price':'3.50','weight':'10','variation_modifiers':{
        'Size=A3':{'price':'8.50','weight':'150'},
        'Colour=Blue|Size=A3':{'packaging':{'ship_separately':True}}}}
    value=apply_variation_modifiers(document,{'Size':'A3','Colour':'Blue'},log=lambda *a,**k:None)
    assert value['price']=='8.50' and value['weight']=='150'
    assert effective(document,{'Size':'A3','Colour':'Blue'})['ship_separately']
    # Historical explicit empty exact modifier still intentionally masks fallback.
    document['variation_modifiers']['Colour=Blue|Size=A3']={}
    assert apply_variation_modifiers(document,{'Size':'A3','Colour':'Blue'},log=lambda *a,**k:None)['price']=='3.50'


@pytest.mark.parametrize('layout',['Simple','Variable Collection','Single Variable'])
def test_existing_schema_and_new_fields_all_layouts(layout):
    from app.product_info import validate_product_info
    data={'collection_type':layout,'sku_prefix':'SYN','price':'3.50','packaging':{'measured':False,'may_share':False}}
    assert validate_product_info(data,'collection').valid
    data['packaging']['packed_dimensions_cm']={'length':'0'}
    assert not validate_product_info(data,'collection').valid


def test_native_explicit_packed_size_2027_only_existing_policy_retained(monkeypatch):
    from app import shopify_native_dimensions as native,shopify_variables as variables
    monkeypatch.setenv('SHOPIFY_NATIVE_DIMENSIONS_ENABLED','true');monkeypatch.setenv('SHOPIFY_PACKED_DIMENSIONS_USE_PRODUCT_DIMENSIONS','false')
    p=type('Parent',(),{})()
    authored={'packaging':{'packed_dimensions_cm':{'length':'42','width':'29.7','height':'.3'},'measured':True}}
    monkeypatch.setattr(native,'product_document',lambda _:authored)
    assert native.desired(p)['desired']=={'length':42.0,'width':29.7,'height':.3,'unit':'CENTIMETERS'}
    assert variables.packed_policy({},authored['packaging'])['desired']['length']==42
    authored['packaging']['measured']=False
    assert native.desired(p)['desired'] is None
    monkeypatch.setenv('SHOPIFY_NATIVE_DIMENSIONS_ENABLED','false')
    assert native.desired(p)['desired'] is None
    assert native.VERSION=='2027-01'


def test_capability_write_only_not_claimed_synced():
    raw={'shop':{'id':'gid://shopify/Shop/42','plan':{'displayName':'Basic'}},
        'queryRoot':{'fields':[]},'mutations':{'fields':[{'name':'shippingPackageUpdate'}]},
        'measurement':{'fields':[{'name':'weight'}]},'measurementInput':{'inputFields':[{'name':'shippingPackageId'}]},'package':None}
    result=shopify_packaging.assess(raw,['read_products','write_products','write_inventory'])
    assert result['assignment_write_schema'] and not result['assignment_read_schema']
    assert result['package_update_schema'] and not result['package_sync_enabled']
    assert result['missing_scopes']==['read_shipping','write_shipping'] and not result['checkout_eligible']
    assert shopify_packaging.CAPABILITY in READ_QUERIES and shopify_packaging.CAPABILITY not in WRITE_QUERIES
    assert not any('shippingPackage' in q or 'carrierServiceCreate' in q for q in WRITE_QUERIES)


def test_local_admin_crud_csrf_versioning_history_no_remote_writes(packaging_app):
    app,path=packaging_app;client=logged(app)
    for page in ('/packaging/','/packaging/new','/packaging/simulator','/packaging/rates'):
        response=client.get(page);assert response.status_code==200
        assert response.headers['Cache-Control']=='no-store, private'
    data=package();data['name']='Fictional measured package'
    created=client.post('/packaging/new',json=data);assert created.status_code==201
    ident=created.json['id']
    assert client.get(f'/packaging/{ident}/edit').status_code==200
    data['version']=1
    assert client.post(f'/packaging/{ident}/edit',json=data).status_code==200
    assert client.post(f'/packaging/{ident}/edit',json=data).status_code==400
    assert client.post(f'/packaging/{ident}/duplicate',json={}).status_code==200
    assert client.post(f'/packaging/{ident}/archive',json={'version':2,'confirm':False}).status_code==400
    assert client.post(f'/packaging/{ident}/archive',json={'version':2,'confirm':True}).json['archived']
    assert client.get(f'/packaging/{ident}/usage').status_code==200
    app.config['WTF_CSRF_ENABLED']=True
    assert client.post('/packaging/new',json=data).status_code==400
    assert client.post('/packaging/simulate',json={'lines':[]}).status_code==400


def test_unauthorised_and_non_admin_blocked(packaging_app):
    app,path=packaging_app
    assert app.test_client().get('/packaging/').status_code in (302,401)
    client=logged(app)
    with app.app_context():u=User.query.filter_by(username='packing-admin').one();u.is_admin=False;db.session.commit()
    assert client.get('/packaging/choices').status_code==403


def test_rate_revisions_and_retained_plan_unchanged_after_package_edit(packaging_app):
    app,path=packaging_app;client=logged(app)
    assert client.post('/packaging/rates',json=rates()).status_code==200
    assert client.post('/packaging/rates',json=dict(rates(),source='Fictional next revision')).status_code==200
    with app.app_context():
        row=catalogue.save(dict(package(),name='Fixture'))
        result=workspace.simulate([item()]);record=db.session.get(PackingRecord,result['record_id']);original=record.result
        catalogue.save(dict(package(),name='Renamed'),row,version=1)
        assert db.session.get(PackingRecord,record.id).result==original
        assert ShippingRates.query.count()==2 and workspace.rates()['source']=='Fictional next revision'


def test_no_public_callback_or_order_write_routes(packaging_app):
    app,path=packaging_app
    assert not any('callback' in rule.rule or 'carrier' in rule.rule for rule in app.url_map.iter_rules() if rule.endpoint.startswith('packaging.'))


def test_stack_limit_of_supporting_item_cannot_be_ignored():
    lines=[item('base',dims=('16','16','.5'),stack_limit=1),item('top',dims=('15','15','.5'),stack_limit=20)]
    result=packing.plan(lines,[package(dims=('18','18','3'))])
    assert len(result['parcels'])==2


def test_full_support_no_overlap_geometry():
    result=packing.plan([item('a',('10','8','2'),qty=5,classification='general',orientation='any')],
        [package('box',('25','20','10'))])
    assert result['state']=='verified'
    for parcel in result['parcels']:
        placed=parcel['items']
        for i,a in enumerate(placed):
            pos=list(map(Decimal,a['position_cm']));size=list(map(Decimal,a['occupied_cm']))
            assert all(pos[k]+size[k]<=Decimal(parcel['external_cm'][axis]) for k,axis in enumerate(('length','width','height')))
            for b in placed[i+1:]:
                other=list(map(Decimal,b['position_cm']));box=list(map(Decimal,b['occupied_cm']))
                assert any(pos[k]+size[k]<=other[k] or other[k]+box[k]<=pos[k] for k in range(3))


def test_complete_planner_endpoint_uses_source_and_persists_not_products(packaging_app,monkeypatch):
    app,path=packaging_app;client=logged(app)
    source={'requires_shipping':True,'price':'3.50','packaging':{k:v for k,v in item().items() if k in {'packed_dimensions_cm','weight_g','clearance_cm','protection_weight_g','orientation','classification','may_share','may_stack','stack_limit','measured'}}}
    original=copy.deepcopy(source)
    monkeypatch.setattr('app.taxonomy_assignments.product_document',lambda p:copy.deepcopy(source))
    with app.app_context():
        p=Product(sku='FICTIONAL-1',title='Fictional card',product_type='simple',catalogue_status='active')
        db.session.add(p);db.session.commit();pid=p.id
        row=catalogue.save(dict(package(),name='Measured fixture'))
    result=client.post('/packaging/simulate',json={'lines':[{'product_id':pid,'quantity':2}]})
    assert result.status_code==200 and result.json['state']=='verified'
    assert len(result.json['parcels'][0]['items'])==2 and not result.json['checkout_integrated']
    assert source==original
    with app.app_context():
        assert PackingRecord.query.count()==1
        assert db.session.get(Product,pid).sku=='FICTIONAL-1'
    assert client.get('/packaging/products?q=FICTIONAL-1').json['products'][0]['product_id']==pid


def test_order_same_sku_lines_join_by_gids_not_name(packaging_app,monkeypatch):
    from test_shopify_orders import order,STORE,VARIANT
    from app import shopify_orders as imports,shopify_drafts
    from app.models import ShopifyOrder
    app,path=packaging_app
    monkeypatch.setattr('app.taxonomy_assignments.product_document',lambda p:{'requires_shipping':True,'packaging':{}})
    with app.app_context():
        p=Product(sku='FICTIONAL-1',title='Current renamed card',product_type='simple',catalogue_status='active')
        db.session.add(p);db.session.flush()
        data=order();data['_lines']=data['lineItems']['nodes'];header,lines=imports.normalise(data,api_version='2026-10')
        imports.persist(STORE,header,lines)
        db.session.add(ShopifyProductIdentity(product_id=p.id,source_identity='Fictional/Card',sku=p.sku,
            store_key=shopify_drafts.digest({'id':STORE}),store_id=STORE,store_domain='fictional.myshopify.com',
            shopify_product_id=data['_lines'][0]['product']['id'],state='verified',reviewed_digest='fixture',
            media_evidence_json=shopify_drafts.encode({'version':1,'variant_id':VARIANT})))
        db.session.commit()
        local,warnings=workspace.order_lines(ShopifyOrder.query.one())
        assert not warnings and len(local)==2 and local[0]['identity']!=local[1]['identity']
        assert all(v['quantity']==1 for v in local)


def test_packaging_fields_in_existing_editor_and_intake_templates(packaging_app):
    app,path=packaging_app
    for name in ('metadata_editor.html','image_preparation/metadata_edit.html'):
        text=app.jinja_loader.get_source(app.jinja_env,name)[0]
        assert 'packaging/_product.html' in text and 'packaging/_modal.html' in text
    assert 'data-order-packing' in app.jinja_loader.get_source(app.jinja_env,'orders/detail.html')[0]


def test_capability_refresh_denied_keeps_prior_evidence(packaging_app,monkeypatch):
    app,path=packaging_app;client=logged(app)
    monkeypatch.setattr(shopify_packaging,'refresh',lambda:(_ for _ in ()).throw(ShopifyError('permission unavailable')))
    response=client.post('/packaging/capabilities',follow_redirects=True)
    assert response.status_code==200 and b'No shipping settings changed' in response.data


def test_package_validation_rejects_nonfinite_excessive_interior_and_fractional_cost():
    for changes in ({'tare_g':'NaN'},{'cost_pence':'1.2'},{'internal_cm':{'length':'1000'}}):
        with pytest.raises(ValueError):catalogue.clean(dict(package(),**changes))
