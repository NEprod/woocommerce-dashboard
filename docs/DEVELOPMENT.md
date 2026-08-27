# Development

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

Use placeholder/fabricated directories and keep `DISCORD_ENABLED=false`. Application startup applies database migrations, so never point development or tests at the live instance directory unintentionally.

## Checks

```bash
python -m compileall app tests
pytest
docker build -t neprod/woocommerce-dashboard:development .
python -c "import xml.etree.ElementTree as ET; ET.parse('unraid/my-woocommerce-dashboard.xml')"
```

Migration tests construct a frozen, synthetic Phase 0 database in a temporary directory. They must cover fresh initialization, adoption, repeated upgrade, backup, injected failure, restore, and post-restore use. Never substitute a local or archived database. Operational procedures are in [Database Migrations](MIGRATIONS.md).

Storage-hardening tests set an explicit test-only `SECRET_KEY` before application
imports and use only temporary instance/catalogue/output directories. They cover
secure backup modes, central redaction, memory and database operation retention,
backup count/age floors, narrow stale-temporary cleanup, Docker logging limits,
and runtime image boundaries. Never weaken production key validation to simplify
tests.

Operation-control tests use temporary databases and fictional paths. They must prove conflict rejection before mutation, success and exception cleanup, sanitized persistent errors, notification-failure cleanup, and startup interruption recovery. Resetting the process-local test lock is allowed only in isolated tests. See [Catalogue Operation Control](CATALOGUE_OPERATIONS.md).

Projection tests must use emitted fictional rows and temporary catalogue mounts. They must prove exact collection types and relationships, lossless parent/variation row storage, normalized field parity, portable relative provenance across mount changes, and preservation of existing Product, Variation, and Woo placeholder IDs. They must not alter scanner fixtures to conceal a row-builder discrepancy.

Transactional-ingestion tests inject failures after collection, parent, product-gallery, asset, taxonomy, parent-attribute, variation, variation-attribute, variation-gallery, and operation-item stages. Every case must prove complete rollback of that parent, a sanitized failed history item, and survival of unrelated committed parents. They also cover missing variation-parent rows and update-in-place preservation of internal and Woo-placeholder identities.

Marker-recovery tests use only temporary fictional catalogues/databases and disable Discord. They must cover the protected old ordering, the new pending/DB/finalization ordering, atomic `.scanned` and `sku_index.json` replacement, pre-DB and parent-transaction failures, post-commit marker and `.update` failures, interruption recovery, retry identity reuse for parents and variations, valid-marker retention, and unrelated-product isolation.

Controlled reconstruction can be inspected or run from an application runtime:

```bash
python -m app.utils.reconstruction status
python -m app.utils.reconstruction run
```

`status` is read-only. `run` acquires the catalogue-operation lock, never aliases
full scan, suppresses Discord, and prints bounded counts plus a backup path
relative to the instance directory. Reconstruction tests use temporary fictional
catalogues and cover preflight, backup/restore, transaction rollback, identity
preservation, idempotence, lifecycle reconciliation, and pending recovery.

Metadata-contract tests validate both Draft 2020-12 schemas, every fictional
example/template, partial inheritance, aliases and warnings, unsafe nested
structures, editor side-effect boundaries, in-app resources, and the production
image copy boundary. The frozen Phase 0 complete-row parity test must remain green.
Runtime resources live under `app/resources/product_info`; test-only fixtures do
not belong there. See [product_info.json Contract](PRODUCT_INFO.md).

Tests must create temporary directories and SQLite databases. Fixtures under `tests/fixtures` must be fictional and contain no copied commercial catalogue text, customer information, live SKU, local personal path, credential, or webhook. Tests must never use the live `.env`, `instance/site.db`, catalogue, output folder, Discord, WooCommerce, WordPress, or internet.

Deployment-contract tests parse the tracked Unraid XML, verify every template
static reference, and recreate the application against one temporary instance
directory to prove `site.db` plus migration/reconstruction backups persist. Docker
replacement verification must likewise use temporary `/app/instance`, `/catalogue`,
and `/output` mounts and must never point at live Unraid or local data.

## Git workflow

Phase 2 uses the long-lived `develop` branch. Every approved milestone is a
focused commit pushed to `origin/develop`; milestone work is not merged to
`main` and final tags are not created until the Phase 2 release gate. Do not
force-push or remove the development branch without explicit approval.

Milestone UI tests must cover authenticated route safety, neutral branding,
local assets, keyboard navigation, focus return, representative responsive
breakpoints, and absence of horizontal viewport overflow. Browser checks use a
temporary database and fabricated account only.

Before every commit and push:

1. Review `git status --short --ignored`.
2. Review the exact staged file list and diff.
3. Confirm `.env`, `instance/`, databases, catalogue markers, output, backups, and logs are ignored.
4. Scan staged content for webhook, key, token, bearer, and private-key patterns.
5. Run compile checks and pytest.
6. Build and validate the image using temporary mounts only.
7. Confirm no production application behaviour was changed incidentally.

For Phase 2 Milestones 1–8, publish the approved immutable
`phase-2-m<N>` multi-platform image and update `develop` from the same build
result. Both tags must share one manifest containing `linux/amd64` and
`linux/arm64`. Stable and historical tags remain untouched.
