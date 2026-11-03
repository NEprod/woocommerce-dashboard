"""One customs field through existing editor/scan/Shopify paths; isolated only."""
import copy
import json
import time

import pytest
from flask import current_app

from app import db, shopify_drafts as drafts, automatic_publishing as publisher
from app.catalogue_fields import validate_extensions
from app.commerce import catalogue_snapshot
from app.models import Product, ShopifyProductIdentity, ShopifyPublishJob, CatalogueOperation
from app.product_info import validate_product_info
from app.shopify_connection import ShopifyError, SIMPLE_QUERY, VARIABLE_FIELDS
from app.shopify_simple_fields import review_rows
from app.taxonomy_assignments import product_document
from test_metadata_editor_core import core, save
from test_phase3_woo_publish_preview import preview_app
from test_shopify_drafts import world
from test_shopify_simple_updates import created, change, UpdateAdapter
from test_shopify_variables import variable_world, create as create_variable
from test_automatic_publishing import authorise, execute
from test_shopify_publication_sales import AcceptanceAdapter, VariableAcceptanceAdapter

HS = 'harmonized_system_code'
REMOTE = 'harmonizedSystemCode'


@pytest.mark.parametrize('value', ['490900', '001234', '0012345678901', ''])
def test_valid_string_codes_and_blank(value):
    data = {HS: value}
    validate_extensions(data)
    assert validate_product_info(data, 'override').valid
    assert validate_product_info(dict(data, collection_type='Simple', sku_prefix='HS-'), 'collection').valid


@pytest.mark.parametrize('value', ['12345', '12345678901234', '49.0900', '49 0900', 'hs490900',
                                  ' 490900', '490900\n', '４９０９００', '٤٩٠٩٠٠', 490900, None, True])
def test_invalid_codes_fail_clearly(value):
    data = {HS: value}
    with pytest.raises(ValueError, match='6–13 digits'):
        validate_extensions(data)
    errors = validate_product_info(data, 'override').errors
    assert errors and errors[0].path == '$.' + HS and '6–13 digits' in errors[0].message


def test_inheritance_sparse_override_and_snapshot(world):
    product, folder, _ = world
    change(world, **{HS: '001234'})
    override = folder/'product_info.json'
    assert catalogue_snapshot(product).data['shipping'][HS] == '001234'
    override.write_text(json.dumps({HS: '490900'}))
    assert product_document(product)[HS] == '490900'
    assert drafts.local_plan(product)['managed']['variant']['inventoryItem'][REMOTE] == '490900'
    override.write_text(json.dumps({HS: ''}))
    assert REMOTE not in drafts.local_plan(product)['managed']['variant']['inventoryItem']
    override.write_text('{}')
    assert catalogue_snapshot(product).data['shipping'][HS] == '001234'


def test_manual_simple_hs_only_update_and_readback(world):
    product, identity, adapter = created(world)
    remote_id = identity.shopify_product_id
    variants = copy.deepcopy(adapter.remote['simpleVariants']['nodes'])
    media = copy.deepcopy(adapter.remote['media'])
    change(world, **{HS: '0012345678901'})
    plan, token = drafts.preview(product, adapter)
    assert plan['classification'] == 'update'
    field = next(r for r in review_rows(plan) if r['label'] == 'HS Code')
    assert (field['before'], field['after'], field['action']) == ('Not set', '0012345678901', 'Change')
    assert drafts.update_inputs(plan) == ({'id': remote_id},
        {'id': variants[0]['id'], 'inventoryItem': {REMOTE: '0012345678901'}})
    adapter.calls.clear(); adapter.inputs.clear()
    result = drafts.confirm(token, True, adapter)
    assert result.state == 'verified' and result.shopify_product_id == remote_id
    assert adapter.inputs == [{'id': variants[0]['id'], 'inventoryItem': {REMOTE: '0012345678901'}}]
    assert adapter.remote['simpleVariants']['nodes'][0]['inventoryItem']['id'] == variants[0]['inventoryItem']['id']
    assert adapter.remote['media'] == media
    assert not {'product_create', 'product_update', 'stage_write', 'alt_update', 'order_update'} & set(adapter.calls)
    plan, token = drafts.preview(product, adapter)
    assert plan['classification'] == 'no_change' and token is None


@pytest.mark.parametrize('blank', [False, True])
def test_unspecified_never_clears_existing_shopify_code(world, blank):
    product, identity, adapter = created(world)
    adapter.remote['simpleVariants']['nodes'][0]['inventoryItem'][REMOTE] = '490900'
    if blank: change(world, **{HS: ''})
    change(world, description='Unrelated reviewed description')
    plan, token = drafts.preview(product, adapter)
    assert REMOTE not in plan['managed']['variant']['inventoryItem']
    assert not any(r['field'] == REMOTE for r in plan['fields'])
    drafts.confirm(token, True, adapter)
    assert adapter.remote['simpleVariants']['nodes'][0]['inventoryItem'][REMOTE] == '490900'


def test_simple_create_includes_code_and_queries_read_it(world):
    change(world, **{HS: '490900'})
    adapter = UpdateAdapter()
    plan, token = drafts.preview(world[0], adapter)
    result = drafts.confirm(token, True, adapter)
    assert result.state == 'verified'
    assert adapter.remote['simpleVariants']['nodes'][0]['inventoryItem'][REMOTE] == '490900'
    assert REMOTE in SIMPLE_QUERY and REMOTE in VARIABLE_FIELDS


def test_existing_inventory_only_review_includes_hs_without_other_mutations(world):
    product, identity, adapter = created(world)
    change(world, **{HS:'490900'})
    plan, token = drafts.preview(product, adapter, scope='inventory')
    assert plan['classification'] == 'update'
    field = next(f for f in plan['fields'] if f['field'] == REMOTE)
    assert field['changed'] and not field['deferred']
    adapter.calls.clear(); adapter.inputs.clear()
    drafts.confirm(token, True, adapter)
    assert drafts.evidence(identity)['inventory_update']['state'] == 'verified'
    assert adapter.inputs == [{'id':plan['variant_id'],'inventoryItem':{REMOTE:'490900'}}]
    assert not {'product_create','product_update','stage_write','alt_update'} & set(adapter.calls)


@pytest.mark.parametrize('problem', ['missing', 'invalid'])
def test_missing_or_invalid_readback_is_not_equality(world, problem):
    product, _, adapter = created(world)
    change(world, **{HS: '490900'})
    inventory = adapter.remote['simpleVariants']['nodes'][0]['inventoryItem']
    if problem == 'missing': inventory.pop(REMOTE)
    else: inventory[REMOTE] = 490900
    adapter.calls.clear()
    with pytest.raises(ShopifyError, match='evidence is (incomplete|invalid)'):
        drafts.preview(product, adapter)
    assert not {'variant_write', 'product_update', 'product_create'} & set(adapter.calls)


def test_variable_code_applies_to_all_existing_inventory_items(variable_world):
    product, shared, *_ = variable_world
    identity, adapter = create_variable(variable_world)
    old = copy.deepcopy(adapter.remote['variants']['nodes'])
    doc = json.loads(shared.read_text()); doc[HS] = '001234'; shared.write_text(json.dumps(doc))
    plan, token = drafts.preview(product, adapter)
    changed = [r for r in plan['fields'] if r['changed']]
    assert len(changed) == len(old) and all(r['field'].endswith('.'+REMOTE) for r in changed)
    adapter.calls.clear(); adapter.inputs.clear()
    drafts.confirm(token, True, adapter)
    assert identity.state == 'verified'
    assert all(v['inventoryItem'][REMOTE] == '001234' for v in adapter.remote['variants']['nodes'])
    assert [(v['id'], v['sku'], v['inventoryItem']['id']) for v in adapter.remote['variants']['nodes']] == [
        (v['id'], v['sku'], v['inventoryItem']['id']) for v in old]
    writes = [p for batch in adapter.inputs if isinstance(batch, list) for p in batch]
    assert len(writes) == len(old) and all(set(p) == {'id', 'inventoryItem'} and p['inventoryItem'] == {REMOTE:'001234'} for p in writes)
    assert not {'product_create', 'variants_create', 'binary_upload', 'product_update'} & set(adapter.calls)
    assert drafts.preview(product, adapter)[0]['classification'] == 'no_change'


def test_partial_variable_customs_write_cannot_claim_success_or_replay(variable_world):
    product, shared, *_ = variable_world
    identity, adapter = create_variable(variable_world)
    doc = json.loads(shared.read_text()); doc[HS] = '490900'; shared.write_text(json.dumps(doc))
    _, token = drafts.preview(product, adapter)
    attempts = []
    def partial(config, product_id, variants):
        attempts.append(copy.deepcopy(variants))
        adapter._apply(adapter.remote['variants']['nodes'][0], variants[0])
        raise ShopifyError('Partial HS Code batch outcome uncertain')
    adapter.update_variants = partial
    result = drafts.confirm(token, True, adapter)
    assert result.state != 'verified' and len(attempts) == 1
    values = [v['inventoryItem'][REMOTE] for v in adapter.remote['variants']['nodes']]
    assert values.count('490900') == 1 and values.count(None) == len(values)-1
    original_operation = identity.operation_id
    db.session.remove()
    identity = db.session.get(ShopifyProductIdentity, identity.id)
    assert identity.operation_id == original_operation and identity.state != 'verified'
    with pytest.raises(ShopifyError): drafts.confirm(token, True, adapter)
    assert len(attempts) == 1


def test_acknowledged_hs_survives_delayed_readback_and_worker_restart(variable_world, monkeypatch):
    from datetime import timedelta
    from app.models import ScheduledTask
    from app.manual_publishing import KEY
    from test_shopify_variables import rescan
    from test_variable_verification_completion import DelayedVariants, approve_recovery
    from test_manual_publishing_worker import step
    rescan(variable_world, **{HS:'001234'})
    adapter=DelayedVariants();adapter.delay=4
    product,row,operation_id,_=approve_recovery(variable_world,adapter,monkeypatch,large=False)
    step(adapter)
    assert row.state=='processing'
    identity_id=row.id;product_id=product.id;remote=copy.deepcopy(adapter.__dict__)
    task=db.session.get(ScheduledTask,KEY)
    task.owner='terminated-hs-worker';task.lease_until=drafts.now()-timedelta(seconds=1);task.resume_at=None
    db.session.commit();db.session.remove();db.engine.dispose()
    replacement=DelayedVariants();replacement.__dict__.update(remote)
    for _ in range(8):
        step(replacement,owner='replacement-hs-worker')
        row=db.session.get(ShopifyProductIdentity,identity_id)
        if row.state=='verified':break
    assert row.state=='verified' and row.operation_id==operation_id
    assert all(v['inventoryItem'][REMOTE]=='001234' for v in replacement.remote['variants']['nodes'])
    assert replacement.calls.count('product_create')==replacement.calls.count('variants_create')==1
    assert replacement.calls.count('variants_update')==1
    assert db.session.get(CatalogueOperation,operation_id).status=='succeeded'
    assert db.session.get(Product,product_id).sku==row.sku


@pytest.mark.parametrize('outcome', ['rejected', 'ambiguous'])
def test_mutation_error_retains_evidence_and_prevents_replay(world, outcome):
    product, identity, adapter = created(world)
    change(world, **{HS:'490900'})
    _, token = drafts.preview(product, adapter)
    attempts = []
    def failing(config, product_id, value):
        attempts.append(copy.deepcopy(value))
        if outcome == 'ambiguous': raise ShopifyError('HS Code transport outcome uncertain')
        return {'productVariantsBulkUpdate': {'productVariants': [], 'userErrors':[
            {'field':['variants','0','inventoryItem',REMOTE], 'message':'HS Code rejected by Shopify'}]}}
    adapter.update_simple_variant = failing
    result = drafts.confirm(token, True, adapter)
    assert result.state != 'verified' and len(attempts) == 1
    retained = drafts.evidence(identity)
    assert retained.get('update_attempted')
    assert ('Shopify rejected fields' if outcome == 'rejected' else 'HS Code transport') in retained.get('error', '')
    with pytest.raises(ShopifyError): drafts.confirm(token, True, adapter)
    assert len(attempts) == 1


def test_automatic_hs_update_then_unchanged_skips_and_draft_is_paused(world, monkeypatch):
    monkeypatch.setattr('app.utils.discord.publishing_message', lambda data: (True, 'mocked'))
    product = world[0]
    change(world, live=True)
    adapter = AcceptanceAdapter(); authorise(adapter)
    assert execute(product, adapter) == 'verified'
    remote = ShopifyProductIdentity.query.one().shopify_product_id
    change(world, **{HS:'490900'})
    adapter.calls.clear(); adapter.inputs.clear()
    assert execute(product, adapter) == 'verified'
    assert adapter.remote['simpleVariants']['nodes'][0]['inventoryItem'][REMOTE] == '490900'
    assert ShopifyProductIdentity.query.one().shopify_product_id == remote
    assert not {'product_create','product_update','stage_write','alt_update'} & set(adapter.calls)
    adapter.calls.clear()
    assert execute(product, adapter) == 'verified' and not adapter.calls
    change(world, live=False, **{HS:'001234'})
    assert execute(product, adapter) == 'paused'
    assert adapter.remote['simpleVariants']['nodes'][0]['inventoryItem'][REMOTE] == '490900'


@pytest.mark.parametrize('owner,remove', [('override',False), ('shared',False), ('override',True)])
@pytest.mark.parametrize('name', ['Simple Collection/Blue Token', 'Variable Collection/Badge One'])
def test_hs_save_scoped_scan_and_normal_status_refresh_before_hourly_publish(core, monkeypatch, owner, name, remove):
    """The actual save/scan/read-worker boundary, not a manufactured queue entry."""
    from app import scheduled_tasks as tasks, catalogue_views as views
    from app.models import ShopifyCollectionIdentity, ScheduledTaskRun
    from app.utils import scan_runner
    from test_edit_publish_lifecycle import LifecycleAdapter, cycle
    from test_shopify_foundation import TOKEN
    app, client, root, _, ids, *_ = core
    product_id = ids[name]; collection = name.split('/')[0]
    if remove:
        # Seed a valid inherited value plus distinct authored child value. The
        # action under test clears only that override through the normal editor.
        for path,value in [(root/collection/'product_info.json','490900'),(root/name/'product_info.json','001234')]:
            doc=json.loads(path.read_text());doc[HS]=value;path.write_text(json.dumps(doc))
    expected = '490900' if remove else '001234'
    monkeypatch.setenv('SHOPIFY_STORE_DOMAIN', 'fictional.myshopify.com')
    monkeypatch.setenv('SHOPIFY_ADMIN_ACCESS_TOKEN', TOKEN)
    monkeypatch.setattr('app.routes.start_scan', scan_runner.start_scan)
    monkeypatch.setattr('requests.sessions.Session.request', lambda *a, **k: pytest.fail('External request'))
    monkeypatch.setattr('app.utils.discord.publishing_message', lambda value: (True, 'mocked'))
    scoped = []
    original = scan_runner.scan_collection
    def collection_only(folder, *args, **kwargs):
        scoped.append(str(folder))
        assert str(folder) == str(root/collection)
        return original(folder, *args, **kwargs)
    monkeypatch.setattr(scan_runner, 'scan_collection', collection_only)
    variable = collection == 'Variable Collection'
    adapter = VariableAcceptanceAdapter() if variable else LifecycleAdapter()
    def edit(**kwargs):
        calls = list(adapter.calls)
        response = save(core, name, **kwargs)
        assert response.status_code == 200, response.json
        deadline = time.monotonic()+15
        while time.monotonic() < deadline:
            with app.app_context(): progress = scan_runner.get_progress(response.json['run_id'])
            if progress['status'] in {'done','error'}: break
            time.sleep(.03)
        assert progress['status'] == 'done', progress
        with app.app_context():
            db.session.expire_all()
            op = db.session.get(CatalogueOperation, response.json['operation_id'])
            assert op.status == 'succeeded'
            Product.query.filter(Product.id != product_id).update({'catalogue_status':'missing'})
            ShopifyPublishJob.query.filter(ShopifyPublishJob.product_id != product_id).delete()
            db.session.commit()
        assert adapter.calls == calls, 'Save/scoped scan must not contact Shopify'
        return response.json['operation_id']
    edit(kind='shared', data={'live':True, 'categories':[]})
    with app.app_context():
        db.session.add(ShopifyCollectionIdentity(store_id='gid://shopify/Shop/42',
            source_key='collection:'+collection, remote_id='gid://shopify/Collection/800',
            state='verified', evidence_json='{"version":1}'))
        db.session.commit(); authorise(adapter); cycle(adapter)
        identity = ShopifyProductIdentity.query.one(); retained = identity.shopify_product_id
        assert identity.state == 'verified'
        previous = db.session.get(ShopifyPublishJob, product_id).requested_revision
    original_shared = (root/collection/'product_info.json').read_bytes()
    op = edit(kind=owner, **({'removals':[[HS]]} if remove else {'data':{HS:expected}}))
    assert client.get(f'/edit_products/{product_id}/edit/{owner}').status_code == 200
    target = root/name/'product_info.json' if owner == 'override' else root/collection/'product_info.json'
    if remove: assert HS not in json.loads(target.read_text())
    else: assert json.loads(target.read_text())[HS] == expected
    if owner == 'override': assert (root/collection/'product_info.json').read_bytes() == original_shared
    with app.app_context():
        product = db.session.get(Product, product_id)
        job = db.session.get(ShopifyPublishJob, product_id)
        assert job.scan_operation_id == op and job.requested_revision != previous
        assert job.requested_revision == publisher.local_revision(product)
        assert product_document(product)[HS] == expected
        # The scan already queued the existing read-only comparison. No manual
        # Discovery, Preview, full scan or publishing cycle is used to detect HS.
        comparison_run = db.session.get(tasks.ScheduledTask, tasks.KEY).active_run_id
        assert comparison_run
        adapter.calls.clear(); adapter.inputs.clear()
        for _ in range(12):
            if db.session.get(ScheduledTaskRun, comparison_run, populate_existing=True).state == 'completed': break
            claimed = tasks.claim('hs-status-worker')
            assert claimed and claimed[0] in {tasks.KEY, views.KEY}, claimed
            tasks.run_slice(*claimed, 'hs-status-worker', adapter=adapter)
        else: pytest.fail('Existing status refresh did not complete')
        # Simulate the existing projection task after the comparison has finished,
        # rather than treating an earlier view rebuild as its final result.
        view_run = tasks.enqueue(views.KEY, manual=True)
        claimed = tasks.claim('hs-status-worker')
        assert claimed == (views.KEY, view_run), claimed
        tasks.run_slice(*claimed, 'hs-status-worker', adapter=adapter)
        assert not adapter.inputs and not any('write' in c or 'update' in c or 'create' in c for c in adapter.calls)
        with current_app.test_request_context():
            state = views.saved_states([product])[product_id]
            assert state['state'] == 'update' and 'Update Available' in state['label'], state
            before = '001234' if remove else 'Not set'
            assert any(value.endswith('HS Code: '+before+' → '+expected) for value in state['change_summary']), state
        # Only now explicitly simulate the existing hourly publishing entry.
        cycle(adapter)
        nodes = adapter.remote['variants' if variable else 'simpleVariants']['nodes']
        assert all(v['inventoryItem'][REMOTE] == expected for v in nodes)
        assert ShopifyProductIdentity.query.one().shopify_product_id == retained
        db.session.remove()
    with app.app_context():
        product = db.session.get(Product, product_id)
        with current_app.test_request_context(): assert views.saved_states([product])[product_id]['state'] == 'no_change'
        adapter.calls.clear(); cycle(adapter)
        assert not adapter.calls
    assert len(scoped) == 2
