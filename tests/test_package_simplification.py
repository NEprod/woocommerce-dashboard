"""Local package repair: synthetic store, no Shopify writes or rate calculations."""
import json

import pytest

from app import db, package_links, packaging_catalogue as catalogue, packing
from app.packaging_models import Package, PackageLink
from test_packaging import packaging_app, logged, package, item
from test_package_manager import manager
from test_setup_flow import setup_app
from test_shopify_drafts import world
from test_phase3_woo_publish_preview import preview_app

V2 = 'gid://shopify/ShippingPackageV2/116237992319'


def test_genuine_v2_id_saves_and_reopens_verbatim(manager):
    app, _ = manager
    row = Package.query.filter_by(seed_key='c5-plus').one()
    ident = row.id
    client = logged(app)
    result = client.post(f'/packaging/{ident}/shopify-id', json={'id': V2, 'expected': []})
    assert result.status_code == 200
    assert result.json['package_link']['id'] == V2
    db.session.remove()
    from flask import g
    g.pop('_login_user', None)  # fresh request after the synthetic restart
    assert package_links.current(ident)['id'] == V2
    page = client.get(f'/packaging/{ident}/edit')
    assert V2.encode() in page.data and b'not remotely verified' in page.data
    assert PackageLink.query.one().remote_id == V2


def test_main_save_includes_manual_id_without_changing_package_identity(manager):
    app, _ = manager
    row = Package.query.first()
    ident = row.id
    result = logged(app).post(f'/packaging/{ident}/edit', json={
        'name': 'Merchant renamed package', 'version': row.version,
        'shopify_link': {'id': V2, 'expected': []}})
    assert result.status_code == 200
    assert package_links.current(ident)['id'] == V2
    assert db.session.get(Package, ident).name == 'Merchant renamed package'


def test_external_fallback_is_provisional_not_verified():
    result = packing.recommend(item(), [package(internal_cm={})])
    assert result['recommendation'] is not None
    assert result['state'] == 'estimated' and result['recommendation']['fit'] == 'Estimated Fit (unverified)'


@pytest.mark.parametrize('value', [V2+'?x=1', V2.replace('V2','V0'), V2.replace('/116237992319','/0'), '116237992319'])
def test_invalid_v2_formats_do_not_convert_or_persist(manager, value):
    row = Package.query.first()
    with pytest.raises(ValueError): package_links.set_id(row, value, [])
    assert PackageLink.query.count() == 0


def test_duplicate_v2_rename_and_confirmed_unlink(manager):
    first, second = Package.query.limit(2).all()
    package_links.set_id(first, V2, [])
    with pytest.raises(ValueError, match='already linked'): package_links.set_id(second, V2, [])
    catalogue.save({'name': 'Unique renamed package'}, first, version=first.version)
    assert package_links.current(first.id)['id'] == V2
    with pytest.raises(ValueError, match='Confirm unlinking'): package_links.set_id(first, None, [V2], unlink=True)
    package_links.set_id(first, None, [V2], unlink=True, confirmed=True)
    assert package_links.current(first.id)['id'] is None


@pytest.mark.parametrize('bad', ['not-a-gid', 'duplicate', 'blank', 'stale'])
def test_atomic_save_preserves_measurements_and_link_on_error(manager, bad):
    app, _ = manager
    first, second = Package.query.limit(2).all()
    package_links.set_id(first, V2, [])
    if bad == 'duplicate':
        first = second
    before = (first.version, first.name, first.specification)
    target = V2 if bad in {'duplicate','stale'} else '' if bad == 'blank' else 'not-a-gid'
    expected = [] if bad in {'duplicate','stale'} else [V2]
    response = logged(app).post(f'/packaging/{first.id}/edit', json={
        'name': 'Should not persist', 'version': first.version,
        'external_cm': {'length':'99'}, 'shopify_link': {'id': target, 'expected': expected}})
    assert response.status_code == 400
    db.session.expire_all()
    assert (first.version, first.name, first.specification) == before
    assert PackageLink.query.one().remote_id == V2


def test_new_package_id_purchase_data_units_and_hidden_values(manager):
    app, _ = manager
    client = logged(app)
    response = client.post('/packaging/new', json={'name': 'Synthetic supplier box', 'type':'BOX',
        'external_cm': {'length':'6','width':'4','height':'1'}, 'dimension_unit':'in',
        'internal_cm': {}, 'tare_g':'.04', 'weight_unit':'kg',
        'purchase_price':'0.42',
        'purchase_url':'https://supplier.example.test/box', 'shopify_link':{'id':V2,'expected':[]}})
    assert response.status_code == 201
    ident = response.json['id']
    row = db.session.get(Package, ident)
    spec = catalogue.view(row)
    assert not {'flat','fragile','rolled','max_contents_g','clearance_cm','dimensions_measured'} & set(spec)
    assert spec['external_cm']=={'length':'15.24','width':'10.16','height':'2.54'}
    assert spec['tare_g']=='40.00' and spec['purchase_price']=='0.42'
    assert all(v is None for v in spec['internal_cm'].values())
    assert V2 not in row.specification and package_links.current(ident)['id']==V2
    raw=json.loads(row.specification)
    raw.update(clearance_cm='0.2',protection_weight_g='3',flat=True,legacy_local_note='Keep historical data')
    row.specification=json.dumps(raw); db.session.commit()
    response=client.post(f'/packaging/{ident}/edit',json={'name':'Synthetic revised box','version':row.version})
    assert response.status_code==200
    saved=json.loads(row.specification)
    assert all(saved[k]==raw[k] for k in raw)
    assert package_links.current(ident)['id']==V2
    assert client.post('/packaging/new', json={'name':'  SYNTHETIC revised  box '}).status_code==400


@pytest.mark.parametrize('metadata', [{'purchase_price':'NaN'}, {'purchase_price':'.001'},
    {'purchase_url':'javascript:alert(1)'}, {'purchase_url':'https://user:password@example.test/'}])
def test_invalid_purchase_metadata_is_local_validation_only(metadata):
    with pytest.raises(ValueError): catalogue.clean(metadata)


def test_internal_axes_win_over_external_and_unknown_is_not_zero():
    p=package(internal_cm={'length':'14','width':'18','height':'2'})
    assert packing.recommend(item(),[p])['recommendation'] is None
    p=package(internal_cm={'length':'14'})
    assert packing.recommend(item(),[p])['recommendation'] is None
    p=package(external_cm={'length':'18','width':'18'},internal_cm={})
    assert packing.recommend(item(),[p])['recommendation'] is None


def test_partial_internal_axes_use_external_only_for_missing_axes():
    result=packing.recommend(item(),[package(internal_cm={'length':'16'})])
    assert result['state']=='estimated'
    assert any('width unknown' in w for w in result['warnings'])
    assert not any('length unknown' in w for w in result['warnings'])


def test_basic_recommendation_needs_only_packed_size_not_old_approvals():
    result=packing.recommend({'packed_dimensions_cm':{'length':'15','width':'15','height':'.3'}},[package()])
    assert result['state']=='size_checked' and result['recommendation']['gross_weight_g'] is None


def test_incomplete_package_can_still_be_selected_and_saved(manager):
    from app.packaging_contract import effective
    row=Package.query.filter_by(seed_key='a3-tube').one()
    assert catalogue.view(row)['readiness']=='Measurements needed'
    assert effective({'packaging':{'selected_package_id':row.id}})['selected_package_id']==row.id
    response=logged(manager[0]).get('/packaging/choices')
    assert response.status_code==200 and row.id in [p['id'] for p in response.json['packages']]


def test_retired_approvals_capacities_and_protection_do_not_gate_size_check():
    p=package(internal_cm={},tare_g=None,protection_weight_g=None,max_contents_g=None)
    result=packing.recommend(item(),[p])
    assert result['state']=='estimated' and result['recommendation']['gross_weight_g'] is None
    p=package(flat=False,fragile=False,rolled=False,automatic=False,dimensions_measured=False,tare_measured=False,
        max_contents_g='1',max_gross_g='1',clearance_cm='100',protection_weight_g='100',protection=[])
    result=packing.recommend(item(weight_g='3000',fragile=True,measured=False,requirements=['missing protection']),[p])
    assert result['recommendation'] and result['state']=='size_checked'
    assert result['recommendation']['gross_weight_g']=='3040'


@pytest.mark.parametrize('height,eligible', [('.2', True),('.3', True),('.31',False),(None,False)])
def test_c5_limit_in_estimated_recommendations(height, eligible):
    p=package(type='ENVELOPE',external_cm={'length':'24','width':'16.5'},internal_cm={},max_contents_thickness_mm='3')
    result=packing.recommend(item(dims=('15.5','15.5',height)),[p])
    assert bool(result['recommendation']) is eligible
    if eligible: assert result['state']=='estimated'


def test_internal_size_recommendation_precedes_estimates_and_never_reassigns():
    source=item(selected_package_id='cf513dd4-d976-47dd-9f15-96fe6eb320ef')
    original=json.dumps(source,sort_keys=True)
    result=packing.recommend(source,[package('small-estimate',internal_cm={}),package('measured',dims=('20','20','2'))])
    assert result['recommendation']['package_id']=='measured' and result['state']=='size_checked'
    assert result['alternatives'][0]['fit']=='Estimated Fit (unverified)'
    assert json.dumps(source,sort_keys=True)==original


def test_v2_transport_preserves_id_and_sends_no_unrelated_fields(monkeypatch):
    from app.shopify_connection import ShopifyAdapter, ShopifyConfiguration, PACKAGE_ASSIGN
    adapter=ShopifyAdapter(); calls=[]
    monkeypatch.setattr(adapter,'_transport',lambda config,query,variables: calls.append((query,variables)))
    adapter.assign_package(ShopifyConfiguration(domain='fictional.myshopify.com',token='synthetic_only_0000000000000'),
        'gid://shopify/InventoryItem/123',V2)
    assert calls==[(PACKAGE_ASSIGN,{'id':'gid://shopify/InventoryItem/123','input':{'measurement':{'shippingPackageId':V2}}})]


def test_purchasing_metadata_does_not_create_product_shopify_changes(world):
    from app import shopify_drafts as drafts
    from test_shopify_package_assignments import existing, linked
    from test_shopify_simple_updates import change
    product, identity, adapter=existing(world)
    pkg=linked()
    change(world,packaging={'selected_package_id':pkg.id})
    _,token=drafts.preview(product,adapter)
    drafts.confirm(token,True,adapter)  # fictional adapter only, no network
    assert drafts.preview(product,adapter)[0]['classification']=='no_change'
    before=drafts.evidence(identity)
    adapter.calls.clear()
    catalogue.save({'name':pkg.name,'purchase_price':'0.42','purchase_url':'https://supplier.example.test'},pkg,version=pkg.version)
    plan,token=drafts.preview(product,adapter)
    assert plan['classification']=='no_change' and token is None
    assert before==drafts.evidence(identity)
    assert not any(isinstance(c,tuple) for c in adapter.calls)


def test_tube_external_fallback_is_estimated_and_ignores_retired_end_cap_setting():
    from test_packaging import tube
    p=tube(internal_cm={},internal_diameter_cm=None,end_cap_allowance_cm='2')
    result=packing.recommend(item(dims=('21','3','3'),method='rolled',rollable=True),[p])
    assert result['state']=='estimated' and result['recommendation']['fit']=='Estimated Fit (unverified)'
    assert packing.recommend(item(dims=('24','3','3'),method='rolled'),[p])['recommendation']
    assert packing.recommend(item(dims=('24.1','3','3'),method='rolled'),[p])['recommendation'] is None


def test_simplified_form_has_only_internal_dimensions_and_thickness_in_advanced(manager):
    app,_=manager
    page=logged(app).get('/packaging/'+Package.query.first().id+'/edit')
    assert page.status_code==200
    text=page.get_data(as_text=True)
    assert 'Advanced · optional internal usable dimensions' in text and '<details open' not in text
    assert 'Save locally also saves this field' in text and 'ShippingPackageV2' in text
    for name in ('purchase_price','purchase_url','internal_cm.length','max_contents_thickness_mm'):
        assert 'name="'+name+'"' in text
    for name in ('flat','fragile','rolled','end_cap_allowance_cm','clearance_cm','max_contents_g','max_gross_g',
        'protection_weight_g','dimensions_measured','tare_measured','automatic','protection'):
        assert 'name="'+name+'"' not in text
    assert 'type="file"' not in text and 'Sync All' not in text
