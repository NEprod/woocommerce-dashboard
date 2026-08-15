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

Ordinary append/update ingestion groups emitted variation rows beneath their emitted parent row. One SQLite transaction covers the collection relationship, parent projection and provenance, galleries, JSON assets, taxonomy, parent attributes, variations, variation attributes and variation galleries, stale-variation reconciliation, plus that parent's successful operation-history item. A stage failure rolls that parent graph back and records a separate sanitized failed item; parents committed by earlier transactions remain committed. Stale variations are soft-marked `missing` in that transaction and restored in place when their emitted identity returns.

Product-level reconciliation is a separate post-ingestion transaction and is
allowed only after an exhaustive scope has resolved, every selected parent has
committed, and marker finalization has succeeded. Deliberate full scans cover the
catalogue; shared JSON saves explicitly force every product in one collection and
cover only that collection. Expected filesystem products are preflighted and
compared with emitted parents. Missing, empty, unreadable, invalid, or partially
resolved scope prevents product reconciliation. Append and individual update scans never
treat unseen products as missing.

## Reconstruction

Setup detection reads only the configured repository-fixture catalogue and the
current projection. It distinguishes no identities, existing marker/pending
identities, an existing projection, and malformed/unavailable state. Ambiguous
state blocks identity-generating actions rather than falling back to full scan.

Reconstruction acquires the catalogue-operation lock, preflights every collection,
override JSON object, marker, and expected parent, then resolves all scanner rows
before touching the projection. It forces selection with SKU reuse enabled and
counter reset disabled. Valid markers are read but not rewritten. Database
identity overlays add safely matched variation identities that are newer than an
old marker payload.

After complete resolution, SQLite is integrity-checked and backed up beneath the
active instance directory. Collection, product, variation, provenance, taxonomy,
and lifecycle changes are applied in one transaction. Users, settings, operation
history, internal IDs, and Woo placeholders are outside replacement or are updated
in place. A parent/replacement failure rolls the transaction back; the verified
backup remains. New-product pending markers finalize only after commit. Existing
pending recovery makes the operation partial. Discord and Woo integrations are
not invoked.

Alembic owns SQLite schema initialization and upgrades. Application startup must reach migration head before requests are served. A structurally matching unversioned Phase 0 database is backed up and stamped at the frozen baseline; an unknown schema is rejected. Migration backups cover SQLite only and do not make filesystem catalogue state transactional.

Atomic replacement prevents torn JSON files but does not make SQLite, marker files, SKU indexes, and processed images one transaction. Operational backup and recovery must include both the instance mount and catalogue/output mounts.

## External services

Discord notifications are outbound webhook POST requests configured exclusively through runtime environment variables. WooCommerce support currently stops at CSV-compatible field construction and unused database mapping columns; no live WooCommerce or WordPress API integration exists.
