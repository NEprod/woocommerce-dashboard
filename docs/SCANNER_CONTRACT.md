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
