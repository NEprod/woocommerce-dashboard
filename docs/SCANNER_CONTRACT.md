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

Product metadata starts with the shared collection object. Override scalar/object values replace shared values. When both shared and override values are lists, the current implementation combines and deduplicates them. Title has dedicated shared/override/folder fallback rules.

`attributes` define variation dimensions. The scanner creates the Cartesian product of attribute values. `variation_modifiers` select effective price, sale price, weight, and dimensions by exact or most-specific partial attribute key. Image attributes select folders for Single Variable variation images.

## Local state

- `.scanned` records processed state, parent SKU, title, used images, timestamp, and variation SKU mappings.
- `.update` forces reprocessing without globally forcing a collection.
- `sku_index.json` stores counters for new parent and variation SKU allocation.

## Modes

- **append:** skip folders already carrying `.scanned`, unless `.update` exists; allocate new SKUs for processed folders.
- **update:** select by the same marker rules; reuse parent and matching variation SKUs from `.scanned`.
- **full:** process regardless of `.scanned`; do not reuse `.scanned` SKUs; reset the first collection counter as currently implemented.

When a product is selected for update, the parent and every currently resolved variation are processed together.

## Preserved discrepancy

Saving shared JSON writes `.update` beside the collection JSON. This selects Single Variable roots correctly, but Simple and Variable Collection scans check `.update` in product child folders. Phase 0 characterises and documents this discrepancy without changing it.

## Filesystem side effects and current ordering

The scanner is not a read-only resolver. For each selected product, the current Phase 0 implementation performs these filesystem effects before database ingestion begins:

1. SKU allocation updates `sku_index.json` when a new parent or variation identity is needed.
2. Source images are processed into the configured output folder.
3. `.scanned` is written with the parent SKU, resolved title, source image filenames, scan timestamp, and variation mappings where applicable.
4. Writing `.scanned` removes the product-level `.update` marker when present.
5. After all selected collections have been scanned, the accumulated Woo-style rows are passed to SQLite ingestion.

For variable products, variation counter updates occur while variations are being built, between image processing and the final `.scanned` write. `sku_index.json` and `.scanned` are currently written directly rather than by atomic replacement. SQLite and filesystem state do not share a transaction: filesystem changes can survive a database failure, and the database currently commits parents before variations.

Phase 1 may make marker/index writes atomic and add recoverable orchestration, but must preserve marker payloads, SKU allocation, SKU reuse, row resolution, and the distinction between filesystem and SQLite consistency.

## Characterised discrepancies awaiting separate decisions

The following behavior is deliberately protected as the current contract and is not corrected as part of database parity:

- A variation modifier can resolve `sale_price` internally, but the variation row builder retains the base sale price instead of emitting the modifier value.
- Authored `shipping_class` reaches the row builder input, but the Woo row currently emits an empty Shipping class.
- Shared and override lists are deduplicated with a set, so membership is preserved but ordering is not deterministic.
- An unknown `collection_type` passes the current minimal validation and produces no rows because no scanner branch matches it.
- The editor uses `upsell_ids` and `cross_sell_ids`, while the row builder consumes `upsells` and `crosssells`.
- Woo parent and variation rows expose at most five attribute slots.
- `.scanned.images_used` records source image filenames; emitted row URLs can refer to converted output filenames.

These discrepancies require explicit future scanner-contract decisions. Phase 1 persists only values that reach the approved emitted projection.
