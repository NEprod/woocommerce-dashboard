# product_info.json Contract

For the current Shopify field/editor audit, save foundation and planned extensions,
see [M3A.9 editor contract](PRODUCT_EDITOR_CONTRACT.md). M3A.9.2 integrates signed,
admin/CSRF-protected source patches, backups and guarded atomic saving with scoped
rescans; unknown/legacy/empty values survive. Inheritance/SKU/marker contracts remain;
Single Variable source selection no longer merges its root twice. Vendor, handle
and explicit shopify_product_type now have guided/schema controls.

This is the definitive Phase 1 contract for authored catalogue metadata. The
filesystem remains authoritative. Collection metadata supplies shared defaults;
Simple and Variable Collection product folders may supply partial overrides.

The complete per-field matrix is machine-readable at
`app/resources/product_info/field_inventory.json` and is rendered inside the
authenticated `/metadata-reference` page. For every recognized field it records
the canonical key and aliases, type, units, required/optional state, collection
and override placement, inheritance, merge rules, parent and variation effects,
Woo-style output, SQLite destination, implementation status, example, and
warning/error behaviour.

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
types therefore retain their protected scanner behaviour.

## Inheritance and merge behaviour

### Shopify Simple publication (M3A.5)

The same resolved metadata now feeds reviewed draft create/update. An existing
verified Shopify identity is updated, never replaced by another Create. Missing
optional shipping/origin/vendor/SEO fields remain unmanaged remotely. An explicit
valid unscheduled sale below regular maps sale to customer price and regular to
compare-at; no sale restores regular and clears compare-at. Scheduled sale dates
block sale publication pending an approved scheduler policy.

Forward-compatible Advanced JSON `vendor` and `handle` are consumed when explicit:
vendor up to 255 characters; handle a lowercase hyphenated slug, at most 255, unique
in Shopify. They retain existing editor unknown-field review behaviour. Prefer
unique product overrides for handles, not one shared handle across many products.
SEO uses existing `meta_title` / `meta_description`. Product grams/mm retain their
units; dimension metafields are not parcel measurements. Customisation remains
definitions only. See [publishing contract](SHOPIFY_INTEGRATION.md#m3a5--complete-managed-simple-drafts-and-reviewed-updates).

### M3A.4 optional shipping and customisation

Existing JSON remains valid. Optional `country_of_origin` is an uppercase
two-letter code (for example `GB` or `CN`); `requires_shipping` is a boolean.
Both inherit from collection metadata with scalar product overrides. Code
validation checks format, not the full ISO country catalogue; the future publisher
must also validate Shopify's supported country enumeration. Optional deployment
setting `DEFAULT_COUNTRY_OF_ORIGIN` supplies a snapshot fallback only when authored
origin is absent. It defaults to unset, never GB automatically. Review manufacturing
origin before setting GB; imported finished products must override it accurately.
Missing shipping intent remains unknown.

`customisation` is a definition, not customer data or a variation driver:

```json
{
  "customisation": {
    "proof_policy": "optional",
    "fields": {
      "name": {"type": "text", "label": "Name", "required": true},
      "photo": {"type": "photo", "label": "Photograph"}
    }
  }
}
```

Proof policy is `none`, `required` or `optional`; absence makes no proof promise.
Field identifiers start with a lowercase letter and contain lowercase letters,
digits and underscores, at most 64 characters. At most 30 fields are allowed.
Types are `text`, `number`, `multiline`, `photo`, `choice`, `checkbox`. Enabled
resolved fields require a type and label; choice fields require unique `choices`.
Optional constraints are `max_length`, `minimum`, `maximum`; `required` and
`enabled` are booleans. No customer forms, uploads, proofs, conditional fields or
price adjustments are implemented.

Unlike other objects, customisation merges by stable field identifier: adding one
product field preserves inherited fields; a partial existing-field patch preserves
unspecified properties. `{"enabled": false}` disables an inherited field;
`{"remove": true}` (the only property) removes it from that product's resolved
definition. Empty objects do not clear inheritance. Explicit product proof policy
replaces inheritance. Variation-specific overrides are deferred; modifiers are
untouched. Edit through existing Advanced JSON, not a second definition file.
Runtime schemas compose the shared definition in `app/catalogue_fields.py`; editor
and resolved scanner validation reuse it. Existing affected-folder rescan controls
remain. Snapshots do not write sources or require new SQLite columns.

The scanner starts from the collection object and applies the product override:

- scalar values and objects are replaced by the override, except the explicit
  customisation stable-field merge above;
- lists are combined and deduplicated through a set, so membership is preserved
  but output ordering is not deterministic;
- title uses one combination/fallback rule: nonblank product override and shared
  title become `Override - Shared`; shared title alone becomes
  `Product folder - Shared`; neither becomes the product folder name. Missing,
  `null`, empty, and whitespace-only titles are absent. The collection folder
  display name is separate;
- partial overrides remain valid and inherited fields need not be repeated.

The editor patches reviewed changed properties into the same authored file, with
explicit removal paths and no empty pruning. This is distinct from scanner
shared/override resolution; unrepresented keys stay untouched.

## Field classification

| Key | Classification | Type / units | Placement | Resolution and output summary |
|---|---|---|---|---|
| `collection_type` | canonical and active | non-empty string | required collection; not normal override | Selects `Simple`, `Variable Collection`, or `Single Variable`; unknown values warn but retain the scanner behaviour of emitting no rows. Stored on Collection and Product. |
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
| `live` | canonical and active | boolean | either | Future publishing intent: `true` maps to Published and `false` to Draft when Woo sync is introduced; defaults to `true` when omitted. It is not current remote WooCommerce state. |
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
| `relationships` | canonical and active | object of ordered SKU arrays | product override; Single Variable root | Durable local cross-sell/upsell source. A product-specific block is complete and takes precedence over resolved legacy lists; SQLite rows are reconstructable projection only. |
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
  and update/shared-refresh orchestration behaviour.

The editor keeps invalid textarea content in place for correction. Templates load
into the form without saving. The normal override template is `{}` rather than a
copy of every possible field.

## Phase 2 source editors and resolved detail

The guided Collection Metadata Editor changes the authoritative collection
document and clearly reports that every inheriting product may be affected. The
guided Product Override Editor changes only the selected product's optional
partial document. Collection defaults are displayed beside override and
resolved values, but inherited defaults are never flattened into an override.
An override field is persisted only when explicitly enabled; disabling it
removes that authored key and reveals the inherited result.

`live` is presented as publishing intent, independently of local catalogue
lifecycle state. A product-level `live` key overrides the collection intent;
without that key the product inherits the collection value. The resolved value
is projected to `Product.published` for future synchronization, but no current
WooCommerce publication is claimed because Woo sync is not implemented.

Advanced JSON is an explicit expert mode for the same authored source, not a
third metadata layer. It validates through this module's existing schemas and
uses the same atomic save, backup, and catalogue-update pipeline as guided mode.
Unknown forward-compatible keys are preserved by guided saves. Formatting is
user-triggered and does not change scanner resolution semantics.

Product Detail is a resolved, read-only view. It does not edit SQLite or imply
that each product owns a complete JSON document. Product and variation images
belong to their source folders and projected image records. Catalogue-relative
source identity and stored final website URLs may be displayed, but image bytes
remain outside SQLite and `/app/instance`. `/output` is temporary user-managed
upload staging and is neither persisted as image identity nor used for preview.
Future Woo synchronization must use the already stored final URLs directly; the
current app does not upload or convert media.

## Known unchanged discrepancies

Milestone 9 documents but does not correct these scanner contracts:

1. Variation modifier `sale_price` is resolved but not emitted.
2. `shipping_class` is authored but the row field is blank.
3. Set-based list deduplication makes order nondeterministic.
4. Unknown collection types produce no rows.
5. Editor legacy names differ from the row builder’s upsell/cross-sell names.
6. Woo-style rows support only five attributes.
