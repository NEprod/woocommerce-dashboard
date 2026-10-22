"""Editor additions use authoritative sparse JSON and the existing signed publisher."""
import copy
import json
import shutil

import pytest

from app import create_app, db, shopify_drafts as drafts
from app.models import Product, Settings
from config import Config
from app.shopify_attributes import assigned
from app.shopify_simple_fields import review_rows
from app.taxonomy_assignments import product_document, variation_drivers
from app.taxonomy_workspace import empty_registry, validate
from app.utils.ingest import ingest_rows_to_db
from app.utils.scanner import scan_collection
from test_metadata_editor_core import core, save
from test_product_shipping_editor import bootstrap, rescan
from test_shopify_drafts import world
from test_shopify_taxonomy_products import registry
from test_shopify_live_fixes import AccessAdapter
from test_shopify_simple_updates import change
from test_phase3_woo_publish_preview import preview_app


def information_registry():
    def entry(key,name,**extra):
        return dict(key=key,name=name,slug=key,state='active',**extra)
    result=empty_registry()
    result['attributes']=[entry('attr-'+key,name,navigation=False,visible_default=False,
        terms=[entry('term-'+term_key,term_name) for term_key,term_name in terms])
        for key,name,terms in [
            ('material','Material',[('card','Card'),('paper','Paper')]),
            ('personalisation','Personalisation',[('personalised','Personalised')]),
            ('production','Production Method',[('printed','Printed')]),
            ('style','Style / Theme',[('pixel','Pixel Art')]),
            ('lead-time','Lead Time',[('3-5-days','3–5 Days')]),
            ('occasion','Occasion',[('anniversary','Anniversary')]),
            ('recipient','Recipient',[('wife','For Wife'),('husband','For Husband')]),
        ]]
    return result


BASE={'Material':['Card'],'Personalisation':['Personalised'],
      'Production Method':['Printed'],'Style / Theme':['Pixel Art']}
ADDED={'attributes':{'attr-lead-time':['term-3-5-days'],'attr-occasion':['term-anniversary'],'attr-material':['term-paper']}}


def install_information(app,directory):
    directory.mkdir(exist_ok=True)
    (directory/'registry.json').write_bytes(validate(information_registry()))
    app.config['TAXONOMY_ROOT']=str(directory)


@pytest.mark.parametrize('owner,kind',[
    ('Simple Collection/Blue Token','override'),
    ('Variable Collection/Badge One','override'),
    ('Single Variable','shared'),
    ('Simple Collection/Blue Token','shared')])
def test_effective_cards_source_roundtrip_and_fresh_reprojection(core,tmp_path,owner,kind):
    app,client,root,output,ids,*_=core
    install_information(app,tmp_path/'taxonomy')
    shared_path=root/(owner if owner=='Single Variable' else owner.split('/')[0])/'product_info.json'
    shared=json.loads(shared_path.read_text())
    # Preserve the real fixture's variation axes/modifiers in both variable layouts.
    axes=list(shared.get('attributes',{})) if shared['collection_type']!='Simple' else []
    shared['variation_attributes']=axes
    shared['attributes']={**shared.get('attributes',{}),**BASE}
    shared_path.write_text(json.dumps(shared))
    child=root/owner/'product_info.json';before=shared_path.read_bytes()
    original_child=json.loads(child.read_text())
    prior_modifiers=copy.deepcopy(shared.get('variation_modifiers'))
    result=save(core,owner,kind=kind,data={'taxonomy_additions':ADDED})
    assert result.status_code==200,result.json
    assert json.loads((shared_path if kind=='shared' else child).read_text())['taxonomy_additions']==ADDED
    if kind=='override':
        assert shared_path.read_bytes()==before
        assert json.loads(child.read_text())=={**original_child,'taxonomy_additions':ADDED}
        assert 'attributes' not in json.loads(child.read_text())
    page=client.get(f'/edit_products/{ids[owner]}/edit/{kind}')
    data=bootstrap(page)
    expected={**BASE,'Material':['Card','Paper'],'Lead Time':['3–5 Days'],'Occasion':['Anniversary']}
    for name,terms in expected.items():assert data['resolved']['attributes'][name]==terms
    assert b'Woo publishing' not in page.data
    assert b'data-taxonomy-additions="attributes"' not in page.data
    effective=rescan(core,owner)
    assert all(effective['attributes'][name]==terms for name,terms in expected.items())
    assert variation_drivers(effective)==axes
    assert json.loads(shared_path.read_text()).get('variation_modifiers')==prior_modifiers
    # Rebuild a disposable projection using copied authoritative source, not DB-only assignments.
    copied=tmp_path/'copied-catalogue';shutil.copytree(root,copied)
    previous=Config.SQLALCHEMY_DATABASE_URI
    try:
        Config.SQLALCHEMY_DATABASE_URI='sqlite:///'+str(tmp_path/'fresh-projection.db')
        rebuilt=create_app(recover=False)
        install_information(rebuilt,tmp_path/'taxonomy')
        with rebuilt.app_context():
            assert Product.query.count()==0
            db.session.add(Settings(product_folder=str(copied),output_folder=str(output),url_prefix='https://invalid.example/'))
            db.session.commit()
            source=(copied/owner if owner=='Single Variable' else copied/owner.split('/')[0])
            rows=scan_collection(source,'https://invalid.example/',output,force_update=True,update_csv=True,log=lambda *a,**k:None)
            assert rows
            ingest_rows_to_db(rows,log=lambda *a,**k:None)
            db.session.remove()
            product=Product.query.filter_by(source_relpath=owner).one()
            projected=product_document(product)
            assert all(projected['attributes'][name]==terms for name,terms in expected.items())
    finally:
        Config.SQLALCHEMY_DATABASE_URI=previous


def test_existing_shopify_review_includes_inherited_and_sparse_information_without_unapproved_writes(world,registry):
    registry.write_bytes(validate(information_registry()))
    change(world,attributes=BASE,variation_attributes=[])
    child=world[1]/'product_info.json'
    child.write_text(json.dumps({'taxonomy_additions':ADDED}))
    adapter=AccessAdapter()
    source=world[2].read_bytes()
    plan,token=drafts.preview(world[0],adapter)
    assert token and not plan['blockers'],plan['blockers']
    assert len(plan['attributes'])==6
    rows=review_rows(plan)
    assert {r['label'] for r in rows if r['label'].startswith('attributes.')}=={'attributes.'+name for name in [*BASE,'Lead Time','Occasion']}
    assert all(r['changed'] for r in plan['fields'] if r['field'].startswith('attributes.'))
    assert next(a for a in plan['attributes'] if a['name']=='Lead Time')['terms'][0]['name']=='3–5 Days'
    # Internal does NOT mean omitted: it keeps NONE storefront access in the existing contract.
    assert all(not a['public'] for a in plan['attributes'])
    assert not adapter.resources and not adapter.attr_values
    assert world[2].read_bytes()==source
    assert len(assigned(world[0]))==6
    # Approved mocked publication verifies the existing publisher; no new mapping/mutation is introduced.
    receipt=drafts.confirm(token,True,adapter)
    assert receipt.state=='verified',drafts.evidence(receipt).get('error')
    assert len(adapter.attr_values)==6
    assert drafts.preview(world[0],adapter)[0]['classification']=='no_change'


def test_existing_recipient_matching_and_new_product_additions_share_one_supported_review(world,registry):
    registry.write_bytes(validate(information_registry()))
    change(world,attributes={**BASE,'Recipient':['For Wife','For Husband']},variation_attributes=[])
    adapter=AccessAdapter()
    plan,token=drafts.preview(world[0],adapter)
    assert token and not plan['blockers']
    assert drafts.confirm(token,True,adapter).state=='verified'
    child=world[1]/'product_info.json'
    additions={'attributes':{'attr-lead-time':['term-3-5-days'],'attr-occasion':['term-anniversary']}}
    child.write_text(json.dumps({'taxonomy_additions':additions}))
    before=world[2].read_bytes();calls=len(adapter.calls)
    plan,token=drafts.preview(world[0],adapter)
    assert token and not plan['blockers']
    rows={r['label']:r for r in review_rows(plan)}
    assert rows['attributes.Recipient']['action']=='Already matching'
    assert rows['attributes.Lead Time']['action']=='Change'
    assert rows['attributes.Occasion']['action']=='Change'
    assert len(plan['attributes'])==7
    assert not any('create' in name or 'write' in name or 'update' in name for name in adapter.calls[calls:])
    assert world[2].read_bytes()==before
    assert json.loads(child.read_text())=={'taxonomy_additions':additions}


def test_explicit_legacy_replacement_and_restoring_inheritance_preserve_separate_additions(core,tmp_path):
    app,client,root,_,ids,*_=core;owner='Simple Collection/Blue Token'
    install_information(app,tmp_path/'taxonomy')
    parent=root/'Simple Collection/product_info.json'
    shared=json.loads(parent.read_text());shared.update(attributes=BASE,variation_attributes=[])
    parent.write_text(json.dumps(shared));before=parent.read_bytes()
    child=root/owner/'product_info.json'
    own=json.loads(child.read_text());own['attributes']={'Recipient':['For Wife']}
    child.write_text(json.dumps(own))
    additions={'attributes':{'attr-occasion':['term-anniversary'],'attr-lead-time':['term-3-5-days']}}
    assert save(core,owner,data={'taxonomy_additions':additions}).status_code==200
    data=bootstrap(client.get(f'/edit_products/{ids[owner]}/edit/override'))
    assert data['resolved']['attributes']=={'Recipient':['For Wife'],'Occasion':['Anniversary'],'Lead Time':['3–5 Days']}
    assert json.loads(child.read_text())['attributes']==own['attributes']
    assert save(core,owner,removals=[['attributes']]).status_code==200
    assert json.loads(child.read_text())=={k:v for k,v in {**own,'taxonomy_additions':additions}.items() if k!='attributes'}
    effective=rescan(core,owner)
    assert effective['attributes']=={**BASE,'Occasion':['Anniversary'],'Lead Time':['3–5 Days']}
    assert parent.read_bytes()==before
