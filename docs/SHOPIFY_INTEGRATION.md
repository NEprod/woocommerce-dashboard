# Shopify integration — Phase 4 M1 / M2

This integration is read-only and awaits user acceptance. It does not replace Woo,
rename the application or migrate catalogue data.

## Installation and configuration

Choose WooCommerce or Shopify during first administrator creation. An installation
without `instance/commerce.json` defaults to WooCommerce. Connection settings cannot
switch an established installation; activation/migration needs a later reviewed
workflow. No Shopify credentials are required to boot.

Administrators use **Settings → Commerce connection** (`/settings/commerce`) to
enter `shop-name.myshopify.com`, an installed app's **Admin API access token**, and
a pinned API version. Version choices currently cover 2026-01 through 2026-10;
the default is 2026-10. This allowlist needs maintenance as versions retire.
Client secrets and Storefront API tokens are not Admin API credentials. Obtain an
appropriate installed-app token using Shopify's supported authentication flow;
automatic OAuth exchange and renewal are not implemented. Replace expiring tokens
manually. Request `read_products` for the next read-only discovery milestone.
Connection success reports granted scopes, not readiness for future publishing.

**Test connection** never saves. **Test and save** retests before atomic persistence
and leaves the active platform unchanged. A blank token retains the saved token
only for the same domain. Changed settings revisions require reload/review.
Normal authentication, administrator authorization and CSRF protect the route.

Tokens are not redisplayed, logged by this integration or placed in authored JSON.
Saved configuration uses private 0600 `instance/commerce.json`, not encryption at
rest. Protect the instance volume and backups. No extra secret backup is created.
Optional deployment variables are `SHOPIFY_STORE_DOMAIN`,
`SHOPIFY_ADMIN_ACCESS_TOKEN` and `SHOPIFY_API_VERSION`. Any nonempty variable makes
this whole configuration deployment-owned/read-only in the UI; provide a complete
domain/token pair. Docker operators must explicitly pass these variables through
their deployment configuration; this milestone changes no Compose ownership.

## M2 discovery — implementation awaiting live acceptance

M1 was checkpointed locally at `e2b454bb2a3eef85475fb635a3d85e03576a36dc`.
M2 remains uncommitted on `feature/shopify-foundation`.

Open **Settings → Commerce connection → Inspect Shopify catalogue**, or
`/shopify/discovery`. Shopify-selected installations also have a Discovery sidebar
destination. Administrators can inspect a prepared Shopify connection while the
installation remains WooCommerce. GET renders the shell without a remote call;
each CSRF-protected button makes one fixed read query. Test connection, Retrieve
products and Retrieve collections use the same adapter/transport as M1.

Products expose Shopify GID, title, handle, status, plain/escaped HTML descriptions
and option names/values. Inspect a product for variants (GIDs, SKU, price,
compare-at price, selected options and position) and media. Inspect variant media
for its associations. These are remote observations, not local identities or a
promise that matching SKUs authorize linking. No database mapping/import occurs.

Each list/detail connection retrieves at most 20 records per action. Product
detail shows 20 variants and 20 media; each variant initially shows up to five
media IDs and flags further associations. Separate **Next variants**, **Next
product media**, **Inspect variant media / Next variant media**, and **Next
members** controls expose additional pages without hidden fan-out. Pagination
evidence must be complete/advance; missing evidence is not an empty catalogue.
No automatic exhaustive traversal, background refresh or unbounded accumulation.

Media exposes Shopify media GID, content type, processing status, and for images:
file status, MIME type, image ID, actual CDN URL, dimensions, alt text and original
source URL when Shopify returns it. Processing images can have no URL yet. Order
is the returned connection order within the page, with the Shopify cursor—not
an invented absolute position on later pages. Original-source URLs can be signed
and short-lived. Nothing is fetched from those URLs; they are inspection links.

The current MediaImage/MediaImageOriginalSource contract has no separate original
filename field. A decoded URL basename is displayed only as a **hint**, never
proof of the original upload or a local filename. Local path/name/extension
remain unmapped. Shopify may optimize delivery independently of file extension;
the code never substitutes `.webp`, strips URL parameters or reconstructs URLs.
It never downloads, converts, renames, uploads or modifies local image metadata.

Collections expose GID/title/handle/description/sort order and paged product
membership. The still-supported legacy `ruleSet` field identifies automated
legacy rules when present; absent rules are labelled honestly rather than assumed
to prove a manual type in the evolving collection-source contract. Modern source
classification is deferred. No parent/child category tree is inferred. Shopify
menu navigation is a separate API and is not queried here; the local registry
continues to define intended storefront hierarchy. No registry mutation occurs.

`read_products` is required for product, variant, product-media and collection
reads (a granted write_products scope also permits reads, but this milestone
does not need it). No write, inventory, order or navigation scopes are requested.
Live acceptance requires the user's installed-app Admin token, domain, pinned
version and populated/empty-store read tests; mocked tests are not that evidence.

Official discovery contracts:
[MediaImage](https://shopify.dev/docs/api/admin-graphql/latest/objects/MediaImage),
[original source](https://shopify.dev/docs/api/admin-graphql/latest/objects/MediaImageOriginalSource),
[ProductVariant](https://shopify.dev/docs/api/admin-graphql/latest/objects/ProductVariant),
[Collection](https://shopify.dev/docs/api/admin-graphql/latest/objects/Collection).

## Network and safety boundary

Only allowlisted fixed GraphQL **queries** for connection/products/variants/media/
collections can use the shared transport. HTTPS transport is POST, but contains
no GraphQL mutation. Canonical myshopify domains only, no redirects, inherited
proxy/netrc disabled, connect/read timeouts 5/15 seconds, response limit 64 KiB for
connection and 1 MiB for discovery, with bounded response iteration. No automatic
retries: HTTP 429 and GraphQL THROTTLED stop with a wait/manual-retry message,
discarding partial evidence rather than looping or hammering the query budget.
Authentication, timeout, HTTP,
rate-limit, malformed evidence and version/domain mismatch errors are redacted and
actionable. No product, collection, inventory, order or menu writes are possible.

Woo adapters retain the current publisher path. Shopify-selected installations
guard Woo workflows instead of invoking them with Shopify credentials. Preparing
Shopify settings on Woo does not change existing mappings or publishing.

The recursively immutable local catalogue snapshot reuses resolved filesystem
metadata and projected rows. It preserves local product/child identities, SKU,
type, pricing, image and taxonomy references. It performs no remote reads, source
writes, scanner execution, SKU allocation or ORM autoflush. Remote platform IDs
will have separate mappings in a later milestone, never authored JSON.

## Verification and next milestone

Focused tests use fictional stores/mocked transport, including setup persistence,
credential protection, transport failures, immutable snapshots and targeted Woo
compatibility. Real Shopify connectivity remains unverified until the user supplies
their own installed-app credentials and performs the read-only connection test.

Next proposed M3: reviewed product publishing and separate Shopify identity
mappings, followed by staged media upload/processing/readback and explicit variant
media associations. Design review must first define ID persistence, Preview/digest
and recovery boundaries; do not reuse Woo IDs or reconstruct image URLs. Deferred:
inventory, orders, navigation editing/sync,
customisation fields, variant-media mapping, webhooks, OAuth/renewal and reviewed
platform switching. No new dependency or database migration is introduced.

Official contracts: [GraphQL Admin API](https://shopify.dev/docs/api/admin-graphql),
[API versioning](https://shopify.dev/docs/api/usage/versioning),
[access scopes](https://shopify.dev/docs/api/usage/access-scopes), and
[authentication](https://shopify.dev/docs/apps/build/authentication-authorization).
