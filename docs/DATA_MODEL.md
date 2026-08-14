# Current Data Model

## Authority split

```text
Filesystem/folder structure:
physical collection and product membership

product_info.json:
authored catalogue metadata and generation rules

.scanned:
processed-state and durable local SKU mapping

sku_index.json:
new SKU allocation counters

SQLite:
queryable resolved catalogue plus application/integration state

WooCommerce:
future downstream integration
```

SQLite is not independently authoritative for authored product metadata.

## Schema versioning

Alembic revisions define the SQLite schema. Revision `0001_phase0` freezes the Phase 0 tables and is also the adoption point for structurally matching unversioned Phase 0 databases. Revision `0002_operations` adds catalogue operation history. Application models do not create or alter tables directly at startup. See [Database Migrations](MIGRATIONS.md).

## Models

- `User`: local authentication and administrator flag.
- `Settings`: catalogue root, processed-image output root, and public URL prefix.
- `CatalogueOperation`: bounded scan/update/reconstruction history, product counts, and recovery state.
- `CatalogueOperationItem`: reserved per-parent database/marker recovery detail for later Phase 1 milestones.
- `Product`: resolved parent identity, commercial/content fields, state defaults, paths, and future Woo sync fields.
- `Variation`: child of Product with SKU, price/inventory/dimension fields, state defaults, and future Woo fields.
- `ProductImage` / `VariationImage`: ordered image URL galleries.
- `VariationAttribute`: resolved name/value pairs.
- `ProductAsset`: local filesystem paths, actively used for shared and override JSON.
- `Collection`: intended explicit collection model; dormant in the active ingestion path.
- `Category`, `Tag`, and association tables: present but not populated by active ingestion.
- `Service`: dormant hosting/domain-oriented model.

The Product-to-Variation relationship is active and populated. The Collection-to-Product relationship is not: collection rows and `collection_id` are not populated by the normal scan path.

## Scanner data currently omitted

Categories, tags, SEO metadata, exact collection type, publication state, explicit collection identity, source folder, and direct path columns are wholly or partially lost between resolved scanner rows and SQLite. Removed products and variations are not reconciled. These are Phase 1 concerns; this document makes no schema redesign decision.
