"""The ordinary signed publisher owns dimensions; optional packages never block it."""
import copy
import json
import uuid
import pytest
from app import db, shopify_drafts as drafts, shopify_variables as variables
from app.models import Product
from app.shopify_connection import ShopifyError
from app.shopify_products import source_fingerprint
from test_shopify_drafts import world
from test_shopify_simple_updates import created, change
from test_shopify_native_dimensions import NativeAdapter
from test_shopify_variables import variable_world, create
from test_metadata_editor_core import core, save
from test_phase3_woo_publish_preview import preview_app, _client


def test_existing_finished_update_through_signed_review_and_readback(world):
    product,row,adapter=created(world)
    retained=row.shopify_product_id
    change(world,finished_dimensions={'length':'6','width':'6','height':'.1','unit':'in','label':'folded'},
        packaging={'selected_package_id':None,'weight_g':'51.25'},requires_shipping=True)
    before=json.loads(world[2].read_text())
    plan,token=drafts.preview(product,adapter)
    assert plan['classification']=='update' and not plan['blockers']
    assert any('Solo package selection is optional' in w for w in plan['warnings'])
    assert any(f['field']=='dimensions.length' and f['changed'] for f in plan['fields'])
    with pytest.raises(ShopifyError): drafts.confirm(token,False,adapter)
    result=drafts.confirm(token,True,adapter)
    assert result.state=='verified' and result.shopify_product_id==retained
    assert json.loads(adapter.remote['length']['value'])['value']==152.4
    variant=adapter.remote['simpleVariants']['nodes'][0]
    assert variant['inventoryItem']['measurement']['weight']=={'value':51.25,'unit':'GRAMS'}
    assert variant['inventoryItem']['requiresShipping'] is True
    assert not any('shippingPackageId' in str(i) for i in adapter.inputs)
    assert json.loads(world[2].read_text())==before
    assert drafts.preview(product,adapter)[0]['classification']=='no_change'


def test_optional_manual_package_and_clear_are_not_false_remote_updates(world):
    product,row,adapter=created(world)
    change(world,packaging={'selected_package_id':str(uuid.uuid4()),'assignment_kind':'manual','override_reason':'Measured locally'})
    plan,token=drafts.preview(product,adapter)
    assert plan['classification']=='no_change' and not plan['blockers']
    assert any('manual controls in Shopify Admin' in w for w in plan['warnings'])
    assert plan['local_package_changes'] == 1
    assert plan['optional_packages'][0]['state'] == 'Shopify Package ID required'
    assert not any('update' in c or 'create' in c for c in adapter.calls)
    change(world,packaging={'selected_package_id':None})
    plan,_=drafts.preview(product,adapter)
    assert plan['classification']=='no_change'
    assert plan['optional_packages'][0]['state'] == 'Explicitly Unassigned'


def test_create_without_package_supports_separate_finished_and_packed(world,monkeypatch):
    monkeypatch.setenv('SHOPIFY_NATIVE_DIMENSIONS_ENABLED','true')
    monkeypatch.setenv('SHOPIFY_PACKED_DIMENSIONS_USE_PRODUCT_DIMENSIONS','false')
    change(world,finished_dimensions={'length':'42','width':'29.7','height':'.03','unit':'cm'},
        packaging={'packed_dimensions_cm':{'length':'30','width':'3','height':'3'},'measured':True,'weight_g':'101.25'})
    adapter=NativeAdapter()
    plan,token=drafts.preview(world[0],adapter)
    assert plan['classification']=='create' and not plan['blockers']
    result=drafts.confirm(token,True,adapter)
    assert result.state=='verified'
    assert adapter.packed=={'length':30.0,'width':3.0,'height':3.0,'unit':'CENTIMETERS'}
    assert json.loads(adapter.remote['length']['value'])['value']==420
    assert drafts.preview(world[0],adapter)[0]['classification']=='no_change'


def test_variant_finished_and_weight_preserve_retained_ids(variable_world):
    product,shared,*_=variable_world
    row,adapter=create(variable_world)
    original=copy.deepcopy([(v['id'],v['inventoryItem']['id'],v['sku']) for v in adapter.remote['variants']['nodes']])
    before=source_fingerprint(product,[])
    authored=json.loads(shared.read_text())
    chosen=product.variations[0]
    from app.utils.json_utils import json_key
    key=json_key({a.name:a.value for a in chosen.attributes})
    mod=authored.setdefault('variation_modifiers',{}).setdefault(key,{})
    mod['finished_dimensions']={'length':'29.7','width':'21','height':'.03','unit':'cm'}
    mod['packaging']={'selected_package_id':None,'weight_g':'99.25'}
    shared.write_text(json.dumps(authored))
    assert source_fingerprint(product,[])!=before
    plan,token=drafts.preview(product,adapter)
    assert plan['classification']=='update' and not plan['blockers']
    assert any(chosen.sku in warning and 'optional solo package' in warning for warning in plan['warnings'])
    result=drafts.confirm(token,True,adapter)
    assert result.state=='verified'
    assert [(v['id'],v['inventoryItem']['id'],v['sku']) for v in adapter.remote['variants']['nodes']]==original
    remote=next(v for v in adapter.remote['variants']['nodes'] if v['sku']==chosen.sku)
    assert json.loads(remote['length']['value'])['value']==297
    assert remote['inventoryItem']['measurement']['weight']['value']==99.25
    assert drafts.preview(product,adapter)[0]['classification']=='no_change'


@pytest.mark.parametrize('owner,kind',[('Simple Collection/Blue Token','override'),('Variable Collection/Badge One','override'),('Single Variable','shared')])
def test_authoritative_editor_save_roundtrip_without_package(core,owner,kind):
    app,client,root,output,ids,*_=core
    before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('sku_index.json')}
    data={'finished_dimensions':{'length':'6','width':'6','unit':'in'},'packaging':{'selected_package_id':None,'weight_g':'51','packed_dimensions_cm':{'length':'16','width':'16','height':'.3'},'measured':True}}
    response=save(core,owner,kind=kind,data=data)
    assert response.status_code==200,response.json
    target=root/('Single Variable/product_info.json' if kind=='shared' else owner+'/product_info.json')
    document=json.loads(target.read_text())
    assert document['packaging']==data['packaging'] and document['finished_dimensions']==data['finished_dimensions']
    from app.utils.scanner import scan_collection
    from app.utils.ingest import ingest_rows_to_db
    with app.app_context():
        product=db.session.get(Product,ids[owner]);oldsku=product.sku
        collection=root/owner.split('/')[0]
        rows=scan_collection(collection,'https://invalid.example/',output,update_csv=True,log=lambda *a,**k:None)
        ingest_rows_to_db(rows,log=lambda *a,**k:None)
        assert db.session.get(Product,ids[owner]).sku==oldsku
    assert before=={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('sku_index.json')}
