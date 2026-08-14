# Docker

Image repository: `neprod/woocommerce-dashboard`

Phase 0 publishes the equivalent tags `phase-0`, `0.1.0`, and `latest`. The immutable version tag is preferred for deployment.

## Build

```bash
docker build -t neprod/woocommerce-dashboard:phase-0 .
docker tag neprod/woocommerce-dashboard:phase-0 neprod/woocommerce-dashboard:0.1.0
```

The image runs as a non-root user with Gunicorn, one worker, four threads, and port `7485`. One worker and one application replica are required because scan progress, background threads, and the catalogue mutation lock are process-local. Persistent operation rows support diagnosis but are not a distributed mutex.

Gunicorn imports the application before accepting requests. That startup applies Alembic migrations to `/app/instance/site.db`. A matching unversioned Phase 0 database is backed up under `/app/instance/backups` before adoption; backups never default to disposable container storage such as `/tmp`. A failed or unknown migration prevents the worker from starting. Keep the instance mount writable by the container user and preserve its backup files until the upgraded application has been validated.

## Compose

Copy `.env.example` to the ignored `.env` and set:

- `INSTANCE_FOLDER_HOST`: persistent database/application instance directory.
- `PRODUCT_FOLDER_HOST`: catalogue directory mounted at `/catalogue`.
- `OUTPUT_FOLDER_HOST`: generated output directory mounted at `/output`.

```bash
IMAGE_TAG=0.1.0 docker compose up -d
```

Application settings stored through the UI must use container paths (`/catalogue` and `/output`), not host paths.

## Persistence and backup

Back up the instance/database and filesystem catalogue together with an understood consistency point. Back up authored JSON, `.scanned`, SKU indexes, and source assets. Never rely on the disposable container layer for application data.

Never bake `.env`, SQLite, product folders, markers, generated images, exports, logs, or backups into the image. The mounted instance directory contains both the live database and migration backups, so the instance mount itself must be included in operational backups.

For recovery, stop the container and use a one-off container with the same instance mount to run `python -m app.database restore`, as documented in [Database Migrations](MIGRATIONS.md). Do not restore while Gunicorn is accessing SQLite.

Automatic startup migration is approved only for the documented single-worker Phase 1 runtime. A future multi-worker or multi-replica deployment must run migrations as a separate, single-owner deployment step before application replicas start.

## Later Unraid deployment

Deployment to Unraid is outside Phase 0. A later deployment should pull the immutable tag, map persistent appdata to `/app/instance`, map catalogue/output paths explicitly, inject secrets at runtime, retain one worker, and verify backups before enabling scans.

The Phase 0 image is a reproducible baseline, not a production-readiness declaration.
