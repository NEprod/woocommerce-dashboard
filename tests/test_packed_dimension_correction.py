"""M3A.9.5.6: original finished dimensions are never a shipping fallback."""
import copy
import json
from decimal import Decimal
import pytest
from app import shopify_drafts as drafts, shopify_native_dimensions as native, shopify_variables as variables
from app.packaging_contract import effective, finished_mm, packed_cm, dimension_configurations
from app.utils.json_utils import apply_variation_modifiers, merge_product_json
from app.product_info import validate_product_info
from app.shopify_connection import ShopifyError
from test_shopify_drafts import world
from test_shopify_simple_updates import change
from test_shopify_native_dimensions import NativeAdapter
from test_shopify_variables import variable_world, rescan, create
from test_phase3_woo_publish_preview import preview_app
from test_packaging import packaging_app, logged, package, item
from test_setup_flow import setup_app
from test_metadata_editor_core import core, save


def sizes():
    return {'collection_type':'Single Variable','price':'3.50','weight':'50',
        'dimensions':{'length':210,'width':148,'height':1},
        'attributes':{'Size':['A5','A4','A3'],'Colour':['Blue','Red']},'variation_attributes':['Size','Colour'],
        'variation_modifiers':{'Size=A4':{'price':'5.50','weight':'100','dimensions':{'length':297,'width':210,'height':1}},
            'Size=A3':{'price':'8.50','weight':'150','dimensions':{'length':420,'width':297,'height':1}}}}


def test_existing_finished_fields_and_price_weight_contract_unchanged():
    source=sizes();before=copy.deepcopy(source)
    for size,price,weight,length in [('A5','3.50','50','210'),('A4','5.50','100','297'),('A3','8.50','150','420')]:
        attrs={'Size':size,'Colour':'Blue'}
        result=apply_variation_modifiers(source,attrs,log=lambda *a,**k:None)
        assert result['price']==price and result['weight']==weight
        assert Decimal(finished_mm(source,attrs)['length'])==Decimal(length)
        source.setdefault('variation_modifiers',{})['Colour=Blue|Size='+size]={'packaging':{'fragile':True}}
        result2=apply_variation_modifiers(source,attrs,log=lambda *a,**k:None)
        assert result2==result
    assert source['dimensions']==before['dimensions']


def test_three_geometry_configurations_not_six_colour_variants():
    doc=sizes();before=copy.deepcopy(doc)
    groups=dimension_configurations(doc)
    assert len(groups)==3 and {g['key'] for g in groups}=={'Size=A5','Size=A4','Size=A3'}
    assert all(len(g['selections'])==2 and g['copy']['unit']=='mm' for g in groups)
    assert doc==before


def test_single_configuration_without_invented_variants():
    assert dimension_configurations({'dimensions':{'length':155,'width':155,'height':3}})[0]['key']==''
    with pytest.raises(ValueError,match='Complete positive'):
        dimension_configurations({'dimensions':{'length':155,'width':155}})
    simple=sizes();simple['collection_type']='Simple';simple['attributes']={'Recipient':[str(n) for n in range(300)]}
    assert len(dimension_configurations(simple))==1


def test_crossed_geometry_groups_never_become_overlapping_broad_rules():
    doc=sizes();doc['attributes']['Size']=['A4','A3']
    doc['variation_modifiers']['Colour=Blue|Size=A4']={'dimensions':{'length':420,'width':297,'height':1}}
    doc['variation_modifiers']['Colour=Blue|Size=A3']={'dimensions':{'length':297,'width':210,'height':1}}
    configurations=dimension_configurations(doc)
    assert len(configurations)==4 and len({c['key'] for c in configurations})==4
    assert all(c['key'] and len(c['attributes'])==2 for c in configurations)


def test_original_dimension_modifier_beats_parent_explicit_unit_alias():
    doc=sizes();doc['finished_dimensions']={'length':'6','width':'6','height':'.1','unit':'in'}
    assert finished_mm(doc,{'Size':'A3','Colour':'Blue'})['length']=='420'
    assert finished_mm(doc,{'Size':'A5','Colour':'Blue'})['length']=='152.4'


def test_bounded_copy_uses_existing_generator():
    doc=sizes();doc['attributes']['Colour']=[str(n) for n in range(100)]
    with pytest.raises(ValueError,match='256'): dimension_configurations(doc)


@pytest.mark.parametrize('unit,values,expected',[('mm',('160','110','4'),('16','11','.4')),('cm',('16','11','.4'),('16','11','.4')),('in',('6','4','.1'),('15.24','10.16','.254'))])
def test_shipping_units_exact_and_separate(unit,values,expected):
    packing={'packed_dimensions':dict(zip(('length','width','height'),values),unit=unit),'measured':True}
    assert tuple(Decimal(v) for v in packed_cm(packing).values())==tuple(Decimal(v) for v in expected)
    assert native.explicit_packed(packing)['desired']['unit']=='CENTIMETERS'
    doc={'dimensions':{'length':420,'width':297,'height':1},'packaging':packing}
    assert Decimal(finished_mm(doc)['length'])==420


def test_sparse_shipping_rules_and_variant_exception_do_not_mask_prices():
    doc=sizes();doc['variation_modifiers']['Size=A3']['packaging']={'packed_dimensions':{'length':'44','width':'31','height':'.5','unit':'cm'},'measured':True}
    doc['variation_modifiers']['Colour=Blue|Size=A3']={'price':'9.00','packaging':{'fragile':False,'selected_package_id':None}}
    selected=effective(doc,{'Size':'A3','Colour':'Blue'})
    assert selected['packed_dimensions']['length']=='44' and selected['measured']
    assert selected['fragile'] is False and selected['selected_package_id'] is None
    assert apply_variation_modifiers(doc,{'Size':'A3','Colour':'Blue'},log=lambda *a,**k:None)['price']=='9.00'
    assert effective(doc,{'Size':'A3','Colour':'Red'})['packed_dimensions']==selected['packed_dimensions']


def test_inherited_unit_objects_do_not_mix_axes_or_units():
    common={'packaging':{'packed_dimensions_cm':{'length':'16','width':'11','height':'.4'},'fragile':True}}
    child={'packaging':{'packed_dimensions':{'length':'6','width':'4','height':'.1','unit':'in'},'fragile':False,'selected_package_id':None}}
    value=effective(merge_product_json(common,child))
    assert 'packed_dimensions_cm' not in value and packed_cm(value)['length']=='15.24'
    assert value['fragile'] is False and value['selected_package_id'] is None
    assert 'packed_dimensions' not in effective(merge_product_json(child,common))


@pytest.mark.parametrize('flag',['true','false'])
def test_retired_reuse_flag_cannot_publish_finished_geometry(world,monkeypatch,flag):
    monkeypatch.setenv('SHOPIFY_NATIVE_DIMENSIONS_ENABLED','true');monkeypatch.setenv('SHOPIFY_PACKED_DIMENSIONS_USE_PRODUCT_DIMENSIONS',flag)
    change(world,dimensions={'length':420,'width':297,'height':1})
    assert native.desired(world[0])['desired'] is None
    assert variables.packed_policy({'length':420,'width':297,'height':1})['desired'] is None
    adapter=NativeAdapter();plan,token=drafts.preview(world[0],adapter)
    assert plan['native']['unconfigured'] and token
    row=drafts.confirm(token,True,adapter)
    assert row.state=='verified' and not adapter.writes
    assert json.loads(adapter.remote['length']['value'])['value']==420


def test_history_requires_receipt_not_equality_alone():
    remote={'length':15.5,'width':15.5,'height':.3,'unit':'CENTIMETERS'}
    size={'length':'155','width':'155','height':'3'}
    assert native.historical_finding(remote,None,size)=='Insufficient evidence'
    old={'state':'verified','observed':remote,'plan':{'use_product_dimensions':True,'desired':remote}}
    assert native.historical_finding(remote,None,size,old)=='Likely previously mapped from finished dimensions'
    assert native.historical_finding(remote,remote,size,old)=='Correct and verified'
    assert native.historical_finding(None,None,size)=='Insufficient evidence'
    assert native.historical_finding(dict(remote,height=0,length=0,width=0),None,size)=='Missing remote packed dimensions'


def test_normal_signed_correction_preserves_identity_and_finished_metafields(world,monkeypatch):
    adapter=NativeAdapter();change(world,dimensions={'length':420,'width':297,'height':1})
    _,token=drafts.preview(world[0],adapter);row=drafts.confirm(token,True,adapter)
    retained=row.shopify_product_id;before=copy.deepcopy(adapter.remote)
    adapter.packed={'length':42,'width':29.7,'height':.1,'unit':'CENTIMETERS'}
    native.persist(row,{'state':'verified','observed':adapter.packed,'plan':{'use_product_dimensions':True,'desired':adapter.packed},'checked_at':drafts.now().isoformat()})
    monkeypatch.setenv('SHOPIFY_NATIVE_DIMENSIONS_ENABLED','true')
    plan,_=drafts.preview(world[0],adapter)
    assert plan['native']['historical_finding']=='Likely previously mapped from finished dimensions'
    assert not adapter.writes and adapter.remote==before
    change(world,packaging={'packed_dimensions':{'length':'31','width':'4','height':'4','unit':'cm'},'measured':True})
    plan,token=drafts.preview(world[0],adapter)
    assert plan['classification']=='update' and plan['native']['historical_finding']=='Different remote packed dimensions'
    assert [f['field'] for f in plan['fields'] if f['changed']]==['native.packed_dimensions']
    drafts.confirm(token,True,adapter)
    assert row.shopify_product_id==retained and adapter.remote==before
    assert adapter.packed['length']==31 and len(adapter.writes)==1
    assert drafts.preview(world[0],adapter)[0]['classification']=='no_change'


def test_copy_preview_is_authenticated_local_only(packaging_app):
    app,_=packaging_app;client=logged(app)
    before=app.test_client().post('/packaging/dimension-copy-preview',json={'document':sizes()})
    assert before.status_code in {302,401}
    answer=client.post('/packaging/dimension-copy-preview',json={'document':sizes()})
    assert answer.status_code==200 and len(answer.json['configurations'])==3
    assert answer.json['remote_writes']==answer.json['local_writes']==0
    app.config['WTF_CSRF_ENABLED']=True
    assert client.post('/packaging/dimension-copy-preview',json={'document':sizes()}).status_code==400


@pytest.mark.parametrize('owner,kind',[('Simple Collection/Blue Token','override'),('Variable Collection/Badge One','override'),('Single Variable','shared')])
def test_new_packed_units_survive_atomic_save_and_scan_without_finished_rewrite(core,owner,kind):
    app,client,root,output,ids,*_=core
    target=root/('Single Variable/product_info.json' if kind=='shared' else owner+'/product_info.json')
    original=json.loads(target.read_text()) if target.exists() else {}
    packing={'packed_dimensions':{'length':'6.5','width':'6.5','height':'.2','unit':'in'},'measured':True,'selected_package_id':None}
    response=save(core,owner,kind=kind,data={'packaging':packing})
    assert response.status_code==200,response.json
    after=json.loads(target.read_text())
    assert after['packaging']==packing
    for k in ('dimensions','price','weight','variation_modifiers'): assert after.get(k)==original.get(k)
    from app.utils.scanner import scan_collection
    from app.utils.ingest import ingest_rows_to_db
    from app.models import Product
    from app.taxonomy_assignments import product_document
    with app.app_context():
        product=Product.query.get(ids[owner]);sku=product.sku
        rows=scan_collection(root/owner.split('/')[0],'https://invalid.example/',output,force_update=True,update_csv=True,log=lambda *a,**k:None)
        assert ingest_rows_to_db(rows,log=lambda *a,**k:None)['products_failed']==0
        assert product.sku==sku and product_document(product)['packaging']==packing


def test_variable_packed_only_update_preserves_all_existing_mappings(variable_world,monkeypatch):
    row,adapter=create(variable_world);before=copy.deepcopy(drafts.evidence(row)['variant_maps'])
    monkeypatch.setenv('SHOPIFY_NATIVE_DIMENSIONS_ENABLED','true')
    rescan(variable_world,packaging={'packed_dimensions':{'length':'16','width':'12','height':'1','unit':'cm'},'measured':True})
    plan,token=drafts.preview(variable_world[0],adapter)
    assert plan['classification']=='update' and not plan['blockers']
    drafts.confirm(token,True,adapter)
    assert drafts.evidence(row)['variant_maps']==before
    assert all(v['length']==16 for v in adapter.packed.values())
    assert drafts.preview(variable_world[0],adapter)[0]['classification']=='no_change'
