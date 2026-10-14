"""Definition settings only: fictional API/registry, never live Shopify/Discord."""
import copy
import json
from pathlib import Path

import pytest
from flask import current_app

from app import db, shopify_drafts as drafts, shopify_attributes as attrs, shopify_status as status
from app.models import ShopifyAttributeIdentity, ShopifyCollectionIdentity, ShopifyProductComparison
from app.shopify_connection import ShopifyAdapter, ShopifyError, ATTRIBUTE_FIELD_CAPABILITIES, ATTRIBUTE_FIELD_ACCESS
from app.shopify_simple_fields import review_rows
from test_shopify_drafts import world
from test_shopify_simple_updates import change
from test_shopify_taxonomy_products import registry
from test_shopify_taxonomy_lifecycle import informational
from test_shopify_attribute_policy import display, legacy
from test_shopify_live_fixes import AccessAdapter
from test_shopify_variables import variable_world, VariableAdapter, rescan
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_foundation import isolated_transport, Session, Response


class CapabilitiesAdapter(AccessAdapter):
    def __init__(self):
        super().__init__()
        self.eligible={key:True for key in attrs.CAPABILITY_LABELS}
        self.patches=[]

    def product_revisions(self,config,ids):
        return {gid:{'id':gid,'updatedAt':self.remote.get('updatedAt','2026-10-12T00:00:00Z'),'status':self.remote['status']} for gid in ids}

    def create_attribute_resource(self,config,kind,spec):
        if kind=='field':
            assert 'capabilities' not in spec  # no guessed type eligibility before readback
        result=super().create_attribute_resource(config,kind,spec)
        if kind=='field':
            for key,cap in self.resources[self.key(kind,spec)]['capabilities'].items():
                cap['eligible']=self.eligible[key]
        return result

    def update_attribute_access(self,config,kind,gid,spec):
        # Exact pending target exists durably before every definition mutation.
        pending=ShopifyAttributeIdentity.query.filter_by(remote_id=gid).one()
        assert pending.state=='updating_access' and pending.operation_id
        self.patches.append((kind,gid,copy.deepcopy(spec)))
        return super().update_attribute_access(config,kind,gid,spec)


def setup(world,registry,adapter=None,*,internal=False,groups=False):
    data=legacy(registry)
    if internal:
        data['attributes'][0]['browse_role']='internal'
    registry.write_text(json.dumps(data))
    authored={'attributes':{'Occasion':['Birthday']},'variation_attributes':[]}
    if groups: authored.update(categories=['Cards'],storefront_collections=['Personalised'])
    change(world,**authored)
    adapter=adapter or CapabilitiesAdapter()
    plan,token=drafts.preview(world[0],adapter)
    assert token and not plan['blockers'],plan['blockers']
    row=drafts.confirm(token,True,adapter)
    assert row.state=='verified',drafts.evidence(row).get('error')
    return row,adapter


def field(adapter):
    return next(value for (kind,_),value in adapter.resources.items() if kind=='field')


def disable(adapter,*keys):
    for key in keys or tuple(attrs.CAPABILITY_LABELS):
        field(adapter)['capabilities'][key]['enabled']=False
        if key=='adminFilterable':field(adapter)['capabilities'][key]['status']='NOT_FILTERABLE'


def test_new_customer_attribute_uses_confirmed_eligibility(world,registry):
    row,adapter=setup(world,registry)
    assert all(cap['enabled'] for cap in field(adapter)['capabilities'].values())
    assert field(adapter)['access']['storefront']=='PUBLIC_READ'
    assert next(value for (kind,_),value in adapter.resources.items() if kind=='definition')['access']['storefront']=='PUBLIC_READ'
    assert len(adapter.patches)==1 and set(adapter.patches[0][2]['capabilities'])==set(attrs.CAPABILITY_LABELS)
    assert 'access' not in adapter.patches[0][2]


@pytest.mark.parametrize('keys',[('adminFilterable',),('smartCollectionCondition',),('adminFilterable','smartCollectionCondition')])
def test_existing_capabilities_are_independent_reviewed_differences(world,registry,keys):
    row,adapter=setup(world,registry)
    identities={r.source_key:r.remote_id for r in ShopifyAttributeIdentity.query.all()}
    values=copy.deepcopy(adapter.attr_values); product=row.shopify_product_id
    media=copy.deepcopy(adapter.remote['media']); disable(adapter,*keys)
    plan,token=drafts.preview(world[0],adapter)
    assert token and plan['classification']=='update'
    changes=[r for r in plan['fields'] if r['field'].startswith('attribute_capability.')]
    assert len(changes)==len(keys) and all(r['before'] is False and r['after'] is True for r in changes)
    assert plan['attributes'][0]['assignment_changed'] is False
    with current_app.test_request_context(): assert display(world[0])['label']=='Update Available'
    writes_before=len(adapter.calls); patches_before=len(adapter.patches)
    assert drafts.confirm(token,True,adapter).state=='verified'
    writes=adapter.calls[writes_before:]
    assert len(adapter.patches)==patches_before+1
    assert set(adapter.patches[-1][2]['capabilities'])==set(keys) and 'access' not in adapter.patches[-1][2]
    assert 'attribute_assignment_write' not in writes and not any('create' in v for v in writes)
    assert product==row.shopify_product_id and values==adapter.attr_values and media==adapter.remote['media']
    assert identities=={r.source_key:r.remote_id for r in ShopifyAttributeIdentity.query.all()}
    assert drafts.preview(world[0],adapter)[0]['classification']=='no_change'
    assert display(world[0])['label']=='Up to Date'


def test_correct_settings_and_repeat_comparisons_do_not_write(world,registry):
    row,adapter=setup(world,registry); adapter.calls.clear()
    for _ in range(3):
        plan,token=drafts.preview(world[0],adapter)
        assert plan['classification']=='no_change' and token is None
    assert not any('create' in v or 'update' in v or 'write' in v for v in adapter.calls)


def test_internal_attribute_remains_private_without_managing_public_capabilities(world,registry):
    row,adapter=setup(world,registry,internal=True)
    assert all(not cap['enabled'] for cap in field(adapter)['capabilities'].values())
    assert all(value['access']['storefront']=='NONE' for (kind,_),value in adapter.resources.items() if kind in {'field','definition'})
    assert not adapter.patches and 'capabilities' not in drafts.evidence(row)['plan']['attributes'][0]['resources'][1]['spec']


@pytest.mark.parametrize('public_parent',[True,False])
def test_mixed_visibility_never_creates_or_updates_definition(world,registry,public_parent):
    data=legacy(registry)
    data['attributes'][0]['browse_role']='filter' if public_parent else 'internal'
    data['attributes'][0]['terms'][0]['browse_role']='internal' if public_parent else 'filter'
    registry.write_text(json.dumps(data)); change(world,attributes={'Occasion':['Birthday']},variation_attributes=[])
    adapter=CapabilitiesAdapter(); plan,token=drafts.preview(world[0],adapter)
    assert not token and any('Mixed public/internal' in reason for reason in plan['blockers'])
    assert not adapter.resources and not adapter.patches


@pytest.mark.parametrize('key',list(attrs.CAPABILITY_LABELS))
def test_ineligible_definition_is_not_forced_and_limitation_is_explicit(world,registry,key):
    adapter=CapabilitiesAdapter(); adapter.eligible[key]=False
    row,adapter=setup(world,registry,adapter)
    assert field(adapter)['capabilities'][key]['enabled'] is False
    assert all(key not in patch.get('capabilities',{}) for _,_,patch in adapter.patches)
    plan,token=drafts.preview(world[0],adapter)
    assert plan['classification']=='no_change' and token is None
    assert any(attrs.CAPABILITY_LABELS[key] in warning and 'not eligible' in warning for warning in plan['warnings'])


def test_unsupported_api_fields_are_disclosed_without_breaking_access(world,registry):
    adapter=CapabilitiesAdapter(); original=adapter.attribute_schema
    adapter.attribute_schema=lambda config:{k:v for k,v in original(config).items() if k not in {'capabilityUpdate','capabilityOutput'}}
    row,adapter=setup(world,registry,adapter)
    assert not adapter.patches and field(adapter)['access']['storefront']=='PUBLIC_READ'
    plan,_=drafts.preview(world[0],adapter)
    assert len(plan['attributes'][0]['limitations'])==2 and all('unsupported' in v for v in plan['attributes'][0]['limitations'])


def test_legacy_comparison_is_invalidated_only_for_informational_attributes(world,registry):
    row,adapter=setup(world,registry)
    saved=ShopifyProductComparison.query.one(); summary=json.loads(saved.summary_json)
    summary.pop('attribute_capability_policy'); saved.summary_json=json.dumps(summary); db.session.commit()
    assert display(world[0])['label']=='Checking Changes'
    result=status.refresh([world[0]],adapter=adapter)
    assert result['full_comparisons']==1 and not result['errors']
    assert display(world[0])['label']=='Up to Date'


@pytest.mark.parametrize('kind',['definition','field'])
def test_both_storefront_access_levels_are_read_and_repaired(world,registry,kind):
    row,adapter=setup(world,registry)
    target=next(value for (k,_),value in adapter.resources.items() if k==kind)
    target['access']['storefront']='NONE'
    plan,token=drafts.preview(world[0],adapter)
    changes=[r for r in plan['fields'] if r['field'].startswith('attribute_access.')]
    assert len(changes)==1 and changes[0]['before']=='NONE' and changes[0]['after']=='PUBLIC_READ'
    assert drafts.confirm(token,True,adapter).state=='verified'
    assert target['access']['storefront']=='PUBLIC_READ'


def test_capability_repair_does_not_create_or_mutate_collections(world,registry):
    row,adapter=setup(world,registry,groups=True)
    mappings={r.source_key:r.remote_id for r in ShopifyCollectionIdentity.query.all()}
    collections=copy.deepcopy(adapter.collections)
    disable(adapter); plan,token=drafts.preview(world[0],adapter)
    assert all(group['action']=='Already assigned' for group in plan['collections'])
    before=len(adapter.calls); drafts.confirm(token,True,adapter)
    assert not any('collection_create' in v or 'collection_join' in v for v in adapter.calls[before:])
    assert collections==adapter.collections and mappings=={r.source_key:r.remote_id for r in ShopifyCollectionIdentity.query.all()}


@pytest.mark.parametrize('index',['IN_PROGRESS','FAILED'])
def test_enabled_flag_is_not_confirmation_of_filter_index_or_storefront_filters(world,registry,index):
    row,adapter=setup(world,registry); field(adapter)['capabilities']['adminFilterable']['status']=index
    plan,token=drafts.preview(world[0],adapter)
    assert token is None and plan['classification']=='no_change'
    assert any(index in warning and 'readiness is not confirmed' in warning for warning in plan['warnings'])


def test_capability_timeout_recovers_exact_identity_with_read_only_queries(world,registry):
    row,adapter=setup(world,registry); disable(adapter)
    original=adapter.update_attribute_access
    def lost(*args):
        original(*args); raise ShopifyError('Definition update response lost')
    adapter.update_attribute_access=lost
    _,token=drafts.preview(world[0],adapter); drafts.confirm(token,True,adapter)
    assert row.state=='uncertain'
    mapping=ShopifyAttributeIdentity.query.filter_by(state='uncertain').one(); gid=mapping.remote_id
    before=len(adapter.calls); attrs.reconcile(mapping,adapter,drafts.configuration())
    assert mapping.state=='verified' and mapping.remote_id==gid
    assert all(v=='attribute_read' for v in adapter.calls[before:])
    drafts.reconcile(row,adapter=adapter)
    assert row.state=='verified' and drafts.preview(world[0],adapter)[0]['classification']=='no_change'


def test_capability_drift_invalidates_signed_confirm_before_write(world,registry):
    row,adapter=setup(world,registry); disable(adapter)
    _,token=drafts.preview(world[0],adapter)
    field(adapter)['capabilities']['smartCollectionCondition']['enabled']=True
    before=len(adapter.patches)
    with pytest.raises(ShopifyError): drafts.confirm(token,True,adapter)
    assert len(adapter.patches)==before


def test_all_customer_facing_registry_attributes_use_the_same_policy(world,registry):
    supplied=json.loads((Path(__file__).parents[1]/'deployment/examples/tlc/registry.json').read_text())
    data=json.loads(registry.read_text()); data['attributes']=supplied['attributes']; registry.write_text(json.dumps(data))
    change(world,attributes={attr['name']:[attr['terms'][0]['name']] for attr in supplied['attributes']},variation_attributes=[])
    plan,_=drafts.preview(world[0],CapabilitiesAdapter())
    assert len(plan['attributes'])==7 and not plan['blockers']
    for attr in plan['attributes']:
        spec=attr['resources'][1]['spec']
        assert attr['public'] and spec['access']['storefront']=='PUBLIC_READ'
        assert set(spec['capabilities'])==set(attrs.CAPABILITY_LABELS)


def test_missing_capability_evidence_cannot_be_equal(world,registry):
    row,adapter=setup(world,registry); original=adapter.attribute_resource
    def incomplete(config,kind,spec):
        value=original(config,kind,spec)
        if kind=='field': value.pop('capabilities',None)
        return value
    adapter.attribute_resource=incomplete
    plan,token=drafts.preview(world[0],adapter)
    assert token is None and plan['blockers']
    assert any('capability readback is incomplete' in reason for reason in plan['blockers'])
    assert display(world[0])['label']=='Verification Unavailable'


def test_legacy_mapping_specs_cannot_hide_current_capability_differences(world,registry):
    row,adapter=setup(world,registry)
    for mapping in ShopifyAttributeIdentity.query.all():
        evidence=json.loads(mapping.evidence_json); evidence['spec'].pop('capabilities',None)
        mapping.evidence_json=json.dumps(evidence)
    db.session.commit()
    plan,_=drafts.preview(world[0],adapter)
    disable(adapter,'adminFilterable')
    with pytest.raises(ShopifyError,match='capabilities do not match'):
        attrs.verify(plan,drafts.configuration(),adapter,row.shopify_product_id)


def test_old_signed_readback_cannot_manufacture_new_capability_checkpoint(world,registry):
    row,adapter=setup(world,registry)
    old=copy.deepcopy(drafts.evidence(row)['plan']); old.pop('attribute_capability_policy')
    db.session.delete(ShopifyProductComparison.query.one()); db.session.commit()
    from app.shopify_comparison import record
    assert record(world[0],old,row) is False and ShopifyProductComparison.query.count()==0
    assert display(world[0])['label']=='Checking Changes'
    disable(adapter)
    assert not status.refresh([world[0]],adapter=adapter)['errors']
    assert display(world[0])['label']=='Update Available'


def test_transport_uses_capability_input_not_deprecated_collection_toggle(world,monkeypatch,isolated_transport):
    config=drafts.configuration()
    response=Response({'data':{'metafieldDefinitionUpdate':{'updatedDefinition':{'id':'gid://shopify/MetafieldDefinition/99'},'userErrors':[]}}})
    session=Session(response); monkeypatch.setattr('app.shopify_connection.requests.Session',lambda:session)
    ShopifyAdapter().update_attribute_access(config,'field','gid://shopify/MetafieldDefinition/99',{'namespace':'$app:tlc_attributes','key':'example','capabilities':{'adminFilterable':{'enabled':True}}})
    payload=session.calls[0][1]['json']
    assert payload['query']==ATTRIBUTE_FIELD_ACCESS
    assert payload['variables']['definition']=={'namespace':'$app:tlc_attributes','key':'example','ownerType':'PRODUCT','capabilities':{'adminFilterable':{'enabled':True}}}
    assert 'useAsCollectionCondition' not in json.dumps(payload)
    assert '/2026-10/' in session.calls[0][0]


def test_review_displays_exact_flags_and_shared_definition_impact(world,registry,monkeypatch):
    row,adapter=setup(world,registry); disable(adapter)
    monkeypatch.setattr(drafts,'ShopifyAdapter',lambda:adapter)
    client=_client(current_app._get_current_object())
    response=client.post('/products/1/shopify/review',data={'action':'preview'})
    assert response.status_code==200
    for label in (b'Admin/API filtering',b'Automated collection conditions',b'False',b'True',b'all products referencing'):
        assert label in response.data


def test_variable_layouts_preserve_product_variants_inventory_and_images(variable_world,registry):
    informational(registry)
    original=json.loads(variable_world[1].read_text())['attributes']
    rescan(variable_world,attributes={**original,'Occasion':['Birthday']},variation_attributes=list(original))
    class Combined(VariableAdapter,CapabilitiesAdapter):
        def __init__(self):
            VariableAdapter.__init__(self); self.resources={}; self.attr_values={}; self.resource_failure=None
            self.patches=[]; self.eligible={key:True for key in attrs.CAPABILITY_LABELS}
        def test_connection(self,config):
            shop=VariableAdapter.test_connection(self,config)
            shop['scopes']+=list(attrs.READ_SCOPES|attrs.WRITE_SCOPES); return shop
    adapter=Combined(); _,token=drafts.preview(variable_world[0],adapter)
    row=drafts.confirm(token,True,adapter); assert row.state=='verified',drafts.evidence(row).get('error')
    identities=copy.deepcopy(drafts.evidence(row)['variant_maps']); media=copy.deepcopy(adapter.remote['media'])
    disable(adapter); _,token=drafts.preview(variable_world[0],adapter)
    assert drafts.confirm(token,True,adapter).state=='verified'
    assert identities==drafts.evidence(row)['variant_maps'] and media==adapter.remote['media']
    assert drafts.preview(variable_world[0],adapter)[0]['classification']=='no_change'
