# WooCommerce Dashboard

WooCommerce Dashboard is a local Flask application that scans a structured product catalogue, resolves shared and product-specific JSON metadata, prepares WooCommerce-compatible product rows, and ingests the currently supported subset into SQLite for a web interface.

Phase 0 (`0.1.0`) establishes a secure, documented, tested, and containerised baseline. It preserves the existing scanner and application behaviour; it is not a production-ready release.

## Current capabilities

- First-user and scanner-folder setup.
- Simple, Variable Collection, and Single Variable scanning.
- Shared collection metadata with optional per-product overrides.
- Stable local SKU markers and variation generation.
- Image preparation and SQLite ingestion.
- Single-process catalogue operation locking and persistent operation history.
- A limited product JSON editor and Discord scan/ingest notifications.

Known incomplete areas include database field parity, active Collection-to-Product links, several missing routed templates, and live WooCommerce integration. See [Current State](docs/CURRENT_STATE.md).

## Local development

Requires Python 3.13 or another compatible supported Python 3 version.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
pytest
python run.py
```

Replace placeholders in `.env` locally and keep Discord disabled during development unless notifications are intentionally being tested. Application startup applies versioned SQLite migrations, so use an isolated instance directory for safety. Existing unversioned Phase 0 databases are backed up and adopted only when their schema matches the frozen baseline. See [Database Migrations](docs/MIGRATIONS.md).

## Docker

```bash
cp .env.example .env
# Set the three *_HOST mount paths in .env.
docker compose build
docker compose up -d
```

The container listens on port `7485`, runs Gunicorn with one worker and four threads, and expects persistent instance, catalogue, and output mounts. Never bake a live `.env`, database, catalogue, or generated output into the image. Full guidance is in [Docker](docs/DOCKER.md).

## Project links

- Source: <https://github.com/NEprod/woocommerce-dashboard>
- Container: <https://hub.docker.com/r/neprod/woocommerce-dashboard>

## Documentation

- [Current State](docs/CURRENT_STATE.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Scanner Contract](docs/SCANNER_CONTRACT.md)
- [Data Model](docs/DATA_MODEL.md)
- [Database Migrations](docs/MIGRATIONS.md)
- [Catalogue Operation Control](docs/CATALOGUE_OPERATIONS.md)
- [Roadmap](docs/ROADMAP.md)
- [Decisions](docs/DECISIONS.md)
- [Development](docs/DEVELOPMENT.md)
- [Docker](docs/DOCKER.md)
- [Security](docs/SECURITY.md)
