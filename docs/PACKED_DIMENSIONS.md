# M3A.9.5.6 — Finished and packed dimensions correction

## Confirmed cause and preserved contracts

`shopify_native_dimensions.desired()` and `shopify_variables.packed_policy()`
fell back to the original finished `dimensions` (or indexed variant axes) when
`SHOPIFY_PACKED_DIMENSIONS_USE_PRODUCT_DIMENSIONS=true`. The0017 independent
packed-size branch took priority but did not remove that fallback. This explains
the unsafe mapping in both Simple and Variable publishing; it is not a scanner,
SKU, pricing or dimensional-variation defect.

Original `dimensions: {length,width,height}` remains finished millimetres, including
the existing exact/most-specific `variation_modifiers` calculations. Existing
price/sale/weight modifiers, variation generation, source identities, SKUs and
Shopify mappings are unchanged. No catalogue JSON is rewritten on upgrade.
The optional `finished_dimensions` unit-explicit field from0017 remains readable;
an original size-changing modifier takes precedence over a parent-only alias.
An explicit per-variant finished alias can still override that variant's size.

## Authored shipping contract

Use the existing `packaging` object, not a second product database:

```json
{
  "dimensions": {"length":420,"width":297,"height":1},
  "packaging": {
    "packed_dimensions": {"length":"31","width":"4","height":"4","unit":"cm"},
    "measured":true,
    "method":"rolled",
    "rollable":true,
    "orientation":"fixed",
    "fragile":false,
    "clearance_cm":"0",
    "selected_package_id":null
  }
}
```

These values are fictional, not measured TLC packaging instructions. Never infer
a tube diameter from flat artwork. Supported size units: `mm`, `cm`, `in` (25.4mm
per inch). The previously authored `packed_dimensions_cm` remains supported without
conversion/rewrite. Author only one representation in any layer. Unit-explicit size
is one atomic object so a child unit cannot reinterpret inherited axes. Existing
centimetre sparse axes remain compatible. Missing values are not zero.

Collection → sparse product packaging → matching broad-to-specific packaging
modifier patches. A specific price-only rule cannot hide a Size packaging rule;
a specific shipping exception may override it. Price/weight modifier selection
itself is untouched. Explicit `false`/package `null` stay explicit.
Existing shipping weight behaviour remains: authored shipping grams, otherwise the
established product/variant weight with its existing modifiers. No size-derived
prices, weights, carrier rates or postage charges are introduced.

## Editor and copy

Product Editor/shared editor and Catalogue Intake reuse the same packing component.
Original Product Dimensions stay in their existing controls. Independent Packed
Shipping Dimensions include units, packing method, measurement confirmation,
protection, fragility/orientation and optional solo package. Size/packing modifier
rows receive corresponding editors; price-only or colour-only rows are not given
redundant size forms. Explicit combined-expression exceptions remain supported.

Copy Product Dimensions → Packed Dimensions reads the current unsaved effective
draft. The existing variation generator is used only to inspect up to256 combinations,
not to generate new SKUs. Equal geometry shares one group across unrelated options.
Existing attribute/value expressions identify groups. Crossed geometry exceptions
that cannot be represented by one broad rule retain full option expressions.

A modal shows old/new sizes before applying. Overwriting authored or inherited sizes
requires its checkbox. Copy changes only packed size and resets `measured:false`:
a copy is not proof of physical measurement. Prices, weights, finished values,
unknown JSON and unsaved edits are preserved. Existing specific shipping exceptions
remain. Cancel writes nothing. Subsequent finished edits never recopy automatically;
the UI reminds the merchant to review shipping independently.

Save Locally → targeted scanner projection → ordinary Product Review & Sync.
Advanced JSON reads/edits the same fields. Saving never publishes or authorises sales.

## Shopify and historical repair

Normal product, variant, weight and finished-size metafields stay on2026-10.
Finished sizes use the configured namespace (default `tlc_dashboard`), dimension-type
`product_length`, `product_width`, `product_height` on Product/ProductVariant.
They are data for customer-facing theme display, not native shipping fields. No
theme or app extension changed; live storefront display still needs merchant
unpublished-theme validation using the existing guide's Liquid/dynamic-source setup.

Only `SHOPIFY_NATIVE_DIMENSIONS_ENABLED=true` enables the existing2027-01 adapter.
The retired reuse flag is ignored, even if left true in Unraid. Only complete,
independently authored `measured:true` packed dimensions can be proposed. Existing
capability introspection, installed scope check (`write_inventory`, with product/
inventory read access), signed source review, retained variant/InventoryItem mapping,
durable pre-write receipt, independent readback and read-only uncertain-write recovery
remain. No global API-version upgrade. No shipping scope is added.

Read-only Preview classifies existing native values as correct/verified, missing,
different, likely old finished-dimension mapping, or insufficient evidence. A matching
old reuse receipt plus fresh native read is evidence for likely previous mapping;
equality with finished dimensions alone is insufficient. This is never clearing
authority. Without authored packed sizes, retain remote values and prior receipts;
show unconfigured/outstanding native work, not native synchronisation success.
Metadata may complete independently while this shipping stage remains outstanding.
Author actual packed sizes, save/scan, then review the exact native before/after
through normal Review & Sync. An old matching receipt never authorises a retry.
New verified correction evidence retains the immediately prior verified values.
Remote clearing remains unsupported/manual; no automatic resets or bulk repair.

Public Shopify documentation confirms native packed size starts in2027-01;
development has no configured merchant Admin token for a fresh store schema probe.
The installed app's actual schema/version/scopes are checked by the existing adapter
at review/execution, not assumed from CLI app-management access. Automated fixtures
are not live Shopify verification.

## Six packages and optional assignments

The six0017 definitions, UUIDs, measurements/readiness and retained links stay intact.
Recommendations use only authored packed geometry, measured usable package space,
existing shipping weight, protection/orientation/fragility and capacity. C5+ alone:
2mm or exactly3mm may qualify;3.1mm/unknown thickness does not. Large Letter Box has
no3mm cap. Tubes require measured prepared roll, explicit permission, internal
diameter/length and end-cap allowance. No selected package never blocks supported
product publication. There is no finished-size fallback or automatic assignment.

Use this package when shipped alone is explicit. Inherit, accepted, manual and
explicit no-package (`null`) remain distinct. Unchecking an inherited package saves
null. Checking a fresh displayed recommendation accepts it; Use Recommendation also
accepts it. Alternative buttons/manual choices remain explicit. Decline recommendation
saves no-package, not a dormant automatic acceptance. Each size/exception editor has
its own selection. Inline package creation preserves drafts and does not automatically select it.
Native package association/readback is still unsupported in this implementation;
set/clear manually in Shopify Admin and verify there. Local selection/clear is not
remote success. No package/default/profile change is made by this release.

Shopify alone calculates shipping/postage/checkout costs. No local rate calculator,
basket packing, simulator, CarrierService, callback or checkout interception exists.

## Deployment and controlled acceptance

No migration: head remains `0018_orders_webhooks`; published0017/0018 are unchanged.
Back up existing appdata/SQLite and catalogue before updating. Keep all current
mounts, credentials and normal2026-10 settings. Select tested develop or immutable
`shopify-packed-dimensions-<commit>` manually. Do not replace the Unraid XML or mounts.
Native capability remains optional; remove/set false the ignored reuse flag for
clarity, and enable native dimensions only when deliberately testing that feature.

1. Open an existing Draft with known finished measurements. Confirm original axes,
   SKUs, variant counts, price/weight modifiers and six packages are unchanged.
2. Keep an unsaved title, preview Copy, cancel, then copy. Confirm title/finished
   dimensions/prices/weights survive. Copied shipping size is unmeasured.
3. For a dimensional Size attribute plus colour/theme options, verify only actual
   size groups are copied; inspect preserved exact variant exceptions.
4. Edit packed size/units independently, measure/confirm, leave package unselected,
   save/scan/reopen. Verify finished size and packed size remain independent.
5. Build normal Shopify Preview. Inspect finished metafields separately from exact
   InventoryItem packed before/after and historical warning. No Preview mutation.
6. Only deliberately confirm a reviewed Draft correction after checking all stages.
   Verify native readback, retained GIDs/SKUs/status/channels and no redundant write
   on the next Preview. Do not infer success from a notification.
7. With packed size absent, confirm remote measurements are retained, no finished
   fallback, no clearing, and no claim of verified native shipping.
8. Test advisory C5+2/3/3.1mm and measured flat/tube fit. Accept or leave unchecked;
   verify null rejects inherited package and manual remote assignment warnings.
9. Recheck Orders (independent same-SKU personalisations), startup catch-up, existing
   customisation block and scheduled sales. No real test notification is necessary.

No live product correction, storefront size display or checkout acceptance is claimed.
No Shopify app/theme candidate is required for this correction. The independently
staged Orders read_customers candidate remains unreleased and outside this release.

## Focused verification

The affected Python run passed177 cases, including all three layouts, scan/save,
native stage/recovery, sale expiry, package thickness/tubes and retained identities.
The earlier bounded401-case pass covers Orders/webhooks, startup, editors,
customisations, sales and publishing; these are overlapping, not additive totals.
Final JavaScript checks include actual component handlers for unchecked defaults,
accept/alternative/decline, independent selections, copy and numeric modifier-type
preservation. Offline Docker smoke covers0017→0018, two workers, restart, six-package
persistence, original order-line snapshots and independent measurement values.
Exact final image/revision/digest are reported at delivery, not inferred here.

Real-DOM desktop/mobile copy checks verified unsaved-state retention, overwrite gate,
unit separation, size grouping and no horizontal overflow. A browser-native Advanced
JSON confirmation subsequently blocked automated clicks in the in-app browser;
final checkbox transitions are covered by focused JavaScript tests, not claimed as
live-store/mobile-Safari acceptance. A disposable operator-check page using the actual
component/local API is available via `tests/browser/packed_selection_fixture.py`
at loopback port7497 `/fixture/packed-dimensions`, with synthetic measured packages,
no save/Shopify/Discord and no production mounts.
