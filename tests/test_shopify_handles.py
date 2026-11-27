"""Readable URL/recovery policy, using fictional Shopify responses only."""
import copy
import json
import pytest
from app import db, shopify_drafts as drafts
from app.shopify_handles import slug, legacy, prepare
from app.shopify_connection import ShopifyError
from app.models import ShopifyPublishJob
from test_shopify_drafts import world
from test_phase3_woo_publish_preview import preview_app
from test_shopify_simple_updates import created, change
from test_shopify_variables import variable_world, create

OLD='tlc-draft-'+'a'*32


@pytest.mark.parametrize('own,shared,single,expected', [
    ({}, {'meta_title':'Generic collection'}, False, 'Storage Tray | Cozy Collection'),
    ({'meta_title':''}, {'meta_title':'Generic collection'}, False, 'Storage Tray | Cozy Collection'),
    ({'meta_title':'Unique tray SEO'}, {'meta_title':'Generic collection'}, False, 'Unique tray SEO'),
    ({}, {'meta_title':'Unique root SEO'}, True, 'Unique root SEO'),
])
def test_product_seo_title_uses_resolved_title_unless_product_owned(monkeypatch,own,shared,single,expected):
    from app import taxonomy_assignments as taxonomy, metadata_workspace as workspace
    from types import SimpleNamespace
    original={'title':'Storage Tray | Cozy Collection','meta_title':'Generic collection','meta_description':'Keep description'}
    monkeypatch.setattr(taxonomy,'product_document',lambda p:dict(original))
    monkeypatch.setattr(workspace,'metadata_source',lambda p,k:dict(data=shared if k=='shared' else own))
    monkeypatch.setattr(workspace,'title_preview',lambda *a:dict(single_scope=single))
    resolved=taxonomy.publishing_document(SimpleNamespace(title='Indexed title'))
    assert resolved['meta_title']==expected
    assert resolved['meta_description']=='Keep description' and original['meta_title']=='Generic collection'


def test_seo_fallback_preview_write_readback_and_explicit_override(world):
    product,row,adapter=created(world)
    assert adapter.remote['seo']['title']==adapter.remote['title']
    change(world,title='New product title',meta_title='')
    p,token=drafts.preview(product,adapter)
    assert p['managed']['product']['seo']['title']==p['managed']['product']['title']
    drafts.confirm(token,True,adapter)
    assert row.state=='verified' and adapter.remote['seo']['title']==adapter.remote['title']
    change(world,meta_title='My unique search title')
    p,token=drafts.preview(product,adapter);drafts.confirm(token,True,adapter)
    assert adapter.remote['seo']['title']=='My unique search title'


class Lookup:
    def __init__(self, occupied=()): self.occupied=set(occupied)
    def find_draft(self,config,handle):
        return dict(nodes=[dict(id='gid://shopify/Product/999')] if handle in self.occupied else [],pageInfo=dict(hasNextPage=False))


def plan():
    return dict(sku='ABC-001',scope='full',warnings=[],managed=dict(product=dict(title='Café — Christmas Angel!')))


def test_readable_slug_collision_explicit_and_incomplete_discovery():
    assert slug('Café — Christmas Angel!')=='cafe-christmas-angel'
    assert legacy(OLD) and not legacy('tlc-draft-card')
    p=plan();prepare(p,None,Lookup());assert p['handle']=='cafe-christmas-angel'
    p=plan();prepare(p,None,Lookup(['cafe-christmas-angel']));assert p['handle']=='cafe-christmas-angel-abc-001'
    p=plan();p['managed']['product']['handle']='custom-url';prepare(p,None,Lookup());assert p['handle']=='custom-url'
    with pytest.raises(ShopifyError):prepare(p,None,Lookup(['custom-url']))
    with pytest.raises(ShopifyError):prepare(plan(),None,Lookup(['cafe-christmas-angel','cafe-christmas-angel-abc-001']))
    adapter=Lookup();adapter.find_draft=lambda *a:dict(nodes=[],pageInfo=dict(hasNextPage=True))
    with pytest.raises(ShopifyError,match='incomplete'):prepare(plan(),None,adapter)


def test_readable_and_scoped_handles_preserved_and_pending_create_stable():
    p=plan();prepare(p,None,Lookup(),remote=dict(id='gid://shopify/Product/100',handle='already-readable'))
    assert p['handle']=='already-readable' and 'handle' not in p['managed']['product']
    p=plan();p['scope']='inventory';prepare(p,None,Lookup(),remote=dict(id='gid://shopify/Product/100',handle=OLD))
    assert 'handle' not in p['managed']['product']
    p=plan();prepare(p,None,Lookup(),retained=OLD);assert p['handle']==OLD


def make_legacy(row,adapter):
    data=drafts.evidence(row);data['plan']['handle']=OLD;data['plan']['managed']['product']['handle']=OLD
    row.media_evidence_json=drafts.encode(data);adapter.remote['handle']=OLD;db.session.commit()


def test_simple_correction_same_identity_redirect_and_readable_title_changes(world):
    product,row,adapter=created(world)
    assert not legacy(adapter.remote['handle'])
    make_legacy(row,adapter);nonce=drafts.evidence(row)['plan']['nonce'];remote_id=row.shopify_product_id
    from app.shopify_products import product_state
    state=product_state(product,[row],url_builder=lambda *a,**k:'/review')
    assert state['label']=='Update Available'
    p,token=drafts.preview(product,adapter)
    assert p['classification']=='update' and any(f['field']=='handle' and f['changed'] for f in p['fields'])
    assert adapter.remote['handle']==OLD
    drafts.confirm(token,True,adapter)
    assert row.shopify_product_id==remote_id and row.state=='verified'
    assert not legacy(adapter.remote['handle']) and adapter.remote['metafield']['value']==nonce
    assert any(i.get('redirectNewHandle') is True for i in adapter.inputs)
    stable=adapter.remote['handle'];change(world,title='A completely different title')
    p,token=drafts.preview(product,adapter);assert not any(f['field']=='handle' and f['changed'] for f in p['fields'])
    drafts.confirm(token,True,adapter);assert adapter.remote['handle']==stable
    assert 'product_create' not in adapter.calls


def test_variable_parent_correction_preserves_variants(variable_world):
    product=variable_world[0];row,adapter=create(variable_world)
    assert not legacy(adapter.remote['handle'])
    make_legacy(row,adapter)
    variants=copy.deepcopy(adapter.remote['variants']);nonce=drafts.evidence(row)['plan']['nonce'];adapter.calls.clear();adapter.inputs.clear()
    p,token=drafts.preview(product,adapter);assert p['classification']=='update'
    drafts.confirm(token,True,adapter)
    assert row.state=='verified' and not legacy(adapter.remote['handle'])
    assert adapter.remote['variants']==variants and drafts.evidence(row)['plan']['nonce']==nonce
    assert any(i.get('redirectNewHandle') is True for i in adapter.inputs if isinstance(i,dict))
    assert 'product_create' not in adapter.calls and 'variable_create' not in adapter.calls


def test_collision_change_after_review_blocks_unapproved_handle(world):
    product,_,_=world
    from test_shopify_simple_updates import UpdateAdapter
    adapter=UpdateAdapter();p,token=drafts.preview(product,adapter)
    candidate=p['handle']
    adapter.find_draft=lambda c,h:dict(nodes=[dict(id='gid://shopify/Product/999')] if h==candidate else [],pageInfo=dict(hasNextPage=False))
    with pytest.raises(ShopifyError,match='availability changed'):drafts.confirm(token,True,adapter)
    assert 'product_create' not in adapter.calls and 'stage_write' not in adapter.calls


def test_auto_policy_queues_existing_legacy_handle_without_bom_cost_job(world):
    from app import automatic_publishing as publisher
    product,row,adapter=created(world);make_legacy(row,adapter);change(world,live=True)
    job=ShopifyPublishJob(product_id=product.id,source_identity=product.source_relpath,requested_revision='old-policy',state='verified',generation=1,trigger='scan',next_attempt=drafts.now(),updated_at=drafts.now())
    db.session.add(job);db.session.commit()
    publisher.refresh_production_costs(fence=lambda:None)
    db.session.refresh(job)
    assert job.state=='pending' and job.trigger=='url_handle'
    assert job.requested_revision==publisher.local_revision(product)
    p,token=drafts.preview(product,adapter)
    proof=publisher.execution_proof(product,p)
    assert proof['handle']==p['handle']
    assert adapter.remote['handle']==OLD  # refresh/preview never writes Shopify


@pytest.mark.parametrize('edit_source',[False,True])
def test_url_policy_upgrade_preserves_cost_refresh_authority(world,edit_source):
    from app import automatic_publishing as publisher
    product,row,adapter=created(world);change(world,live=True)
    basis=publisher.local_revision(product,include_costs=False,handle_policy=False)
    previous=publisher.local_revision(product,handle_policy=False)
    job=ShopifyPublishJob(product_id=product.id,source_identity=product.source_relpath,
        requested_revision=previous,production_basis_revision=basis,state='verified',generation=1,
        trigger='scan',next_attempt=drafts.now(),updated_at=drafts.now())
    db.session.add(job);db.session.commit()
    if edit_source:change(world,description='An unscanned source edit')
    publisher.refresh_production_costs(fence=lambda:None);db.session.refresh(job)
    if edit_source:
        assert job.state=='verified' and job.production_basis_revision==basis
    else:
        assert job.state=='pending'
        assert job.production_basis_revision==publisher.local_revision(product,include_costs=False)
