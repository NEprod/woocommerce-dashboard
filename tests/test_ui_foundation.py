"""Batch 1 presentation contracts; synthetic SQLite/catalogue only."""
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from html.parser import HTMLParser
from app import db
from app.models import Product, ShopifyProductIdentity
from app.orders_workspace import include_test_orders, overview
from app.deployment_guard import Identity, DEV_DOMAIN, DEV_SHOP
from test_phase2_dashboard import dashboard_app, dashboard_client
from test_metadata_editor_core import core, payload
from test_shopify_orders import orders_app, save, order
from test_setup_flow import setup_app

ROOT = Path(__file__).resolve().parents[1]

class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags=[]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag,dict(attrs)))

def proof(environment='development'):
    return (Identity(environment, 'a'*32, DEV_DOMAIN if environment=='development' else 'fixture.myshopify.com',
        DEV_SHOP if environment=='development' else 'gid://shopify/Shop/103940129151', 'gid://shopify/App/447856541697'), 'fixture', None)

@pytest.mark.parametrize('identity,expected', [(None,False), ('development',True), ('production',False)])
def test_test_order_default_requires_verified_environment(dashboard_app, identity, expected):
    app,_=dashboard_app
    with app.app_context():
        app.config['TLC_DEPLOYMENT_PROOF']=proof(identity) if identity else None
        assert include_test_orders({'environment':'development','dev':'1'}) is expected
        assert include_test_orders({'include_test':'0'}) is False
        assert include_test_orders({'include_test':'1'}) is True
        assert include_test_orders({'include_test':'bad'}) is expected

def test_orders_three_metrics_share_test_filter_and_existing_eligibility(orders_app):
    raw=order();raw['test']=True
    raw['createdAt']='2026-10-16T10:00:00Z';raw['updatedAt']='2026-10-16T11:00:00Z'
    save(raw)
    excluded=overview(include_test=False,as_of=datetime(2026,10,16,12,tzinfo=timezone.utc))
    included=overview(include_test=True,as_of=datetime(2026,10,16,12,tzinfo=timezone.utc))
    assert (excluded['unfulfilled'],excluded['items'],excluded['today'])==(0,0,0)
    assert included['today']==1
    assert included['unfulfilled']==1 and included['items']>0

def test_attention_all_filter_api_and_pagination(dashboard_client):
    response=dashboard_client.get('/api/edit_products?issue=all&per_page=25&page=1')
    assert response.status_code==200
    assert response.json['filter']['key']=='all'
    assert [p['sku'] for p in response.json['items']]==['CARD-002']
    assert dashboard_client.get('/api/edit_products?issue=all&q=not-present').json['items']==[]
    assert dashboard_client.get('/products?issue=all').status_code==200

def test_link_counts_require_store_bound_verified_identity(dashboard_app):
    from app.dashboard import verified_catalogue_links
    app,_=dashboard_app
    with app.app_context():
        assert verified_catalogue_links() is None
        app.config['TLC_DEPLOYMENT_PROOF']=proof()
        product=Product.query.filter_by(sku='CARD-001').one()
        row=ShopifyProductIdentity(product_id=product.id,source_identity=product.source_relpath,sku=product.sku,
            store_key='fixture',store_id=DEV_SHOP,store_domain=DEV_DOMAIN,
            shopify_product_id='gid://shopify/Product/123',state='verified',verification_state='verified',
            reviewed_digest='a'*64,media_evidence_json=json.dumps({'observed_status':'ACTIVE'}),verified_at=datetime.now())
        db.session.add(row);db.session.commit()
        assert verified_catalogue_links()['parents']==1
        assert verified_catalogue_links()['active']==1
        assert verified_catalogue_links()['unverified_parents']==2
        row.state='uncertain';db.session.commit()
        assert verified_catalogue_links()['parents']==0

def test_healthy_webhooks_do_not_show_warning(dashboard_app, monkeypatch):
    from flask import render_template
    app,_=dashboard_app
    sync=SimpleNamespace(error_category=None,error=None,webhook_configured=True,webhook_error=None,
        webhook_incomplete=0,last_successful=None)
    with app.test_request_context('/'):
        html=render_template('orders/overview.html',orders_overview=dict(unfulfilled=0,items=0,today=0,
            include_test=True,sync=sync,recent=[]))
    assert 'operator-warning' not in html
    assert 'Test orders included' in html

@pytest.mark.parametrize('kind',['shared','override'])
def test_editor_navigation_markets_and_shared_save_contract(core,kind):
    app,client,_,_,ids,*_=core
    product_id=ids['Simple Collection/Blue Token']
    result=client.get(f'/edit_products/{product_id}/edit/{kind}')
    assert result.status_code==200
    tags=Elements(result.get_data(as_text=True)).tags
    assert next(tag for tag,attrs in tags if 'data-market-editor' in attrs)=='section'
    assert any(attrs.get('data-field-wrapper')=='description' and 'editor-field-wide' in attrs.get('class','') for _,attrs in tags)
    ids={attrs['id'] for _,attrs in tags if 'id' in attrs}
    for tag,attrs in tags:
        if tag=='a' and attrs.get('href','').startswith('#'):
            assert attrs['href'][1:] in ids, attrs['href']
    acknowledgements=[attrs for _,attrs in tags if 'data-acknowledge-shared' in attrs]
    assert len(acknowledgements)==(2 if kind=='shared' else 0)
    assert any('metadata-mobile-savebar' in attrs.get('class','') for _,attrs in tags)
    assert any('data-retry-local-scan' in attrs for _,attrs in tags)

def test_scoped_scan_still_requires_shared_acknowledgement(core):
    sku,body=payload(core,'Simple Collection/Blue Token',kind='shared')
    body.update(retry_scan=True,acknowledge_shared=False)
    before={str(p):p.read_bytes() for p in core[2].rglob('*.json')}
    response=core[1].post(f'/edit_products/{sku}/save',json=body)
    assert response.status_code==400 and response.json['error']=='shared_confirmation_required'
    assert {str(p):p.read_bytes() for p in core[2].rglob('*.json')}==before
