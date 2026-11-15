"""Local Promotions, separate credentials and mocked discount API only."""
import copy
import json
import re

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app import db
from app.promotions import domain, publish, shopify
from app.promotions.models import Promotion
from test_inventory import client_for, workshop
from test_setup_flow import setup_app
from test_phase3_woo_publish_preview import preview_app
from test_shopify_drafts import world
from test_shopify_variables import variable_world, VariableAdapter

STORE='gid://shopify/Shop/42'
APP='gid://shopify/App/321'
MARKET='gid://shopify/Market/22'
ID='gid://shopify/DiscountNode/22'


def cfg(**changes):
    return shopify.Configuration(**(dict(domain='fictional-dev.myshopify.com',store=STORE,app=APP,
        client='1'*32,kid='sample',private_key='/run/local/sample.pem',uk_market=MARKET)|changes))


def data(kind='percent', **changes):
    return dict(name='Fixture promotion',kind=kind,scope='product' if kind in ('exact','bxgy') else 'order',
                method='automatic',intent='live',starts='2020-01-01T00:00:00Z',ends=None,
                targets=[],combines={},minimum=1,value=1200,tiers=[{'qty':2,'total':900}],repeat=True,
                buy=2,get=1,limit=1,**{}) | changes


def new(kind='percent', **fields):
    row=Promotion(id='a'*32,document=domain.canonical(domain.definition(data(kind,**fields))),updated_at=publish.stamp())
    db.session.add(row)
    db.session.commit()
    return row


def local_update(row, **fields):
    row.document=domain.canonical(domain.definition(publish.document(row)|fields))
    row.revision+=1
    db.session.commit()


def connection(nodes):
    return {'nodes':nodes,'pageInfo':{'hasNextPage':False}}


def readback(e, *, status='ACTIVE'):
    flavour = publish._type(e)
    remote={'__typename':'Discount'+flavour, 'title':e['title'],'tags':[e['marker']],
            'status':status,'startsAt':e['starts'],'endsAt':e['ends'],'updatedAt':'2026-01-02T00:00:00Z',
            'combinesWith':e['combines'],
            'context':({'__typename':'DiscountCustomerSegments','segments':[{'id':e['segment']}]}
                if e.get('segment') else {'__typename':'DiscountCustomerAll'})}
    if e['method']=='code':
        remote.update(codes=connection([{'code':e['code']}]),usageLimit=e['usage'],appliesOncePerCustomer=e['once'])
    def variants(key):
        return {'__typename':'DiscountProducts','products':connection([]),
            'productVariants':connection([{'id':vid} for vid in e[key]])}
    if e['kind']=='exact':
        remote.update(context={'__typename':'DiscountMarkets','markets':connection([{'id':e['function']['market']}])},
            discountClasses=['PRODUCT'],appliesOnOneTimePurchase=True,appliesOnSubscription=False,
            appDiscountType={'appKey':e['function']['app_key'],'functionId':e['function']['id']})
    elif e['kind']=='bxgy':
        remote.update(usesPerOrderLimit=str(e['limit']),
            customerBuys={'items':variants('variants'),'value':{'quantity':str(e['buy'])},
                'isOneTimePurchase':True,'isSubscription':False},
            customerGets={'appliesOnOneTimePurchase':True,'appliesOnSubscription':False,'items':variants('rewards'),
                'value':{'quantity':{'quantity':str(e['get'])},'effect':{'percentage':e['value']/10000}}})
    else:
        remote.update(discountClasses=[e['scope'].upper()],minimumRequirement={'greaterThanOrEqualToQuantity':str(e['minimum'])},
            customerGets={'appliesOnOneTimePurchase':True,'appliesOnSubscription':False,
                'items':variants('variants') if e['scope']=='product' else {'__typename':'AllDiscountItems'},
                'value':{'__typename':'DiscountPercentage','percentage':e['value']/10000} if e['kind']=='percent' else
                    {'__typename':'DiscountAmount','amount':{'amount':domain.money(e['value']),'currencyCode':'GBP'},
                     'appliesOnEachItem':e['scope']=='product'}})
    return {'id':ID,'discount':remote,'metafield':{'jsonValue':e.get('function_config')}}


class Mock:
    config=cfg()
    def __init__(self):
        self.remote=None
        self.calls=[]
        self.lost=False
        self.hide=False

    def connection(self):
        return {'shop':STORE,'app':APP,'scopes':['read_products','write_discounts','read_markets','read_customers']}

    def verify_variants(self,rows):
        self.seen_variants=rows

    def automatic_count(self):
        return 0

    def find(self,marker):
        return [self.remote] if self.remote and not self.hide else []

    def code_owner(self,code):
        return None

    def function(self):
        return {'id':'owned-function','app_key':'dev-promotions','market':MARKET}

    def apply(self,action,e,remote):
        self.calls.append((action,shopify.mutation_input(e,remote) if action!='disable' else remote['id']))
        self.remote=readback(e,status='EXPIRED' if action=='disable' else publish._phase(e))
        if self.lost:
            raise shopify.ShopifyPromotionError('Simulated lost response after Shopify applied it.')
        return ID

    def read(self,ident,kind):
        return None if self.hide else copy.deepcopy(self.remote)


def approve(row,mock):
    plan=publish.build(row,mock)
    publish.confirm(row,publish.approval(plan,'operator'),'operator',mock)
    db.session.refresh(row)
    return plan


def test_admin_local_edit_duplicate_and_no_implicit_publication(workshop, monkeypatch):
    client=client_for(workshop)
    outsider=client_for(workshop,3)
    assert outsider.get('/promotions/').status_code==403
    assert outsider.post('/promotions/new',data={}).status_code==403
    assert client.get('/promotions/new').status_code==200
    def block():
        raise AssertionError('A local draft save or list must never open Shopify.')
    monkeypatch.setattr(publish,'client',block)
    form=dict(name='Any two',kind='exact',method='automatic',scope='product',intent='draft',tiers='2=9.00\n3=12.50',repeat='yes')
    response=client.post('/promotions/new',data=form)
    assert response.status_code==303
    with workshop.app_context():
        row=Promotion.query.one()
        ident=row.id
        assert publish.document(row)['tiers']==[{'qty':2,'total':900},{'qty':3,'total':1250}]
        assert row.receipt is None and row.state=='local'
    assert client.get(response.location).status_code==200
    assert client.post(response.location,data=dict(form,revision=1,name='Changed')).status_code==303
    assert client.post(response.location,data=dict(form,revision=1,name='Stale')).status_code==409
    assert client.post('/promotions/'+ident+'/duplicate').status_code==303
    with workshop.app_context():
        assert Promotion.query.count()==2
        copy_row=Promotion.query.filter(Promotion.id!=ident).one()
        assert publish.document(copy_row)['intent']=='draft' and copy_row.receipt is None


def test_confirmation_and_local_mutations_keep_existing_csrf(workshop):
    workshop.config['WTF_CSRF_ENABLED']=True
    client=client_for(workshop)
    assert client.post('/promotions/new',data={}).status_code==400
    page=client.get('/promotions/new')
    token=re.search(rb'name="csrf_token" value="([^"]+)"',page.data).group(1).decode()
    response=client.post('/promotions/new',data=dict(csrf_token=token,name='Private draft',kind='exact',
        method='automatic',scope='product',intent='draft',tiers='2=9.00',repeat='yes'))
    assert response.status_code==303
    review=response.location.replace('/edit','/review')
    assert client.post(review,data={'action':'confirm','approval':'forged','ack':'yes'}).status_code==400


def test_uk_schedule_and_local_safety():
    assert domain.schedule_time('2026-07-01 18:00')=='2026-07-01T17:00:00Z'
    assert domain.schedule_time('2026-11-01 18:00')=='2026-11-01T18:00:00Z'
    assert domain.schedule_time('2026-10-25 01:30+01:00')=='2026-10-25T00:30:00Z'
    for value in ['2026-10-25 01:30','2026-03-29 01:30']:
        with pytest.raises(ValueError,match='clock-change'):
            domain.schedule_time(value)
    with pytest.raises(ValueError,match='end time'):
        domain.definition(data(ends='2019-01-01T00:00:00Z'))
    with pytest.raises(ValueError,match='combine'):
        domain.definition(data('exact',combines={'orderDiscounts':True}))


def test_native_coupon_scheduled_update_disable_and_noop(workshop):
    with workshop.app_context():
        row=new('percent',method='code',code='WELCOME10',usage=40,once=True,
                segment='gid://shopify/Segment/19',value=1000,starts='2099-01-01T00:00:00Z')
        mock=Mock()
        plan=approve(row,mock)
        assert plan['action']=='create' and row.state=='verified'
        assert mock.calls[-1][1]['customerGets']['value']=={'percentage':0.1}
        assert mock.calls[-1][1]['context']=={'customerSegments':{'add':['gid://shopify/Segment/19']}}
        assert publish.receipt(row)['published']['result']['status']=='SCHEDULED'
        assert approve(row,mock)['action']=='verify' and len(mock.calls)==1
        local_update(row,value=1500)
        assert approve(row,mock)['action']=='update' and len(mock.calls)==2
        local_update(row,intent='disabled')
        assert approve(row,mock)['action']=='disable' and len(mock.calls)==3
        assert publish.receipt(row)['published']['result']['id']==ID
        assert approve(row,mock)['action']=='verify_disabled' and len(mock.calls)==3


def test_uncertain_create_is_never_replayed(workshop):
    with workshop.app_context():
        row=new('fixed',value=500)
        mock=Mock()
        mock.lost=True
        plan=publish.build(row,mock)
        token=publish.approval(plan,'operator')
        with pytest.raises(ValueError,match='lost response'):
            publish.confirm(row,token,'operator',mock)
        assert row.state=='uncertain' and len(mock.calls)==1
        with pytest.raises(ValueError,match='reconciliation'):
            publish.confirm(row,token,'operator',mock)
        mock.hide=True
        with pytest.raises(ValueError,match='Do not repeat|do not repeat'):
            publish.reconcile(row,mock)
        mock.hide=False
        publish.reconcile(row,mock)
        assert row.state=='verified' and len(mock.calls)==1
        assert mock.calls[0][1]['customerGets']['value']=={'discountAmount':{'amount':'5.00','appliesOnEachItem':False}}


def test_explicit_development_boundaries_and_assertion(monkeypatch):
    with pytest.raises(ValueError):
        cfg(store=shopify.PRODUCTION_SHOP).validate()
    with pytest.raises(ValueError):
        cfg(client=shopify.EXISTING_CLIENT).validate()
    shared={'shop':{'id':STORE,'myshopifyDomain':cfg().domain,'currencyCode':'GBP','plan':{'partnerDevelopment':False}},
            'currentAppInstallation':{'app':{'id':APP},'accessScopes':[{'handle':'write_discounts'},{'handle':'read_products'}]}}
    client=shopify.Admin(cfg())
    monkeypatch.setattr(client,'_graphql',lambda *a:shared)
    with pytest.raises(ValueError,match='Production is blocked'):
        client.connection()
    shared['shop']['plan']['partnerDevelopment']=True
    shared['currentAppInstallation']['app']['id']='gid://shopify/App/1'
    with pytest.raises(ValueError,match='separate app'):
        client.connection()
    shared['currentAppInstallation']['app']['id']=APP
    shared['currentAppInstallation']['accessScopes'].pop()
    with pytest.raises(ValueError,match='read_products'):
        client.connection()
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    from base64 import urlsafe_b64decode
    first=shopify.assertion(cfg(),key,12345)
    second=shopify.assertion(cfg(),key,12345)
    assert first!=second
    claims=json.loads(urlsafe_b64decode(first.split('.')[1]+'=='))
    assert claims['aud']==cfg().token_url and claims['exp']-claims['iat']==60


def test_extra_read_scopes_are_required_only_when_the_discount_needs_them():
    base=['read_products','write_discounts']
    publish._required_optional(data('fixed'),base)
    with pytest.raises(ValueError,match='read_markets'):
        publish._required_optional(data('exact'),base)
    with pytest.raises(ValueError,match='read_customers'):
        publish._required_optional(data('percent',segment='gid://shopify/Segment/8'),base)


def test_stale_approval_cannot_publish_changed_local_definition(workshop):
    with workshop.app_context():
        row=new('fixed')
        mock=Mock()
        token=publish.approval(publish.build(row,mock),'one')
        with pytest.raises(ValueError,match='different promotion or operator'):
            publish.confirm(row,token,'two',mock)
        local_update(row,value=700)
        with pytest.raises(ValueError,match='changed'):
            publish.confirm(row,token,'one',mock)
        assert row.state=='local' and not mock.calls


@pytest.mark.parametrize('variable_world',['Single Variable'],indirect=True)
def test_variant_identity_dynamic_options_and_native_bxgy(variable_world):
    from app import shopify_drafts as drafts
    product,*_ = variable_world
    adapter=VariableAdapter()
    _,token=drafts.preview(product,adapter)
    drafts.confirm(token,True,adapter)
    base={'id':product.id,'source':product.source_relpath}
    option=dict(base,mode='option',name='Size',value='Maxi')
    available=domain.resolve([option],STORE)
    assert len(available)==2 and all(row['options']['Size']=='Maxi' for row in available)
    fixed=dict(base,mode='fixed',sku=available[0]['sku'],gid=available[0]['id'])
    result=domain.resolve([fixed],STORE)
    assert len(result)==1
    for v in product.variations:
        v.menu_order = 100 - v.id
    assert domain.resolve([fixed],STORE)==result
    broken=dict(fixed,sku='REPLACEMENT')
    with pytest.raises(ValueError,match='no matching'):
        domain.resolve([broken],STORE)
    row=new('bxgy',targets=[option],method='code',code='BUY2',value=10000)
    mock=Mock()
    approve(row,mock)
    values=mock.calls[0][1]
    assert values['customerBuys']['value']=={'quantity':'2'} and values['customerGets']['value']['discountOnQuantity']['effect']=={'percentage':1}
    assert len(values['customerGets']['items']['products']['productVariantsToAdd'])==2
    assert publish.receipt(row)['published']['result']['get']==1


@pytest.mark.parametrize('variable_world',['Variable Collection'],indirect=True)
def test_exact_owned_configuration_and_overlapping_group_is_blocked(variable_world):
    from app import shopify_drafts as drafts
    product,*_ = variable_world
    adapter=VariableAdapter()
    _,token=drafts.preview(product,adapter)
    drafts.confirm(token,True,adapter)
    selected={'id':product.id,'source':product.source_relpath,'mode':'option','name':'Size','value':'Large'}
    first=new('exact',targets=[selected])
    mock=Mock()
    approve(first,mock)
    submitted=mock.calls[0][1]
    assert submitted['functionHandle']==shopify.HANDLE and submitted['context']=={'markets':{'add':[MARKET]}}
    config=json.loads(submitted['metafields'][0]['value'])
    assert config['currency']=='GBP' and len(config['variants'])==2
    peer=Promotion(id='b'*32,document=first.document,updated_at=publish.stamp())
    db.session.add(peer)
    db.session.commit()
    with pytest.raises(ValueError,match='overlap'):
        publish.build(peer,Mock())
    assert 'customerGets' not in submitted and publish.receipt(first)['published']['result']['function']['id']=='owned-function'
