# M3A.9.5.7 — dimensional destination evidence and local package review

## Findings

The previous full Simple/Variable comparator already compared actual remote
`product_length`, `product_width`, `product_height` values, including absent values.
It did not require or independently read their metafield **definitions and
storefront access**. Matching unstructured values could therefore produce No Change
without establishing customer-facing metadata readiness. Old cached evidence had
no destination-definition policy version. A local save was not a reliable way to
discover missing destination requirements.

Package IDs were deliberately excluded from remote managed fields because a safe
association readback contract was unavailable. The UI showed only warnings, making
the correct *no remote mutation* classification look like the local selection had
been ignored. Disabled native publishing also returned before projecting authored
packed values, hiding the intended measurements rather than explaining the gate.

These paths are reproduced with synthetic fixtures. No merchant Admin API token is
configured in development; the exact missing values/definitions and opt-in setting
on the merchant's Unraid instance have **not** been independently observed here.
Do not infer that every previously published product lacks values.

## Finished Product Dimensions

Reuse the existing configured namespace (`SHOPIFY_PRODUCT_DIMENSIONS_NAMESPACE`,
default `tlc_dashboard`) and keys `product_length`, `product_width`, `product_height`.
Each is a Shopify `dimension` value carrying its unit; exact local conversion to mm
and equivalent remote-unit comparison are unchanged. No new metaobjects or duplicate
size namespace is introduced. Normal API: **2026-10**.

One bounded three-definition query per required owner verifies owner type, namespace,
key, type, ID and storefront access. Missing definitions and `NONE → PUBLIC_READ`
are separate signed reviewed changes even when product values match. Compatible
definitions are reused. Type/identity conflicts block rather than recreate. Once
retained, definition IDs cannot be silently replaced/adopted. Definition writes have
durable pre-request receipts and independent readback. Timeout recovery is read-only;
an incomplete stage is not replayed. Shared access changes affect that definition's
other values too; the review identifies PRODUCT versus PRODUCTVARIANT ownership.

Simple/shared dimensions use Product metadata. Variable variants retain their own
dimension metadata and exact retained variant IDs. If dimensions differ by variant,
no generic parent size is proposed; any previously published parent size is preserved
and warned about, never automatically cleared. Bind future theme size display to
the selected variant. Existing shared Product and matching Variant keys remain
compatible; no historical values are deleted. Missing axes are unmanaged, not zero.

Review labels absent values/definitions **New Shopify field — Finished Product
Dimensions**, differing values/access **Changed Shopify field**, matching values
Already matching. A policy marker invalidates old evidence once, using the existing
bounded Product reconciliation scheduler/manual refresh, not a bulk writer. Existing
ten-minute caching then applies. No product needs resaving to discover missing fields.

Definitions are public-readable data, not proof of visible storefront output. In an
unpublished theme, a merchant can use compatible dynamic sources or Liquid, e.g.
`product.metafields.tlc_dashboard.product_length | metafield_tag`; size-changing
products must use the selected variant's corresponding metafields. Theme binding,
variant-change rendering and live display remain separate acceptance work. No theme
or extension is modified or staged by this correction.

## Packed Dimensions — Shopify Shipping

The existing isolated opt-in adapter remains the sole publisher. It uses **2027-01**
only for fixed capability/native measurement queries and `inventoryItemUpdate` with
`measurement: {packedDimensions: {length, width, height, unit}}`. It validates the
returned API-version header, actual schema fields/input/units, read scopes and
`write_inventory`, and the retained Product/Variant/InventoryItem mapping.

Only complete independently authored `packaging` size with `measured:true` is
eligible. Finished size, selected package exteriors and the retired reuse flag are
never fallback sources. No prices or weight modifiers change. Review includes actual
native before/after and InventoryItem IDs. Existing signed confirmation, durable
per-item write intent, readback and uncertainty recovery remain in place.

`SHOPIFY_NATIVE_DIMENSIONS_ENABLED=true` is a deliberate deployment opt-in, not set
automatically. Disabled, unavailable/version-fallback, incomplete-schema or missing
permission cases retain local values and show Awaiting Shopify API capability or
the precise existing failure. They cannot renew full successful verification for
authored required packed values. Other reviewed supported metadata can complete
independently; partial native success is not full synchronisation.

The official [September 2026 changelog](https://shopify.dev/changelog/posts/manage-packed-product-dimensions-with-the-admin-graphql-api)
announces packed dimensions from2027-01. No additional merchant scope or app version
is added here. Actual store availability is checked by the adapter, not inferred
from CLI authentication or documentation. Historical finished-to-packed receipts
still support advisory findings and explicitly reviewed correction with real packed
measurements. No automatic remote clearing.

## Optional Package

The existing six package UUIDs, limits and unknown measurements are unchanged.
Recommendations still use prepared packed size only and never auto-assign. Saved
local choices and explicit null overrides appear in a dedicated Review group,
including each variant, current selection, recommendation and previous recorded
selection. “Local Configuration Changed” means changed from the last publishing
receipt, or a historical selection not recorded there; saving has already applied
it locally. It is **not** a pending remote update and does not require a remote
confirmation to take effect. A package-only review has no publish token/mutation.

The [2026-10 measurement input](https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/InventoryItemMeasurementInput)
documents `shippingPackageId`, but the corresponding
[measurement output](https://shopify.dev/docs/api/admin-graphql/2026-10/objects/InventoryItemMeasurement)
does not document association readback. Therefore this is not described as
universally nonexistent; **verified assignment/change/clear is unsupported by TLC's
current adapter**. No write-only association, fake metafield or default-package
change is introduced. Use available Shopify Admin controls separately; no remote
assignment/clearing is claimed from the local checkbox. Native packed measurements
may let Shopify choose its own saved package on eligible orders; that is separate.

## Deployment and controlled acceptance

No migration: head remains `0018_orders_webhooks`. Existing database/appdata mounts,
credentials, scopes, theme, store shipping and Unraid templates remain unchanged.
Back up the existing appdata database and authored catalogue before updating the
container. Use the reported immutable development image for rollback/reproducibility.

1. Update the image only; preserve all current mounts/environment. Do not replace
   the database. Confirm migration head0018, Orders and Packaging still open, and
   the six existing packages remain (unknown physical values must stay unknown).
2. Choose an existing **Draft** Simple product with finished size already authored.
   Refresh Shopify Status, then Build read-only Preview without resaving. Inspect
   Product Dimensions: missing remote values/definitions must be New, differing
   values/access Changed, matches Already matching. This is read-only.
3. Author actual packed shipping measurements, confirm measured and save locally;
   wait for scanner projection. Leave package unchecked if undesired. With the
   deliberate native opt-in enabled, check the exact2027-01 capability, InventoryItem
   and native before/after. Without support, expect an explicit pending capability,
   **not** a published measurement. Never confirm a guessed/copy-only parcel size.
4. Accept a measured local package or explicitly decline an inherited choice.
   Save, reopen Review and inspect Optional Package. A package-only change should
   say saved local configuration changed / remote association unsupported, with
   no product publishing button solely for that choice.
5. On a multi-size Draft Variable Collection or Single Variable product, inspect
   every SKU's finished size, independent packed size, InventoryItem and local
   package exception. Do not approve until all identities/measurements match intent.
   Price/weight/stock/publication changes unrelated to this correction must not be
   included unexpectedly. Existing parent size is not silently removed.
6. Only when ready, explicitly confirm the reviewed supported changes. Check the
   durable receipt and independent readback; rebuild Preview to confirm matching
   fields no longer cause mutations. On timeout use read-only reconciliation, not
   a repeated publish. Verify actual Admin data; automated fixtures are not live proof.

No shipping prices, mixed baskets, CarrierService, checkout interception, Royal Mail
rates, store-default package, theme/app release or live Shopify test writes occur.

## Verification evidence

Mocked publishing/dimension checks:94 passed; final targeted safety/recovery/status
checks:29 passed (overlapping, not additive). The bounded Orders/webhooks/startup/
status/editor/packaging/sale regression pass had368 passes and one stale button-label
assertion; that assertion was corrected to the actual existing unified Sync button
and passed on targeted recheck.75 JavaScript checks passed. Browser checks exercised
the actual Review template at desktop/mobile sizes: missing destination fields,
variant-owned sizes, local package-only change with no confirmation token, and no
horizontal overflow after correcting the mobile table minimum width.

These are synthetic/offline results, not live merchant publication evidence. Docker
migration/two-worker/restart results and immutable release identities are reported
with the delivered image, after its final smoke verification.
