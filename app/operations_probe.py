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


def main():
    url = make_url(Config.SQLALCHEMY_DATABASE_URI)
    if url.get_backend_name() != 'sqlite' or not url.database:
        raise SystemExit('This read-only diagnostic requires the existing SQLite file.')
    database = Path(url.database)
    if not database.is_absolute():
        database = Path(__file__).resolve().parents[1] / 'instance' / database
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
    print(json.dumps({'database_writes': 0, 'shopify_requests': 0,
        'historical_operation_rows': len(rows),
        'historical_raw_incomplete_rows': sum(r.status in {'partial','failed','interrupted'} for r in rows),
        'historical_groups': [{'type': k[0], 'stored_status': k[1], 'count': n} for k,n in sorted(groups.items())],
        'current_operation_incidents': len(issues['failures']),
        'resolved_same_scope_read_incidents': len(issues['resolved']),
        'derived_completed_with_original_partial': sum(interpretation(r)['corrected'] for r in rows),
        'retained_recovery_cases': [{'id': r.id, 'type': r.operation_type, 'state': r.recovery_state}
            for r in rows if r.recovery_state not in (None, 'none')],
        'note': 'Operation ledger only. Dashboard also derives product readiness and latest scheduled task incidents. No ambiguous write was cleared.'}, indent=2))


if __name__ == '__main__':
    main()
