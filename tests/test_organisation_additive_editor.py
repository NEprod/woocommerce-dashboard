"""Merchant-supplied override, normal additive saves and four-field isolation."""
import copy
import json
from pathlib import Path

import pytest

from app import db, organisation
from app.models import Product
from app.taxonomy_assignments import product_document
from app.taxonomy_workspace import validate
from app.utils.json_utils import merge_product_json
from test_informational_attributes import BASE, information_registry
from test_metadata_editor_core import core, save
from test_product_shipping_editor import bootstrap, rescan

RECIPIENTS=['For Wife','For Husband','For Girlfriend','For Boyfriend','For Partner','For Couple']
RECIPIENT_KEYS=['term-wife','term-husband','term-girlfriend','term-boyfriend','term-partner','term-couple']


def test_removed_storage_field_has_no_application_support():
    from app.product_info import load_schema
    obsolete='taxonomy_additions'
    for kind in ('collection','override'):
        assert obsolete not in load_schema(kind)['properties']
    application=Path(__file__).resolve().parents[1]/'app'
    for path in application.rglob('*'):
        if path.is_file() and path.suffix in {'.py','.js','.html','.json'} and 'vendor' not in path.parts:
            assert obsolete not in path.read_text(),path


def registry_document():
    data=information_registry()
    data['attributes'][-1]['terms']=[dict(key=key,name=name,slug=key,state='active') for key,name in zip(RECIPIENT_KEYS,RECIPIENTS)]
    data['categories']=[dict(key='cat-cards',name='Cards',slug='cards',parent=None,state='active'),
        dict(key='cat-anniversary',name='Anniversary Cards',slug='anniversary',parent='cat-cards',state='active')]
    data['storefront_collections']=[dict(key='range-16bit',name='16-Bit',slug='16bit',state='active'),
        dict(key='range-anniversary',name='Anniversary Cards',slug='anniversary',state='active')]
    data['tags']=[dict(key='tag-'+key,name=name,slug=key,state='active') for key,name in
        [('personalised','Personalised'),('pixel','Pixel Art'),('anniversary','Anniversary')]]
    return data


def install(app,directory):
    directory.mkdir()
    (directory/'registry.json').write_bytes(validate(registry_document()))
    app.config['TAXONOMY_ROOT']=str(directory)


def merchant_override():
    return {'categories':['Cards > Anniversary Cards'],'attributes':{'Recipient':RECIPIENTS,'Occasion':['Anniversary']},
        'sale_price':'1.50','sale_start_date':'2026-10-15T20:15:00.000Z','sale_end_date':'2026-10-15T20:30:00.000Z',
        'packaging':{'packed_dimensions':{'height':'3','length':'155','unit':'mm','width':'155'},
            'measured':True,'assignment_kind':'manual','classification':'rigid','fragile':True,
            'selected_package_id':'c476b503-94de-59b0-9db9-f345fe35059e'}}


def test_only_four_information_sections_join_while_other_overrides_are_identical(core,tmp_path):
    app,*_=core;install(app,tmp_path/'taxonomy')
    shared={'collection_type':'Simple','attributes':BASE,'categories':['Cards'],'storefront_collections':['16-Bit'],'tags':['Personalised'],
        'price':'8','weight':50,'dimensions':{'length':155},'description':'Shared','vendor':'Shared',
        'packaging':{'packed_dimensions':{'length':160,'unit':'mm'}},'customisation':{'proof_policy':'none'}}
    child={**merchant_override(),'price':'3.25','weight':44,'description':'Product','meta_title':'Product SEO','meta_description':'Private description',
        'dimensions':{'length':160,'height':4},'country_of_origin':'GB','manage_stock':False,'requires_shipping':True,
        'shopify_status':'DRAFT','shopify_channels':{'online_store':False},'vendor':'TLC','image_attributes':['Design'],
        'variation_modifiers':{'Size=A4':{'price':9,'weight':60}},'customisation':{'proof_policy':'required'},
        'shopify_product_type_key':'cat-cards','opaque':{'keep':[1,2]}}
    before=copy.deepcopy((shared,child));four={'attributes','categories','storefront_collections','tags'}
    with app.app_context():
        actual=merge_product_json(shared,child)
        baseline=merge_product_json({k:v for k,v in shared.items() if k not in four},{k:v for k,v in child.items() if k not in four})
    for key,value in baseline.items():assert actual[key]==value,key
    assert (shared,child)==before
    from app.metadata_workspace import metadata_comparison
    rows={row['key']:row for row in metadata_comparison(shared,child,actual)}
    assert rows['attributes']['state']==rows['categories']['state']=='product_additions'
    assert rows['price']['state']==rows['weight']['state']==rows['shopify_product_type_key']['state']=='overridden'


def test_merchant_top_level_attributes_combine_without_source_rewrite(core,tmp_path):
    app,client,root,_,ids,*_=core;install(app,tmp_path/'taxonomy')
    owner='Simple Collection/Blue Token';parent=root/'Simple Collection/product_info.json';child=root/owner/'product_info.json'
    shared=json.loads(parent.read_text());shared.update(attributes={**BASE,'Lead Time':['3–5 Days']},variation_attributes=[],
        categories=['Cards'],storefront_collections=['16-Bit'],tags=['Personalised','Pixel Art'])
    parent.write_text(json.dumps(shared));original=merchant_override();child.write_text(json.dumps(original));parent_before=parent.read_bytes()
    before=rescan(core,owner)
    assert before['attributes']=={**BASE,'Lead Time':['3–5 Days'],'Recipient':RECIPIENTS,'Occasion':['Anniversary']}
    assert before['categories']==['Cards','Cards > Anniversary Cards']
    reopened=bootstrap(client.get(f'/edit_products/{ids[owner]}/edit/override'))
    assert reopened['authored']['attributes']=={'Recipient':RECIPIENTS,'Occasion':['Anniversary']}
    # Normal edits use the same top-level field names as the collection.
    local={'attributes':{'Recipient':RECIPIENTS,'Occasion':['Anniversary']},
        'storefront_collections':['Anniversary Cards'],'tags':['Anniversary']}
    response=save(core,owner,data=local)
    assert response.status_code==200,response.json
    expected={**original,**local}
    assert json.loads(child.read_text())==expected
    assert parent.read_bytes()==parent_before
    after=rescan(core,owner)
    assert after['attributes']=={**BASE,'Lead Time':['3–5 Days'],'Occasion':['Anniversary'],'Recipient':RECIPIENTS}
    assert after['storefront_collections']==['16-Bit','Anniversary Cards']
    assert after['tags']==['Personalised','Pixel Art','Anniversary']
    assert after['packaging']==before['packaging']
    for key in ('sale_price','sale_start_date','sale_end_date'):assert after[key]==before[key]
    with app.app_context():
        db.session.remove()
        assert product_document(db.session.get(Product,ids[owner]))['attributes']==after['attributes']
    assert bootstrap(client.get(f'/edit_products/{ids[owner]}/edit/override'))['authored']==expected
    # Existing source edit receipts include a recoverable backup of the original JSON.
    backups=list(child.parent.glob('*.bak.*'))
    assert backups and any(json.loads(path.read_text())==original for path in backups)


@pytest.mark.parametrize('owner,kind',[
    ('Simple Collection/Blue Token','override'),('Variable Collection/Badge One','override'),('Single Variable','shared')])
def test_four_normal_fields_are_sparse_without_altering_other_overrides(core,tmp_path,owner,kind):
    app,client,root,_,ids,*_=core;install(app,tmp_path/'taxonomy')
    parent=root/owner.split('/')[0]/'product_info.json';shared=json.loads(parent.read_text())
    shared.update(categories=['Cards'],storefront_collections=['16-Bit'],tags=['Personalised','Pixel Art'])
    if shared['collection_type']!='Simple':shared['variation_attributes']=list(shared.get('attributes',{}))
    parent.write_text(json.dumps(shared));before=parent.read_bytes()
    path=root/owner/'product_info.json';own=json.loads(path.read_text());modifiers=copy.deepcopy(shared.get('variation_modifiers'))
    original_tags=own.get('tags',[]) if kind=='override' else ['Personalised','Pixel Art']
    additions={'categories':['Cards > Anniversary Cards'],'storefront_collections':['Anniversary Cards'],
        'tags':[*original_tags,'Anniversary'],'attributes':{'Occasion':['Anniversary']}}
    if kind=='shared':
        additions['categories'].insert(0,'Cards');additions['storefront_collections'].insert(0,'16-Bit')
        additions['attributes']={**own.get('attributes',{}),**additions['attributes']}
    assert save(core,owner,kind=kind,data=additions).status_code==200
    assert json.loads(path.read_text())=={**own,**additions}
    if kind=='override':assert parent.read_bytes()==before
    effective=rescan(core,owner)
    assert effective['categories']==['Cards','Cards > Anniversary Cards']
    assert effective['storefront_collections']==['16-Bit','Anniversary Cards']
    assert effective['tags']==list(dict.fromkeys(['Personalised','Pixel Art',*original_tags,'Anniversary']))
    assert effective['attributes']['Occasion']==['Anniversary']
    assert effective.get('variation_modifiers')==modifiers
    page=client.get(f'/edit_products/{ids[owner]}/edit/{kind}')
    for field in ('categories','tags','storefront_collections'):assert f'data-effective-organisation="{field}"' in page.text
    assert 'data-effective-organisation="organisation"' not in page.text
    restore={key:own[key] for key in additions if key in own}
    assert save(core,owner,kind=kind,data=restore,removals=[[key] for key in additions if key not in own]).status_code==200
    final=rescan(core,owner)
    assert final['categories']==['Cards'] and final['storefront_collections']==['16-Bit']
    assert final['tags']==list(dict.fromkeys(['Personalised','Pixel Art',*original_tags]))
