# Variant dimensions, existing navigation metadata and honest completion

## Confirmed reporting defect and correction

The Simple create/update and Variable finalisers required `package_state == 'none'`.
`shopify_package_assignments.apply()` correctly returned `accepted` after a durable,
matching InventoryItem acknowledgement, so an otherwise complete operation became
Partial. Discord then unconditionally appended the readback limitation as deferred
work. This was not evidence of a failed assignment.

Finalisers now accept `none` or `accepted`. Notification classification checks every
reviewed assignment against its exact InventoryItem/package receipt. Completed
assignments are **Completed — Shopify Accepted**, never independently API verified.
Rejected inputs remain Failed, missing acknowledgements remain Partial, and pending/
uncertain writes remain Recovery Required. Native authored targets still require
independent size evidence; enabling the adapter alone does not invent a size target.
Missing definitions/access/values remain genuine reviewed differences. Product
freshness and package evidence remain separate. No receipt, checkpoint or timestamp
is fabricated. Existing terminal-event deduplication remains; reads do not send a
new warning for each product check.

Progress and retained identity pages distinguish acknowledgement from independent
readback. Operation status uses the existing `succeeded`/`partial` states; no migration.
Historical warning events remain history. Only the existing complete-readback settling
path can resolve an earlier operation, and only with its matching accepted receipts.
No bulk history rewrite is required.

## Finished dimensions: support versus actual remote evidence

The existing path is unchanged:

`product_info.json → resolved_metadata → apply_variation_modifiers → scanner rows →
variant projection → signed Review & Sync → productVariantsBulkUpdate → read_variable`.

Original `dimensions` and modifier `dimensions` are **millimetres** (not cm or parcel
size). Explicit `finished_dimensions` has its own declared mm/cm/in unit. Effective
values use `packaging_contract.finished_mm`. Price/weight calculations, child SKUs,
option identities and GIDs are unchanged.

Simple shared sizes use PRODUCT-owned `tlc_dashboard.product_length`, `product_width`,
`product_height` with Shopify type `dimension`. Variants receive their own effective
values on PRODUCTVARIANT. When sizes differ, no incorrect generic parent size is
proposed; historical remote parent values are not automatically cleared. Existing
`shopify_dimension_metadata` independently reads each required owner definition,
identity/type and PUBLIC_READ, and proposes creation/access correction separately
from matching values. No duplicate definition or extra formatted size is introduced.

Synthetic tests prove both Variable Collection and Single Variable modifier sizes,
missing remote values, access drift, stable identities and idempotent second reviews.
That is not evidence that a particular merchant product has already been published.

### Merchant evidence observed on 20 October 2026

Read-only Safari inspection of the authenticated merchant interfaces established:

* Personalised Hero Print is product `16012561056127`, SKU `HERO-0001`, with nine
  existing Size × Style variants. The parent PRODUCT finished-size fields show Add
  (empty); that is appropriate for differing sizes, not proof of a publisher defect.
* A4 / Hero A, variant `58780533916031` (`HERO-0001-2`), has finished length297,
  width210, height1 **mm** in Shopify Admin. Its shipping pane shows packed21×4×4cm,
  weight100g and TLC A4 Poster Tube. These are Admin UI observations, not public API
  assignment readback or a new mutation receipt.
* All nine variants were individually read in Admin and have the expected dimensions
  below. All three PRODUCTVARIANT definitions have type Dimension and their
  “Storefront API access” checkbox enabled. Definition IDs: length484277158271,
  width484277322111, height484277125503; keys are the existing `tlc_dashboard.product_*`.
  This is UI evidence of public access, not a new authenticated GraphQL response.
* The shared collection editor independently displays A5 210×148×1mm, A4 297×210×1mm,
  A3 420×297×1mm in indexed effective variations. The modifier Length/Width/Height
  columns therefore describe finished millimetres. Its separately authored packed
  controls show A5 210×148×1mm, A4 210×40×40mm, A3 297×40×40mm and separate selections.
* Copy of Tinker draft theme `201872474495` has a Text block, “Dimensions when
  complete”, using the TLC finished width/length/height dynamic sources. The simple
  card preview displays155mm×155mm×3mm. The merchant observed the wall print display
  `× ×`. A4's remote values are present, so changing their publisher to fill a generic
  parent would hide the owner-binding problem and give other sizes incorrect values.

### Actual variant matrix (Admin UI, not synthetic fixture or mutation evidence)

All finished values below are PRODUCTVARIANT-owned, length×width×height in mm,
with the shared public definitions above. The three Hero A and A4/Hero B shipping
panes were also read; “not read” is deliberately not inferred from another style.

| SKU | Size / style | Shopify variant ID | Actual finished mm | Actual packed cm | Actual assigned package shown in Admin |
|---|---|---|---|---|---|
| HERO-0001-1 | A5 / Hero A |58780533653887|210×148×1|21×14.8×0.1|TLC C5+ Board Back Envelope|
| HERO-0001-2 | A4 / Hero A |58780533916031|297×210×1|21×4×4|TLC A4 Poster Tube|
| HERO-0001-3 | A3 / Hero A |58780533948799|420×297×1|29.7×4×4|TLC A3 Poster Tube|
| HERO-0001-4 | A5 / Hero B |58780533981567|210×148×1|Not read|Not read|
| HERO-0001-5 | A4 / Hero B |58780534014335|297×210×1|21×4×4|TLC A4 Poster Tube|
| HERO-0001-6 | A3 / Hero B |58780534047103|420×297×1|Not read|Not read|
| HERO-0001-7 | A5 / Hero C |58780534079871|210×148×1|Not read|Not read|
| HERO-0001-8 | A4 / Hero C |58780534112639|297×210×1|Not read|Not read|
| HERO-0001-9 | A3 / Hero C |58780534145407|420×297×1|Not read|Not read|

For **every row**, InventoryItem GID, central Package GID and durable assignment
receipt were not collected from the private deployment database during this
inspection; the command below retrieves them with exact identity checks. Package
independent public API readback remains unsupported for every row. Browser inspection
is Admin UI evidence, not a persisted merchant-confirmation record or proof of the
original mutation's source revision. Reconcile any discrepancy through signed review;
do not rewrite values simply because display is blank. The observed shipping weights
for Hero A are50g/100g/150g for A5/A4/A3; no weight change was made.

No theme/product/definition save or release was performed. No development merchant
API credentials are configured. InventoryItem GIDs, variant receipts and
fresh API definition/native evidence must be collected with the read-only commands
below rather than inferred from UI values or fixtures.

## Draft-theme correction (separate approval; no theme edit in this release)

Inspect the current product-context dynamic-source expression. A PRODUCT source
cannot display differing PRODUCTVARIANT values. Keep simple products bound to their
existing product metafields. For multiple-size products use the selected variant's
same three keys, not a new formatted metafield and not packed shipping measurements.

Illustrative Liquid for a separately approved draft block:

```liquid
{% assign size_owner = product %}
{% unless product.has_only_default_variant %}
  {% assign size_owner = product.selected_or_first_available_variant %}
{% endunless %}
{% assign length = size_owner.metafields.tlc_dashboard.product_length %}
{% assign width = size_owner.metafields.tlc_dashboard.product_width %}
{% assign height = size_owner.metafields.tlc_dashboard.product_height %}
{% if length != blank and width != blank %}
  <p>Dimensions when complete: {{ length | metafield_text | escape }} ×
    {{ width | metafield_text | escape }}{% if height != blank %} ×
    {{ height | metafield_text | escape }}{% endif %}</p>
{% endif %}
```

This is initial-render logic, not a claim of reactive variant support. A draft
implementation must put this in the product section subtree actually refreshed on
variant selection, using the installed Tinker/Horizon section-rendering contract.
If that subtree is not refreshed, integrate a small block with that exact theme's
variant-change lifecycle and server-rendered per-variant data; do not assume a
universal `variant:change` event, rely on product JSON containing metafields, or
poll the Admin API in a storefront. Test A5→A4→A3, style changes, deep links, back/
forward, mobile and unavailable combinations. Guard absent fields to avoid `× ×`.
Do not use stale historical parent metadata as an automatic fallback for variant
sizes; the publisher already resolves legitimate inherited dimensions onto variants.

Ordinary product-context Text dynamic sources do not by themselves establish a
selected-variant reactive binding. Prefer a compatible existing block where the
installed theme actually supports it; otherwise a small draft-only block change is
needed. Live theme and app extension remain unchanged.

Official references: [Liquid metafields](https://shopify.dev/docs/api/liquid/objects/metafields),
[selected variant](https://shopify.dev/docs/api/liquid/objects/product),
[variant support](https://shopify.dev/docs/storefronts/themes/product-merchandising/variants),
[compatible dynamic sources](https://shopify.dev/docs/storefronts/themes/architecture/settings/dynamic-sources).

## Existing navigation metadata matrix (schema audit, not invented merchant fields)

Actual vocabulary comes from the configured registry; Occasion/Recipient/etc. are
not hardcoded or newly authored here. The local development registry/mapping DB is
not the merchant's current Unraid data. Read-only Safari inspection of the deployed
registry confirms the following actual vocabulary. Run the diagnostic for fresh
per-product names,
expanded app namespace, existing keys, values and access. Existing policy already
offers reviewed access correction; no new duplicate mapping is needed.

| Existing authored field | Effective source/local type | Existing Shopify destination | Theme use / access |
|---|---|---|---|
| `categories` | Shared + additive child values; registered hierarchy | Reviewed native manual Collection memberships/ancestors; standard category through existing mapping | `product.collections`; Liquid, compatible collection/reference block if supported; not a duplicate text metafield |
| `primary_category` | Effective stable assigned registry key | Existing standard taxonomy mapping policy | Native product category where exposed by Liquid/theme; collection membership remains separate; do not invent a custom category field |
| `storefront_collections` | Inherit or explicit child replacement/list | Native Collection memberships | `product.collections`, compatible existing block or Liquid; theme must choose which memberships to show |
| `attributes.<actual registry name>` informational | Effective shared/product terms; stable registry keys | PRODUCT `$app:tlc_attributes.tlc_<first20 SHA256(attribute key)>`, `list.metaobject_reference`; retained expanded namespace and referenced term `name` fields | Existing public policy requires both field and term-definition PUBLIC_READ; `.value` list in Liquid; compatible dynamic sources depend on block/`name` field. Private/internal roles stay private, not forcibly public |
| `attributes` selected by `variation_attributes` (legacy driver fallback preserved) | Existing ordered options and child attributes | Native product options / variant selectedOptions, not duplicate informational fields | `product.options_with_values` / selected variant `.options`; reactive display requires theme lifecycle |
| `tags` | Existing merged authored strings | Native product tags | `product.tags` Liquid; direct dynamic-source selection depends on compatible block; no invented list metafield |
| `shopify_product_type` | Effective explicit scalar | Native `productType` | `product.type` Liquid / compatible theme source |

Attribute `browse_role`/`browse_parent`/`browse_group` and public-intent policy live in
the existing registry, not another product-specific navigation configuration.
Destination/both roles drive existing native collections; informational assignments
reuse controlled term references. Structured lists are not flattened into CSV text.
No category, collection, menu, term or source JSON is changed to implement display.

For product details, use the theme editor's dynamic-source picker only if it offers
the actual product-owned field and compatible referenced `name` values. Otherwise
use a narrowly scoped Liquid block iterating the existing list, e.g.
`product.metafields[verified_expanded_namespace][existing_key].value`, then each
entry's `name.value`. Never guess the expanded namespace from Client ID. Native
tags/options/collections do not need new metafields just to be displayed. The
merchant's exact block compatibility is an acceptance check, not assumed support.

### Actual merchant registry observations

The deployed Attributes & Tags workspace was read without pressing reconciliation
or saving. These are its actual11 attributes, not illustrative new variables:

| Authored JSON field | Existing remote registration shown locally | Storefront access evidence |
|---|---|---|
| `attributes.Occasion` | PRODUCT MetafieldDefinition474344522111; controlled reference list | Retained observed PUBLIC_READ; exact key `tlc_c7313f93702c0adcff1b`, namespace `app--434537660417--tlc_attributes` |
| `attributes.Recipient` | PRODUCT MetafieldDefinition474342621567; controlled reference list | Fresh access not collected |
| `attributes.Age / Milestone` | Defined locally; not registered in usage workspace | Not established; register only on approved actual use |
| `attributes.Personalisation` | PRODUCT MetafieldDefinition474344620415; controlled reference list | Fresh access not collected |
| `attributes.Material` | PRODUCT MetafieldDefinition474344423807; controlled reference list | Fresh access not collected |
| `attributes.Production Method` | PRODUCT MetafieldDefinition474344718719; controlled reference list | Fresh access not collected |
| `attributes.Style / Theme` | Defined locally; not registered in usage workspace | Not established |
| `attributes.Build Type` | Defined locally; not registered in usage workspace | Not established |
| `attributes.Design Style` | Defined locally; not registered in usage workspace | Not established |
| `attributes.Angel Wings` | Defined locally; not registered in usage workspace | Not established |
| `attributes.Outer Detail` | Defined locally; not registered in usage workspace | Not established |

Each follows the existing effective shared/product inheritance and list-of-controlled-
references mapping **when informational**; when selected as a variation driver on a
particular product it follows native options instead. All use the same theme
compatibility rules in the schema table above: existing list-reference `.value` in
Liquid, direct dynamic source only when its block supports the referenced `name`
field. Do not force internal public policy or automatically create all11 definitions.
The namespace above is copied from retained Shopify evidence, not inferred from
Client ID. Cached “verified” registration does not prove a current product assignment
or fresh term-definition access. For Occasion, the stored Admin-filter index was
IN_PROGRESS despite an enabled flag; that is not a missing storefront value or a
reason to repeatedly rewrite access. No new metadata mapping was necessary.

## Read-only Unraid verification matrices

In the existing container console after upgrading (substitute the exact product SKU):

```sh
python -m app.shopify_attribute_diagnostic --sku HERO-0001 --variant-matrix
python -m app.shopify_attribute_diagnostic --sku HERO-0001 --navigation-matrix
python -m app.shopify_attribute_diagnostic --sku 16bit-0001 --navigation-matrix
```

These commands open the existing `/app/instance/site.db` read-only without starting
the Flask factory, scanner, scheduler or workers. No source/receipt/checkpoint/Shopify
writes. The variant command is bounded to the existing32-variant review limit and
validates exact shop/correlation/retained variant/SKU/options/InventoryItem identities.
It returns one row per variant, effective finished metadata, actual values and
definition/access actions, only-authored packed sizes, opt-in2027-01 native evidence,
central package link and receipt-target agreement. Disabled native opt-in is reported
as not read, not a proven mismatch or success. Package API readback remains unsupported.
This metadata-only diagnostic does not establish gallery freshness or require a
publishable full gallery just to inspect dimension fields; ordinary publisher/media
guards are unchanged.
The navigation command uses existing reader/review planners for actual product terms,
public policy, destinations and access actions; it applies none of them. Scope or
read failures remain failures rather than an empty supposedly matching product.

Share only this catalogue evidence if further diagnosis is needed; no credentials
or customer/order data. Unknown live values must stay unknown until observed.

## Operational acceptance and rollback

1. Back up SQLite appdata and catalogue JSON; retain the prior immutable Docker image.
   Pull the new immutable development tag, recreate with unchanged mounts/secrets/API
   opt-in. Migration head remains0018_orders_webhooks; no app/theme/scope release.
2. For HERO-0001 compare all nine variant rows. Resolve any real missing value or
   definition/access through normal signed Product Review & Sync, never bulk writes.
   Keep original finished sizes and independent prepared sizes separate.
3. Inspect one deliberately approved package assignment. Result should be Completed —
   Shopify Accepted, informational Discord, no outstanding required stage solely for
   unsupported readback. Review again: no repeated write. Wrong IDs, permission denial,
   lost acknowledgement and actual native/metadata mismatches must still need action.
4. Run bounded read-only product reconciliation; readable fields retain genuine
   freshness checks. Check Orders and separate same-SKU personalisations remain visible.
5. Separately authorise draft-theme work and test sizes before any theme publication.
   This backend release alone does not fix the draft or live block's owner binding.

Manual “Confirm in Shopify Admin” recording is deferred: acceptance does not require
it, and an explicit receipt-bound administrator/CSRF confirmation lifecycle deserves
separate approval rather than inferring a new evidence type from a browser visit.
No merchant-confirmed database state or API-verified assignment is fabricated here.

The deployed task banner also exposed existing gallery-bound (53 images versus20)
and Draft/Archived-versus-channel-intent diagnostics. These are retained task/UI
observations, not newly executed production reconciliation. They are genuine separate
conditions to inspect on the affected products; this completion fix does not suppress
them, truncate media, reset checkpoints or change publication intent.

## Test / release gate

425 unique focused/bounded Python cases were covered across targeted runs, reusing
unchanged evidence and rerunning affected completion/diagnostic cases. The stale
taxonomy upgrade test that hardcoded0012 now checks the actual migration head0018;
no migration source changed. 27 JavaScript cases include acceptance-only progress,
single POST, reconnect/dedup and mobile Products state. Local synthetic browser review
and acknowledgement receipts were checked at desktop and mobile width with no page
overflow; this is separate from authenticated read-only Admin observations above.

Release requires `python3 tests/docker_variant_sync_smoke.py IMAGE`: reuse the
existing network-isolated0017→0018 upgrade, two non-root workers, sparse save/scan/
reopen, Orders answers, central package IDs, receipts and restart checks, plus exact
accepted/rejected/uncertain/mismatched-target completion assertions inside the image.
Do not publish an untested build or describe those fixture checks as merchant writes.

Rollback application image only with the retained compatible0018 database and mounts.
Do not restore an old database over subsequent Orders imports. No remote writes or
destructive local data conversion occurred during this milestone.
