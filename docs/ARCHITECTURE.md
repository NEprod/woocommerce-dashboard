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

Run state and mutual exclusion are process-local. Persistent operation rows provide history and interrupted-run diagnosis, not a distributed lock. This is why the Phase 1 container remains limited to one Gunicorn worker and one application replica. See [Catalogue Operation Control](CATALOGUE_OPERATIONS.md).

## Persistence

SQLite stores users, settings, resolved products, variations, images, attributes, JSON asset paths, operation history, and dormant integration-oriented models. Product folders, authored JSON, scanner markers, and SKU counters remain independent filesystem state.

Alembic owns SQLite schema initialization and upgrades. Application startup must reach migration head before requests are served. A structurally matching unversioned Phase 0 database is backed up and stamped at the frozen baseline; an unknown schema is rejected. Migration backups cover SQLite only and do not make filesystem catalogue state transactional.

## External services

Discord notifications are outbound webhook POST requests configured exclusively through runtime environment variables. WooCommerce support currently stops at CSV-compatible field construction and unused database mapping columns; no live WooCommerce or WordPress API integration exists.
