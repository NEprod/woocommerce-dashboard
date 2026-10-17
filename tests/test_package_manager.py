"""Simplified local package manager and manually entered store-bound IDs."""
import copy
import json
import pytest
from app import db,package_links,packaging_catalogue as catalogue
from app.packaging_models import Package,PackageLink,PackagingEvidence
from test_packaging import packaging_app,logged,package
from test_setup_flow import setup_app


@pytest.fixture
def manager(packaging_app,monkeypatch):
    app,path=packaging_app
    monkeypatch.setenv('SHOPIFY_STORE_DOMAIN','fictional.myshopify.com')
    monkeypatch.setenv('SHOPIFY_ADMIN_ACCESS_TOKEN','fictional_package_token_00000000')
    with app.app_context():yield app,path


def test_six_records_unchanged_and_automatic_catalogue_actions_removed(manager):
    app,_=manager
    before={p.id:(p.name,p.specification) for p in Package.query.all()}
    client=logged(app);response=client.get('/packaging/')
    assert response.status_code==200 and b'Shipping Packages' in response.data
    assert b'Sync All' not in response.data and b'Reconcile with Shopify' not in response.data
    assert b'Add Package' in response.data and b'Shopify Package ID' in response.data
    assert client.get('/packaging/reconcile').status_code==404
    assert client.post('/packaging/sync').status_code==404
    assert {p.id:(p.name,p.specification) for p in Package.query.all()}==before
    c5=catalogue.view(Package.query.filter_by(seed_key='c5-plus').one())
    assert c5['max_contents_thickness_mm']=='3' and c5['external_cm']['height'] is None
    assert all(v is None for v in c5['internal_cm'].values())


@pytest.mark.parametrize('value',['123','TLC mailer','gid://shopify/Product/123','gid://shopify/ShippingPackage/0','https://example.test/123','gid://shopify/ShippingPackage/123?token=x','<script>'])
def test_invalid_ids_not_manufactured(manager,value):
    row=Package.query.first()
    with pytest.raises(ValueError):package_links.set_id(row,value,[])
    assert PackageLink.query.count()==0


@pytest.mark.parametrize('value',['gid://shopify/ShippingPackage/123','gid://shopify/DeliveryPackage/456'])
def test_manual_gid_saved_verbatim_not_verified(manager,value):
    row=Package.query.first();local_id=row.id
    result=package_links.set_id(row,value,[])
    assert result['id']==value and 'not remotely verified' in result['status']
    assert row.id==local_id and json.loads(PackageLink.query.one().observed)['api_verified'] is False
    assert package_links.current(row.id)['id']==value


def test_duplicate_and_ambiguous_identity_rejected(manager):
    first,second=Package.query.limit(2).all()
    package_links.set_id(first,'gid://shopify/ShippingPackage/123',[])
    with pytest.raises(ValueError,match='already linked'):package_links.set_id(second,'gid://shopify/ShippingPackage/123',[])
    assert PackageLink.query.count()==1


def test_rename_unknown_dimensions_and_id_are_independent(manager):
    app,_=manager;row=Package.query.filter_by(seed_key='c5-plus').one();ident=row.id
    package_links.set_id(row,'gid://shopify/ShippingPackage/123',[])
    value=catalogue.view(row);value['name']='Merchant renamed mailer'
    catalogue.save(value,row,version=row.version)
    assert row.id==ident and package_links.current(row.id)['id']=='gid://shopify/ShippingPackage/123'
    assert catalogue.view(row)['max_contents_thickness_mm']=='3'
    client=logged(app)
    assert client.get('/packaging/'+ident+'/edit').status_code==200
    assert b'Confirm unlink locally' in client.get('/packaging/'+ident+'/edit').data


def test_unlink_confirmed_keeps_package_and_history(manager):
    row=Package.query.first();ident=row.id
    package_links.set_id(row,'gid://shopify/ShippingPackage/123',[])
    with pytest.raises(ValueError,match='Confirm unlinking'):
        package_links.set_id(row,None,['gid://shopify/ShippingPackage/123'],unlink=True)
    package_links.set_id(row,None,['gid://shopify/ShippingPackage/123'],unlink=True,confirmed=True)
    assert package_links.current(ident)['id'] is None and db.session.get(Package,ident)
    assert PackageLink.query.count()==0 and PackagingEvidence.query.count()==1


def test_stale_link_edit_cannot_replace_newer_id(manager):
    row=Package.query.first()
    package_links.set_id(row,'gid://shopify/ShippingPackage/123',[])
    with pytest.raises(ValueError,match='changed'):package_links.set_id(row,'gid://shopify/ShippingPackage/124',[])
    assert package_links.current(row.id)['id']=='gid://shopify/ShippingPackage/123'


def test_idempotent_id_save_and_store_isolation(manager,monkeypatch):
    row=Package.query.first()
    package_links.set_id(row,'gid://shopify/ShippingPackage/123',[]);version=row.version
    package_links.set_id(row,'gid://shopify/ShippingPackage/123',['gid://shopify/ShippingPackage/123'])
    assert row.version==version and PackageLink.query.count()==1
    monkeypatch.setenv('SHOPIFY_STORE_DOMAIN','other-fictional.myshopify.com')
    assert package_links.current(row.id)['id'] is None


def test_route_add_edit_units_and_manual_id(manager):
    app,_=manager;client=logged(app)
    response=client.post('/packaging/new',json={'name':'Fictional extra box','type':'BOX',
        'external_cm':{'length':'6','width':'4'},'dimension_unit':'in','tare_g':'.04','weight_unit':'kg'})
    assert response.status_code==201;ident=response.json['id']
    value=catalogue.view(db.session.get(Package,ident))
    assert value['external_cm']=={'length':'15.24','width':'10.16','height':None} and value['tare_g']=='40.00'
    result=client.post('/packaging/'+ident+'/shopify-id',json={'id':'gid://shopify/ShippingPackage/123','expected':[]})
    assert result.status_code==200 and result.json['package_link']['id']=='gid://shopify/ShippingPackage/123'
    assert client.post('/packaging/'+ident+'/shopify-id',json={'unlink':True,'expected':['gid://shopify/ShippingPackage/123']}).status_code==400


def test_manual_route_retains_authentication_and_csrf(manager):
    app,_=manager;row=Package.query.first();client=logged(app)
    app.config['WTF_CSRF_ENABLED']=True
    assert client.post('/packaging/'+row.id+'/shopify-id',json={'id':'gid://shopify/ShippingPackage/123','expected':[]}).status_code==400
    assert PackageLink.query.count()==0
