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
        assert json.loads(pending.evidence_json)['registration_phase']=='settings_sent'
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


@pytest.mark.parametrize('boundary',['pre_dispatch_pause','crash_after_ack','crash_before_ack'])
def test_manual_partial_update_resumes_only_safe_work_on_the_existing_draft(world,registry,monkeypatch,boundary):
    from datetime import timedelta
    from app.manual_publishing import KEY
    from app.models import CatalogueOperation, Product, ScheduledTask, ShopifyProductIdentity
    from app.shopify_capacity import RequestNotSent
    from app.utils.operation_control import recover_interrupted_operations
    from test_automatic_publishing import queued
    from test_manual_publishing_worker import accepted, products_badge, step

    class ProcessExited(BaseException):
        pass

    monkeypatch.setenv('TLC_SCHEDULER_READY_AT','99999999999')
    row,adapter=setup(world,registry)
    queued(world[0])  # The fixture inserts the product outside the catalogue scanner.
    row_id,product_id,remote_id=row.id,world[0].id,row.shopify_product_id
    before=adapter.calls.copy()
    patch_count=len(adapter.patches)
    disable(adapter,'smartCollectionCondition')
    original=adapter.update_attribute_access
    read=adapter.attribute_resource
    def restart_at_boundary(*args):
        adapter.update_attribute_access=original
        if boundary=='pre_dispatch_pause':
            raise RequestNotSent('Worker budget reached before this definition update')
        result=original(*args)
        if boundary=='crash_before_ack':
            raise ProcessExited()
        def exit_on_readback(*args):
            adapter.attribute_resource=read
            raise ProcessExited()
        adapter.attribute_resource=exit_on_readback
        return result
    adapter.update_attribute_access=restart_at_boundary
    operation,_=accepted(world,adapter)
    if boundary=='pre_dispatch_pause':
        step(adapter)
        assert db.session.get(CatalogueOperation,operation).status=='pending'
        assert len(adapter.patches)==patch_count
    else:
        with pytest.raises(ProcessExited):
            step(adapter)
        assert db.session.get(CatalogueOperation,operation).status=='running'
        assert len(adapter.patches)==patch_count+1
        task=db.session.get(ScheduledTask,KEY)
        task.lease_until=drafts.now()-timedelta(seconds=1)
        db.session.commit()
    db.session.remove()
    db.engine.dispose()
    assert recover_interrupted_operations()==0
    step(adapter,owner='replacement-update-worker')
    row=db.session.get(ShopifyProductIdentity,row_id)
    assert row.shopify_product_id==remote_id
    uncertain=boundary=='crash_before_ack'
    assert row.state==('uncertain' if uncertain else 'verified')
    receipt=db.session.get(CatalogueOperation,operation)
    assert (receipt.status,receipt.recovery_state)==(('partial','review_required') if uncertain else ('succeeded','none'))
    assert len(adapter.patches)==patch_count+1
    assert not {'product_create','product_update','binary_upload','staged_uploads','attribute_assignment_write'}.intersection(adapter.calls[len(before):])
    assert products_badge(db.session.get(Product,product_id))['label']==('Recovery Required' if uncertain else 'Up to Date')


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


def test_reconciled_attribute_stop_admits_signed_review_on_retained_draft(variable_world,registry,monkeypatch):
    from app import manual_publishing as manual
    from app.models import CatalogueOperation
    from app.shopify_capacity import RequestNotSent
    from test_manual_publishing_worker import accepted, products_badge, step
    from test_shopify_markets import MarketMixin
    from test_shopify_publication_sales import VariableAcceptanceAdapter
    from test_variable_creation_recovery import RealPagedVariable, large_variable, finish_uploads, assert_complete

    monkeypatch.setenv('TLC_SCHEDULER_READY_AT','99999999999')
    informational(registry)
    class Adapter(MarketMixin,RealPagedVariable,CapabilitiesAdapter):
        def __init__(self):
            super().__init__()
            self.resources={}; self.attr_values={}; self.resource_failure=None
            self.patches=[]; self.eligible={key:True for key in attrs.CAPABILITY_LABELS}
            self.memberships.clear()
            self.market_rows=self.market_rows[:1]  # UK only, same availability as the development Draft
            self.stop_on_settings=True
            self.defer_once=True
        def test_connection(self,config):
            shop=super().test_connection(config)
            shop['scopes']+=list(attrs.READ_SCOPES|attrs.WRITE_SCOPES)
            return shop
        def update_attribute_access(self,*args):
            if self.stop_on_settings:
                raise ShopifyError('Original registration did not acknowledge an update')
            if self.defer_once:
                self.defer_once=False
                raise RequestNotSent('Recovery worker deferred before dispatch')
            return CapabilitiesAdapter.update_attribute_access(self,*args)
        def set_publication(self,config,product_id,pid,publish):
            if pid in {'gid://shopify/Publication/2','gid://shopify/Publication/3'}:
                return VariableAcceptanceAdapter.set_publication(self,config,product_id,pid,publish)
            return super().set_publication(config,product_id,pid,publish)

    product=large_variable(variable_world)
    source=variable_world[1]
    approved={**json.loads(source.read_text()),'live':True,'shopify_channels':{'online-store':True,'shop':True,'pos':True},
              'categories':['Cards']}
    source.write_text(json.dumps(approved))
    monkeypatch.setattr(attrs,'assigned',lambda _:[{
        'key':'attr-occasion','name':'Occasion','role':'filter','public':True,
        'terms':[{'key':'term-birthday','name':'Birthday','aliases':[],'state':'active'}],
    }])
    adapter=Adapter()
    row=finish_uploads(product,adapter)
    original=db.session.get(CatalogueOperation,row.operation_id)
    original_id, remote_id=original.id,row.shopify_product_id
    historical=(original.status,original.recovery_state,original.error,original.finished_at)
    assert historical[:2]==('partial','review_required') and row.state=='uncertain'
    assert drafts.evidence(row)['failure_diagnostic']['stage']=='attribute_registration'
    field_mapping=ShopifyAttributeIdentity.query.filter_by(state='uncertain').one()
    field_id=field_mapping.remote_id
    attrs.reconcile(field_mapping,adapter,drafts.configuration())
    drafts.reconcile(row,adapter=adapter)
    assert row.state=='attribute_review_required' and row.verification_state=='base_verified'
    prior=drafts.evidence(row)
    assert prior['update_attempted'] is True  # original Create already joined Cards
    assert any(s.startswith('join:') for s in prior['completed_steps'])
    original_summary=json.loads(original.scope)['operation_summary']
    original_variants=copy.deepcopy(prior['variant_maps'])
    original_media=copy.deepcopy(adapter.remote['media'])

    source.write_text(json.dumps({**approved,'description':'An unrelated change must not be executed by recovery.'}))
    plan,token=drafts.preview(product,adapter)
    assert token is None and any('only the remaining informational' in error for error in plan['blockers'])
    source.write_text(json.dumps(approved))
    _,token=drafts.preview(product,adapter)
    reviewed=drafts.serializer().loads(token)
    for unsafe in ({'remote_id':'gid://shopify/Product/999'}, {'identity_digest':'stale'}, {'scope':'inventory'}):
        with pytest.raises(ShopifyError,match='no new Shopify write'):
            manual.require_no_prior_recovery(product.id,{**reviewed,**unsafe})
    # Even a matching signed identity must not override another pending mutation.
    conflict={**prior,'package_assignments':{'old':{'state':'uncertain'}}}
    drafts.save(row,conflict)
    with pytest.raises(ShopifyError,match='no new Shopify write'):
        manual.require_no_prior_recovery(product.id,{**reviewed,'identity_digest':drafts.digest(conflict)})
    drafts.save(row,prior)

    before=list(adapter.calls)
    adapter.stop_on_settings=False
    new_op,_=accepted(variable_world,adapter)
    assert adapter.calls.count('attribute_access_update')==before.count('attribute_access_update')
    step(adapter)
    assert db.session.get(CatalogueOperation,new_op).status=='pending' and not adapter.patches
    for _ in range(6):
        if db.session.get(CatalogueOperation,new_op).finished_at:
            break
        step(adapter,owner='replacement-recovery-worker')
    receipt=db.session.get(CatalogueOperation,new_op)
    assert (receipt.status,receipt.recovery_state)==('succeeded','none'),receipt.error
    finished=drafts.evidence(row)
    assert finished['reconciled_attribute_update']=={'original_operation':original_id,'operation':new_op}
    assert_complete(product,row,adapter)
    assert row.shopify_product_id==remote_id and finished['variant_maps']==original_variants
    assert adapter.remote['media']==original_media and field_mapping.remote_id==field_id
    assert len(adapter.patches)==1 and adapter.calls.count('attribute_assignment_write')==1
    assert not {'product_update','variants_create','variants_update','binary_upload','product_create','attribute_field_create'}.intersection(adapter.calls[len(before):])
    assert adapter.remote['status']=='ACTIVE' and adapter.memberships=={f'gid://shopify/Publication/{i}' for i in (1,2,3)}
    assert adapter.market_context(drafts.configuration(),remote_id,['GB'])['c0'] is True
    assert products_badge(product)['label']=='Up to Date'
    assert (original.status,original.recovery_state,original.error,original.finished_at)==historical
    assert json.loads(original.scope)['operation_summary']==original_summary


@pytest.mark.parametrize('boundary',['before_dispatch','after_ack','uncertain'])
def test_manual_53_image_registration_restart(variable_world,registry,monkeypatch,boundary):
    from app.models import CatalogueOperation, Product, ShopifyProductIdentity
    from app.shopify_capacity import BackgroundYield, RequestNotSent
    from test_variable_creation_recovery import RealPagedVariable, large_variable, assert_complete
    from test_manual_publishing_worker import accepted, products_badge, step

    monkeypatch.setenv('TLC_SCHEDULER_READY_AT','99999999999')
    informational(registry)
    class Adapter(RealPagedVariable,CapabilitiesAdapter):
        def __init__(self):
            RealPagedVariable.__init__(self)
            self.resources={}; self.attr_values={}; self.resource_failure=None
            self.patches=[]; self.eligible={key:True for key in attrs.CAPABILITY_LABELS}
            self.interrupted=False; self.pause_after_ack=False
        def test_connection(self,config):
            shop=RealPagedVariable.test_connection(self,config)
            shop['scopes']+=list(attrs.READ_SCOPES|attrs.WRITE_SCOPES)
            return shop
        def update_attribute_access(self,*args):
            if boundary=='before_dispatch' and not self.interrupted:
                self.interrupted=True
                raise RequestNotSent('Test: request not sent')
            result=CapabilitiesAdapter.update_attribute_access(self,*args)
            if not self.interrupted:
                self.interrupted=True
                if boundary=='uncertain':
                    raise BackgroundYield('Test: no reliable response acknowledgement after dispatch')
                self.pause_after_ack=boundary=='after_ack'
            return result
        def attribute_resource(self,config,kind,spec):
            if kind=='field' and self.pause_after_ack:
                self.pause_after_ack=False
                raise RequestNotSent('Test: readback deferred after acknowledged write')
            return CapabilitiesAdapter.attribute_resource(self,config,kind,spec)

    # Index the two actual option axes first; the additional attribute is informational.
    product=large_variable(variable_world)
    product_id=product.id
    from unittest.mock import patch
    from app import shopify_attributes
    # Model the additional registry assignment without rescanning the fixture's
    # explicitly indexed large gallery (preserves its local image projections).
    with patch.object(shopify_attributes,'assigned',return_value=[{
            'key':'attr-occasion','name':'Occasion','role':'filter','public':True,
            'terms':[{'key':'term-birthday','name':'Birthday','aliases':[],'state':'active'}],
        }]):
        adapter=Adapter()
        operation,_=accepted(variable_world,adapter)
        for _ in range(18):
            step(adapter)
            if adapter.interrupted:
                break
        row=ShopifyProductIdentity.query.one()
        kept_id,remote_id=row.id,row.shopify_product_id
        writes=list(adapter.calls)
        assert writes.count('binary_upload')==53 and writes.count('product_create')==1
        variant_maps=copy.deepcopy(drafts.evidence(row)['variant_maps'])
        media_ids=[image['id'] for image in adapter.remote['media']['nodes']]
        assert len(variant_maps)==14 and len(media_ids)==53
        task=db.session.get(CatalogueOperation,operation)
        assert task.status==('partial' if boundary=='uncertain' else 'pending')
        remote=copy.deepcopy(adapter.__dict__)
        db.session.remove()
        db.engine.dispose()
        replacement=Adapter()
        replacement.__dict__.update(remote)
        for _ in range(5):
            step(replacement,owner='replacement-registration-worker')
            task=db.session.get(CatalogueOperation,operation)
            if task.finished_at:
                break
        row=db.session.get(ShopifyProductIdentity,kept_id)
        assert row.operation_id==operation and row.shopify_product_id==remote_id
        assert drafts.evidence(row)['variant_maps']==variant_maps
        assert [image['id'] for image in replacement.remote['media']['nodes']]==media_ids
        if boundary=='uncertain':
            assert task.recovery_state=='review_required'
            assert replacement.calls==writes
        else:
            assert_complete(product,row,replacement)
            assert task.status=='succeeded'
            assert len(replacement.patches)==1
            assert replacement.calls.count('variants_create')==writes.count('variants_create')==1
            assert replacement.calls.count('variants_update')==writes.count('variants_update')==1
            assert products_badge(db.session.get(Product,product_id))['label']=='Up to Date'
