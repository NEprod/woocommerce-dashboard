# WooCommerce Dashboard

<p align="center">
  <img src="app/static/assets/img/woocommerce-dashboard-logo.svg" width="520" alt="WooCommerce Dashboard logo">
</p>

WooCommerce Dashboard is a local Flask application that scans a structured product catalogue, resolves shared and product-specific JSON metadata, prepares WooCommerce-compatible product rows, and ingests the currently supported subset into SQLite for a web interface.

Phase 0 (`0.1.0`) established the secure, documented, tested, and containerised baseline. Phase 1 (`0.2.3`) makes SQLite a complete, recoverable projection of resolved scanner output while preserving the protected scanner contract and adds a reusable Unraid installation template.

Phase 2 and the Catalogue Intake workflow are released in `v0.3.1`. Phase 3
development takes place on `develop`; its first milestone adds a secure,
read-only WooCommerce connection and API capability workspace. The established
responsive WooCommerce Dashboard shell uses centralized design tokens,
local project-owned UI assets, accessible grouped navigation, and explicit safe
pages for workspaces that are not available yet. The approved design-system
reconciliation makes `docs/DESIGN_SYSTEM.md` authoritative: warm-white canvas
and floating white cards dominate, while deep slate is reserved for navigation,
group headers, progress, and code surfaces. Lime and teal remain restrained.
The current `develop` Dashboard presents genuine catalogue health, scanner
state, recent operations, attention signals, and recently updated products from
the local projection without fabricated analytics. Its Products workspace now
groups genuine parent products by collection, preserves supported Dashboard
issue filters, paginates parent results, and loads projected variation details
only when requested.
Product Detail now presents the resolved parent/variation workspace, metadata
provenance, ordered catalogue image diagnostics, and read-only stored website
URLs. Guided editors preserve the real ownership model: one collection document
supplies shared defaults and an optional partial product override stores only
intentional differences. Advanced JSON remains available as an explicit expert
mode, and every save continues through the protected backup, atomic-write, and
scanner update workflow.
Catalogue lifecycle labels remain local scanner state. Separately, resolved
`live` is shown as Published or Draft intent. Controlled Woo publishing can now
apply that reviewed intent to an explicitly selected set of at most ten parent
products; catalogue lifecycle remains a separate local state.
The released Phase 1 scanner and persistence contracts remain unchanged.
The Collections workspace provides server-backed browsing and a resolved
Collection Detail view for metadata health, catalogue lifecycle, publishing
intent, overrides, source-image coverage, affected products, and recent scoped
operations. Collection JSON remains authoritative and editing continues through
the existing Collection Metadata Editor.

Phase 2.5 adds an optional, authenticated Catalogue Intake workspace backed only
by a dedicated `/intake` mount. Browsing and deterministic grouping/renaming
previews remain read-only. An explicitly confirmed, server-revalidated grouping
now copies unchanged source images through private verified staging into a
duplicate-safe provisional result below `Prepared/`; it does not rename source
files, access the catalogue, or invoke the scanner. Later gated steps safely
advance the same Prepared result through folder editing, image renaming, and an
authoritative shared `product_info.json`. Metadata completion remains
**validation required** and does not perform catalogue handoff or scanning.

## Current capabilities

- First-user and scanner-folder setup.
- Simple, Variable Collection, and Single Variable scanning.
- Shared collection metadata with optional per-product overrides.
- Stable local SKU markers and variation generation.
- Image preparation and SQLite ingestion.
- Single-process catalogue operation locking and persistent operation history.
- A schema-backed product JSON editor, in-app metadata reference/templates, and Discord scan/ingest notifications.
- Environment-only WooCommerce credentials with bounded REST discovery,
  capability health history, digest-bound preview, and explicitly confirmed
  two-pass publishing for at most ten selected parent products.

The catalogue projection retains every emitted scanner row, exact collection relationships, queryable taxonomy/publication metadata, and portable source provenance. Ordinary append/update ingestion commits each complete parent graph as one SQLite transaction, with atomic marker/index replacement and recoverable pending identities across filesystem/database failures. Emitted variation sets reconcile in place, and only explicitly exhaustive successful scopes can mark catalogue products missing. Shared metadata edits use an exhaustive collection-limited refresh. Setup distinguishes new catalogues from existing marker identities and offers identity-preserving reconstruction without turning an empty database into a full SKU reset. The complete `product_info.json` contract has runtime schemas, fictional examples, editor-safe validation, templates, and an in-app reference. Ordered cross-sell/upsell relationships remain a local authored source of truth. Phase 3 controlled publishing regenerates the exact reviewed preview, resolves taxonomy, writes and verifies parents/variations in Pass 1, then applies only safely resolved ordered relationship IDs in Pass 2. See [Controlled Woo Publishing](docs/WOO_CONTROLLED_PUBLISHING.md) and [Current State](docs/CURRENT_STATE.md).

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

Replace the `SECRET_KEY` and path placeholders in `.env` before startup and keep
Discord disabled during development unless notifications are intentionally being
tested. Missing or recognizable placeholder secret keys are rejected. Application
startup applies versioned SQLite migrations, so use an isolated instance directory
for safety. Existing unversioned Phase 0 databases are backed up and adopted only
when their schema matches the frozen baseline. See [Database Migrations](docs/MIGRATIONS.md).

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
- [Collections Workspace](docs/COLLECTIONS.md)
- [Catalogue Intake](docs/CATALOGUE_INTAKE.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Scanner Contract](docs/SCANNER_CONTRACT.md)
- [Scanner, Operations, and Discord](docs/SCANNER_OPERATIONS.md)
- [product_info.json Contract](docs/PRODUCT_INFO.md)
- [Data Model](docs/DATA_MODEL.md)
- [Database Migrations](docs/MIGRATIONS.md)
- [Catalogue Operation Control](docs/CATALOGUE_OPERATIONS.md)
- [Storage and Retention](docs/STORAGE_RETENTION.md)
- [Phase 1 Acceptance](docs/PHASE_1_ACCEPTANCE.md)
- [Roadmap](docs/ROADMAP.md)
- [Decisions](docs/DECISIONS.md)
- [Development](docs/DEVELOPMENT.md)
- [Docker](docs/DOCKER.md)
- [Unraid](docs/UNRAID.md)
- [WooCommerce Connection](docs/WOOCOMMERCE_CONNECTION.md)
- [Woo Publish Preview](docs/WOO_PUBLISH_PREVIEW.md)
- [Controlled Woo Publishing](docs/WOO_CONTROLLED_PUBLISHING.md)
- [Security](docs/SECURITY.md)
