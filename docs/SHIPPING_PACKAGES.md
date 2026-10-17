# Shipping Packages — mini manager and safe reconciliation

This follow-up to M3A.9.5.7 reuses `/packaging/`, the existing three packaging tables
and stable local UUIDs. Open **Settings → Shipping Packages** (also in the sidebar).
No migration is added: the head remains `0018_orders_webhooks`. No product JSON,
dimensions, price/weight modifiers, recommendations, Orders, checkout, Royal Mail,
default package, app configuration or permissions are changed.

## What is implemented

* Search/sort and compact desktop/mobile package listing, physical readiness,
  Shopify relationship state, last successful verification, Edit and Sync Preview.
* Add/Edit/Save Locally, including incomplete records, unit selection (`mm/cm/in`,
  `g/kg/oz/lb`), independent optional usable space and existing local fit rules.
  Values are normalised using Decimal to cm/g; unknowns remain unknown. Tubes retain
  their separate length/diameter/end-cap geometry. Existing inline Add is reused.
* Explicit read-only schema/scope refresh using the normal2026-10 transport. Page
  rendering itself makes no Shopify call. Evidence is bound to the current store
  configuration; a stale/different-store record cannot supply an Admin link.
* Merchant-assisted comparison of 1–50 observations copied from Shopify Admin.
  Normalised names only suggest candidates. Retained store/GID matches take priority;
  names alone never link records. Type, converted dimensions and tare are compared.
* Per-package Import Shopify → TLC, manual TLC → Shopify checklist, Keep Local/record
  relationship or Skip. A bulk direction helper applies only to unambiguous complete
  compatible candidates; every selected row still has an individual signed preview.
* Explicit confirmation, 15-minute signed reviews, package-version/store/relationship
  conflict checks, atomic local batches and durable idempotent completion receipts.
  Missing observed values do not clear known local values. Changed imported outside
  size/tare resets the corresponding physical-measurement flag visibly in Preview.
  Usable interiors, C5+ thickness cap and all TLC-only rules survive imports.
* Optional retention of a real merchant-provided `ShippingPackage` GID against the
  verified store, without changing the local UUID. Blank GID is allowed: import data
  without creating a relationship. Never invent a GID. Unique constraints prevent
  duplicate links. Manual observations are explicitly **not API verification**.
* Individual/Sync All Preview probes capability, reports each unlinked/blocked package
  as skipped and supplies manual setup values. It is not a destructive merge and
  never claims that a manual checklist has remotely synchronised a package.

## API findings and hard boundary

Official reference checked on17 October2026 for Admin GraphQL2026-10:

| Operation/field | Verified evidence and release behaviour |
| --- | --- |
| `shippingPackageUpdate(id:, shippingPackage:)` | Documented. Payload exposes `userErrors`, not the updated package. **Not registered as a writable operation here.** |
| `CustomShippingPackageInput` | Name, type, outside dimensions and empty weight have documented equivalents. The additional `default` field is outside scope and is never sent. Its defaultfalse makes independent default-state readback especially important. |
| `ShippingPackageType` | `BOX`, `ENVELOPE`, `SOFT_PACK`, `FLAT_RATE`; no native tube mapping. Carrier-specific flat-rate geometry is not imported as an ordinary local box. |
| List/read saved packages | No public supported listing/individual readback contract was established from QueryRoot/Shop reference. Runtime probe reports actual installed schema flags; no guessed query is sent. |
| Create package | No supported create operation was established. Create in Shopify Admin, then reconcile. |
| Product/inventory association | Separate, still unverified; no product selection or association is changed by catalogue reconciliation. |

References: [update mutation](https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/shippingPackageUpdate),
[input](https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/CustomShippingPackageInput),
[types](https://shopify.dev/docs/api/admin-graphql/2026-10/enums/ShippingPackageType),
[queries](https://shopify.dev/docs/api/admin-graphql/2026-10/objects/QueryRoot),
[Shop](https://shopify.dev/docs/api/admin-graphql/2026-10/objects/Shop).

**Authenticated saved-package discovery was not verified in development:** merchant
Admin API credentials were unavailable. CLI app authentication is not a store token.
This is not proof that every store lacks these fields. If the installed schema exposes
more functionality, inspect its exact arguments, identity and default/readback contract
before implementing another adapter. The present release deliberately enables no
remote package writes, even if an update mutation appears in introspection.

The update reference requires “Any of shipping access scopes or
manage_delivery_settings user permission.” This is not evidence that both
`read_shipping` and `write_shipping` must be added. The probe reports which of those
names are absent, not an automatic permission request. No app candidate is staged:
extra scopes alone would not establish safe readback. Any later permission change
needs a separately approved exact diff and staged app version.

On Unraid's existing container console, run the small read-only probe if needed:

```sh
python -m app.packages_probe
```

It prints schema field names/flags, granted scopes, store identity and request counts,
not credentials or package/customer values. It does not initialise the app, run
migrations, write SQLite, change Shopify or send Discord. Alternatively use **Refresh
Shopify capabilities** on the page; this stores only local capability evidence. A
Shipping and delivery link is shown only after validating the returned merchant shop
domain. Otherwise navigate in Shopify Admin → Settings → Shipping and delivery.

## Six packages and meanings

Existing merchant edits, UUIDs and links are never replaced with reference values.
No startup reseed or duplicate records are introduced.

| Initial package | Reference exterior only; not usable-space evidence |
| --- | --- |
| TLC C5+ Board Back Envelope | 24×16.5cm, unknown package thickness; local contents cap3mm |
| TLC Large Letter Box | 35.3×25×2.5cm, pending physical verification |
| TLC Small Parcel Box | 45×35×16cm, pending physical verification |
| TLC Medium Parcel Box | 61×46×46cm, pending physical verification |
| TLC A4 Poster Tube | Advertised length24cm/diameter5cm, pending physical verification |
| TLC A3 Poster Tube | Measurements pending |

External dimensions never become internal dimensions automatically. Unknown tare or
thickness is not zero. Measurement readiness and Shopify relationship state are
separate: a linked package can still be unsafe for recommendations. Local package
UUIDs remain the product references, so renaming/linking does not rewrite selections.

## Daily workflow and status

1. Edit a package and Save Locally; this never writes Shopify.
2. If a retained relationship exists, native-equivalent changes become **Local changes
   pending**. TLC-only rule changes do not create a permanent remote pending state.
3. Use Sync Preview/Sync All Preview. Without verified readback, follow its manual
   Shopify checklist; the result is skipped/manual action, not success.
4. In Shopify Admin create/edit the package deliberately, preserving the store default.
5. Reconcile: copy observed name/type/outside size/tare, compare, explicitly choose the
   existing local package and direction, inspect Preview and confirm local actions.
6. Import updates only supported known values; Keep Local leaves them untouched.
   Recording a relationship requires an actual GID and current verified store evidence.

Manual evidence can show **Shopify changes detected** or **Conflict — review needed**;
these describe the retained observation, not an autonomous fresh remote read. Otherwise
the related record shows **Shopify API unavailable**, **Not linked**, and/or
**Measurements incomplete**. No manual relationship displays **Linked and in sync** or
a successful API verification timestamp. Uncertain historical evidence is preserved
for explicit review, not silently overwritten.

## Verification and safe Unraid acceptance

Focused Python:70 cases (24 new manager +46 existing packaging); bounded publishing,
dimension, editor, Orders/webhooks, sales and startup regression:260 cases. JavaScript:
21 cases including unit controls and existing draft-safe inline packaging. Synthetic
browser checks cover CSRF-enabled add/reopen, manual compare/import confirmation,
Sync All skip results, desktop/mobile layouts without horizontal overflow. Offline
Docker smoke uses isolated mounts/networknone, upgrades0017→0018, two non-root web
workers and restart, retaining six UUIDs, manual links, receipts, separate order-line
personalisations and independent finished/packed measurements. No live Shopify writes
or Discord test messages; no claim of actual merchant-package reconciliation.

1. Back up the existing Unraid appdata/SQLite with the container stopped, or use a
   consistent SQLite backup. Keep all current mounts, credentials and environment.
2. Select the published immutable `shopify-packages-manager-<commit>` image (or pull
   develop after checking its revision). Do not add a new mount or change XML paths.
3. Open Settings → Shipping Packages: confirm the existing six records/merchant edits,
   unknowns and C5+3mm rule. Inspect existing product choices without changing them.
4. Refresh capabilities/run the probe; report its safe output if discovery is blocked.
   Do not add scopes or assume that a successful Connection test verifies packages.
5. Try a local-only note/measurement change, Save Locally, reopen and verify the same
   UUID. Do not mark physically unmeasured geometry measured merely to pass a check.
6. Copy one real Shopify package observation into Reconcile. Inspect the candidate,
   select the intended existing local record, Preview, then explicitly confirm only
   the desired local import/relationship. Check that usable space/rules survive.
7. Sync All must show honest skipped/manual results, not a fake remote success. Follow
   manual Shopify actions only after your own review; leave the store default intact.
8. Restart Unraid: six records, UUIDs, links/receipts and product choices remain. Verify
   Orders and existing Product Review still open. No checkout test/rate change is needed.

Rollback uses the prior tested immutable image with the existing database: this release
adds no schema migration. Keep appdata backups; do not delete retained relationships
or review evidence to roll back the UI.
