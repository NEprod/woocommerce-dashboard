# M3A.9 Product Creator & Editor contract

## M3A.9.3.4 — one review entry after local editing

Product Override and Shared Collection Metadata retain Publishing status, discovered
channel choices, explicit false/unmanaged behaviour, sparse inheritance and validation.
Only the redundant Publishing-section review link was removed. Save locally, wait
for the existing targeted scan, then review Shopify changes from Products or Product
Details. A shared save authorises no child publication; each child remains independently
reviewed. Channel discovery is read-only. There was no button-specific JS handler to
remove; main signed/CSRF review, recovery and publication services remain unchanged.
Container startup now queues Shopify Products reads, never local catalogue scanning.

## M3A.9.3.3 — one publishing review; local saving remains local

Review & Sync now includes eligible discovered status/channel before/after changes
with existing metadata and variants. It binds the store, retained product/nonce,
actual publication/channel identities and memberships, source and configuration.
Changed selections or memberships require a fresh signed confirmation. Unmanaged
does not mean unpublish; Active does not mean Online Store-published. Blocked
availability is displayed and deferred, never hidden behind metadata success.

Save Locally retains atomic patch/backup, sparse inheritance and affected .update
scanning. After committed projection, the existing worker receives bounded read-only
comparison work. No-op saves do not scan, private-only metadata does not imply Update
Available, and saving never authorises Shopify publication or scheduled sales.
Review Sale remains a separate signed future-write approval. Editor conflict/unsaved
and scanner guards remain intact. Legacy instructions below requiring a separate
status/channel confirmation are superseded by this unified workflow.

Status: M3A.9.3 publication/defaults/scheduled sales implemented, 2026-10-15. TLCDashboard is Shopify-first and private to
Tender Loving Creations. This is the staged editor implementation contract, not a
claim that complete creator forms or storefront customisation exist. Historical
design-only sections below describe their checkpoint; this section supersedes the
earlier scheduled-sale/publication deferrals only.

## M3A.9.3.2 — local sale lifecycle presentation

Pricing now separates saved regular amount, configured promotional amount, current
effective local price, and separately verified remote evidence. The compact saved
local summary shows No Sale / Scheduled / Active / Ended and regular/configured
amounts; an Ended sale retains authored JSON. Approval/execution status is separate:
an authorised/paused/failed/recovery row is not the local date lifecycle or proof of
remote price. Existing guided sale inputs remain authored values, not a live price.

The shared exact-decimal project()/modifier selector drives browser, detail, variant
JSON and editor summaries; no scan-time effective-price column is introduced. A page
revalidates bounded local data at its next known boundary, with visibility/focus
catch-up and no Shopify requests. It does not authorise or run a transition. Saving
retains the existing local save/scan/review sequence and signed authority. A failed
local revalidation is visible and does not invent remote equality.

Native evidence freshness expiry is labelled historical matching evidence, not Needs
Attention solely due to age. Missing/blocked/uncertain/different evidence remains
protected. Live acceptance and historical Unraid task identification are separate
from the fictional scan/timeline regression; see CURRENT_STATE.

## M3A.9.3.1 — saved-source sale review UX

The Pricing section uses one combined UK `DD/MM/YYYY HH:mm` control per boundary,
Europe/London GMT/BST display, a live device clock, Set to Now and Clear authored
value. Ambiguous autumn times require an explicit GMT/BST selection; nonexistent
spring times are rejected. Unchanged controls preserve original strings, offsets,
seconds and inclusive date-only end semantics. Editing a boundary deliberately
creates an exact UTC date-time; end is exclusive. Clear removes the authored key,
so an inherited boundary can apply; it does not cancel an existing approval.

Review Sale opens a Bootstrap modal over the same editor, preserving its mode,
values and scroll. It is disabled for unsaved edits (conservatively all editor
changes), saves in flight, unavailable/stale/invalid source and incomplete scans.
Save Locally → completed projection → Review Sale → explicit Authorise is required.
The readiness/Preview/Confirm endpoint checks the existing signed editor revision,
submitted draft equality with saved JSON, effective validation, active operation,
current SKU marker/update/pending state, source/marker timestamps and relevant
failed scan receipts before any Shopify read. The signed 15-minute sale review is
wrapped with the saved editor binding and rechecked on confirmation. Old full-page
review POSTs cannot bypass this handoff; its schedule status/pause controls remain.

Opening/closing a modal cannot approve, pause or replay a sale. An in-flight explicit
authorisation is not aborted by closing; unknown responses require checking retained
schedule status, never automatic resubmission. The existing authorisation service,
task/receipts/recovery, price calculations, inheritance, scanner and Discord are
unchanged. No migration, scope, API version or deployment setting is added.

## M3A.9.3 — publication, defaults and separately approved sales

- Publishing section: `shopify_status` DRAFT/ACTIVE/ARCHIVED; legacy `live` still
  resolves false → DRAFT, true → ACTIVE when status is absent. Same-layer conflicts
  fail validation. Editing status keeps legacy live consistent; removing a child
  intent restores the shared intent. Local catalogue active/inactive is separate.
- `shopify_channels` is an optional object of discovered channel handles → explicit
  booleans, NOT Shopify IDs. Missing object inherits; a child object replaces the
  inherited object, including `{}` to leave channels unmanaged. Unknown authored
  handles survive editing but block publication until resolved. Actual publications
  are discovered explicitly; page rendering makes no remote request. Publication
  review shows actual/desired membership and permission blockers. Shared edits
  show their existing impact review and never authorise child publication.
- Product > shared > nonblank environment > documented fallback supplies effective
  origin, requires-shipping, tracking and backorders. Explicit false/no survives.
  No defaults are backfilled into JSON. See PRODUCT_INFO for exact settings.
- Sale date/date-time controls retain local definitions. The shared exact-decimal
  effective price resolver applies existing variation-modifier selection. Future
  dates/amounts do not change current Shopify-managed fingerprints until effective.
  JSON Save/Validate/Scan does **not** authorise any Shopify mutation or schedule.
- Product → Shopify Actions → Publication & sale controls exposes separate signed
  status/channel confirmation and per-product future-price authorisation. Both
  require admin + CSRF + unexpired (15-minute) review and current source/identities.
  Metadata confirmation still creates Drafts or preserves existing availability.
- Sale approval binds store, ProductVariant/InventoryItem IDs, exact prices, source,
  effective defaults, taxonomy, image hashes and dates. Revised inputs hold execution
  for review, not automatic re-authorisation. No bulk/shared-child approval.
- Migration `0014_shopify_sales` retains current authority per store/source; original
  approvals and deterministic transition receipts use CatalogueOperation. The existing
  worker gets a five-minute sale task, not another scheduling engine. Future-only
  approvals stay local; no due authority means no Shopify API request.

UK time: date-only start is midnight Europe/London; date-only end includes that
entire day (next midnight exclusive). Offset-less ambiguous/nonexistent DST times
are rejected; explicit offset resolves them. Ongoing sale/no end is supported.
Pause stops future execution, **not** a remote active price. To restore early, set a
past end, save/scan, and review/authorise the displayed regular-price restoration.
Uncertain writes require read-only recovery and cannot be paused away or replayed.

Limits: max 32 sale variants, three due parents per task run, shared capacity/lease
and interactive-priority controls. Normal latency is up to the task interval plus
capacity/backlog/downtime; not a hard real-time promise. No quantities, locations,
fulfilment, bulk authorisation, customisation forms or menu publication are added.

## M3A.9.2 — implemented core editor

Normal Products tools/detail open the existing metadata workspace. General, Pricing,
Variations/informational attributes, Images, Organisation, Shipping, SEO and Advanced
reuse current components/tokens and mobile save controls. Shared/override links show
the source, inherited/effective values and affected children. Single Variable has one
JSON owner: a reproduced fallback previously merged its root as shared AND override,
doubling its title instead of matching scanner folder/shared-title resolution. Source
selection now matches the scanner; no calculations, source files or identities change.

Guided fields: title, full/short descriptions, publishing intent, regular/unscheduled
sale price, grams/mm dimensions, origin, requires-shipping, tracking/backorders policy
(not quantities/locations), vendor, explicit Shopify product type, categories/tags,
storefront collections, primary TLC key, attributes/variation drivers/modifiers/image
axes, native SEO title/description and handle. Vendor/handle/type now have schema
validation. Existing layout/prefix are visible but cannot change in ordinary edits.
Sale dates, customisation definitions, relationships and unknown/private/legacy keys
remain in Advanced; no scheduling or customer-field designer is claimed.

Images show original relative source, authenticated preview, featured/gallery role,
position/alt, missing source, variant associations and cached retained Shopify IDs/CDN
URLs. Gallery/order/uploads/replacement are read-only; image-axis controls keep current
validation. Six indexed effective variants are previewed, with detail access to the
remainder; these are scanner results, not an unsaved recalculation.

Live saving now uses metadata_edit through metadata_save:

- Admin and CSRF required. Four-hour signed reviews bind product/layer, exact source,
  existence, shared/child dependencies and registry digest. Ownership/root checks reject
  traversal, escaped symlinks, wrong-owner and stale/altered submissions.
- Browser sends changed fields plus explicit object-key removals, not full replacement.
  Unchanged numeric representations, modifiers/images and unknown/private/legacy/empty
  values remain. Advanced uses the same patch protocol; template proposals only fill
  missing keys, preserving existing and unrepresented data.
- Validate authored/effective inherited data before side effects and again under the
  catalogue-operation lock. Shared edits require acknowledgement for multiple children;
  max 1000 per review, no child JSON duplication.
- Mandatory validated 0600 backup precedes same-directory temp write/flush/fsync and
  atomic replacement. Final source/dependency guard runs after temp flush; conflicts
  clean up the temp without overwriting newer source. Uncooperative external writers
  still cannot participate in an atomic filesystem/SQLite CAS: avoid external editing
  during an active save; this is not a distributed transaction.
- Only successful JSON writes precede product .update content1/update scan; shared
  saves use existing exhaustive own-collection refresh. No new marker/SKU protocol.
  UI distinguishes JSON saved from scanning/projection completion. Post-write failure
  has durable json_saved/scan_pending evidence and explicit signed scan-only retry,
  without rewriting JSON, another backup or duplicate editor notice.
- No-op has no write/marker/scan/Discord event. Real edits retain one configured editor
  notice, with existing scanner discovery/logging. No Shopify writes on save/retry,
  validation or page opening; signed Review/Confirm remains separate.
- Existing effective managed fingerprints invalidate actual per-product changes, not
  private notes or all catalogue comparisons. Scheduled-sale blockers remain; no future
  price/date is newly sent early. Normal `2026-10` and opt-in packed `2027-01` remain separate.

Legacy override-create POST is a confined unsaved-draft handoff, not arbitrary-folder
creation. Normal Products no longer offers direct override deletion; the retained
compatibility endpoint now needs admin/CSRF, signed ownership and backup. Full creator,
uploads, customisation designer/storefront, paid charges and sale scheduling remain
deferred. Migration head remains `0013_scheduled_tasks`. Registry choices can refresh
read-only, but a changed registry invalidates the original review: copy the Advanced
draft, reload and review against current choices before saving.

The historical M3A.9.1 audit below records pre-M3A.9.2 UI/save behaviour and future
contracts; statements that its helper was not integrated describe that checkpoint.

## Authority and implemented boundary

`product_info.json` is authored truth; `registry.json` is taxonomy; `sku_index.json`
and markers preserve allocated identities/history. SQLite is the reconstructable
catalogue projection plus durable remote identities, receipts, notifications and
tasks. Remote GIDs, customer answers and receipts never belong in authored JSON.
No production JSON, folder, image, SKU or remote object changes in this milestone.
Normal Shopify operations stay on **2026-10**; only default-off packed measurements
use **2027-01**. No platform selector or second product authority is planned.

New implementation:

- `app/metadata_edit.py`: pure same-document edit preparation, 1 MiB input/output
  bound, exact-source SHA-256 conflict check, independent patches, explicit key-path
  removals, unknown/legacy/internal/empty-value preservation and existing validation.
  No file/database writes, locks, scan, SKU allocation, notification or publishing.
  At M3A.9.1 save endpoints did not use this helper; M3A.9.2 integrates it above.
- Scoped manual reconciliation no longer resets the whole-catalogue selection
  cursor, including partial failure. No scheduler schema or safety-limit change.

## Three unchanged physical layouts

| Layout / authored selector | JSON ownership and inheritance | Identity / scanner / Shopify |
| --- | --- | --- |
| Simple Product / `Simple` | Shared collection root; optional sparse JSON in each product child. Special shared/override/folder title combination. | One independent parent per product folder, marker SKU, Shopify product/default variant. |
| Variable Collection / `Variable Collection` | Shared collection root for multiple child products; supported sparse child overrides. | Every child has its own parent SKU, combination SKUs and Shopify parent/variant/InventoryItem identities. Never flatten into one parent. |
| Single Variable Product / `Single Variable` | One root JSON for one parent; reserved case-insensitive `parent/` and ordered image-attribute directory levels. No distinct override at the same root. | One parent with stable combination SKUs and Shopify variations. Never move folders or treat image directories as products. |

Products/detail/shared/override editing and Prepared metadata building already
recognise these layouts, but are not a complete New Product wizard. Preserve
append/update/full/shared-refresh/reconstruction contracts; ordinary edits never
request identity-regenerating full mode. See SCANNER_CONTRACT and PRODUCT_INFO.

## Field-by-field compatibility audit

Unless noted, fields are authored shared or sparse override values and inherit when
absent. `S` = scanner/row, `DB` = SQLite projection, `P` = Shopify publisher; UI `G`
guided, `A` Advanced JSON, `R` read-only. This covers every registered root field;
the historical machine-readable matrix remains in
`app/resources/product_info/field_inventory.json`. No existing field is deleted.

| Field | Class / meaning / inheritance | Dependencies / current UI | Validation / future disposition |
| --- | --- | --- | --- |
| `collection_type` | KEEP; shared-only layout selector | S, DB Collection/Product, P layout; G | Required nonblank string; future creator only three known modes. Existing unknown modes warn/emit no rows. No ordinary layout conversion. |
| `sku_prefix` | KEEP; shared-only new-allocation prefix | S, DB Collection; P allocated SKU; G | Required nonblank; identity-sensitive future confirmation, no old SKU regeneration. |
| `title` | MODERNISE; special override/shared/folder combination | S Name, DB title, P title; G | String; show authored contribution and effective title separately. |
| `description` | MODERNISE; full-copy scalar override | S/DB description, P descriptionHtml; G Additional | String; promote to General. No SEO/short substitution; explicit clearing disclosed. |
| `short_description` | KEEP; scalar catalogue summary | S/DB/UI summary, no automatic Shopify main mapping; G | String; keep optional/collapsed, not deleted as Woo-only. |
| `price` | KEEP; regular-price scalar | S/DB parent/variants, P price; G | Number/numeric string; P finite nonnegative currency precision; preserve variant modifiers. |
| `sale_price` | KEEP; optional sale scalar | S/DB base sale, P unscheduled sale; G | Numeric; P sale below regular. Modifier sale discrepancy remains protected. |
| `sale_start_date` | MODERNISE; scalar calendar start | S/DB dates, P scheduled-sale blocker; G Additional | ISO date; future effective scheduling below, not implemented. |
| `sale_end_date` | MODERNISE; scalar calendar end | S/DB dates, P scheduled-sale blocker; G Additional | ISO date; future timezone/interval validation. |
| `weight` | KEEP; grams scalar | S/DB parent/variants, P inventory measurement; G | Numeric; P precision/range validation; no silent unit conversion. |
| `dimensions` | KEEP; whole-object product mm override | S/DB length/width/height, P product metafields/opt-in packed policy; G | Only numeric length/width/height; P positive bounded values. Same-file patch differs from whole-object inheritance. Not parcel presets. |
| `country_of_origin` | KEEP; scalar then optional deployment default | Snapshot/P inventory item, no new DB column; A | Uppercase two-letter; P Shopify enum. Default unset; GB only if accurate/configured. |
| `requires_shipping` | KEEP; scalar physical requirement | Snapshot/P inventory item; A | Boolean; absent unmanaged, never inferred from weight. |
| `manage_stock` | KEEP; scalar finished-product tracking | Authored/P inventory item; scanner quantity defaults unchanged; A | Boolean; absent reviewed untracked policy. No quantity/location authoring yet. |
| `backorders` | KEEP; scalar selling policy | Authored/P inventoryPolicy; A | no/yes/notify; notify does not implement customer alerts. |
| `categories` | KEEP; additive shared+override memberships | S/DB categories, P registry-key collection/ancestor mappings; G picker | String array; active unambiguous registry assignments. Empty child list cannot remove inherited categories. |
| `primary_category` | KEEP; scalar local category key | Resolver/P breadcrumb metadata; A | Nullable bounded assigned active key; not standard Shopify category. |
| `storefront_collections` | KEEP; absent inherits, explicit list replaces including empty | Resolver/P memberships; G | String array/registry identity; not filesystem Collection or hierarchical Shopify collection. |
| `tags` | KEEP; additive shared+override | S/DB tags, P tags; G | String array, P bounded nonblank. Missing unmanaged; explicit resolved empty can clear. Review remote replacements. Current form pruning can erase empty intent: future save must preserve it. |
| `live` | KEEP; scalar Draft/Live intent | S/DB historical Published default, P requires actual boolean; G | Missing unresolved for P. Live intent/ACTIVE are not verified Online Store publication; channel transitions deferred. |
| `attributes` | MODERNISE; entire object replaces inherited assignments | S/DB attributes, P variants/informational references; G registry rows | Scalar arrays; preserve term spelling/identity. Historical five row-slot limit is not an informational limit. |
| `variation_attributes` | KEEP; absent legacy all-attribute drivers, explicit list replaces | S combinations, P options; G per-attribute toggle | Unique exact assigned names; explicit empty means none. Scanner max5 explicit drivers; P max3,32 reviewed variants. Simple no drivers. |
| `image_attributes` | KEEP; ordered Single Variable directory drivers | S image resolution, DB associations, P featured media; G | String array; explicit image drivers must remain variation drivers. No directory reordering by accident. |
| `variation_modifiers` | MODERNISE; whole-object replacement; exact/most-specific match | S effective price/weight/mm, DB variants, P checks/uses projection; G working modifier rows | Object, Name=Term segments joined by pipe; numeric price/sale/weight/dimensions. Modifier sale calculated but not emitted: no silent correction. |
| `customisation` | KEEP/EXTEND later; stable-field merge/proof scalar | Resolver/snapshot; no forms/price/order answers; A | Existing strict definition schema; proposal below. |
| `meta_title` | MODERNISE; scalar SEO title | S historical Yoast row, DB, P native SEO; G | String; guided60 guidance vs P max70. Retain key, remove Yoast presentation. |
| `meta_description` | MODERNISE; scalar SEO copy | S historical Yoast row, DB, P native SEO; G | String; guided160 guidance vs P max320. Separate from main description. |
| `shipping_class` | LEGACY; retained scalar Woo metadata | Intentionally blank row/DB, P none; G | String; hide normal Shopify control, preserve authored data. |
| `grouped_ids` | LEGACY; additive editor-only list | No active row/P projection; G Additional | String array; hide/preserve. |
| `grouped_products` | LEGACY; Woo CSV-only list | External row may project DB; JSON not consumed, P none; G | String array; hide/preserve. |
| `upsell_ids` | LEGACY spelling; additive alias | Editor persists, scanner ignores spelling; G | String array; no automatic normalisation. |
| `cross_sell_ids` | LEGACY spelling; additive alias | Same as upsell_ids; G | String array; hide/preserve. |
| `upsells` | KEEP compatibility; old additive relationship | S/DB lists, Shopify relationship writes deferred; G | String array; separate future relationship editor. |
| `crosssells` | KEEP compatibility; old additive relationship | S/DB lists, Shopify relationship writes deferred; G | String array; preserve spelling/data. |
| `relationships` | KEEP; complete product-specific ordered relationship set | Separate workspace/DB reconstructable edges; P deferred; A | Only unique bounded cross_sells/upsells SKU arrays; product block outranks old lists. No automatic shared migration. |

Forward-compatible keys currently consumed outside the registered root inventory
(existing editor warns unknown; do not mistake that warning for obsolete data):

| Key | Authority / inheritance / use | UI / validation / class |
| --- | --- | --- |
| `vendor` | Authored scalar, P vendor, no new DB column | A; explicit nonblank max255; KEEP, recognise schema/control later. |
| `handle` | Authored scalar, P reviewed unique slug | A; lowercase hyphenated max255; KEEP; normally per-product, shared collision warning needed. |
| `shopify_product_type` | Authored scalar, P explicit productType | A; max255; KEEP; not an entire category path. |
| `harmonized_system_code` | Authored scalar; Variable P explicit6–12 digits | A; KEEP; Simple equivalent/control needs tested extension before support is claimed. |
| `stock_quantity` | Forward-compatible value, not an authorised quantity writer | A; INTERNAL/restricted; publishing rejects quantity without explicit tracking, never sets quantities/locations. |

Generated sku/source_folder/modifiers, row defaults, image URLs and remote GIDs are
INTERNAL, not ordinary authored inputs. Unknown production instructions/design
references/historical Woo IDs or website URLs are preserved; no general active
consumer was found for arbitrary root keys. Establish ownership before any removal.
No deprecated-field migration occurs here. Production Method as a public registry
attribute is distinct from private design paths or production notes.

Standard Shopify category is **not a consumed authored root key** today. It comes
from reviewed `ShopifyCollectionIdentity.standard_category_id` mappings keyed to
assigned registry identities; publishing validates that actual API category and
blocks conflicting mappings. Reuse that Taxonomy mapping UI/storage, not a new
`shopify_category` JSON field or guessed ID. Local category hierarchy stays separate.

## Existing editor reuse map

| Component | Reuse / extend / eventual retirement |
| --- | --- |
| metadata_editor.html / metadata-editor.js | Extend guided/Advanced, errors, override switches, choices, ordering and dirty state. Hide confirmed legacy controls in M3A.9.2, never delete hidden data. |
| metadata_workspace.py | Confined portable sources, authored/effective comparisons, affected-child pages, variation/media detail. Add raw token/scope summary. |
| product_info.py / schemas / inventory / examples/templates | Shared validation/reference APIs. Incrementally recognise consumed keys; fictional full templates must not replace real unknown fields. |
| Variation/modifier and image-attribute controls | Keep working terms/toggles, modifier matching, ordered image drivers and read-only effective SKU/price. No competing engine. |
| intake_metadata_builder.py / Prepared forms | Folder-aware semantic validation, proposal digest, staging/readback and scan handoff for missing root JSON. Not a generic New Product endpoint. |
| Structured import / folder editor / grouping | Confined proposals, staged copy/collision safety; no existing-product folder reorganisation. |
| Products / detail / Collections / Taxonomy | Existing navigation, thumbnails, seven-column/mobile cards, shared/override actions and operation progress. No second catalogue. |
| custom.css / shared layout/components | Current tokens, field-grid, editor-workspace-section, buttons/status/mobile classes. No new palette/framework. |
| Legacy raw editor/routes | Preserve compatibility until callers/tests are mapped; later converge on canonical workspace, not mass-delete shared code. |

Future sections: General, Pricing, Variations, Customisations, Images, Organisation,
Shipping, SEO, Production, Advanced. General separates authored/effective title,
read-only SKU/layout, description and intent. Private production instructions and
technical sources/markers/identity diagnostics use progressive disclosure. Preserve
shared collection editing rather than copying 150 dog-breed JSONs.

## Customisation schema and templates

Reuse merge_product_json/merge_customisation, not another inheritance engine.
Scalars override, objects replace except customisation; categories/tags deduplicate
through a set (membership, not ordering); storefront_collections/variation_attributes
explicit lists replace. Title combination and same-file patch semantics stay distinct.

**Implemented unversioned v1:** proof_policy none/required/optional; max 30 field IDs
`[a-z][a-z0-9_]{0,63}`; text, multiline, number, photo, choice, checkbox. Enabled
resolved fields need type/label; optional required/enabled/choices/max_length/minimum/
maximum. Fields merge by stable ID/property; child additions preserve inherited fields.
enabled:false disables; remove:true alone removes locally; empty object does not clear.
Explicit proof policy replaces. No customer forms/upload/charges or variation inputs.

**Design-only v2:** retain existing type names and unversioned v1 compatibility;
introduce schema_version 2 with tested validator/resolver work in M3A.9.3, no automatic
rewrites. Add radio, date, colour, instruction; choice remains dropdown, photo means
file/photo. Future properties: help_text, placeholder, integer order (stable-ID tie),
min_length/max_length, numeric bounds, choices/default, explicit MIME accept list,
max_upload_bytes, visible_when (stable field reference, equals/in operators),
production_key and optional charge. Defaults must match type/choices; conditional
references must exist, not cycle or make required hidden fields impossible. No
executable expressions/arbitrary HTML; file defaults forbidden. Exact file limits
need approval then. Instruction has no answer. Variation overrides remain deferred.

Already-valid shared definition:

```json
{"customisation":{"proof_policy":"optional","fields":{
  "dogs_name":{"type":"text","label":"Dog's Name","required":true,"max_length":80}
}}}
```

All relevant breeds inherit once. Child label-only patch retains type/required;
explicit disabled/removal does not alter siblings. Future order patches reorder
without renaming IDs. Order is not accepted by v1 today.

Templates initially extend repository-managed preset assets near existing product
templates (not a separate database/product file). Birthday: child name/age/optional
photo/message; Pet: dog's name/optional photo/engraving style; Sign: number/street/text
style. Applying a preset is an independent **reviewed copy** into a chosen authored
layer; stable IDs retained, collisions disclosed, existing fields not overwritten
silently. Future provenance/version is not a live reference. Editing one copy never
edits every use; shared collection JSON remains the live inheritance mechanism.
Admin template CRUD/live versioned references require a later authority decision.

## Save and scanner lifecycle

Current route deep-merges partial data, prunes empties, validates, acquires operation,
backs up/atomically saves, then scans. Guided sends replace:true. Full replacement
and pruning can lose unknown/explicit empty instructions; backup/marker failures are
best-effort and no stale-source token is enforced. JSON save/override routes are
CSRF-exempt today: future integration must add admin/CSRF, not copy the exemption.
No endpoint behaviour changes in this foundation. These are concrete M3A.9.2 gates.

Future safe save:

1. Confine a known source under catalogue root; load raw text, authored/effective
   values and affected children; capture source plus shared/registry dependencies.
2. Submit explicit changes/removals and scope confirmation/CSRF. prepare_edit checks
   freshly read raw text, preserves unknown/private/legacy keys and explicit empties.
3. Validate authored and effective inherited metadata/driver combinations. Show
   warnings/identity-sensitive changes and read-only calculated values.
4. Acquire existing catalogue operation, reread source/dependency digests before
   backup/replace. Stale form 409 offers reload/diff, never silent overwrite. External
   file writers do not share this lock: atomic replace is not cross-system isolation.
5. Required backup succeeds before write; failure blocks saving. Product override
   writes .update in affected product folder; shared metadata uses existing exhaustive
   **own-collection** refresh, not a root marker assumed to select every child.
   Report save/marker/scan failures separately with retained recoverable receipt.
6. Reuse start_scan/ingestion and identities. No-op means no write/rescan/notification.
   Shopify managed status follows separately; no automatic publishing on save.

.scanned holds sku/title/images_used/**scan_date** and variable combinations/SKUs.
.scanned.pending stages unchanged marker payload until DB parent commit/finalisation;
.update existence selects the folder (content currently1, not metadata). Index stores
parent/variation counters/mappings, never general metadata. Fresh DB reconstruction
forces selection with identity reuse/no reset despite old markers; append still skips
them. Ordinary editor saves must not select destructive full regeneration. Keep
detailed discovery/skip/SKU/variation/error/summary logs and legitimate Discord events.

## New Product, duplication and images

Future creation: choose confined destination/layout; propose correct folder/JSON
ownership; shared/individual data; registry/modifiers/media/customisation; validate;
review collisions/new allocation; exclusively create missing files using existing
staged intake/catalogue operation; scan normally; separately review Shopify. Never
overwrite valid JSON or invent facts. Variable child inherits shared; Single Variable
has no second override root. No new creation/folder-mutation endpoint here.

Duplication design: copy selected authored/media/customisation data and intended
inheritance, allocate **new** local identity/SKU through scanner, exclude every remote
GID/ledger item/pending marker/verification record. No duplication implemented.

Reuse confined gallery/variation diagnostics and authenticated previews. Future panel
shows original source, primary/gallery order/alt, driver/variant association, missing
file and remote mapping separately. Adding files needs reviewed safe destination;
removing association is not file deletion. Discovery ordering is filename-based; no
general authored gallery manifest exists today. Add one compatible association schema
only after scanner/publisher tests, never another image authority. No WebP conversion,
source rename, automatic remote replacement or featured-media logic change here.
Keep actual CDN URLs, never extension substitution.

## Scheduled sale contract — design only

Keep regular/sale/start/end keys. Current publishers reject scheduled sale dates;
**this milestone does not enable date-aware publishing**. Future pure effective-price
projection must be shared by Preview, status fingerprints and authorised task writes:

- Normal price=regular, compare-at=null; valid unscheduled sale price=sale,
  compare-at=regular; require 0 <= sale < regular.
- Date-only start 00:00 Europe/London inclusive; end inclusive to next local midnight
  exclusive. Missing start immediate, missing end open-ended; end >= start. ZoneInfo
  conversion handles 23/25-hour DST days, not fixed 24-hour arithmetic.
- Before start/after end normal projection; future amount/date edits excluded from
  **current managed** fingerprints unless effective price changes. Signed reviews
  still bind full source/dependency changes for stale-confirmation safety.
- Active sale projects sale/regular. Explicit early cancellation restores regular/null
  through reviewed transition. Draft/Live and sale state remain separate.
- Variants retain identities/modifier-derived regular prices. Define/test per-variant
  sale/modifier policy first, never one parent sale copied across variants or a silent
  fix of the known modifier-sale emission discrepancy.
- Reuse existing durable scheduler, opt-in authorisation, persisted transition
  receipt, identity/current-price readback and verification/recovery. After downtime,
  converge to current effective price, not replay expired discounts. Failed/uncertain
  transition never earns successful freshness; retain last good state/actionable error
  and no blind retry. Existing notification ledger handles approved outcomes.

Future tests: before/at/after, schedule-only edits, inherited schedules, modifier
prices, UK DST, cancellation, downtime, timeout/restart. No pricing/fingerprint/write
change implemented now.

## Storefront, orders and paid customisations — proposals

Reviewed publishing eventually writes a versioned **resolved public definition** to
a dedicated app-owned product metafield, excluding private paths/notes/answers.
Ship/test actual TLC Liquid product-theme block or supported theme app extension
separately; check installed app distribution/extension eligibility before packaging.
Local definitions alone do not render controls.
[Theme extension contract](https://shopify.dev/docs/apps/build/online-store/theme-app-extensions).

Free inputs use product-form line-item properties, not variants. Rendering/validation
are custom work around native data carriers. Stable answer keys/definition version
and per-configuration identifier distinguish identical variants with different answers.
Test normal/Ajax cart, quantity/edit/remove, accelerated checkout and persistence.
[Ajax Cart API](https://shopify.dev/docs/api/ajax/reference/cart) supports distinct
property-bearing lines; [product template](https://shopify.dev/docs/storefronts/themes/architecture/templates/product/overview)
supports text/file property inputs, not a private upload service or surcharge.

Future order import reads line-item customAttributes and snapshots labels/answers/
version by Shopify order and **line-item identity**, not current form definition.
[Admin LineItem](https://shopify.dev/docs/api/admin-graphql/latest/objects/LineItem).
Orders/answers then need operational tables, not duplicate product truth. Protected
uploads need authentication/rate limits, MIME/magic/size checks, malware policy,
private storage, opaque references, signed expiring access, retention/consent and safe
proof preview. Never public customer photos/credentials in form definitions. Proof
email/approval/production/packing consume immutable answers/private files; deferred.
Order scopes and customer-data approval precede implementation.

| Paid approach | Feasibility / tradeoff / recommendation |
| --- | --- |
| Existing finite variants | Native checkout/refund/SKU visibility; valid only stock/production choices. Shopify3 options/default2048 variants, TLC currently32. Names/photos never variants. |
| Linked add-on product/variant | Native extra line price/refund/reporting, no required paid app. Recommended bounded Basic prototype for gift box/engraving; prove pairing/quantity/removal/refund allocation/theme compatibility. Browser-only links are bypassable: mandatory fees need trusted enforcement before release. |
| Cart Transform/Functions | Custom Function apps require Plus; price-changing lineUpdate Plus/development eligibility. Not assumed available for this private Basic app. Public Store apps may offer supported Functions with dependencies/cost; no public-app workaround promised. |
| Properties/discount alone | Not an arbitrary surcharge writer. Properties carry answers, not authorised prices. Reject shortcut. |

Verified contracts: [option limit](https://shopify.dev/docs/storefronts/themes/product-merchandising/variants),
[variant count](https://shopify.dev/docs/api/admin-graphql/latest/objects/ProductVariant),
[Functions availability](https://shopify.dev/docs/apps/build/functions),
[Cart Transform](https://shopify.dev/docs/api/functions/latest/cart-transform).
Reserve future charge metadata amount decimal string/currency/basis per_item and an
operational add-on mapping only after mechanism validation. No browser-supplied
price trusted, paid app selected, charges or uploads implemented.

## Database decision and scheduler scalability

Migration head stays **0013_scheduled_tasks**; no new table/migration for helper or
cursor fix. Templates need no speculative DB. Future orders/uploads/authorised sale
transitions require separately justified operational persistence, reusing identities/
receipts/tasks where applicable.

Current 1000-parent windows rotate; partial run persists lowest unfinished selection;
paused queue survives owner restart; costly heads rotate/defer after3 local-budget
failures. 60 slices/20 minutes remain bounds, not proof 1000 were verified. Only complete
runs update task success; failed reads do not refresh product successful timestamps.
Global credentials/permissions failure cannot create successful work regardless of
fairness. No live catalogue throughput benchmark or unbounded scaling claim.

**Proven fix:** explicitly scoped manual selection and partial finalisation used to
overwrite the whole-catalogue cursor. Frequent scoped runs could keep restarting the
first window. Both writes now exclude scopes. Tests cover 1005 parents/windows/wrap,
scoped isolation and partial restart continuation; existing tests cover priority/
throttling, owner restart, bounded deferrals and last-success accuracy. Deferred
products stay eligible next sweep; no second scheduler or persistent metadata cache.
Future very large catalogues need measured sweeps/queue retention/alerts separately.

## Sequenced implementation and acceptance

1. **M3A.9.1 now:** inventory/reuse/design contract, pure helper, cursor correction,
   regression; no new full form or deployed save guard.
2. **M3A.9.2 next — safe existing-document editor:** source/dependency tokens, admin/
   CSRF, patch/removal, reliable backup/atomic-write/scoped scan; currently supported
   General/Pricing/Shipping/SEO/Organisation controls, authored/effective displays,
   hidden legacy/unknown/empty preservation. Keep working variation/image controls.
3. **M3A.9.3:** tested v1/v2 customisation types/constraints/conditions/order,
   collection merge, preset copies/proof policy; no upload/charge promises yet.
4. **M3A.9.4:** safe local New Product/missing JSON using existing intake/layout,
   collision controls/new allocation. Duplication only with identity exclusion tests.
5. **M3A.9.5:** source/gallery/featured/variant association authoring with compatible
   schema/scanner tests before replacements.
6. **Separate approved milestones:** effective sale projection then authorised tasks;
   public definitions/theme rendering/order capture; private uploads/proofs; paid
   add-on pilot only after checkout integrity. None claimed working from local schema.

Planned tests (not passed claims): stale shared/override/registry conflicts, CSRF/
authorisation, unknown/empty preservation, backup/write/marker failures/no-op silence,
all-layout SKU/modifier/image parity and affected rescan/retained mappings; 150-child
customisation merge/removal/order; v1/v2 roundtrip; sale boundaries/DST/fingerprints;
theme/cart/checkout/order identity; private-upload expiry/security; duplicate exclusions.
No broad scanner/editor rewrite is needed.
