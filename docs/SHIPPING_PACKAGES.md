# Shipping Packages — manual IDs and reviewed product assignments

This is the merchant-approved reduced scope following M3A.9.5.7. The intermediate
catalogue-reconciliation screens/handlers were removed before publication. Existing
package records, links and historical evidence are preserved. No new database
migration: head remains `0018_orders_webhooks`.

## Local settings

Open **Settings → Shipping Packages**, reusing `/packaging/`. The six existing TLC
packages retain their UUIDs, merchant edits, unknown measurements, tube geometry
and C5+3mm contents restriction. No reseeding/duplicates. Exterior fallback is never
saved as usable interior. Unknown tare/thickness is not zero.

Add/Edit/Save Locally supports mm/cm/in and g/kg/oz/lb, name/type, external size,
empty weight and Shopify ID. Optional purchasing information is a GBP unit purchase
price (exact decimal, not a shipping/product price) and supplier URL. URLs are not
fetched. No new image store/upload was introduced: existing storage is catalogue-
owned artwork/staged Shopify assets, not a supported general package-image store.
Package images remain a small deferred gap.

Collapsed **Advanced** contains optional usable interior axes (and tube diameter)
in the same dimension unit. The main form shows the optional envelope/letter
**Maximum Contents Thickness** rule separately.
Interior axes take precedence individually. Each missing axis falls back to the
corresponding exterior axis with **Estimated Fit / unverified** presentation. A
known envelope contents-thickness limit is compared directly with prepared item
thickness, separately from length/width and any authored internal usable height.
It never fills in a stored dimension, adds clearance/padding, or requires exterior
height to equal the rule. Exterior envelope height is an independent optional
measurement, **not** an implicit contents-capacity limit, even when known. An
envelope without either a configured contents limit or known internal usable height
has unverified capacity and remains manually selectable. Unknown item thickness
never becomes zero. C5+ allows2mm and exactly3mm, rejects3.1mm; an absent rule never
inherits C5+'s3mm restriction, including the separate Large Letter Box. Cylindrical
tubes require explicitly prepared rolled geometry, not automatic rolling of prints.

The active package model and basic solo size checker no longer use fragility,
flat/rigid/rolled approvals, protective materials/weight, extra clearance, end-cap
reserve, maximum gross/contents weight or physical verification flags. These are
**not moved to Advanced**. Existing historical JSON is retained on sparse saves,
but ignored by recommendations. New records do not acquire these retired settings.
No columns are dropped, migration is unnecessary, and no catalogue JSON is rewritten.
Internal-size checks are not physical fit, protection, weight-capacity or carrier
certification. Manual selection is allowed even when measurements are incomplete,
with unverified fit. Known item weight plus tare may be displayed; unknown weight
stays unknown. No protective-material weight or postage calculation is performed.

Product Editor and Catalogue Intake/JSON Creator still share this catalogue, stable
local IDs, inheritance, per-size/variant choices, advisory recommendations, explicit
no-package state and draft-safe inline Add. No automatic product assignment occurs
when creating a package or entering its Shopify ID.

On Edit, enter **Shopify Package ID** and click the main **Save locally** (measurements
and link are one transaction), or use **Save ID only** to preserve unsaved edits.
It accepts the actual complete `gid://shopify/…Package/<positive numeric ID>` or
version-suffixed package GID, including the merchant-supplied
`gid://shopify/ShippingPackageV2/116237992319`, copied from the merchant's Shopify
Admin, preserving its package object name verbatim. Format validation is not identity
verification. Raw numbers, names, unrelated object IDs, query strings and fabricated
IDs are rejected. Shopify's public docs specify an ID scalar, not a queryable package
type; if the actual supplied ID uses another format, inspect that evidence rather
than manufacturing or converting an identifier.

IDs are retained in the existing PackageLink table and scoped to the configured shop
domain/retained store identity. Duplicate IDs on different local packages, conflicting
old links and stale edits are refused. Renames preserve the link. Changing/unlinking
archives the previous relationship evidence. Unlink requires explicit confirmation;
it removes only the local relationship, not the package or any remote assignment.
Unsaved package measurements remain in the form when an ID is saved.

Confirmed save defects: the previous regex required the object name to end in
`Package`, rejecting genuine `ShippingPackageV2`. In addition, the main Save action
did not submit the separate ID field. Both regressions are covered through route,
JavaScript-handler, browser and offline Docker persistence checks. No identifier
conversion or change to `inventoryItemUpdate` is made. Shopify accepting a specific
V2 assignment still requires separately authorised live acceptance.

There is **no Sync All, automatic discovery, package import/update/create queue or
bidirectional reconciliation**. No package catalogue mutation is registered. The
old read-only schema probe can remain an administrative diagnostic; it is not needed
to enter IDs or assign a package and does not discover saved packages.

## One-time merchant-assisted ID collection

1. In your own authenticated Shopify Admin on a desktop browser, open Settings →
   Shipping and delivery → Packages.
2. Use the browser's developer tools Network panel while opening an existing package.
   Inspect the responses the Shopify Admin UI itself has already requested. Match
   the actual package name, type and measurements and copy its complete package GID.
   Do not run/replay undocumented Admin requests from TLCDashboard or a console script.
3. If a URL exposes only a number, do not construct a GID from it. Obtain the real
   complete ID from the UI's existing data or ask Shopify support. Do not guess.
4. Enter each ID against its existing TLC record. Never share a HAR, cookies, tokens,
   headers or unrelated customer data. A selected format-valid ID is labelled
   **Entered manually — not remotely verified**.

The six actual merchant IDs were **not obtained or validated live during development**.
This collection is a separate merchant-assisted step, not an automatic capability.

## Supported reviewed assignment

Official2026-10 references confirm:
[InventoryItemMeasurementInput.shippingPackageId](https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/InventoryItemMeasurementInput)
and [inventoryItemUpdate](https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/inventoryItemUpdate).
The mutation requires `write_inventory` and inventory-item update permission. Existing
read_products/read_inventory support retained identity reads. No new scope or app
version is requested/released. Actual token/user permission remains a live acceptance
check, not a fixture result.

Normal **Save Locally → Product Review & Sync → signed approval** resolves the selected
local UUID centrally to its store-bound Shopify ID, retained variant and InventoryItem.
A package-only change is actionable without an unrelated product/price/weight write.
The stage sends only:

```graphql
mutation TLCPackageAssignment($id: ID!, $input: InventoryItemInput!) {
  inventoryItemUpdate(id: $id, input: $input) {
    inventoryItem { id }
    userErrors { field message }
  }
}
```

Variables contain only `id` = the retained InventoryItem GID and
`input.measurement.shippingPackageId` = the merchant-entered package GID. They contain
no weight, SKU, packed dimensions, package exterior, default or price field.
This2026-10 write is independent of the deliberate native packed-dimensions2027-01
opt-in. Finished-size metafields, packed dimensions and existing modifiers/calculations
are unchanged.

Before each mutation, the existing operation ledger and identity evidence durably
record pending intent (visible from a separate database connection). Scope/source/
mapping changes invalidate signed reviews. Successful acknowledgements retain variant,
InventoryItem, local package, Shopify package, source revision, operation and timestamp.
Both active identity evidence and per-operation history retain the result. An unchanged
accepted target is not resent after later edits, refreshes, restart or receipt polling.

No selected package/ID or missing write_inventory does not prevent other supported
product updates. It produces a specific package warning. Changing the linked ID or
selected package produces a fresh signed assignment proposal. Explicit no-package
overrides stay explicit: nullable ID input alone does not document clearing semantics,
so no automatic null/clearing mutation is sent. Clear in Shopify Admin deliberately;
local unlink/no-package is never reported as remote removal.

## Honest outcomes and recovery

[InventoryItemMeasurement output](https://shopify.dev/docs/api/admin-graphql/2026-10/objects/InventoryItemMeasurement)
does not expose the assigned shippingPackageId. Therefore:

* A complete response with matching InventoryItem ID and empty userErrors records
  **Shopify accepted — not independently verified**.
* Package acceptance is separate from independently readable product evidence.
  Matching complete readable fields can show Up to Date with an explicit Optional
  Package notice; this does not independently verify the assignment. Missing package
  readback alone does not invalidate product freshness. Pending assignment proposals
  still show Update Available, and uncertain writes still require recovery.
  [Reconciliation repair](RECONCILIATION_REPAIR.md) supersedes the prior blocking policy.
* The overall operation is partial when a new assignment has acceptance-only evidence.
  Progress/history explain this, and Discord must not claim full verified success.
* Authoritative userErrors/authentication rejection records rejected, not success.
* Lost/partial/GraphQL/transport acknowledgements conservatively retain uncertainty.
  The identity becomes Recovery Required. No blind retry, readback-based invented
  verification, new Create or deletion is allowed. A normal read-only reconciliation
  cannot resolve a field Shopify does not return.
* A crash after committed pending intent similarly protects the receipt. Inspect the
  exact variant/package in Shopify Admin. This release has no automated override of
  uncertain package evidence; administrator-assisted resolution needs separate review.

No live Shopify mutations, checkout rate verification, theme change or app release
was performed during development. No claim is made that manual IDs or a fixture
acknowledgement proves a particular checkout package or price.

## Release and one-card Unraid acceptance

1. Back up existing appdata/SQLite consistently. Update only the image to the published
   immutable `shopify-packages-simplified-<shortcommit>` tag (or verified develop).
   Keep existing mounts, environment and credentials. No XML/new mount/migration.
2. Confirm the six package IDs/measurements/rules and existing product choices survived.
   Enter the real card-mailer Shopify ID through its package Edit page and use the
   main Save Locally button. Reopen/reload, then restart once and confirm it remains
   verbatim and labelled manually entered, not remotely verified. No publishing is
   needed for this local acceptance test.
3. Open a single-card product. Preserve finished and independent packed dimensions,
   price/weight/modifiers and SKU. Select that local mailer explicitly, Save Locally,
   wait for normal scanner projection, then open Product Review & Sync.
4. Confirm the displayed SKU, retained variant/InventoryItem GID and Shopify package ID.
   Inspect all other proposed fields; do not approve unexpected changes. Approve only
   after checking the actual package in Shopify Admin.
5. Expect an acceptance-only receipt, not independent assignment verification. Build
   Preview again: no repeated assignment write. Check another size/variant separately.
6. On a controlled storefront test, add exactly one eligible card, choose the same
   destination/service, and compare Shopify's offered rate/package behaviour while
   retaining the parcel default. Do not hard-code/assume£2.29,£2.95 or a66p saving.
   Stop before payment; no order or shipping-setting edit is required.
7. Restart Unraid: local IDs, product choices and acceptance receipts persist. Inspect
   Orders/customisations normally. Missing API access must not erase local history.
8. For rollback select the prior tested immutable Dashboard image with the same
   database/mounts. Previously accepted Shopify assignments do not automatically undo;
   review any remote reversal in Shopify Admin. Retain backups and operation evidence.

Tests use synthetic data, mocked Shopify and isolated offline Docker mounts. They
cover both Variable layouts, durable intent/acknowledgement, idempotence, errors,
manual ID validation/duplicates/rename/unlink, existing recommendations, Orders,
customisations, sales and dimensions. Docker checks upgrade0017→0018, two non-root
web workers, restart persistence and independent order personalisations. No live
postage calculation is claimed or implemented.
