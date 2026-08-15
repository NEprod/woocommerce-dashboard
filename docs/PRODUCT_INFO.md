# product_info.json Contract

This is the definitive Phase 1 contract for authored catalogue metadata. The
filesystem remains authoritative. Collection metadata supplies shared defaults;
Simple and Variable Collection product folders may supply partial overrides.

The complete per-field matrix is machine-readable at
`app/resources/product_info/field_inventory.json` and is rendered inside the
authenticated `/metadata-reference` page. For every recognized field it records
the canonical key and aliases, type, units, required/optional state, collection
and override placement, inheritance, merge rules, parent and variation effects,
Woo-style output, SQLite destination, implementation status, example, and
warning/error behavior.

## Files and schemas

- Collection schema: `app/resources/product_info/schemas/collection.schema.json`
- Partial override schema: `app/resources/product_info/schemas/override.schema.json`
- Fictional examples: `app/resources/product_info/examples/`
- Editor templates: `app/resources/product_info/templates/`

Both schemas use JSON Schema Draft 2020-12. Collection files require
`collection_type` and `sku_prefix`. Every other field is optional. The override
schema has no required fields, so `{}` remains a valid minimal override and an
override never needs to repeat shared metadata.

Schemas deliberately allow unknown top-level properties so the editor can warn
without destroying forward-compatible content. They are applied to editor saves,
not to scanner-wide loading. Existing catalogue files and unknown collection
types therefore retain their protected scanner behavior.

## Inheritance and merge behavior

The scanner starts from the collection object and applies the product override:

- scalar values and objects are replaced by the override;
- lists are combined and deduplicated through a set, so membership is preserved
  but output ordering is not deterministic;
- title uses its established combination/fallback behavior: product override and
  shared title become `Override - Shared`, with folder-name fallbacks;
- partial overrides remain valid and inherited fields need not be repeated.

The web editor deep-merges a submitted partial form into the existing file before
pruning empty values. This same-file editor merge is distinct from scanner
shared/override resolution and does not change it.

## Field classification

| Key | Classification | Type / units | Placement | Resolution and output summary |
|---|---|---|---|---|
| `collection_type` | canonical and active | non-empty string | required collection; not normal override | Selects `Simple`, `Variable Collection`, or `Single Variable`; unknown values warn but retain the scanner behavior of emitting no rows. Stored on Collection and Product. |
| `title` | canonical and active | string | either | Special shared/override/folder merge; emits `Name`; stored as Product title and in resolved rows. |
| `sku_prefix` | canonical and active | non-empty string | required collection; not normal override | Used only for new SKU allocation; identity-sensitive; stored on Collection. |
| `price` | canonical and active | number/numeric string; store currency | either | Scalar override; parent and base variation `Regular price`; normalized on Product/Variation. |
| `sale_price` | canonical and active | number/numeric string; store currency | either | Parent/base variation `Sale price`; modifier sale price discrepancy remains unchanged. |
| `sale_start_date` | canonical and active | `YYYY-MM-DD` | either | Emits sale-start date; normalized on Product/Variation. |
| `sale_end_date` | canonical and active | `YYYY-MM-DD` | either | Emits sale-end date; normalized on Product/Variation. |
| `weight` | canonical and active | number/numeric string; grams | either | Parent/base variation weight; modifiers may replace variation weight. |
| `dimensions` | canonical and active | `{length,width,height}`; millimetres | either | Parent/base variation dimensions; modifiers may replace variation dimensions. |
| `categories` | canonical and active | string array | either | Additive set-based merge; emits Categories; normalized through Category membership. |
| `tags` | canonical and active | string array | either | Additive set-based merge; emits Tags; normalized through Tag membership. |
| `live` | canonical and active | boolean | either | Maps to Published and Product publication status; defaults to live when omitted. |
| `short_description` | canonical and active | string | either | Emits Short description; stored on Product. |
| `description` | canonical and active | string | either | Emits Description; stored on Product. |
| `attributes` | canonical and active | object of non-empty scalar arrays | either | Creates Cartesian variation combinations and parent/variation attributes; Woo-style rows contain only five attribute slots. |
| `image_attributes` | canonical and active | string array | either | Selects Single Variable variation image folders; image URLs are stored through emitted rows. |
| `variation_modifiers` | canonical and active | keyed modifier object | either | Exact/most-specific matching for price, sale price, weight, and dimensions; modifier `sale_price` is currently not emitted. |
| `shipping_class` | supported but currently ignored | string | either | Authored value is retained in JSON, but the row builder emits blank Shipping class and SQLite therefore receives no value. |
| `grouped_ids` | editor-only | string array | either | Persisted by the editor but not consumed by the row builder. |
| `grouped_products` | Woo CSV-only | string array | either | Recognized for documentation, but JSON does not populate the current Grouped products row field. |
| `upsell_ids` | accepted alias | string array | either | Legacy/editor spelling related to `upsells`; accepted and warned, but deliberately not normalized or consumed by the current row builder. |
| `cross_sell_ids` | accepted alias | string array | either | Legacy/editor spelling related to `crosssells`; accepted and warned, but deliberately not normalized or consumed by the current row builder. |
| `upsells` | canonical and active | string array | either | Current row-builder spelling; emits Upsells and maps to `Product.upsell_ids`. |
| `crosssells` | canonical and active | string array | either | Current row-builder spelling; emits Cross-sells and maps to `Product.cross_sell_ids`. |
| `meta_title` | canonical and active | string | either | Emits Yoast-style title metadata; stored on Product. |
| `meta_description` | canonical and active | string | either | Emits Yoast-style description metadata; stored on Product. |

Generated `sku`, filesystem-derived images, internal `source_folder`, and the
temporary per-variation `modifiers` structure are not authored root fields.
Woo-style inventory, tax, visibility, purchase, download, external-product, and
position fields are currently fixed/default row-builder values rather than
`product_info.json` inputs. Their resolved values remain available in
`resolved_row_json` and applicable normalized SQLite columns.

## Attributes and variation modifiers

`attributes` maps each name to a non-empty array. The scanner takes the Cartesian
product. More than five names are accepted with an editor warning because the
protected Woo-style row builder exposes only five slots.

Modifier keys use `Attribute=Value` segments joined with `|`, for example
`Finish=Gloss|Size=Large`. Values may contain `price`, `sale_price`, `weight`, and
`dimensions`. Exact matches win; otherwise the most-specific matching partial key
is used. Unsafe arrays/scalars in place of these objects block editor saves.

## Editor validation and side effects

Validation happens before the catalogue-operation lock, backup, write, marker,
or scan:

- malformed JSON, non-object roots, missing collection requirements, clearly
  wrong types, and unsafe attribute/modifier structures are blocking errors;
- aliases, unknown fields, ignored/editor-only fields, unknown collection types,
  more than five attributes, nondeterministic override list ordering, and known
  modifier discrepancies are warnings;
- invalid saves preserve the original file, create no backup or `.update`, start
  no operation/scan, and return field paths plus submitted content where practical;
- valid saves retain the existing backup, atomic replacement, override `.update`,
  and update/shared-refresh orchestration behavior.

The editor keeps invalid textarea content in place for correction. Templates load
into the form without saving. The normal override template is `{}` rather than a
copy of every possible field.

## Known unchanged discrepancies

Milestone 9 documents but does not correct these scanner contracts:

1. Variation modifier `sale_price` is resolved but not emitted.
2. `shipping_class` is authored but the row field is blank.
3. Set-based list deduplication makes order nondeterministic.
4. Unknown collection types produce no rows.
5. Editor legacy names differ from the row builder’s upsell/cross-sell names.
6. Woo-style rows support only five attributes.
