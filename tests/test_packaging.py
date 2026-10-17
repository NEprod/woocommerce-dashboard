"""Optional solo packaging and explicit dimensions; fictional data, offline only."""
import copy
from datetime import datetime
from decimal import Decimal
import json
import uuid
import pytest
import requests
from app import db
from app.models import User
from app.packaging_models import Package, PackageLink
from app import packaging_catalogue as catalogue, packing, shopify_packaging
from app.packaging_contract import effective, finished_mm
from app.database import ensure_database, migration_head
from app.shopify_connection import READ_QUERIES, WRITE_QUERIES, ShopifyError
from app.utils.json_utils import merge_product_json, apply_variation_modifiers
from test_setup_flow import setup_app


def package(key='card',dims=('18','18','2'),**changes):
    raw={'type':'BOX','external_cm':dict(zip(('length','width','height'),dims)),
        'internal_cm':dict(zip(('length','width','height'),dims)),
        'tare_g':'40','max_contents_g':'2000','max_gross_g':'2100','clearance_cm':'0','protection_weight_g':'0',
        'cost_pence':20,'dimensions_measured':True,'tare_measured':True,'automatic':True,'flat':True,
        'fragile':False,'protection':['rigid backing']}
    raw.update(changes)
    return dict(catalogue.clean(raw),id=key,name=key,version=1,archived=False)


def item(dims=('15.5','15.5','.3'),**changes):
    raw={'packed_dimensions_cm':dict(zip(('length','width','height'),dims)),
        'weight_g':'50','clearance_cm':'0','protection_weight_g':'0',
        'orientation':'flat','classification':'flat','method':'flat','fragile':False,'measured':True}
    raw.update(changes)
    return raw


@pytest.fixture
def packaging_app(setup_app,monkeypatch):
    app,path=setup_app
    monkeypatch.setattr(requests.Session,'request',lambda *a,**k:pytest.fail('Unexpected network'))
    with app.app_context():
        db.session.add(User(username='packing-admin',email='packing@example.test',password='synthetic',is_admin=True));db.session.commit()
    return app,path


def logged(app):
    client=app.test_client()
    with app.app_context(): ident=User.query.filter_by(username='packing-admin').one().id
    with client.session_transaction() as session: session['_user_id']=str(ident);session['_fresh']=True
    return client


def test_six_seeds_unknown_measurements_restart_and_no_cancelled_tables(packaging_app):
    from sqlalchemy import inspect
    app,path=packaging_app
    with app.app_context():
        rows=Package.query.all();assert len(rows)==6
        identities={p.id for p in rows}
        assert all(uuid.UUID(p.id) for p in rows)
        views=[catalogue.view(r) for r in rows]
        assert all(v['readiness']=='Measurements needed' and v['tare_g'] is None for v in views)
        mailer=next(v for v in views if v['type']=='ENVELOPE')
        assert mailer['external_cm']=={'length':'24','width':'16.5','height':None}
        assert mailer['max_contents_thickness_mm']=='3'
        tube=next(v for v in views if v['name']=='TLC A3 Poster Tube')
        assert all(v is None for v in tube['external_cm'].values()) and tube['external_diameter_cm'] is None
        assert not {'packaging_rates','packaging_plan'} & set(inspect(db.engine).get_table_names())
    ensure_database('sqlite:///'+str(path))
    with app.app_context(): assert {p.id for p in Package.query.all()}==identities
    assert migration_head()=='0018_orders_webhooks'


@pytest.mark.parametrize('height,eligible',[('.2',True),('.3',True),('.31',False),('.4',False),(None,False)])
def test_c5_three_mm_rule_boundaries(height,eligible):
    p=package(type='ENVELOPE',max_contents_thickness_mm='3')
    result=packing.recommend(item(dims=('15.5','15.5',height)),[p])
    assert bool(result['recommendation']) is eligible
    if height in ('.31','.4'): assert '3 mm' in result['rejected_packages'][0]['reason']


def test_exact_three_mm_still_needs_verified_interior_and_protection():
    assert packing.recommend(item(),[package(internal_cm={})])['recommendation'] is None
    assert packing.recommend(item(clearance_cm='.1'),[package(dims=('15.5','15.5','.3'))])['recommendation'] is None


def test_large_letter_box_is_not_restricted_to_three_mm():
    p=package('Large Letter',('35.3','25','2.5'))
    assert packing.recommend(item(dims=('15','15','1')),[p])['state']=='verified'


@pytest.mark.parametrize('dims,expected',[ (('29.7','21','.3'),'c4'),(('42','29.7','.3'),'c3')])
def test_flat_prints_no_rolling_or_default_fallback(dims,expected):
    packages=[package(),package('c4',('32','22','2')),package('c3',('45','32','2'))]
    assert packing.recommend(item(dims=dims),packages)['recommendation']['package_id']==expected
    assert packing.recommend(item(dims=dims),[package()])['recommendation'] is None


def tube(**changes):
    values=dict(type='TUBE',internal_cm={'length':'23'},external_diameter_cm='5',internal_diameter_cm='4',end_cap_allowance_cm='1',rolled=True,flat=False)
    return package('tube',('24',None,None),**dict(values,**changes))


@pytest.mark.parametrize('dims,eligible',[(('21','3','3'),True),(('22','4','4'),True),(('22.1','3','3'),False),(('21','4.1','4.1'),False)])
def test_tube_uses_roll_axis_diameter_and_end_cap_reserve(dims,eligible):
    result=packing.recommend(item(dims=dims,method='rolled',rollable=True),[tube()])
    assert bool(result['recommendation']) is eligible


def test_tube_cannot_roll_a_flat_print_or_assume_unknown_sizes():
    assert packing.recommend(item(),[tube()])['recommendation'] is None
    assert packing.recommend(item(method='rolled',rollable=False),[tube()])['state']=='insufficient_information'
    assert packing.recommend(item(method='rolled',rollable=True),[tube(internal_diameter_cm=None)])['recommendation'] is None


@pytest.mark.parametrize('change',[{'fragile':True},{'weight_g':'3000'},{'packed_dimensions_cm':{}},{'measured':False},
    {'clearance_cm':None},{'method':None},{'orientation':None},{'requirements':['bubble wrap']}])
def test_incomplete_or_unsafe_item_has_no_verified_fit(change):
    assert packing.recommend(item(**change),[package(type='ENVELOPE')])['recommendation'] is None


def test_solo_decimal_weight_alternatives_and_determinism():
    source=item(weight_g='10.25',protection_weight_g='.125');original=copy.deepcopy(source)
    packages=[package('B'),package('A',tare_g='40.1',protection_weight_g='2.2'),package('C')]
    result=packing.recommend(source,packages)
    assert result['recommendation']['name']=='A' and Decimal(result['recommendation']['gross_weight_g'])==Decimal('52.675')
    assert len(result['alternatives'])==2 and result==packing.recommend(source,packages)
    assert source==original and 'selected_package_id' not in source


def test_explicit_unassigned_overrides_parent_and_sparse_false_survives():
    uid=str(uuid.uuid4());common={'packaging':{'fragile':True,'selected_package_id':uid,'packed_dimensions_cm':{'length':'20','width':'15','height':'1'}}}
    child={'packaging':{'fragile':False,'selected_package_id':None}}
    result=effective(merge_product_json(common,child))
    assert result['selected_package_id'] is None and not result['fragile']
    assert result['packed_dimensions_cm']['length']=='20'
    assert effective(merge_product_json(common,{}))['selected_package_id']==uid
    assert effective({})=={}


@pytest.mark.parametrize('key',['packaging','finished_dimensions'])
def test_metadata_only_exact_modifier_does_not_mask_legacy_price(key):
    document={'price':'3.50','weight':'10','variation_modifiers':{'Size=A3':{'price':'8.50','weight':'150'},'Colour=Blue|Size=A3':{key:{}}}}
    assert apply_variation_modifiers(document,{'Size':'A3','Colour':'Blue'},log=lambda *a,**k:None)['price']=='8.50'
    document['variation_modifiers']['Colour=Blue|Size=A3']={}
    assert apply_variation_modifiers(document,{'Size':'A3','Colour':'Blue'},log=lambda *a,**k:None)['price']=='3.50'


@pytest.mark.parametrize('unit,dims,expected',[('cm',('29.7','21','.3'),('297.0','210','3.0')),('cm',('42','29.7','.3'),('420','297.0','3.0')),('in',('6','6','.1'),('152.4','152.4','2.54'))])
def test_finished_units_are_exact_and_independent_of_packed(unit,dims,expected):
    doc={'finished_dimensions':dict(zip(('length','width','height'),dims),unit=unit),'packaging':item()}
    assert tuple(finished_mm(doc).values())==expected
    assert effective(doc)['packed_dimensions_cm']['length']=='15.5'


def test_finished_whole_object_inheritance_and_exact_variant():
    common={'finished_dimensions':{'length':'42','width':'29.7','unit':'cm'},'variation_modifiers':{'Size=A4':{'finished_dimensions':{'length':'29.7','width':'21','unit':'cm'}}}}
    resolved=merge_product_json(common,{'finished_dimensions':{'length':'6','width':'6','unit':'in'}})
    assert finished_mm(resolved)['length']=='152.4'
    assert finished_mm(resolved,{'Size':'A4'})['length']=='297.0'


@pytest.mark.parametrize('layout',['Simple','Variable Collection','Single Variable'])
def test_schema_roundtrip_no_required_package_and_invalid_sizes(layout):
    from app.product_info import validate_product_info
    data={'collection_type':layout,'sku_prefix':'SYN','price':'3.50','finished_dimensions':{'length':'6','width':'6','unit':'in'},'packaging':{'measured':False,'selected_package_id':None}}
    assert validate_product_info(data,'collection').valid
    data['packaging']['packed_dimensions_cm']={'length':'0'}
    assert not validate_product_info(data,'collection').valid


def test_native_explicit_packed_size_only_2027_and_finished_never_fallback(monkeypatch):
    from app import shopify_native_dimensions as native,shopify_variables as variables
    monkeypatch.setenv('SHOPIFY_NATIVE_DIMENSIONS_ENABLED','true');monkeypatch.setenv('SHOPIFY_PACKED_DIMENSIONS_USE_PRODUCT_DIMENSIONS','true')
    p=type('Parent',(),{'length':155,'width':155,'height':3})()
    authored={'finished_dimensions':{'length':'42','width':'29.7','height':'.1','unit':'cm'}}
    monkeypatch.setattr(native,'product_document',lambda _:authored)
    assert native.desired(p)['desired']['length']==15.5
    authored['packaging']={'packed_dimensions_cm':{'length':'21','width':'3','height':'3'},'measured':True}
    assert native.desired(p)['desired']['length']==21
    assert variables.packed_policy({},authored['packaging'])['desired']['length']==21
    authored['packaging']['measured']=False
    assert native.desired(p)['desired'] is None
    monkeypatch.setenv('SHOPIFY_NATIVE_DIMENSIONS_ENABLED','false')
    assert native.desired(p)['desired'] is None and native.VERSION=='2027-01'


def test_native_precision_and_invalid_finished_values():
    from app.shopify_native_dimensions import explicit_packed
    assert explicit_packed({'measured':True,'packed_dimensions_cm':{'length':'0.000000001','width':'1','height':'1'}})['desired'] is None
    for value in (False,None,[],{'length':'-1','unit':'cm'}):
        with pytest.raises(ValueError): finished_mm({'finished_dimensions':value})


def test_package_links_survive_rename_and_restart(packaging_app):
    app,path=packaging_app
    with app.app_context():
        row=catalogue.save(dict(package(),name='Old linked package'))
        ident=row.id
        db.session.add(PackageLink(package_id=ident,store_id='gid://shopify/Shop/42',remote_id='gid://shopify/ShippingPackage/7',observed='{}',checked_at=datetime.utcnow()));db.session.commit()
        catalogue.save(dict(package(),name='Renamed'),row,version=1)
        assert PackageLink.query.one().package_id==ident
    ensure_database('sqlite:///'+str(path))
    with app.app_context(): assert PackageLink.query.one().remote_id=='gid://shopify/ShippingPackage/7'


def test_write_only_capability_is_not_verified_sync():
    raw={'shop':{'id':'gid://shopify/Shop/42'},'queryRoot':{'fields':[]},'mutations':{'fields':[{'name':'shippingPackageUpdate'}]},'measurement':{'fields':[{'name':'weight'}]},'measurementInput':{'inputFields':[{'name':'shippingPackageId'}]},'package':None}
    result=shopify_packaging.assess(raw,['read_products','write_products','write_inventory'])
    assert result['assignment_write_schema'] and not result['assignment_read_schema']
    assert result['package_update_schema'] and not result['package_sync_enabled']
    assert shopify_packaging.CAPABILITY in READ_QUERIES and shopify_packaging.CAPABILITY not in WRITE_QUERIES
    assert not any('shippingPackage' in q or 'carrierServiceCreate' in q for q in WRITE_QUERIES)


def test_admin_crud_csrf_versioning_and_cancelled_routes_absent(packaging_app):
    app,path=packaging_app;client=logged(app)
    for page in ('/packaging/','/packaging/new'):
        response=client.get(page);assert response.status_code==200 and response.headers['Cache-Control']=='no-store, private'
    data=package();data['name']='Fictional measured package'
    created=client.post('/packaging/new',json=data);assert created.status_code==201
    ident=created.json['id'];data['version']=1
    assert client.get(f'/packaging/{ident}/edit').status_code==200
    assert client.post(f'/packaging/{ident}/edit',json=data).status_code==200
    assert client.post(f'/packaging/{ident}/edit',json=data).status_code==400
    assert client.post(f'/packaging/{ident}/duplicate',json={}).status_code==200
    assert client.post(f'/packaging/{ident}/archive',json={'version':2,'confirm':False}).status_code==400
    assert client.post(f'/packaging/{ident}/archive',json={'version':2,'confirm':True}).json['archived']
    assert client.get(f'/packaging/{ident}/usage').status_code==200
    for page in ('/packaging/simulator','/packaging/rates','/packaging/simulate','/packaging/orders/1/suggest'):
        assert client.get(page).status_code==404
    app.config['WTF_CSRF_ENABLED']=True
    assert client.post('/packaging/new',json=data).status_code==400


def test_recommend_is_advisory_and_never_saves_draft(packaging_app):
    app,path=packaging_app;client=logged(app)
    with app.app_context(): catalogue.save(dict(package(),name='Measured fixture'))
    document={'packaging':item()};original=copy.deepcopy(document)
    response=client.post('/packaging/recommend',json={'document':document})
    assert response.status_code==200 and response.json['recommendation']
    assert document==original
    with app.app_context(): assert Package.query.count()==7


def test_auth_and_permissions_unavailable(packaging_app,monkeypatch):
    app,path=packaging_app
    assert app.test_client().get('/packaging/').status_code in (302,401)
    client=logged(app)
    monkeypatch.setattr(shopify_packaging,'refresh',lambda:(_ for _ in ()).throw(ShopifyError('permission unavailable')))
    assert b'No shipping settings changed' in client.post('/packaging/capabilities',follow_redirects=True).data
    with app.app_context(): u=User.query.filter_by(username='packing-admin').one();u.is_admin=False;db.session.commit()
    assert client.get('/packaging/choices').status_code==403


def test_editor_components_and_orders_unchanged(packaging_app):
    app,path=packaging_app
    for name in ('metadata_editor.html','image_preparation/metadata_edit.html'):
        text=app.jinja_loader.get_source(app.jinja_env,name)[0]
        assert 'packaging/_product.html' in text and 'packaging/_modal.html' in text
    assert 'data-order-packing' not in app.jinja_loader.get_source(app.jinja_env,'orders/detail.html')[0]


@pytest.mark.parametrize('changes',[{'tare_g':'NaN'},{'cost_pence':'1.2'},{'internal_cm':{'length':'1000'}},{'external_diameter_cm':'2','internal_diameter_cm':'3'}])
def test_invalid_values(changes):
    with pytest.raises(ValueError): catalogue.clean(dict(package(),**changes))
