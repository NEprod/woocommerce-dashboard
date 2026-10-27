"""Rebuildable views cannot become a source or remote write authority."""
import json
from unittest.mock import patch

import pytest
from flask import current_app

from app import db, catalogue_views as views, shopify_drafts as drafts
from app.models import Product, ShopifyPublishJob, ShopifyProductIdentity
from app.shopify_products import states_for
from test_automatic_publishing import queued, authorise, execute
from test_shopify_drafts import world
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_publication_sales import AcceptanceAdapter
from test_shopify_simple_updates import change


@pytest.fixture(autouse=True)
def offline_notices(monkeypatch):
    monkeypatch.setattr('app.utils.discord.publishing_message', lambda data: (False, 'disabled'))


@pytest.mark.parametrize('path', ['/', '/api/edit_products', '/operations/issues?category=shopify'])
def test_fast_get_reads_no_product_sources_images_or_shopify_and_makes_no_writes(world, path):
    product, *_ = world
    queued(product); views.capture(product); db.session.commit()
    before = ShopifyPublishJob.query.one().view_json
    client = _client(current_app)
    with (patch('app.taxonomy_assignments.product_document', side_effect=AssertionError('source reread')),
         patch('app.catalogue_images.resolve_product_catalogue_image', side_effect=AssertionError('image walk')),
         patch('requests.Session.request', side_effect=AssertionError('remote read'))):
        result = client.get(path)
    assert result.status_code == 200
    db.session.refresh(db.session.get(ShopifyPublishJob, product.id))
    assert ShopifyPublishJob.query.one().view_json == before


def test_saved_view_displays_live_pending_and_paused_draft_without_reauthoring(world):
    product, _, source = world
    change(world, live=True)
    job = queued(product); views.capture(product); db.session.commit()
    original = source.read_bytes()
    with current_app.test_request_context('/'):
        current_app.preprocess_request()
        @views.saved_read_view
        def state(): return states_for([product])[product.id]
        assert state()['state'] == 'create'
    assert source.read_bytes() == original
    change(world, live=False)
    job = queued(product); views.capture(product); db.session.commit()
    with current_app.test_request_context('/'):
        @views.saved_read_view
        def state(): return states_for([product])[product.id]
        assert state()['state'] == 'draft'


def test_current_uncertain_package_evidence_cannot_borrow_cached_green_status(world):
    product, *_ = world
    change(world, live=True)
    adapter = AcceptanceAdapter(); authorise(adapter)
    assert execute(product, adapter) == 'verified'
    row = ShopifyProductIdentity.query.one()
    data = drafts.evidence(row); data['package_assignments'] = {'parent': {'state': 'uncertain'}}
    row.media_evidence_json = json.dumps(data); db.session.commit()
    with current_app.test_request_context('/'):
        @views.saved_read_view
        def state(): return states_for([product])[product.id]
        assert state()['state'] == 'recovery_required'


def test_context_change_invalidates_cached_equality_without_scanning_catalogue(world):
    product, *_ = world
    queued(product); views.capture(product); db.session.commit()
    job = ShopifyPublishJob.query.one(); assert not job.view_pending
    with patch.object(views, 'context_digest', return_value='changed-registry-context'):
        views.refresh_context()
    db.session.refresh(job)
    assert job.view_pending
    assert job.verified_revision is None


def test_unknown_upgrade_health_is_explicit_not_a_fabricated_missing_image(world):
    product, *_ = world
    assert not ShopifyPublishJob.query.count()
    with current_app.test_request_context('/'):
        @views.saved_read_view
        def state(): return states_for([product])[product.id]
        assert state()['state'] == 'unknown'
        assert views.pending_count() >= 1


def test_operations_probe_can_read_saved_image_views_with_query_only_database(world):
    from pathlib import Path
    from app.operations_probe import dashboard_snapshot
    product, *_ = world
    queued(product); views.capture(product); db.session.commit()
    result = dashboard_snapshot(Path(db.engine.url.database))
    assert result['local_catalogue']['availability_percent'] == 100
    assert 'active_issue_counts' in result


def test_historical_read_incident_does_not_reintroduce_source_walk_on_fast_get(world):
    from app.models import CatalogueOperation
    from datetime import timedelta
    product,*_=world
    change(world,live=True)
    adapter=AcceptanceAdapter();authorise(adapter)
    assert execute(product,adapter)=='verified'
    old=drafts.now()-timedelta(days=1)
    db.session.add(CatalogueOperation(id='old-read-incident',operation_type='shopify_status_refresh',status='failed',
        scope=json.dumps({'products':[product.id]}),started_at=old,finished_at=old,error='Required read failed.',recovery_state='none'))
    db.session.commit()
    client=_client(current_app)
    with patch('app.shopify_comparison.fingerprint',side_effect=AssertionError('GET must not rehash source')):
        assert client.get('/operations/issues?category=failures').status_code==200
        assert client.get('/').status_code==200
