# WooCommerce Dashboard

<p align="center">
  <img src="app/static/assets/img/woocommerce-dashboard-logo.svg" width="520" alt="WooCommerce Dashboard logo">
</p>

WooCommerce Dashboard is a local Flask application that scans a structured product catalogue, resolves shared and product-specific JSON metadata, prepares WooCommerce-compatible product rows, and ingests the currently supported subset into SQLite for a web interface.

Phase 0 (`0.1.0`) established the secure, documented, tested, and containerised baseline. Phase 1 (`0.2.3`) makes SQLite a complete, recoverable projection of resolved scanner output while preserving the protected scanner contract and adds a reusable Unraid installation template.

Phase 2 development takes place on `develop`. Its first milestone introduces a
neutral, responsive WooCommerce Dashboard shell with centralized design tokens,
local project-owned UI assets, accessible grouped navigation, and explicit safe
pages for workspaces that are not available yet. The released Phase 1 scanner
and persistence contracts remain unchanged.

## Current capabilities

- First-user and scanner-folder setup.
- Simple, Variable Collection, and Single Variable scanning.
- Shared collection metadata with optional per-product overrides.
- Stable local SKU markers and variation generation.
- Image preparation and SQLite ingestion.
- Single-process catalogue operation locking and persistent operation history.
- A schema-backed product JSON editor, in-app metadata reference/templates, and Discord scan/ingest notifications.

The catalogue projection retains every emitted scanner row, exact collection relationships, queryable taxonomy/publication metadata, and portable source provenance. Ordinary append/update ingestion commits each complete parent graph as one SQLite transaction, with atomic marker/index replacement and recoverable pending identities across filesystem/database failures. Emitted variation sets reconcile in place, and only explicitly exhaustive successful scopes can mark catalogue products missing. Shared metadata edits use an exhaustive collection-limited refresh. Setup distinguishes new catalogues from existing marker identities and offers identity-preserving reconstruction without turning an empty database into a full SKU reset. The complete `product_info.json` contract has runtime schemas, fictional examples, editor-safe validation, templates, and an in-app reference. Live WooCommerce integration remains outside Phase 1. See [Current State](docs/CURRENT_STATE.md).

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

The container listens on port `7485`, runs Gunicorn with one worker and four threads, and expects persistent instance, catalogue, and output mounts. Generic Docker defaults preserve UID/GID `100:100`; Unraid should set `PUID=99`, `PGID=100`, and `UMASK=002`. The entrypoint prepares permissions and then runs Gunicorn non-root through `gosu`. Never bake a live `.env`, database, catalogue, or generated output into the image. Full guidance is in [Docker](docs/DOCKER.md).

The canonical persistent mappings are `/app/instance` for `site.db` and application backups, `/catalogue` for authored catalogue and scanner identity state, and `/output` for generated files. Unraid users should start with the tracked [Unraid template](unraid/my-woocommerce-dashboard.xml) and [installation guide](docs/UNRAID.md).

## Project links

- Source: <https://github.com/NEprod/woocommerce-dashboard>
- Container: <https://hub.docker.com/r/neprod/woocommerce-dashboard>

Suggested Docker Hub overview: “WooCommerce Dashboard is a self-hosted Flask application for resolving a filesystem product catalogue into a recoverable SQLite projection and WooCommerce-compatible rows. It includes transactional ingestion, marker recovery, reconstruction, metadata schemas, and multi-platform Unraid-compatible images.”

## Documentation

- [Current State](docs/CURRENT_STATE.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Scanner Contract](docs/SCANNER_CONTRACT.md)
- [product_info.json Contract](docs/PRODUCT_INFO.md)
- [Data Model](docs/DATA_MODEL.md)
- [Database Migrations](docs/MIGRATIONS.md)
- [Catalogue Operation Control](docs/CATALOGUE_OPERATIONS.md)
- [Phase 1 Acceptance](docs/PHASE_1_ACCEPTANCE.md)
- [Roadmap](docs/ROADMAP.md)
- [Decisions](docs/DECISIONS.md)
- [Development](docs/DEVELOPMENT.md)
- [Docker](docs/DOCKER.md)
- [Unraid](docs/UNRAID.md)
- [Security](docs/SECURITY.md)
