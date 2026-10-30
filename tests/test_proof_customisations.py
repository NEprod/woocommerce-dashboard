"""Existing configuration, publisher and captured order paths; never live writes."""
import copy
import itertools
import json

import pytest
from flask import current_app

from app import db, automatic_publishing as publisher, catalogue_views as views
from app import scheduled_tasks as tasks, shopify_drafts as drafts
from app.customisation_review import compare
from app.models import Product, CatalogueOperation, ShopifyCollectionIdentity, ShopifyPublishJob, ShopifyProductIdentity
from app.order_personalisation import parse
from app.storefront_customisations import public_configuration
from app.taxonomy_assignments import product_document
from app.utils import scan_runner
from test_metadata_editor_core import core, save
from test_edit_publish_lifecycle import LifecycleAdapter, cycle
from test_automatic_publishing import authorise
from test_shopify_foundation import TOKEN
from test_shopify_drafts import world
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_variables import variable_world
from test_storefront_customisations import CustomerVariableAdapter, PUBLIC
from test_shopify_simple_updates import change
from test_shopify_orders import orders_app
from test_setup_flow import setup_app

FIELDS = {'name': {'type': 'text', 'label': 'Name', 'required': True}}
MODES = ('none', 'optional', 'required')


@pytest.mark.parametrize('before,after', itertools.permutations(MODES, 2))
def test_every_policy_transition_changes_only_existing_public_document(before, after):
    def public(mode):
        return public_configuration({'storefront_enabled': True, 'proof_policy': mode, 'fields': FIELDS})
    old, new = public(before), public(after)
    assert old['revision'] != new['revision'] and old['fields'] == new['fields']
    assert public(after) == new
    detail = compare({'before': {'value': {'value': json.dumps(old)}}, 'desired': new})
    assert detail['available'] and detail['proof_change']
    assert not detail['changes'] and not detail['message']


def test_old_none_and_disabled_configurations_do_not_receive_false_updates():
    absent = public_configuration({'storefront_enabled': True, 'fields': FIELDS})
    explicit = public_configuration({'storefront_enabled': True, 'fields': FIELDS, 'proof_policy': 'none'})
    assert absent == explicit and 'proof_policy' not in explicit
    assert public_configuration({'proof_policy': 'required'}) == public_configuration({})
    assert public_configuration({'storefront_enabled': True, 'proof_policy': 'required'})['fields'] == []
    with pytest.raises(ValueError, match='no supported enabled fields'):
        public_configuration({'storefront_enabled': True, 'proof_policy': 'none'})


def test_proof_change_on_new_local_draft_never_enters_create(world):
    change(world, live=False, customisation={**PUBLIC, 'proof_policy': 'optional'})
    adapter = LifecycleAdapter(); authorise(adapter); adapter.calls.clear()
    publisher.queue_product(world[0]); db.session.commit()
    cycle(adapter)
    assert not adapter.calls and not ShopifyProductIdentity.query.count()


def test_manual_variable_proof_update_preserves_all_existing_variant_and_media_identities(variable_world):
    product, source, *_ = variable_world
    document = json.loads(source.read_text()); document['customisation'] = PUBLIC
    source.write_text(json.dumps(document))
    adapter = CustomerVariableAdapter()
    _, token = drafts.preview(product, adapter); row = drafts.confirm(token, True, adapter)
    ids = row.shopify_product_id, copy.deepcopy(drafts.evidence(row)['variant_maps'])
    media = copy.deepcopy(adapter.remote['media'])
    document['customisation'] = {**PUBLIC, 'proof_policy': 'optional'}
    source.write_text(json.dumps(document)); adapter.calls.clear()
    plan, token = drafts.preview(product, adapter, scope='customisations')
    assert token and plan['classification'] == 'update'
    row = drafts.confirm(token, True, adapter)
    assert row.state == 'verified'
    assert (row.shopify_product_id, drafts.evidence(row)['variant_maps']) == ids
    assert adapter.remote['media'] == media
    assert adapter.calls.count('configuration_value_write') == 1
    assert not any(c in adapter.calls for c in ('product_create', 'binary_upload', 'variants_create', 'variants_update'))
    assert drafts.preview(product, adapter, scope='customisations')[0]['classification'] == 'no_change'


def test_signed_shared_save_scan_hourly_update_all_modes_same_identity_and_restart(core, monkeypatch):
    import threading
    app, client, root, *_ = core
    monkeypatch.setenv('SHOPIFY_STORE_DOMAIN', 'fictional.myshopify.com')
    monkeypatch.setenv('SHOPIFY_ADMIN_ACCESS_TOKEN', TOKEN)
    monkeypatch.setattr('app.routes.start_scan', scan_runner.start_scan)
    monkeypatch.setattr('requests.sessions.Session.request', lambda *a, **k: pytest.fail('External network'))
    monkeypatch.setattr('app.utils.discord.publishing_message', lambda *a, **k: (True, 'mocked'))
    monkeypatch.setattr('app.utils.discord.actionable_message', lambda *a, **k: (True, 'mocked'))
    scanned, done = [], threading.Event()
    original_scan, original_queue = scan_runner.scan_collection, tasks.queue_projected
    def scoped(folder, *args, **kwargs):
        assert str(folder) == str(root/'Simple Collection')
        scanned.append(str(folder))
        return original_scan(folder, *args, **kwargs)
    def queued(operation_id):
        result = original_queue(operation_id); done.set(); return result
    monkeypatch.setattr(scan_runner, 'scan_collection', scoped)
    monkeypatch.setattr(tasks, 'queue_projected', queued)
    adapter = LifecycleAdapter()
    source = root/'Simple Collection/product_info.json'
    child = root/'Simple Collection/Blue Token/product_info.json'
    child_before = child.read_bytes()
    def edit(mode):
        done.clear(); calls = list(adapter.calls)
        response = save(core, 'Simple Collection/Blue Token', kind='shared', data={
            'live': True, 'categories': [], 'customisation': {
                'storefront_enabled': True, 'fields': FIELDS, 'proof_policy': mode}})
        assert response.status_code == 200, response.json
        assert done.wait(20)
        assert adapter.calls == calls, 'Save/scoped scan cannot read or write Shopify'
        assert json.loads(source.read_text())['customisation']['proof_policy'] == mode
        with app.app_context():
            op = db.session.get(CatalogueOperation, response.json['operation_id'])
            assert op.status == 'succeeded' and op.products_failed == 0
        return response.json['operation_id']
    edit('none')
    with app.app_context():
        product = Product.query.filter_by(source_relpath='Simple Collection/Blue Token').one()
        product_id = product.id
        Product.query.filter(Product.id != product.id).update({'catalogue_status': 'missing'})
        ShopifyPublishJob.query.filter(ShopifyPublishJob.product_id != product.id).delete()
        db.session.add(ShopifyCollectionIdentity(store_id='gid://shopify/Shop/42',
            source_key='collection:Simple Collection', remote_id='gid://shopify/Collection/800',
            state='verified', evidence_json='{}'))
        db.session.commit(); authorise(adapter); cycle(adapter)
        identity = ShopifyProductIdentity.query.one(); remote_id = identity.shopify_product_id
        assert identity.state == 'verified'
    # Each directed transition occurs, including both reversals through None.
    for mode in ('optional', 'required', 'optional', 'none', 'required', 'none'):
        scan_id = edit(mode)
        with app.app_context():
            # This single-product adapter is not a second Shopify store. The
            # real containing scan still resolves siblings; isolate publication.
            Product.query.filter(Product.id != product_id).update({'catalogue_status': 'missing'})
            ShopifyPublishJob.query.filter(ShopifyPublishJob.product_id != product_id).delete()
            db.session.commit()
            product = db.session.get(Product, product_id)
            assert product_document(product)['customisation']['proof_policy'] == mode
            job = db.session.get(ShopifyPublishJob, product.id)
            assert job.scan_operation_id == scan_id and job.requested_revision == publisher.local_revision(product)
            read = tasks.tick('proof-source-reader', adapter=adapter)
            assert read and read[0] == tasks.KEY
            with current_app.test_request_context():
                assert views.saved_states([product])[product.id]['state'] == 'update'
            adapter.calls.clear(); run = cycle(adapter)
            assert json.loads(run.data_json)['publication_counts']['updated_verified'] == 1, run.data_json
            assert adapter.calls.count('configuration_value_write') == 1
            assert not any(c in adapter.calls for c in ('product_create', 'binary_upload', 'variants_create'))
            assert json.loads(adapter.customer_value['value']).get('proof_policy', 'none') == mode
            assert ShopifyProductIdentity.query.one().shopify_product_id == remote_id
            with current_app.test_request_context():
                assert views.saved_states([product])[product.id]['state'] == 'no_change'
            db.session.remove(); db.engine.dispose()
        with app.app_context():
            product = db.session.get(Product, product_id)
            assert product_document(product)['customisation']['proof_policy'] == mode
            adapter.calls.clear(); cycle(adapter); assert not adapter.calls
    assert len(scanned) == 7 and child.read_bytes() == child_before


@pytest.mark.parametrize('choice,policy,display', [
    ('yes','optional','Yes, send me a proof for approval.'),
    ('no','optional','No, proceed without a proof.'), ('required','required','Proof Required')])
def test_purchased_proof_survives_original_order_properties_without_becoming_a_customer_field(choice, policy, display):
    from test_shopify_orders import properties, PRODUCT
    original = properties('Ellie', 6)
    snapshot = json.loads(original[-1]['value'])
    snapshot['proof'] = {'policy': policy, 'choice': choice, 'property_key': 'Digital proof', 'display': display}
    original[-1]['value'] = json.dumps(snapshot)
    original.append({'key': 'Digital proof', 'value': display})
    before = copy.deepcopy(original)
    result = parse(original, PRODUCT)
    assert result['proof'] == snapshot['proof'] and not result['warnings']
    assert [f['display'] for f in result['fields']] == ['Ellie', '6']
    assert original == before
    original[-1]['value'] = 'Contradictory preference'
    assert parse(original, PRODUCT)['proof'] is None and parse(original, PRODUCT)['warnings']


@pytest.mark.parametrize('value', [None, [], {'policy': []}, {'policy':'required','choice': []}])
def test_malformed_proof_never_crashes_or_discards_original_purchase_evidence(value):
    original = [{'key':'_tlc_configuration','value':'a'*64}, {'key':'_tlc_personalisation',
        'value':json.dumps({'schema_version':1,'revision':'a'*64,'fields':[],'proof':value})}]
    assert parse(original)['proof'] is None and parse(original)['warnings']


def test_orders_import_and_restart_keep_independent_captured_proof_preferences(orders_app):
    from app.models import ShopifyOrderLine
    from app.orders_workspace import line_view
    from test_shopify_orders import order, save as save_order, client
    app, *_ = orders_app
    raw = order()
    for line, choice, display in zip(raw['lineItems']['nodes'], ('yes', 'no'),
            ('Yes, send me a proof for approval.', 'No, proceed without a proof.')):
        pairs = line['customAttributes']
        snapshot = json.loads(next(p['value'] for p in pairs if p['key'] == '_tlc_personalisation'))
        snapshot['proof'] = {'policy': 'optional', 'choice': choice, 'display': display, 'property_key': 'Digital proof'}
        next(p for p in pairs if p['key'] == '_tlc_personalisation')['value'] = json.dumps(snapshot)
        pairs.append({'key': 'Digital proof', 'value': display})
    before = copy.deepcopy(raw)
    save_order(raw)
    rows = ShopifyOrderLine.query.order_by(ShopifyOrderLine.id).all()
    ids = [row.id for row in rows]
    assert [line_view(row)['personalisation']['proof']['choice'] for row in rows] == ['yes', 'no']
    assert all(not line_view(row)['personalisation']['warnings'] for row in rows)
    html = client(app).get('/orders/1').get_data(as_text=True)
    assert 'Digital proof:</strong> Yes, send me a proof for approval.' in html
    assert 'Digital proof:</strong> No, proceed without a proof.' in html
    db.session.remove(); db.engine.dispose()
    rows = ShopifyOrderLine.query.order_by(ShopifyOrderLine.id).all()
    assert [row.id for row in rows] == ids
    assert [line_view(row)['personalisation']['proof']['choice'] for row in rows] == ['yes', 'no']
    assert raw == before
