# Existing Architecture

```text
Filesystem and product_info.json
        ↓
Scanner
        ↓
Resolved Woo-compatible rows
        ↓
SQLite ingestion
        ↓
Flask web UI
```

## Flask application

`app.create_app()` is the application factory. It initializes SQLAlchemy, login management, CSRF protection, routes, and the database tables. `run.py` is both the development entry point and Gunicorn import target.

All HTTP routes currently live in a single blueprint in `app/routes.py`. Jinja templates and bundled Bootstrap/Volt assets provide the UI.

## Scanner

An authenticated request first acquires the catalogue operation lock, creates an in-memory run record, and starts a daemon thread. The thread reads the `Settings` row, enumerates collection folders, invokes the scanner, ingests generated rows, and updates progress. Server-Sent Events stream log lines while browser polling reads counts and status. Editor mutations acquire the same lock before changing JSON or marker files.

Before ordinary selection, the thread recovers any `.scanned.pending` whose recorded parent transaction already committed. Selected products atomically stage pending marker intent while retaining an existing `.scanned` and `.update`. After each parent transaction, the coordinator either finalizes `.scanned` and removes `.update`, records `database_recovery_required`, or records `marker_recovery_required`. Pending identity is catalogue-local and contains only the established marker payload plus bounded coordination fields.

Run state and mutual exclusion are process-local. Persistent operation rows provide history and interrupted-run diagnosis, not a distributed lock. This is why the Phase 1 container remains limited to one Gunicorn worker and one application replica. See [Catalogue Operation Control](CATALOGUE_OPERATIONS.md).

## Persistence

SQLite stores users, settings, the complete emitted parent/variation row projection, exact Collection → Product → Variation relationships, images, attributes, taxonomy, JSON provenance, operation history, and dormant integration-oriented models. Product folders, authored JSON, scanner markers, and SKU counters remain independent filesystem state.

Each collection and product has a POSIX-style source path relative to the configured catalogue root. This is the portable identity/provenance representation and remains stable when the catalogue mount point changes. Existing absolute path columns remain runtime locators for filesystem routes; they are not portable identity. Parent and variation `resolved_row_json` retain every key/value actually emitted by the protected scanner, while normalized columns and related tables provide common query fields.

Ordinary append/update ingestion groups emitted variation rows beneath their emitted parent row. One SQLite transaction covers the collection relationship, parent projection and provenance, galleries, JSON assets, taxonomy, parent attributes, variations, variation attributes and variation galleries, plus that parent's successful operation-history item. A stage failure rolls that parent graph back and records a separate sanitized failed item; parents committed by earlier transactions remain committed. Current-row child reconciliation is within this boundary, but stale-child and missing-product policy is deferred to Milestone 7.

Alembic owns SQLite schema initialization and upgrades. Application startup must reach migration head before requests are served. A structurally matching unversioned Phase 0 database is backed up and stamped at the frozen baseline; an unknown schema is rejected. Migration backups cover SQLite only and do not make filesystem catalogue state transactional.

Atomic replacement prevents torn JSON files but does not make SQLite, marker files, SKU indexes, and processed images one transaction. Operational backup and recovery must include both the instance mount and catalogue/output mounts.

## External services

Discord notifications are outbound webhook POST requests configured exclusively through runtime environment variables. WooCommerce support currently stops at CSV-compatible field construction and unused database mapping columns; no live WooCommerce or WordPress API integration exists.
