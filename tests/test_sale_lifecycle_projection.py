"""Fictional sale/scan/status timeline; no live catalogue, API or webhook access."""
import copy
import json
from pathlib import Path
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from flask import current_app

from app import db, shopify_drafts as drafts, shopify_sales as sales
from app.models import Product, ShopifyProductComparison
from app.products_browser import build_products_data, parse_products_filters
from app.metadata_workspace import product_workspace
from app.shopify_products import states_for
from test_shopify_drafts import world
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_publication_sales import clock, published, approve
from test_shopify_simple_updates import change
from test_shopify_native_dimensions import NativeAdapter, enable
from test_shopify_publication_sales import SafetyMixin
from test_metadata_editor_core import core
from app.sale_pricing import catalogue, project, instant
from app.utils.scanner import scan_collection
from app.utils.ingest import ingest_rows_to_db
from app import shopify_status, shopify_comparison


class LifecycleAdapter(SafetyMixin, NativeAdapter):
    def product_revisions(self, config, ids):
        self.calls.append('revision_read')
        return {i:{'id':i, 'status':self.remote['status'], 'updatedAt':self.remote.get('updatedAt','2026-10-12T00:00:00Z')} for i in ids}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError('No real API/webhook requests permitted')
    monkeypatch.setattr('requests.sessions.Session.request', forbidden)
    monkeypatch.setenv('DISCORD_ENABLED', 'false')


def browser(product):
    with current_app.test_request_context():
        return next(p for p in build_products_data(parse_products_filters({}))['items'] if p['id'] == product.id)


def test_expired_configured_sale_is_not_current_catalogue_price(world, clock):
    product = world[0]
    change(world, sale_price='1.50', sale_start_date='2026-10-15T15:00:00Z', sale_end_date='2026-10-15T16:00:00Z')
    product.sale_price = Decimal('1.50')
    db.session.commit()
    clock[0] = datetime(2026, 10, 15, 16, 4)
    assert browser(product)['price']['minimum'] == '3.50'
    with current_app.test_request_context():
        assert product_workspace(product)['price_min'] == '3.50'


def test_ten_minute_native_expiry_is_stale_not_needs_attention(world, clock, monkeypatch):
    clock[0] = datetime(2026, 10, 15, 16, 2)
    enable(monkeypatch)
    change(world, weight='50', dimensions={'length':155, 'width':155, 'height':3})
    adapter = NativeAdapter()
    _, token = drafts.preview(world[0], adapter)
    row = drafts.confirm(token, True, adapter)
    clock[0] = datetime(2026, 10, 15, 16, 3, 11, 760818)
    drafts.preview(world[0], adapter)
    with current_app.test_request_context():
        assert states_for([world[0]])[world[0].id]['label'] == 'Up to Date'
    clock[0] = datetime(2026, 10, 15, 16, 14)
    with current_app.test_request_context():
        state = states_for([world[0]])[world[0].id]
        assert state['label'] == 'Last check matched'
        assert state['evidence_stale'] is True
    assert row.state == 'verified'


def test_1703_1708_1714_expiry_scan_and_reconciliation(world, clock, monkeypatch):
    """BST timeline reproduces independent display and native-evidence expiry defects."""
    enable(monkeypatch)
    clock[0] = datetime(2026,10,15,15,50)
    product, folder, shared = world
    change(world, sku_prefix='TEST-', sale_price='1.50', sale_start_date='2026-10-15T15:55:00Z',
        sale_end_date='2026-10-15T16:00:00Z', weight='50', dimensions={'length':155,'width':155,'height':3})
    (folder/'.scanned').write_text(json.dumps({'sku':product.sku}))
    from app.models import Settings
    output=Path(db.session.query(Settings).first().product_folder).parent/'processed'
    output.mkdir()
    logs=[]
    def rescan():
        rows=scan_collection(str(folder.parent),'https://invalid.example/',str(output),
            force_update=True,update_csv=True,reset_sku_index=False,log=lambda message,**kw:logs.append(message))
        assert rows
        result=ingest_rows_to_db(rows,log=lambda *a,**kw:None)
        assert result['products_failed']==0
        db.session.expire_all()
    rescan()
    adapter=LifecycleAdapter()
    notices=[]
    monkeypatch.setattr('app.shopify_notifications.deliver',lambda op,event,**kw:notices.append((op,event)))
    _,token=drafts.preview(product,adapter);row=drafts.confirm(token,True,adapter)
    notices.clear()
    _,token=sales.review(product,adapter);authority=sales.authorise(product,token,True,adapter)
    identity=(row.shopify_product_id,drafts.evidence(row)['variant_id'],drafts.evidence(row)['inventory_item_id'])
    authored=shared.read_bytes()
    originals={p.name:p.read_bytes() for p in folder.iterdir() if p.suffix in {'.png','.jpg'}}
    assert browser(product)['price']['minimum']=='3.50'
    assert browser(product)['price']['label']=='Scheduled'
    clock[0]=datetime(2026,10,15,15,55)
    assert sales.transition(authority,adapter)=='active'
    assert browser(product)['price']['minimum']=='1.50'
    current=adapter.remote['simpleVariants']['nodes'][0]
    assert (current['price'],current['compareAtPrice'])==('1.50','3.50')
    clock[0]=datetime(2026,10,15,16)
    assert sales.transition(authority,adapter)=='completed'
    assert (current['price'],current['compareAtPrice'])==('3.50',None)
    clock[0]=datetime(2026,10,15,16,3,11,760818)
    plan,_=drafts.preview(product,adapter)
    assert plan['classification']=='no_change'
    clock[0]=datetime(2026,10,15,16,4)
    assert browser(product)['shopify']['label']=='Up to Date'
    assert browser(product)['price']['minimum']=='3.50'
    fingerprint=shopify_comparison.fingerprint(product,plan['images'])
    before_calls=list(adapter.calls)
    clock[0]=datetime(2026,10,15,16,8)
    rescan()
    assert shopify_comparison.fingerprint(product,plan['images'])==fingerprint
    assert adapter.calls==before_calls
    assert browser(product)['shopify']['label']=='Up to Date'
    assert product.sale_price==Decimal('1.50')  # configured amount remains indexed
    clock[0]=datetime(2026,10,15,16,14)
    displayed=browser(product)
    assert displayed['price']['minimum']=='3.50' and displayed['price']['label']=='Ended'
    assert displayed['shopify']['label']=='Last check matched'
    result=shopify_status.refresh([product],adapter=adapter)
    assert result['full_comparisons']==1 and not result['errors']
    assert browser(product)['shopify']['label']=='Up to Date'
    with current_app.test_request_context(): assert product_workspace(product)['price_min']=='3.50'
    assert adapter.remote['status']=='DRAFT' and not getattr(adapter,'memberships',set())
    assert identity==(row.shopify_product_id,drafts.evidence(row)['variant_id'],drafts.evidence(row)['inventory_item_id'])
    assert shared.read_bytes()==authored and json.loads(authored)['sale_price']=='1.50'
    assert originals=={p.name:p.read_bytes() for p in folder.iterdir() if p.name in originals}
    assert len(notices)==2 and len(set(notices))==2
    assert any('Assigned SKU' in line for line in logs)
    change(world,description='Genuinely changed authored copy')
    assert browser(product)['shopify']['label']=='Checking Changes'
    assert not shopify_status.refresh([product],adapter=adapter,force=True)['errors']
    assert browser(product)['shopify']['label']=='Update Available' and len(notices)==2


@pytest.mark.parametrize('at,state,price',[
    (datetime(2026,10,15,15,59),'scheduled','3.50'),
    (datetime(2026,10,15,16),'active','1.50'),
    (datetime(2026,10,15,16,59,59),'active','1.50'),
    (datetime(2026,10,15,17),'expired','3.50')])
def test_catalogue_uses_exact_resolver_boundaries(world,clock,at,state,price):
    change(world,sale_price='1.50',sale_start_date='2026-10-15T17:00+01:00',sale_end_date='2026-10-15T18:00+01:00')
    clock[0]=at
    result=catalogue(world[0])
    assert result['minimum']==price and result['state']==state
    assert result['configured_sale_minimum']=='1.50' and result['regular_minimum']=='3.50'


@pytest.mark.parametrize('layout',['Simple Collection/Blue Token','Variable Collection/Badge One','Single Variable'])
@pytest.mark.parametrize('phase',['scheduled','active','expired'])
def test_all_layout_scans_retain_configured_schedule_and_current_prices(core,clock,layout,phase):
    app,_,root,output,ids,*_=core
    path=root/layout
    owner=path/'product_info.json'
    if layout=='Single Variable':
        shared=json.loads(owner.read_text())
    else: shared=json.loads(owner.read_text()) if owner.exists() else {}
    shared.update(sale_price='1.50',sale_start_date='2026-10-15T16:00Z',sale_end_date='2026-10-15T17:00Z')
    owner.write_text(json.dumps(shared))
    clock[0]={'scheduled':datetime(2026,10,15,15),'active':datetime(2026,10,15,16),'expired':datetime(2026,10,15,17)}[phase]
    with app.app_context():
        p=db.session.get(Product,ids[layout])
        before=(p.sku,[(v.sku,v.source_identity) for v in p.variations])
        original=owner.read_bytes()
        rows=scan_collection(str(path if layout=='Single Variable' else path.parent),'https://invalid.example/',str(output),force_update=True,update_csv=True,reset_sku_index=False,log=lambda *a,**kw:None)
        assert ingest_rows_to_db(rows,log=lambda *a,**kw:None)['products_failed']==0
        db.session.expire_all()
        assert before==(p.sku,[(v.sku,v.source_identity) for v in p.variations])
        pricing=catalogue(p)
        assert pricing['state']==phase
        assert pricing['minimum']==('1.50' if phase=='active' else pricing['regular_minimum'])
        assert owner.read_bytes()==original
        assert browser(p)['price']['minimum']==pricing['minimum']


def test_old_comparison_and_failure_cannot_replace_newer_checkpoint(world,published,clock):
    product,row,adapter,_=published
    clock[0]+=timedelta(minutes=1)
    old,_=drafts.preview(product,adapter)
    clock[0]+=timedelta(minutes=1)
    drafts.preview(product,adapter)
    saved=ShopifyProductComparison.query.one()
    current=saved.summary_json
    assert shopify_comparison.record(product,old,row) is False
    shopify_comparison.failed(product,'Old response failed',started=old['comparison_started_at'])
    assert saved.summary_json==current


def test_old_identity_comparison_cannot_erase_uncertain_write(world,published):
    product,row,adapter,_=published
    plan,_=drafts.preview(product,adapter)
    saved=ShopifyProductComparison.query.one().summary_json
    data=drafts.evidence(row);data['stage']='uncertain_newer_operation'
    drafts.save(row,data,'uncertain')
    assert shopify_comparison.record(product,plan,row) is False
    assert ShopifyProductComparison.query.one().summary_json==saved
    assert browser(product)['shopify']['label']=='Recovery Required'


def test_local_revalidation_endpoint_is_bounded_read_only_and_current(world,clock):
    product=world[0]
    change(world,sale_price='1.50',sale_end_date='2026-10-15T16:00Z')
    clock[0]=datetime(2026,10,15,16)
    client=_client(current_app._get_current_object())
    response=client.get(f'/api/products/pricing?ids={product.id}')
    assert response.status_code==200 and response.headers['Cache-Control']=='no-store'
    assert response.json['items'][0]['price']['minimum']=='3.50'
    assert response.json['items'][0]['price']['label']=='Ended'
    assert client.get('/api/products/pricing?ids=1,1').status_code==400
    assert client.get('/api/products/pricing?ids='+','.join(map(str,range(1,102)))).status_code==400


@pytest.mark.parametrize('metadata,at,price,state',[
    ({},datetime(2026,10,15),'3.50','none'),
    ({'sale_price':'0.00'},datetime(2026,10,15),'0.00','active'),
    ({'sale_price':'0.10'},datetime(2026,10,15),'0.10','active'),
    ({'sale_price':'1.50','sale_end_date':'2026-10-25'},datetime(2026,10,25,23,59),'1.50','active'),
    ({'sale_price':'1.50','sale_end_date':'2026-10-25'},datetime(2026,10,26),'3.50','expired'),
    ({'sale_price':'1.50','sale_end_date':'2026-03-29'},datetime(2026,3,29,23),'3.50','expired'),
    ({'sale_price':'1.50','sale_start_date':'2026-10-25T01:30+01:00'},datetime(2026,10,25,0,30),'1.50','active'),
    ({'sale_price':'1.50','sale_start_date':'2026-10-25T01:30'},datetime(2026,10,25),'Needs Attention','invalid')])
def test_display_contract_open_ended_decimal_and_uk_dst(world,clock,metadata,at,price,state):
    change(world,**metadata);clock[0]=at
    before=world[2].read_bytes()
    result=catalogue(world[0])
    assert result['state']==state
    assert (result['label'] if state=='invalid' else result['minimum'])==price
    assert world[2].read_bytes()==before


def test_products_detail_editor_and_json_agree_after_expiry(world,clock):
    product=world[0];change(world,sale_price='1.50',sale_end_date='2026-10-15T16:00Z')
    clock[0]=datetime(2026,10,15,16,4)
    client=_client(current_app._get_current_object())
    for path in (f'/products/{product.id}',f'/edit_products/{product.id}/edit/shared'):
        response=client.get(path)
        assert response.status_code==200
        assert 'Sale: Ended' in response.text and 'data-sale-effective>£3.50' in response.text
        assert 'data-sale-configured>£1.50' in response.text
    response=client.get('/api/edit_products')
    item=next(i for i in response.json['items'] if i['id']==product.id)
    assert item['price']['minimum']=='3.50' and item['price']['configured_sale_minimum']=='1.50'


@pytest.mark.parametrize('at,price',[(datetime(2026,10,25,23),'1.50'),(datetime(2026,10,26),'3.50')])
def test_indexed_raw_dates_preserve_inclusive_end_when_source_is_unavailable(world,clock,at,price):
    product=world[0];product.regular_price=Decimal('3.50');product.sale_price=Decimal('1.50')
    product.resolved_row_json=json.dumps({'Date sale price ends':'2026-10-25'})
    clock[0]=at
    result=catalogue(product,authored={})
    assert result['minimum']==price and result['configured_sale_minimum']=='1.50'


@pytest.mark.parametrize('method',['record','summary'])
def test_comparison_commit_cannot_clobber_another_connection(world,published,monkeypatch,method):
    from sqlalchemy import update
    product,row,adapter,_=published
    plan,_=drafts.preview(product,adapter)
    saved=ShopifyProductComparison.query.one()
    original=saved.summary_json
    newer=json.dumps({**json.loads(original),'newer_connection_evidence':True})
    def newer_commit():
        with db.engine.begin() as connection:
            connection.execute(update(ShopifyProductComparison).where(ShopifyProductComparison.id==saved.id).values(summary_json=newer))
    if method=='record':
        monkeypatch.setattr('app.shopify_capacity.fence_owner',newer_commit)
        assert shopify_comparison.record(product,plan,row) is False
    else:
        newer_commit()
        assert shopify_comparison.store_summary(saved,json.loads(original)) is False
    db.session.expire_all()
    assert json.loads(saved.summary_json)['newer_connection_evidence'] is True
