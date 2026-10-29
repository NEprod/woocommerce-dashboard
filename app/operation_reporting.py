"""Read-only reporting semantics and current issues from existing authoritative data.

No receipt rewriting, remote reads, new issue store or automatic mutation recovery.
"""
import hashlib
import json
from collections import Counter

from flask import url_for

from app.models import CatalogueOperation, Product, Variation, ScheduledTaskRun
from app.utils.redaction import redact_diagnostic
from app.catalogue_views import saved_read_view

CATEGORIES = {
    'metadata': ('Required metadata gaps', 'product/field-group findings'),
    'images': ('Missing required images', 'product findings'),
    'missing': ('Missing catalogue items', 'products and variations'),
    'failures': ('Unresolved execution incidents', 'distinct operation/scope incidents'),
    'history_review': ('Historical receipts needing evidence review', 'distinct operation/scope cases'),
    'recovery': ('Recovery Required', 'retained recovery cases'),
    'shopify': ('Shopify review and reconciliation', 'product findings'),
    'information': ('Catalogue information', 'optional product findings'),
}
READ_TYPES = {'shopify_status_refresh', 'append', 'product_update', 'shared_collection_update', 'full'}


def document(value):
    try:
        data = json.loads(value or '{}')
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def operation_summary(scope):
    value = scope.get('operation_summary')
    return value if isinstance(value, dict) else {}


def interpretation(row):
    scope = document(row.scope)
    summary = operation_summary(scope)
    recovery = row.recovery_state not in (None, 'none')
    execution = row.status
    known_processing = (row.operation_type == 'shopify_draft_publish' and scope.get('automatic_publishing')
        and execution == 'partial' and not row.error and not row.products_failed and summary.get('identity_id')
        and summary.get('state') in {None, 'processing'}
        and summary.get('stage') in {'media_processing','media_reordering'}
        and summary.get('native_dimensions') in {'disabled','blocked','verified'}
        and summary.get('package_assignment') in {'none','accepted','outstanding'})
    known_upload = (row.operation_type == 'shopify_draft_publish' and execution == 'partial'
        and not recovery and not row.error and not row.products_failed and summary.get('identity_id')
        and summary.get('stage') == 'gallery_uploading' and summary.get('create_attempted') is False
        and type(summary.get('uploads_acknowledged')) is int and type(summary.get('images')) is int
        and 0 <= summary['uploads_acknowledged'] <= summary['images'] <= 250)
    if known_upload:
        execution = 'processing'
    if known_processing:
        execution, recovery = 'processing', False
    # Only explicit new evidence permits a corrected historical interpretation.
    entries = summary.get('warning_entries') or []
    from app.utils.scan_runner import scan_finding_severity
    proven_scan = (row.operation_type in {'append', 'product_update', 'shared_collection_update', 'full'}
                   and row.marker_state == 'finalized' and (row.products_attempted or 0) > 0
                   and row.products_attempted == row.products_succeeded and summary.get('warnings')
                   and len(entries) == summary.get('warnings')
                   and all(scan_finding_severity(e) == 'Information' for e in entries))
    if execution == 'partial' and (summary.get('execution_complete') is True or proven_scan) and not (
            row.products_failed or row.error or summary.get('errors') or summary.get('failures')
            or summary.get('blocking_errors') or summary.get('failed_stage') or recovery):
        execution = 'succeeded'
    findings = bool(summary.get('findings') or summary.get('warnings') or summary.get('information'))
    label = {'succeeded': 'Completed with findings' if findings else 'Completed',
             'partial': 'Partially completed', 'failed': 'Failed', 'interrupted': 'Interrupted',
             'running': 'Running', 'pending': 'Queued', 'cancelled': 'Cancelled',
             'processing': 'Awaiting Shopify media readback'}.get(execution, execution)
    if recovery:
        label = 'Recovery Required'
    elif known_upload:
        label = 'Gallery uploads in progress — no ProductCreate attempted'
    elif execution == 'succeeded' and summary.get('package_assignment') == 'accepted':
        label = 'Completed — Shopify Accepted'
    return {'execution': execution, 'label': label, 'original_status': row.status,
            'corrected': execution != row.status, 'findings': findings, 'recovery': recovery}


def _scope_key(row):
    scope = document(row.scope)
    keys = ('product_id', 'products', 'products_fingerprint', 'source_identity', 'source_relpath', 'collection_relpath',
            'identity_id', 'sku', 'scan_mode', 'store_id')
    target = {key: scope[key] for key in keys if key in scope}
    if isinstance(target.get('products'), list):
        target['products'] = sorted(target['products'], key=str)
    raw = json.dumps([row.operation_type, target], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:24], bool(target)


def task_interpretation(run):
    data = document(run.data_json)
    counts = data.get('counts') or {}
    execution = run.state
    legacy_complete = (run.task_key == 'shopify_products' and execution == 'partially_completed'
        and not run.error and not data.get('errors') and data.get('queue') == []
        and not counts.get('deferred') and not counts.get('unavailable')
        and counts.get('considered', 0) > 0
        and counts.get('checked', 0)+counts.get('skipped_recent', 0) == counts['considered'])
    if legacy_complete:
        execution = 'completed'
    findings = counts.get('attention') or counts.get('updates') or counts.get('unavailable')
    label = 'Completed with findings' if execution == 'completed' and findings else execution.replace('_', ' ').title()
    return {'execution': execution, 'label': label, 'original_status': run.state, 'corrected': execution != run.state}


def _issue(key, category, entity, cause, severity, detected, action, destination, **extra):
    return dict({'id': key, 'category': category, 'entity': entity, 'cause': cause, 'severity': severity,
                'detected': detected, 'resolution': 'Awaiting merchant action', 'action': action,
                'destination': destination}, **extra)


def operation_issues(rows):
    """Conservative incident folding. A read success cannot resolve a write failure."""
    active, recovery, resolved = {}, [], []
    from app.shopify_package_assignments import superseded_operations
    superseded = superseded_operations(rows)
    for row in sorted(rows, key=lambda r: (r.started_at, r.id)):
        if row.id in superseded:
            resolved.append(row.id)
            continue
        scope = document(row.scope)
        # A durable scheduled run owns its slices; represented below once per run.
        if scope.get('scheduled_run') and row.recovery_state in (None, 'none'):
            continue
        view = interpretation(row)
        destination = url_for('main.operation_detail', operation_id=row.id)
        if view['recovery']:
            recovery.append(_issue('recovery:'+row.id, 'recovery', row.operation_type.replace('_', ' ').title(),
                redact_diagnostic(row.error or row.recovery_state, limit=500), 'Recovery Required', row.finished_at or row.started_at,
                'Review original receipt; do not retry an uncertain write', destination, receipt=destination,
                identifier=row.id, resolution='Recovery Required'))
            continue
        scope_key, scoped = _scope_key(row)
        if view['execution'] in {'failed', 'partial', 'interrupted'}:
            summary = operation_summary(scope)
            errors = summary.get('errors') or []
            unassessed = (view['execution'] == 'partial' and not (row.error or row.products_failed or errors
                or summary.get('failures') or summary.get('blocking_errors') or summary.get('failed_stage')
                or summary.get('pending') or summary.get('deferred') or summary.get('execution_complete') is False))
            detail = next((e.get('message') for e in errors if isinstance(e, dict) and e.get('message')), None) if isinstance(errors, list) else None
            condition = row.error or detail or 'incomplete_execution'
            key = scope_key+':'+hashlib.sha256(str(condition).encode()).hexdigest()[:12]
            prior = active.get(key)
            active[key] = _issue(key, 'history_review' if unassessed else 'failures', row.operation_type.replace('_', ' ').title(),
                redact_diagnostic(row.error or detail or 'Required execution was not recorded complete. Inspect retained stage evidence.', limit=500),
                'Action Required' if unassessed else 'Error' if view['execution'] == 'failed' else 'Warning', row.finished_at or row.started_at,
                'Review original receipt; current failure or historical success is not established' if unassessed else 'Inspect failed stages and safe recovery guidance', destination, receipt=destination,
                identifier=row.id, observations=(prior or {}).get('observations', 0)+1,
                first_detected=(prior or {}).get('first_detected', row.started_at),
                resolution='Evidence review required' if unassessed else 'Reopened' if key in resolved else 'Existing' if prior else 'New',
                scope_known=scoped, scope_key=scope_key)
        elif view['execution'] == 'succeeded' and row.operation_type in READ_TYPES and scoped:
            # The full requested scope, not one incomplete slice, must be conclusive.
            summary = operation_summary(scope)
            if summary.get('execution_complete') is True:
                for key in [key for key, issue in active.items() if issue['scope_key'] == scope_key]:
                    active.pop(key)
                    resolved.append(key)
    return {'failures': [i for i in active.values() if i['category'] == 'failures'],
            'review': [i for i in active.values() if i['category'] == 'history_review'],
            'recovery': recovery, 'resolved': resolved}


def catalogue_observations(row):
    """Attach small validation observations to *new* existing receipts only.

    Explicit booleans supply lifecycle evidence; absence never means resolution.
    Only committed projections and actually checked targets are observed.
    """
    from flask import g, has_request_context
    from app.models import CatalogueOperationItem
    scope = document(row.scope)
    summary = operation_summary(scope)
    if row.operation_type == 'shopify_status_refresh':
        ids = summary.get('checked_ids') or []
        query = Product.query.filter(Product.id.in_(ids))
    elif row.operation_type in {'append', 'full', 'product_update', 'shared_collection_update'}:
        sources = [i.source_path for i in CatalogueOperationItem.query.filter_by(
            operation_id=row.id, database_state='committed').all()]
        query = Product.query.filter(Product.source_relpath.in_(sources))
    else:
        return {}
    from app.dashboard import _missing_image_product_ids
    if has_request_context() and hasattr(g, '_missing_catalogue_image_product_ids'):
        delattr(g, '_missing_catalogue_image_product_ids')
    missing_images = set(_missing_image_product_ids())
    observations = {}
    for product in query.filter_by(catalogue_status='active').limit(1000).all():
        observations[f'product:{product.id}:description'] = not (
            (product.short_description or '').strip() or (product.description or '').strip())
        observations[f'product:{product.id}:images'] = product.id in missing_images
        observations[f'product:{product.id}:seo'] = any(not (getattr(product, k) or '').strip()
            for k in ('meta_title','meta_description'))
    return observations


def lifecycle(items, rows):
    observed = {}
    for row in rows:
        observations = document(row.scope).get('reporting_observations') or {}
        if not isinstance(observations, dict):
            continue
        for key, present in observations.items():
            if type(present) is not bool:
                continue
            prior = observed.get(key)
            reopened = bool(present and prior and (not prior['present'] or prior['reopened']))
            observed[key] = {'present': present, 'reopened': reopened,
                'repeated': bool(present and prior and prior['present']),
                'at': row.finished_at or row.started_at,
                'receipt': url_for('main.operation_detail', operation_id=row.id)}
    for issue in items:
        evidence = observed.get(issue['id'])
        if not evidence:
            continue
        issue['receipt'] = evidence['receipt']
        if evidence['present']:
            issue.update(resolution='Reopened' if evidence['reopened'] else 'Existing' if evidence['repeated'] else 'New',
                         detected=evidence['at'])
        else:
            # Current authoritative projection wins over an old observation;
            # do not suppress a genuinely returned condition.
            issue['resolution'] = 'Returned since last validation; revalidate'
    return observed


@saved_read_view
def issue_projection():
    """Single source for counters, filtered drill-down and read-only diagnostics."""
    from app.dashboard import _missing_image_product_ids
    from app.shopify_products import states_for
    products = Product.query.order_by(Product.id).all()
    rows = CatalogueOperation.query.order_by(CatalogueOperation.started_at, CatalogueOperation.id).all()
    operation = operation_issues(rows)
    issues = operation['failures'] + operation['recovery']
    missing_images = set(_missing_image_product_ids())
    active = [p for p in products if p.catalogue_status == 'active']
    states = states_for(active)
    # Legacy read receipts can lack an explicit successful sibling receipt.
    # Complete, current comparison evidence newer than the incident is equally
    # authoritative, but never proves the result of a historical write.
    product_map = {p.id: p for p in active}
    from app.catalogue_views import jobs_for
    publish_jobs = jobs_for(active)
    retained_failures = []
    by_operation = {r.id: r for r in rows}
    for incident in operation['failures'] + operation['review']:
        row = by_operation[incident['identifier']]
        scope = document(row.scope)
        ids = scope.get('products')
        resolved = False
        # A publisher's local preflight receipt explicitly proves no write was
        # attempted. A later revalidated desired revision can resolve that exact
        # prerequisite, never an actual failed or uncertain remote mutation.
        if (scope.get('mode') == 'automatic_preflight'
                and operation_summary(scope).get('write_attempted') is False):
            job = publish_jobs.get(scope.get('product_id'))
            resolved = bool(job and job.state == 'verified' and job.verified_revision == job.requested_revision
                and job.updated_at > (row.finished_at or row.started_at))
            if not resolved and job:
                from app.automatic_publishing import no_changes_settlement, draft_pause_settlement
                proof = no_changes_settlement(job)
                resolved = bool(proof and proof.get('operation_id') == row.id
                    and states.get(job.product_id, {}).get('state') == 'no_change'
                    and job.updated_at > (row.finished_at or row.started_at))
                paused = draft_pause_settlement(job)
                resolved = resolved or bool(paused and paused.get('operation_id') == row.id
                    and job.updated_at > (row.finished_at or row.started_at))
        if row.operation_type == 'shopify_status_refresh' and isinstance(ids, list) and 0 < len(ids) < 20:
            try:
                from datetime import datetime
                from app.catalogue_views import active as saved_read
                if saved_read():
                    # A completed later read resolves this historical read
                    # incident, independently of its product readiness or the
                    # age of its remote-freshness checkpoint. No source walk.
                    resolved = all(product_map.get(i) and states[i]['state'] in {'no_change', 'update', 'create'} and
                        states[i].get('compared_at') and datetime.fromisoformat(states[i]['compared_at'].removesuffix('Z')) >
                        (row.finished_at or row.started_at) for i in ids)
                else:
                    from app.scheduled_tasks import recent_success
                    from app.shopify_drafts import configuration
                    config = configuration()
                    resolved = all(product_map.get(i) and recent_success(product_map[i], config) and
                        datetime.fromisoformat(states[i]['compared_at'].removesuffix('Z')) > (row.finished_at or row.started_at)
                        for i in ids)
            except (ValueError, TypeError, KeyError, OSError, RuntimeError):
                resolved = False
        if not resolved:
            retained_failures.append(incident)
    issues = retained_failures + operation['recovery']
    for product in products:
        destination = url_for('main.product_detail', product_id=product.id)
        common = dict(entity=product.title or product.sku, detected=None,
                      identifier=product.sku, product_id=product.id, source_updated=product.local_updated_at)
        if product.catalogue_status == 'missing':
            common['detected'] = product.missing_at
            issues.append(_issue(f'product:{product.id}:missing', 'missing', cause='Source product was absent during authoritative catalogue reconciliation.',
                severity='Action Required', action='Inspect product and source folder', destination=destination, **common))
            continue
        if product.catalogue_status != 'active':
            continue
        if not (product.short_description or '').strip() and not (product.description or '').strip():
            issues.append(_issue(f'product:{product.id}:description', 'metadata', cause='Both description and short description are missing.',
                severity='Action Required', action='Edit product metadata', destination=destination, **common))
        if product.id in missing_images:
            issues.append(_issue(f'product:{product.id}:images', 'images', cause='No safe original parent or variation artwork resolves. Publishing requires original artwork.',
                severity='Action Required', action='Inspect product artwork; then rescan', destination=destination, **common))
        missing_seo = [name for name in ('meta_title', 'meta_description') if not (getattr(product, name) or '').strip()]
        if missing_seo:
            issues.append(_issue(f'product:{product.id}:seo', 'information', cause='Optional SEO fields absent: '+', '.join(missing_seo)+'.',
                severity='Information', action='Review optional product metadata', destination=destination, **common))
        state = states[product.id]
        # Unknown without discovery evidence is not an invented reconciliation failure.
        if state['state'] in {'update', 'attention', 'unavailable', 'local_changes', 'processing'} and not state.get('execution_pending'):
            details = (state.get('blockers') or [])[:3] + (state.get('change_summary') or [])[:3]
            issues.append(_issue(f'product:{product.id}:shopify', 'shopify', cause=redact_diagnostic(state['message']+' '+ '; '.join(details), limit=900),
                severity='Warning' if state['state'] == 'unavailable' else 'Action Required',
                action='Open Review & Sync' if state.get('review_url') else 'Inspect product',
                destination=state.get('review_url') or destination, **common))
        elif state['state'] == 'recovery_required':
            # Avoid double-counting a product whose original operation is already listed.
            has_receipt = any(document(r.scope).get('product_id') == product.id and
                              r.recovery_state not in (None, 'none') for r in rows)
            if not has_receipt:
                issues.append(_issue(f'product:{product.id}:recovery', 'recovery', cause=state['message'], severity='Recovery Required',
                    action='Inspect retained identity; do not retry writes', destination=state.get('identity_url') or destination, **common))
    for variant in Variation.query.filter_by(catalogue_status='missing').all():
        issues.append(_issue(f'variant:{variant.id}:missing', 'missing', 'Variation '+str(variant.sku or variant.id),
            'Variation absent during authoritative catalogue reconciliation.', 'Action Required',
            variant.missing_at, 'Inspect parent product and variation',
            url_for('main.product_detail', product_id=variant.product_id), identifier=variant.sku))
    # Latest scheduled task result only; older runs remain in task history.
    latest = {}
    for run in ScheduledTaskRun.query.order_by(ScheduledTaskRun.created_at.desc()).all():
        latest.setdefault(run.task_key, run)
    for run in latest.values():
        if task_interpretation(run)['execution'] not in {'failed', 'partially_completed'}:
            continue
        data = document(run.data_json)
        cause = run.error or data.get('reason') or 'Required task targets remain incomplete; inspect task history.'
        issues.append(_issue('task:'+run.task_key, 'failures', run.task_key.replace('_', ' ').title(),
            redact_diagnostic(cause, limit=500), 'Error' if run.state == 'failed' else 'Warning', run.completed_at,
            'Inspect scheduled task history', url_for('tasks.run_detail', run_id=run.id), identifier=run.id))
    observations = lifecycle(issues, rows)
    counts = Counter(i['category'] for i in issues)
    return {'items': issues, 'counts': {key: counts[key] for key in CATEGORIES},
            'shopify_state': _shopify_state(),
            'historical_incomplete': sum(r.status in {'failed', 'partial', 'interrupted'} for r in rows),
            'historical_operations': len(rows), 'resolved_read_incidents': len(operation['resolved']),
            'resolved_catalogue_conditions': sum(not v['present'] for v in observations.values())}


def _shopify_state():
    from app.shopify_drafts import configuration
    from app.models import ShopifyProductComparison
    try:
        config = configuration()
        matching = [document(r.summary_json) for r in ShopifyProductComparison.query.all()
                    if document(r.summary_json).get('config') == config.fingerprint()]
    except (ValueError, RuntimeError, OSError):
        return 'Shopify not configured or configuration unavailable. Open connection settings.'
    if not matching:
        return 'Configuration saved; catalogue discovery and product verification have not yet completed.'
    return 'Retained product evidence available. Live connection is not tested on dashboard load; unknown/unlinked products still need discovery.'
