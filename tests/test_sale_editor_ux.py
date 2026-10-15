"""Saved-authority handoff only; fictional catalogues and mocked Shopify."""
import copy
import json
import os
import re
from datetime import datetime, timedelta

import pytest
from app import db, sale_editor
from app.metadata_save import review_token, source
from app.models import Product, CatalogueOperation, CatalogueOperationItem, ShopifySaleSchedule, User
from app.metadata_edit import MetadataEditConflict
from test_metadata_editor_core import core, save
from test_shopify_publication_sales import world, preview_app, clock, published


def request_body(core, name='Simple Collection/Blue Token', kind='override', action='status'):
    with core[0].app_context():
        product=db.session.get(Product,core[4][name])
        return {'action':action,'kind':kind,'revision':review_token(product,kind),
            'data':json.loads(source(product,kind)[1])}


def post(core, body, name='Simple Collection/Blue Token'):
    return core[1].post(f'/products/{core[4][name]}/shopify/sale/editor',json=body)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr('app.shopify_connection.ShopifyAdapter._request',lambda *a,**k:pytest.fail('Unexpected Shopify request'))
    monkeypatch.setenv('DISCORD_ENABLED','false')
    monkeypatch.setattr('app.utils.discord._post',lambda *a,**k:(False,'disabled in isolated test'))


@pytest.mark.parametrize('name,kind',[('Simple Collection/Blue Token','override'),('Variable Collection/Badge One','shared'),('Single Variable','shared')])
def test_saved_current_scanned_json_is_ready_for_all_layouts(core,name,kind):
    result=post(core,request_body(core,name,kind),name)
    assert result.status_code==200,result.json
    assert result.json['ready'] and 'projection' in result.json['message']


@pytest.mark.parametrize('change',['draft','source','shared','revision','invalid'])
def test_unsaved_or_stale_source_rejected_before_shopify(core,change):
    body=request_body(core,action='preview')
    target=core[2]/'Simple Collection/Blue Token/product_info.json'
    if change=='draft':body['data']['sale_price']='1.00'
    elif change=='revision':body['revision']+='broken'
    else:
        if change=='shared':target=core[2]/'Simple Collection/product_info.json'
        document=json.loads(target.read_text());document['sale_start_date']='not-a-date' if change=='invalid' else '2027-01-01'
        target.write_text(json.dumps(document))
    result=post(core,body)
    assert result.status_code==409 and not result.json['ready']
    with core[0].app_context():assert ShopifySaleSchedule.query.count()==0


@pytest.mark.parametrize('marker',['.update','.scanned.pending','older_marker','no_marker'])
def test_pending_or_missing_projection_blocks_review(core,marker):
    folder=core[2]/'Simple Collection/Blue Token'
    if marker=='older_marker':os.utime(folder/'.scanned',ns=(1,1))
    elif marker=='no_marker':(folder/'.scanned').unlink()
    else:(folder/marker).write_text('pending')
    result=post(core,request_body(core,action='preview'))
    assert result.status_code==409 and 'projection' in result.json['error']


def test_saved_json_with_failed_scan_stays_blocked_even_with_current_marker(core):
    with core[0].app_context():
        product=db.session.get(Product,core[4]['Simple Collection/Blue Token'])
        db.session.add(CatalogueOperation(id='f'*32,operation_type='product_update',status='failed',
            scope=json.dumps({'local_save':True,'sku':product.sku}),finished_at=datetime.utcnow()))
        db.session.commit()
    result=post(core,request_body(core,action='preview'))
    assert result.status_code==409 and 'recovery' in result.json['error']


def test_modal_review_and_confirmation_are_source_bound_and_explicit(core,monkeypatch):
    calls=[]
    plan={'variants':[{'sku':'TLC-1','regular':'8.50','sale':'6.00'}],
        'start':'2026-07-01T11:00:00','end':'2026-12-01T12:00:00'}
    monkeypatch.setattr('app.shopify_sales.review',lambda product,adapter=None:(plan,'signed-inner-sale-authority'))
    def authorise(product,token,acknowledged,adapter=None):
        if not acknowledged:raise ValueError('Explicit authorisation required')
        calls.append(token)
    monkeypatch.setattr('app.shopify_sales.authorise',authorise)
    body=request_body(core,action='preview');response=post(core,body)
    assert response.status_code==200,response.json
    assert response.json['start']=='01/07/2026 12:00 BST' and response.json['end']=='01/12/2026 12:00 GMT'
    assert not calls
    confirm={**body,'action':'confirm','review':response.json['review'],'acknowledge':False}
    assert post(core,confirm).status_code==409 and not calls
    assert post(core,{**confirm,'acknowledge':True,'review':response.json['review']+'bad'}).status_code==409
    assert post(core,{**confirm,'acknowledge':True}).status_code==200 and calls==['signed-inner-sale-authority']
    calls.clear()
    (core[2]/'Simple Collection/Blue Token/product_info.json').write_text('{"sale_price":"1.00"}')
    assert post(core,{**confirm,'acknowledge':True}).status_code==409 and not calls


def test_scan_finishes_before_review_can_be_enabled(core,monkeypatch):
    from app.utils.scan_runner import start_scan, get_progress
    import time
    monkeypatch.setattr('app.routes.start_scan',start_scan)
    response=save(core,data={'sale_price':'1.00','sale_start_date':'2027-12-01T12:00:00.000Z'})
    assert response.status_code==200,response.json
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        with core[0].app_context():progress=get_progress(response.json['run_id'])
        if progress['status'] in {'done','error'}:break
        time.sleep(.03)
    assert progress['status']=='done',progress
    result=post(core,request_body(core))
    assert result.status_code==200,result.json
    with core[0].app_context():assert ShopifySaleSchedule.query.count()==0


def test_legacy_review_cannot_bypass_saved_editor_guard(core):
    result=core[1].post(f'/products/{core[4]["Simple Collection/Blue Token"]}/shopify/sale',data={'action':'preview'})
    assert result.status_code==400 and b'Product Editor' in result.data


def test_modal_endpoint_requires_admin_and_csrf(core):
    body=request_body(core)
    core[0].config['WTF_CSRF_ENABLED']=True
    assert post(core,body).status_code==400
    page=core[1].get(f'/edit_products/{core[4]["Simple Collection/Blue Token"]}/edit/override').text
    csrf=re.search(r'<meta name="csrf-token" content="([^"]+)"',page).group(1)
    url=f'/products/{core[4]["Simple Collection/Blue Token"]}/shopify/sale/editor'
    assert core[1].post(url,json=body,headers={'X-CSRFToken':csrf}).status_code==200
    with core[0].app_context():User.query.first().is_admin=False;db.session.commit()
    assert core[1].post(url,json=body,headers={'X-CSRFToken':csrf}).status_code==403


def test_editor_exposes_combined_dates_clock_and_modal_without_remote_render(core):
    page=core[1].get(f'/edit_products/{core[4]["Simple Collection/Blue Token"]}/edit/override').text
    for content in ('DD/MM/YYYY HH:mm','data-sale-clock','data-sale-now','data-sale-clear','data-review-sale disabled',
                    'id="saleReviewModal"','data-sale-authorise disabled','sale-editor.js'):
        assert content in page
    assert 'Review / Authorise Sale</a>' not in page


def test_real_saved_review_wraps_existing_authority_without_any_price_mutation(world,published):
    product,row,adapter,notices=published
    document=json.loads(world[2].read_text())
    document.update(sku_prefix='TLC-',sale_price='3.00',sale_start_date='2026-11-01T10:00:00.000Z',sale_end_date='2026-11-02T10:00:00.000Z')
    world[2].write_text(json.dumps(document))
    (world[1]/'.scanned').write_text(json.dumps({'sku':product.sku}))
    payload={'kind':'shared','revision':review_token(product,'shared'),'data':document}
    preview=sale_editor.review(product,payload,adapter)
    assert ShopifySaleSchedule.query.count()==0 and not notices
    result=sale_editor.authorise(product,{**payload,'review':preview['review'],'acknowledge':True},adapter)
    assert result['authorised'] and ShopifySaleSchedule.query.one().state=='authorised'
    assert not any('write' in call or 'create' in call for call in adapter.calls)
    assert not notices and row.shopify_product_id=='gid://shopify/Product/100'


def test_source_change_during_remote_review_cannot_receive_authority(core,monkeypatch):
    body=request_body(core,action='preview')
    def changed(product,adapter=None):
        target=core[2]/'Simple Collection/Blue Token/product_info.json'
        target.write_text(json.dumps({**body['data'],'sale_price':'1.00'}))
        return {'variants':[],'start':None,'end':None},'old-inner-review'
    monkeypatch.setattr('app.shopify_sales.review',changed)
    response=post(core,body)
    assert response.status_code==409 and 'review' not in response.json
    with core[0].app_context():assert ShopifySaleSchedule.query.count()==0


def test_current_revision_does_not_make_invalid_saved_json_publishable(core):
    target=core[2]/'Simple Collection/Blue Token/product_info.json'
    target.write_text('{"requires_shipping":"false"}')
    result=post(core,request_body(core,action='preview'))
    assert result.status_code==409 and 'invalid' in result.json['error']


def test_running_projection_receipt_blocks_even_when_marker_is_present(core):
    with core[0].app_context():
        db.session.add(CatalogueOperation(id='a'*32,operation_type='product_update',status='running',scope='{}'))
        db.session.commit()
    result=post(core,request_body(core,action='preview'))
    assert result.status_code==409 and 'operation is active' in result.json['error']


@pytest.mark.parametrize('left,right,equal',[
    ({'price':10.0,'nested':[1.0]},{'price':10,'nested':[1]},True),
    ({'requires_shipping':False},{'requires_shipping':0},False),
    ({'price':'10.00'},{'price':10},False)])
def test_browser_json_numeric_roundtrip_does_not_invent_unsaved_changes(left,right,equal):
    assert sale_editor.same_json(left,right) is equal
