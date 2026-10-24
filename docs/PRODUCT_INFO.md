# product_info.json Contract

## Shipping editor correction

Shipping-only child `variation_modifiers` patches extend inherited modifier entries
without replacing parent commercial rules. Historical commercial override objects
retain replacement behaviour. Root/modifier shipping use the same schema. Explicit
null package choices survive Intake serialization. Display fallback never writes
packed axes. Retired verification/protection keys stay compatible, not new requirements.
See [current rules and examples](PRODUCT_SHIPPING_EDITOR_REPAIR.md).

## M3A.9.5.6 — dimensional authority correction

`dimensions` is the original finished-product size in millimetres, with established
dimension/price/weight modifiers unchanged. It never substitutes for native shipping.
Existing `finished_dimensions` remains compatible as an optional explicit-unit form.
`packaging.packed_dimensions` is independent `{length,width,height,unit}` (mm/cm/in);
existing `packed_dimensions_cm` stays valid. Unit-explicit dimensions replace as one
object; other shipping patches preserve sparse false/null and use existing modifier
expressions. Never author both size representations in one layer. Copy Preview is
one-time, never a saved link, and never marks a parcel measured. No JSON is rewritten
on deployment. See [examples, precedence and safety](PACKED_DIMENSIONS.md).

## M3A.9.5.4 — optional authored packaging

`finished_dimensions` explicitly records actual finished size (length/width/height,
unit mm/cm/in, optional local display label). Whole-object inheritance keeps units
together; an exact/most-specific variation object can override it. Legacy `dimensions`
retains its historical millimetre interpretation; nothing is automatically rewritten.
`packaging` stores independent measured packed_dimensions_cm, optional shipping weight_g,
method (flat/rolled/rigid/protected), orientation/classification, fragility, rollability,
protection/clearance and optional selected local UUID/reason/assignment_kind. Nested
collection → sparse product → matching variation modifier merge preserves false/null.
Missing selected_package_id inherits; null explicitly unassigns; UUID explicitly selects.
No selection is valid. Recommendations are advisory, never automatically accepted.
Existing JSON remains valid; deployment/time passing never rewrites authored files.
The [packaging guide](PACKAGING_AND_SHIPPING.md) defines fields and safety gates.

## M3A.9.5 — explicit public storefront intent

`customisation.storefront_enabled` is an optional boolean within the existing
customisation definition. It follows pinned-template → shared JSON → sparse product
override precedence; explicit false overrides true, absence inherits (default false).
Adding fields still merges by stable ID; disabled fields and removal tombstones are
omitted from public projection. No second authored definition file is introduced.

```json
{"customisation":{"storefront_enabled":true,"fields":{"recipient_name":{
  "type":"text","label":"Recipient name","required":true,"max_length":30}}}}
```

Only supported, enabled public fields/choices/constraints/defaults are exported.
Unknown keys, proof policy, template IDs/revisions, private notes and source paths
are excluded. Customer help/labels are intentionally public: never enter secrets there.
Enabled photo/file fields block storefront configuration publication until a complete
secure upload flow exists, including optional uploads; exclude or disable them for
the pilot. Disabling storefront intent publishes a reviewed disabled configuration,
not a deletion. Saving JSON neither publishes nor changes Shopify/theme activation.
Regular product publishing without opt-in retains existing local-only behaviour.

## M3A.9.4.2 — title contributions are separate authored scopes

For a Simple or Variable Collection child:

`(product.title OR product folder) + " | " + (shared.title OR collection folder)`

Exactly one space surrounds the vertical bar. For collection folder
`16-Bit Pixel Art Cards` and child `Fathers Day Level Complete`:

| Product JSON title | Shared JSON title | Full canonical title |
| --- | --- | --- |
| Level Complete | Retro Gaming Cards | Level Complete \| Retro Gaming Cards |
| Level Complete | Missing | Level Complete \| 16-Bit Pixel Art Cards |
| Missing | Retro Gaming Cards | Fathers Day Level Complete \| Retro Gaming Cards |
| Missing | Missing | Fathers Day Level Complete \| 16-Bit Pixel Art Cards |

Absent, null, empty and whitespace-only titles fall back independently on read.
The existing authored schema permits strings, including empty strings, but rejects
new null/non-string title values; legacy scalar-to-string scan compatibility remains
unchanged. Invalid legacy authoring must be deliberately corrected, never silently
normalised by deployment. An actually authored complete-looking title is still an
authored contribution; suffixes are not guessed or stripped automatically.

Single Variable has one root JSON owner and uses root-folder
` | ` authored-root-title when both differ (M3A.9.5); identical components appear once,
and no root title means the root folder alone. A root-only Simple intake preview
uses its authored root title or root folder, not a nonexistent parent. Scanner
layout classification is unchanged; scoped child layouts use the two-part rule above.

Canonical titles are never truncated or written back into JSON. CSS may ellipsize
display; Discord has independent transport/display limits. Shopify Review retains
its existing 255-character title validation and blocks oversized titles rather than
silently shortening them. Shared title edits affect all eligible child projections,
not child source files or identities; each remote title change still needs review.

## M3A.9.4.1 editor corrections — schema unchanged

Required/Enabled are independent authored booleans. `enabled:false,required:true`
is preserved; the disabled field has no active local preview input. Once enabled,
its Required rule applies again. Label changes retain stable field ID. The designer
loads effective values but writes only changed properties into a child override.
An unchanged Apply is a no-op. Cancel never writes a field or marks an otherwise
saved outer draft dirty. Save Locally remains the only JSON persistence action.

Defaults are now entered through type-appropriate controls, not typed JSON. False,
empty text/list and legacy null are retained distinctly from unspecified/inherited.
Dropdown/radio defaults use IDs; group defaults use selected checkboxes; date and
colour controls retain existing representations. Options can be added, labelled,
removed and reordered without changing correct stable IDs or opaque properties.
Existing object option IDs are read-only. Legacy string options retain their original
text identity; intentional replacement uses Add/Remove and reviewed default selection.
Removing a selected option blocks Apply until its default is resolved explicitly.

Native boolean controls and responsive modal groups use shared theme tokens.
File categories, extensions, size/count remain future-upload definitions only.
Incompatible inherited type changes require editing the owner or a new field plus
explicit exclusion, not silently erasing inherited constraints. No schema/migration,
template storage, scanner, variant, source layout, price or Shopify payload change.

## M3A.9.4 — local customer customisations and reusable templates

`customisation` remains the only authored root for customer input **definitions**.
It is separate from variation-driving `attributes`, registry informational attributes
and private production notes. Nothing is added to JSON merely by opening the editor.
Legacy unversioned definitions and type names remain supported. New fields include:

```json
{"customisation":{"schema_version":2,"proof_policy":"optional","fields":{
  "dog_name":{"type":"text","label":"Dog's Name","required":true,
    "max_length":30,"placeholder":"Your dog's name","help_text":"Check spelling carefully.","order":0},
  "memorial_date":{"type":"date","label":"Memorial Date","required":false,"order":1}
}}}
```

Types: `text`, `multiline` (alias `textarea`), `number`, `choice` (alias `dropdown`),
`radio`, `checkbox`, `checkbox_group`, `date`, `colour`, `photo` (alias `file_upload`).
IDs match `[a-z][a-z0-9_]{0,63}` and never change on label edits. Ordering is ascending
`order` (default 0), then stable ID; the designer's Move Up/Down writes deterministic
order patches without changing identity. Required/enabled are booleans. Enabled
resolved fields need type and a nonblank label. Disabled fields remain in definitions.

Flat type-specific constraints: text `min_length/max_length`; number `minimum/maximum`
and `whole_number`; date `min_date/max_date` in exact valid YYYY-MM-DD form; checkbox
group `min_selections/max_selections`. Contradictory or wrong-type constraints fail.
Choice/radio/group require `choices`: legacy unique strings remain valid; prefer
`[{"id":"script","label":"Script"}]` so labels can change without changing answers.
Colour optionally uses choices such as `{"id":"red","label":"Red","value":"#ff0000"}`;
otherwise six-digit hex input is allowed. Defaults must have the correct type,
match options and satisfy constraints. File defaults are forbidden.

File definitions accept `allowed_media_categories` (`image`/`document`), dot-prefixed
lowercase `allowed_extensions`, positive `max_file_size_bytes` and `max_files`.
These are intended future requirements, not an operational upload security system.
No arbitrary field-count limit is imposed; the existing 1 MiB JSON definition/source
bound controls resource use. Unknown keys round-trip as opaque metadata in Advanced
JSON; they are not executable validation, paid rules or public output.

Inheritance uses `merge_product_json`/`merge_customisation`. Template fields come
first, shared fields second and distinct product fields last. Every local field
has its complete type and label; inherited IDs cannot be redefined, patched,
disabled or removed. Missing keys and empty fields objects never delete inherited values. Proof policy overrides
as a scalar (`none/required/optional`); variation-specific customisations remain deferred.

Reusable templates live in the existing persistent SQLite database. Assign a pinned
revision instead of copying the definition into every product:

```json
{"customisation":{"template":{"id":"t_example_stable_id","revision":1},
  "fields":{"gift_note":{"type":"text","label":"Gift note","max_length":200}}}}
```

Only Shared Collection/owning JSON may reference a reusable template. Distinct
shared and product definitions append without changing inherited fields. Product
template references, `template:null`, field `remove` instructions and incomplete
definitions are validation errors identifying the invalid key. No alternate
authoring format, fallback or automatic migration exists. Resolved data is
self-contained. Authored owner JSON retains the pinned reference. Editing/renaming a
template creates an immutable new revision; existing assignments keep their revision.
Archive blocks new assignments but old references continue resolving. No deletion
endpoint exists because filesystem dependencies cannot be proven absent from a
partial index. Missing pinned revisions block saves/scans rather than dropping fields.
Back up the database **together with** the authored catalogue to retain dependencies.

The editor offers field configuration, template CRUD/duplicate/archive, ordering,
protected inheritance/local additions and a local customer form preview. The preview stores
no answers and provides no file input/upload. Local saves retain existing admin/CSRF,
signed source/dependency reviews, shared acknowledgement, atomic backup/write, no-op
and targeted scanner contracts. Template library saves do not scan products. Customisation
changes do not affect Shopify-managed fingerprints or authorise publishing/sales.

Migration `0015_customisation_templates` adds only the immutable revision table;
no product/variant/identity rows or source files are migrated. Downgrade refuses to
drop a nonempty library; restoring older DB backups may orphan newer authored references.

The older M3A.4 field-count/Advanced-only descriptions below are historical and are
superseded by this extension. Storefront rendering, conditional fields, actual file
uploads, customer answers, proofs, paid extras and order capture remain deferred.

## M3A.9.3.2 price meanings (no authored format change)

`price` is regular price; `sale_price` is configured promotional price, including
after expiry. Dates select the current effective price through the existing exact
Decimal Europe/London resolver. SQLite regular/sale/date columns and lossless emitted
rows retain configuration; sale presence alone never proves an active discount.
Products, details, editor summaries and variant APIs now compute current presentation
at read time using that same resolver and existing modifier selection, not SQL
sale-or-regular coalescing. Before start/at-or-after end returns regular/no compare-at;
active returns sale/regular compare-at. Authored JSON is never rewritten as time passes.

Original precision, offsets and inclusive date-only end semantics remain. Source
files supply inherited metadata; raw indexed dates are a display fallback only when
authored sources are unavailable. Invalid pricing is shown as unavailable/attention,
not an invented selling price. This local intention does not establish Shopify price:
independent readback, complete comparison and retained receipts remain authoritative.

## M3A.9.3 authored intent and effective defaults

Existing files, three physical layouts, sparse merging, SKU/modifier/image/marker
contracts remain unchanged. New optional keys are `shopify_status`
(`DRAFT`, `ACTIVE`, `ARCHIVED`) and `shopify_channels` (discovered handle → boolean).
Status and legacy live must agree when both occur in one authored layer. A child's
legacy live overrides an inherited new status; a child's status overrides inherited
live. A channel object is replaced, not merged; missing inherits, `{}` unmanages,
explicit false requests reviewed unpublication. No remote ID belongs in this JSON.

Effective precedence is child → shared → nonblank deployment default → fallback:

| Authored key | Deployment setting | Unconfigured fallback |
| --- | --- | --- |
| country_of_origin | DEFAULT_COUNTRY_OF_ORIGIN | Unknown/unmanaged; no invented GB |
| requires_shipping | DEFAULT_REQUIRES_SHIPPING (`true`/`false`) | Unknown/unmanaged |
| manage_stock | DEFAULT_MANAGE_STOCK (`true`/`false`) | false |
| backorders | DEFAULT_BACKORDERS (`yes`/`no`/`notify`) | yes when untracked; no when tracked (existing behaviour) |

Explicit JSON booleans and `backorders:no` win, including DENY on an untracked
variant. Tracking and selling policy are distinct; Shopify availability also depends
on channel/status rules. No stock quantity is fabricated. Null is not inheritance
for these fields: remove the authored key through the editor to inherit. GB is
appropriate only when the actual origin qualifies, not simply because TLC ships it.
Blank new environment settings preserve existing installations. The editor displays
effective value/source without saving defaults merely because it was opened.

Existing `price`, `sale_price`, `sale_start_date`, `sale_end_date` now share one
date-aware Shopify projection: before start/at-or-after end → regular price and no
compare-at; active → sale price and regular compare-at. Prices use exact Decimal,
at most two decimals; sale must be strictly below each affected regular amount.
Existing modifier selection determines per-variant absolute regular/sale amounts.
Legacy scanner CSV remains unchanged; the publisher explicitly reads a selected
sale modifier that the old CSV did not emit. It still checks indexed regular price,
measurements, identities and existing scanner sale projection before writing.

Date-only values use Europe/London; end date includes the whole day. Date-times
are exclusive boundaries; provide an explicit offset during a DST ambiguity.
No end means ongoing. Dates/amounts for a future sale remain local and do not
invalidate current-price equality. They DO invalidate separate future-write
authorisation. Saving JSON never creates that authorisation or publishes prices.
See SHOPIFY_INTEGRATION for reviewed execution/recovery and limitations.

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
- title uses the M3A.9.4.2 scoped contribution/folder rule above. Missing,
  `null`, empty and whitespace-only titles are absent on read; collection display
  identity is separate from its authored title contribution;
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
