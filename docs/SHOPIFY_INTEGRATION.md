# Shopify integration — Phase 4 M1 / M2 / M3A

## Shopify-first Products workspace

Open **Products**, find LCBC-0001, then **Review Shopify Changes** (also on Product
Detail). The product-scoped workspace uses the existing publisher, not another
catalogue: build its read-only Preview, review Update/No Change, and confirm once only
when changes are supported. Retained verified identities never enter Create. Identity
/ Refresh opens the existing read-only reconciliation and retained operation evidence;
uncertain writes stay blocked. First creation checks remote SKU candidates through
bounded [Shopify productVariants discovery](https://shopify.dev/docs/api/admin-graphql/2026-10/queries/productVariants)
at review and confirmation; candidates are not silently adopted.

List/Detail/Dashboard GETs do not contact Shopify. No Change there is explicitly the
last verified managed state with matching local metadata/image stats, not a claim
that remote state was just checked. Preview still hashes bytes and reads current
remote fields. Source fingerprints live only in existing versioned bounded operation
evidence; legacy records without that baseline display Verification Unavailable with
their retained identity and timestamp. No migration/backfill or authored rewrite.

`live: false` is required for draft writes. `live: true` is displayed as Live intended,
but blocked until reviewed Online Store channel publication is implemented; missing
or invalid intent must be resolved in the existing collection/override editor. Do not
change a genuinely intended Live product to Draft merely to bypass this limitation.
Shopify status and channel publication are separate; publication remains Not verified.

Selection (individual, collection's visible products or eligible page products)
prepares individual review links only. It does not authorize or execute a batch.
Variable publishing, collections/navigation, actual Live transitions and storefront
customisation remain deferred. Existing M3A.5 fields/media limitations below apply.

### Unraid acceptance from normal Products

1. Back up persistent appdata/database and use the new commit-specific test image,
   preserving every existing volume/environment value. No migration is added here.
2. Test Shopify connection with the installed app's existing `read_products` and
   `write_products` scopes; add `write_files` only for reviewed alt-text changes.
3. Open Products → LCBC-0001 → View Product. Confirm retained Product ID
   `gid://shopify/Product/16009365160319`; legacy evidence may require Review for
   current comparison. Inspect authored/inherited live intent, shipping and metadata.
4. If intended Draft, ensure the resolved authored `live` is explicitly false through
   the established editor. Save only intentional edits; let targeted rescan complete.
5. Review Shopify Changes → Build read-only Preview. Expect Update or No Change,
   never Create for the retained verified product. Resolve recovery via Identity first.
6. Review changed/matching/unsupported fields and media actions; explicitly confirm
   a supported Update once. No Change needs no write. Do not retry uncertain writes.
7. Verify the same product ID in Shopify Admin, shipping/SEO/pricing/dimensions and
   original media IDs/URLs. Use Identity / Refresh for processing or recovery.
8. Return to Products and build fresh Review: expect No Change after successful
   complete readback. Check mobile action visibility and selection-only review queue.
   Report discrepancies before bulk or collection publishing proceeds.

The operational fingerprint reuses the Simple publisher's supported field mapping;
changes only to deferred customisation/category metadata do not falsely imply a
publishable Update. Such definitions remain visible locally and unsupported in Review.

## M3A.5 — complete managed Simple drafts and reviewed updates

Implemented, mocked-test verified; **live update acceptance pending**. The original
M3A live test below remains creation/media acceptance only. `/shopify/drafts`
now previews Create, Update or No Change; unsafe/uncertain identities show an
explicit recovery blocker. A retained product never falls back to Create, even
if it disappears remotely or configuration points at a different store. Verified
legacy mappings are read without rewriting their store keys/domain. Only one
active local Simple parent and one remote Simple variant are supported; DRAFT
only. No sales-channel publication, Variables, collections or category writes.

### Managed product information

| Authored/resolved source | Shopify target / policy |
|---|---|
| Title, description | `title`, `descriptionHtml`; same filesystem resolver |
| Stable SKU | Retained default variant / inventory item SKU, never new local allocation |
| Regular/sale price | No sale: regular → price, compare-at cleared. Valid unscheduled sale below regular: sale → price, regular → compare-at. Invalid/equal/higher sale or scheduled sale dates block review. Removing a sale restores regular price. No automatic sale scheduler. |
| Weight in grams | `inventoryItem.measurement.weight`, unit `GRAMS`; readback compares equivalent weight units |
| Shipping intent, origin | `requiresShipping`, `countryCodeOfOrigin`; absent values are unmanaged. Origin is checked against the configured API's authoritative `CountryCode` enum, not only its format. GB is never implicit. |
| Optional vendor | `vendor` only when explicitly authored/configured in resolved metadata |
| Tags | Reviewed full tag-list replacement, including remote-only tags; absent tags stay unmanaged |
| `meta_title`, `meta_description` | Shopify SEO title/description when authored; supported limits 70/320 characters |
| Optional `handle` | Explicit lowercase slug; uniqueness checked. Updates preserve attempt correlation, do not create redirects. Without authored handle, existing URL stays unchanged. |
| Dimensions in millimetres | Product metafields of type `dimension`, keys `product_length`, `product_width`, `product_height` in namespace `tlc_dashboard` |
| Original image gallery | Create uploads original PNG/JPEG/WebP; update retains IDs/URLs and unchanged source hashes, with reviewed order/alt changes |

Dimension namespace is optionally configured with
`SHOPIFY_PRODUCT_DIMENSIONS_NAMESPACE` (default `tlc_dashboard`). Values are typed
JSON such as `{"value":155,"unit":"mm"}`. Namespace/key upserts reuse existing
metafields; no definitions are automatically created. Incompatible existing field
types block review. These are **individual product dimensions**, not finished
parcels, package presets or Shopify Shipping label measurements. Changing namespace
does not delete old fields. Zero/absent dimensions are omitted. Missing optional
fields never manufacture defaults or silently clear remote information. Current
vendor/handle are forward-compatible Advanced JSON values, validated by publishing.

Inventory tracking, quantities, locations, fulfilment services and shipping package
configuration are never written. Standard categories, collections, relationships,
customer-facing customisation and scheduled-sale automation remain deferred.

### Review, execution and recovery

Preview reads connection/store identity, the current complete remote Simple state
and original local hashes. It shows changed/matching fields, warnings, unsupported
features and media actions. Country enum and handle checks add bounded reads only
when needed. Confirm is administrator-only, CSRF-protected and signed for 15 minutes.
It revalidates local/configuration/identity/remote digests under the existing
operation lock. Only the reviewed changed fields are mutated; source/configuration
is rechecked between write steps. A No Change plan has no confirmation/write token.

Pending/update-attempt evidence is committed before mutations to the existing
`ShopifyProductIdentity` record. Bounded version-1 evidence retains previous verified
plan provenance, variant/inventory IDs, media receipts, completed stages and job
receipt where applicable. CatalogueOperation history is retained, not rewritten.
Independent readback must prove the whole managed contract, IDs, correlation and
gallery before state becomes verified. No migration, source rewrite or Woo change.

Update, readback or transport failure retains earlier successful steps and blocks
another write. Explicit reconciliation uses only reads, serialized against writes,
and does not retry/delete/recreate anything. A complete match verifies the reviewed
result; incomplete/partial/different results remain uncertain. An unchanged read
alone does not prove that an uncertain delayed mutation cannot still arrive. Inspect
the retained draft and correct the reviewed fields in Shopify Admin if necessary,
then reconcile again. Historical partial/interrupted operations remain visible.

Unchanged images are never staged or re-uploaded on update. Image replacement,
addition/removal, missing mappings or remote-only media block review rather than
delete/overwrite. Alt uses `fileUpdate` and requires **`write_files`**; the existing
`read_products` / `write_products` scopes cover product/variant read/update. A shared
Shopify file's other uses can reflect its alt change; this is disclosed at review.
Gallery reorder is asynchronous: an incomplete order stays Processing until an
explicit read observes it. No background retry/poll storm. Actual returned CDN URLs
remain evidence; extensions are never rewritten. API contracts:
[inventory fields](https://shopify.dev/docs/api/admin-graphql/latest/input-objects/InventoryItemInput),
[alt updates](https://shopify.dev/docs/api/admin-graphql/latest/mutations/fileUpdate),
[gallery order](https://shopify.dev/docs/api/admin-graphql/latest/mutations/productReorderMedia).

### Live acceptance: update the retained LCBC-0001 draft

1. Back up persistent appdata/database and retain the previous image tag. Deploy
   the unique M3A.5 AMD64 test image manually, keeping all current volume mappings.
   No new migration is required; existing 0010 remains authoritative.
2. Keep the verified store configuration. If changing alt text, release/install
   an app version granting `write_files` too; test connection (restart to obtain
   a fresh Client Credentials token after a scope change if necessary).
3. Use existing metadata editor/Advanced JSON to review accurate origin, shipping,
   weight, descriptions, prices/SEO/vendor/tags/dimensions. Use the offered affected
   update workflow where projection changed. Never change source filenames/SKU.
4. Open `/shopify/drafts`, select `LCBC-0001` and build read-only Preview. Verify
   retained Shop `gid://shopify/Shop/103940129151` and Product
   `gid://shopify/Product/16009365160319`; the plan must say Update or No Change,
   not Create. Stop if the identity is missing or a recovery blocker appears.
5. Inspect every field/tag replacement/handle/media action and confirm once. Check
   the same draft in Shopify Admin: SKU, customer price/compare-at, shipping/origin,
   SEO/vendor/tags, product dimension metafields and retained image IDs/URLs.
6. If Processing, use read-only reconciliation after the gallery job finishes.
   If uncertain, do not confirm again; reconcile and report the retained operation.
7. Successful verification followed by fresh Preview must show No Change. Confirm
   that no duplicate product or unchanged-image upload occurred. Report differences
   before continuing to category/collection mapping.

## M3A.4 catalogue snapshot readiness

The existing resolver supports optional `country_of_origin`, `requires_shipping`
and `customisation`; see [schema and inheritance](PRODUCT_INFO.md). Advanced JSON
uses existing validation/rescan controls. No user catalogue is automatically
rewritten. Optional `DEFAULT_COUNTRY_OF_ORIGIN` can be supplied through Docker
`.env` or a manually added Unraid variable; leave blank unless reviewed and accurate.
Product/collection origin overrides it; no shipping intent is invented.

The immutable snapshot exposes `shipping` (grams/mm, origin/provenance, shipping
intent), `seo`, optional authored `vendor`/`handle`, `storefront_collections`,
resolved `taxonomy`, `customisation`, and each variant's projected weight/dimensions.
Existing descriptions/prices/images/identities remain available. Vendor/handle
are retained forward-compatible metadata, not newly formalised editor fields.
No new DB columns/migration. These fields are ready for a future publisher,
**not silently added to M3A's remote write contract**. M3A still creates/verifies
one Simple draft's existing title/description/SKU/price/media contract. Shipping,
SEO, sale policy, standard categories and collections require reviewed managed
updates. Retained identities still block duplicate creates. Customisation does not
create variants, forms, proofs or customer uploads.

Existing Taxonomy editing remains available. Reference-impact review for category
path changes and customer-navigation eligibility are deferred; drafts or unknown
Shopify publication cannot make customer menus visible. Registry hierarchy/order
remains the future authority, never an independent navigation file.

Connection/discovery remain read-only. M3A adds separately reviewed creation of
one Simple draft with original images; the user has now accepted that core live
path (details below). Complete publishing remains unfinished. Woo remains a
temporary fallback; no runtime rename or catalogue migration has occurred.

## TLCDashboard transition assessment 2026-10-12

### Direction and live evidence

TLCDashboard is exclusively for Tender Loving Creations. Shopify is the intended
permanent commerce platform, not one half of a permanent dual-platform product.
Keep Woo as a working fallback without new features until a separate retirement
milestone. Do not implement generic plugins, public distribution, multi-business
support or a platform-switching migration workflow.

User-reported live evidence (not a new API call by this assessment): Client
Credentials authenticated Shop `gid://shopify/Shop/103940129151`, API `2026-10`,
with `read_products` and `write_products`. SKU `LCBC-0001`, £3.50, was created as
unpublished draft `gid://shopify/Product/16009365160319`. Original local PNG upload
and product were visibly confirmed in Shopify Admin. Explicit read-only
reconciliation reached retained stage `verified`. This proves core Simple create,
media and reconciliation, not shipping completeness, updates, Variable products,
modifiers, collections/categories/navigation, personalisation, orders or bulk sync.
Full-lifetime automatic token renewal was not part of the reported live test.

The user observed `tenderlcreations.myshopify.com` failing identity comparison and
`jrg9sr-yy.myshopify.com` successfully verifying this Shop. Preserve that evidence
and working configuration. M3A.2's domain-alias correction is mocked-test verified,
not yet separately live accepted; do not attribute the successful original-domain
test to the alias fix. Store GID remains authoritative; authentication alone
never proves a configured domain belongs to a store.

### Catalogue authority, intake and projection

The physical layouts are fixed: Simple Product (`collection_type: Simple`),
Variable Collection (`Variable Collection`) and Single Variable Product
(`Single Variable`). Labels are not instructions to rename stored enum values.
Collection-root shared JSON, optional Simple/Variable Collection overrides,
Single Variable root JSON and reserved case-insensitive Parent image ownership
remain exactly as implemented. Do not move folders/images, allocate replacement
SKUs, rewrite JSON or change marker semantics.

`intake_structured_import.py` safely copies reviewed existing trees into Prepared;
existing JSON is preserved byte-for-byte. `intake_metadata_builder.py` validates
and explicitly authors missing definitions through the existing staged writer.
Prepared validation/copy handoff precedes an explicit catalogue scan. Existing
metadata/override editors use `product_info.py`, `metadata_workspace.py` and safe
JSON writers; inherited defaults are not flattened into product overrides.
The legacy `/api/override/create` route operates on an already projected product
and cannot bootstrap a catalogue lacking collection JSON; it creates only `{}`
when missing. Fresh folder import must therefore use the pre-scan Intake/editor
path, not assume this projected-product action can create shared requirements.
This is not automatic generation of unknown product details.

Important fresh-install gap: `_eligible_operation` in the Prepared metadata builder
requires a successful image-renaming or previous metadata operation; structured
import alone is not eligible. A pure no-renaming authoring path for every existing
missing-shared-JSON tree is therefore not established by this assessment. Preserve
original names and source folders; separately verify/approve a review-only folder
classification and missing-JSON authoring entry rather than forcing renaming or
inventing metadata. This does not block publishing already valid/scanned products.

`scan_runner.py` plans scope/locks/history; `scanner.py` resolves shared metadata,
children, modifiers and images; `sku_manager.py` retains marker-based identity and
atomic indexes. `ingest.py` projects complete parent units transactionally into
Collection/Product/Variation, attributes, images, assets and taxonomy memberships.
`product_info.json` stays authoritative; SQLite is projection/identity/operations.
`commerce.py` freezes authored and projected state for Shopify. Shopify draft
publishing reads authorized original bytes, not generated WebP output URLs.

### Evidence-backed optimisation candidates, not implementation

1. `ingest_rows_to_db` always runs `_scan_sku_folder_index` (full `os.walk`) even
   with `source_folders`; `_source_context` is then called for every indexed SKU,
   reloading the same collection JSON repeatedly. First candidate: operation-local
   source/context cache for affected parents, with proven fallback parity.
2. `catalogue_snapshot` calls the authored resolver, then lazily traverses ORM
   attributes/images/children. Future batch publishing should eager-load bounded
   parent scopes once; M3A deliberately handles one parent, so no current query
   storm or measured timing is asserted.
3. SKU index load/save occurs during variation allocation. This is deliberate
   durable identity behaviour, not permission to batch counters. Image byte hashes
   are reread at Confirm intentionally for source freshness; do not cache across
   that boundary. No measured startup bottleneck was established.

Baselines before scanner optimisation: `test_scanner_contract_characterization`,
`test_phase0_scanner_output_parity`, `test_scanner_resolution`,
`test_m52_scan_ingest_regression`, `test_transactional_ingest`,
`test_ingest_mapping`, `test_reconstruction`, `test_single_variable_parent_images`,
`test_source_image_resolution`, `test_product_info_contract` and relevant Intake
tests. Select cases proving same IDs/SKUs/combinations/modifiers/inheritance/images,
scope selection and marker/database recovery; older Phase 0 discrepancy tests
are historical characterisation, not a mandate to restore obsolete durability.

Detailed discovery, skipped/unchanged, new/updated, missing/invalid JSON, SKU,
modifier matches, variation, warnings and completion logs must survive. Preserve
`utils/discord.py`, existing webhook names and bounded non-fatal delivery,
`ingest.notify_ingest_product`, metadata/Intake events and operation terminal hooks.
Woo-specific `notify_woo_publish_*` formatters/callers can retire only after shared
Discord infrastructure is retained. Avoid duplicate generic/specialized terminal
messages. New Shopify/order/proof notification types are deferred.

### Field gaps and minimum backwards-compatible authoring

| Information | Existing authority / M3A status | Planned boundary |
| --- | --- | --- |
| Title, description, SKU, regular price | Authored/inherited + projection; M3A sends and verifies these | Preserve resolver; review HTML/short-description display policy, do not concatenate silently |
| Weight, dimensions | Existing authored grams/mm; projected parent/modified variants; M3A omits them | Variant InventoryItem weight; typed dimensions metafields, not assumed parcel dimensions |
| Sale price/dates | Existing authored/projected fields; M3A omits them | Reviewed price/compare-at policy; scheduled sales separately |
| Origin, requires shipping, vendor | Not recognised canonical authored fields | Optional `country_of_origin`, `requires_shipping`, `vendor`; collection defaults/overrides; reviewed configurable GB origin only when accurate |
| SEO title/description | Existing `meta_title`, `meta_description`; M3A omits | Shopify SEO fields; optional authored `handle` separate from existing correlation handle/recovery |
| Category, tags, collections | Existing local categories/tags/`storefront_collections`; M3A omits | Explicit standard-category mapping and operational collection identities; never convert Dashboard Collection into remote membership implicitly |
| Images, alt/order | M3A uploads original PNG/JPEG/WebP, retains actual IDs/URLs and verifies order/alt | Live proved one PNG, not multi-image or WebP cases; variant media deferred |
| Inventory | Existing projected defaults are not reviewed Shopify stock/location policy | Separate tracked/backorder/location decision; do not publish invented stock |
| Personalisation/photos/proof/recipe | No canonical typed authoring/publishing contract established | Later optional `personalisation`, `proof_required`, `production_recipe` definitions after separate schema approval; no bulk JSON rewrite |

No authored fields are added by this assessment. Existing optional overrides and
units remain; do not require every new field on every product. Update the existing
schemas/inventory/editor only for approved fields. All remote IDs, upload receipts,
store identity and verification evidence stay in operational storage. Product
dimensions are not proof of packaging dimensions or carrier eligibility.

### Product types and customisation

Simple maps to one Shopify product/default variant. Variable Collection maps each
existing parent to its Shopify product, not the filesystem collection to a single
product. Single Variable maps its existing root parent/child projection directly.
Keep child SKUs and local source identities; map remote variant IDs separately.
Only explicit variation drivers produce options; informational attributes become
typed metadata/taxonomy, not additional combinations. Absence of
`variation_attributes` retains current legacy resolution; `[]` stays meaningful.

Shopify currently supports up to three options and normally 2,048 variants per
product; read actual Shop resource limits before planning writes. The known 2×7
14-child projection fits. The local proposal UI's 1,000-child bound is a separate
current workflow limit. Never silently truncate larger local products. Preserve
exact/most-specific modifier price, weight and dimensions; the characterised
modifier sale-price emission discrepancy needs its own approved decision before
claiming sale-modifier parity. M3A.4 extends the immutable snapshot with projected
variant weight/dimensions without re-running the scanner.

Names/age/messages/dropdowns/checkboxes should normally be storefront form inputs
and line-item properties, not variants. Photo uploads need a verified private file
handling/retention contract; arbitrary uploaded URLs are not production evidence.
Conditional/required input validation and proof consent require theme/server work.
Properties do not authorize additional charges. Paid customisation needs real
priced variants/add-on line items and checkout validation, not a client-set price.
Basic-plan custom apps cannot assume Shopify Functions availability (custom apps
containing Functions require Plus). Do not buy an app or implement that feature now.

### Taxonomy, completeness and controlled updates

The mounted `TAXONOMY_ROOT/registry.json` remains the sole vocabulary/hierarchy:
stable keys, parent keys, aliases, order, category paths, storefront collections,
attributes and scoped terms. Keep its reviewed atomic writer. Local categories can
drive reviewed Shopify collections and nested menu items; Shopify collections
are flat membership resources. Shopify's standard product category is a separate
Shopify taxonomy selection mapped explicitly to existing local intent, not an
automatic match to local category names. Proposed standard-category mapping should
extend the current authority, never create a competing manually maintained file.
Keep remote collection/menu IDs outside JSON; menu eligibility/empty-collection
filtering belongs in a future reviewed navigation plan. Tags remain readable tags.

M3A creates only; existing identities block another Create. An update must read
the retained Shop/product/default-variant identity, compare current managed fields,
show signed scoped field diff, revalidate source/config/remote freshness at Confirm,
record pending operation evidence before mutation, update by retained ID and read
back the complete approved contract. Never create-on-missing, delete-on-failure,
blindly retry uncertain writes or reupload unchanged verified media. Preserve
unmanaged remote fields, stock and sales-channel publication. Local page rendering
should remain immediate; bounded explicit/incremental reads supply remote evidence.

**Single next milestone recommendation: Simple shipping-ready draft + reviewed
update**, one parent per operation, DRAFT only. Reuse existing weight/dimensions;
approve minimal optional shipping/origin fields with inherited defaults, explicit
GB review, typed dimensions metafields and full readback. Update the live retained
draft without a second Create or image replacement. Add bounded versioned operation
evidence using the existing identity table; stop for approval if a schema change is
genuinely required. No Variables, taxonomy/menu writes, rename, scanner optimisation,
inventory quantity changes or bulk publishing. Confirm API scopes required for the
chosen inventory-item mutation before changing installed app permissions.

Then merchandising/SEO/tags/sale fields; Variables/modifiers/media; reviewed
collections/categories/navigation; customisation; orders/proofs/production;
Shipping labels/fulfillment/tracking. Shopify now documents `shippingLabelPurchase`,
but eligibility, shipping terms, required order/fulfillment scopes and
`buy_shipping_labels` permission must be independently confirmed before that later
financial workflow; it is not included in current `read_products/write_products`.

### Theme, naming and eventual Woo retirement

`custom.css :root` already centralises semantic palette/spacing/radii; common
navigation, cards, filterbars, buttons and statuses reuse it. Remaining fixed RGBA
values in header overlays/focus rings/shadows/Intake/relationship controls and
`#F0BBC2` blocking borders would resist a palette swap. Small future work should
tokenise these semantic derivatives, align legacy SCSS/Bootstrap mappings, and
handle fixed SVG/PNG artwork separately. Keep current appearance; Shopify pages
must reuse shared components. No redesign or vendor CSS rewrite.

Names remain: local `woocommerce-dashboard`, GitHub `NEprod/woocommerce-dashboard`,
Docker `neprod/woocommerce-dashboard`, Compose service `app`/container
`woocommerce-dashboard`, Unraid `my-woocommerce-dashboard.xml`, appdata
`/mnt/user/appdata/woocommerce-dashboard/instance`, template/image/support/raw URLs,
brand filenames/page titles/footer, Discord default name and branding assertions.
No tracked CI workflow directory was found; audit any external build automation,
Hub credentials/token repository permissions, repository webhooks, raw asset URLs
and deployment scripts before rename. Local absolute paths may exist in external
IDE/task/build settings; portable catalogue paths must not be rewritten to follow
the repository directory rename.

After shipping-ready Simple acceptance, approve names/availability separately:
`tlc-dashboard`, `NEprod/tlc-dashboard`, `neprod/tlc-dashboard` are proposals only.
First snapshot/backup source, DB and mounts; change visible branding and assertions
in one bounded commit; rename GitHub/update remotes and external integration URLs;
create a new approved Docker repository and publish a unique test artifact while
retaining the old repository/tags; introduce a parallel Unraid template. Keep
existing host appdata/catalogue/output/intake/taxonomy paths, internal container
mounts, port, environment keys and credentials unchanged. Stop old container before
mounting its writable DB in the replacement; never run two writers over that state.
Rename local checkout last with tooling references updated. Test rollback against
backup and prior image; newer schemas may prevent older-image reuse without DB
restore. Do not rely solely on GitHub redirects or delete the previous artifacts.

Eventual removal candidates: `woocommerce_connection.py`, `woo_*` publishing/
preview/live-state/taxonomy/identity services, Woo routes/templates/JS, Woo adapter,
credentials and platform controls. Retain `catalogue_images.py`,
`taxonomy_assignments.py`, registry/writer, metadata resolver, scanner/markers/SKU,
SQLite projection/operations, `utils/discord.py` and source safety. Scanner still
emits Woo-style rows and uses `csv_writer.py`; that is shared catalogue plumbing,
not safe to delete by filename. Capture an immutable recoverable checkpoint before
cleanup. All listed Shopify publishing/customisation/taxonomy/order/proof/
production/shipping/fulfillment gates must pass first. Historical Woo identity and
operation records are not deleted without separate approval.

Official contracts consulted: [InventoryItemInput](https://shopify.dev/docs/api/admin-graphql/latest/input-objects/InventoryItemInput),
[Product input](https://shopify.dev/docs/api/admin-graphql/latest/input-objects/ProductInput),
[variant limits](https://shopify.dev/docs/api/admin-graphql/latest/objects/ProductVariant),
[three options](https://help.shopify.com/en/manual/products/variants/add-variants),
[cart properties](https://shopify.dev/docs/api/ajax/reference/cart),
[Functions plan restrictions](https://shopify.dev/docs/apps/build/functions),
[Shipping label purchase](https://shopify.dev/docs/api/admin-graphql/latest/mutations/shippingLabelPurchase).
These are planning contracts, not evidence of live acceptance for those features.

## Installation and configuration

Choose WooCommerce or Shopify during first administrator creation. An installation
without `instance/commerce.json` defaults to WooCommerce. Connection settings cannot
switch an established installation; activation/migration needs a later reviewed
workflow. No Shopify credentials are required to boot.

Administrators use **Settings → Commerce connection** (`/settings/commerce`) to
enter `shop-name.myshopify.com`, an installed app's **Admin API access token**, and
a pinned API version. Version choices currently cover 2026-01 through 2026-10;
the default is 2026-10. This allowlist needs maintenance as versions retire.
Client secrets and Storefront API tokens are not Admin API credentials. Obtain an
appropriate installed-app token using Shopify's supported authentication flow;
Manual Admin tokens remain operator-managed. M3A.1 additionally supports automatic
Client Credentials acquisition/renewal through Docker/Unraid (below).
Request `read_products` for discovery and `write_products` for M3A.
Connection success reports granted scopes, not readiness for future publishing.

### M3A.2 store domain verification

Connection validation requests the authenticated Shop GID, `myshopifyDomain`,
`primaryDomain.host` and connected `domains.host`. The configured myshopify host
must exactly match one of those authoritative hosts; successful authentication
alone is insufficient. Shopify can retain an original myshopify domain while
another is the storefront primary. The connected-domain list is currently a
deprecated but available GraphQL field in the pinned supported versions; review
its replacement when updating the API allowlist.

New draft mappings use the stable Shop GID to derive their store key. Existing
domain-keyed mappings are found by their persisted Shop GID without rewriting
them, preserving duplicate prevention and recovery across verified aliases.
Reconciliation still rejects a different Shop GID. Changing the configured host
after Preview requires a fresh Preview; signed confirmation remains strict.
No migration or live store mutation is needed for this correction. Live connection
acceptance against the user's two domains remains to be performed in Unraid.

References: [Shop GraphQL contract](https://shopify.dev/docs/api/admin-graphql/latest/objects/Shop)
and [Shopify domain changes](https://help.shopify.com/en/manual/domains/domain-type/change-primary-domain).

**Test connection** never saves. **Test and save** retests before atomic persistence
and leaves the active platform unchanged. A blank token retains the saved token
only for the same domain. Changed settings revisions require reload/review.
Normal authentication, administrator authorization and CSRF protect the route.

Tokens are not redisplayed, logged by this integration or placed in authored JSON.
Saved configuration uses private 0600 `instance/commerce.json`, not encryption at
rest. Protect the instance volume and backups. No extra secret backup is created.
Optional deployment variables are `SHOPIFY_STORE_DOMAIN`,
`SHOPIFY_ADMIN_ACCESS_TOKEN` and `SHOPIFY_API_VERSION`. Any nonempty variable makes
this whole configuration deployment-owned/read-only in the UI; provide a complete
domain/token pair. Compose now explicitly passes all three optional variables
from `.env`; the Unraid template has matching optional fields (token masked).
Blank values preserve Dashboard settings. A partial environment configuration
never borrows saved credentials. Deployment values are not redisplayed on the
connection page; testing is read-only and saving/overwriting them is refused.
Environment configuration does not activate Shopify or change the installation
platform. Credentials remain separate from all product identity records.

## M3A.1 — Client Credentials authentication

Shopify supports this server-side grant for a **Dev Dashboard app installed on a
store in the same Shopify organization as the app**. This fits the intended
TLC-owned app/store deployment only if that organization condition is true;
installation alone is not proof. Live connection acceptance must confirm it.
Apps serving other organizations require a separately scoped authentication flow;
the application does not invent a fallback or OAuth redirect workaround.

In Dev Dashboard create/select the app, configure its version with `read_products`
and `write_products`, release/install the version on your own store, then open
**Settings** for the **Client ID** and **Client Secret**. The Client ID identifies
the app; it is not an access token. An already installed compatible app does not
need reinstalling merely to change TLCDashboard authentication. Scope changes
may require the normal Shopify version/permission approval process.

For Docker/Unraid, set:

```dotenv
SHOPIFY_STORE_DOMAIN=your-shop.myshopify.com
SHOPIFY_CLIENT_ID=<Dev Dashboard Client ID>
SHOPIFY_CLIENT_SECRET=<Dev Dashboard Client Secret>
SHOPIFY_ADMIN_ACCESS_TOKEN=
SHOPIFY_API_VERSION=2026-10
```

Both client fields are masked in the Unraid template. Compose passes them through;
no actual credentials belong in repository files. To switch from manual tokens,
remove/blank the deployment Admin API token, add both client fields and restart
the container. Keep appdata and the selected platform unchanged. For manual-token
mode, blank both client fields and provide domain/token. Both modes present,
partial client pairs, invalid domains or missing deployment credentials are
rejected; no values are combined with saved Dashboard configuration. With all
Shopify environment variables absent/blank, protected manual Dashboard settings
continue to work. Deployment-owned settings can be tested but never overwritten
or redisplayed by the Dashboard.

The existing Shopify transport posts form-encoded `grant_type=client_credentials`,
Client ID and Client Secret to the validated store's `/admin/oauth/access_token`.
It validates `access_token`, `expires_in` and `scope` from Shopify. Tokens live only
in a process-private bounded cache (16 credential/store keys) and are renewed on
demand up to 60 seconds before returned expiry. Short lifetimes use a proportionate
margin. No refresh token is required: renewal repeats the documented grant.
A lock coalesces simultaneous acquisition; failed authentication is held for
30 seconds to avoid storms. Restart discards cached tokens and reacquires on the
next Shopify request; no background scheduler or token persistence is introduced.

The token is attached only to Admin GraphQL requests. Staged binary uploads still
use Shopify's signed upload parameters without Admin tokens/client secrets.
Preview fingerprints use stable credentials, not acquired tokens: normal renewal
does not invalidate a review, but changed credentials still require fresh Preview.
No short-lived tokens are stored in authored JSON, identities, operation evidence
or configuration files. Errors are generic/redacted, responses bounded to 64 KiB
for authentication, connect/read timeouts 5/15 seconds, redirects/proxy inheritance
disabled. Authentication requests are never automatically retried. A GraphQL 401
invalidates the cached token and starts the cooldown; it does **not** replay the
failed read/write. An uncertain draft remains subject to existing reconciliation.

Open **Settings → Commerce connection → Test connection**. This obtains/caches a
token and reads shop identity/scopes; it does not publish. On failure check app
installation, same-organization ownership, credentials, granted scopes and
connectivity. Correct the environment/restart or wait out the cooldown before
another explicit test. Client Credentials are not universal merchant OAuth;
multi-worker deployments have separate caches, while the supported container
continues to use one worker with four threads. Docker administrators can inspect
environment values, so secure container access and backups.

Official contracts: [Client Credentials Grant](https://shopify.dev/docs/apps/build/authentication-authorization/client-credentials-grant),
[access tokens](https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens).

## M2 discovery — implementation awaiting live acceptance

M1 was checkpointed locally at `e2b454bb2a3eef85475fb635a3d85e03576a36dc`.
M2 was checkpointed locally at `9b01d63ca4bdbc3aa175e2112983c9031486da5a`;
neither checkpoint was pushed/released. M3A is source checkpointed locally;
temporary commit-tagged AMD64 image acceptance is separate from release approval.

Open **Settings → Commerce connection → Inspect Shopify catalogue**, or
`/shopify/discovery`. Shopify-selected installations also have a Discovery sidebar
destination. Administrators can inspect a prepared Shopify connection while the
installation remains WooCommerce. GET renders the shell without a remote call;
each CSRF-protected button makes one fixed read query. Test connection, Retrieve
products and Retrieve collections use the same adapter/transport as M1.

Products expose Shopify GID, title, handle, status, plain/escaped HTML descriptions
and option names/values. Inspect a product for variants (GIDs, SKU, price,
compare-at price, selected options and position) and media. Inspect variant media
for its associations. These are remote observations, not local identities or a
promise that matching SKUs authorize linking. No database mapping/import occurs.

Each list/detail connection retrieves at most 20 records per action. Product
detail shows 20 variants and 20 media; each variant initially shows up to five
media IDs and flags further associations. Separate **Next variants**, **Next
product media**, **Inspect variant media / Next variant media**, and **Next
members** controls expose additional pages without hidden fan-out. Pagination
evidence must be complete/advance; missing evidence is not an empty catalogue.
No automatic exhaustive traversal, background refresh or unbounded accumulation.

Media exposes Shopify media GID, content type, processing status, and for images:
file status, MIME type, image ID, actual CDN URL, dimensions, alt text and original
source URL when Shopify returns it. Processing images can have no URL yet. Order
is the returned connection order within the page, with the Shopify cursor—not
an invented absolute position on later pages. Original-source URLs can be signed
and short-lived. Nothing is fetched from those URLs; they are inspection links.

The current MediaImage/MediaImageOriginalSource contract has no separate original
filename field. A decoded URL basename is displayed only as a **hint**, never
proof of the original upload or a local filename. Local path/name/extension
remain unmapped. Shopify may optimize delivery independently of file extension;
the code never substitutes `.webp`, strips URL parameters or reconstructs URLs.
It never downloads, converts, renames, uploads or modifies local image metadata.

Collections expose GID/title/handle/description/sort order and paged product
membership. The still-supported legacy `ruleSet` field identifies automated
legacy rules when present; absent rules are labelled honestly rather than assumed
to prove a manual type in the evolving collection-source contract. Modern source
classification is deferred. No parent/child category tree is inferred. Shopify
menu navigation is a separate API and is not queried here; the local registry
continues to define intended storefront hierarchy. No registry mutation occurs.

`read_products` is required for product, variant, product-media and collection
reads (a granted write_products scope also permits reads, but this milestone
does not need it). No write, inventory, order or navigation scopes are requested.
Live acceptance requires the user's installed-app Admin token, domain, pinned
version and populated/empty-store read tests; mocked tests are not that evidence.

Official discovery contracts:
[MediaImage](https://shopify.dev/docs/api/admin-graphql/latest/objects/MediaImage),
[original source](https://shopify.dev/docs/api/admin-graphql/latest/objects/MediaImageOriginalSource),
[ProductVariant](https://shopify.dev/docs/api/admin-graphql/latest/objects/ProductVariant),
[Collection](https://shopify.dev/docs/api/admin-graphql/latest/objects/Collection).

## Network and safety boundary

Discovery accepts only allowlisted fixed GraphQL **queries**; its HTTPS POST
contains no GraphQL mutation. The separate M3A confirmation service permits only
stagedUploadsCreate, productCreate and productVariantsBulkUpdate. Canonical
myshopify domains only, no redirects, inherited
proxy/netrc disabled, connect/read timeouts 5/15 seconds, response limit 64 KiB for
connection and 1 MiB for discovery, with bounded response iteration. No automatic
retries: HTTP 429 and GraphQL THROTTLED stop with a wait/manual-retry message,
discarding partial evidence rather than looping or hammering the query budget.
Authentication, timeout, HTTP,
rate-limit, malformed evidence and version/domain mismatch errors are redacted and
actionable. Discovery cannot write. M3A does not write inventory quantities,
collections, orders, menus or sales-channel publications.

Woo adapters retain the current publisher path. Shopify-selected installations
guard Woo workflows instead of invoking them with Shopify credentials. Preparing
Shopify settings on Woo does not change existing mappings or publishing.

The recursively immutable local catalogue snapshot reuses resolved filesystem
metadata and projected rows. It preserves local product/child identities, SKU,
type, pricing, image and taxonomy references. It performs no remote reads, source
writes, scanner execution, SKU allocation or ORM autoflush. M3A remote platform
IDs have separate mappings, never authored JSON.

## M3A — reviewed single Simple draft, awaiting live acceptance

Open **Settings → Commerce connection → Review one Simple Shopify draft**, or
`/shopify/drafts`. This administrator-only proof of concept may use prepared
Shopify credentials while Woo remains active. Page GETs make no remote calls.
Preview reads the immutable local snapshot/original images and verifies Shopify
store identity/scopes using reads only. It shows local identity/SKU, description,
price (in Shopify store currency), DRAFT status and ordered original filenames,
extensions, references, sizes and alt text. All projected parent images are
included; there is no image-subset editor in M3A.

Confirmation requires CSRF, explicit acknowledgement and a signed 15-minute
Preview. Source/projection, original byte hashes, credentials and store identity
are rechecked. The existing CatalogueOperation lock/history is reused. A durable
pending Shopify identity is committed **before the first mutation**, and a
create-attempt marker is committed before productCreate. Preview alone never
reserves identities or uploads anything.

Approved migration `0010_shopify_product_identity` follows `0009_woo_live_state`
and adds only `shopify_product_identity`. It stores verified store GID/key/domain,
local product/source identity/SKU, nullable Shopify product GID, operation reference,
reviewed digest, state/verification state and timestamps. SQLite enforces unique
store/local-product, store/source and store/remote-product pairs. Version-1 media
evidence is bounded to 64 KiB in the writer and database; it retains reviewed
local references/hashes, returned media IDs/URLs, processing observations and
recovery stage/error. No credentials or signed upload targets are stored there.
No backfill; existing Woo mappings are untouched. Nullable SET NULL references
preserve the identity reservation if local projection/history rows are removed.

Original supported PNG, JPEG and WebP bytes are read only from authorized catalogue
paths, checked for format/extension consistency, then sent through Shopify staged
multipart upload targets without the Admin token. There is no local conversion,
rename, download or source write. Bounds: one active Simple parent, no child rows,
one to six images, 10 MiB each / 30 MiB total, title 255 characters and description
8,000 characters (also subject to the evidence bound). Signed targets are not
retained. Product creation associates media in reviewed order with reviewed alt
text; the sole default variant then receives SKU/price. A unique operation handle
and correlation metafield distinguish this attempt from arbitrary matching SKUs.

Independent product readback verifies store/product correlation, DRAFT status,
title/description, sole variant SKU/price, media IDs/order/alt and processing state.
Actual returned Shopify CDN URLs are stored unchanged; an upload extension is not
a promise about browser delivery format. READY media with returned URLs yields
verified; asynchronous processing remains partial/processing until explicit read
reconciliation. Strict description comparison may require inspection if Shopify
normalizes submitted HTML; it never silently accepts a mismatch.

Official write contracts:
[stagedUploadsCreate](https://shopify.dev/docs/api/admin-graphql/latest/mutations/stagedUploadsCreate),
[productCreate](https://shopify.dev/docs/api/admin-graphql/latest/mutations/productCreate),
[productVariantsBulkUpdate](https://shopify.dev/docs/api/admin-graphql/latest/mutations/productVariantsBulkUpdate).

### Partial failures and recovery

Known/uncertain creation permanently blocks another create through this workflow,
including after restart or an empty search. There is no automatic write retry,
write-resume, rollback or remote deletion. Failures before productCreate was even
attempted permit a new reviewed operation; orphan staged uploads are not deleted.
Startup marks interrupted operations using the existing recovery contract; durable
Shopify identity reservations survive.

Open the retained identity page and **Reconcile using Shopify reads only**. Known
IDs are independently read. Unknown IDs use the exact unique handle or an explicit
candidate product GID, then require the operation correlation. If creation timed
out before media receipts were retained, the operator must inspect the observed
IDs/URLs/order and explicitly approve that mapping; signed fresh remote/local
digests are checked before adoption. Missing/wrong fields, ambiguous candidates,
failed media or incomplete evidence stay blocked. Correct the retained draft in
Shopify Admin where needed, then reconcile. Historical CatalogueOperation results
are not rewritten by reconciliation; current identity verification is separate.

### Manual acceptance — one real TLC product

1. Back up the persistent app database/instance volume before upgrading to 0010.
   Configure the installed app's Admin token with `write_products` (which also
   permits product reads), domain and supported version through Docker/Unraid or
   protected settings. Test connection; do not change the active platform.
2. Choose one scanned active Simple product with stable SKU, valid price and one
   to six original PNG/JPEG/WebP parent images on the readable catalogue mount.
   Record its local metadata/image hashes and current Woo mapping if applicable.
3. Build Preview at `/shopify/drafts`; review fields, filenames/order/alt and
   price in the store currency. Confirm once with the explicit acknowledgement.
4. Inspect the retained identity/operation and Shopify Admin: one unpublished
   DRAFT, correct SKU/price, original gallery order/alt. Do not publish to channels.
   Use read-only reconciliation if images are still processing.
5. Check verified product/media GIDs and actual returned URLs, unchanged source
   files/SKU/Woo identities, and that a second create is refused. For failures,
   follow the retained identity instructions rather than pressing Create again.

Categories/collections/navigation, controlled attributes, tags, relationships,
inventory quantities, Variable products, updates to existing Shopify products and
full synchronization are deliberately omitted. No extra dependency was added.

## Verification and next milestone

Focused tests use fictional stores/mocked transport, including setup persistence,
credential protection, transport failures, immutable snapshots and targeted Woo
compatibility. Real Shopify connectivity remains unverified until the user supplies
their own installed-app credentials and performs the read-only connection test.

Next: live acceptance of M3A's single Simple draft, then a separately scoped
reviewed update/variant publishing milestone; do not reuse Woo IDs or reconstruct
image URLs. Deferred:
inventory, orders, navigation editing/sync,
customisation fields, variant-media mapping, webhooks, merchant OAuth and reviewed
platform switching. Client Credentials renewal is implemented in M3A.1; other
merchant OAuth/token lifecycle flows remain deferred. Only the approved M3A
identity migration is introduced; M3A.1 adds no migration.

Official contracts: [GraphQL Admin API](https://shopify.dev/docs/api/admin-graphql),
[API versioning](https://shopify.dev/docs/api/usage/versioning),
[access scopes](https://shopify.dev/docs/api/usage/access-scopes), and
[authentication](https://shopify.dev/docs/apps/build/authentication-authorization).
