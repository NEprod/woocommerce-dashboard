# Shopify integration — Phase 4 M1 / M2 / M3A

Connection/discovery remain read-only. M3A adds separately reviewed creation of
one Simple draft with original images; live acceptance is pending. It does not
replace Woo, rename the application or migrate catalogue data.

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
Manual Admin tokens remain operator-managed. M3A.1 additionally supports automatic
Client Credentials acquisition/renewal through Docker/Unraid (below).
Request `read_products` for discovery and `write_products` for M3A.
Connection success reports granted scopes, not readiness for future publishing.

### M3A.2 store domain verification

Connection validation requests the authenticated Shop GID, `myshopifyDomain`,
`primaryDomain.host` and connected `domains.host`. The configured myshopify host
must exactly match one of those authoritative hosts; successful authentication
alone is insufficient. Shopify can retain an original myshopify domain while
another is the storefront primary. The connected-domain list is currently a
deprecated but available GraphQL field in the pinned supported versions; review
its replacement when updating the API allowlist.

New draft mappings use the stable Shop GID to derive their store key. Existing
domain-keyed mappings are found by their persisted Shop GID without rewriting
them, preserving duplicate prevention and recovery across verified aliases.
Reconciliation still rejects a different Shop GID. Changing the configured host
after Preview requires a fresh Preview; signed confirmation remains strict.
No migration or live store mutation is needed for this correction. Live connection
acceptance against the user's two domains remains to be performed in Unraid.

References: [Shop GraphQL contract](https://shopify.dev/docs/api/admin-graphql/latest/objects/Shop)
and [Shopify domain changes](https://help.shopify.com/en/manual/domains/domain-type/change-primary-domain).

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
domain/token pair. Compose now explicitly passes all three optional variables
from `.env`; the Unraid template has matching optional fields (token masked).
Blank values preserve Dashboard settings. A partial environment configuration
never borrows saved credentials. Deployment values are not redisplayed on the
connection page; testing is read-only and saving/overwriting them is refused.
Environment configuration does not activate Shopify or change the installation
platform. Credentials remain separate from all product identity records.

## M3A.1 — Client Credentials authentication

Shopify supports this server-side grant for a **Dev Dashboard app installed on a
store in the same Shopify organization as the app**. This fits the intended
TLC-owned app/store deployment only if that organization condition is true;
installation alone is not proof. Live connection acceptance must confirm it.
Apps serving other organizations require a separately scoped authentication flow;
the application does not invent a fallback or OAuth redirect workaround.

In Dev Dashboard create/select the app, configure its version with `read_products`
and `write_products`, release/install the version on your own store, then open
**Settings** for the **Client ID** and **Client Secret**. The Client ID identifies
the app; it is not an access token. An already installed compatible app does not
need reinstalling merely to change TLCDashboard authentication. Scope changes
may require the normal Shopify version/permission approval process.

For Docker/Unraid, set:

```dotenv
SHOPIFY_STORE_DOMAIN=your-shop.myshopify.com
SHOPIFY_CLIENT_ID=<Dev Dashboard Client ID>
SHOPIFY_CLIENT_SECRET=<Dev Dashboard Client Secret>
SHOPIFY_ADMIN_ACCESS_TOKEN=
SHOPIFY_API_VERSION=2026-10
```

Both client fields are masked in the Unraid template. Compose passes them through;
no actual credentials belong in repository files. To switch from manual tokens,
remove/blank the deployment Admin API token, add both client fields and restart
the container. Keep appdata and the selected platform unchanged. For manual-token
mode, blank both client fields and provide domain/token. Both modes present,
partial client pairs, invalid domains or missing deployment credentials are
rejected; no values are combined with saved Dashboard configuration. With all
Shopify environment variables absent/blank, protected manual Dashboard settings
continue to work. Deployment-owned settings can be tested but never overwritten
or redisplayed by the Dashboard.

The existing Shopify transport posts form-encoded `grant_type=client_credentials`,
Client ID and Client Secret to the validated store's `/admin/oauth/access_token`.
It validates `access_token`, `expires_in` and `scope` from Shopify. Tokens live only
in a process-private bounded cache (16 credential/store keys) and are renewed on
demand up to 60 seconds before returned expiry. Short lifetimes use a proportionate
margin. No refresh token is required: renewal repeats the documented grant.
A lock coalesces simultaneous acquisition; failed authentication is held for
30 seconds to avoid storms. Restart discards cached tokens and reacquires on the
next Shopify request; no background scheduler or token persistence is introduced.

The token is attached only to Admin GraphQL requests. Staged binary uploads still
use Shopify's signed upload parameters without Admin tokens/client secrets.
Preview fingerprints use stable credentials, not acquired tokens: normal renewal
does not invalidate a review, but changed credentials still require fresh Preview.
No short-lived tokens are stored in authored JSON, identities, operation evidence
or configuration files. Errors are generic/redacted, responses bounded to 64 KiB
for authentication, connect/read timeouts 5/15 seconds, redirects/proxy inheritance
disabled. Authentication requests are never automatically retried. A GraphQL 401
invalidates the cached token and starts the cooldown; it does **not** replay the
failed read/write. An uncertain draft remains subject to existing reconciliation.

Open **Settings → Commerce connection → Test connection**. This obtains/caches a
token and reads shop identity/scopes; it does not publish. On failure check app
installation, same-organization ownership, credentials, granted scopes and
connectivity. Correct the environment/restart or wait out the cooldown before
another explicit test. Client Credentials are not universal merchant OAuth;
multi-worker deployments have separate caches, while the supported container
continues to use one worker with four threads. Docker administrators can inspect
environment values, so secure container access and backups.

Official contracts: [Client Credentials Grant](https://shopify.dev/docs/apps/build/authentication-authorization/client-credentials-grant),
[access tokens](https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens).

## M2 discovery — implementation awaiting live acceptance

M1 was checkpointed locally at `e2b454bb2a3eef85475fb635a3d85e03576a36dc`.
M2 was checkpointed locally at `9b01d63ca4bdbc3aa175e2112983c9031486da5a`;
neither checkpoint was pushed/released. M3A is source checkpointed locally;
temporary commit-tagged AMD64 image acceptance is separate from release approval.

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

Discovery accepts only allowlisted fixed GraphQL **queries**; its HTTPS POST
contains no GraphQL mutation. The separate M3A confirmation service permits only
stagedUploadsCreate, productCreate and productVariantsBulkUpdate. Canonical
myshopify domains only, no redirects, inherited
proxy/netrc disabled, connect/read timeouts 5/15 seconds, response limit 64 KiB for
connection and 1 MiB for discovery, with bounded response iteration. No automatic
retries: HTTP 429 and GraphQL THROTTLED stop with a wait/manual-retry message,
discarding partial evidence rather than looping or hammering the query budget.
Authentication, timeout, HTTP,
rate-limit, malformed evidence and version/domain mismatch errors are redacted and
actionable. Discovery cannot write. M3A does not write inventory quantities,
collections, orders, menus or sales-channel publications.

Woo adapters retain the current publisher path. Shopify-selected installations
guard Woo workflows instead of invoking them with Shopify credentials. Preparing
Shopify settings on Woo does not change existing mappings or publishing.

The recursively immutable local catalogue snapshot reuses resolved filesystem
metadata and projected rows. It preserves local product/child identities, SKU,
type, pricing, image and taxonomy references. It performs no remote reads, source
writes, scanner execution, SKU allocation or ORM autoflush. M3A remote platform
IDs have separate mappings, never authored JSON.

## M3A — reviewed single Simple draft, awaiting live acceptance

Open **Settings → Commerce connection → Review one Simple Shopify draft**, or
`/shopify/drafts`. This administrator-only proof of concept may use prepared
Shopify credentials while Woo remains active. Page GETs make no remote calls.
Preview reads the immutable local snapshot/original images and verifies Shopify
store identity/scopes using reads only. It shows local identity/SKU, description,
price (in Shopify store currency), DRAFT status and ordered original filenames,
extensions, references, sizes and alt text. All projected parent images are
included; there is no image-subset editor in M3A.

Confirmation requires CSRF, explicit acknowledgement and a signed 15-minute
Preview. Source/projection, original byte hashes, credentials and store identity
are rechecked. The existing CatalogueOperation lock/history is reused. A durable
pending Shopify identity is committed **before the first mutation**, and a
create-attempt marker is committed before productCreate. Preview alone never
reserves identities or uploads anything.

Approved migration `0010_shopify_product_identity` follows `0009_woo_live_state`
and adds only `shopify_product_identity`. It stores verified store GID/key/domain,
local product/source identity/SKU, nullable Shopify product GID, operation reference,
reviewed digest, state/verification state and timestamps. SQLite enforces unique
store/local-product, store/source and store/remote-product pairs. Version-1 media
evidence is bounded to 64 KiB in the writer and database; it retains reviewed
local references/hashes, returned media IDs/URLs, processing observations and
recovery stage/error. No credentials or signed upload targets are stored there.
No backfill; existing Woo mappings are untouched. Nullable SET NULL references
preserve the identity reservation if local projection/history rows are removed.

Original supported PNG, JPEG and WebP bytes are read only from authorized catalogue
paths, checked for format/extension consistency, then sent through Shopify staged
multipart upload targets without the Admin token. There is no local conversion,
rename, download or source write. Bounds: one active Simple parent, no child rows,
one to six images, 10 MiB each / 30 MiB total, title 255 characters and description
8,000 characters (also subject to the evidence bound). Signed targets are not
retained. Product creation associates media in reviewed order with reviewed alt
text; the sole default variant then receives SKU/price. A unique operation handle
and correlation metafield distinguish this attempt from arbitrary matching SKUs.

Independent product readback verifies store/product correlation, DRAFT status,
title/description, sole variant SKU/price, media IDs/order/alt and processing state.
Actual returned Shopify CDN URLs are stored unchanged; an upload extension is not
a promise about browser delivery format. READY media with returned URLs yields
verified; asynchronous processing remains partial/processing until explicit read
reconciliation. Strict description comparison may require inspection if Shopify
normalizes submitted HTML; it never silently accepts a mismatch.

Official write contracts:
[stagedUploadsCreate](https://shopify.dev/docs/api/admin-graphql/latest/mutations/stagedUploadsCreate),
[productCreate](https://shopify.dev/docs/api/admin-graphql/latest/mutations/productCreate),
[productVariantsBulkUpdate](https://shopify.dev/docs/api/admin-graphql/latest/mutations/productVariantsBulkUpdate).

### Partial failures and recovery

Known/uncertain creation permanently blocks another create through this workflow,
including after restart or an empty search. There is no automatic write retry,
write-resume, rollback or remote deletion. Failures before productCreate was even
attempted permit a new reviewed operation; orphan staged uploads are not deleted.
Startup marks interrupted operations using the existing recovery contract; durable
Shopify identity reservations survive.

Open the retained identity page and **Reconcile using Shopify reads only**. Known
IDs are independently read. Unknown IDs use the exact unique handle or an explicit
candidate product GID, then require the operation correlation. If creation timed
out before media receipts were retained, the operator must inspect the observed
IDs/URLs/order and explicitly approve that mapping; signed fresh remote/local
digests are checked before adoption. Missing/wrong fields, ambiguous candidates,
failed media or incomplete evidence stay blocked. Correct the retained draft in
Shopify Admin where needed, then reconcile. Historical CatalogueOperation results
are not rewritten by reconciliation; current identity verification is separate.

### Manual acceptance — one real TLC product

1. Back up the persistent app database/instance volume before upgrading to 0010.
   Configure the installed app's Admin token with `write_products` (which also
   permits product reads), domain and supported version through Docker/Unraid or
   protected settings. Test connection; do not change the active platform.
2. Choose one scanned active Simple product with stable SKU, valid price and one
   to six original PNG/JPEG/WebP parent images on the readable catalogue mount.
   Record its local metadata/image hashes and current Woo mapping if applicable.
3. Build Preview at `/shopify/drafts`; review fields, filenames/order/alt and
   price in the store currency. Confirm once with the explicit acknowledgement.
4. Inspect the retained identity/operation and Shopify Admin: one unpublished
   DRAFT, correct SKU/price, original gallery order/alt. Do not publish to channels.
   Use read-only reconciliation if images are still processing.
5. Check verified product/media GIDs and actual returned URLs, unchanged source
   files/SKU/Woo identities, and that a second create is refused. For failures,
   follow the retained identity instructions rather than pressing Create again.

Categories/collections/navigation, controlled attributes, tags, relationships,
inventory quantities, Variable products, updates to existing Shopify products and
full synchronization are deliberately omitted. No extra dependency was added.

## Verification and next milestone

Focused tests use fictional stores/mocked transport, including setup persistence,
credential protection, transport failures, immutable snapshots and targeted Woo
compatibility. Real Shopify connectivity remains unverified until the user supplies
their own installed-app credentials and performs the read-only connection test.

Next: live acceptance of M3A's single Simple draft, then a separately scoped
reviewed update/variant publishing milestone; do not reuse Woo IDs or reconstruct
image URLs. Deferred:
inventory, orders, navigation editing/sync,
customisation fields, variant-media mapping, webhooks, merchant OAuth and reviewed
platform switching. Client Credentials renewal is implemented in M3A.1; other
merchant OAuth/token lifecycle flows remain deferred. Only the approved M3A
identity migration is introduced; M3A.1 adds no migration.

Official contracts: [GraphQL Admin API](https://shopify.dev/docs/api/admin-graphql),
[API versioning](https://shopify.dev/docs/api/usage/versioning),
[access scopes](https://shopify.dev/docs/api/usage/access-scopes), and
[authentication](https://shopify.dev/docs/apps/build/authentication-authorization).
