"""Due-only sale/release work using current pricing and collection scan machinery."""
import json
import time
from datetime import timedelta
from unittest.mock import patch

import pytest

from app import db, publishing_due as due, automatic_publishing as publisher
from app.models import Product, ShopifyDueEvent, ShopifyPublishJob, CatalogueOperation, User
from test_phase3_woo_publish_preview import preview_app
from test_shopify_drafts import world
from test_shopify_publication_sales import AcceptanceAdapter, clock
from test_shopify_simple_updates import change
from test_automatic_publishing import authorise, queued, execute, notices
from test_metadata_editor_core import core, save
from test_product_shipping_editor import rescan


def test_sale_start_and_revert_use_indexed_priority_work_and_existing_prices(world, clock):
    product, *_ = world
    change(world, live=True, sale_price='2.50', sale_start_date='2026-10-20T11:00:00Z',
           sale_end_date='2026-10-20T12:00:00Z')
    adapter = AcceptanceAdapter(); authorise(adapter)
    assert execute(product, adapter) == 'verified'
    assert adapter.remote['simpleVariants']['nodes'][0]['price'] == '3.50'
    assert ShopifyDueEvent.query.count() == 2
    assert due.process_due(lambda: None) == 0
    clock[0] += timedelta(hours=1)
    assert due.process_due(lambda: None) == 1
    job = db.session.get(ShopifyPublishJob, product.id)
    assert job.priority == 2 and job.trigger == 'sale_activate'
    assert publisher.publish_one(job, owner='sale-start', guard=lambda: None, fence=lambda: None, adapter=adapter) == 'verified'
    assert adapter.remote['simpleVariants']['nodes'][0]['price'] == '2.50'
    clock[0] += timedelta(hours=1)
    assert due.process_due(lambda: None) == 1
    assert publisher.publish_one(job, owner='sale-end', guard=lambda: None, fence=lambda: None, adapter=adapter) == 'verified'
    assert adapter.remote['simpleVariants']['nodes'][0]['price'] == '3.50'
    assert adapter.calls.count('product_create') == 1


def test_pause_retains_due_events_and_source_change_cannot_fire_stale_schedule(world, clock):
    product, *_ = world
    change(world, live=False, shopify_release_at='2026-10-20T11:00:00Z')
    adapter = AcceptanceAdapter(); authorise(adapter); queued(product)
    publisher.configure(enabled=False, actor_id=User.query.first().id)
    clock[0] += timedelta(hours=2)
    assert due.process_due(lambda: None) == 0
    assert ShopifyDueEvent.query.one().state == 'pending'
    authorise(adapter)
    change(world, shopify_release_at=None)  # unscanned cancellation: do not execute the old event
    assert due.process_due(lambda: None) == 0
    assert ShopifyDueEvent.query.one().state == 'attention'
    assert not adapter.remote
    queued(product)
    assert ShopifyDueEvent.query.one().state == 'cancelled'


def test_rescan_does_not_rearm_completed_sale_with_same_schedule(world, clock):
    product, *_ = world
    change(world, live=True, sale_price='2.50', sale_start_date='2026-10-20T11:00:00Z')
    adapter = AcceptanceAdapter(); authorise(adapter); queued(product)
    clock[0] += timedelta(hours=1)
    assert due.process_due(lambda: None) == 1
    change(world, title='An unrelated later title edit'); queued(product)
    assert ShopifyDueEvent.query.one().state == 'completed'
    assert due.process_due(lambda: None) == 0


@pytest.mark.parametrize('source,kind', [('Simple Collection/Blue Token','override'),
    ('Variable Collection/Badge One','override'), ('Single Variable','shared')])
def test_release_reuses_actual_collection_scan_and_preserves_shared_json(core, monkeypatch, clock, source, kind):
    app, client, root, output, ids, *_ = core
    product_id = ids[source]
    result = save(core, name=source, kind=kind, data={'live': False, 'shopify_status': 'DRAFT',
                             'shopify_release_at': '2026-10-20T11:00:00Z'})
    assert result.status_code == 200
    rescan(core, source)
    collection_folder = root / source.split('/')[0]
    parent = collection_folder / 'product_info.json'
    before = parent.read_bytes()
    target = root / source / 'product_info.json'
    original_document=json.loads(target.read_text())
    with app.app_context():
        row = publisher.policy()
        row.enabled, row.actor_id = True, User.query.first().id
        db.session.commit()
        event = ShopifyDueEvent.query.filter_by(product_id=product_id, event_type='release').one()
        assert event.state == 'pending'
        from app.utils import scan_runner
        original = scan_runner.scan_collection
        folders = []
        def scoped(folder, *args, **kwargs):
            folders.append(str(folder))
            assert str(folder) == str(collection_folder)
            return original(folder, *args, **kwargs)
        clock[0] += timedelta(hours=1)
        with patch.object(scan_runner, 'scan_collection', scoped):
            assert due.process_due(lambda: None) == 1
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                db.session.expire_all()
                operation = db.session.get(CatalogueOperation, event.operation_id)
                if operation.status not in {'running', 'pending'}: break
                time.sleep(.05)
            assert operation.status == 'succeeded', operation.error
        due.process_due(lambda: None)
        assert event.state == 'completed'
        job = db.session.get(ShopifyPublishJob, product_id)
        assert job.priority == 2 and job.trigger == 'release'
    if kind == 'override': assert parent.read_bytes() == before
    else:
        assert json.loads(target.read_text()) == {**original_document,'live':True,'shopify_status':'ACTIVE'}
    assert json.loads(target.read_text())['live'] is True
    assert json.loads(target.read_text())['shopify_status'] == 'ACTIVE'
    assert folders == [str(collection_folder)]


@pytest.mark.parametrize('value', ['2026-03-29T01:30', '2026-10-25T01:30', 'not a date'])
def test_release_rejects_ambiguous_or_invalid_uk_times(value):
    from app.product_info import validate_product_info
    result = validate_product_info({'shopify_release_at': value}, 'override')
    assert not result.valid
