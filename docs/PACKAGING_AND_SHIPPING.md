# Packaging & Shipping — M3A.9.5.4 (final reduced scope)

**Current package simplification:** the [Shipping Packages guide](SHIPPING_PACKAGES.md)
supersedes historical protection/capacity/physical-verification requirements below.
Those values are historical data only, not active controls or recommendation gates.
Solo recommendations check basic size: internal axes, otherwise exterior bounds
explicitly estimated/unverified, and the C5+3mm exception. No carrier approval,
shipping-rate calculation or physical fit certification is implied.

**Current reduced manager scope:** automatic catalogue reconciliation/sync was removed.
Settings keeps the six local records and manually entered Shopify Package IDs. The
normal reviewed product publisher can submit2026-10 InventoryItem assignments and
record acceptance only; independent readback/automatic clearing remains unavailable.
No default/rate/checkout change. [SHIPPING_PACKAGES.md](SHIPPING_PACKAGES.md) supersedes
the intermediate reconciliation description immediately below.

**Current package catalogue administration:** Settings → Shipping Packages reuses
these records and forms, with unit controls and signed merchant-assisted reconciliation.
Remote listing/readback has not been verified; Sync Preview is manual/skipped, not
an automatic Shopify update. Six identities, usable-space rules and all product
recommendations remain unchanged. See [SHIPPING_PACKAGES.md](SHIPPING_PACKAGES.md).

**M3A.9.5.6 correction supersedes the historical dimensional/reuse policy below:**
original `dimensions` is finished-product millimetres, not legacy data. Only explicit
packed measurements publish natively, even with the old reuse flag. Independent
mm/cm/in, modifier-aware copy and historical repair are in [PACKED_DIMENSIONS.md](PACKED_DIMENSIONS.md).
Six packages, optional recommendations, no rates/checkout calculations remain unchanged.

TLCDashboard manages **optional solo packaging** and independent finished/prepared
measurements. Shopify handles checkout, its native multi-item package selection
where supported, Royal Mail Tracked 24/48, and labels. This milestone does not change
any live shipping profile, default package, carrier, app scope, order or theme.
No basket planner, simulator, rate tables, CarrierService or public callback remains.

## Catalogue and migration

Migration **0017_packaging**, after deployed **0016_shopify_orders**, creates only
`packaging_package`, `packaging_link`, `packaging_evidence`. UUIDv5 seed identities
make the six initial local definitions stable across clean installs; names are not
identities. No source JSON is rewritten. Existing operational links survive local
renames/archives. Packages created later use UUIDv4. Startup does not reseed duplicates.

| Package | Proposed/advertised exterior (cm) | Unconfirmed |
| --- | --- | --- |
| TLC C5+ Board Back Envelope | 24 × 16.5 × unknown | Interior, thickness, tare |
| TLC Large Letter Box | 35.3 × 25 × 2.5 | Physical exterior, interior, tare |
| TLC Small Parcel Box | 45 × 35 × 16 | Physical exterior, interior, tare |
| TLC Medium Parcel Box | 61 × 46 × 46 | Physical exterior, interior, tare |
| TLC A4 Poster Tube | 24 long, diameter 5 | Physical exterior, usable length/diameter, end caps, tare |
| TLC A3 Poster Tube | Unknown | All measurements, end caps, tare |

All seeds are **Measurements needed**, unlinked, not automatic-fit verified. Empty
weights are unknown, not the previous provisional 40 g. C5+ alone starts with a
merchant `max_contents_thickness_mm: "3"` rule. It is not a universal Royal Mail
limit and is not applied to the Large Letter Box.

Use Packaging → Add/Edit/Duplicate. Enter exterior and independently measured usable
interior, tare, capacity, protective clearance/material weight, suitability and cost
in integer GBP pence. Unknown is never zero. Review Usage before archiving; archive
does not remove authored references, retained links or any Shopify package. Archived
packages are excluded from new recommendations. No remote deletion or default action.

The unreleased 0017 was simplified before publication. Do not deploy an intermediate
development 0017 database; the supported upgrade starts at deployed 0016. Normal
startup makes a verified backup under the existing instance/appdata backup directory.
Restore the pre-upgrade database with the previous image for rollback; destructive
downgrade is intentionally refused. Existing mounts/Orders data remain unchanged.

## Three independent measurement meanings

1. `dimensions` retains historical millimetre semantics and modifier calculations.
   It is **not silently reclassified** as finished size or packed size.
2. New optional `finished_dimensions` explicitly confirms customer-received size:

   ```json
   {"finished_dimensions":{"length":"6","width":"6","height":"0.1","unit":"in","label":"folded"}}
   ```

   Units are `mm`, `cm`, `in`; Decimal conversion, 25.4 mm/in. A4 example29.7×21cm,
   A3 example42×29.7cm; thickness is product-specific, never invented. Shared
   collection → product → exact/most-specific variation modifier replaces this
   complete object (unit travels with measurements). Missing inherits. `{}` clears
   explicit finished interpretation locally and restores historical compatibility;
   it does not authorise remote dimension deletion. Missing individual axes are not
   managed/cleared remotely; inspect existing values before using them in a theme.
3. `packaging.packed_dimensions_cm` is the measured **prepared individual item**,
   including its individual sleeve/protection, **not the selected outer package**.
   It is independent of finished size. Do not count an outer box/tube twice.
   A measured roll must have its own axial length and diameter; flat A3 geometry
   cannot establish rolled size. Changing finished size never changes packed size;
   the editor prompts the merchant to review shipping measurements separately.

Example optional shipping configuration:

```json
{"packaging":{
  "packed_dimensions_cm":{"length":"16","width":"16","height":"0.3"},
  "weight_g":"50","measured":true,"method":"flat","orientation":"flat",
  "classification":"flat","fragile":false,"rollable":false,
  "clearance_cm":"0","protection_weight_g":"0","requirements":["rigid backing"],
  "selected_package_id":null
}}
```

Zero clearance/protection weight is only accurate when already included or unnecessary;
do not copy example values into real products without measuring. `requires_shipping`
keeps the existing root field/default convention; no competing flag is introduced.
Optional `weight_g` overrides the historical grams for shipping only, including at
variant level; pricing/scanner modifiers and existing stored weights are not rewritten.

`packaging` merges recursively across collection/product then the existing exact or
longest-match variation expression. Explicit false and `selected_package_id:null`
survive. A new packaging/finished-only modifier cannot mask historical price/weight
rules. Historical empty exact modifiers retain their established semantics.

## Optional selection and recommendations

* Missing `selected_package_id`: inherit, or no selection when no parent value.
* Explicit null: **No Package Assigned**, do not inherit parent selection.
* UUID: explicit local package; `assignment_kind:accepted` or `manual` records how
  chosen, with optional `override_reason`. Inherited UUID remains distinguishable.

Recommendation is advisory, never automatically accepted. Use Recommendation, select
a different package, inherit or choose No Package Assigned. A manual choice survives
unrelated price/SEO edits. No package does not block save, create, weight, native
packed size or normal Review & Sync and does not alone cause Needs Attention.

Solo checks use **prepared packed size** only, Decimal geometry, permitted fixed/flat/
any orientation, clearance on each face, materials, fragility and weight capacities.
They require measured item/package dimensions and tare/protection/capacity evidence;
missing measurements return an actionable warning, not an optimistic fit. Soft-pack
deformation remains manual. The C5+ cap uses actual packed thickness before orientation:
2/3mm may fit subject to all other checks;3.1/4mm and unknown thickness cannot qualify.
Large Letter Boxes use measured interior/protection, not the C5+ cap.

Tubes require explicit `method:rolled`, `rollable:true`, measured prepared roll,
approved rolled suitability and measured interior length/diameter. Length is the
roll axis; usable length minus total end-cap reserve must fit it; the maximum measured
cross-section/diameter plus protection must fit the usable diameter. Flat prints are
never auto-rolled. Confirm a circular prepared roll; these fields do not authorise
bending a rigid item. No A3 tube dimensions are inferred from its name.

Rank safe candidates deterministically by practical external size then known packaging
cost/name/UUID; return a recommendation and at most two alternatives. No global-optimum,
carrier-eligibility or postage-price claim. Rejections explain measurements/suitability.

Intake, Product Editor and variant modifier rows reuse the same components. Advanced
JSON shows actual authored fields. Inline Add Package uses one modal, saves only the
local package, preserves unsaved product state/focus/scroll, refreshes choices and
recommends again. **It does not force selection.** Cancel/Escape leaves the draft alone.

## Ordinary Shopify Review & Sync

Save Locally → existing targeted scanner projection → Review & Sync → signed confirm
→ independent readback. No new product-packaging queue/publisher, no publish-on-save.

* Explicit finished size uses existing configured namespace (default `tlc_dashboard`)
  `product_length`, `product_width`, `product_height`, Shopify `dimension` type on
  Product / ProductVariant. Exact source units remain authored, API values normalize
  to millimetres. Existing IDs and ordinary metafield difference/readback rules remain.
  With no explicit finished object, legacy metadata publishing is unchanged.
* Weight / requiresShipping / origin use normal2026-10 inventory-item handling.
* Opt-in native packed size stays **2027-01 only**. Set
  `SHOPIFY_NATIVE_DIMENSIONS_ENABLED=true` and retain `write_inventory` plus read access.
  Explicit packed cm requires `measured:true`; complete independent capability,
  identity, signed review, readback and uncertainty protection remain mandatory.
  `SHOPIFY_PACKED_DIMENSIONS_USE_PRODUCT_DIMENSIONS` is retired and ignored;
  there is no finished-to-packed fallback. Explicit unit-aware packed dimensions
  or compatible packed cm are required; incomplete/unmeasured values stay outstanding.
* Optional solo UUID is not sent as a fake Shopify ID. Selected/explicitly cleared
  packages show a manual-operation warning without blocking supported field updates.
  A local clear is not proof that a prior remote assignment cleared.

### Package API limitation and actual evidence

Official2026-10 schema documents `shippingPackageUpdate` and write-only
`InventoryItemMeasurementInput.shippingPackageId`; the public returned measurement
does not establish a matching package association readback. Mutation presence alone
does not make an update safely verifiable. Package listing/creation/reset and direct
association readback are **not enabled** without a tested authenticated contract.
The explicit read-only capability probe reports schema names, store, scopes and time
using the existing transport, never writes. No new permission is added/requested.
Missing shipping scopes are reported, not silently broadened.

The16October authenticated Admin UI audit observed the old seven TLC packages and
6×6 Envelope default with Tracked24/48. That is historical, not proof of today's six
replacements or any remote package GID. No configured merchant Admin token was
available locally for a fresh authenticated package API import in this release.
No actual remote package IDs have been invented or linked. The seed list is **local
initial configuration, not remote reconciliation success**. The previous saved
packages and oversized A3 test package must be inspected by the merchant; no automated
archive/remote overwrite occurs. Retained mapping storage is ready but new links are
blocked until actual remote identity/readback is obtainable.

Manual: Shopify Admin → Settings → Shipping and delivery → Packages. Inspect existing
records/default before creating anything. Match exact type and physically verified
measurements; do not recreate similarly named records. Create only missing packages
using copyable local measurements. For a product/variant, use its Shipping section's
package selector if available. Verify manually there, including clearing an assignment.
Do not expose Unraid, change profiles, default, Royal Mail services or permissions.

### Customer-facing finished size

This is a **data foundation**, not an activated storefront feature. Shopify Liquid
supports `dimension` metafields and `metafield_tag`; supported theme text settings can
connect compatible product dynamic sources after definitions/setup. A merchant can
use a Custom Liquid block on **unpublished** Tinker for axis display, for example:

```liquid
{% assign size_owner = product.selected_or_first_available_variant %}
{% unless size_owner.metafields.tlc_dashboard.product_length %}{% assign size_owner = product %}{% endunless %}
{% if size_owner.metafields.tlc_dashboard.product_length %}
  Finished size: {{ size_owner.metafields.tlc_dashboard.product_length | metafield_tag }}
  × {{ size_owner.metafields.tlc_dashboard.product_width | metafield_tag }}
{% endif %}
```

Only label historical values as Finished after the merchant confirms explicit finished
semantics. Configure the actual namespace if changed. Size labels (A4/folded) remain
authored/editor previews; they are not separately published in this version. Dynamic
variant switching and exact Tinker layout require a separate theme acceptance check;
the example alone does not prove live updates. Storefront API access is a separate
definition `PUBLIC_READ` decision, not implied by Admin readback/Liquid. No definitions
or theme were changed, no extension version was necessary or staged, and no live
storefront dimension display is claimed.

References: [native packed API](https://shopify.dev/changelog/posts/manage-packed-product-dimensions-with-the-admin-graphql-api),
[package update](https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/shippingPackageUpdate),
[Liquid dimension](https://shopify.dev/docs/api/liquid/objects/metafield),
[theme dynamic sources](https://shopify.dev/docs/storefronts/themes/architecture/settings/dynamic-sources),
[Storefront API access](https://shopify.dev/docs/storefronts/headless/building-with-the-storefront-api/products-collections/metafields).

## Acceptance / rollback

### Offline verification recorded

Final focused package/schema/publishing/native pass: **80 passed**. Bounded regression:
**402 passed**, one stale supported-field inventory assertion updated for the two new
fields, then its complete affected file **23 passed**. JavaScript bounded pass:
**67 passed** (the final package-only rerun also8 passed). Fixtures cover ordinary
signed Create/Update/No Change, retained variant/inventory IDs, separate finished and
packed values, no-package save, targeted scan/SKUs, tubes and3mm boundaries. No live
Shopify or Discord calls. In-app browser desktop/mobile checks confirmed independent
finished-size preview, advisory fit, explicit acceptance/clear, inline save/cancel
retaining an unsaved title and no horizontal overflow. Real mobile Safari and active
storefront size display have not been verified. The release also requires the
offline two-worker migration/restart script before publication; its actual result
and image identity are recorded in the completion report, not inferred here.

1. Back up existing appdata/SQLite consistently. Select tested `develop` or immutable
   `shopify-packaging-<commit>`; retain all existing mounts, environment and credentials.
2. Update manually, confirm0017 and Orders/history present. No auto-deployment.
3. Open Packaging: six unverified seeds, no invented tare/interiors. Inspect actual
   Shopify packages manually; do not duplicate old records or alter default.
4. Measure a fictional/test solo package and product: verify2/3mm C5+ acceptance,
   3.1mm rejection, larger-box fit and measured rolled tube checks.
5. In an existing Draft editor keep an unsaved title, open/cancel/save an inline
   package; verify the title survives and no package is automatically selected.
6. Save with no package, then with a local choice, then null overriding a shared
   choice. Confirm JSON, targeted rescan/SKUs, ordinary Preview warning and unchanged
   remote assignment unless manually changed. Review supported fields independently.
7. On an explicitly reviewed Draft, confirm separate finished/packed values, shipping
   weight and readback through normal product review; do not publish Live/channel
   changes inadvertently. No live product mutation was made by automated tests.
8. Confirm historical dimensions/legacy policy and variable modifiers remain stable.
9. If testing size display, use unpublished Tinker only and verify selected variant
   changes before any separate theme approval. Do not claim live visibility yet.
10. Rollback image plus pre0017 database backup together. No Shopify rollback needed
    for this release: it performs no automatic shipping changes. Later reviewed
    product mutations require their own reviewed reversal, never blind retry.
