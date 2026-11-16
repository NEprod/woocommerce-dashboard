"""Fictional catalogue/Shopify only. No theme deployment, live writes or webhooks."""
import copy
import json
import sqlite3

import pytest

from app import db, shopify_drafts as drafts, shopify_customisations as storefront
from app.customisations import merge
from app.models import CatalogueOperation, ShopifyAttributeIdentity, ShopifyProductIdentity
from app.storefront_customisations import public_configuration
from test_shopify_drafts import world
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_simple_updates import UpdateAdapter, change
from test_shopify_variables import VariableAdapter, variable_world
from test_shopify_foundation import isolated_transport, Session, Response, TOKEN
from test_shopify_taxonomy_lifecycle import LifecycleAdapter, informational
from test_shopify_taxonomy_products import registry

PUBLIC = {'storefront_enabled':True, 'proof_policy':'required', 'private_notes':'NEVER PUBLIC', 'fields':{
    'name':{'type':'text','label':'Name','required':True,'max_length':30,'order':0,'production_recipe':'PRIVATE PATH'},
    'age':{'type':'number','label':'Age','whole_number':True,'minimum':1,'maximum':120,'order':1},
    'message':{'type':'multiline','label':'Message','order':2}}}


class ConfigurationMixin:
    definition = None
    customer_value = None
    timeout = None

    def customisation_definition(self, config, namespace, key):
        assert namespace == storefront.NAMESPACE and key == storefront.KEY
        self.calls.append('configuration_definition_read')
        return copy.deepcopy(self.definition)

    def customisation_value(self, config, product_id, namespace, key):
        assert self.remote['id'] == product_id
        self.calls.append('configuration_value_read')
        return copy.deepcopy(self.customer_value)

    def durable(self):
        row = ShopifyProductIdentity.query.one()
        dbfile = db.engine.url.database
        with sqlite3.connect(dbfile) as connection:
            encoded, operation_id = connection.execute('SELECT media_evidence_json, operation_id FROM shopify_product_identity').fetchone()
            status = connection.execute('SELECT status FROM catalogue_operation WHERE id=?',(operation_id,)).fetchone()[0]
        assert status == 'running'
        assert encoded == row.media_evidence_json
        assert drafts.evidence(row)['storefront_configuration']['state'] == 'pending'

    def create_attribute_resource(self, config, kind, spec):
        self.durable()
        assert kind == 'field' and spec['type'] == 'json' and spec['namespace'] == storefront.NAMESPACE
        self.calls.append('configuration_definition_write')
        self.definition = {**spec, 'id':'gid://shopify/MetafieldDefinition/801',
            'namespace':'app--77--tlc_customisations','type':{'name':'json'}}
        if self.timeout == 'definition': raise drafts.ShopifyError('Definition outcome uncertain.')
        return {'metafieldDefinitionCreate':{'createdDefinition':{'id':self.definition['id']},'userErrors':[]}}

    def update_attribute_access(self, config, kind, remote_id, spec):
        self.durable()
        assert remote_id == self.definition['id']
        self.calls.append('configuration_access_write')
        self.definition['access'] = spec['access']
        return {'metafieldDefinitionUpdate':{'updatedDefinition':{'id':remote_id},'userErrors':[]}}

    def set_attributes(self, config, values):
        self.durable()
        value, = values
        assert value['ownerId'] == self.remote['id']
        assert value['compareDigest'] == (self.customer_value or {}).get('compareDigest')
        self.calls.append('configuration_value_write')
        self.customer_value = {**value, 'id':'gid://shopify/Metafield/802',
            'namespace':'app--77--tlc_customisations','compareDigest':drafts.digest(value['value'])}
        if self.timeout == 'value': raise drafts.ShopifyError('Configuration outcome uncertain.')
        return {'metafieldsSet':{'metafields':[self.customer_value],'userErrors':[]}}


class CustomerAdapter(ConfigurationMixin, UpdateAdapter): pass
class CustomerVariableAdapter(ConfigurationMixin, VariableAdapter): pass


def create(world, *, enabled=True):
    if enabled: change(world, customisation=PUBLIC)
    adapter = CustomerAdapter()
    plan, token = drafts.preview(world[0], adapter)
    assert not any('write' in call for call in adapter.calls)
    row = drafts.confirm(token, True, adapter)
    assert row.state == 'verified', drafts.evidence(row).get('error')
    return row, adapter


def test_allowlist_resolved_sparse_overrides_and_stable_revision():
    before = copy.deepcopy(PUBLIC)
    resolved = merge(PUBLIC, {'fields':{
        'colour':{'type':'colour','label':'Colour','choices':[{'id':'red','label':'Red','value':'#ff0000','private':'omit'}]}}})
    value = public_configuration(resolved)
    assert [f['id'] for f in value['fields']] == ['name','age','message','colour']
    assert next(f for f in value['fields'] if f['id']=='name')['max_length'] == 30
    assert value['fields'][-1]['choices'] == [{'id':'red','label':'Red','value':'#ff0000'}]
    assert 'PRIVATE' not in json.dumps(value) and value['proof_policy'] == 'required' and PUBLIC == before
    assert value == public_configuration(json.loads(json.dumps(resolved,sort_keys=True)))
    resolved['private_notes'] = 'other private text'
    assert value == public_configuration(resolved)


def test_opt_in_default_disable_and_files_fail_closed():
    assert not public_configuration({})['enabled']
    assert public_configuration({**PUBLIC, 'storefront_enabled':False})['fields'] == []
    for kind in ('photo','file_upload'):
        with pytest.raises(ValueError, match='secure upload'):
            public_configuration({**PUBLIC,'fields':{'photo':{'type':kind,'label':'Photo','required':True}}})
    with pytest.raises(ValueError): public_configuration({'storefront_enabled':True})
    assert not merge(PUBLIC, {'storefront_enabled':False})['storefront_enabled']


def test_reviewed_simple_create_public_ids_readback_no_source_changes(world):
    change(world, customisation=PUBLIC)
    source = world[2].read_bytes()
    row, adapter = create(world)
    data = drafts.evidence(row)
    assert data['storefront_configuration']['state'] == 'verified'
    assert data['storefront_configuration']['definition_id'] == adapter.definition['id']
    assert data['storefront_configuration']['metafield_id'] == adapter.customer_value['id']
    assert world[2].read_bytes() == source
    plan, token = drafts.preview(world[0], adapter)
    assert plan['classification'] == 'no_change' and token is None
    assert not any(f['changed'] for f in plan['fields'])


@pytest.fixture
def stopped_draft(world, monkeypatch):
    from app import shopify_attributes
    change(world, customisation=PUBLIC)
    adapter = CustomerAdapter()
    apply = shopify_attributes.apply

    def interrupted(row, data, config, adapter):
        data['stage'] = 'attribute_registration'
        drafts.save(row, data)
        raise drafts.ShopifyError('Attribute definition outcome uncertain.')

    monkeypatch.setattr(shopify_attributes, 'apply', interrupted)
    _, token = drafts.preview(world[0], adapter)
    row = drafts.confirm(token, True, adapter)
    monkeypatch.setattr(shopify_attributes, 'apply', apply)
    assert row.state == 'uncertain' and adapter.customer_value is None
    return row, adapter, token


def test_interrupted_create_finishes_existing_draft_only_after_fresh_review(world, stopped_draft, monkeypatch):
    row, adapter, create_token = stopped_draft
    row_id, remote_id = row.id, row.shopify_product_id
    media = copy.deepcopy(adapter.remote['media'])
    # Legacy Phase B receipts already contain the failure diagnostic. Recovery
    # works after reloading the row, without any new marker or migration.
    db.session.expire_all()
    row = db.session.get(ShopifyProductIdentity, row_id)
    adapter.calls.clear()
    for _ in range(2):
        drafts.reconcile(row, adapter=adapter)
        assert row.state == 'creation_review_required' and row.verification_state == 'base_verified'
        assert row.verified_at is None and row.shopify_product_id == remote_id
        assert drafts.evidence(row)['creation_remaining_review'] == ['customer configuration']
    plan, token = drafts.preview(world[0], adapter)
    assert token and plan['classification'] == 'update' and not plan.get('publication')
    assert plan['remote_id'] == remote_id and plan['customisations']['value_changed']
    with pytest.raises(drafts.ShopifyError, match='no duplicate creation'):
        drafts.confirm(create_token, True, adapter)
    with monkeypatch.context() as scoped:
        scoped.setattr('app.shopify_capacity.background', lambda: {'authority': 'automatic'})
        with pytest.raises(drafts.ShopifyError, match='fresh manual Review'):
            drafts.confirm(token, True, adapter)
    with pytest.raises(drafts.ShopifyError, match='acknowledgement'):
        drafts.confirm(token, False, adapter)
    assert all(call.endswith('_read') for call in adapter.calls)
    adapter.calls.clear()
    drafts.confirm(token, True, adapter)
    assert [call for call in adapter.calls if not call.endswith('_read')] == [
        'configuration_definition_write', 'configuration_value_write']
    assert row.state == row.verification_state == 'verified' and row.verified_at
    assert row.shopify_product_id == remote_id and adapter.remote['status'] == 'DRAFT'
    assert adapter.remote['media'] == media and ShopifyProductIdentity.query.count() == 1
    assert 'creation_remaining_review' not in drafts.evidence(row)


@pytest.mark.parametrize('change_kind', ['attempted', 'unexplained_update', 'different_stage', 'different_operation', 'remote_drift', 'not_draft'])
def test_interrupted_create_never_defers_uncertain_mutations_or_drift(world, stopped_draft, change_kind):
    row, adapter, _ = stopped_draft
    data = drafts.evidence(row)
    if change_kind == 'attempted':
        data['storefront_configuration'] = {'state': 'pending', 'value_attempted': True}
    elif change_kind == 'unexplained_update':
        data['update_attempted'] = True
    elif change_kind == 'different_stage':
        data['failure_diagnostic']['stage'] = 'customisation_configuration'
    elif change_kind == 'different_operation':
        data['failure_diagnostic']['operation_id'] = 'unrelated-operation'
    elif change_kind == 'remote_drift':
        adapter.definition = {'id': 'gid://shopify/MetafieldDefinition/801', 'key': storefront.KEY,
            'namespace': 'app--77--tlc_customisations', 'ownerType': 'PRODUCT', 'type': {'name': 'json'},
            'access': {'admin': 'MERCHANT_READ', 'storefront': 'NONE'}}
    else:
        adapter.remote['status'] = 'ACTIVE'
    drafts.save(row, data)
    adapter.calls.clear()
    drafts.reconcile(row, adapter=adapter)
    assert row.state == 'uncertain' and row.verified_at is None
    assert not drafts.evidence(row).get('creation_remaining_review')
    with pytest.raises(drafts.ShopifyError, match='Recovery required'):
        drafts.preview(world[0], adapter)
    assert all(call.endswith('_read') for call in adapter.calls)


def test_first_use_attribute_readback_loss_can_finish_retained_create(world, registry):
    from app import shopify_attributes

    class Adapter(ConfigurationMixin, LifecycleAdapter):
        def attribute_resource(self, config, kind, spec):
            result = super().attribute_resource(config, kind, spec)
            if kind == 'definition' and result and self.resource_failure == 'readback':
                self.resource_failure = None
                raise drafts.ShopifyError('The definition was created but the readback was interrupted.')
            return result

        def create_attribute_resource(self, config, kind, spec):
            if kind == 'field' and spec['namespace'] == storefront.NAMESPACE:
                return super().create_attribute_resource(config, kind, spec)
            return LifecycleAdapter.create_attribute_resource(self, config, kind, spec)

        def update_attribute_access(self, config, kind, remote_id, spec):
            return LifecycleAdapter.update_attribute_access(self, config, kind, remote_id, spec)

        def set_attributes(self, config, values):
            if values[0]['namespace'] == storefront.NAMESPACE:
                return super().set_attributes(config, values)
            return LifecycleAdapter.set_attributes(self, config, values)

    informational(registry)
    change(world, customisation=PUBLIC, attributes={'Occasion': ['Birthday']},
           categories=['Cards > Birthday Cards'], storefront_collections=['Personalised'])
    adapter = Adapter()
    adapter.resource_failure = 'readback'
    _, token = drafts.preview(world[0], adapter)
    row = drafts.confirm(token, True, adapter)
    remote_id = row.shopify_product_id
    resource = ShopifyAttributeIdentity.query.one()
    definition_id = resource.remote_id
    assert row.state == resource.state == 'uncertain' and adapter.customer_value is None
    data = drafts.evidence(row)
    assert data['update_attempted'] is True  # Initial collection joins set this flag, too.
    assert {step for step in data['completed_steps'] if step.startswith('join:')} == {
        'join:categories:cards', 'join:categories:birthday', 'join:storefront_collections:personalised'}
    drafts.reconcile(row, adapter=adapter)
    assert row.state == 'uncertain'  # Must resolve the uncertain resource first.
    shopify_attributes.reconcile(resource, adapter, drafts.configuration())
    saved = drafts.evidence(row)
    unexpected = copy.deepcopy(saved)
    unexpected['completed_steps'].append('product_update')
    drafts.save(row, unexpected)
    drafts.reconcile(row, adapter=adapter)
    assert row.state == 'uncertain'  # An unrelated update is not a collection join.
    drafts.save(row, saved)
    drafts.reconcile(row, adapter=adapter)
    assert resource.state == 'verified' and row.state == 'creation_review_required'
    assert drafts.evidence(row)['verification']['attributes'] == 'remaining_review'
    # Even with an intended Live product, this step may only finish its Draft.
    change(world, live=True)
    plan, token = drafts.preview(world[0], adapter)
    assert token and plan['remote_id'] == remote_id and not plan.get('publication')
    assert drafts.confirm(token, True, adapter).state == 'verified'
    assert resource.remote_id == definition_id and adapter.calls.count('attribute_definition_create') == 1
    assert row.shopify_product_id == remote_id and adapter.calls.count('product_create') == 1
    assert adapter.remote['status'] == 'DRAFT' and adapter.customer_value is not None


def test_reviewed_configuration_uses_collection_plus_local_fields_and_is_repeatable(world):
    from test_customisation_additive import SHARED, LOCAL
    change(world, customisation=SHARED)
    child = world[1] / 'product_info.json'
    child.write_text(json.dumps({'customisation': LOCAL}))
    before = child.read_bytes(), world[2].read_bytes()
    row, adapter = create(world, enabled=False)
    published = json.loads(adapter.customer_value['value'])
    assert [f['id'] for f in published['fields']] == ['recipient', 'age', 'personal_message']
    assert published['fields'][0]['required'] is True and published['fields'][0]['max_length'] == 30
    writes = [call for call in adapter.calls if 'write' in call]
    plan, token = drafts.preview(world[0], adapter)
    assert plan['classification'] == 'no_change' and token is None
    assert [call for call in adapter.calls if 'write' in call] == writes
    assert (child.read_bytes(), world[2].read_bytes()) == before


def test_scoped_update_changes_config_only_not_price_or_channel(world):
    row, adapter = create(world)
    remote_id = row.shopify_product_id
    variant = copy.deepcopy(adapter.remote['simpleVariants'])
    media = copy.deepcopy(adapter.remote['media'])
    change(world, price='99.00', description='Unrelated pending change',customisation={**PUBLIC,'fields':{'name':{**PUBLIC['fields']['name'],'label':'Recipient name'}}})
    adapter.calls.clear()
    plan, token = drafts.preview(world[0], adapter, scope='customisations')
    assert plan['classification'] == 'update' and plan['scope'] == 'customisations'
    assert not any('write' in c for c in adapter.calls)
    with pytest.raises(drafts.ShopifyError, match='acknowledgement'): drafts.confirm(token,False,adapter)
    result = drafts.confirm(token, True, adapter)
    assert result.state == 'verified' and result.shopify_product_id == remote_id
    assert adapter.remote['simpleVariants'] == variant and adapter.remote['media'] == media
    assert 'product_update' not in adapter.calls and 'product_create' not in adapter.calls
    assert drafts.evidence(result)['customisation_update']['state'] == 'verified'
    assert json.loads(adapter.customer_value['value'])['fields'][0]['label'] == 'Recipient name'
    assert db.session.get(CatalogueOperation,row.operation_id).status == 'succeeded'
    full, _ = drafts.preview(world[0],adapter)
    assert any(f['field']=='price' and f['changed'] for f in full['fields'])


def test_access_only_does_not_rewrite_value_or_identity(world):
    row, adapter = create(world)
    ids = row.shopify_product_id, adapter.definition['id'], adapter.customer_value['id']
    adapter.definition['access']['storefront'] = 'NONE'
    adapter.calls.clear()
    plan, token = drafts.preview(world[0],adapter)
    assert not plan['customisations']['value_changed'] and plan['customisations']['definition_changed']
    drafts.confirm(token,True,adapter)
    assert 'configuration_access_write' in adapter.calls and 'configuration_value_write' not in adapter.calls
    assert ids == (row.shopify_product_id,adapter.definition['id'],adapter.customer_value['id'])


@pytest.mark.parametrize('change_kind',['local','remote','store'])
def test_stale_or_altered_signed_confirmation_rejected(world,change_kind):
    row, adapter = create(world)
    change(world, customisation={**PUBLIC,'fields':{'name':{**PUBLIC['fields']['name'],'label':'New label'}}})
    _, token = drafts.preview(world[0],adapter,scope='customisations')
    if change_kind=='local': change(world,customisation=PUBLIC)
    elif change_kind=='remote': adapter.customer_value['compareDigest']='other-digest'
    else:
        original=adapter.test_connection
        adapter.test_connection=lambda c:dict(original(c),shop_id='gid://shopify/Shop/999')
    adapter.calls.clear()
    with pytest.raises(drafts.ShopifyError): drafts.confirm(token,True,adapter)
    assert not any('write' in c for c in adapter.calls)
    with pytest.raises(drafts.ShopifyError): drafts.confirm(token+'tampered',True,adapter)


def test_uncertain_scoped_write_recovery_reads_only_and_restart(world):
    row, adapter = create(world)
    change(world,customisation={**PUBLIC,'fields':{'name':{**PUBLIC['fields']['name'],'max_length':12}}})
    _, token = drafts.preview(world[0],adapter,scope='customisations')
    adapter.timeout='value'
    row=drafts.confirm(token,True,adapter)
    assert row.state=='uncertain'
    with pytest.raises(drafts.ShopifyError,match='reconcile|Recovery'): drafts.preview(world[0],adapter,scope='customisations')
    count=adapter.calls.count('configuration_value_write')
    rowid=row.id; db.session.remove()
    row=db.session.get(ShopifyProductIdentity,rowid)
    drafts.reconcile(row,adapter=adapter)
    assert row.state=='verified' and adapter.calls.count('configuration_value_write')==count
    assert drafts.evidence(row)['plan']['managed']['namespace']
    assert drafts.evidence(row)['storefront_configuration']['state']=='verified'


def test_reviewed_disable_no_delete_and_no_all_product_activation(world):
    row, adapter=create(world)
    change(world,customisation={**PUBLIC,'storefront_enabled':False})
    _,token=drafts.preview(world[0],adapter,scope='customisations')
    drafts.confirm(token,True,adapter)
    assert not json.loads(adapter.customer_value['value'])['enabled']
    assert adapter.definition['id']=='gid://shopify/MetafieldDefinition/801'
    assert not any('delete' in c for c in adapter.calls)


def test_never_enabled_retained_product_needs_no_disabled_value_creation(world):
    row,adapter=create(world,enabled=False)
    plan,token=drafts.preview(world[0],adapter,scope='customisations')
    assert plan['classification']=='no_change' and token is None
    assert adapter.definition is None and adapter.customer_value is None


def test_missing_scope_preview_blocks_only_write(world):
    row, adapter=create(world)
    adapter.definition['access']['storefront']='NONE'
    base=adapter.test_connection
    adapter.test_connection=lambda c:dict(base(c),scopes=['read_products'])
    plan,token=drafts.preview(world[0],adapter,scope='customisations')
    assert plan['classification']=='update' and token is None
    assert any('write_products' in b for b in plan['blockers'])


def test_lost_definition_response_is_read_only_resolved_before_fresh_value_review(world):
    row, adapter = create(world,enabled=False)
    change(world,customisation=PUBLIC)
    _,token=drafts.preview(world[0],adapter,scope='customisations')
    adapter.timeout='definition'
    drafts.confirm(token,True,adapter)
    assert row.state=='uncertain' and adapter.customer_value is None
    drafts.reconcile(row,adapter=adapter)
    assert row.state=='verified' and drafts.evidence(row)['customisation_update']['state']=='review_required'
    assert adapter.calls.count('configuration_definition_write')==1
    adapter.timeout=None
    _,token=drafts.preview(world[0],adapter,scope='customisations')
    drafts.confirm(token,True,adapter)
    assert row.state=='verified' and adapter.calls.count('configuration_definition_write')==1
    assert adapter.customer_value


def test_retained_definition_and_value_id_replacements_are_blocked(world):
    row,adapter=create(world)
    adapter.customer_value['id']='gid://shopify/Metafield/999'
    with pytest.raises(drafts.ShopifyError,match='removed or replaced'):
        drafts.preview(world[0],adapter,scope='customisations')


def test_enabled_public_only_fingerprint_and_private_notes(world):
    from app.shopify_products import source_fingerprint
    row,adapter=create(world)
    images=drafts.local_plan(world[0])['images']
    before=source_fingerprint(world[0],images)
    private=copy.deepcopy(PUBLIC);private['private_notes']='another'
    change(world,customisation=private)
    assert source_fingerprint(world[0],images)==before
    private['proof_policy']='none'
    change(world,customisation=private)
    assert source_fingerprint(world[0],images)!=before
    private['fields']['name']['label']='Recipient'
    change(world,customisation=private)
    assert source_fingerprint(world[0],images)!=before


def test_both_variable_layouts_reuse_stable_parent_and_variant_ids(variable_world):
    product,path,*_=variable_world
    document=json.loads(path.read_text());document['customisation']=copy.deepcopy(PUBLIC);path.write_text(json.dumps(document))
    adapter=CustomerVariableAdapter()
    plan,token=drafts.preview(product,adapter)
    assert not plan['blockers']
    row=drafts.confirm(token,True,adapter)
    assert row.state=='verified', drafts.evidence(row).get('error')
    identities=copy.deepcopy(drafts.evidence(row)['variant_maps'])
    plan,token=drafts.preview(product,adapter)
    assert plan['classification']=='no_change' and token is None
    document['customisation']['fields']['name']['label']='Your name';path.write_text(json.dumps(document))
    _,token=drafts.preview(product,adapter,scope='customisations')
    drafts.confirm(token,True,adapter)
    assert drafts.evidence(row)['variant_maps']==identities
    assert adapter.calls.count('product_create')==1


def test_normal_review_and_editor_render_scoped_configuration(world,monkeypatch):
    row, adapter=create(world)
    change(world,customisation={**PUBLIC,'storefront_enabled':False})
    monkeypatch.setattr(drafts,'ShopifyAdapter',lambda:adapter)
    monkeypatch.setattr(storefront,'ShopifyAdapter',lambda:adapter)
    from flask import current_app
    client=_client(current_app)
    # Resolve route path from url_for rather than relying on a second publishing endpoint.
    from flask import url_for
    with current_app.test_request_context():
        target=url_for('commerce.drafts',product_id=world[0].id)
    response=client.post(target,data={'action':'preview','customisations_only':'yes'})
    assert response.status_code==200
    assert b'Customer customisations' in response.data
    assert b'Enabled:</strong> Yes' in response.data
    assert b'not authorising other product fields' in response.data
    _,token=drafts.preview(world[0],adapter,scope='customisations')
    response=client.post(target,data={'action':'confirm','review':token,'acknowledge':'yes'},headers={'Accept':'application/json'})
    assert response.status_code==202 and response.json['background_active'],response.json
    from app import scheduled_tasks as tasks, manual_publishing as manual
    operation_id=response.json['operation_id']
    claimed=tasks.claim('customisation-worker',manual_only=True)
    assert claimed and claimed[0]==manual.KEY
    tasks.run_slice(*claimed,'customisation-worker',adapter=adapter)
    operation=db.session.get(CatalogueOperation,operation_id)
    assert operation.status=='succeeded',operation.error
    operation=db.session.get(CatalogueOperation,row.operation_id)
    assert json.loads(operation.scope)['mode']=='customisations'
    assert drafts.evidence(row)['plan']['managed']['namespace']


def test_exact_transport_queries_remain_read_only_bounded_and_2026_10(monkeypatch):
    from app.shopify_connection import ShopifyAdapter,ShopifyConfiguration,CUSTOMISATION_VALUE,CUSTOMISATION_DEFINITION
    adapter=ShopifyAdapter()
    config=ShopifyConfiguration('fictional.myshopify.com',TOKEN,api_version='2026-10')
    for query in (CUSTOMISATION_VALUE,CUSTOMISATION_DEFINITION):
        session=Session(Response({'data':{}}))
        monkeypatch.setattr('app.shopify_connection.requests.Session',lambda:session)
        adapter._read(config,query,{})
        url,kwargs=session.calls[0]
        assert url=='https://fictional.myshopify.com/admin/api/2026-10/graphql.json'
        assert kwargs['timeout']==(5,15) and kwargs['allow_redirects'] is False
        with pytest.raises(drafts.ShopifyError):adapter._write(config,query,{})


def test_staged_extension_configuration_and_liquid_contract():
    import re
    import tomllib
    from pathlib import Path
    root=Path(__file__).parent.parent/'shopify/extensions/tlc-customisations'
    config=tomllib.loads((root/'shopify.extension.toml').read_text())
    assert config['type']=='theme' and 'client_id' not in config
    liquid=(root/'blocks/customer-fields.liquid').read_text()
    schema=json.loads(re.search(r'{% schema %}(.*?){% endschema %}',liquid,re.S)[1])
    assert schema['target']=='section'
    assert 'json | escape' in liquid and 'closest.product | default: product' in liquid
    assert not any(s in liquid.lower() for s in ('client_secret','access_token','/uploads/'))
