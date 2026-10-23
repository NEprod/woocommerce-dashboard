"""Stable-key additions and parent Product Type; fictional catalogue/Shopify only."""
import copy
import json

import pytest

from app import db, organisation, shopify_drafts as drafts
from app.models import Product
from app.product_info import validate_product_info
from app.taxonomy_assignments import product_document, variation_drivers
from app.utils.json_utils import merge_product_json, apply_variation_modifiers
from test_metadata_editor_core import core, save
from test_product_shipping_editor import rescan, bootstrap
from test_shopify_drafts import world, FakeDraftAdapter
from test_shopify_simple_updates import created, change
from test_phase3_woo_publish_preview import preview_app
from test_shopify_variables import variable_world, VariableAdapter


def choices():
    def row(key, value, **extra):
        return dict(key=key,value=value,name=value,state='active',aliases=[],**extra)
    return {'status':'ready','digest':'fixture',
        'categories':[row('cat-cards','Cards',parent=None),row('cat-gifts','Gifts',parent=None),row('cat-birthday','Cards > Birthday',parent='cat-cards')],
        'storefront_collections':[row('range-pixel','Pixel'),row('range-spring','Spring')],
        'tags':[row('tag-personalised','Personalised')],
        'attributes':[row('attr-occasion','Occasion',terms=[row('term-birthday','Birthday'),row('term-anniversary','Anniversary'),row('term-mother','Mother\'s Day')]),
            row('attr-recipient','Recipient',terms=[row('term-him','Him'),row('term-her','Her'),row('term-mum','Mum')]),
            row('attr-size','Size',terms=[row('term-a5','A5'),row('term-a4','A4')])]}


@pytest.fixture
def vocabulary(monkeypatch):
    value=choices()
    monkeypatch.setattr(organisation,'registry_options',lambda:value)
    return value


def parent():
    return {'collection_type':'Simple','attributes':{'Occasion':['Birthday','Anniversary'],'Recipient':['Him','Her']},
        'categories':['Cards'],'storefront_collections':['Pixel'],'tags':['Existing'],
        'shopify_product_type_key':'cat-cards','price':7,'weight':50}


def additions():
    return {'attributes':{'Occasion':['Birthday',"Mother's Day"],'Recipient':['Mum']},
        'categories':['Cards','Cards > Birthday'],'storefront_collections':['Spring'],'tags':['Personalised']}


def test_additive_information_and_organisation_keep_sparse_sources(vocabulary):
    shared,child=parent(),additions();before=copy.deepcopy((shared,child))
    effective=merge_product_json(shared,child)
    assert effective['attributes']=={'Occasion':['Birthday','Anniversary',"Mother's Day"],'Recipient':['Him','Her','Mum']}
    assert effective['categories']==['Cards','Cards > Birthday']
    assert effective['storefront_collections']==['Pixel','Spring']
    assert effective['tags']==['Existing','Personalised']
    assert (shared,child)==before
    assert variation_drivers(effective)==[]


def test_new_group_and_removing_local_terms_preserve_parent(vocabulary):
    shared=parent();shared['attributes'].pop('Recipient')
    child=additions()
    assert merge_product_json(shared,child)['attributes']['Recipient']==['Mum']
    child['attributes'].pop('Occasion')
    assert merge_product_json(shared,child)['attributes']['Occasion']==['Birthday','Anniversary']
    assert merge_product_json(shared,{})['attributes']==shared['attributes']


def test_stable_identity_alias_dedup_and_deterministic_order(vocabulary):
    vocabulary['attributes'][0]['terms'][0]['aliases']=['Birthday party']
    shared=parent();shared['attributes']['Occasion']=['Birthday party','Anniversary']
    for _ in range(5):
        assert merge_product_json(shared,additions())['attributes']['Occasion']==['Birthday party','Anniversary',"Mother's Day"]
    assert merge_product_json({'categories':['B','A'],'tags':['x','y']},{'categories':['A','C'],'tags':['y','z']})['categories']==['B','A','C']


def test_informational_objects_and_collection_lists_are_additive_even_when_empty(vocabulary):
    shared=parent();shared['storefront_collections'].append('Spring');shared['attributes']['Occasion'].append("Mother's Day")
    replaced=merge_product_json(shared,{'attributes':{'Occasion':['Birthday']},'storefront_collections':[]})
    assert replaced['attributes']=={'Occasion':['Birthday','Anniversary',"Mother's Day"],'Recipient':['Him','Her']}
    assert replaced['storefront_collections']==['Pixel','Spring']
    cleared=merge_product_json(shared,{'attributes':{},'variation_attributes':[]})
    assert cleared['attributes']==shared['attributes'] | {'Occasion':['Birthday','Anniversary',"Mother's Day"]}
    assert cleared['variation_attributes']==[]


def test_information_cannot_change_variations_or_modifiers(vocabulary):
    shared={'collection_type':'Variable Collection','attributes':{'Size':['A5','A4']},'variation_modifiers':{'Size=A4':{'price':9,'weight':60,'dimensions':{'length':297}}}}
    effective=merge_product_json(shared,{'attributes':{'Recipient':['Mum']}})
    assert variation_drivers(effective)==['Size'] and effective['attributes']['Recipient']==['Mum']
    assert apply_variation_modifiers(shared,{'Size':'A4'},log=lambda *a,**k:None)==apply_variation_modifiers(effective,{'Size':'A4'},log=lambda *a,**k:None)
    # Explicit variation-option overrides still use the established contract.
    assert merge_product_json(shared,{'attributes':{'Size':['A4']}})['attributes']=={'Size':['A4']}


def test_product_type_scalar_inheritance_override_rename_and_independence(vocabulary):
    shared=parent()
    assert organisation.product_type(merge_product_json(shared,{}))=='Cards'
    assert organisation.product_type(merge_product_json(shared,{'shopify_product_type_key':'cat-gifts'}))=='Gifts'
    vocabulary['categories'][0]['name']='Greeting Cards'
    assert organisation.product_type(shared)=='Greeting Cards'
    assert shared['categories']==['Cards'] and shared['storefront_collections']==['Pixel']
    assert organisation.product_type({}) is None
    assert organisation.product_type({'shopify_product_type':'Historic label'})=='Historic label'
    assert organisation.product_type(merge_product_json(shared,{'shopify_product_type':'Historic child override'}))=='Historic child override'


@pytest.mark.parametrize('key', ['cat-deleted','cat-birthday'])
def test_unresolved_or_subcategory_product_type_does_not_guess(vocabulary,key):
    with pytest.raises(ValueError):organisation.product_type({'shopify_product_type_key':key})
    assert validate_product_info({'shopify_product_type_key':key},'override').valid  # safe local editing is allowed


def test_schema_does_not_require_type_but_enforces_single_key_and_normal_field_shape():
    assert validate_product_info({},'override').valid
    assert not validate_product_info({'shopify_product_type_key':['cat-cards','cat-gifts']},'override').valid
    assert validate_product_info(additions(),'override').valid
    assert not validate_product_info({'attributes':{'Occasion':'Birthday'}},'override').valid


@pytest.mark.parametrize('owner,kind',[('Simple Collection/Blue Token','override'),('Variable Collection/Badge One','override'),('Single Variable','shared')])
def test_save_reopen_rescan_and_no_parent_mutation(core,vocabulary,owner,kind):
    app,client,root,_,ids,*_=core
    path=root/owner/'product_info.json';before=json.loads(path.read_text())
    parent_path=root/owner.split('/')[0]/'product_info.json';parent_before=parent_path.read_bytes()
    attributes={'Recipient':['Mum']}
    if kind=='shared':attributes={**before.get('attributes',{}),**attributes}
    data={'shopify_product_type_key':'cat-cards','attributes':attributes}
    if kind=='shared' and before.get('collection_type')!='Simple':
        data['variation_attributes']=before.get('variation_attributes',list(before.get('attributes',{})))
    assert save(core,owner,kind=kind,data=data).status_code==200
    assert json.loads(path.read_text())=={**before,**data}
    if kind=='override':assert parent_path.read_bytes()==parent_before
    page=client.get(f'/edit_products/{ids[owner]}/edit/{kind}')
    assert bootstrap(page)['authored']['attributes']==attributes
    effective=rescan(core,owner)
    assert effective['attributes']['Recipient']==['Mum'] and effective['shopify_product_type_key']=='cat-cards'
    assert save(core,owner,kind=kind,data=data).json['unchanged']
    if kind=='override':assert parent_path.read_bytes()==parent_before


def test_shopify_create_native_type_then_verify_and_no_repeat(world,vocabulary):
    product,_,_=world
    adapter=FakeDraftAdapter()
    change(world,shopify_product_type_key='cat-cards')
    plan,token=drafts.preview(product,adapter)
    assert plan['managed']['product']['productType']=='Cards'
    assert any(field['field']=='productType' for field in plan['fields'])
    row=drafts.confirm(token,True,adapter=adapter)
    assert row.state=='verified' and adapter.remote['productType']=='Cards'
    before=adapter.calls.count('product_create')
    assert drafts.preview(product,adapter)[0]['classification']=='no_change'
    assert adapter.calls.count('product_create')==before==1


def test_shopify_update_type_and_independent_readback(world,vocabulary):
    product,row,adapter=created(world)
    adapter.remote['productType']='Existing remote label'
    change(world,shopify_product_type_key='cat-gifts')
    plan,token=drafts.preview(product,adapter)
    field=next(f for f in plan['fields'] if f['field']=='productType')
    assert field['changed'] and field['before']=='Existing remote label' and field['after']=='Gifts'
    assert drafts.confirm(token,True,adapter=adapter).state=='verified'
    assert adapter.remote['productType']=='Gifts' and any(p.get('productType')=='Gifts' for p in adapter.inputs)
    assert drafts.preview(product,adapter)[0]['classification']=='no_change'
    adapter.remote['productType']='Changed outside dashboard'
    assert next(f for f in drafts.preview(product,adapter)[0]['fields'] if f['field']=='productType')['changed']


def test_unspecified_type_is_preserved_and_deleted_key_stays_actionable(world,vocabulary):
    product,row,adapter=created(world);adapter.remote['productType']='Keep remote'
    plan,_=drafts.preview(product,adapter)
    assert 'productType' not in plan['managed']['product']
    change(world,shopify_product_type_key='cat-deleted')
    from app.shopify_connection import ShopifyReadinessFinding
    with pytest.raises(ShopifyReadinessFinding,match='Unresolved Product Type'):
        drafts.preview(product,adapter)
    assert adapter.remote['productType']=='Keep remote'


def test_variable_parent_type_never_changes_variants(variable_world,vocabulary):
    product,path,*_=variable_world
    document=json.loads(path.read_text());document['shopify_product_type_key']='cat-cards'
    path.write_text(json.dumps(document))
    identities=[(v.id,v.sku,v.regular_price,v.weight) for v in product.variations]
    adapter=VariableAdapter()
    plan,token=drafts.preview(product,adapter)
    assert plan['managed']['product']['productType']=='Cards'
    assert all('productType' not in variant['managed'] for variant in plan['variants'])
    row=drafts.confirm(token,True,adapter)
    assert row.state=='verified' and adapter.remote['productType']=='Cards'
    assert [(v.id,v.sku,v.regular_price,v.weight) for v in product.variations]==identities


def test_registry_unavailable_does_not_block_legacy_or_unspecified_types(monkeypatch):
    monkeypatch.setattr(organisation,'registry_options',lambda:{'status':'registry_missing'})
    assert merge_product_json({'categories':['Cards']},{'categories':['Gifts']})['categories']==['Cards','Gifts']
    assert organisation.product_type({}) is None
    assert organisation.product_type({'shopify_product_type':'Existing'})=='Existing'
    with pytest.raises(ValueError,match='readable'):
        organisation.product_type({'shopify_product_type_key':'cat-cards'})


def test_parent_rename_invalidates_comparison_and_review_before_any_write(world,vocabulary):
    product,_,_=world;adapter=FakeDraftAdapter()
    change(world,shopify_product_type_key='cat-cards')
    from app.shopify_taxonomy import fingerprint
    before=fingerprint(product)
    _,token=drafts.preview(product,adapter)
    vocabulary['categories'][0]['name']='Greeting Cards'
    assert fingerprint(product)!=before
    from app.shopify_connection import ShopifyError
    with pytest.raises(ShopifyError):drafts.confirm(token,True,adapter)
    assert 'product_create' not in adapter.calls


def test_value_sources_do_not_call_local_additions_inherited(vocabulary):
    from app.metadata_workspace import metadata_comparison
    shared,child=parent(),additions()
    rows={row['key']:row for row in metadata_comparison(shared,child,merge_product_json(shared,child))}
    for key in ('attributes','categories','storefront_collections','tags'):
        assert rows[key]['state']=='product_additions'
        assert json.loads(rows[key]['override_display'])==additions()[key]
    assert rows['shopify_product_type_key']['state']=='inherited'


def test_new_type_selections_are_parents_but_invalid_retained_keys_do_not_block_other_edits(core,vocabulary):
    owner='Simple Collection/Blue Token';target=core[2]/owner/'product_info.json'
    before=target.read_bytes()
    assert save(core,owner,data={'shopify_product_type_key':'cat-birthday'}).status_code==400
    assert save(core,owner,data={'shopify_product_type_key':'cat-deleted'}).status_code==400
    assert target.read_bytes()==before
    data=json.loads(before);data['shopify_product_type_key']='cat-deleted';target.write_text(json.dumps(data))
    assert save(core,owner,data={'description':'Safe unrelated local edit'}).status_code==200
    assert json.loads(target.read_text())['shopify_product_type_key']=='cat-deleted'


def test_legacy_type_label_survives_edits_but_new_free_text_is_not_authorable(core,vocabulary):
    owner='Simple Collection/Blue Token';target=core[2]/owner/'product_info.json'
    document=json.loads(target.read_text());document['shopify_product_type']='Historic label';target.write_text(json.dumps(document))
    assert save(core,owner,data={'description':'Unrelated edit'}).status_code==200
    before=target.read_bytes()
    assert save(core,owner,data={'shopify_product_type':'Arbitrary new label'}).status_code==400
    assert target.read_bytes()==before
    assert save(core,owner,data={'shopify_product_type_key':'cat-cards'}).status_code==200
