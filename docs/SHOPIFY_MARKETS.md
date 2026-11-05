# Collection Markets

Collection Editor → Publishing destinations → **Markets** loads the connected
store's real choices on demand, read-only. Select **Manage Markets for this
collection**, tick the markets you want, and use **Save Metadata**. Save and
the existing containing-collection scan are local only. The Products status
follows the existing read-only comparison: checking first, then Update
Available for a confirmed difference or Up to Date for a verified match.

## Authority and approvals

- `shopify_market_ids` is an optional array of Shopify Market GIDs in the shared
  `product_info.json`. Products inherit from their owning source collection.
  Storefront browsing groups are not additional source collections and do
  not add market preferences. No product override or new database table.
- The resolved authored document is saved in the existing catalogue view
  (`shopify_publish_job.view_json`). Current market intent participates in
  the Live product fingerprint. Publication evidence is retained in the
  normal comparison and identity receipt.
- Unticking **Manage Markets** removes the JSON key and preserves Shopify
  availability. An explicit empty selection is not supported. Unselected
  active region markets are reviewed for removal only when management is on.
- Drafts retain preferences without publishing them. Changes to Markets
  require explicit signed Review & Sync. Hourly publishing and Sync Now
  do not authorise a market change or the associated activation. An existing
  authorised release can still make local intent Live for review.
- Example Review: **United Kingdom — Already matching**, **Europe — Add**,
  **United States — Remove**. Names and relationships come from Shopify.

## Shopify prerequisites and safeguards

Use the supported Admin GraphQL API `2026-10`, with `read_markets` for market
discovery/hierarchy, `read_products` for catalogue/product evidence, and
`read_publications` for publication/context reads. `write_publications` is
required for product membership changes, along with the existing publisher
permissions. No `write_markets` or catalogue-configuration mutations. The
checked-in app configuration already requests the product/publication scopes
but not `read_markets`; no app permission was changed by this release. Verify
actual installed scopes separately. An approved app permission change may
be required before market discovery works.

The supported topology is active, non-overlapping country-based markets with
one dedicated active product-publication catalogue each, with automatic
product inclusion off. Shared, inherited, ambiguous, unsupported or
incomplete mappings fail closed. Current application bounds are 20 markets,
120 explicit countries and five changed market/channel publication
memberships per signed operation. These are TLC safety bounds, not Shopify
platform limits. Do not change store-wide catalogue settings just to pass
the check without assessing the effect on other products.

Review binds the exact product, local source, market-to-catalogue-to-
publication mapping and current memberships. Confirm revalidates them and
uses the existing `publishablePublish`/`publishableUnpublish` stage with
durable intent/acknowledgements. It reads back complete market publication
membership and, when Active, `publishedInContext` for the applicable
countries before declaring verification. A new Draft is not activated until
its reviewed catalogue membership is read back. Ambiguous/partial writes
retain recovery evidence; do not retry by publishing again. Existing channel
actions are still controlled by their own authored settings.

References: [Market API](https://shopify.dev/docs/api/admin-graphql/2026-10/objects/Market),
[catalogues in Markets](https://shopify.dev/docs/apps/build/markets/catalogs),
[product context reads](https://shopify.dev/docs/api/admin-graphql/latest/objects/Product),
and [publication mutation](https://shopify.dev/docs/api/admin-graphql/latest/mutations/publishablePublish).

## Isolated acceptance and manual rollout

1. Use a staging store with separately approved permissions and dedicated
   catalogues. Confirm the real region names, memberships and whether the
   product is also published to its normal channels. Do not test on live
   customers' product availability.
2. Back up Unraid appdata. Install the immutable release tag manually, keeping
   the existing `/app/instance`, catalogue, Intake and output mounts. No new
   migration or rescan is needed just to install. The previous image is
   `neprod/woocommerce-dashboard:royal-mail-live-f1b9d52d0333`.
3. On one staged collection, save two actual market choices and wait for the
   scoped scan and normal read-only comparison. Verify the existing shared
   JSON and that no product publication occurred.
4. For a linked Live product, inspect the exact current/intended market
   choices in Review & Sync before approving. In Shopify Admin, open the
   product's Publishing/Markets availability and the relevant Markets
   catalogues. Check the expected UK, European and US markets individually
   as configured, plus the unchanged sales channels and product ID. Confirm
   TLCDashboard shows verified matching evidence. Reopen Review: no write
   should be needed.
5. Leave a Draft unchanged and verify its status is still Draft. Verify a
   legacy unmanaged collection gets no market actions. Roll back the Docker
   tag if needed; do not restore unrelated data or assume an image rollback
   undoes an explicitly approved Shopify mutation.

The live store and deployed revision were deliberately not inspected. The
actual installed scopes and catalogue topology require merchant acceptance.
