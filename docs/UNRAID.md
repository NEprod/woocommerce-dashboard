# Unraid Installation and Persistence

Use `neprod/woocommerce-dashboard:0.2.3` for a pinned Phase 1 installation. The
image supports `linux/amd64` for Unraid and `linux/arm64` for Apple Silicon. It
runs as a non-root user with one Gunicorn worker, four threads, and container
port `7485`.

The tracked template is `unraid/my-woocommerce-dashboard.xml`. It can reproduce
a manually configured container or be saved as an Unraid user template. It is a
basis for a future Community Applications submission; the application is not
currently claimed to be listed in Community Applications.

## Required container settings

- Repository: `neprod/woocommerce-dashboard:0.2.3`
- Network: `bridge`
- Container port: `7485/tcp`; default host port: `7485`
- WebUI: `http://[IP]:[PORT:7485]/`
- Architecture: `linux/amd64` and `linux/arm64`

Persistent mappings:

```text
Database and application backups:
/mnt/user/appdata/woocommerce-dashboard/instance → /app/instance (read/write)

Product catalogue and scanner state:
user catalogue share → /catalogue (read/write)

Generated output:
user output share → /output (read/write)

Optional Catalogue Intake staging area:
user-selected Unraid share or folder → /intake (read/write)
```

All three mappings are required. `/app/instance` contains the application-owned
`site.db` plus `backups/`. `/catalogue` contains collection folders,
`product_info.json`, source images, `.scanned`, `.scanned.pending`, `.update`,
and `sku_index.json`; it must be writable. `/output` contains processed/generated
images and must also be writable. Keep catalogue and output in purpose-specific
shares rather than under appdata.

`Catalogue Intake` is an optional fourth path. Leave its host path empty when
the feature is not used. To enable it, edit the container, choose a user-owned
Unraid share or folder as the host path, keep container path `/intake`, select
Read/Write mode, apply the template change, and restart the container. Do not
enter `/catalogue`, `/output`, `/app/instance`, or a personal path copied from
another system. The application displays intake-relative paths only.

Browsing and grouping/rename/metadata previews remain read-only. Explicitly confirmed
grouping revalidates its preview, copies unchanged images through hidden private
staging, and promotes a verified duplicate-safe provisional result below
`/intake/Prepared/`. Later folder, image, and metadata steps advance that same
visible result through hidden staging and rollback. Metadata save creates or
updates only root `product_info.json`; it does not alter loose sources or invoke
the scanner. The explicitly confirmed final handoff copies the complete verified
Prepared result into hidden staging beneath `/catalogue`, then creates or safely
replaces exactly one collection using protected rollback. Prepared remains
unchanged and Append Scan remains a separate manual action. Both `/intake` and
`/catalogue` must be genuine readable mounts; catalogue handoff additionally
requires `/catalogue` to be writable. Read/write mode is required for confirmed
operations; read-only mounts still permit previews where practical. See
[Catalogue Intake](CATALOGUE_INTAKE.md).

Unraid user shares may reject Linux `RENAME_NOREPLACE` with `EINVAL`. The
application handles that capability result by retaining the dedicated mutation
lock, confirming staging and `Prepared` are on the same mounted filesystem,
rechecking that the selected destination is absent, and using the compatible
ordinary directory rename. It never uses `os.replace()`, never copies into a
visible partial result, and reports a controlled failure if safe promotion is
not possible.

Do not change the database mount to `/config` and do not rename `site.db`.
Existing installations rely on `/app/instance/site.db`; changing either path can
make a populated installation appear new.

## Environment

Use these Unraid ownership defaults:

```text
PUID=99
PGID=100
UMASK=002
```

`PUID` is the UID used by the application process, `PGID` is its primary GID,
and `UMASK` controls permissions for new files. Unraid commonly uses `99:100`.
The image must consume these variables; adding them to XML alone cannot change a
fixed container user. This image starts as root only for entrypoint permission
preparation, then uses `gosu` to run application import, migrations, Gunicorn,
and its worker as the configured non-root identity.

`SECRET_KEY` is required. Generate a strong value on a trusted shell and paste
only the result into the Unraid variable:

```bash
openssl rand -hex 32
```

**Before updating the development container to the storage-hardening image,
confirm that its `SECRET_KEY` field contains a stable generated value.** The new
image intentionally refuses missing values, `dev-secret`, `changeme`, and obvious
example placeholders. It never prints or saves the supplied value.

Discord is disabled by default with `DISCORD_ENABLED=false`. It is optional and
delivery failure does not fail a scanner operation. All fields apply after a
container restart:

| Field label | Variable | Type | Default | Required | Secret | Placeholder example |
|---|---|---|---|---|---|---|
| Enable Discord Notifications | `DISCORD_ENABLED` | Variable | `false` | No | No | `true` |
| Discord Display Name | `DISCORD_DEFAULT_USERNAME` | Variable | `WooCommerce Dashboard` | No | No | `Catalogue Scanner` |
| Discord Avatar URL | `DISCORD_DEFAULT_AVATAR_URL` | Variable | empty | No | No | `https://example.invalid/avatar.png` |
| Discord Scanner Updates Webhook | `DISCORD_WEBHOOK_SCANS_INFO` | Masked variable | empty | No | Yes | `https://discord.com/api/webhooks/REPLACE_ME` |
| Discord Scanner Warnings and Failures Webhook | `DISCORD_WEBHOOK_SCANS_ERRORS` | Masked variable | empty | No | Yes | `https://discord.com/api/webhooks/REPLACE_ME` |
| Discord Metadata Updates Webhook | `DISCORD_WEBHOOK_EDITS` | Masked variable | empty | No | Yes | `https://discord.com/api/webhooks/REPLACE_ME` |
| Discord Product Overrides Webhook | `DISCORD_WEBHOOK_OVERRIDES` | Masked variable | empty | No | Yes | `https://discord.com/api/webhooks/REPLACE_ME` |
| Discord Product Ingest Webhook | `DISCORD_WEBHOOK_INGEST` | Masked variable | empty | No | Yes | `https://discord.com/api/webhooks/REPLACE_ME` |

Existing users should update their template to receive the clearer labels. The
target environment variable names remain compatible, so existing values do not
need renaming. Keep the ingest webhook empty if per-product messages would be
noisy. Treat every webhook as a secret. See
[Scanner, Operations, and Discord](SCANNER_OPERATIONS.md).

The authenticated `/settings` page presents only safe availability and
configured/not-configured states. It never renders mount paths, webhook values,
`SECRET_KEY`, or an environment dump. Environment and Discord configuration
remain owned by the Unraid container template; restart the container after a
change. The page is diagnostic and read-only, not a secret editor.

## First start

1. Create/select the three required host directories and ensure the container can write
   to them.
2. Optionally select a separate Catalogue Intake folder and map it read/write to
   `/intake`.
3. Install the XML as a user template or reproduce its fields manually.
4. Set a generated `SECRET_KEY`; leave Discord disabled initially.
5. Start the container and open `http://<unraid-ip>:7485/`.
6. Complete `/setup`, then enter the container paths `/catalogue` and `/output`
   in initial settings.
7. Review the initial-scan classification before starting any catalogue action.

## Docker log retention

Compose users receive the project policy automatically. For an Unraid container,
add the equivalent Docker options in **Extra Parameters**:

```text
--log-driver local --log-opt max-size=10m --log-opt max-file=5 --log-opt compress=true
```

This retains five 10 MiB stdout/stderr files with compression where supported,
an approximate 50 MiB pre-compression ceiling. If the Unraid host centrally
manages Docker log rotation, verify that its effective policy is at least as
strict and do not configure duplicate application file logging.

Reconstruction preserves existing marker/SKU identities while rebuilding the
database projection. Intentional full regeneration retains the protected
SKU-reset behaviour and must not be used as a substitute for reconstruction.

## Updating and replacing the container

Stop the container, back up the mounted data, then update or replace only the
image/container definition. Reusing the same three mounts preserves the database,
catalogue identities, and output. Startup migrates `/app/instance/site.db` to the
current migration head before Gunicorn accepts traffic.

Pin `0.2.3` for repeatable deployments. `phase-1` and `latest` track newer
compatible publications and therefore change over time. Verify backups and read
release notes before moving a pinned installation.

## Backup and restore

Back up the appdata instance directory and catalogue separately at an understood
consistency point. The instance backup covers:

```text
/app/instance/site.db
/app/instance/backups/
```

The catalogue backup covers authored JSON, source assets, SKU indexes, and marker
state. Back up `/output` when generated files are not reproducible or are costly
to recreate. Image replacement does not remove correctly mounted data, but it is
not a backup.

Migration and reconstruction backups are unique SQLite files beneath
`/app/instance/backups/`. To restore appdata, stop the container, restore the
instance directory with ownership/permissions suitable for the container user,
then start the pinned image. For an individual database backup, use the controlled
restore procedure in [Database Migrations](MIGRATIONS.md); never replace SQLite
while Gunicorn is running.

The application bounds its own backup history as documented in
[Storage and Retention](STORAGE_RETENTION.md). This does not replace an Unraid
appdata/catalogue backup. `/app/instance/backups` is restricted to mode `0700`
and database backups remain `0600`.

## Template use

For an existing manually configured container, compare its port, environment,
and all three paths with the XML before applying changes. In Unraid, a configured
container can be saved as a user template from the Docker edit page; retain the
fixed image tag and do not save real webhook values into a shared XML file.

The tracked XML and stable raw GitHub icon URL are suitable inputs for a future
Community Applications submission, but no submission or listing is asserted by
this release.

## Troubleshooting

- Inspect the Unraid Docker log for Gunicorn startup and migration errors.
- For `sqlite3.OperationalError: unable to open database file`, stop the
  container and inspect numeric ownership with:

  ```bash
  ls -ldn /mnt/user/appdata/woocommerce-dashboard/instance
  chown -R 99:100 /mnt/user/appdata/woocommerce-dashboard/instance
  chmod -R u+rwX,g+rwX /mnt/user/appdata/woocommerce-dashboard/instance
  ```

  Confirm the exact application-owned path before changing it. If custom
  `PUID`/`PGID` values are configured, substitute those numeric values.
- A setup screen after an upgrade usually indicates the wrong or unwritable
  `/app/instance` mount. Confirm `site.db` exists on the host before continuing.
- Permission errors involving markers or indexes mean `/catalogue` is not
  writable. The entrypoint checks this mount and warns, but does not recursively
  chown catalogue contents or block the initial setup page.
- Missing generated images usually indicate an incorrect `/output` mapping.
- Changing `PUID` or `PGID` requires matching access on every mounted share.
  `/output` is checked like `/catalogue` and is not recursively chowned.
- Preserve `/app/instance/backups/` when diagnosing migration or reconstruction
  failures; do not repeatedly recreate the container with a different mount.
