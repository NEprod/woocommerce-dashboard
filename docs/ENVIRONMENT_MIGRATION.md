# Phase B: deployment identity and offline development migration

Do not switch the existing dashboard's store or reuse its original appdata.
Phase B adds tooling; it does not create the new Shopify product app, stage
real data, change Unraid, or approve a cutover. Existing **unmarked** deployments
with no `TLC_DEPLOYMENT_*` variables retain legacy behaviour. They do not
receive these new safeguards until enrolled.

## Managed identity

Set these as deployment configuration, not as values in catalogue JSON:

| Setting | Meaning |
| --- | --- |
| `TLC_DEPLOYMENT_ENV` | `development` or `production` |
| `TLC_DEPLOYMENT_STORAGE_ID` | 32 lowercase hex digits; generated and returned by staging for development |
| `TLC_DEPLOYMENT_SHOP_DOMAIN` | Permanent Shopify domain; development is `tlc-development.myshopify.com` |
| `TLC_DEPLOYMENT_SHOP_ID` | Development `gid://shopify/Shop/112056795527`; production `gid://shopify/Shop/103940129151` |
| `TLC_DEPLOYMENT_PRODUCT_APP_ID` | The authenticated product App GID. Obtain the development value in Phase C; production is `gid://shopify/App/434537660417` |
| `TLC_DEPLOYMENT_ALLOW_WRITES` | `false` (default) for acceptance; separately approve any manual write enablement |
| `TLC_DEPLOYMENT_ALLOW_AUTOMATION` | `false` (default); requires writes enabled **and** existing per-store automatic publishing approval |
| `PRODUCT_FOLDER`, `OUTPUT_FOLDER` | Explicit container catalogue and output roots; use `/catalogue`, `/output` for staged data |
| `SHOPIFY_STORE_DOMAIN`, `SHOPIFY_API_VERSION` | Same permanent domain; use `2026-10` for the product app |
| `SHOPIFY_CLIENT_ID` and `SHOPIFY_CLIENT_SECRET` **or** `SHOPIFY_ADMIN_ACCESS_TOKEN` | Exactly one form of product app authentication, supplied securely by the operator |
| `SECRET_KEY` | Independent protected value; managed session cookies use HTTPS and environment-specific names |

The independent Shopify read proves the authenticated permanent domain, Shop
GID, app ID and development-plan flag before Flask can migrate SQLite or start
workers. Later product and Promotions requests are bound to the verified
configuration. Deployment startup does **not** run Orders catch-up; the
operator can independently test manual reads. With automation off only
explicitly queued manual work is claimed. Managed Phase B rejects legacy
photo, Orders gateway, Woo, Discord-webhook, and manually pinned Online Store
publication settings. Do not enroll existing production until those services
have a separately verified configuration and migration plan.

Optional Promotions settings are the existing `TLC_PROMOTIONS_DEV_*` values
from [PROMOTIONS.md](PROMOTIONS.md), including the private PEM mounted
read-only (mode 0400/0600). The product app is not the Promotions app, and
production cannot use these development credentials. Leave optional settings
unset until independently verified.

Each writable root contains `.tlc-deployment.json`:

```json
{"version":1,"environment":"development","storage_id":"<32 lowercase hex digits>","role":"catalogue"}
```

Use role `instance`, `catalogue`, or `output`; also mark separately mounted
`intake` or `taxonomy` roots if used. Never copy markers or hand-enrol the old
data. Future clean production may enrol **empty, dedicated** roots only using
its own storage ID, with host mounts verified by the operator. The guard rejects
wrong role/identity, symlinks, overlapping roots and overlapping Linux mount
sources it can observe. It cannot inspect a second container's namespace:
the operator must compare actual host mount paths and backing locations,
including Unraid user-share/disk aliases. Do not expose either root through
another writable mount. A marked volume refuses legacy startup.

## Source and backup

An operator-approved source is a frozen backup bundle containing
`instance/site.db` and `catalogue/` at the same consistency point.
For the later cutover take a separate, protected backup of all writable roots
(including output/intake/taxonomy and persistent configuration if present),
record the original image digest/revision, pause writers and stop all
processes sharing those roots. Take a standalone SQLite backup with the
backup API, verify integrity/foreign keys, and record catalogue checksums.
Never copy an active SQLite database file alone. Rehearse restore to a
separate location. Do not put the backup, which can contain orders and
credentials, in Docker build context or mount it into the new dashboard.
See the [database backup contract](MIGRATIONS.md#backups).

## Offline commands

Use only synthetic data during Phase B. When separately approved for actual
data, use the verified, quiescent backup, preferably mounted read-only, and
an offline environment. The same module is included in the Docker image.

```sh
python -m app.development_migration dry-run --source /backup/verified-bundle
python -m app.development_migration stage --source /backup/verified-bundle \
  --destination /staging/new-development --approve <review_digest>
```

The dry run writes only a temporary verification SQLite database. It reports
table counts, detached fields, pending/mismatched scanner markers, review
counts, verified inventory balances and blockers without dumping source
records. The approval digest binds the reviewed schema, database and catalogue
content. Staging requires an entirely new directory, not nested in the backup.
Use only a successful result with `staged: true`. Do not mount a partially
created destination from a failed staging run; pick a new directory after
investigation.

The allowlisted transfer creates a fresh schema at `0025_promotions`. It carries
catalogue and variant identities, authored JSON and supported source assets,
local packaging, inventory IDs, reserved location, barcodes, complete ledger,
recipes and costs. It checks database integrity, BOM item references and
scanner-to-database SKU consistency. It clears old remote state instead of
pretending re-linking is safe. Generated output and image projections are
rebuilt by an explicit scan with retained SKU markers. Original orders, users,
credentials, settings, webhook/notification and private upload state are not
imported. Use a new local administrator. Only approved **test** assets belong
in the source catalogue: the tool cannot recognise private content in an image,
arbitrary free text or supported design file. Review test assets before staging.

Unknown schemas, linked files, unsupported file types, database journals,
ambiguous scanner or relationship recovery, and unrecognised Shopify GIDs stop
the transfer. Do not force past them. Review old Market selections in the
backup and deliberately choose dev equivalents; staging never remaps them.
Fixed-variant or segment promotion selections are detached, reported and
blocked for publication until explicitly reviewed in the editor. Promotions
remain local drafts (archived records stay archived). Draft/Live product intent
survives but remote schedules and pending jobs do not. Recoverable original
state stays with the backup.

## Cutover and rollback — separate approval

1. Get approval for Phase C. Verify the new product app on TLC Development,
   and keep the live app, live photo proxy and existing Promotions credentials
   unchanged.
2. Get approval for the backup and migration. Review the dry run before
   staging. Verify the staged data and its isolation. On Unraid, the user alone
   later maps staged `instance`, `catalogue`, and `output` to
   `/app/instance`, `/catalogue`, and `/output` in the existing **development**
   container, with its own HTTPS endpoint and the verified dev settings.
3. Start with managed writes and automation disabled. Verify catalogue/stock
   counts, per-location balances, BOM costs and barcode lookups, and set up
   a new administrator. In a separate approved acceptance step, enable
   manual writes only, publish one reviewed test product, and verify new dev
   Shopify IDs and readback before further publishing. Leave auto and
   Orders/webhook/photo integrations off until each is accepted.
4. On failure, stop the replacement, preserve its failed state for diagnosis,
   and use the paired original image and verified data backup if restoring.
   Never use the staged copy as a live-shop backup, or automatically reconnect
   original data to the live shop with its remote writers enabled.
