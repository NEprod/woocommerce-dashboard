# Product Shipping Editor and sparse override repair

## Confirmed defects

`packaging.js` assigned a saved UUID to a native select before adding its options.
A real browser discards that value. The asynchronous refresh then retained the
empty control, showing an authored selection as unassigned. Reselecting an already-
authored UUID could produce an empty patch and “No changes to save”; another
shipping edit could serialize the empty choice. The original lightweight JS DOM
did not emulate this browser rule. The new regression failed before the repair.
Selection identity now survives independently of option loading, including missing
or archived choices.

Size-specific shipping also depended on the commercial modifier override checkbox,
and the closed `variationModifier` schema rejected the existing packaging extension.
Shipping exceptions now serialize independently and validate with the root contracts.
Shipping-only child modifier patches retain inherited commercial modifier entries;
historical commercial override tables retain replacement semantics. New package-only
rules do not remove parent prices, weights or finished sizes. Dirty tracking compares
the authored draft; reverting returns to Saved. Intake pruning retains explicit null.
Commercial overrides also inherit shipping metadata independently: even an explicit
empty commercial table does not reintroduce parent prices/weights when it inherits a
package rule. Those inherited shipping-only rows are projections, not copied JSON.

These are code/fixture findings, not inspection of the merchant's Unraid database or
a claim that its historical `16bit-0001` source was inspected.

## Current workflow and boundaries

Shipping contains packed units/length/width/thickness, explicit Copy Product Dimensions
and one optional package selector. Size-changing modifier editors appear here; colour-
only rules do not get duplicate shipping editors. Explicit variant exceptions remain.
Weight stays in existing product/variant controls, with unchanged calculations.

Choose inherit, a package for this scope, or explicit no-package. No second acceptance
checkbox, reason or hidden whole-table override is required. Recommendations never
select automatically. Manual selection is allowed with unknown fit; Add Package keeps
the draft and does not automatically select the new record. Stable UUIDs resolve central
Shopify IDs, not package names. Packed and package overrides remain independent.

Blank packed axes show effective finished-size **placeholders**, including unsaved
edits and existing dimensional modifiers. Placeholders are not serialized. A local
recommendation request can explicitly use temporary finished fallback, always Estimated
Fit and with no writes. Normal recommendations/publishers remain authored-only.
Unknown values are not zero. Copy previews the existing bounded size groups, requires
confirmation before overwrite and changes only the unsaved draft. Cancel preserves
edits. After Save Metadata, copied values are independent; later finished edits never
recopy. Copy no longer adds the retired `measured` flag.

Removed everyday controls: duplicate weight, physical-verification switch, orientation,
product class, clearance, protection weight, fragile/rolling permissions, protection
requirements and selection reason. Historical JSON survives ordinary edits. There is
no destructive schema cleanup or migration (head `0018_orders_webhooks`). Future
removal of historical keys needs a separate consumer/data audit. Historical prepared-
roll/orientation values still prevent unsafe automatic rolling; manual tube selection
remains possible with unverified fit.

Finished-size metafield meanings remain unchanged. Only complete explicit packed sizes
can be proposed by the existing opt-in 2027-01 adapter. The obsolete measured checkbox
is not another authoring gate; signed review, capability/scopes, positive complete axes,
IDs, receipts and readback remain required. No API opt-in is enabled here.

Package assignment retains signed 2026-10 `inventoryItemUpdate` /
`measurement.shippingPackageId`, existing `write_inventory`, durable acceptance-only
receipts and uncertain-write recovery. Accepted unchanged assignments are not replayed.
Unlinked selections do not block other updates. Local clearing does not claim remote
clearing. No live Shopify writes, app/scope/theme/default/rate/checkout changes.

## Verification and acceptance

Python tests cover the synthetic 155 × 155 × 3 mm card, Save → reopen → actual targeted
scanner → linked review plan, repeat saves, inheritance/none/clearing, three layouts,
size-specific exceptions and unchanged modifier results. Existing assignment tests
cover acknowledgement, uncertainty and idempotence. Desktop Chrome and 400 px responsive
checks cover save/reload, size-specific packages and copy/cancel/unsaved edits. They are
not physical-device Safari or live merchant acceptance. Browser scan jobs are stubbed;
Python and Docker exercise actual scanner projection.

The offline Docker harness extends 0017→0018 upgrade, two non-root web workers,
restart, Orders Ellie/6 and Freddie/5, IDs, receipts and freshness preservation with
an actual metadata save/targeted scan on disposable mounts. Network is disabled.

1. Stop TLCDashboard; back up existing appdata/SQLite and catalogue JSON. Retain the
   previous immutable image and all current mounts/settings.
2. Pull/recreate with the reported `shopify-shipping-editor-<commit>` image and the
   same mounts, secret and API flags. No fresh DB, new mount or new migration is needed.
3. Open `16bit-0001` → **Edit Product Override**, not Shared Collection Data. Check
   the provisional finished values against its actual measurements.
4. Select the linked C5+ package. Check Unsaved changes, Save Metadata, wait for the
   targeted scan, then reload. Inspect child JSON and unchanged shared collection.
   An already-authored identical choice may correctly yield No changes.
5. Copy only appropriate finished measurements, adjust actual prepared dimensions,
   save/reload and confirm original finished dimensions, prices and weight are unchanged.
6. On a multi-size child, select a package for one size only. Save/reload and verify
   other sizes, prices/weights and variant/SKU identities. Test inherit and no-package.
7. Open Review & Sync: inspect InventoryItem/package GIDs and authored packed targets
   (or accurate capability deferral). Do not approve unexpected fields. A live assignment
   still needs deliberate merchant approval; this release does not perform one.
8. Run normal read-only Product reconciliation and check Orders/personalisation after
   restart. No DB/checkpoint reset, full rescan or marker removal is needed.

Rollback can use the retained earlier immutable image with the same mounts; no schema
rollback is needed. Its editor still has the old selection limitations, so avoid
shipping edits through it. Do not restore an old whole DB over newly imported Orders
merely to roll back the UI. Live Shopify and checkout acceptance remain merchant actions.
