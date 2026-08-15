# Docker

Image repository: `neprod/woocommerce-dashboard`

Phase 1 publishes the equivalent multi-platform tags `phase-1`, `0.2.0`, and `latest`. The immutable version tag is preferred for deployment. Historical Phase 0 tags `phase-0` and `0.1.0` remain unchanged.

## Build

```bash
docker build -t neprod/woocommerce-dashboard:phase-1 .
docker tag neprod/woocommerce-dashboard:phase-1 neprod/woocommerce-dashboard:0.2.0
```

The image runs as a non-root user with Gunicorn, one worker, four threads, and port `7485`. One worker and one application replica are required because scan progress, background threads, and the catalogue mutation lock are process-local. Persistent operation rows support diagnosis but are not a distributed mutex.

Gunicorn imports the application before accepting requests. That startup applies Alembic migrations to `/app/instance/site.db`. A matching unversioned Phase 0 database is backed up under `/app/instance/backups` before adoption; backups never default to disposable container storage such as `/tmp`. A failed or unknown migration prevents the worker from starting. Keep the instance mount writable by the container user and preserve its backup files until the upgraded application has been validated.

## Compose

Copy `.env.example` to the ignored `.env` and set:

- `INSTANCE_FOLDER_HOST`: persistent database/application instance directory.
- `PRODUCT_FOLDER_HOST`: catalogue directory mounted at `/catalogue`.
- `OUTPUT_FOLDER_HOST`: generated output directory mounted at `/output`.

```bash
IMAGE_TAG=0.2.0 docker compose up -d
```

Application settings stored through the UI must use container paths (`/catalogue` and `/output`), not host paths.

## Persistence and backup

Back up the instance/database and filesystem catalogue together with an understood consistency point. Back up authored JSON, `.scanned`, `.scanned.pending`, `.update`, SKU indexes, processed output, and source assets. Never rely on the disposable container layer for application data. Pending envelopes are required to preserve identities and finish database/marker recovery after interruption.

Never bake `.env`, SQLite, product folders, markers, generated images, exports, logs, or backups into the image. The mounted instance directory contains the live database plus migration and reconstruction backups, so the instance mount itself must be included in operational backups and have space for unique reconstruction snapshots.

The production image includes `app/resources/product_info` because collection and
override schemas, the field inventory, fictional examples, and editor templates
are runtime help/validation resources. They contain no catalogue data. Test files
and test-only fixtures remain excluded because the Dockerfile copies `app`, not
`tests`.

For recovery, stop the container and use a one-off container with the same instance mount to run `python -m app.database restore`, as documented in [Database Migrations](MIGRATIONS.md). Do not restore while Gunicorn is accessing SQLite.

Automatic startup migration is approved only for the documented single-worker Phase 1 runtime. A future multi-worker or multi-replica deployment must run migrations as a separate, single-owner deployment step before application replicas start.

## Unraid deployment boundary

Server-specific deployment remains outside Phase 1. An Unraid deployment should pull the immutable `0.2.0` tag, map persistent appdata to `/app/instance`, map catalogue/output paths explicitly, inject secrets at runtime, retain one worker, and verify backups before enabling scans.

The Phase 1 image supplies both target architectures, but publication does not replace deployment-specific backup, mount, secret, and operational validation.

## Phase 1 multi-platform publication

The published Phase 0 image was built on Apple Silicon and its manifest does not provide `linux/amd64`, so it is not usable by the target Unraid server. Do not overwrite `phase-0` or `0.1.0` to correct that historical image. Do not publish, replace, or modify any Docker Hub tag during Phase 1 Milestones 4–9.

After Milestones 0–9 and every final acceptance check pass, Milestone 10 must build once for both target platforms and attach all Phase 1 tags to that same result. The intended Buildx shape is:

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --tag neprod/woocommerce-dashboard:phase-1 \
  --tag neprod/woocommerce-dashboard:0.2.0 \
  --tag neprod/woocommerce-dashboard:latest \
  --push .
```

The final release procedure must inspect the remote manifests rather than relying on local image metadata. It must prove all three tags share one manifest digest containing `linux/amd64` and `linux/arm64`, then pull and run each platform explicitly with temporary mounts. On both platform images, Gunicorn must start, the temporary database must migrate to head, and `/setup` must respond. Image inspection must also prove runtime migrations, schemas, and in-app reference resources are present while tests, fixtures, development dependencies, `.env`, databases, backups, catalogue/output data, marker files, Git data, credentials, and secrets are absent.

Record the existing `phase-0` and `0.1.0` references before publication and verify them again afterward. Neither Phase 0 tag may be pushed or changed. The complete release checklist is in [Phase 1 Acceptance](PHASE_1_ACCEPTANCE.md).
