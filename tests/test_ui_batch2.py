"""Batch 2 presentation contracts. Temporary catalogue and fictional Shopify only."""
import copy
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import pytest
from flask import current_app
from app import db, shopify_drafts as drafts
from app.models import ShopifyCollectionIdentity, ShopifyProductIdentity, CatalogueOperation
from app.product_presentation import source_label, readable, resolved_value, review_display, product_issue_links
from app.shopify_simple_fields import review_rows
from test_shopify_drafts import world, FakeDraftAdapter
from test_shopify_simple_updates import created, change
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_variables import variable_world, VariableAdapter
from test_shopify_taxonomy_products import registry


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__(); self.tags=[]; self.forms=[]; self.current=None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs); self.tags.append((tag,attrs))
        if tag=='form':
            self.current={'action':attrs.get('action',''), 'method':attrs.get('method','get'), 'inputs':[]}
            self.forms.append(self.current)
        elif self.current is not None and tag=='input':
            self.current['inputs'].append(attrs)

    def handle_endtag(self, tag):
        if tag=='form': self.current=None


def client(): return _client(current_app._get_current_object())


def test_workspace_is_compact_and_retains_signed_actions_offline(world, monkeypatch):
    product,row,adapter=created(world)
    monkeypatch.setattr('app.shopify_connection.ShopifyAdapter._request',lambda *a,**k:pytest.fail('GET contacted Shopify'))
    html=client().get(f'/products/{product.id}').get_data(as_text=True)
    assert html.index('aria-label="Breadcrumb"') < html.index('Product overview') < html.index('Local taxonomy assignments')
    assert 'Full Source Comparison' in html and 'Resolved Value' in html and 'Source</th>' in html
    assert 'Mixed sources / Derived</span><span class="field-helper">' not in html
    assert html.count('data-shopify-detail-refresh')==1
    page=Page(html)
    sync=next(f for f in page.forms if f['action'].endswith('/sync-now'))
    ack=next(i for i in sync['inputs'] if i['name']=='acknowledged')
    assert ack['value']=='yes' and 'required' in ack and 'checked' not in ack
    assert f'/products/{product.id}/shopify/review' in html
    assert f'/shopify/drafts/identity/{row.id}' in html
    assert not adapter.calls


@pytest.mark.parametrize('state,label', [('inherited','Inherited'),('overridden','Overridden'),
    ('product_additions','Mixed sources'),('scanner-generated','Derived')])
def test_source_labels_do_not_change_resolved_values(state,label):
    row=dict(state=state,collection=['Card'],override=['Card'],resolved=['Card'])
    before=copy.deepcopy(row)
    assert source_label(row)==label and row==before


def test_derived_and_mixed_sources_are_not_falsely_inherited():
    assert source_label(dict(state='inherited',collection='Shared',override=None,resolved='Folder | Shared'))=='Inherited + derived'
    assert source_label(dict(state='overridden',collection={'a':1},override={'b':2},resolved={'a':1,'b':2}))=='Mixed sources / derived'
    assert readable({'length':155,'width':155})=='Length: 155; Width: 155'
    assert resolved_value({'key':'price','resolved':'3.50'})=='£3.50'
    assert resolved_value({'key':'price','resolved':'3.505'})=='3.505'
    assert resolved_value({'key':'weight','resolved':50})=='50 g'


def test_review_get_only_arms_read_preview_not_any_execution(world,monkeypatch):
    product,_,_=world
    monkeypatch.setattr(drafts,'preview',lambda *a,**k:pytest.fail('GET built preview synchronously'))
    html=client().get(f'/products/{product.id}/shopify/review').get_data(as_text=True)
    page=Page(html)
    preview=next(a for t,a in page.tags if 'data-preview-form' in a)
    assert preview['data-auto-preview']=='yes'
    assert 'data-shopify-publish' not in html
    inputs=page.forms[0]['inputs']
    assert next(i for i in inputs if i.get('name')=='action')['value']=='preview'
    assert not any(i.get('name') in ('review','acknowledge') for i in inputs)
    for name in ['inventory_only','customisations_only']:
        node=next(i for i in inputs if i.get('name')==name)
        assert node['value']=='yes' and 'checked' not in node
    assert 'Advanced Review Options' in html


def test_healthy_review_collapses_matching_and_retains_every_field(world,monkeypatch):
    product,row,adapter=created(world)
    plan,token=drafts.preview(product,adapter)
    before=copy.deepcopy(plan)
    rows=review_rows(plan)
    display=review_display(plan,rows)
    assert len(display['matching'])==len(rows) and not display['changes']
    assert plan==before and token is None
    monkeypatch.setattr(drafts,'ShopifyAdapter',lambda:adapter)
    adapter.calls.clear()
    response=client().post(f'/products/{product.id}/shopify/review',data={'action':'preview'})
    html=response.get_data(as_text=True)
    assert response.status_code==200 and 'No Change' in html
    assert all(r['label'] in html for r in rows)
    matching=next(a for t,a in Page(html).tags if 'data-review-matching' in a)
    assert 'open' in matching and 'data-shopify-publish' not in html
    assert not any('write' in c or 'update' in c or 'create' in c for c in adapter.calls)


def test_pending_edit_approval_is_original_token_and_unchecked(world,monkeypatch):
    product,row,adapter=created(world)
    change(world,description='<p>A genuine edit</p>')
    plan,token=drafts.preview(product,adapter)
    monkeypatch.setattr(drafts,'preview',lambda p,**kw:(plan,token))
    html=client().post(f'/products/{product.id}/shopify/review',data={'action':'preview'}).get_data(as_text=True)
    approval=next(f for f in Page(html).forms if any(i.get('name')=='review' for i in f['inputs']))
    assert next(i for i in approval['inputs'] if i.get('name')=='review')['value']==token
    ack=next(i for i in approval['inputs'] if i.get('name')=='acknowledge')
    assert ack['value']=='yes' and 'required' in ack and 'checked' not in ack
    assert 'Main description' in html and 'Confirm reviewed changes &amp; Sync' in html


@pytest.mark.parametrize('fields,expected', [({},'full'),({'inventory_only':'yes'},'inventory'),
    ({'customisations_only':'yes'},'customisations')])
def test_advanced_options_forward_the_same_original_scope(world,monkeypatch,fields,expected):
    product,_,_=world
    plan,_=drafts.preview(product,FakeDraftAdapter())
    calls=[]
    def preview(p,**kwargs):
        calls.append((p.id,kwargs));return plan,None
    monkeypatch.setattr(drafts,'preview',preview)
    response=client().post(f'/products/{product.id}/shopify/review',data={'action':'preview',**fields})
    assert response.status_code==200 and calls==[(product.id,{'scope':expected})]


def test_two_limited_scopes_still_block_before_any_preview(world,monkeypatch):
    product,_,_=world
    monkeypatch.setattr(drafts,'preview',lambda *a,**k:pytest.fail('Conflicting scopes reached engine'))
    response=client().post(f'/products/{product.id}/shopify/review',data={
        'action':'preview','inventory_only':'yes','customisations_only':'yes'})
    assert response.status_code==400 and b'Choose only one limited review scope' in response.data


def test_collection_blocker_links_exact_existing_mapping_workflow(world,registry,monkeypatch):
    product,_,_=world
    change(world,categories=['Cards > Birthday Cards'])
    mapping=ShopifyCollectionIdentity.query.one();mapping.state='uncertain';mapping.source_key='categories:birthday'
    db.session.add(ShopifyCollectionIdentity(store_id=mapping.store_id,source_key='categories:cards',
        remote_id='gid://shopify/Collection/802',state='verified',evidence_json='{"version":1}'))
    db.session.commit()
    adapter=FakeDraftAdapter();monkeypatch.setattr(drafts,'ShopifyAdapter',lambda:adapter)
    response=client().post(f'/products/{product.id}/shopify/review',data={'action':'preview'})
    html=response.get_data(as_text=True)
    assert response.status_code==200 and 'Collection recovery required' in html
    links=[a['href'] for t,a in Page(html).tags if t=='a' and a.get('href','').startswith('/taxonomy/shopify?source=')]
    assert links and parse_qs(urlsplit(links[0]).query)['source']==[mapping.source_key]
    assert links[0].endswith('#individual-mapping') and 'data-shopify-publish' not in html
    assert not any('write' in c or 'create' in c for c in adapter.calls)


def test_child_collection_issue_does_not_falsely_flag_its_healthy_parent(world,monkeypatch):
    monkeypatch.setattr('app.shopify_taxonomy.assignments',lambda p:([
        {'key':'categories:cards','label':'Cards'},
        {'key':'categories:birthday','label':'Cards > Birthday Cards'}],[]))
    with current_app.test_request_context():
        links=product_issue_links(world[0],['Collection recovery required: Cards > Birthday Cards'])
    assert len(links)==1 and links[0]['label']=='Cards > Birthday Cards'


def test_recovery_page_preserves_form_and_does_not_reconcile_on_get(world,monkeypatch):
    product,row,adapter=created(world)
    row.state='uncertain';row.verification_state='unverified';db.session.commit()
    retained=row.shopify_product_id
    monkeypatch.setattr(drafts,'reconcile',lambda *a,**k:pytest.fail('GET reconciled identity'))
    html=client().get(f'/shopify/drafts/identity/{row.id}').get_data(as_text=True)
    assert 'Recovery Required' in html and 'Reconcile Existing Product' in html
    assert f'/products/{product.id}/shopify/review' in html and 'Candidate identity' in html
    assert any(i.get('name')=='candidate_id' for f in Page(html).forms for i in f['inputs'])
    assert row.shopify_product_id==retained and not adapter.calls
    assert not any('checked' in i for f in Page(html).forms for i in f['inputs'] if i.get('type')=='checkbox')
    review=client().get(f'/products/{product.id}/shopify/review').get_data(as_text=True)
    assert next(a for t,a in Page(review).tags if 'data-preview-form' in a)['data-auto-preview']=='no'
    assert 'Resolve Shopify Recovery' in review and 'Automatic preview is paused' in review


@pytest.mark.parametrize('state',['processing','pending','updating','deleting','uploading'])
def test_auto_preview_never_finalises_or_resumes_unresolved_identity(world,monkeypatch,state):
    product,row,_=created(world)
    row.state=state;db.session.commit()
    monkeypatch.setattr(drafts,'preview',lambda *a,**k:pytest.fail('Unresolved identity automatically inspected'))
    html=client().get(f'/products/{product.id}/shopify/review').get_data(as_text=True)
    assert next(a for t,a in Page(html).tags if 'data-preview-form' in a)['data-auto-preview']=='no'
    assert 'Build read-only Preview' in html and 'Resolve Shopify Recovery' in html


def test_variable_review_retains_all_owners_and_fields(variable_world,monkeypatch):
    product,*_=variable_world;adapter=VariableAdapter()
    plan,token=drafts.preview(product,adapter)
    rows=review_rows(plan)
    monkeypatch.setattr(drafts,'preview',lambda p,**kw:(plan,token))
    response=client().post(f'/products/{product.id}/shopify/review',data={'action':'preview'})
    html=response.get_data(as_text=True)
    assert response.status_code==200
    assert 'Expand variant groups' in html and 'data-review-group' in html
    assert all(r['label'] in html for r in rows)
    assert all(v['sku'] in html for v in plan['variants'])
    assert 'Current Shopify' in html and 'Proposed Value' in html and 'Action / Status' in html
    assert not any('write' in c or 'create' in c for c in adapter.calls)
