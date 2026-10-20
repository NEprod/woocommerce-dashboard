"""Read-only historical/current execution evidence; no app startup or migrations.

Run in the installed container: python -m app.operations_probe
No credentials, error bodies, source paths or customer details are printed.
"""
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from flask import Flask
from sqlalchemy.engine import make_url
from config import Config


def execution_evidence(row):
    """Allowlisted reason codes only: never export arbitrary error bodies/PII.

    A historical recorded error is not proof the condition still exists, nor
    does a readiness-looking message prove all remote reads completed.
    """
    from app.operation_reporting import document, interpretation
    scope = document(row.scope)
    summary = scope.get('operation_summary') or {}
    messages = [str(row.error or '')] + [str(e.get('message') or '') for e in summary.get('errors', []) if isinstance(e, dict)]
    text = ' '.join(messages).lower()
    codes = []
    for needle, code in (
        ('draft/archived intent cannot request channel publication', 'authored_publication_conflict'),
        ('managed evidence is incomplete', 'incomplete_managed_evidence'),
        ('incomplete channel', 'incomplete_channel_read'), ('pagination', 'pagination_incomplete'),
        ('throttl', 'rate_limited'), ('access denied', 'access_denied'),
        ('permission', 'permission_check'), ('timed out', 'timeout'), ('timeout', 'timeout'),
        ('persistence', 'persistence_failure'), ('database', 'database_failure'),
        ('images', 'image_evidence'), ('reconcil', 'reconciliation_review')):
        if needle in text and code not in codes:
            codes.append(code)
    view = interpretation(row)
    classification = ('protected_recovery' if view['recovery'] else
        'derived_completed' if view['execution'] == 'succeeded' else
        'continuation_slice' if scope.get('scheduled_run') and summary.get('continuation') and not summary.get('errors') else
        'recorded_execution_error' if summary.get('errors') or row.error or row.products_failed else
        'insufficient_retained_execution_evidence')
    return {'id': row.id, 'type': row.operation_type, 'stored_status': row.status,
        'derived_execution': view['execution'], 'evidence_class': classification,
        'reason_codes': codes or ['receipt_review_required'],
        'execution_complete_recorded': summary.get('execution_complete') is True,
        'recorded_error_count': len(summary.get('errors') or []),
        'pending_count': len(summary.get('pending') or []),
        'scheduled_slice': bool(scope.get('scheduled_run')),
        'next_action': 'Review original receipt; do not infer current failure or historical success from a stored Partial label.'}


def dashboard_snapshot(database):
    """Use the real dashboard query against a read-only SQLite connection.

    No application factory, migrations, workers, notification sends or remote
    reads. Missing source mounts stay missing; never invent catalogue evidence.
    """
    from app import db
    app = Flask('read-only-dashboard-probe')
    app.config.from_object(Config)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite://'
    def connect():
        connection = sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True)
        connection.execute('PRAGMA query_only=ON')
        return connection
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'creator': connect}
    for endpoint, path in (
        ('main.operation_detail', '/operations/<operation_id>'),
        ('main.operation_issues_page', '/operations/issues'),
        ('main.product_detail', '/products/<int:product_id>'),
        ('commerce.drafts', '/products/<int:product_id>/shopify/review'),
        ('commerce.draft_identity', '/shopify/identities/<int:identity_id>'),
        ('tasks.run_detail', '/tasks/runs/<run_id>')):
        app.add_url_rule(path, endpoint=endpoint)
    db.init_app(app)
    from app.dashboard import build_dashboard_data
    from app.models import CatalogueOperation, ScheduledTaskRun
    with app.test_request_context('/'):
        db.session.autoflush = False
        data = build_dashboard_data()
        stored = {r.id: r.status for r in CatalogueOperation.query.all()}
        stored.update({r.id: r.state for r in ScheduledTaskRun.query.all()})
        return {'local_catalogue': data['health'], 'active_issue_counts': data['issue_data']['counts'],
            'counting_units': {r['key']: r['unit'] for r in data['attention_links']},
            'recent_changes': [dict(id=r['id'], type=r['type'], displayed_status=r['status_label'], stored_status=stored.get(r['id']))
                for r in data['recent_operations']],
            'note': 'Dashboard projection, not a live Shopify test. Recent Changes status is derived; ledger stored status is retained separately.'}


def main():
    url = make_url(Config.SQLALCHEMY_DATABASE_URI)
    if url.get_backend_name() != 'sqlite' or not url.database:
        raise SystemExit('This read-only diagnostic requires the existing SQLite file.')
    database = Path(url.database)
    if not database.is_absolute():
        # Also supports streaming this probe through `python -` into an existing
        # container, without replacing any deployed application file.
        import app as application
        database = Path(application.__file__).resolve().parents[1] / 'instance' / database
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True) as connection:
        connection.execute('PRAGMA query_only=ON')
        connection.row_factory = sqlite3.Row
        rows = [SimpleNamespace(**dict(row)) for row in connection.execute(
            'SELECT id,operation_type,status,scope,started_at,finished_at,products_attempted,'
            'products_succeeded,products_failed,error,marker_state,recovery_state FROM catalogue_operation ORDER BY started_at,id')]
    for row in rows:
        row.started_at = datetime.fromisoformat(row.started_at)
        row.finished_at = datetime.fromisoformat(row.finished_at) if row.finished_at else None
    app = Flask('read-only-operations-probe')
    app.add_url_rule('/operations/<operation_id>', endpoint='main.operation_detail')
    from app.operation_reporting import operation_issues, interpretation
    with app.test_request_context():
        issues = operation_issues(rows)
    groups = Counter((r.operation_type, r.status) for r in rows)
    by_id = {r.id: r for r in rows}
    partial_refresh = [execution_evidence(r) for r in rows if r.operation_type == 'shopify_status_refresh' and r.status == 'partial']
    try:
        dashboard = dashboard_snapshot(database)
    except Exception as exc:
        # Never export exception bodies, which can include source paths/values.
        dashboard = {'available': False, 'reason': type(exc).__name__,
            'next_action': 'Read-only dashboard projection could not run. Inspect dashboard locally; no success or zero count is inferred.'}
    print(json.dumps({'database_writes': 0, 'shopify_requests': 0,
        'historical_operation_rows': len(rows),
        'historical_raw_incomplete_rows': sum(r.status in {'partial','failed','interrupted'} for r in rows),
        'historical_groups': [{'type': k[0], 'stored_status': k[1], 'count': n} for k,n in sorted(groups.items())],
        'current_operation_incidents': len(issues['failures']),
        'historical_cases_needing_evidence_review': len(issues['review']),
        'incident_evidence': [dict(execution_evidence(by_id[i['identifier']]),
            observations=i['observations'], scope_known=i['scope_known']) for i in issues['failures']],
        'unassessed_receipt_evidence': [dict(execution_evidence(by_id[i['identifier']]),
            observations=i['observations'], scope_known=i['scope_known']) for i in issues['review']],
        'historical_partial_refresh_evidence_groups': dict(Counter(r['evidence_class'] for r in partial_refresh)),
        'historical_partial_refresh_reason_groups': dict(Counter(code for r in partial_refresh for code in r['reason_codes'])),
        'dashboard': dashboard,
        'resolved_same_scope_read_incidents': len(issues['resolved']),
        'derived_completed_with_original_partial': sum(interpretation(r)['corrected'] for r in rows),
        'retained_recovery_cases': [{'id': r.id, 'type': r.operation_type, 'state': r.recovery_state}
            for r in rows if r.recovery_state not in (None, 'none')],
        'note': 'Conservatively retained ledger incidents, not proof that every historical condition is currently active. Dashboard additionally evaluates current product comparisons and latest scheduled task incidents. Reason groups overlap. Stored statuses are never rewritten; no ambiguous write was cleared.'}, indent=2))


if __name__ == '__main__':
    main()
