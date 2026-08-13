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

## Verified catalogue/database consistency

At the audit baseline, the live local catalogue and SQLite database agreed for 11 parent SKUs and 49 variation SKUs. Database integrity passed. Titles, types, mapped prices, dates, dimensions, descriptions, images, variation attributes, and supported modifier results agreed for the currently ingested subset.

The real catalogue and database are never part of the repository or container image.

## Web UI

Authentication, initial settings, the initial scan screen, `/edit_products`, raw JSON viewing, and the JSON editor exist. The dashboard is a placeholder. Routes for Products, scanner, sync, orders, POS, tools, site, settings, and web sync refer to missing templates.

## Database ingestion limitations

- The active path does not populate `Collection` or `Product.collection_id`.
- Exact filesystem collection type is collapsed to `Simple` or `Variable`.
- Categories, tags, SEO fields, and publication state are not actively mapped.
- Removed products and variations are not reconciled.
- Parent and variation ingestion use separate commits.
- `.scanned` is written before database ingestion succeeds.

Scanner characterization also confirms that variation modifier sale prices are not emitted by the variation row builder, authored shipping class is emitted as blank, list ordering is not stable, unknown collection types yield no rows, editor relationship-key names differ from the row builder, and Woo rows are limited to five attribute slots. These remain protected discrepancies pending separate contract decisions.

## Integrations

WooCommerce-compatible rows and future Woo ID columns exist, but there is no live WooCommerce or WordPress API client. Discord webhooks can receive scan start/completion/failure and per-product ingestion notifications. Delivery failures are not persisted or retried.

## Known operational risks

- Shared collection edits place `.update` at collection root, but Simple and Variable Collection products check child product folders.
- The initial UI exposes append, not full, mode.
- Concurrent in-process scans are not locked.
- Scan progress is process-local and non-durable.
- Several routes are incomplete because templates are absent.

Phase 0 does not fix any of these issues.
