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

All HTTP routes currently live in a single blueprint in `app/routes.py`. Jinja
templates provide the UI over bundled Bootstrap assets. The Phase 2 shell uses
one semantic-token stylesheet, project-owned SVG symbols, and a small local
JavaScript controller; it does not require Bootstrap, jQuery, fonts, or icons
from a public CDN at runtime.

The authenticated shell uses a desktop sidebar, tablet icon rail, mobile primary
bottom navigation, and a secondary More drawer. Navigation groups Dashboard,
Catalogue, Scanner/Operations, Metadata, System, and Future workspaces. Incomplete workspaces use one shared
`Planned` template and never imply that a backend integration exists. Legacy
route aliases redirect to an appropriate safe workspace.

## Scanner

An authenticated request first acquires the catalogue operation lock, creates an in-memory run record, and starts a daemon thread. The thread reads the `Settings` row, enumerates collection folders, invokes the scanner, ingests generated rows, and updates progress. Server-Sent Events stream log lines while browser polling reads counts and status. Editor mutations acquire the same lock before changing JSON or marker files.

Before ordinary selection, the thread recovers any `.scanned.pending` whose recorded parent transaction already committed. Selected products atomically stage pending marker intent while retaining an existing `.scanned` and `.update`. After each parent transaction, the coordinator either finalizes `.scanned` and removes `.update`, records `database_recovery_required`, or records `marker_recovery_required`. Pending identity is catalogue-local and contains only the established marker payload plus bounded coordination fields.

Run state and mutual exclusion are process-local. Persistent operation rows provide history and interrupted-run diagnosis, not a distributed lock. This is why the Phase 1 container remains limited to one Gunicorn worker and one application replica. See [Catalogue Operation Control](CATALOGUE_OPERATIONS.md).

Phase 2 exposes a backward-compatible presentation view over that existing
process-local state. Legacy `total`, `done`, `status`, and `summary` fields
remain intact; normalized operation, progress, timing, and count objects drive
one shared accessible component across setup and metadata-triggered updates.
The added stage/current-item fields are observational only. They neither add a
queue nor persist live progress, and reconstruction remains the existing
synchronous controlled operation.

The authenticated Dashboard is composed by `app/dashboard.py`. Its queries are
read-only views over Collection, Product, Variation, ProductImage, and
CatalogueOperation records, supplemented by the existing process-local active
operation observation. The route does not cache, migrate, reconcile, or mutate
catalogue state. Summary and completeness values are derived at request time;
recent lists are deliberately bounded.

The authenticated Products browser is composed by `app/products_browser.py`.
`/api/edit_products` remains the backward-compatible parent endpoint while
adding collection groups, genuine summary facts, supported URL filters, and
server-side pagination. Correlated aggregate queries provide variation counts
and price ranges without loading child rows. The separate authenticated
`/api/products/<id>/variations` endpoint loads ordered variation attributes and
other projected child facts only after expansion. Both paths are read-only;
existing metadata editor, raw-source, override creation, and override deletion
routes remain the action authority.

Products and Dashboard thumbnails use the authenticated opaque route
`/catalogue-images/products/<id>`; expanded variation previews use
`/catalogue-images/variations/<id>`. SQLite continues to store the scanner's
Woo-facing image URL and portable source provenance rather than image bytes.
For UI display, files beneath the configured catalogue mount are authoritative;
emitted URLs are filename hints because uploader conversion can change the
extension to `.webp` and may change the upload name.

`app/catalogue_images.py` follows scanner-supported folders only. Parent
resolution uses the ordered primary gallery mapping, the product shortcut,
remaining ordered parent images, recorded `.scanned.images_used`, then safe
direct-folder discovery. If no parent source resolves, the first ordered valid
variation image becomes the parent thumbnail. Variation routes preserve their
own Single Variable image-attribute folder identity and fall back to the parent
only when necessary; Variable Collection variations retain the scanner's shared
parent-image behavior.

Single Variable collections reserve the semantic name `parent` at the collection
root for the parent primary/gallery set. Its directory match is case-insensitive,
preserves the actual spelling in portable provenance, and rejects multiple
case-variants as ambiguous. Configured image-attribute names define variation
directory depth in order; every case-variant of `parent` is excluded from that hierarchy. Ingestion
keeps the scanner-generated website URLs and positions in `ProductImage` and
`VariationImage`, and records confined portable source identities as image
`ProductAsset` rows. UI resolution prefers those persisted source identities,
then compatible marker/URL discovery, and uses a variation preview only when no
usable genuine parent source remains.
PNG, JPG, JPEG, and WebP sources are accepted
case-insensitively by extension. Traversal, symlink escape, unsupported and
invalid files are rejected, and catalogue paths are never returned to the
browser.

`app/metadata_workspace.py` is the read-only composition boundary for Product
Detail and metadata source editors. It resolves collection and override JSON by
portable catalogue-relative identity, confines reads beneath the configured
catalogue root, applies the protected `merge_product_json()` behavior, and
presents collection/override/resolved comparisons without mutating the
projection. `/products/<id>` is the canonical Product Detail route;
`/collections/<id>/metadata` edits the shared source and the established
`/edit_products/<id>/edit/<label>` compatibility route opens the same guided
editor. The old save endpoint remains the only write authority.

Gallery routes extend the opaque authenticated image interface with a bounded
ordered index for product- and variation-owned sources. They recalculate each
confined mapping and never accept a filesystem path from the request. Stored
website URLs remain read-only diagnostics. A parent preview fallback shown for
a variation is explicitly labelled and never creates a variation image URL.

Product Detail loads at most 24 variation children initially. Further detail
uses `/api/products/<id>/detail-variations`; collection editor previews use the
paginated `/api/collections/<id>/affected-products`. Relationships required by
each page are select-in loaded so page size, rather than catalogue size, bounds
work and avoids per-row database access.

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
