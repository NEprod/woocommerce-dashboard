"""Sanitised live sequence: four variable parents, one linked, old source error.

Actual signed save/scanner and read-task entry points; no real network or writes.
"""
import json
import shutil
import threading

import pytest
from flask import current_app

from app import db, catalogue_views as views, scheduled_tasks as tasks, shopify_drafts as drafts
from app.models import (Product, ShopifyCollectionIdentity, ShopifyProductComparison,
                        ShopifyProductIdentity, ShopifyPublishJob, ScheduledTask, ScheduledTaskRun,
                        CatalogueOperation)
from app.utils import scan_runner
from app.automatic_publishing import local_revision, policy
from test_metadata_editor_core import core, save
from test_edit_publish_lifecycle import LifecycleAdapter
from test_storefront_customisations import ConfigurationMixin
from test_shopify_publication_sales import VariableAcceptanceAdapter
from test_shopify_foundation import TOKEN


class AdocAdapter(ConfigurationMixin, VariableAcceptanceAdapter):
    durable = LifecycleAdapter.durable

    def find_draft(self, config, handle):
        if self.remote and handle == self.remote.get('handle'):
            return super().find_draft(config, handle)
        return {'nodes': [], 'pageInfo': {'hasNextPage': False}}


def test_valid_field_save_replaces_old_display_without_waiting_for_view_worker(core, monkeypatch):
    app, client, root, *_ = core
    monkeypatch.setenv('SHOPIFY_STORE_DOMAIN', 'fictional.myshopify.com')
    monkeypatch.setenv('SHOPIFY_ADMIN_ACCESS_TOKEN', TOKEN)
    monkeypatch.setenv('TLC_SCHEDULER_READY_AT', '99999999999')
    monkeypatch.setattr('app.routes.start_scan', scan_runner.start_scan)
    monkeypatch.setattr('requests.sessions.Session.request', lambda *a, **k: pytest.fail('Real network'))
    monkeypatch.setattr('app.utils.discord.publishing_message', lambda *a, **k: (True, 'mocked'))
    monkeypatch.setattr('app.utils.discord.actionable_message', lambda *a, **k: (True, 'mocked'))
    collection = root/'Variable Collection'
    shared = collection/'product_info.json'
    original = json.loads(shared.read_text())
    shared.write_text(json.dumps({**original, 'live': True, 'categories': []}))
    for name in ('Badge Two', 'Badge Three', 'Badge Four'):
        (collection/name).mkdir()
        for filename in ('product_info.json', 'badge.png'):
            shutil.copy2(collection/'Badge One'/filename, collection/name/filename)
    with app.app_context():
        # The real case already had four indexed parents before the edited save.
        # Establish that state with the existing append path, not by introducing
        # new folders during a forced metadata update.
        from app.utils.scanner import scan_collection
        from app.utils.ingest import ingest_rows_to_db
        rows = scan_collection(collection, 'https://invalid.example/', core[3], log=lambda *a, **k: None)
        assert ingest_rows_to_db(rows, log=lambda *a, **k: None)['products_failed'] == 0
        linked = Product.query.filter_by(source_relpath='Variable Collection/Badge One').one()
        db.session.add(ShopifyCollectionIdentity(store_id='gid://shopify/Shop/42',
            source_key='collection:Variable Collection', remote_id='gid://shopify/Collection/800',
            state='verified', evidence_json='{}'))
        db.session.commit()
        adapter = AdocAdapter()
        _, token = drafts.preview(linked, adapter)
        identity = drafts.confirm(token, True, adapter)
        assert identity.state == 'verified'
        retained_identity = identity.shopify_product_id
    adapter.calls.clear()
    projected = threading.Event()
    original_queue = tasks.queue_projected
    def queue_projected(operation_id):
        result = original_queue(operation_id)
        projected.set()
        return result
    monkeypatch.setattr(tasks, 'queue_projected', queue_projected)

    def edit(customisation):
        projected.clear()
        result = save(core, 'Variable Collection/Badge One', kind='shared',
                      data={'customisation': customisation})
        assert result.status_code == 200, result.json
        assert projected.wait(20), 'Containing scan did not complete and queue its normal read-only check'
        assert not adapter.calls, 'Save/scanner must not contact Shopify'
        return result.json['operation_id']

    edit({'storefront_enabled': True, 'proof_policy': 'none'})
    with app.app_context():
        assert not policy().enabled
        assert tasks.tick('old-source-reader', adapter=adapter)[0] == tasks.KEY
        products = Product.query.filter(Product.source_relpath.startswith('Variable Collection/')).all()
        assert len(products) == 4 and all(len(p.variations) == 4 for p in products), [(p.sku,p.source_relpath,len(p.variations)) for p in products]
        for product in products:
            assert db.session.get(ShopifyPublishJob, product.id).state == 'attention'
        historical = CatalogueOperation.query.filter_by(operation_type='shopify_status_refresh').order_by(
            CatalogueOperation.started_at.desc()).first()
        historical_id = historical.id
        historical_receipt = historical.scope, historical.status, historical.finished_at
        # Retain the older release's exact generic failure shape, including an
        # uncomputable failure fingerprint. This is fixture history, not repair.
        old_error = 'Local catalogue evidence is unavailable for this product; review its source. No Shopify write occurred.'
        for row in ShopifyProductComparison.query.filter(
                ShopifyProductComparison.source_identity.startswith('Variable Collection/')):
            data = json.loads(row.summary_json)
            data.update(refresh_failure={'kind': 'unavailable', 'at': drafts.now().isoformat(),
                        'message': old_error}, failure_local=None)
            row.summary_json = json.dumps(data)
        db.session.commit()
    adapter.calls.clear()
    child_json = {path: path.read_bytes() for path in collection.glob('*/product_info.json')}
    scan = edit({'storefront_enabled': True, 'proof_policy': 'none', 'fields': {
        'dogs_name': {'type': 'text', 'label': 'Dogs Name', 'required': True,
                      'min_length': 2, 'max_length': 16, 'order': 0,
                      'help_text': 'Check spelling.', 'placeholder': 'Enter dogs name here'}}})
    parent_bytes = shared.read_bytes()
    with app.app_context():
        products = Product.query.filter(Product.source_relpath.startswith('Variable Collection/')).all()
        for product in products:
            job = db.session.get(ShopifyPublishJob, product.id)
            assert job.scan_operation_id == scan and job.error is None
            assert job.requested_revision == local_revision(product) and job.state == 'pending'
            state = json.loads(job.view_json)['state']
            assert state['label'] == 'Status refresh pending'
            assert state['state'] == 'unavailable'  # no premature publishing permission
            assert 'Local scan completed' in state['message']
        read_run = db.session.get(ScheduledTask, tasks.KEY).active_run_id
        assert read_run
        # A new worker/application session resumes the persisted manual read task
        # during startup grace. Do not run or tick the separate saved-view task.
        db.session.remove()
    with app.app_context():
        assert tasks.tick('new-worker-after-restart', adapter=adapter) == (tasks.KEY, read_run)
        run = db.session.get(ScheduledTaskRun, read_run)
        assert run.state == 'completed', (run.error,run.data_json)
        products = Product.query.filter(Product.source_relpath.startswith('Variable Collection/')).all()
        with current_app.test_request_context():
            states = views.saved_states(products)
        for product in products:
            expected = 'update' if product.source_relpath.endswith('/Badge One') else 'create'
            assert states[product.id]['state'] == expected, states[product.id]
            job = db.session.get(ShopifyPublishJob, product.id)
            assert not job.view_pending and job.scan_operation_id == scan
            assert job.state == 'pending' and job.verified_revision is None
        assert ShopifyProductIdentity.query.one().shopify_product_id == retained_identity
        historical = db.session.get(CatalogueOperation, historical_id)
        assert (historical.scope, historical.status, historical.finished_at) == historical_receipt
        assert not policy().enabled
    assert shared.read_bytes() == parent_bytes
    assert all(path.read_bytes() == content for path, content in child_json.items())
    assert not any('write' in call or 'create' in call or 'update' in call for call in adapter.calls)
