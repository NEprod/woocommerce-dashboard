# Shopify foundation — Phase 4 M1

This milestone is read-only and awaits user acceptance. It does not replace Woo,
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

## Network and safety boundary

The only Shopify operation is the fixed GraphQL **query** for shop ID/domain and
current installation access scopes. Its HTTPS transport is POST, but it contains
no GraphQL mutation. Canonical myshopify domains only, no redirects, inherited
proxy/netrc disabled, connect/read timeouts 5/15 seconds, response limit 64 KiB and
bounded response iteration. No automatic retries. Authentication, timeout, HTTP,
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

Next: bounded read-only Shopify catalogue/collection/taxonomy discovery. Deferred:
product publishing and identifiers, inventory, orders, navigation editing/sync,
customisation fields, variant-media mapping, webhooks, OAuth/renewal and reviewed
platform switching. No new dependency or database migration is introduced.

Official contracts: [GraphQL Admin API](https://shopify.dev/docs/api/admin-graphql),
[API versioning](https://shopify.dev/docs/api/usage/versioning),
[access scopes](https://shopify.dev/docs/api/usage/access-scopes), and
[authentication](https://shopify.dev/docs/apps/build/authentication-authorization).
