# Current Data Model

## Authority split

```text
Filesystem/folder structure:
physical collection and product membership

product_info.json:
authored catalogue metadata and generation rules

.scanned:
processed-state and durable local SKU mapping

.scanned.pending:
recovery-only intended marker payload and operation/state reference

sku_index.json:
new SKU allocation counters

SQLite:
queryable resolved catalogue plus application/integration state

WooCommerce:
future downstream integration
```

SQLite is not independently authoritative for authored product metadata.

## Schema versioning

Alembic revisions define the SQLite schema. Revision `0001_phase0` freezes the Phase 0 tables and is also the adoption point for structurally matching unversioned Phase 0 databases. Revision `0002_operations` adds catalogue operation history. Revision `0003_projection` activates catalogue relationships, complete emitted-row storage, normalized metadata, and portable provenance. Revision `0004_lifecycle` adds soft missing/restored state, variation source identity, and lifecycle outcome counts. Application models do not create or alter tables directly at startup. See [Database Migrations](MIGRATIONS.md).

## Models

- `User`: local authentication and administrator flag.
- `Settings`: catalogue root, processed-image output root, and public URL prefix.
- `CatalogueOperation`: bounded scan/update/reconstruction history, projection and lifecycle counts, and recovery state.
- `CatalogueOperationItem`: per-parent ingestion/lifecycle outcome, portable source path, sanitized failure, database state, and marker-recovery state.
- `Collection`: stable catalogue-relative source identity, exact collection type, SKU prefix, runtime root, shared JSON provenance, and child products.
- `Product`: resolved parent identity, collection relationship, complete emitted row JSON, normalized commercial/content/publication/SEO fields, portable and runtime provenance, and future Woo sync fields.
- `ProductAttribute`: emitted parent attribute definitions, values, visibility/global flags, and position.
- `Variation`: child of Product with complete emitted row JSON, portable source provenance, canonical emitted-attribute identity, normalized SKU/price/dimension/image fields, lifecycle state, and future Woo fields.
- `ProductImage` / `VariationImage`: ordered image URL galleries.
- `VariationAttribute`: resolved name/value pairs.
- `ProductAsset`: local filesystem paths, actively used for shared and override JSON.
- `Category`, `Tag`, and association tables: emitted parent taxonomy membership.
- `Service`: dormant hosting/domain-oriented model.

Collection → Product → Variation is active and populated by normal ingestion. `source_relpath` and the JSON `*_relpath` columns are POSIX-style paths relative to `Settings.product_folder`; these are portable across host/container mount changes. Legacy `root_path`, `product_dir`, JSON path, and `ProductAsset.path` values remain absolute runtime locators for existing filesystem behavior.

`resolved_row_json` is the lossless boundary for every key/value actually emitted by the protected row builder, including blank values and characterized discrepancies. Normalized columns are the query surface and do not invent values that the scanner failed to emit.

Ordinary append/update ingestion commits the complete emitted parent graph and its successful operation item in one transaction. Existing matching rows are updated in place so Product, Variation, gallery, asset, attribute, taxonomy and Woo-placeholder identities are retained. A parent-stage failure rolls back that graph and is recorded separately as `database_state=rolled_back`; unrelated committed parents remain intact.

`Product.catalogue_status` and `Variation.catalogue_status` use `active` or
`missing`; `missing_at` records the soft transition and `restored_at` records the
latest return. Missing rows remain related and retain internal IDs, SKUs,
provenance, Woo placeholders, and historical timestamps. `Product.status` and
`Variation.status` remain the emitted/Woo publication status and are not reused
for catalogue presence.

Every successfully committed parent treats its emitted variation set as
authoritative inside the parent transaction. Product presence is reconciled only
from an approved exhaustive scope: catalogue-wide full/reconstruction or a
collection-limited shared refresh. Ordinary append and individual update scopes
are never authoritative for unseen products.

Reconstruction does not replace the database file or recreate application tables.
It updates the resolved Collection → Product → Variation projection inside one
transaction. Portable product source identity is matched before SKU; variation
attribute identity is matched before SKU. Consequently safe matches retain row
IDs, Woo placeholders, timestamps, relationships, and lifecycle history. User,
Settings, and prior CatalogueOperation rows are not part of projection replacement.
