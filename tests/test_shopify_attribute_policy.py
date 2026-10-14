"""Customer-facing taxonomy policy over fictional files/API only; no live writes."""
import copy
import json
from datetime import timedelta
from pathlib import Path

import pytest
from flask import current_app

from app import db, shopify_drafts as drafts, shopify_attributes as attributes, shopify_status as status
from app.models import ShopifyAttributeIdentity, ShopifyProductComparison
from app.taxonomy_registry import attribute_browse_role, attribute_storefront_public
from app.shopify_connection import ShopifyError
from app.shopify_products import states_for
from app.shopify_simple_fields import review_rows
from app import taxonomy_workspace
from test_shopify_drafts import world
from test_shopify_taxonomy_products import registry
from test_shopify_taxonomy_lifecycle import informational, LifecycleAdapter
from test_shopify_live_fixes import AccessAdapter
from test_shopify_simple_updates import change
from test_shopify_variables import variable_world, VariableAdapter, rescan
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_foundation import isolated_transport


def legacy(registry):
    informational(registry)
    data=json.loads(registry.read_text()); attr=data['attributes'][0]
    attr.pop('browse_role',None)
    for term in attr['terms']:
        term.pop('browse_role',None); term.pop('browse_parent',None)
    registry.write_text(json.dumps(data))
    return data


def display(product):
    with current_app.test_request_context(): return states_for([product])[product.id]


def created(world,registry,*,internal=False):
    data=legacy(registry)
    if internal:
        data['attributes'][0]['browse_role']='internal'; registry.write_text(json.dumps(data))
    change(world,attributes={'Occasion':['Birthday']},variation_attributes=[])
    adapter=AccessAdapter()
    adapter.product_revisions=lambda config,ids:{gid:{'id':gid,'status':'DRAFT','updatedAt':adapter.remote['updatedAt']} for gid in ids}
    plan,token=drafts.preview(world[0],adapter); assert token and not plan['blockers']
    row=drafts.confirm(token,True,adapter)
    assert row.state=='verified',drafts.evidence(row).get('error')
    adapter.remote.setdefault('updatedAt',json.loads(ShopifyProductComparison.query.one().summary_json)['remote_updated_at'])
    return row,adapter


@pytest.mark.parametrize('navigation,visible,explicit,expected',[(True,False,None,'filter'),(False,True,None,'filter'),(False,False,None,'internal'),(True,True,'internal','internal'),(False,False,'both','both')])
def test_legacy_customer_intent_and_explicit_private_policy(navigation,visible,explicit,expected):
    attr={'navigation':navigation,'visible_default':visible}
    if explicit is not None: attr['browse_role']=explicit
    before=copy.deepcopy(attr)
    assert attribute_browse_role(attr)==attribute_browse_role(attr,{})==expected
    assert attr==before


def test_all_seven_supplied_tlc_attributes_resolve_public_without_json_rewrite():
    path=Path(__file__).resolve().parents[1]/'deployment/examples/tlc/registry.json'
    before=path.read_bytes(); data=json.loads(before)
    assert len(data['attributes'])==7
    assert all(attribute_browse_role(a)=='filter' and attribute_storefront_public(a) for a in data['attributes'])
    assert all(attribute_browse_role(a,t)=='filter' for a in data['attributes'] for t in a['terms'])
    assert path.read_bytes()==before


def test_new_public_resources_filter_only_and_retained_ownership(world,registry):
    data=legacy(registry); change(world,attributes={'Occasion':['Birthday']},variation_attributes=[])
    source=registry.read_bytes(); adapter=AccessAdapter()
    plan,token=drafts.preview(world[0],adapter)
    assert plan['attributes'][0]['public'] and plan['attributes'][0]['role']=='filter'
    assert plan['collections']==[]  # filter terms are not implicit collections
    assert all(r['spec']['access']['storefront']=='PUBLIC_READ' for r in plan['attributes'][0]['resources'] if r['kind'] in {'definition','field'})
    assert plan['attributes'][0]['resources'][1]['spec']['namespace']=='$app:tlc_attributes'
    assert adapter.resources=={} and registry.read_bytes()==source
    row=drafts.confirm(token,True,adapter)
    assert row.state=='verified'
    assert len(ShopifyAttributeIdentity.query.all())==3
    assert 'collection_create' not in adapter.calls


def test_existing_private_resources_offer_persisted_update_without_recreation(world,registry):
    row,adapter=created(world,registry,internal=True)
    identities={r.source_key:r.remote_id for r in ShopifyAttributeIdentity.query.all()}
    assigned=copy.deepcopy(adapter.attr_values); product_gid=row.shopify_product_id
    data=json.loads(registry.read_text()); data['attributes'][0].pop('browse_role'); registry.write_text(json.dumps(data))
    assert display(world[0])['state']=='local_changes'
    adapter.calls.clear(); result=status.refresh([world[0]],force=True,adapter=adapter)
    assert not result['errors']
    saved=json.loads(ShopifyProductComparison.query.one().summary_json)
    assert saved['classification']=='update' and saved['change_count']==4
    assert sum(v['field'].startswith('attribute_access.') for v in saved['changes'])==2
    assert sum(v['field'].startswith('attribute_capability.') for v in saved['changes'])==2
    assert display(world[0])['label']=='Update Available'
    assert 'storefront access' in ' '.join(display(world[0])['change_summary'])
    assert not any('create' in call or 'update' in call or 'write' in call for call in adapter.calls)
    db.session.expire_all()
    assert display(world[0])['label']=='Update Available'
    plan,token=drafts.preview(world[0],adapter)
    assert token and plan['attributes'][0]['action']=='Already matching'
    assert all(r['action']=='Already matching' for r in review_rows(plan) if r['label']=='attributes.Occasion')
    assert len([r for r in review_rows(plan) if 'storefront access' in r['label'] and r['action']=='Change'])==2
    before=list(adapter.calls); drafts.confirm(token,True,adapter)
    writes=adapter.calls[len(before):]
    assert writes.count('attribute_access_update')==2 and 'attribute_assignment_write' not in writes
    assert not any('create' in call for call in writes)
    assert row.shopify_product_id==product_gid and identities=={r.source_key:r.remote_id for r in ShopifyAttributeIdentity.query.all()}
    assert assigned==adapter.attr_values
    assert drafts.preview(world[0],adapter)[0]['classification']=='no_change'
    assert display(world[0])['label']=='Up to Date'


@pytest.mark.parametrize('drift',['access','assignment'])
def test_existing_remote_drift_rechecked_even_without_product_revision_change(world,registry,drift):
    row,adapter=created(world,registry)
    adapter.calls.clear(); status.refresh([world[0]],adapter=adapter)
    assert adapter.calls==[]  # recent trusted comparison is shared and local rendering is instant
    stamp=adapter.remote['updatedAt']
    if drift=='access':
        for (kind,_),value in adapter.resources.items():
            if kind in {'definition','field'}: value['access']['storefront']='NONE'
    else:
        value=next(iter(adapter.attr_values.values())); value['value']='[]'; value['compareDigest']='changed'
    comparison=ShopifyProductComparison.query.one(); data=json.loads(comparison.summary_json)
    data['related_checked_at']=(drafts.now()-timedelta(minutes=11)).isoformat()
    comparison.summary_json=json.dumps(data); db.session.commit()
    result=status.refresh([world[0]],adapter=adapter)
    assert result['full_comparisons']==1 and not result['errors']
    assert adapter.remote['updatedAt']==stamp and display(world[0])['label']=='Update Available'
    assert not any('create' in call or 'update' in call or 'write' in call for call in adapter.calls)
    retained=json.loads(ShopifyProductComparison.query.one().summary_json)
    assert retained['classification']=='update' and retained['change_count']>0


def test_internal_vocabulary_stays_private(world,registry):
    row,adapter=created(world,registry,internal=True)
    assert all(r['access']['storefront']=='NONE' for (kind,_),r in adapter.resources.items() if kind in {'definition','field'})
    assert drafts.preview(world[0],adapter)[0]['classification']=='no_change'


@pytest.mark.parametrize('fault',['definition','draft_entry'])
def test_existing_term_readback_cannot_claim_up_to_date_with_incompatible_entry(world,registry,fault):
    row,adapter=created(world,registry); original=adapter.attribute_resource
    def incompatible(config,kind,spec):
        result=original(config,kind,spec)
        if kind=='term':
            if fault=='definition': result['definition']['id']='gid://shopify/MetaobjectDefinition/99999'
            else: result['capabilities']['publishable']={'status':'DRAFT'}
        return result
    adapter.attribute_resource=incompatible; adapter.calls.clear()
    status.refresh([world[0]],force=True,adapter=adapter)
    assert display(world[0])['label']!='Up to Date'
    plan,token=drafts.preview(world[0],adapter)
    assert token is None and plan['blockers']
    assert not any('create' in c or 'update' in c or 'write' in c for c in adapter.calls)


def test_existing_access_missing_write_permission_records_update_but_blocks_confirm(world,registry):
    row,adapter=created(world,registry)
    for (kind,_),value in adapter.resources.items():
        if kind in {'definition','field'}: value['access']['storefront']='NONE'
    original=adapter.test_connection
    adapter.test_connection=lambda config:{**original(config),'scopes':sorted(attributes.READ_SCOPES|{'read_products','write_products'})}
    adapter.calls.clear(); status.refresh([world[0]],force=True,adapter=adapter)
    saved=json.loads(ShopifyProductComparison.query.one().summary_json)
    assert saved['classification']=='update' and any('write_metaobject_definitions' in b for b in saved['blockers'])
    assert display(world[0])['label']=='Update Available'
    plan,token=drafts.preview(world[0],adapter)
    assert token is None and plan['attributes'][0]['missing_scopes']==['write_metaobject_definitions']
    assert 'attribute_access_update' not in adapter.calls


def test_visibility_policy_edit_invalidates_signed_confirmation_before_access_write(world,registry):
    row,adapter=created(world,registry)
    for (kind,_),value in adapter.resources.items():
        if kind in {'definition','field'}: value['access']['storefront']='NONE'
    _,token=drafts.preview(world[0],adapter)
    source=json.loads(registry.read_text()); source['attributes'][0]['browse_role']='internal'; registry.write_text(json.dumps(source))
    adapter.calls.clear()
    with pytest.raises(ShopifyError): drafts.confirm(token,True,adapter)
    assert not any('create' in call or 'update' in call or 'write' in call for call in adapter.calls)


@pytest.mark.parametrize('private_parent',[False,True])
def test_shared_definition_never_flips_access_for_selected_terms(world,registry,private_parent):
    data=legacy(registry); attr=data['attributes'][0]
    attr['browse_role']='internal' if private_parent else 'filter'
    attr['terms'][0]['browse_role']='filter' if private_parent else 'internal'
    registry.write_text(json.dumps(data))
    change(world,attributes={'Occasion':['Birthday']},variation_attributes=[])
    adapter=AccessAdapter(); plan,token=drafts.preview(world[0],adapter)
    assert token is None and any('Mixed public/internal' in b for b in plan['blockers'])
    assert adapter.resources=={} and ShopifyAttributeIdentity.query.count()==0


def test_matching_foreign_access_failure_never_becomes_no_change(world,registry):
    row,adapter=created(world,registry)
    original=adapter.attribute_resource
    def denied(*args): raise ShopifyError('Attribute read unavailable; no access equality can be established')
    adapter.attribute_resource=denied
    result=status.refresh([world[0]],force=True,adapter=adapter)
    assert result['errors'] or display(world[0])['label']=='Verification Unavailable'
    assert display(world[0])['label']!='Up to Date'
    assert row.state=='verified'  # publishing recovery is not rewritten by a read failure
    adapter.attribute_resource=original
    status.refresh([world[0]],force=True,adapter=adapter)
    assert display(world[0])['label']=='Up to Date'


def test_guided_roles_preserve_term_inheritance_and_explicit_internal(world,registry):
    data=legacy(registry)
    fields={'name':'Occasion','slug':'occasion','state':'active','navigation':'yes'}
    taxonomy_workspace.edit_definition(data,'attributes','occasion','',fields)
    assert data['attributes'][0]['browse_role']=='filter'
    term={'name':'Birthday','slug':'birthday','state':'active','browse_role':''}
    taxonomy_workspace.edit_definition(data,'terms','birthday','occasion',term)
    assert 'browse_role' not in next(t for t in data['attributes'][0]['terms'] if t['key']=='birthday')
    fields['browse_role']='internal'; taxonomy_workspace.edit_definition(data,'attributes','occasion','',fields)
    assert attribute_browse_role(data['attributes'][0],next(t for t in data['attributes'][0]['terms'] if t['key']=='birthday'))=='internal'


def test_taxonomy_editor_shows_effective_filter_and_inherited_terms_without_writes(world,registry):
    legacy(registry); before=registry.read_bytes()
    client=_client(current_app._get_current_object())
    attribute=client.get('/taxonomy/edit/attributes?key=occasion').get_data(as_text=True)
    term=client.get('/taxonomy/edit/terms?attribute=occasion&key=birthday').get_data(as_text=True)
    assert 'value="filter" selected' in attribute
    assert 'value="" selected>Inherit attribute role' in term
    page=client.get('/taxonomy?tab=attributes').get_data(as_text=True)
    assert 'Public storefront read' in page and 'Refresh/Compare' in page
    assert registry.read_bytes()==before


class AttributeVariableAdapter(VariableAdapter,AccessAdapter):
    def __init__(self):
        VariableAdapter.__init__(self)
        self.resources={}; self.attr_values={}; self.resource_failure=None
        self.collections={}; self.collection_failure=None
    def test_connection(self,config):
        result=VariableAdapter.test_connection(self,config)
        result['scopes']+=list(attributes.READ_SCOPES|attributes.WRITE_SCOPES)
        return result


def test_existing_variable_attribute_access_update_retains_all_variants(variable_world,registry):
    data=legacy(registry)
    p,shared,_,_,_=variable_world
    source=json.loads(shared.read_text()); source['variation_attributes']=list(source['attributes'])
    source['attributes']['Occasion']=['Birthday']; shared.write_text(json.dumps(source)); rescan(variable_world)
    adapter=AttributeVariableAdapter(); _,token=drafts.preview(p,adapter); row=drafts.confirm(token,True,adapter)
    assert row.state=='verified',drafts.evidence(row).get('error')
    identities=copy.deepcopy(drafts.evidence(row)['variant_maps']); parent=row.shopify_product_id
    for (kind,_),value in adapter.resources.items():
        if kind in {'definition','field'}: value['access']['storefront']='NONE'
    adapter.calls.clear()
    result=status.refresh([p],force=True,adapter=adapter)
    assert not result['errors'] and display(p)['label']=='Update Available'
    plan,token=drafts.preview(p,adapter)
    assert len(plan['variants'])==4 and len(plan['options'])==2
    assert not any(v['action']=='Create' for v in plan['variants'])
    assert drafts.confirm(token,True,adapter).state=='verified'
    assert identities==drafts.evidence(row)['variant_maps'] and parent==row.shopify_product_id
    assert 'variants_create' not in adapter.calls and 'product_create' not in adapter.calls
    assert 'attribute_assignment_write' not in adapter.calls
    assert display(p)['label']=='Up to Date'
