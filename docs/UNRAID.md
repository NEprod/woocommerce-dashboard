# Unraid Installation and Persistence

Use `neprod/woocommerce-dashboard:0.2.2` for a pinned Phase 1 installation. The
image supports `linux/amd64` for Unraid and `linux/arm64` for Apple Silicon. It
runs as a non-root user with one Gunicorn worker, four threads, and container
port `7485`.

The tracked template is `unraid/my-woocommerce-dashboard.xml`. It can reproduce
a manually configured container or be saved as an Unraid user template. It is a
basis for a future Community Applications submission; the application is not
currently claimed to be listed in Community Applications.

## Required container settings

- Repository: `neprod/woocommerce-dashboard:0.2.2`
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
```

All three mappings are required. `/app/instance` contains the application-owned
`site.db` plus `backups/`. `/catalogue` contains collection folders,
`product_info.json`, source images, `.scanned`, `.scanned.pending`, `.update`,
and `sku_index.json`; it must be writable. `/output` contains processed/generated
images and must also be writable. Keep catalogue and output in purpose-specific
shares rather than under appdata.

Do not change the database mount to `/config` and do not rename `site.db`.
Existing installations rely on `/app/instance/site.db`; changing either path can
make a populated installation appear new.

## Environment

`SECRET_KEY` is required. Generate a strong value on a trusted shell and paste
only the result into the Unraid variable:

```bash
openssl rand -hex 32
```

Discord is disabled by default with `DISCORD_ENABLED=false`. Optional supported
variables are `DISCORD_DEFAULT_USERNAME`, `DISCORD_DEFAULT_AVATAR_URL`,
`DISCORD_WEBHOOK_SCANS_INFO`, `DISCORD_WEBHOOK_SCANS_ERRORS`,
`DISCORD_WEBHOOK_EDITS`, `DISCORD_WEBHOOK_OVERRIDES`, and
`DISCORD_WEBHOOK_INGEST`. Treat every webhook as a secret.

## First start

1. Create/select the three host directories and ensure the container can write
   to them.
2. Install the XML as a user template or reproduce its fields manually.
3. Set a generated `SECRET_KEY`; leave Discord disabled initially.
4. Start the container and open `http://<unraid-ip>:7485/`.
5. Complete `/setup`, then enter the container paths `/catalogue` and `/output`
   in initial settings.
6. Review the initial-scan classification before starting any catalogue action.

Reconstruction preserves existing marker/SKU identities while rebuilding the
database projection. Intentional full regeneration retains the protected
SKU-reset behavior and must not be used as a substitute for reconstruction.

## Updating and replacing the container

Stop the container, back up the mounted data, then update or replace only the
image/container definition. Reusing the same three mounts preserves the database,
catalogue identities, and output. Startup migrates `/app/instance/site.db` to the
current migration head before Gunicorn accepts traffic.

Pin `0.2.2` for repeatable deployments. `phase-1` and `latest` track newer
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
- A setup screen after an upgrade usually indicates the wrong or unwritable
  `/app/instance` mount. Confirm `site.db` exists on the host before continuing.
- Permission errors involving markers or indexes mean `/catalogue` is not
  writable.
- Missing generated images usually indicate an incorrect `/output` mapping.
- Preserve `/app/instance/backups/` when diagnosing migration or reconstruction
  failures; do not repeatedly recreate the container with a different mount.
