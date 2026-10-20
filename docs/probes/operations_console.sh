python - <<'PY'
import json, sqlite3
from pathlib import Path
from collections import Counter
from flask import Flask
from sqlalchemy.engine import make_url
import app as package
from app import db
from app.models import CatalogueOperation, ScheduledTaskRun
from app.operation_reporting import document, interpretation, operation_issues
from app.dashboard import build_dashboard_data
from config import Config

url = make_url(Config.SQLALCHEMY_DATABASE_URI)
if url.get_backend_name() != 'sqlite':
    raise SystemExit('Stopped: expected the existing SQLite database.')
path = Path(url.database)
if not path.is_absolute():
    path = Path(package.__file__).resolve().parents[1] / 'instance' / path
def connect():
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    connection.execute('PRAGMA query_only=ON')
    return connection
probe = Flask('readonly-operations-console')
probe.config.from_object(Config)
probe.config.update(SQLALCHEMY_DATABASE_URI='sqlite://', SQLALCHEMY_ENGINE_OPTIONS={'creator': connect})
for endpoint, route in (
    ('main.operation_detail', '/operations/<operation_id>'),
    ('main.operation_issues_page', '/operations/issues'),
    ('main.product_detail', '/products/<int:product_id>'),
    ('commerce.drafts', '/products/<int:product_id>/shopify/review'),
    ('commerce.draft_identity', '/shopify/identities/<int:identity_id>'),
    ('tasks.run_detail', '/tasks/runs/<run_id>')):
    probe.add_url_rule(route, endpoint=endpoint)
db.init_app(probe)
def evidence(row):
    scope = document(row.scope)
    summary = scope.get('operation_summary') or {}
    errors = summary.get('errors') or []
    text = (str(row.error or '') + ' ' + ' '.join(str(e.get('message', '')) for e in errors if isinstance(e, dict))).lower()
    codes = [code for needle, code in (
        ('draft/archived intent cannot request channel publication', 'publication_intent_conflict'),
        ('managed evidence is incomplete', 'incomplete_managed_evidence'),
        ('pagination', 'pagination_incomplete'), ('throttl', 'rate_limited'),
        ('permission', 'permission_check'), ('access denied', 'access_denied'),
        ('timeout', 'timeout'), ('database', 'database_failure'),
        ('images', 'image_evidence'), ('reconcil', 'reconciliation_review')) if needle in text]
    kind = ('protected_recovery' if row.recovery_state not in (None, 'none') else
        'derived_completed' if interpretation(row)['execution'] == 'succeeded' else
        'continuation_slice' if summary.get('continuation') and not errors else
        'recorded_execution_error' if errors or row.error or row.products_failed else 'insufficient_retained_evidence')
    return dict(id=row.id, type=row.operation_type, stored_status=row.status,
        derived_status=interpretation(row)['execution'], evidence_class=kind,
        reason_codes=codes or ['receipt_review_required'], recorded_errors=len(errors),
        pending=len(summary.get('pending') or []), execution_complete=summary.get('execution_complete'))
with probe.test_request_context('/'):
    db.session.autoflush = False
    rows = CatalogueOperation.query.order_by(CatalogueOperation.started_at, CatalogueOperation.id).all()
    by_id = {r.id: r for r in rows}
    incidents = operation_issues(rows)
    refreshes = [evidence(r) for r in rows if r.operation_type == 'shopify_status_refresh' and r.status == 'partial']
    report = dict(database_writes=0, shopify_requests=0, historical_operations=len(rows),
        historical_raw_incomplete=sum(r.status in {'partial','failed','interrupted'} for r in rows),
        historical_partial_refreshes=len(refreshes),
        historical_refresh_evidence_groups=dict(Counter(r['evidence_class'] for r in refreshes)),
        historical_refresh_reason_groups=dict(Counter(code for r in refreshes for code in r['reason_codes'])),
        ledger_incidents=[dict(evidence(by_id[i['identifier']]), observations=i['observations']) for i in incidents['failures']],
        historical_cases_needing_evidence_review=[dict(evidence(by_id[i['identifier']]), observations=i['observations']) for i in incidents.get('review', [])],
        protected_recovery=[dict(id=r.id, type=r.operation_type, state=r.recovery_state) for r in rows if r.recovery_state not in (None,'none')])
    try:
        data = build_dashboard_data()
        stored = {r.id: r.status for r in rows}
        stored.update({r.id: r.state for r in ScheduledTaskRun.query.all()})
        report.update(local_catalogue=data['health'], active_issue_counts=data['issue_data']['counts'],
            recent_changes=[dict(id=r['id'], type=r['type'], displayed_status=r['status_label'], stored_status=stored.get(r['id'])) for r in data['recent_operations']])
    except Exception as error:
        report['dashboard_projection_unavailable'] = type(error).__name__
    report['note'] = 'Read-only. Recorded errors are historical evidence, not proof of a current failure. Reason groups overlap. No recovery was cleared.'
    print(json.dumps(report, indent=2, default=str))
PY
