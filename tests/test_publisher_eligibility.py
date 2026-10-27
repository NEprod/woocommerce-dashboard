"""Queue/scan/readback agreement; fictional Shopify and disposable sources only."""
import json
import pytest

from app import db, automatic_publishing as publisher, shopify_drafts as drafts
from app.models import ShopifyPublishJob, ShopifyProductIdentity, CatalogueOperation
from app.shopify_comparison import fingerprint
from test_automatic_publishing import authorise, queued, notices
from test_shopify_drafts import world
from test_phase3_woo_publish_preview import preview_app
from test_shopify_variables import variable_world, rescan
from test_shopify_simple_updates import change
from test_shopify_publication_sales import AcceptanceAdapter, VariableAcceptanceAdapter
from test_metadata_editor_core import core, save
from test_product_shipping_editor import rescan as collection_rescan


def run(job, adapter):
    return publisher.publish_one(job, owner='eligibility-worker', guard=lambda: None,
                                 fence=lambda: None, adapter=adapter)


def test_manual_verified_revision_settles_old_queue_without_remote_work(world, notices):
    product, *_ = world
    change(world, live=True)
    job = queued(product)
    adapter = AcceptanceAdapter(); authorise(adapter)
    _, token = drafts.preview(product, adapter)
    drafts.confirm(token, True, adapter)
    assert job.state == 'verified' and job.verified_revision == job.requested_revision
    before = CatalogueOperation.query.count()
    adapter.calls.clear(); notices.clear()
    assert run(job, adapter) in {'verified', 'matching'}
    assert not adapter.calls and not notices
    assert CatalogueOperation.query.count() == before


def test_variable_scanned_revision_survives_relationship_reload(variable_world):
    product, *_ = variable_world
    rescan(variable_world, live=True)
    job = db.session.get(ShopifyPublishJob, product.id)
    expected = job.requested_revision
    db.session.expire_all()
    assert publisher.local_revision(product) == expected
    from app.shopify_variables import local_plan
    plan = local_plan(product)
    assert fingerprint(product, plan['images']) == expected


def test_gallery_revision_is_independent_of_relationship_load_order(variable_world):
    product, *_ = variable_world
    expected = publisher.local_revision(product)
    product.variations = list(reversed(product.variations))
    assert publisher.local_revision(product) == expected


def test_unscanned_edit_after_verified_sync_still_blocks_without_write(world, notices):
    product, *_ = world
    change(world, live=True)
    adapter = AcceptanceAdapter(); authorise(adapter)
    job = queued(product)
    assert run(job, adapter) == 'verified'
    change(world, title='A newly scanned pending revision')
    job = queued(product)
    change(world, title='Not yet scanned')
    # Simulate retained pending work, not a new scan adopting the edit.
    job.state = 'pending'; db.session.commit()
    adapter.calls.clear(); notices.clear()
    assert run(job, adapter) == 'attention'
    assert not adapter.calls
    assert 'completed scan' in job.error
    receipt = db.session.get(CatalogueOperation, job.operation_id)
    assert json.loads(receipt.scope)['write_attempted'] is False


@pytest.mark.parametrize('old_scan_hash', [False, True])
def test_old_pending_queue_settles_from_saved_equality_without_source_walk(world, notices, monkeypatch, old_scan_hash):
    from app.models import ShopifyProductComparison
    product, *_ = world
    change(world, live=True)
    adapter = AcceptanceAdapter(); authorise(adapter)
    job = queued(product)
    _, token = drafts.preview(product, adapter); row = drafts.confirm(token, True, adapter)
    # Existing database from before queue settlement was supported.
    job.state, job.verified_revision = 'pending', None
    if old_scan_hash:
        job.requested_revision = 'old-scan-hash'
    from app.catalogue_views import capture
    capture(product); db.session.commit()
    comparison = ShopifyProductComparison.query.one()
    original = comparison.summary_json, comparison.compared_at
    db.session.commit(); db.session.expire_all()  # process/restart reload
    before = CatalogueOperation.query.count()
    adapter.calls.clear(); notices.clear(); metrics = {}
    def forbidden(*a, **kw):
        raise AssertionError('Matching persisted status must not inspect source folders')
    monkeypatch.setattr(publisher, 'local_revision', forbidden)
    assert publisher.publish_one(job, owner='restart', guard=lambda: None, fence=lambda: None,
                                 adapter=adapter, metrics=metrics) == 'matching'
    assert job.state == 'verified'
    assert job.verified_revision is None if old_scan_hash else job.requested_revision == job.verified_revision
    assert (comparison.summary_json, comparison.compared_at) == original
    assert CatalogueOperation.query.count() == before
    assert not adapter.calls and not notices
    assert metrics['selection']['outcome'] == 'no_changes'
    assert metrics['selection']['write_attempted'] is False
    from app.catalogue_views import saved_states
    assert saved_states([product], url_builder=lambda *a, **kw: None)[product.id]['state'] == 'no_change'


@pytest.mark.parametrize('edit', ['shared_json', 'override_json', 'image'])
def test_genuine_unscanned_changes_block_even_with_matching_saved_status(world, edit):
    from PIL import Image
    product, folder, shared = world
    change(world, live=True)
    adapter = AcceptanceAdapter(); authorise(adapter); job = queued(product)
    assert run(job, adapter) == 'verified'
    # The saved update is eligible; a subsequent unscanned change must fail at
    # the write boundary rather than being adopted by the scheduler.
    change(world, title='Scanned pending change'); job = queued(product)
    if edit == 'shared_json':
        change(world, description='Collection changed without scan')
    elif edit == 'override_json':
        (folder / 'product_info.json').write_text(json.dumps({'description': 'Local override changed'}))
    else:
        Image.new('RGB', (7, 7), 'blue').save(folder / 'original.png')
    old = job.requested_revision
    job.state = 'pending'; db.session.commit(); adapter.calls.clear()
    metrics = {}
    assert publisher.publish_one(job, owner='freshness', guard=lambda: None, fence=lambda: None,
                                 adapter=adapter, metrics=metrics) == 'attention'
    assert not adapter.calls and job.requested_revision == old
    receipt = db.session.get(CatalogueOperation, job.operation_id)
    selection = json.loads(receipt.scope)['selection']
    assert selection['current_revision'] != selection['requested_revision']
    assert selection['outcome'] == 'scan_required' and selection['write_attempted'] is False


def test_targeted_variable_scan_then_manual_verification_settles_queue(variable_world):
    product, *_ = variable_world
    rescan(variable_world, live=True)
    job = db.session.get(ShopifyPublishJob, product.id)
    adapter = VariableAcceptanceAdapter(); authorise(adapter)
    _, token = drafts.preview(product, adapter); drafts.confirm(token, True, adapter)
    assert job.state == 'verified' and job.requested_revision == job.verified_revision
    adapter.calls.clear()
    assert run(job, adapter) in {'matching', 'verified'}
    assert not adapter.calls
    rescan(variable_world, description='Targeted scanned change')
    assert job.state == 'pending' and publisher.local_revision(product) == job.requested_revision
    assert run(job, adapter) == 'verified'
    assert adapter.calls.count('product_create') == 0


def test_reconciliation_drift_reopens_but_matching_read_does_not_duplicate_work(world):
    product, *_ = world
    change(world, live=True)
    adapter = AcceptanceAdapter(); authorise(adapter); job = queued(product)
    assert run(job, adapter) == 'verified'
    adapter.remote['title'] = 'Remote drift'
    plan, _ = drafts.preview(product, adapter)
    assert plan['classification'] == 'update' and job.state == 'pending'
    assert job.verified_revision is None
    adapter.remote['title'] = drafts.local_plan(product)['managed']['product']['title']
    plan, _ = drafts.preview(product, adapter)
    assert plan['classification'] == 'no_change' and job.state == 'verified'


def test_equality_never_settles_an_owned_or_recovery_job(world):
    product, *_ = world
    change(world, live=True)
    adapter = AcceptanceAdapter(); authorise(adapter); job = queued(product)
    assert run(job, adapter) == 'verified'
    job.owner, job.state = 'another-worker', 'processing'; db.session.commit()
    from app.shopify_capacity import BackgroundYield
    with pytest.raises(BackgroundYield, match='owns'):
        run(job, adapter)
    drafts.preview(product, adapter)
    assert job.owner == 'another-worker' and job.state == 'processing'
    job.owner, job.state = None, 'recovery_required'; db.session.commit()
    drafts.preview(product, adapter)
    assert job.state == 'recovery_required'


def test_scheduler_skips_settled_product_without_image_inspection(world, monkeypatch, notices):
    from app import scheduled_tasks as tasks
    from app.models import ScheduledTaskRun
    product, *_ = world
    change(world, live=True)
    adapter = AcceptanceAdapter(); authorise(adapter); job = queued(product)
    assert run(job, adapter) == 'verified'
    def forbidden(*a, **kw):
        raise AssertionError('Idle scheduler must not inspect product sources')
    monkeypatch.setattr(publisher, 'local_revision', forbidden)
    adapter.calls.clear(); notices.clear()
    run_id = tasks.enqueue(publisher.KEY)
    assert tasks.claim('idle-worker') == (publisher.KEY, run_id)
    tasks.run_slice(publisher.KEY, run_id, 'idle-worker', adapter=adapter)
    result = db.session.get(ScheduledTaskRun, run_id)
    assert result.state == 'completed'
    assert not adapter.calls and not notices


@pytest.mark.parametrize('source', ['Simple Collection/Blue Token', 'Variable Collection/Badge One', 'Single Variable'])
def test_real_targeted_scans_persist_stable_revision_and_local_edit(core, source, monkeypatch):
    monkeypatch.setenv('SHOPIFY_STORE_DOMAIN', 'fictional.myshopify.com')
    monkeypatch.setenv('SHOPIFY_ADMIN_ACCESS_TOKEN', 'fictional_eligibility_token_00000000')
    from app.models import Product
    app, _, root, _, ids, *_ = core
    shared = root / source.split('/')[0] / 'product_info.json'
    before = shared.read_bytes()
    result = save(core, source, kind='shared' if source=='Single Variable' else 'override',
                  data={'description': 'A scoped scan has indexed this edit', 'live': False, 'shopify_status': 'DRAFT'})
    assert result.status_code == 200
    collection_rescan(core, source)
    with app.app_context():
        product = db.session.get(Product, ids[source]); job = db.session.get(ShopifyPublishJob, product.id)
        expected = job.requested_revision
        db.session.expire_all()
        assert publisher.local_revision(product) == expected, job.error
        assert job.state in {'pending', 'paused'}
    if source != 'Single Variable':
        assert shared.read_bytes() == before


def test_noop_cas_cannot_consume_a_newer_scan_revision(world):
    from sqlalchemy import update
    product, *_ = world
    job = queued(product); old = job.requested_revision
    db.session.execute(update(ShopifyPublishJob).where(ShopifyPublishJob.product_id==product.id).values(
        requested_revision='newer-scanned-revision', state='pending'), execution_options={'synchronize_session':False})
    assert publisher.settle_matching_revision(job, old) is False
    db.session.commit(); db.session.refresh(job)
    assert job.requested_revision == 'newer-scanned-revision' and job.state == 'pending'


@pytest.mark.parametrize('state,expected', [('attention','attention'), ('unavailable','attention'),
    ('recovery_required','recovery_required'), ('processing','deferred')])
def test_saved_blocked_status_never_opens_source_or_writes(world, monkeypatch, state, expected):
    product, *_ = world
    change(world, live=True)
    adapter = AcceptanceAdapter(); authorise(adapter); job = queued(product)
    assert run(job, adapter) == 'verified'
    view = json.loads(job.view_json); view['state'].update(state=state, message='Existing persisted finding')
    job.view_json, job.state, job.verified_revision = json.dumps(view), 'pending', None
    db.session.commit(); adapter.calls.clear()
    monkeypatch.setattr(publisher, 'local_revision', lambda *a: pytest.fail('Blocked product walked source'))
    assert run(job, adapter) == expected
    assert not adapter.calls


@pytest.mark.parametrize('workers', [2, 4])
def test_publisher_single_task_owner_under_worker_contention(world, workers):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from flask import current_app
    from app import scheduled_tasks as tasks
    product, *_ = world
    change(world, live=True); queued(product)
    authorise(AcceptanceAdapter()); tasks.enqueue(publisher.KEY)
    app = current_app._get_current_object(); barrier = Barrier(workers)
    def claim(i):
        with app.app_context():
            barrier.wait()
            try: return tasks.claim('eligibility-worker-'+str(i))
            finally: db.session.remove()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(claim, range(workers)))
    assert sum(r is not None for r in results) == 1


def test_documented_probe_reads_only_specific_preflight_receipt(world):
    from pathlib import Path
    import subprocess, sys
    product, *_ = world
    change(world, live=True); job = queued(product)
    change(world, title='Unscanned source change')
    adapter = AcceptanceAdapter(); authorise(adapter)
    assert run(job, adapter) == 'attention' and not adapter.remote
    path = db.engine.url.database
    source = (Path(__file__).parents[1] / 'docs/PUBLISHER_ELIGIBILITY.md').read_text()
    probe = source.split("python - <<'PY'\n",1)[1].split('\nPY\n',1)[0]
    probe = probe.replace("Path('/app/instance/site.db')", 'Path('+repr(path)+')')
    result = json.loads(subprocess.check_output([sys.executable, '-c', probe], text=True))
    assert result['database_writes'] == result['shopify_requests'] == 0
    assert result['operations'][0]['id'] == job.operation_id
    assert result['operations'][0]['write_attempted'] is False
