"""M3A.9.4.2 fictional catalogue/title regression; no live services or sources."""
import copy
import json

import pytest

from app import db
from app.models import Product, ShopifyProductIdentity
from app.utils.json_utils import merge_product_json, title_components
from app.utils.scanner import scan_collection
from app.utils.ingest import ingest_rows_to_db
from app.metadata_workspace import current_title
from app.taxonomy_assignments import product_document
from app.commerce import catalogue_snapshot
from app.shopify_drafts import local_plan
from test_metadata_editor_core import core, save
from test_shopify_drafts import world
from test_shopify_simple_updates import created
from test_phase3_woo_publish_preview import preview_app


CASES = [
    ('Level Complete', 'Retro Gaming Cards', 'Level Complete | Retro Gaming Cards'),
    ('Level Complete', None, 'Level Complete | 16-Bit Pixel Art Cards'),
    (None, 'Retro Gaming Cards', 'Fathers Day Level Complete | Retro Gaming Cards'),
    (None, None, 'Fathers Day Level Complete | 16-Bit Pixel Art Cards'),
]


@pytest.mark.parametrize('own,shared,expected', CASES)
@pytest.mark.parametrize('layout', ['Simple','Variable Collection'])
def test_four_authoritative_cases(own, shared, expected, layout):
    common={'collection_type':layout, 'title':shared,'tags':['shared']}
    child={'title':own,'tags':['child']}
    before=copy.deepcopy((common,child))
    result=merge_product_json(common,child,path='16-Bit Pixel Art Cards/Fathers Day Level Complete')
    assert result['title']==expected
    assert (common,child)==before and set(result['tags'])=={'shared','child'}


@pytest.mark.parametrize('value',[None,'','  ','\t\n'])
def test_missing_empty_null_whitespace_are_absent_without_suppressing_other_scope(value):
    assert title_components({'title':value},{'title':value},'Collection/Product')['title']=='Product | Collection'
    assert title_components({'title':'Shared'},{'title':value},'Collection/Product')['title']=='Product | Shared'


def test_full_titles_are_not_truncated_and_single_variable_retains_historical_root_rule():
    title='Long authored title '+('é'*300)
    assert title_components({'title':title},{},'Collection/Product')['title']=='Product | '+title
    assert title_components({'title':'Personalised Print','collection_type':'Single Variable'}, {}, 'Single Variable')['title']=='Single Variable - Personalised Print'
    assert title_components({'title':'Single Variable','collection_type':'Single Variable'}, {}, 'Single Variable')['title']=='Single Variable'
    assert title_components({'collection_type':'Single Variable'}, {}, 'Single Variable')['title']=='Single Variable'
    assert title_components({'title':'Root product','collection_type':'Simple'}, {}, 'Root',collection_path='Root')['title']=='Root product'
    # Historical scanner coercion remains compatible; editor schema still rejects it.
    from app.product_info import validate_product_info
    assert title_components({'title':42},{},'Collection/Product')['title']=='Product | 42'
    assert not validate_product_info({'title':42},'override').valid
    assert 'title' not in merge_product_json({'tags':['shared']},{})


@pytest.mark.parametrize('own,shared,expected',CASES)
@pytest.mark.parametrize('layout',['Simple','Variable Collection'])
def test_scanner_missing_child_json_four_cases_and_markers(tmp_path,monkeypatch,own,shared,expected,layout):
    root=tmp_path/'16-Bit Pixel Art Cards';folder=root/'Fathers Day Level Complete';folder.mkdir(parents=True)
    data={'collection_type':layout,'sku_prefix':'TITLE-','price':'3.50','live':False,'title':shared}
    if layout=='Variable Collection':data.update(attributes={'Size':['Small','Large']},variation_attributes=['Size'])
    source=root/'product_info.json';source.write_text(json.dumps(data))
    if own is not None:(folder/'product_info.json').write_text(json.dumps({'title':own,'customisation':{'fields':{}}}))
    before={f:f.read_bytes() for f in root.rglob('product_info.json')}
    monkeypatch.setattr('app.utils.scanner.process_images',lambda *a,**k:[])
    rows=scan_collection(root,'https://invalid.example/',tmp_path/'output',log=lambda *a,**k:None)
    assert rows[0]['Name']==expected and rows[0]['SKU']=='TITLE-0001'
    assert len(rows)==(3 if layout=='Variable Collection' else 1)
    marker=json.loads((folder/'.scanned').read_text())
    assert marker['title']==expected
    index=(root/'sku_index.json').read_bytes()
    assert scan_collection(root,'https://invalid.example/',tmp_path/'output',log=lambda *a,**k:None)==[]
    (folder/'.update').touch()
    again=scan_collection(root,'https://invalid.example/',tmp_path/'output',update_csv=True,reset_sku_index=False,log=lambda *a,**k:None)
    assert [(r['SKU'],r['Name']) for r in again]==[(r['SKU'],r['Name']) for r in rows]
    assert (root/'sku_index.json').read_bytes()==index and not (folder/'.update').exists()
    assert all(f.read_bytes()==value for f,value in before.items())


def test_many_variable_children_keep_distinct_titles_without_redundant_json(tmp_path,monkeypatch):
    root=tmp_path/'Dog Ornaments';root.mkdir()
    source=root/'product_info.json';source.write_text(json.dumps({'collection_type':'Variable Collection','sku_prefix':'DOG-','price':'3.50','title':'Personalised Dog Ornament','attributes':{'Size':['Small','Large']}}))
    names=['Labrador','German Shepherd','Border Collie']+[f'Fictional Breed {i:03}' for i in range(147)]
    for name in names:(root/name).mkdir()
    (root/'Labrador/product_info.json').write_text(json.dumps({'title':'Labrador Memorial'}))
    monkeypatch.setattr('app.utils.scanner.process_images',lambda *a,**k:[])
    rows=scan_collection(root,'https://invalid.example/',tmp_path/'output',log=lambda *a,**k:None)
    parents=[r for r in rows if r['Type']=='variable']
    assert len(parents)==150 and len(rows)==450
    assert len({r['Name'] for r in parents})==150 and len({r['SKU'] for r in rows})==450
    assert 'Labrador Memorial | Personalised Dog Ornament' in {r['Name'] for r in parents}
    assert 'German Shepherd | Personalised Dog Ornament' in {r['Name'] for r in parents}
    assert len(list(root.rglob('product_info.json')))==2


def rescan(core,name,monkeypatch):
    app,_,root,output,*_=core
    notices=[];monkeypatch.setattr('app.utils.ingest.notify_ingest_product',lambda *a,**k:notices.append(k))
    with app.app_context():
        rows=scan_collection(root/name,'https://invalid.example/',output,force_update=True,update_csv=True,reset_sku_index=False,log=lambda *a,**k:None)
        result=ingest_rows_to_db(rows,log=lambda *a,**k:None)
        assert result['products_failed']==0
    assert not notices


def test_old_index_projection_matches_snapshot_list_detail_and_does_not_write(core,monkeypatch):
    app,client,root,_,ids,*_=core
    shared=root/'Simple Collection/product_info.json';data=json.loads(shared.read_text());data.update(title='Retro Gaming Cards',live=False);shared.write_text(json.dumps(data))
    child=root/'Simple Collection/Blue Token/product_info.json';data=json.loads(child.read_text());data.pop('title',None);child.write_text(json.dumps(data))
    files={f:f.read_bytes() for f in root.rglob('product_info.json')}
    with app.app_context():
        product=db.session.get(Product,ids['Simple Collection/Blue Token']);product.title='Outdated indexed title';db.session.commit()
        assert current_title(product)=='Blue Token | Retro Gaming Cards'
        assert catalogue_snapshot(product).data['title']=='Blue Token | Retro Gaming Cards'
        assert local_plan(product)['title']=='Blue Token | Retro Gaming Cards'
        assert product.title=='Outdated indexed title' and not db.session.dirty
    listing=client.get('/api/edit_products').json
    assert 'Blue Token | Retro Gaming Cards' in [p['title'] for g in listing['groups'] for p in g['products']]
    assert 'Blue Token | Retro Gaming Cards' in client.get(f'/products/{product.id}').text
    assert all(f.read_bytes()==v for f,v in files.items())
    rescan(core,'Simple Collection',monkeypatch)
    with app.app_context():assert db.session.get(Product,product.id).title=='Blue Token | Retro Gaming Cards'


@pytest.mark.parametrize('name,kind',[('Simple Collection/Blue Token','override'),('Variable Collection/Badge One','override'),('Single Variable','shared')])
def test_customisation_only_save_keeps_absent_title_and_templates(core,monkeypatch,name,kind):
    from app import customisation_templates
    app,client,root,_,ids,*_=core
    source=root/name/'product_info.json'
    data=json.loads(source.read_text());data.pop('title',None)
    with app.app_context():template=customisation_templates.save({'name':'Fictional title test','definition':{'fields':{'name':{'type':'text','label':'Name'}}}})
    data['customisation']={'template':{'id':template['id'],'revision':1}};source.write_text(json.dumps(data))
    with app.app_context():
        from app.shopify_products import source_fingerprint
        product=db.session.get(Product,ids[name]);before=product_document(product)['title'];sku=product.sku
        fingerprint=source_fingerprint(product,[])
    page=client.get(f'/edit_products/{product.id}/edit/{kind}').text
    assert 'data-title-preview' in page and 'Optional authored contribution' in page
    result=save(core,name,kind=kind,data={'customisation':{'fields':{'name':{'max_length':20}}}})
    assert result.status_code==200,result.json
    authored=json.loads(source.read_text())
    assert 'title' not in authored and authored['customisation']['template']=={'id':template['id'],'revision':1}
    rescan(core,name if kind=='shared' else name.rsplit('/',1)[0],monkeypatch)
    with app.app_context():
        product=db.session.get(Product,product.id)
        assert product.sku==sku and product.title==before==product_document(product)['title']
        assert source_fingerprint(product,[])==fingerprint


def test_shared_title_save_and_projection_updates_children_without_replacing_their_json_or_ids(core,monkeypatch):
    app,client,root,_,ids,*_=core
    with app.app_context():
        products=Product.query.filter_by(collection_id=db.session.get(Product,ids['Simple Collection/Blue Token']).collection_id).all()
        identities={p.id:(p.sku,p.source_relpath,[(v.id,v.sku) for v in p.variations]) for p in products}
        for p in products:db.session.add(ShopifyProductIdentity(store_id='gid://shopify/Shop/123',store_key='fictional',store_domain='fictional.myshopify.com',product_id=p.id,source_identity=p.source_relpath,sku=p.sku,shopify_product_id=f'gid://shopify/Product/{p.id}',state='verified',reviewed_digest='kept',media_evidence_json='{}'))
        db.session.commit()
        remote={p.id:p.shopify_product_id for p in ShopifyProductIdentity.query.all()}
    children={f:f.read_bytes() for f in (root/'Simple Collection').glob('*/product_info.json')}
    result=save(core,kind='shared',data={'title':'Personalised Dog Decorations'})
    assert result.status_code==200 and result.json['scan_pending']
    rescan(core,'Simple Collection',monkeypatch)
    with app.app_context():
        for id,(sku,path,variants) in identities.items():
            p=db.session.get(Product,id)
            assert (p.sku,p.source_relpath,[(v.id,v.sku) for v in p.variations])==(sku,path,variants)
            assert p.title.endswith(' | Personalised Dog Decorations')
        assert {p.id:p.shopify_product_id for p in ShopifyProductIdentity.query.all()}==remote
    assert all(f.read_bytes()==v for f,v in children.items())


def test_draft_title_preview_uses_canonical_server_and_never_authors_generated_title(core):
    app,client,root,_,ids,*_=core
    id=ids['Simple Collection/Blue Token'];source=root/'Simple Collection/Blue Token/product_info.json'
    before=source.read_bytes()
    result=client.post('/api/metadata/validate',json={'kind':'override','product_id':id,'data':{'title':'Level Complete'}})
    assert result.status_code==200 and result.json['title_preview']['title']=='Level Complete | Fictional Desk Token'
    assert source.read_bytes()==before and not core[5] and not core[6]
    result=client.post('/api/metadata/validate',json={'kind':'override','product_id':id,'data':{}})
    assert result.json['title_preview']['title']=='Blue Token | Fictional Desk Token'
    assert client.post('/api/metadata/validate',json={'kind':'override','product_id':[],'data':{}}).status_code==400


def test_title_update_uses_existing_signed_confirm_and_independent_readback(world):
    from app import shopify_drafts as drafts
    from app.shopify_connection import ShopifyError
    product,row,adapter=created(world)
    original=(row.shopify_product_id,copy.deepcopy(adapter.remote['media']))
    path=world[2];data=json.loads(path.read_text());data['title']='Reviewed collection component';path.write_text(json.dumps(data))
    plan,token=drafts.preview(product,adapter)
    with pytest.raises(ShopifyError,match='acknowledgement'):drafts.confirm(token,False,adapter)
    assert not any('update' in c or 'write' in c for c in adapter.calls)
    result=drafts.confirm(token,True,adapter)
    assert result.state=='verified' and adapter.remote['title']==plan['title']
    assert (result.shopify_product_id,adapter.remote['media'])==original
    assert 'product_create' not in adapter.calls and 'stage_write' not in adapter.calls


def test_retained_shopify_title_difference_is_reviewed_not_created_or_written(world):
    from app import shopify_drafts as drafts
    from app.shopify_products import source_fingerprint
    product,row,adapter=created(world)
    identity=(row.id,row.shopify_product_id,row.sku,row.source_identity)
    before=source_fingerprint(product,[])
    path=world[2];data=json.loads(path.read_text());data['title']='Corrected product title';path.write_text(json.dumps(data))
    plan,token=drafts.preview(product,adapter)
    assert plan['classification']=='update' and token
    title=next(r for r in plan['fields'] if r['field']=='title')
    assert title['changed'] and title['after']==product_document(product)['title']
    assert source_fingerprint(product,[])!=before
    assert (row.id,row.shopify_product_id,row.sku,row.source_identity)==identity
    assert not any('write' in c or 'update' in c or c=='product_create' for c in adapter.calls)
    adapter.remote['title']=title['after'];adapter.calls.clear()
    plan,token=drafts.preview(product,adapter)
    assert plan['classification']=='no_change' and token is None
    assert not any('write' in c or 'update' in c or c=='product_create' for c in adapter.calls)
