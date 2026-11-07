# Collection Markets

Collection Editor → Publishing destinations → **Markets** loads the connected
store's real choices on demand, read-only. UK is included by default for locally
Live products intended for Online Store. Tick only the additional markets you
want, then use **Save Metadata**. Save and
the existing containing-collection scan are local only. The Products status
follows the existing read-only comparison: checking first, then Update
Available for a confirmed difference or Up to Date for a verified match.

## Authority and approvals

- `shopify_market_ids` is an optional array of Shopify Market GIDs in the shared
  `product_info.json`. Products inherit all checked international choices from
  their owning source collection. The local model has one source collection per
  product. Storefront browsing groups and Shopify collection memberships have
  no authoritative local market preferences to union. No new ownership link,
  product override, or database table was added.
- The resolved authored document is saved in the existing catalogue view
  (`shopify_publish_job.view_json`). Current market intent participates in
  the Live product fingerprint. Publication evidence is retained in the
  normal comparison and identity receipt.
- Both missing and empty selections mean UK only. Previously saved UK IDs
  are harmless; UK cannot be switched off via the Markets editor. Unselected
  international markets, including newly discovered ones, are reviewed for
  exclusion. Neither deployment nor a local save publishes anything directly.
- The enabled hourly publisher may apply verified additions and, with an
  explicit local selection, removals for catalogues already covered by a
  verified publication receipt for that product and store. Implicit UK-only
  defaults, unmanaged catalogues and changed catalogue mappings still require
  signed Review & Sync before a removal. Sync Now does not approve market changes.
- Drafts retain preferences without publishing them. An existing authorised
  release can still make local intent Live.
- Example Review: **United Kingdom — Included by default**, **EU DDP — Add**,
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

The supported topology is active, non-overlapping country-based markets.
UK can inherit Online Store normally. A selected international market that
inherits Online Store can match only when buyer-context verification shows it
is available. An unselected market cannot be made exclusive while it inherits
Online Store; configure a dedicated active product-publication catalogue with
automatic product inclusion off. An explicit dedicated catalogue is supported
for UK too, but is not required. Shared or multiple active publications,
non-region or overlapping regions and custom parent hierarchies fail closed.
Current application bounds are 20 markets,
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

An acknowledged publication can appear in the catalogue before every buyer
context agrees. After all reviewed publication writes acknowledge, a
contradictory market read gets up to two additional read-only checks, after
2 and 4 seconds. Each check revalidates channels, market topology, catalogue
memberships and buyer contexts. Incomplete evidence, identity/configuration
errors, or a persistent difference still require recovery. There is no write
retry.

For an existing uncertain product, use **Products → Resolve Issue**, open
the retained identity, then select **Reconcile using Shopify reads only**
instead of submitting a new approval.
The original operation stays in history. Recovery requires both matching
publication evidence and the normal product readback before the current
comparison can show **Up to Date**.

References: [Market API](https://shopify.dev/docs/api/admin-graphql/2026-10/objects/Market),
[catalogues in Markets](https://shopify.dev/docs/apps/build/markets/catalogs),
[product context reads](https://shopify.dev/docs/api/admin-graphql/latest/objects/Product),
and [publication mutation](https://shopify.dev/docs/api/admin-graphql/latest/mutations/publishablePublish).

## Observed store and one-time setup

Read-only inspection of Unraid and Shopify Admin on 6 November 2026 confirmed
`afa7ccdb0373` running, UK and EU DDP active, both inheriting All products and
Online Store from Store default, no configured catalogues, and USA only
suggested. The affected 16-Bit collection already selected UK and EU DDP.
Live market discovery succeeded; write permission was not exercised. The old
code required a dedicated publication even for the inherited UK market.

To enable exclusions, first use a test store. In Shopify Admin → **Markets →
Catalogs → Create catalog**, create a draft product catalogue for EU DDP only
and disable automatic inclusion of new products. Set no pricing overrides or
adjustments and verify other settings are unchanged. Seed and verify the
intended product list before assigning/activating it under **EU DDP → Catalogs**:
assigning an empty catalogue can immediately remove current products. For a
no-visibility-change cutover, first mirror today's permitted EU assortment;
then apply removals product by product through Review & Sync. Check the buyer
context, since another inherited catalogue can keep a product available.
Repeat for USA only if you separately create/activate that market. Leave UK,
DDP, duties, tax, currency and shipping settings alone.

See Shopify's [catalogue setup and inheritance guide](https://help.shopify.com/en/manual/markets/customizations/catalogs).

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
   legacy collection previews UK-only intent and does not mutate until approval.
   Use Shopify Admin **Products → View as** for UK and each active international
   market, and check the staged storefront in those buyer contexts. Roll back the Docker
   tag if needed; do not restore unrelated data or assume an image rollback
   undoes an explicitly approved Shopify mutation.

These checks do not change live catalogues, permissions or products.
International exclusions require dedicated catalogue control and a matching
buyer-context read; membership alone is not proof.
