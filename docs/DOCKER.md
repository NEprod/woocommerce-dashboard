# Docker

Image repository: `neprod/woocommerce-dashboard`

Phase 0 publishes the equivalent tags `phase-0`, `0.1.0`, and `latest`. The immutable version tag is preferred for deployment.

## Build

```bash
docker build -t neprod/woocommerce-dashboard:phase-0 .
docker tag neprod/woocommerce-dashboard:phase-0 neprod/woocommerce-dashboard:0.1.0
```

The image runs as a non-root user with Gunicorn, one worker, four threads, and port `7485`. One worker is required because scan progress and background threads are process-local.

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

Never bake `.env`, SQLite, product folders, markers, generated images, exports, logs, or backups into the image.

## Later Unraid deployment

Deployment to Unraid is outside Phase 0. A later deployment should pull the immutable tag, map persistent appdata to `/app/instance`, map catalogue/output paths explicitly, inject secrets at runtime, retain one worker, and verify backups before enabling scans.

The Phase 0 image is a reproducible baseline, not a production-readiness declaration.
