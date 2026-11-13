"""Synthetic order/cart properties. No live Worker, photos or Shopify requests."""
import json
from datetime import timedelta

import pytest
from app import db, customer_photos as photos, shopify_orders as service
from app.models import ShopifyOrder, ShopifyOrderLine, User
from app.shopify_customisations import check_photo_service
from app.shopify_connection import ShopifyError
from app.storefront_customisations import public_configuration
from test_shopify_orders import STORE, ORDER, REV, client, order, orders_app
from test_setup_flow import setup_app

REFS = ['tlcp1_' + char*64 for char in 'bc']
CART = 'fictional-cart-token'
CFG = {'TLC_PHOTO_WORKER_URL':'https://photos.example.test',
       'TLC_PHOTO_WORKER_TOKEN':'fictional_dashboard_bearer_for_tests',
       'TLC_PHOTO_PROXY_PATH':'/apps/tlc-customer-uploads'}


def fixture_order(*, duplicate=False, bad_revision=False):
    raw=order()
    raw['_lines']=raw.pop('lineItems')['nodes']
    raw['cartToken']=CART+'?key=never-retain-this'
    for index,line in enumerate(raw['_lines']):
        props=line['customAttributes']
        snapshot=json.loads(props[3]['value'])
        snapshot['fields'].append({'id':'reference','label':'Photo','type':'file_upload',
            'value':REFS[0 if duplicate else index],'property_key':'Photo','display':'Reference photo supplied'})
        props[3]['value']=json.dumps(snapshot)
        props.append({'key':'Photo','value':'Reference photo supplied'})
        if bad_revision: props[2]['value']='z'*64
    return service.normalise(raw,api_version='2026-10')


def test_verified_order_binding_idempotent_and_download_is_staff_and_line_scoped(orders_app,monkeypatch):
    app,_=orders_app
    app.config.update(CFG)
    calls=[]
    def worker(endpoint,data=None,*,download=False):
        calls.append((endpoint,data))
        if endpoint=='bind':
            assert data['cart']==photos.cart_digest(CART)
            assert len(data['items'])==2
            assert {i['variant'] for i in data['items']}=={'200'}
            assert {i['ref'] for i in data['items']}==set(REFS)
            assert {i['line'] for i in data['items']}=={'gid://shopify/LineItem/1','gid://shopify/LineItem/2'}
            return {'accepted':REFS}
        assert download and data=={'shop':STORE,'order':ORDER,'line':'gid://shopify/LineItem/1','ref':REFS[0]}
        return b'fixture', 'image/png'
    monkeypatch.setattr(photos,'_request',worker)
    raw,lines=fixture_order()
    for i in range(2):
        assert service.persist(STORE,raw,lines)
        db.session.commit()
    saved=ShopifyOrder.query.one()
    first,second=saved.lines
    assert [json.loads(l.personalisation_json)['photo_verified'] for l in saved.lines]==[[ref] for ref in REFS]
    assert 'never-retain-this' not in first.properties_json+first.personalisation_json
    assert ShopifyOrderLine.query.count()==2
    route=f'/orders/{saved.id}/lines/{first.id}/photo/reference'
    staff=client(app)
    assert staff.get(f'/orders/{saved.id+1}/lines/{first.id}/photo/reference').status_code==404
    assert staff.get(f'/orders/{saved.id}/lines/{second.id}/photo/other').status_code==404
    response=staff.get(route)
    assert response.status_code==200 and response.data==b'fixture'
    assert response.headers['Content-Disposition']=='attachment; filename="customer-photo.png"'
    assert response.headers['Cache-Control']=='no-store, private'
    assert staff.get(f'/orders/{saved.id}').text.count('Download customer photo')==2
    assert [endpoint for endpoint,_ in calls]==['bind','photo']
    def unavailable(*a,**kw): raise photos.Unavailable()
    monkeypatch.setattr(photos,'_request',unavailable)
    assert staff.get(route).status_code==503
    raw['updated_at']+=timedelta(minutes=1)
    assert service.persist(STORE,raw,lines)
    db.session.commit()
    assert json.loads(db.session.get(ShopifyOrderLine,first.id).personalisation_json)['photo_verified']==[REFS[0]]
    db.session.get(User,1).is_admin=False
    db.session.commit()
    assert staff.get(route).status_code==403


def test_anonymous_cannot_download(orders_app):
    app,_=orders_app
    assert app.test_client().get('/orders/1/lines/1/photo/reference').status_code in {302,401}


@pytest.mark.parametrize('problem',['duplicate','bad_revision','no_cart','no_config','unavailable','not_accepted'])
def test_unverified_uploads_remain_non_downloadable_and_do_not_break_import(orders_app,monkeypatch,problem):
    app,_=orders_app
    if problem!='no_config':app.config.update(CFG)
    raw,lines=fixture_order(duplicate=problem=='duplicate',bad_revision=problem=='bad_revision')
    if problem=='no_cart':raw['_photo_cart']=None
    def worker(endpoint,*a,**kw):
        assert problem in {'unavailable','not_accepted'} and endpoint=='bind'
        if problem=='unavailable':raise photos.Unavailable()
        return {'accepted':[]}
    monkeypatch.setattr(photos,'_request',worker)
    assert service.persist(STORE,raw,lines)
    db.session.commit()
    saved=ShopifyOrder.query.one()
    for line in saved.lines:
        assert not json.loads(line.personalisation_json).get('photo_verified')
        assert client(app).get(f'/orders/{saved.id}/lines/{line.id}/photo/reference').status_code==404


def test_projection_reuses_existing_definition_and_health_gate(orders_app,monkeypatch):
    app,_=orders_app
    text={'type':'text','label':'Name'}
    field={'type':'photo','enabled':True,'label':'Photo','required':True,'max_file_size_bytes':5_000_000}
    enabled={'storefront_enabled':True,'fields':{'reference':field,'name':text}}
    before={'storefront_enabled':True,'fields':{'name':text}}
    with pytest.raises(ValueError,match='secure upload'):public_configuration(enabled)
    assert public_configuration({**enabled,'fields':{'reference':{**field,'enabled':False},'name':text}})==public_configuration(before)
    app.config.update(CFG)
    spec=public_configuration(enabled,photo_upload=photos.settings()[0])
    assert spec['fields'][1]=={'type':'file_upload','id':'reference','label':'Photo','required':True,
        'max_bytes':5_000_000,'image_types':['image/jpeg','image/png','image/webp']}
    assert CFG['TLC_PHOTO_WORKER_TOKEN'] not in json.dumps(spec)
    fake={'ready':True,'shop':'fictional.myshopify.com','store':STORE,'path':CFG['TLC_PHOTO_PROXY_PATH']}
    monkeypatch.setattr(photos,'_request',lambda route:fake)
    check_photo_service(spec,'fictional.myshopify.com',STORE)
    with pytest.raises(ShopifyError,match='not ready'):check_photo_service(spec,'other.myshopify.com',STORE)
    fake['ready']=False
    with pytest.raises(ShopifyError,match='not ready'):check_photo_service(spec,'fictional.myshopify.com',STORE)
    check_photo_service(public_configuration(before),'fictional.myshopify.com',STORE)
