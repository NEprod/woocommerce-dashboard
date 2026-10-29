"""Real signed editor saves, scoped scanner, scheduler and persisted comparisons.

Only Shopify and Discord are test doubles; catalogue and database are disposable.
"""
import json
import time

import pytest
from flask import current_app

from app import db, automatic_publishing as publisher, shopify_drafts as drafts, shopify_status
from app.models import Product, ShopifyCollectionIdentity, ShopifyProductIdentity, ShopifyPublishJob, CatalogueOperation
from app.taxonomy_assignments import product_document
from app.catalogue_views import saved_states
from app.utils import scan_runner
from test_metadata_editor_core import core, save
from test_storefront_customisations import ConfigurationMixin
from test_shopify_publication_sales import AcceptanceAdapter, SafetyMixin
from test_publishing_checkpoint_integration import cycle as scheduler_cycle
from test_automatic_publishing import authorise
from test_shopify_foundation import TOKEN


class LifecycleAdapter(ConfigurationMixin, AcceptanceAdapter):
    def durable(self, key=None):
        if key is None:
            return ConfigurationMixin.durable(self)
        return SafetyMixin.durable(self, key)


def cycle(adapter):
    try:
        return scheduler_cycle(adapter)
    except AssertionError:
        pytest.fail(repr([(job.state, job.error) for job in ShopifyPublishJob.query.all()]))


@pytest.mark.parametrize('activation_owner', ['override', 'shared'])
def test_edit_scan_create_update_unchanged_and_session_restart(core, monkeypatch, activation_owner):
    app, client, root, output, ids, *_ = core
    monkeypatch.setenv('SHOPIFY_STORE_DOMAIN', 'fictional.myshopify.com')
    monkeypatch.setenv('SHOPIFY_ADMIN_ACCESS_TOKEN', TOKEN)
    monkeypatch.setattr('app.routes.start_scan', scan_runner.start_scan)
    monkeypatch.setattr('app.utils.discord.publishing_message', lambda message: (True, 'mocked'))
    monkeypatch.setattr('app.utils.discord.actionable_message', lambda message: (True, 'mocked'))
    monkeypatch.setattr('requests.sessions.Session.request', lambda *a, **k: pytest.fail('External network'))
    name = 'Simple Collection/Blue Token'
    target = root/name/'product_info.json'
    shared = root/'Simple Collection/product_info.json'
    unrelated = {p: p.read_bytes() for p in (root/'Variable Collection').rglob('*') if p.is_file()}
    scanned = []
    adapter = None
    original_scan = scan_runner.scan_collection

    def scoped(folder, *args, **kwargs):
        scanned.append(str(folder))
        assert str(folder) == str(root/'Simple Collection')
        return original_scan(folder, *args, **kwargs)
    monkeypatch.setattr(scan_runner, 'scan_collection', scoped)

    def edit(**kwargs):
        before_calls = list(adapter.calls) if adapter else []
        result = save(core, name, **kwargs)
        assert result.status_code == 200, result.json
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with app.app_context():
                progress = scan_runner.get_progress(result.json['run_id'])
            if progress['status'] in {'done', 'error'}:
                break
            time.sleep(.03)
        assert progress['status'] == 'done', progress
        with app.app_context():
            op = db.session.get(CatalogueOperation, result.json['operation_id'])
            assert op.status == 'succeeded' and op.products_failed == 0
        assert (adapter.calls if adapter else []) == before_calls, 'Save/scan must never publish or read Shopify'
        return result

    # Start local Draft. Shared and local edits really execute their containing scan.
    edit(kind='shared', data={'live': False, 'categories': [], 'customisation': {'storefront_enabled': True,
        'fields': {'recipient_name': {'type': 'text', 'label': 'Recipient Name', 'required': True}}}})
    shared_before = shared.read_bytes()
    edit(data={'description': 'Product-specific copy', 'customisation': {
        'fields': {'dogs_name': {'type': 'text', 'label': "Dog's name", 'required': False}}}})
    assert shared.read_bytes() == shared_before
    local = json.loads(target.read_text())
    assert set(local['customisation']['fields']) == {'dogs_name'}
    assert client.get(f'/edit_products/{ids[name]}/edit/override').status_code == 200
    with app.app_context():
        product = db.session.get(Product, ids[name])
        # Keep this lifecycle bounded to one publishable product, not sibling fixtures.
        Product.query.filter(Product.id != product.id).update({'catalogue_status': 'missing'})
        db.session.add(ShopifyCollectionIdentity(store_id='gid://shopify/Shop/42',
            source_key='collection:Simple Collection', remote_id='gid://shopify/Collection/800',
            state='verified', evidence_json='{"version":1}'))
        db.session.commit()
        fields = product_document(product)['customisation']['fields']
        assert list(fields) == ['recipient_name', 'dogs_name']
        adapter = LifecycleAdapter()
        authorise(adapter)
        ShopifyPublishJob.query.filter(ShopifyPublishJob.product_id != product.id).delete()
        db.session.commit()
        adapter.calls.clear()
        cycle(adapter)
        assert not adapter.calls and not ShopifyProductIdentity.query.count()

    # Actual editor intent change, scoped ingestion and the normal hourly entry point.
    edit(kind=activation_owner, data={'live': True})
    if activation_owner == 'shared':
        shared_before = shared.read_bytes()
    with app.app_context():
        Product.query.filter(Product.id != ids[name]).update({'catalogue_status': 'missing'})
        ShopifyPublishJob.query.filter(ShopifyPublishJob.product_id != ids[name]).delete()
        db.session.commit()
        product = db.session.get(Product, ids[name])
        job = db.session.get(ShopifyPublishJob, product.id)
        assert job.scan_operation_id and job.requested_revision == publisher.local_revision(product)
        run = cycle(adapter)
        assert json.loads(run.data_json)['publication_counts']['created_verified'] == 1, run.data_json
        identity = ShopifyProductIdentity.query.one()
        retained_id = identity.shopify_product_id
        assert identity.state == 'verified' and adapter.remote['status'] == 'ACTIVE'
        assert [f['id'] for f in json.loads(adapter.customer_value['value'])['fields']] == ['recipient_name', 'dogs_name']
        assert adapter.calls.count('product_create') == 1

    adapter.calls.clear()
    edit(data={'description': 'Second product-specific copy'})
    with app.app_context():
        Product.query.filter(Product.id != ids[name]).update({'catalogue_status': 'missing'})
        ShopifyPublishJob.query.filter(ShopifyPublishJob.product_id != ids[name]).delete()
        db.session.commit()
        run = cycle(adapter)
        assert json.loads(run.data_json)['publication_counts']['updated_verified'] == 1, run.data_json
        assert 'product_create' not in adapter.calls and 'binary_upload' not in adapter.calls
        assert 'configuration_value_write' not in adapter.calls
        product = db.session.get(Product, ids[name])
        with current_app.test_request_context():
            assert saved_states([product])[product.id]['state'] == 'no_change'
        assert not shopify_status.refresh([product], force=True, adapter=adapter)['errors']
        # Read-only comparisons invalidate the saved view; its existing five-minute
        # worker refreshes it. Neither comparison nor projection publishes.
        from app import scheduled_tasks as tasks
        from app import catalogue_views as views
        before_calls = list(adapter.calls)
        view_run = tasks.enqueue(views.KEY, manual=True)
        for _ in range(8):
            claimed = tasks.claim('lifecycle-view')
            assert claimed and claimed[0] in {tasks.KEY, views.KEY}, claimed
            tasks.run_slice(*claimed, 'lifecycle-view', adapter=adapter)
            if claimed == (views.KEY, view_run):
                break
        else:
            pytest.fail('Saved projection worker did not run')
        assert not any('write' in call or 'update' in call or 'create' in call
                       for call in adapter.calls[len(before_calls):])
        # New database session cannot depend on in-memory verification or browser state.
        db.session.remove()
    with app.app_context():
        product = db.session.get(Product, ids[name])
        assert ShopifyProductIdentity.query.one().shopify_product_id == retained_id
        assert db.session.get(ShopifyPublishJob, product.id).state == 'verified'
        with current_app.test_request_context():
            state = saved_states([product])[product.id]
            assert state['state'] == 'no_change', state
        adapter.calls.clear()
        cycle(adapter)
        assert not adapter.calls
    assert len(scanned) == 4
    assert shared.read_bytes() == shared_before
    assert all(p.read_bytes() == content for p, content in unrelated.items())
