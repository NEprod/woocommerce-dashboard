# Private customer photos — setup and acceptance

Default: **off**. Image Upload still saves `type: photo`, label, help, optional
or required, the usual field visibility/order and inheritance, and a disabled
one-image JPG/JPEG, PNG and WebP preset (10,000,000 bytes). Keep production
photo fields disabled until the end-to-end trial passes. Existing non-photo
definitions and Orders schedules need no change.

## 1. Deploy the optional Worker

1. Open `shopify/customer-uploads`. In its `wrangler.toml`, set `SHOP_DOMAIN`
   (canonical `*.myshopify.com`), `SHOP_ID` (the existing Shopify Shop GID),
   `SHOPIFY_CLIENT_ID` (the installed TLC app's client ID),
   `STOREFRONT_ORIGIN` (exact HTTPS storefront origin, no trailing slash),
   and `PROXY_PATH` (the planned app proxy storefront path). Keep the
   `TLC_UPLOADS` binding on the existing private `tlc-customer-uploads`
   bucket. Do not add a public R2 domain, R2.dev URL, bucket CORS or S3 credentials.
   Use only this Worker for this bucket; the shop-wide byte counter is shared.
2. Authenticate Wrangler to the correct Cloudflare account and deploy with
   `npx wrangler deploy --config wrangler.toml` from the Worker directory. The
   Worker denies requests until configuration is complete. Record its HTTPS
   origin, e.g. its Workers.dev hostname, without a trailing slash.
3. Use Wrangler's interactive `secret put` prompt separately for
   `SHOPIFY_CLIENT_SECRET` (the existing app secret),
   `UPLOAD_SIGNING_KEY` (separate stable random secret, at least 32 characters),
   and `DASHBOARD_UPLOAD_TOKEN` (a separate random 32–128 character URL-safe
   secret). Use the existing app credential only in the Worker secret store;
   do not rotate it or copy it into logs. Keep Worker invocation log capture
   disabled for this service (including query strings, request bodies and headers).

## 2. Connect the existing Shopify app

The checked-in app has no proxy; do not overwrite existing app settings by
deploying an example. In a local deployment configuration for the existing
app, append **`write_app_proxy`** to the app's actual current scopes (retain
`read_products`, `read_orders` and all other existing scopes) and configure:

```toml
[app_proxy]
url = "https://YOUR-WORKER-ORIGIN/proxy"
prefix = "apps"
subpath = "tlc-customer-uploads"
```

If the installation uses a customised path, set the actual installed path in
the Worker and TLCDashboard. An app can have only one proxy; stop if existing
live configuration already uses it. No new app or Admin API scopes beyond the
proxy scope are needed. Rebuild the theme extension with
`npm --prefix shopify run build:assets`. Prepare/review a Shopify app version
with the extension and proxy, using `shopify app deploy --no-release` from the
configured app project when ready. Any release or permission approval is a
separate merchant action. Start with a test store and draft theme. The Worker
uses the existing app's client credentials for fixed, read-only Admin
2026-10 product-field and shop identity queries; it does not read customer
records or write Shopify data.

Once the app proxy and its permission are approved, visit the configured
storefront's proxy path with `?probe=1` as a storefront visitor. It should
return `{"proxy":"verified"}`. Only a correctly signed Shopify proxy response
through the configured path can write this readiness receipt to R2. The
receipt is valid for seven days for publishing; repeat the probe before a later
photo configuration change if stale. A successful probe alone does not prove
cart/checkout compatibility.

## 3. Configure Unraid

Manually install the verified immutable Docker tag, retaining current mounts,
appdata and automation settings. There is no schema migration. Supply:

- `TLC_PHOTO_WORKER_URL`: the HTTPS Worker origin, no trailing slash.
- `TLC_PHOTO_WORKER_TOKEN`: the same value as Worker `DASHBOARD_UPLOAD_TOKEN`.
- `TLC_PHOTO_PROXY_PATH`: the actual installed storefront proxy path.

Without these settings, a successful signed proxy probe, private R2 access
and the correct Shopify shop/scopes, changed photo definitions cannot publish.
Existing non-photo configurations do not call the Worker. Do not remove only
the theme app block or roll its asset back while leaving enabled required photo
fields offered for sale. Disable photo definitions through the existing reviewed
workflow first. Roll back Docker independently to the previous immutable
image if needed. Never expose Unraid through the proxy.

## What is stored and who can read it

- The shopper's browser hashes the Shopify cart ID without its private key.
  A signed app proxy and a body digest bind a three-minute, single-object
  upload grant to that cart, Shopify product, selected variant, field and
  published configuration revision. This is cart binding, not customer
  authentication. The Worker validates actual raster headers and container
  structure and bounds bytes/dimensions. It does not run malware scanning or
  strip camera EXIF/location metadata. R2 conditional writes reject replay.
  Rate limits restrict proxy grants and uploads, and a conditional R2 counter
  caps accepted bytes at 200 MB per UTC hour for the shop.
- The storefront shows a local preview; bytes go directly to private R2
  through the Worker. Shopify sees a generic answer plus a random reference
  in the existing private line snapshot. It receives no file or download URL.
- On existing Orders import or webhook processing, the Worker matches the
  issued record against Shopify's authenticated cart token and exact
  shop/product/variant/field/revision. Only one order/line can claim a reference.
  Duplicate imports are safe. At most ten photos are bound per order in this
  integration. If the token is absent, old API schema is selected, references
  conflict, or service access fails, the order still imports; affected photos
  remain unverified. For temporary failures or missing webhook cart data, use
  the existing read-only **Orders → Sync Now** with Admin API 2026-07 or later.
- Only existing TLCDashboard **Orders administrators** can use the download
  link. The server checks the current order/line association and the Worker
  checks the retained binding again. There is no public image URL or browser
  bearer. Normal admins can see other protected order details as before.
- Defaults: **30 days** unbound (`ABANDONED_DAYS`, range 14–90) and **90 days**
  from first order association (`ORDER_PHOTO_DAYS`, range 7–365). The hourly
  cursor sweep deletes stale R2 objects in bounded pages; actual object deletion
  can lag under backlog. Server downloads are denied at the ordered retention
  threshold even before the sweep. Never add a blanket R2 expiry rule. Allow
  enough time for late checkout and order import. An uploaded photo left behind
  by failed checkout, cancellation before order creation, replacement or removal
  follows unbound retention. An operator with the service credential can use
  `POST /private/delete` with the verified shop/order/line/reference tuple.
  A local order redaction does not delete the R2 photo: complete the storage
  deletion first when handling an erasure request. Copies downloaded by staff
  must be removed separately.
- Before activation, update the Privacy Policy with purpose (fulfilment of
  personalised orders), who can access original photos/metadata, Cloudflare
  processing and applicable location/retention, erasure contact and handling
  of photos of other people. Align the stated periods with the Worker settings.

## Controlled acceptance before production

1. In a test store and draft theme, verify the signed probe and enable one
   required photo field on a single test product. Publish the field only via
   the existing reviewed route. Check that a normal product still works with
   the Worker unavailable and a required photo fails closed on the supported
   main product form.
2. In a fresh guest session, test an empty cart, phone picker/camera, preview,
   removal/replacement, wrong type, oversized file, and Worker failure/timeout.
   Confirm a failed/pending/absent required photo blocks Add to Cart. Verify
   switching variants after upload asks for a new selection.
3. Add the same variant twice with different non-personal test photographs.
   Compare the two cart lines; change a quantity without losing its reference.
   Finish an approved test checkout, import the order twice, then download both
   files as an administrator and match each to its own line. Verify an
   anonymous user, non-admin and mismatched order/line cannot download them.
   Test the actual store's guest cart token mapping; without a match, do not launch.
4. Verify a replaced or abandoned test upload ages out without affecting bound
   images (use synthetic aged records in staging; do not shorten production
   retention for testing). Check required-photo purchase shortcuts are disabled
   for the pilot and Orders reports unverified evidence unmistakably. Theme
   validation does **not** enforce direct Shopify cart API requests, checkout
   Functions, quick-add or other channels; do not fulfil missing/unverified photos.

References: [Shopify app proxies](https://shopify.dev/docs/apps/build/online-store/app-proxies),
[signature verification](https://shopify.dev/docs/apps/build/online-store/app-proxies/authenticate-app-proxies),
[Order cart token](https://shopify.dev/changelog/new-field-carttoken-added-to-the-order-graphql-admin-api),
[Cloudflare R2 binding](https://developers.cloudflare.com/r2/api/workers/workers-api-reference/).
