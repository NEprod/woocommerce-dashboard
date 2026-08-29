# Scanner Contract

The scanner is established behaviour. It must not be modified incidentally while improving the database, UI, Woo integration, or deployment system.

## Catalogue hierarchy

```text
Collection
└── Product
    └── Variations
```

Supported collection types are exact strings:

- `Simple`
- `Variable Collection`
- `Single Variable`

Each collection has collection-level `product_info.json` containing shared/default metadata and required `collection_type` and `sku_prefix`. Simple and Variable Collection product folders may contain their own `product_info.json`.

## Metadata resolution

Product metadata starts with the shared collection object. Override scalar/object values replace shared values. When both shared and override values are lists, the current implementation combines and deduplicates them. Title has one authoritative shared/override/folder resolver. Missing, `null`, empty, and whitespace-only title values are absent. A valid product title plus a valid shared product title resolves to `Product title - Shared title`; shared title alone resolves to `Product folder name - Shared title`; neither resolves to the product folder name. The collection folder basename remains the separate collection display name.

Append ingestion assigns every parent through its portable catalogue-relative
source provenance. The source path selects the collection folder and shared
`product_info.json`; collection assignment does not use display titles, database
row order, or a previously processed collection. Full and Append therefore
project the same collection relationship for the same source product.

A shared JSON editor save now orchestrates an explicit exhaustive refresh of its
own collection with SKU reuse enabled. This is operation-level selection, not a
change to the low-level append, individual `.update`, full-scan, inheritance, or
row-emission contracts characterized below.

`attributes` define variation dimensions. The scanner creates the Cartesian product of attribute values. `variation_modifiers` select effective price, sale price, weight, and dimensions by exact or most-specific partial attribute key. Image attributes select folders for Single Variable variation images.

For a `Single Variable` collection, `parent/` is a reserved semantic directory
directly under the collection root. Recognition is case-insensitive, so existing
`Parent/`, `PARENT/`, and mixed-case spellings have the same meaning while the
real on-disk spelling remains in portable source references. More than one
case-variant in the same collection is an ambiguous catalogue error and is not
merged. The reserved-name check occurs before image-attribute traversal. It is
a sibling of the first configured
`image_attributes` directory level and is never an attribute value. With
`image_attributes: ["Style", "Size"]`, variation images are resolved through
`Style/Size/`, in that configured order; files directly inside `parent/` are
owned only by the parent product. Supported image names are ordered
case-insensitively by filename (with the original filename as the stable
tie-breaker). The first successfully processed parent file is primary and the
remaining files are gallery images.

## Local state

- `.scanned` records processed state, parent SKU, title, used images, timestamp, and variation SKU mappings.
- `.scanned.pending` is a recovery-only envelope containing the intended unchanged `.scanned` payload, operation identifier, format version, and pending state.
- `.update` forces reprocessing without globally forcing a collection.
- `sku_index.json` stores counters for new parent and variation SKU allocation.

## Modes

- **append:** skip folders already carrying `.scanned`, unless `.update` exists; allocate new SKUs for processed folders.
- **update:** select by the same marker rules; reuse parent and matching variation SKUs from `.scanned`.
- **full:** process regardless of `.scanned`; do not reuse `.scanned` SKUs; reset the first collection counter as currently implemented.

When a product is selected for update, the parent and every currently resolved variation are processed together.

## Preserved discrepancy

Saving shared JSON writes `.update` beside the collection JSON. This selects Single Variable roots correctly, but Simple and Variable Collection scans check `.update` in product child folders. Phase 0 characterises and documents this discrepancy without changing it.

## Filesystem and database ordering

The protected Phase 0 ordering was:

1. SKU allocation updates `sku_index.json` when a new parent or variation identity is needed.
2. Source images are processed into the configured output folder.
3. `.scanned` is written with the parent SKU, resolved title, source image filenames, scan timestamp, and variation mappings where applicable.
4. Writing `.scanned` removes the product-level `.update` marker when present.
5. After all selected collections have been scanned, the accumulated Woo-style rows are passed to SQLite ingestion.

Milestone 6 changes only durability and finalization ordering:

1. `sku_index.json` counter writes use a same-directory temporary file, file flush, `fsync`, and atomic `os.replace()`.
2. Images are processed with existing behaviour.
3. The unchanged intended `.scanned` payload is atomically staged inside `.scanned.pending`; an existing valid `.scanned` and `.update` remain untouched.
4. SQLite ingests each complete parent transaction and its operation item.
5. For a committed parent, `.scanned.pending` is marked for finalization, its marker payload atomically replaces `.scanned`, `.update` is removed, history is marked finalized, and the pending envelope is removed.

If SQLite does not commit, the pending envelope and previous `.scanned` remain, and `.update` is retained or recreated. If marker replacement or `.update` removal fails after commit, the pending envelope remains for the next operation to finalize without rescanning that committed product. At operation start, committed pending intents are finalized before normal selection; unresolved database intents are selected for retry and reuse their parent and matching variation SKUs.

For variable products, variation counter updates still occur while variations are built. SQLite and filesystem state still cannot share one transaction. Counters may advance and processed images may remain after a failure; recovery deliberately reuses the pending identity instead of attempting destructive rollback.

The top-level `.scanned` identity and matching rules remain unchanged. Single
Variable variation entries may add their ordered `images_used` source names;
older entries without that optional member remain valid. Clean
append/update/full selection and SKU behaviour remain unchanged; only a recovery
retry may reuse `.scanned.pending` so an interrupted product does not receive a
second identity.

## Characterised discrepancies awaiting separate decisions

The following behaviour is deliberately protected as the current contract and is not corrected as part of database parity:

- A variation modifier can resolve `sale_price` internally, but the variation row builder retains the base sale price instead of emitting the modifier value.
- Authored `shipping_class` reaches the row builder input, but the Woo row currently emits an empty Shipping class.
- Shared and override lists are deduplicated with a set, so membership is preserved but ordering is not deterministic.
- An unknown `collection_type` passes the current minimal validation and produces no rows because no scanner branch matches it.
- The editor uses `upsell_ids` and `cross_sell_ids`, while the row builder consumes `upsells` and `crosssells`.
- Woo parent and variation rows expose at most five attribute slots.
- `.scanned.images_used` records ordered parent source image filenames; emitted
  row URLs can refer to converted output filenames. Single Variable variation
  entries may also include `images_used`; older markers without that optional
  member remain valid and retain their SKU matching semantics.

These discrepancies require explicit future scanner-contract decisions. Phase 1 persists only values that reach the approved emitted projection.

## SQLite projection boundary

Milestone 4 does not change scanner selection, resolution, row building, markers, or SKU behaviour. Ingestion stores each emitted parent and variation row losslessly as JSON, so blank and discrepant emitted values remain visible rather than being reconstructed from pre-row metadata. Exact collection type and portable source/JSON provenance are derived from the selected product's `.scanned` identity and its physical location beneath the configured catalogue root; this adds database context without adding keys to scanner rows.

Milestone 5 changes only the SQLite ingestion boundary and history reporting. It does not change scanner selection, resolution, emitted rows, SKU generation/reuse, JSON inheritance, append/update/full behaviour, or marker/index writes.

Milestone 6 adds atomic marker/index replacement and recoverable finalization. It does not change resolved metadata, emitted row values, JSON inheritance, variation matching rules, or intentional clean full-scan regeneration.

Milestone 8 adds reconstruction-only orchestration around the same scanner: force
complete selection, reuse identities, disable counter reset, preserve valid
markers, and supplement safe matches from the existing database projection.
Default append, update, shared-refresh, and full calls retain their characterized
arguments and behaviour. Intentional full regeneration remains separate and
requires explicit UI/API confirmation.

Milestone 9 adds JSON Schema resources and editor-side validation only. It does
not add strict scanner-wide schema enforcement, change merge/resolution behaviour,
normalize aliases, block unknown collection types in the scanner, or correct any
characterized row-builder discrepancy. The definitive authored-field inventory is
in [product_info.json Contract](PRODUCT_INFO.md).
