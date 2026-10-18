"""Sparse card shipping corrections; synthetic sources and zero Shopify writes."""
import copy
import json
import re

import pytest

from app import db, package_links
from app.models import Product
from app.packaging_models import Package
from app.packaging_contract import effective, finished_mm, packed_cm
from app.shopify_native_dimensions import explicit_packed
from app.taxonomy_assignments import product_document
from app.utils.json_utils import merge_product_json, apply_variation_modifiers
from app.utils.scanner import scan_collection
from app.utils.ingest import ingest_rows_to_db
from test_metadata_editor_core import core, save
from test_packed_dimension_correction import sizes
from test_packaging import packaging_app, logged
from test_setup_flow import setup_app


def bootstrap(response):
    return json.loads(re.search(r'<script id="metadata-editor-bootstrap" type="application/json">(.*?)</script>',response.text,re.S)[1])


def rescan(core, owner):
    app,_,root,output,ids,*_=core
    with app.app_context():
        p=db.session.get(Product,ids[owner]); identity=(p.id,p.sku,[(v.id,v.sku) for v in p.variations])
        rows=scan_collection(root/owner.split('/')[0],'https://invalid.example/',output,force_update=True,update_csv=True,log=lambda *a,**k:None)
        assert ingest_rows_to_db(rows,log=lambda *a,**k:None)['products_failed']==0
        db.session.expire_all();p=db.session.get(Product,ids[owner])
        assert (p.id,p.sku,[(v.id,v.sku) for v in p.variations])==identity
        return product_document(p)


def test_card_override_155mm_package_save_reopen_scan_and_review_snapshot(core,monkeypatch):
    monkeypatch.setenv('SHOPIFY_STORE_DOMAIN','fictional.myshopify.com')
    monkeypatch.setenv('SHOPIFY_ADMIN_ACCESS_TOKEN','fictional_package_token_00000000')
    app,client,root,_,ids,*_=core
    owner='Simple Collection/Blue Token'
    parent=root/'Simple Collection/product_info.json'
    shared=json.loads(parent.read_text());shared['dimensions']={'length':155,'width':155,'height':3}
    parent.write_text(json.dumps(shared));before=parent.read_bytes()
    target=root/owner/'product_info.json';child=json.loads(target.read_text())
    with app.app_context():
        package=Package.query.filter_by(seed_key='c5-plus').one();package_id=package.id
        package_links.set_id(package,'gid://shopify/ShippingPackageV2/116237992319',[])
    response=save(core,owner,data={'packaging':{'selected_package_id':package_id,'assignment_kind':'manual'}})
    assert response.status_code==200 and response.json['json_saved'] and not response.json.get('unchanged')
    assert json.loads(target.read_text())=={**child,'packaging':{'selected_package_id':package_id,'assignment_kind':'manual'}}
    assert parent.read_bytes()==before
    page=client.get(f'/edit_products/{ids[owner]}/edit/override')
    assert bootstrap(page)['authored']['packaging']['selected_package_id']==package_id
    resolved=rescan(core,owner)
    assert effective(resolved)['selected_package_id']==package_id
    assert finished_mm(resolved)=={'length':'155','width':'155','height':'3'}
    assert packed_cm(effective(resolved))=={}  # display fallback was not authored
    from app.shopify_optional_packages import snapshot
    with app.app_context():
        p=db.session.get(Product,ids[owner])
        assert snapshot(p,{})['product']['selected_package_id']==package_id
        from app.shopify_optional_packages import attach
        from app.shopify_drafts import configuration
        plan={'managed':{'variant':{}},'inventory_item_id':'gid://shopify/InventoryItem/250',
              'classification':'no_change','taxonomy_review_digest':'synthetic','fields':[]}
        attach(p,plan,None,configuration(),{'shop_id':'gid://shopify/Shop/1','scopes':['write_inventory']})
        entry=plan['optional_packages'][0]
        assert entry['action']=='assign' and entry['inventory_id']=='gid://shopify/InventoryItem/250'
        assert entry['shipping_package_id']=='gid://shopify/ShippingPackageV2/116237992319'
        assert plan['classification']=='update'
    repeat=save(core,owner,data={'packaging':{'selected_package_id':package_id,'assignment_kind':'manual'}})
    assert repeat.status_code==200 and repeat.json['unchanged']
    assert parent.read_bytes()==before


@pytest.mark.parametrize('choice',['inherit','selected','none','clear'])
def test_package_and_packed_overrides_are_independent(core,choice):
    _,_,root,*_=core;owner='Simple Collection/Blue Token'
    parent=root/'Simple Collection/product_info.json';shared=json.loads(parent.read_text())
    with core[0].app_context():
        packages=Package.query.order_by(Package.id).limit(2).all();first,second=[p.id for p in packages]
    shared['packaging']={'packed_dimensions':{'length':16,'width':16,'height':.3,'unit':'cm'},'selected_package_id':first}
    parent.write_text(json.dumps(shared));before=parent.read_bytes()
    packing={'packed_dimensions':{'length':17,'width':16,'height':.3,'unit':'cm'}}
    if choice=='selected':packing['selected_package_id']=second
    if choice=='none':packing['selected_package_id']=None
    if choice=='clear':
        assert save(core,owner,data={'packaging':{'selected_package_id':second}}).status_code==200
        response=save(core,owner,data={'packaging':packing},removals=[['packaging','selected_package_id']])
    else:response=save(core,owner,data={'packaging':packing})
    assert response.status_code==200,response.json
    resolved=rescan(core,owner);p=effective(resolved)
    assert packed_cm(p)['length']=='17'
    assert p['selected_package_id']==(None if choice=='none' else second if choice=='selected' else first)
    assert parent.read_bytes()==before


@pytest.mark.parametrize('owner,kind',[('Simple Collection/Blue Token','override'),('Variable Collection/Badge One','override'),('Single Variable','shared')])
def test_all_layouts_shipping_roundtrip_preserves_other_fields(core,owner,kind):
    target=core[2]/(owner+'/product_info.json');before=json.loads(target.read_text())
    data={'packaging':{'packed_dimensions':{'length':155,'width':155,'height':3,'unit':'mm'},'selected_package_id':None}}
    assert save(core,owner,kind=kind,data=data).status_code==200
    assert json.loads(target.read_text())=={**before,**data}
    assert effective(rescan(core,owner))==data['packaging']


def test_shipping_only_modifier_overrides_preserve_parent_commerce_and_geometry():
    parent=sizes();before=copy.deepcopy(parent)
    override={'variation_modifiers':{'Size=A3':{'packaging':{'selected_package_id':None,'packed_dimensions':{'length':44,'width':31,'height':.5,'unit':'cm'}}}}}
    resolved=merge_product_json(parent,override)
    for size in ('A5','A4','A3'):
        attrs={'Size':size,'Colour':'Blue'}
        assert apply_variation_modifiers(resolved,attrs,log=lambda *a,**k:None)==apply_variation_modifiers(parent,attrs,log=lambda *a,**k:None)
    assert effective(resolved,{'Size':'A3'})['selected_package_id'] is None
    assert packed_cm(effective(resolved,{'Size':'A3'}))['length']=='44'
    assert parent==before
    # Historical commercial override objects still replace, rather than merge.
    legacy={'variation_modifiers':{'Size=A4':{'price':9}}}
    assert merge_product_json(parent,legacy)['variation_modifiers']==legacy['variation_modifiers']


def test_partial_packed_data_is_not_completed_from_finished_for_shopify():
    doc={'dimensions':{'length':155,'width':155,'height':3},'packaging':{'packed_dimensions':{'length':160,'unit':'mm'}}}
    assert explicit_packed(effective(doc))['desired'] is None
    assert explicit_packed({})['desired'] is None
    explicit={'packed_dimensions':{'length':160,'width':160,'height':4,'unit':'mm'}}
    assert explicit_packed(explicit)['desired']=={'length':16.,'width':16.,'height':.4,'unit':'CENTIMETERS'}


@pytest.mark.parametrize('commercial',[{}, {'Size=A3':{'price':'99','weight':'77'}}])
def test_existing_commercial_override_inherits_shipping_without_reintroducing_parent_commerce(commercial):
    parent=sizes();parent['variation_modifiers']['Size=A3']['packaging']={'selected_package_id':'parent-marker'}
    own={'variation_modifiers':copy.deepcopy(commercial)}
    resolved=merge_product_json(parent,own)
    for size in ('A3','A4'):
        attrs={'Size':size,'Colour':'Blue'}
        assert apply_variation_modifiers(resolved,attrs,log=lambda *a,**k:None)==apply_variation_modifiers({**parent,**own},attrs,log=lambda *a,**k:None)
    assert resolved['variation_modifiers']['Size=A3']['packaging']['selected_package_id']=='parent-marker'
    # This is a resolved projection only; authored commercial data stays sparse.
    assert own['variation_modifiers']==commercial


def test_legacy_empty_commercial_rule_remains_a_matching_barrier():
    parent=sizes()
    key='Colour=Blue|Size=A3'
    parent['variation_modifiers'][key]={'packaging':{'selected_package_id':None}}
    own={'variation_modifiers':{'Colour=Blue':{'price':'99'},key:{}}}
    resolved=merge_product_json(parent,own)
    assert resolved['variation_modifiers'][key]=={}
    assert apply_variation_modifiers(resolved,{'Size':'A3','Colour':'Blue'},log=lambda *a,**k:None)['price']=='3.50'


def test_commercial_dimension_override_does_not_adopt_parent_finished_alias():
    parent=sizes();parent['variation_modifiers']['Size=A3']['finished_dimensions']={'length':99,'width':80,'height':1,'unit':'cm'}
    own={'variation_modifiers':{'Size=A3':{'dimensions':{'length':400,'width':290,'height':2}}}}
    resolved=merge_product_json(parent,own)
    assert finished_mm(resolved,{'Size':'A3'})=={'length':'400','width':'290','height':'2'}


def test_recommendation_fallback_is_opt_in_estimated_and_never_persisted(packaging_app):
    app,_=packaging_app;client=logged(app)
    from app.packaging_catalogue import save as package_save
    with app.app_context():
        row=Package.query.filter_by(seed_key='c5-plus').one()
        package_save({'name':row.name,'internal_cm':{'length':'23','width':'16','height':'.3'}},row,version=row.version)
    document={'weight':'40','dimensions':{'length':155,'width':155,'height':3},'packaging':{'packed_dimensions':{'length':156,'unit':'mm'}}}
    before=copy.deepcopy(document)
    assert client.post('/packaging/recommend',json={'document':document}).json['state']=='insufficient_information'
    answer=client.post('/packaging/recommend',json={'document':document,'display_finished_fallback':True}).json
    assert answer['recommendation'] and answer['state']=='estimated'
    assert 'provisional' in answer['recommendation']['fit']
    assert document==before and explicit_packed(document['packaging'])['desired'] is None


def test_variant_shipping_only_override_scanner_roundtrip(core):
    owner='Variable Collection/Badge One';parent=core[2]/'Variable Collection/product_info.json'
    before=parent.read_bytes();shared=json.loads(before)
    expression=next(iter(shared['variation_modifiers']))
    data={'variation_modifiers':{expression:{'packaging':{'selected_package_id':None}}}}
    response=save(core,owner,data=data)
    assert response.status_code==200,response.json
    resolved=rescan(core,owner)
    assert parent.read_bytes()==before
    attrs=dict(part.split('=') for part in expression.split('|'))
    assert effective(resolved,attrs)['selected_package_id'] is None
    assert apply_variation_modifiers(resolved,attrs,log=lambda *a,**k:None)==apply_variation_modifiers(shared,attrs,log=lambda *a,**k:None)
