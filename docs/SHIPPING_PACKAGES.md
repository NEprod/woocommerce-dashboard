# Shipping Packages — manual IDs and reviewed product assignments

This is the merchant-approved reduced scope following M3A.9.5.7. The intermediate
catalogue-reconciliation screens/handlers were removed before publication. Existing
package records, links and historical evidence are preserved. No new database
migration: head remains `0018_orders_webhooks`.

## Local settings

Open **Settings → Shipping Packages**, reusing `/packaging/`. The six existing TLC
packages retain their UUIDs, merchant edits, unknown measurements, tube geometry,
verification flags and C5+3mm contents restriction. No reseeding/duplicates. Exterior
size never becomes usable interior. Unknown tare/thickness is not zero.

Add/Edit/Save Locally supports mm/cm/in and g/kg/oz/lb and all existing fitting rules.
Product Editor and Catalogue Intake/JSON Creator still share this catalogue, stable
local IDs, inheritance, per-size/variant choices, advisory recommendations, explicit
no-package state and draft-safe inline Add. No automatic product assignment occurs
when creating a package or entering its Shopify ID.

On Edit, use the separate **Shopify Package ID** form. It accepts the actual complete
`gid://shopify/…Package/<positive numeric ID>` copied from the merchant's Shopify
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
   immutable `shopify-package-assignments-<shortcommit>` tag (or verified develop).
   Keep existing mounts, environment and credentials. No XML/new mount/migration.
2. Confirm the six package IDs/measurements/rules and existing product choices survived.
   Enter the real card-mailer Shopify ID through its package Edit page.
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
