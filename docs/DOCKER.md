# Docker

Image repository: `neprod/woocommerce-dashboard`

Phase 1 deployment polish publishes the equivalent multi-platform tags `phase-1`, `0.2.3`, and `latest`. The immutable `0.2.3` tag is preferred for deployment. Historical version tags remain unchanged.

Phase 2 milestone builds are development images. Each approved Milestone 1–8
publishes one immutable `phase-2-m<N>` tag and updates the moving `develop` tag
from the same Buildx result. Stable `latest`, `phase-1`, `0.2.3`, and every
historical tag remain unchanged until the explicitly approved final release.

## Build

```bash
docker build -t neprod/woocommerce-dashboard:phase-1 .
docker tag neprod/woocommerce-dashboard:phase-1 neprod/woocommerce-dashboard:0.2.3
```

The container initially starts as root only to validate `PUID`, `PGID`, and `UMASK`, adjust the existing `app` account, and prepare mounted application state. It then uses the packaged `gosu` binary with `exec`; application import, migrations, the Gunicorn master, and its worker all run as the configured non-root UID/GID. Generic Docker defaults preserve the previous `100:100` identity and `UMASK=002`. One worker and one application replica are required because scan progress, background threads, and the catalogue mutation lock are process-local. Persistent operation rows support diagnosis but are not a distributed mutex.

Gunicorn imports the application before accepting requests. That startup applies Alembic migrations to `/app/instance/site.db`. A matching unversioned Phase 0 database is backed up under `/app/instance/backups` before adoption; backups never default to disposable container storage such as `/tmp`. A failed or unknown migration prevents the worker from starting. Keep the instance mount writable by the container user and preserve its backup files until the upgraded application has been validated.

## Compose

Copy `.env.example` to the ignored `.env` and set:

- `INSTANCE_FOLDER_HOST`: persistent database/application instance directory.
- `PRODUCT_FOLDER_HOST`: catalogue directory mounted at `/catalogue`.
- `OUTPUT_FOLDER_HOST`: generated output directory mounted at `/output`.
- `PUID` and `PGID`: numeric non-root runtime identity; defaults are `100:100`.
- `UMASK`: three- or four-digit octal creation mask; default is `002`.

```bash
IMAGE_TAG=0.2.3 docker compose up -d
```

Application settings stored through the UI must use container paths (`/catalogue` and `/output`), not host paths.

## Persistence and backup

The canonical container storage contract is fixed:

| Container path | Access | Contents |
| --- | --- | --- |
| `/app/instance` | required read/write | `/app/instance/site.db` and `/app/instance/backups/` |
| `/catalogue` | required read/write | authored catalogue, source images, metadata, `.scanned`, `.scanned.pending`, `.update`, and SKU indexes |
| `/output` | required read/write | processed/generated image output |

Do not rename `/app/instance` to `/config` or rename `site.db`; either change can
make an existing installation appear empty. Keep catalogue and output outside the
appdata instance directory. Updating or replacing a container preserves data only
when the same host directories are mounted again.

Back up the instance/database and filesystem catalogue together with an understood consistency point. Back up authored JSON, `.scanned`, `.scanned.pending`, `.update`, SKU indexes, processed output, and source assets. Never rely on the disposable container layer for application data. Pending envelopes are required to preserve identities and finish database/marker recovery after interruption.

Never bake `.env`, SQLite, product folders, markers, generated images, exports, logs, or backups into the image. The mounted instance directory contains the live database plus migration and reconstruction backups, so the instance mount itself must be included in operational backups and have space for unique reconstruction snapshots.

The production image includes `app/resources/product_info` because collection and
override schemas, the field inventory, fictional examples, and editor templates
are runtime help/validation resources. They contain no catalogue data. Test files
and test-only fixtures remain excluded because the Dockerfile copies `app`, not
`tests`.

For recovery, stop the container and use a one-off container with the same instance mount to run `python -m app.database restore`, as documented in [Database Migrations](MIGRATIONS.md). Do not restore while Gunicorn is accessing SQLite.

Automatic startup migration is approved only for the documented single-worker Phase 1 runtime. A future multi-worker or multi-replica deployment must run migrations as a separate, single-owner deployment step before application replicas start.

## Bind-mount permissions

Setting `PUID`/`PGID` in Compose or an Unraid template only works because this image consumes them in its entrypoint. The entrypoint validates non-zero numeric IDs and a valid octal umask, updates the existing `app` account, creates `/app/instance/backups`, and deliberately corrects ownership recursively only beneath the application-owned `/app/instance`. Existing `site.db` and backup contents are preserved.

The `/catalogue` and `/output` mount roots may have their ownership adjusted non-recursively, but their contents are never recursively chowned at startup. A write probe emits an actionable container-path warning if either is unavailable; the initial setup page may still load, while scanning still requires both paths to be read/write. Failure to prepare or write `/app/instance` stops startup immediately.

Inspect numeric ownership on a Linux host with `ls -ldn`. For the documented Unraid instance path:

```bash
ls -ldn /mnt/user/appdata/woocommerce-dashboard/instance
chown -R 99:100 /mnt/user/appdata/woocommerce-dashboard/instance
chmod -R u+rwX,g+rwX /mnt/user/appdata/woocommerce-dashboard/instance
```

Only run the correction after stopping the container and confirming the exact application-owned instance directory. Inspect catalogue and output share ownership separately; grant the configured identity suitable access rather than recursively rewriting a large catalogue.

## Unraid deployment boundary

An Unraid deployment should pull the immutable `0.2.3` tag, set `PUID=99`, `PGID=100`, and `UMASK=002`, map persistent appdata to `/app/instance`, map catalogue/output shares explicitly, inject secrets at runtime, retain one worker, and verify backups before enabling scans. The reusable template and full instructions are in [Unraid Installation](UNRAID.md).

The Phase 1 image supplies both target architectures, but publication does not replace deployment-specific backup, mount, secret, and operational validation.

## Multi-platform publication

For a Phase 2 milestone, publish both tags in one build, for example:

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --tag neprod/woocommerce-dashboard:phase-2-m1 \
  --tag neprod/woocommerce-dashboard:develop \
  --push .
```

The immutable milestone tag and `develop` must share one manifest digest. Test
both architectures with separate temporary instance, catalogue, and output
mounts. Milestone routes, Gunicorn startup, migration head `0004_lifecycle`,
SQLite integrity, and image-content exclusions must pass before the milestone
is reported. A separate Unraid development container must never share writable
storage with the stable deployment.

The published Phase 0 image was built on Apple Silicon and its manifest does not provide `linux/amd64`, so it is not usable by the target Unraid server. Do not overwrite `phase-0` or `0.1.0` to correct that historical image. Immutable version tags `0.2.0` and `0.2.1` also remain historical release records.

Patch releases build once for both target platforms and attach the current fixed, phase, and rolling tags to that same result. The `0.2.3` Buildx shape is:

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --tag neprod/woocommerce-dashboard:phase-1 \
  --tag neprod/woocommerce-dashboard:0.2.3 \
  --tag neprod/woocommerce-dashboard:latest \
  --push .
```

The final release procedure must inspect the remote manifests rather than relying on local image metadata. It must prove all three tags share one manifest digest containing `linux/amd64` and `linux/arm64`, then pull and run each platform explicitly with temporary mounts. On both platform images, Gunicorn must start, the temporary database must migrate to head, and `/setup` must respond. Image inspection must also prove runtime migrations, schemas, and in-app reference resources are present while tests, fixtures, development dependencies, `.env`, databases, backups, catalogue/output data, marker files, Git data, credentials, and secrets are absent.

Record the existing `0.2.2`, `0.2.1`, `0.2.0`, `phase-0`, and `0.1.0` references before publication and verify them again afterward. None of those immutable tags may be pushed or changed. The original release checklist is in [Phase 1 Acceptance](PHASE_1_ACCEPTANCE.md).
