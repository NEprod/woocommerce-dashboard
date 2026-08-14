# Current State

This document records the Phase 0 baseline established from two read-only audits. Phase 0 deliberately documents rather than fixes existing operational limitations.

## Startup and setup

`run.py` creates the Flask application. The application factory configures Flask-SQLAlchemy, Flask-Login, CSRF protection, the main blueprint, and upgrades SQLite to the current Alembic migration head. `db.create_all()` is no longer used. Missing databases are initialized from migrations; a matching unversioned Phase 0 database is backed up and adopted at the frozen baseline.

A new installation follows `/` → `/setup` → `/initial-settings` → `/initial-scan`. Setup creates the initial administrator and stores the product root, output root, and public image URL prefix. The initial-scan screen currently submits append mode rather than full mode.

## Scanner modes

- **append** processes products without `.scanned` and products carrying `.update`.
- **update** uses the same selection rule and reuses parent/variation SKUs from `.scanned`.
- **full** forces processing and regenerates SKUs using index counters.

The scanner supports exact collection types `Simple`, `Variable Collection`, and `Single Variable`.

## Operation control

Append, product update, shared collection update, full, and reconstruction operation types share a non-blocking process-local lock. A conflicting request receives HTTP `409` with the active operation type and identifier before it changes catalogue files. Operation history is persistent and records bounded diagnostic fields; startup marks unfinished rows interrupted and requiring review. This control is intentionally limited to the documented single-worker, single-replica runtime.

## Verified catalogue/database consistency

At the audit baseline, the live local catalogue and SQLite database agreed for 11 parent SKUs and 49 variation SKUs. Database integrity passed. Titles, types, mapped prices, dates, dimensions, descriptions, images, variation attributes, and supported modifier results agreed for the currently ingested subset.

The real catalogue and database are never part of the repository or container image.

## Web UI

Authentication, initial settings, the initial scan screen, `/edit_products`, raw JSON viewing, and the JSON editor exist. The dashboard is a placeholder. Routes for Products, scanner, sync, orders, POS, tools, site, settings, and web sync refer to missing templates.

## Database projection

- The active path populates Collection → Product → Variation for all three exact collection types.
- Portable catalogue-relative source and JSON paths are stored separately from runtime absolute paths.
- Every emitted parent and variation row is retained as JSON; commonly queried pricing, inventory, publication, taxonomy, SEO, image, and attribute values are also normalized.
- Existing Product, Variation, and Woo placeholder identities are retained during ordinary row updates.

Remaining ingestion limitations are:

- Removed products and variations are not reconciled.
- Parent and variation ingestion use separate commits.
- `.scanned` is written before database ingestion succeeds.

Scanner characterization also confirms that variation modifier sale prices are not emitted by the variation row builder, authored shipping class is emitted as blank, list ordering is not stable, unknown collection types yield no rows, editor relationship-key names differ from the row builder, and Woo rows are limited to five attribute slots. These remain protected discrepancies pending separate contract decisions.

## Integrations

WooCommerce-compatible rows and future Woo ID columns exist, but there is no live WooCommerce or WordPress API client. Discord webhooks can receive scan start/completion/failure and per-product ingestion notifications. Delivery failures are not persisted or retried.

## Known operational risks

- Shared collection edits place `.update` at collection root, but Simple and Variable Collection products check child product folders.
- The initial UI exposes append, not full, mode.
- Multi-worker or multi-replica catalogue mutation is not supported; the lock is process-local.
- Scan progress is process-local and non-durable.
- Several routes are incomplete because templates are absent.

The protected scanner discrepancies remain unchanged; later Phase 1 milestones address transaction, marker recovery, reconciliation, and reconstruction concerns.
