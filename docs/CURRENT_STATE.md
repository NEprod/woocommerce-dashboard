# Current State

This document records the completed Phase 1 (`0.2.3`) catalogue-integrity release and the current Phase 2 development baseline. Phase 1 builds on the Phase 0 baseline without changing protected scanner row semantics.

## Startup and setup

`run.py` creates the Flask application. The application factory configures Flask-SQLAlchemy, Flask-Login, CSRF protection, the main blueprint, and upgrades SQLite to the current Alembic migration head. `db.create_all()` is no longer used. Missing databases are initialized from migrations; a matching unversioned Phase 0 database is backed up and adopted at the frozen baseline.

A new installation follows `/` → `/setup` → `/initial-settings` → `/initial-scan`. Setup creates the initial administrator and stores the product root, output root, and public image URL prefix. The initial-scan screen classifies the configured catalogue as new, requiring reconstruction, ready, or ambiguous. It shows collection/product/marker/projection counts and enables only a safe recommended action.

Production startup requires an explicitly supplied non-placeholder `SECRET_KEY`.
The application never generates or persists it. `/app/instance`, `/catalogue`,
and `/output` remain three separate required mounts; storage, backup, operation,
temporary-file, and Docker-log limits are documented in
[Storage and Retention](STORAGE_RETENTION.md).

## Scanner modes

- **append** processes products without `.scanned` and products carrying `.update`.
- **update** uses the same selection rule and reuses parent/variation SKUs from `.scanned`.
- **full** forces processing and regenerates SKUs using index counters.
- **shared collection refresh** explicitly forces every product in one collection
  while reusing marker identities. A shared JSON editor save invokes this mode;
  ordinary append and individual-update selection remain unchanged.
- **reconstruction** resolves the complete catalogue with marker and database
  identity reuse, disables SKU-index reset, creates a persistent SQLite backup,
  then updates the full projection in one controlled transaction.

Intentional full regeneration remains a separate warning-labelled action. The UI
and route require explicit confirmation because it retains the scanner's existing
SKU-reset behavior. An empty database never selects it automatically.

The scanner supports exact collection types `Simple`, `Variable Collection`, and `Single Variable`.

## Operation control

Append, product update, shared collection update, full, and reconstruction operation types share a non-blocking process-local lock. A conflicting request receives HTTP `409` with the active operation type and identifier before it changes catalogue files. Operation history is persistent and records bounded diagnostic fields and lifecycle counts; startup marks unfinished rows interrupted and requiring review. This control is intentionally limited to the documented single-worker, single-replica runtime.

Routine successful history is retained for at least 180 days and the newest
1,000 entries; resolved failure history is retained for at least 365 days.
Active, pending, unresolved-recovery, and newest-per-type records are protected.
Process memory retains 20 ordinary completed runs, while active/recovery runs and
their completion summaries remain protected. Each live log queue is bounded to
2,000 lines and approximately 2 MiB.

Ordinary scan ingestion adds one operation item per emitted parent. Successful items are committed with their parent transaction. A failed parent is rolled back and receives a separate sanitized failed item; the operation becomes `partial` when other parents succeeded or `failed` when none did.

Production scans stage `.scanned.pending` before database ingestion and finalize `.scanned` only after the corresponding parent commits. Database failures retain/recreate `.update`; marker-finalization failures retain pending identity. The next operation finalizes already committed intents before scanning and retries only unresolved products with preserved parent/variation SKUs. Marker and index JSON replacements are atomic.

Reconstruction does not rewrite a valid `.scanned`, reset `sku_index.json`, or
remove an existing `.update`. Database identity overlays supplement old marker
payloads for newly discovered variation combinations, so repeated reconstruction
does not allocate another SKU. Only genuinely unmarked products stage and finalize
a new marker. Outstanding pre-existing pending state makes the result partial and
`recovery_required` rather than falsely successful.

The authored `product_info.json` contract is now represented by collection and
partial-override JSON Schemas, a complete field inventory, fictional examples,
minimal/type-specific templates, and an authenticated in-app reference. Editor
saves validate before any backup, marker, operation lock, or scan side effect.
This is intentionally not scanner-wide strict enforcement; protected inheritance,
aliases, unknown collection types, and known discrepancies remain unchanged.

## Verified catalogue/database consistency

At the audit baseline, the live local catalogue and SQLite database agreed for 11 parent SKUs and 49 variation SKUs. Database integrity passed. Titles, types, mapped prices, dates, dimensions, descriptions, images, variation attributes, and supported modifier results agreed for the currently ingested subset.

The real catalogue and database are never part of the repository or container image.

## Web UI

Authentication, initial settings, the initial scan screen, collection-grouped
Products browser, raw JSON viewing, JSON editor, and metadata reference exist.

Phase 2 Milestone 1 adds an original responsive application shell based on
semantic design tokens. Bootstrap, application
JavaScript, and the project-owned SVG icon sprite are served locally without a
runtime CDN dependency. The permanent shell uses a desktop sidebar, compact
tablet rail, and a five-destination mobile bottom bar whose More action opens an
accessible off-canvas menu. Workspaces not implemented yet render explicit
`Planned` pages instead of missing templates or misleading functionality.
Legacy route aliases resolve safely. The setup folder browser is authenticated.

`DESIGN_SYSTEM.md` supersedes the interim Milestone 1.1 dark-first treatment.
The application is now light-first: warm canvas, white cards/forms/tables, dark
primary actions, restrained lime and teal, and deep-slate feature panels only
where they clarify hierarchy. JSON textareas, metadata examples, scanner logs,
table group headers, mobile navigation, and honest summary panels retain a
purposeful dark treatment. The original project logo is not redesigned.

Phase 2 Milestone 2 completes the local setup journey without redirecting away
from its result. Initial append/full scans and identity-preserving
reconstruction now finish on an honest summary with projection totals,
warnings, failures, and routes to Dashboard, Products, or the operation detail
already present on the page. A shared accessible operation-progress component
is used by setup and existing product metadata updates.

The scan runner's process-local progress payload retains its Phase 1 keys and
adds presentation-only operation type, stage, current collection, elapsed time,
collection/product/variation counts, warnings, and failures. These observations
do not change scanner selection, row resolution, operation locking, marker
coordination, ingestion, or persistent operation history. Reconstruction
remains synchronous in Phase 2 and publishes the same normalized completion
shape only after its existing controlled operation returns. Durable progress,
background reconstruction, and the full Operations workspace remain deferred.

Phase 2 Milestone 3 replaces the Dashboard placeholder with a read-only
catalogue-health view derived from the existing SQLite projection,
`CatalogueOperation` history, and the process-local operation state. It reports
real collection, parent-product, variation, active/missing, override, metadata
gap, recent-operation, and recently updated-product facts. Catalogue
availability is the share of projected parent products and variations whose
`catalogue_status` is `active`; metadata gaps are simple field-completeness
signals for active parents, not schema validation. Empty databases have honest
initial-scan actions. The Dashboard does not invent trends, sales, users, or a
WooCommerce connection, and does not add persistence or scanner side effects.

Phase 2 Milestone 4 replaces the flat Products table with a read-only,
collection-grouped browser over the existing SQLite projection. Collection
headers report filtered parent, variation, active/missing, and last-update
facts. Parent rows show genuine type, SKU, projected price/range, lifecycle,
thumbnail/fallback, metadata provenance, variation count, timestamp, and the
existing metadata actions. Title/SKU, collection, type, lifecycle, metadata
source, and Dashboard metadata-issue filters are URL-backed and parent results
are paginated on the server. Variation attributes, price, stock quantity,
lifecycle, provenance, and timestamps are fetched only when a variable parent
is expanded. Desktop uses grouped relational rows, while tablet and mobile use
collection-preserving product cards. This milestone adds no model, migration,
scanner, ingestion, marker, SKU, or filesystem behavior.

The Milestone 4 image-display follow-up serves genuine primary source images
from the mounted catalogue through an authenticated product-ID route. Products
and Dashboard Recent Products share the same square crop and safe fallback.
Woo-facing image references remain ordinary SQLite text; source image bytes are
not copied to SQLite, `/app/instance`, or the production image.

## Database projection

- The active path populates Collection → Product → Variation for all three exact collection types.
- Portable catalogue-relative source and JSON paths are stored separately from runtime absolute paths.
- Every emitted parent and variation row is retained as JSON; commonly queried pricing, inventory, publication, taxonomy, SEO, image, and attribute values are also normalized.
- Existing Product, Variation, and Woo placeholder identities are retained during ordinary row updates.
- Every committed parent's emitted variation set is authoritative. Stale
  variations become `missing` rather than being deleted, and matching rows are
  restored in place if emitted again.
- Products become `missing` only after a completely resolved and successful full
  scan, reconstruction, or collection-limited shared refresh. Append and
  individual product updates never reconcile unseen products.

Missing rows retain internal/SKU/Woo identity, provenance, relationships, and
timestamps. Product restoration matches portable `source_relpath` before SKU;
variation restoration matches its emitted attribute identity before SKU.

Scanner characterization also confirms that variation modifier sale prices are not emitted by the variation row builder, authored shipping class is emitted as blank, list ordering is not stable, unknown collection types yield no rows, editor relationship-key names differ from the row builder, and Woo rows are limited to five attribute slots. These remain protected discrepancies pending separate contract decisions.

## Integrations

WooCommerce-compatible rows and future Woo ID columns exist, but there is no live WooCommerce or WordPress API client. Discord webhooks can receive scan start/completion/failure and per-product ingestion notifications. Delivery failures are not persisted or retried.

## Known operational risks

- Multi-worker or multi-replica catalogue mutation is not supported; the lock is process-local.
- Scan progress is process-local and non-durable.
- Collections, detailed Scanner/Operations history, Settings, and future modules
  intentionally show availability-aware placeholders until their approved
  Phase 2 milestones are implemented.

The protected scanner discrepancies and intentional full-scan semantics remain
unchanged.
