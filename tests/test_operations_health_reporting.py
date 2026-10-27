"""Reporting changes only: fictional catalogue/Shopify, no external writes."""
import json
from datetime import datetime, timedelta

from app import db
from app.models import CatalogueOperation, Product, ShopifyProductComparison
from test_phase2_dashboard import dashboard_app, dashboard_client
from test_phase3_woo_publish_preview import preview_app
from test_shopify_drafts import world
from test_shopify_status_reconciliation import RevisionAdapter
from test_operation_control import operation_app


def test_local_health_does_not_count_historical_operations(dashboard_app):
    from app.dashboard import build_dashboard_data
    app, _ = dashboard_app
    with app.test_request_context('/'):
        from app.models import Variation
        Product.query.update({Product.catalogue_status: 'active'})
        Variation.query.update({Variation.catalogue_status: 'active'})
        db.session.commit()
        data = build_dashboard_data()
        assert data['health']['availability_percent'] == 100
        assert data['health']['label'] == 'Healthy'


def test_refresh_readiness_is_a_finding_not_execution_failure(world, monkeypatch):
    from app import shopify_drafts, shopify_status
    from app.shopify_connection import ShopifyReadinessFinding
    adapter = RevisionAdapter()
    def blocked(*args, **kwargs):
        raise ShopifyReadinessFinding('Required original artwork is missing.', code='missing_images')
    monkeypatch.setattr(shopify_drafts, 'preview', blocked)
    result = shopify_status.refresh([world[0]], force=True, adapter=adapter)
    assert result['errors'] == []
    assert result['findings'][0]['code'] == 'missing_images'
    row = CatalogueOperation.query.filter_by(operation_type='shopify_status_refresh').one()
    assert row.status == 'succeeded'
    assert json.loads(row.scope)['operation_summary']['execution_complete'] is True
    assert json.loads(ShopifyProductComparison.query.one().summary_json)['refresh_failure']['kind'] == 'readiness'


def test_warning_does_not_imply_partial_execution():
    from app.operation_reporting import interpretation
    row = CatalogueOperation(id='warning', operation_type='append', status='succeeded',
        scope=json.dumps({'operation_summary': {'warnings': 1}}), recovery_state='none',
        products_failed=0, error=None)
    view = interpretation(row)
    assert view['execution'] == 'succeeded'
    assert view['label'] == 'Completed with findings'
    row.status = 'partial'
    # No proof of completeness: never rewrite an ambiguous old receipt.
    assert interpretation(row)['execution'] == 'partial'
    row.products_attempted = row.products_succeeded = 1
    row.marker_state = 'finalized'
    assert interpretation(row)['execution'] == 'partial'  # warning contents still unknown
    row.scope = json.dumps({'operation_summary': {'warnings': 1, 'warning_entries': ['Optional parent image absent']}})
    assert interpretation(row)['execution'] == 'succeeded'
    row.scope = json.dumps({'operation_summary': {'warnings': 1, 'warning_entries': ['No SKU matched for attributes']}})
    assert interpretation(row)['execution'] == 'partial'


def test_identical_read_failures_resolve_only_after_same_scope_success(dashboard_app):
    from app.operation_reporting import operation_issues
    app, _ = dashboard_app
    with app.test_request_context('/'):
        stamp = datetime.now()
        rows = [CatalogueOperation(id=f'probe-{i}', operation_type='shopify_status_refresh',
            status='failed', scope='{"products":[1]}', started_at=stamp+timedelta(seconds=i),
            error='Required page failed.', recovery_state='none') for i in range(3)]
        assert len(operation_issues(rows)['failures']) == 1
        success = CatalogueOperation(id='probe-ok', operation_type='shopify_status_refresh', status='succeeded',
            scope='{"products":[1],"operation_summary":{"execution_complete":true}}',
            started_at=stamp+timedelta(seconds=4), recovery_state='none')
        assert not operation_issues(rows+[success])['failures']
        rows[0].operation_type = 'shopify_draft_publish'
        assert len(operation_issues(rows+[success])['failures']) == 1
        rows[0].recovery_state = 'remote_uncertain'
        assert len(operation_issues(rows+[success])['recovery']) == 1


def test_production_shaped_uncertain_partials_are_review_not_proven_failures(dashboard_app):
    from app.operation_reporting import operation_issues
    app, _ = dashboard_app
    with app.test_request_context('/'):
        types = ['append', 'intake_image_rename', 'intake_metadata_save', 'intake_catalogue_handoff',
            'intake_image_rename', 'intake_metadata_save', 'intake_catalogue_handoff', 'woo_connection_test',
            'woo_publish_preview', 'shopify_taxonomy_batch', 'shopify_draft_publish', 'shopify_draft_publish']
        stamp = datetime.now()
        rows = [CatalogueOperation(id=f'legacy-{i}', operation_type=kind, status='partial',
            scope=json.dumps({'product_id': i+1}), started_at=stamp+timedelta(seconds=i),
            products_failed=0, recovery_state='none') for i, kind in enumerate(types)]
        for i in range(2):
            rows.append(CatalogueOperation(id=f'error-{i}', operation_type='shopify_status_refresh', status='partial',
                scope=json.dumps({'products': [i+1]}), started_at=stamp+timedelta(seconds=20+i),
                error='Required evidence read failed.', recovery_state='none'))
        for i in range(8):
            rows.append(CatalogueOperation(id=f'recovery-{i}', operation_type='shopify_draft_publish', status='partial',
                scope='{}', started_at=stamp+timedelta(seconds=30+i), recovery_state='review_required'))
        result = operation_issues(rows)
        assert len(result['failures']) == 2
        assert len(result['review']) == 12
        assert len(result['recovery']) == 8
        assert all(r.status == 'partial' for r in rows)  # no history rewriting
        assert all(i['category'] == 'history_review' and i['receipt'] for i in result['review'])
        for summary in ({'failed_stage': 'persistence'}, {'pending': [1]}, {'execution_complete': False}):
            rows[0].scope = json.dumps({'product_id': 1, 'operation_summary': summary})
            assert len(operation_issues(rows)['failures']) == 3
            assert len(operation_issues(rows)['review']) == 11


def test_counter_and_drilldown_use_same_projection(dashboard_client, dashboard_app):
    from app.operation_reporting import issue_projection
    app, _ = dashboard_app
    with app.test_request_context('/'):
        projection = issue_projection()
    for category, count in projection['counts'].items():
        response = dashboard_client.get('/operations/issues', query_string={'category': category})
        assert response.status_code == 200
        assert f'data-issue-total="{count}"'.encode() in response.data
    response = dashboard_client.get('/')
    assert b'/operations/issues?category=metadata' in response.data


def test_partial_does_not_manufacture_a_warning():
    from app.intake_warnings import warning_presentation
    assert warning_presentation({}, status='partial')['count'] == 0


def test_current_metadata_resolves_and_reopens_without_deleting_history(dashboard_app):
    from app.operation_reporting import issue_projection
    app, _ = dashboard_app
    with app.test_request_context('/'):
        product = Product.query.filter_by(sku='CARD-002').one()
        key = f'product:{product.id}:description'
        stamp = datetime.now()
        for index, present in enumerate((True, False, True)):
            db.session.add(CatalogueOperation(id=f'validation-{index}', operation_type='product_update',
                status='succeeded', started_at=stamp+timedelta(seconds=index), finished_at=stamp+timedelta(seconds=index),
                scope=json.dumps({'reporting_observations': {key: present}}), recovery_state='none'))
            product.description = '' if present else 'Corrected description'
            db.session.commit()
            issues = [i for i in issue_projection()['items'] if i['id'] == key]
            assert bool(issues) is present
        assert issues[0]['resolution'] == 'Reopened'
        assert CatalogueOperation.query.filter(CatalogueOperation.id.like('validation-%')).count() == 3


def test_metadata_unit_and_pagination_are_exact(dashboard_app, dashboard_client):
    from app.operation_reporting import issue_projection
    app, _ = dashboard_app
    with app.test_request_context('/'):
        for index in range(27):
            db.session.add(Product(sku=f'GAP-{index}', title=f'Metadata gap {index}', catalogue_status='active',
                product_type='simple', source_relpath=f'Gap/{index}'))
        db.session.commit()
        data = issue_projection()
        assert data['counts']['metadata'] == 28
        # Optional SEO is separately informational, not a second required gap.
        assert data['counts']['information'] == 28
    first = dashboard_client.get('/operations/issues?category=metadata')
    second = dashboard_client.get('/operations/issues?category=metadata&page=2')
    assert b'data-issue-total="28"' in first.data and b'data-issue-total="28"' in second.data
    assert first.data.count(b'class="operation-history-card"') == 25
    assert second.data.count(b'class="operation-history-card"') == 3
    assert b'Next' in first.data and b'Previous' in second.data


def test_every_nonzero_counter_has_correct_destination_and_explanation(dashboard_client, dashboard_app):
    from app.operation_reporting import issue_projection, CATEGORIES
    import re
    app, _ = dashboard_app
    page = dashboard_client.get('/').get_data(as_text=True)
    links = re.findall(r'class="attention-counter-link" href="([^"]+)"', page)
    with app.test_request_context('/'):
        data = issue_projection()
    assert len(links) == sum(data['counts'][k] > 0 for k in CATEGORIES if k != 'information')
    for link in links:
        response = dashboard_client.get(link)
        assert response.status_code == 200
        assert b'Last detected' in response.data and b'Resolution' in response.data
        issues = re.findall(r'<a class="btn btn-accent text-wrap" href="([^"]+)">', response.get_data(as_text=True))
        for destination in issues:
            assert destination.startswith(('/products/', '/shopify/', '/operations/', '/settings/tasks/'))
    assert dashboard_client.get('/operations/issues?category=bad').status_code == 400
    assert dashboard_client.get('/operations/issues?page=bad').status_code == 400
    assert app.test_client().get('/operations/issues').status_code in {302, 401}


def test_scanner_optional_and_unexpected_findings_are_distinct():
    from app.utils.scan_runner import scan_finding_severity
    for message in ('Optional parent image absent', 'Missing optional artwork',
                    "Using 'Red' for parent images", 'Skipped unchanged folder'):
        assert scan_finding_severity(message) == 'Information'
    for message in ('No SKU matched for attributes', 'No variations generated — check attributes'):
        assert scan_finding_severity(message) == 'Error'
    assert scan_finding_severity('Asset copy interrupted unexpectedly') == 'Warning'


def test_intake_severity_does_not_relabel_readiness_as_warning():
    from app.intake_warnings import completed_reporting, warning_presentation
    summary = completed_reporting({'warnings': 3, 'warning_findings': [
        {'code': 'meta_title', 'message': 'Optional SEO'},
        {'code': 'image_fallback_parent', 'message': 'Fallback available'},
        {'code': 'live', 'message': 'Confirm intended publication'}]})
    assert summary['execution_complete'] and summary['information'] == 2 and summary['action_required'] == 1
    assert warning_presentation(summary)['count'] == 0


def test_first_time_candidate_is_successful_discovery_but_incomplete_page_is_not(world, monkeypatch):
    from app.shopify_drafts import require_absent_sku
    from app.shopify_connection import ShopifyReadinessFinding, ShopifyError
    class Adapter:
        def find_sku(self, *args):
            return {'nodes': [{'id': 'gid://shopify/Product/123'}], 'pageInfo': {'hasNextPage': False}}
    adapter = Adapter()
    import pytest
    with pytest.raises(ShopifyReadinessFinding) as result:
        require_absent_sku(adapter, None, 'FICTION-1')
    assert result.value.code == 'reconciliation'
    monkeypatch.setattr(adapter, 'find_sku', lambda *a: {'nodes': [], 'pageInfo': {'hasNextPage': True}})
    with pytest.raises(ShopifyError) as result:
        require_absent_sku(adapter, None, 'FICTION-1')
    assert not isinstance(result.value, ShopifyReadinessFinding)


def test_internal_slices_are_not_duplicate_primary_dashboard_results(dashboard_app):
    from app.dashboard import build_dashboard_data
    from app.models import ScheduledTaskRun
    app, _ = dashboard_app
    with app.test_request_context('/'):
        stamp = datetime.now()
        run = ScheduledTaskRun(id='logical-run', task_key='shopify_products', trigger='manual',
            state='completed', created_at=stamp, started_at=stamp, completed_at=stamp,
            data_json='{}')
        db.session.add(run)
        for index in range(3):
            db.session.add(CatalogueOperation(id=f'slice-{index}', operation_type='shopify_status_refresh',
                status='partial', started_at=stamp, scope='{"scheduled_run":"logical-run"}', recovery_state='none'))
        db.session.commit()
        data = build_dashboard_data()
        assert sum(i['id'] == 'logical-run' for i in data['recent_operations']) == 1
        assert not any(i['id'].startswith('slice-') for i in data['recent_operations'])
        assert CatalogueOperation.query.filter(CatalogueOperation.id.like('slice-%')).count() == 3


def test_real_read_failure_retains_failure_and_no_successful_checkpoint(world):
    from app import shopify_status
    from app.shopify_connection import ShopifyError
    class Denied(RevisionAdapter):
        def test_connection(self, *args):
            raise ShopifyError('Access denied for required products query.')
    result = shopify_status.refresh([world[0]], force=True, adapter=Denied())
    assert result['errors']
    row = CatalogueOperation.query.filter_by(operation_type='shopify_status_refresh').one()
    assert row.status == 'failed'
    assert json.loads(row.scope)['operation_summary']['execution_complete'] is False


def test_read_only_probe_preserves_database_and_omits_error_bodies(dashboard_app, monkeypatch, capsys):
    import hashlib
    from app.operations_probe import main
    from config import Config
    _app, database = dashboard_app
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    monkeypatch.setattr(Config, 'SQLALCHEMY_DATABASE_URI', 'sqlite:///'+str(database))
    main()
    output = json.loads(capsys.readouterr().out)
    assert output['database_writes'] == output['shopify_requests'] == 0
    assert output['historical_raw_incomplete_rows'] == 1
    assert len(output['retained_recovery_cases']) == 1
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert 'Sanitized fictional failure' not in json.dumps(output)


def test_image_issue_resolution_uses_revalidated_source(dashboard_app):
    from PIL import Image
    from app.operation_reporting import issue_projection
    from app.models import Settings
    app, _ = dashboard_app
    with app.test_request_context('/'):
        product = Product.query.filter_by(sku='CARD-002').one()
        key = f'product:{product.id}:images'
        assert any(i['id'] == key for i in issue_projection()['items'])
        from pathlib import Path
        image = Path(Settings.query.first().product_folder) / product.source_relpath / 'repaired.png'
        Image.new('RGB', (4,4), 'blue').save(image)
        from catalogue_projection_fixtures import project_existing_sources
        project_existing_sources()  # emulate the completed source validation, never a GET walk
    with app.test_request_context('/'):
        assert not any(i['id'] == key for i in issue_projection()['items'])


def test_actual_scan_with_optional_warning_completes(operation_app, tmp_path, monkeypatch):
    import time
    from app.models import Settings
    from app.utils.scan_runner import start_scan, get_progress, ScanScopePlan
    catalogue, output = tmp_path/'catalogue', tmp_path/'output'
    catalogue.mkdir(); output.mkdir()
    with operation_app.app_context():
        db.session.add(Settings(product_folder=str(catalogue), output_folder=str(output), url_prefix='https://invalid.example/'))
        db.session.commit()
    monkeypatch.setattr('app.utils.scan_runner.build_scan_scope', lambda *a, **k:
        ScanScopePlan((str(catalogue),), frozenset(), {}, False, True))
    def scan(*args, **kwargs):
        kwargs['log']('Missing optional parent image', level='WARN')
        return []
    monkeypatch.setattr('app.utils.scan_runner.scan_collection', scan)
    monkeypatch.setattr('app.utils.scan_runner.ingest_rows_to_db', lambda *a, **k: {'products_failed': 0})
    start_scan(operation_app, 'optional-reporting-scan', scan_mode='append')
    deadline = time.monotonic()+5
    while get_progress('optional-reporting-scan')['status'] == 'running' and time.monotonic()<deadline:
        time.sleep(.01)
    with operation_app.app_context():
        row = CatalogueOperation.query.one()
        assert row.status == 'succeeded'
        summary = json.loads(row.scope)['operation_summary']
        assert summary['information'] == 1 and summary['warnings'] == 0
        assert summary['findings'][0]['severity'] == 'Information'


def test_persistence_exception_never_leaves_successful_execution(world, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    from app import shopify_status, shopify_drafts
    from app.operations_workspace import operation_view
    import pytest
    monkeypatch.setattr(shopify_drafts, 'preview', lambda *a, **k: (_ for _ in ()).throw(SQLAlchemyError('synthetic storage error')))
    with pytest.raises(SQLAlchemyError):
        shopify_status.refresh([world[0]], force=True, adapter=RevisionAdapter())
    row = CatalogueOperation.query.filter_by(operation_type='shopify_status_refresh').one()
    assert row.status == 'failed'
    from flask import current_app
    with current_app.test_request_context('/'):
        assert operation_view(row)['error_count'] == 1


def test_old_task_attention_only_partial_is_derived_not_rewritten():
    from app.operation_reporting import task_interpretation
    from app.models import ScheduledTaskRun
    run = ScheduledTaskRun(task_key='shopify_products', state='partially_completed', error=None,
        data_json=json.dumps({'queue': [], 'counts': {'considered': 15, 'checked': 10, 'skipped_recent': 5,
            'attention': 3, 'deferred': 0, 'unavailable': 0}, 'errors': []}))
    assert task_interpretation(run)['label'] == 'Completed with findings'
    assert run.state == 'partially_completed'
    run.error = 'Required page failed'
    assert task_interpretation(run)['execution'] == 'partially_completed'
