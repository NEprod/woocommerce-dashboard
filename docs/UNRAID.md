# TLCDashboard on Unraid

## Image-sync template cleanup after deployment

After the image-sync image is deployed to development and its scoped scan and
Shopify media review are verified, the operator may remove these **three**
obsolete entries from `WooCommerceDashboard-dev`: `Generated Output` (`/output`),
`Output Container Root` (`OUTPUT_FOLDER`) and `Public Image URL Prefix`
(`URL_PREFIX`). The same entries can be omitted from a future production
template. The source catalogue mount (`/catalogue` / `PRODUCT_FOLDER`) and all
original images must remain. Do not delete historical uploads or change the
running Unraid template as part of this repository release.

These are repository templates, not changes to any installed container:

| Template | Image channel | Use |
| --- | --- | --- |
| [TLCDashboard Development](../unraid/my-tlcdashboard-develop.xml) | `neprod/woocommerce-dashboard:develop` | The existing `WooCommerceDashboard-dev` container and TLC Development |
| [TLCDashboard Production](../unraid/my-tlcdashboard-production.xml) | `neprod/woocommerce-dashboard:latest` | Production reference only, pending a separate controlled production cutover |

For updates, prefer a verified immutable tag or digest over either rolling
channel. Do not assume the currently published stable image implements the
managed deployment contract in the Production XML. Verify its revision and
capabilities independently before using the template. Do not install or import
it over the existing Tender Loving Creations dashboard. There is no production
release, deployment, app change, or data migration in this template cleanup.

## Before first start

For a genuinely new catalogue, the first-run configuration page now offers
**Start with an empty catalogue**. After storage/Taxonomy readiness is valid, an
administrator can explicitly finish setup and reach the dashboard without a
scan or any product folders. This writes only the persistent onboarding
completion record; it creates no products, SKUs or scan receipts. Non-empty
folders, existing catalogue records and active catalogue operations must still
use the existing scan/reconstruction path. No mount or credential changes are
made by this option.

Set the template values in Unraid **before** starting a new managed container.
The application reads these on startup. They are not entered on the dashboard
first-run page. The dashboard still handles local administrator onboarding on
genuinely new data, initial scanning and verification when authorised.

- Required: a strong independent `SECRET_KEY`, the correct existing
  `TLC_DEPLOYMENT_STORAGE_ID`, environment identity, separate writable mounts,
  the permanent Shopify domain in both domain
  fields, and one complete Product app credential mechanism. Use **either**
  `SHOPIFY_CLIENT_ID` with `SHOPIFY_CLIENT_SECRET` **or**
  `SHOPIFY_ADMIN_ACCESS_TOKEN`. Both can be represented in the XML; do not
  provide both at runtime. Keep real values in the existing secure container
  configuration, not in Git. Unraid field masking is display-only, not a vault.
- Managed storage identity and Shopify IDs must match the mounted role markers
  and the authenticated Shopify app/shop. Preserve the existing storage ID and
  files. Do not hand-enrol, copy markers between environments or point another
  container at the same SQLite database. Follow
  [deployment identity and storage requirements](ENVIRONMENT_MIGRATION.md) for a
  separate, deliberately prepared production deployment.
- A genuinely fresh installation now initialises its own role markers after
  complete deployment configuration and authenticated Shopify identity checks.
  All managed roots must be separate writable Linux mounts, all markers must be
  absent, and every managed directory must be empty. Existing databases, product
  files, registry files, partial markers and conflicting identities are refused;
  they are never adopted or repaired automatically. Marker files are published
  atomically without replacement and remain readable after the privilege drop.
  Interrupted/partial state fails closed and needs operator review, not another
  installation's markers. This requires no new settings or mappings and does
  not authorise Shopify writes or change normal first-run setup.
- `TLC_DEPLOYMENT_ALLOW_WRITES` and `TLC_DEPLOYMENT_ALLOW_AUTOMATION` default to
  `false`. Keep automation off. For a running development deployment, retain its
  existing, separately authorised manual-write setting. Write permission still
  does not replace plan review and approval. Automation additionally requires
  an explicit in-app policy. Neither switch should be changed just for an
  image update.
- The normal Admin API defaults to `2026-10`. Match the installed Shopify app
  and verify effective scopes in **Commerce connection → Test connection**.
  Scopes are Shopify app permissions, not environment variables. Channels and
  Markets come from connected-store discovery plus reviewed catalogue intent;
  never paste another store's IDs into them. Read-only Orders Sync Now is
  separate. Managed startup disables Orders catch-up even if the startup toggle
  is supplied.
- Keep bridge networking and the image’s TCP port `7485`. A different host
  port can be used if necessary; keep the existing HTTPS endpoint for managed
  login and review. Managed session cookies require HTTPS. Do not make the
  container privileged or expose its administrative endpoint publicly.

## Persistent storage

| Container target | Existing development host path | Contents |
| --- | --- | --- |
| `/app/instance` | `/mnt/cache/appdata/woocommerce-dashboard-dev/instance` | SQLite, local logins, inventory, materials, BOMs, orders, publishing receipts and backups |
| `/catalogue` | `/mnt/user/data/tlc/09_Development_Products/products/` | Shared and product JSON, originals, SKU index and scanner markers |
| `/output` | `/mnt/user/data/tlc/09_Development_Products/uploads/` | Legacy WooCommerce output; remove the Path mapping after image-sync deployment validation, without deleting historical files |
| `/intake` | `/mnt/user/data/tlc/09_Development_Products/intake/` | Optional independent staging area for loose and prepared product images |
| `/taxonomy` | `/mnt/user/data/tlc/09_Development_Products/taxonomy/` | Optional authoritative registry and verified backups |

Production host paths are deliberately blank. Select existing, dedicated
production directories only after approval. If using Intake or Taxonomy, mount
them independently and retain their role markers. Do not nest the mounts,
change `/app/instance/site.db` to a different destination, or change Syncthing.
Back up the database and catalogue as a consistent pair before replacing an
image; never copy only an actively written SQLite file. See
[database backups](MIGRATIONS.md#backups) and
[storage retention](STORAGE_RETENTION.md).

The template exposes `PUID=99`, `PGID=100`, `UMASK=002`, and the container
timezone. Keep existing ownership values when updating. The image drops to the
configured non-root user; only application-owned instance storage receives a
recursive ownership adjustment. Scanner, Royal Mail Click & Drop CSV export,
customs, shipping, packaging, inventory and production workflows keep using
their existing dashboard/catalogue settings and storage. No extra credentials
or storage variables are introduced for those workflows.

## Optional integrations and compatibility limits

- **Product configuration:** leave shipping/stock/origin defaults blank unless
  intended; authored settings win. Typed finished dimensions keep their
  existing metafield namespace. Native packed dimensions are opt-in and still
  require appropriate Shopify access and measured packaging. Finished
  dimensions are never substituted. Y081 and publication/Market intent keep
  their own existing reviewed workflows.
- **Promotions:** available only with the verified **development** installation.
  Fill the existing `TLC_PROMOTIONS_DEV_*` fields together to opt in, provide a
  separate RSA signing PEM as a single read-only file readable by `PUID`, and
  set the private-key path to its container destination. Enter the verified
  development UK Market GID when using exact GBP discounts. The application
  discovers the fixed `tlc-exact-multibuy` Function on the expected app; no
  Function ID is entered in Unraid. Keep all Promotions entries and its mount
  blank if unused. Production Promotions is not implemented in the current
  transport; the Production XML intentionally contains no development
  Promotions credentials or mock production equivalents. See
  [Promotions](PROMOTIONS.md).
- **Private customer photos:** both templates expose the existing dashboard
  variables `TLC_PHOTO_WORKER_URL`, `TLC_PHOTO_WORKER_TOKEN` (masked) and
  `TLC_PHOTO_PROXY_PATH`. Leave all three blank to disable dashboard photo
  access. If any is set, managed startup validates all three before contacting
  Shopify. A saved token or URL from another environment is not a fallback.
  The Worker must prove the configured app-proxy path and exact Shopify shop
  before publishing a photo field or sending Orders identifiers for binding or
  retrieval. Use separate Workers, private R2 buckets, app proxies and secrets
  for each environment. The app-proxy secret, signing key and R2 binding belong
  on the Worker, not in Unraid; only the per-environment dashboard token is
  shared between the two. The supported default preset is
  one JPG/JPEG, PNG or WebP up to 10,000,000 bytes. Secure retrieval is through
  Orders admins, not a public R2 URL. See [private photo setup and acceptance](CUSTOMER_PHOTOS.md).
- **Other legacy options:** WooCommerce credentials, Discord webhook URLs,
  the outbound Orders webhook queue, and the pinned Online Store navigation
  override remain documented under their real environment names for
  migration/reference. Leave them blank on managed deployments. Do not
  remove settings from an existing unmarked legacy container using them
  just because the managed templates show blank defaults.

The XML intentionally does not expose Compose-only host variables or `APP_PORT`;
Unraid uses its Path and Port entries. Build version and supervisor-owned
settings are not operator inputs. The retired
`SHOPIFY_PACKED_DIMENSIONS_USE_PRODUCT_DIMENSIONS` setting has no effect and
has been removed. Cloudflare-side bindings, secrets, limits and retention are
set in the existing Worker, and are not dashboard environment settings.

## Docker log retention and workers

Keep one Gunicorn worker, four threads and the existing single scheduler.
Manual publishing uses 100 requests / 120 seconds per worker slice; the hourly
publisher uses 180 requests / 110 seconds, with narrower readback limits in
specific steps. The scheduler lease is 120 seconds and is renewed. These
are application-owned limits, not supported Unraid tuning variables; do not
override the image command or increase them to hide a pause. Durable
checkpoints and acknowledgements are stored in the existing SQLite instance.

The optional Docker **Extra Parameters** in both templates select the local
log driver (five files, 10 MiB each, compressed). Retain an equivalent
host-managed logging policy if already configured; do not add competing file
logging. Application logs go to stdout/stderr, and operation receipts contain
the bounded worker and verification evidence used for diagnosis.

## Updating the existing development container

1. Wait for the approved immutable development tag, digest and embedded Git
   revision. Take a verified backup and record the previous pinned image.
   Do not run a second container on these mounts.
2. Edit **WooCommerceDashboard-dev** in Unraid. Change only the Repository
   image reference to the verified pinned tag or digest. Check the existing
   host mounts, storage ID, port, HTTPS endpoint, `SECRET_KEY` and Product app
   configuration are unchanged. No new environment fields are required to
   activate continuation. Do not import the new XML over the existing values
   or turn on hourly publishing. Leave photos off unless the separate
   development Worker, private bucket, token and signed proxy are verified.
3. After Unraid applies the update, confirm startup and login over the same
   HTTPS address, and confirm read-only that the Product app still connects
   to TLC Development rather than production.
4. Reconcile the retained `CCCC-0001` Draft read-only. Use **Review remaining
   changes** only if it keeps the current Shopify identity and proposes the
   outstanding availability changes without recreating variants or re-uploading
   media. Separately approve any Shopify writes.
5. When that product reaches **Up to Date**, verify one fresh, previously
   unpublished variable product and one narrowly scoped partial update on
   TLC Development. Review receipts before considering hourly publishing.
