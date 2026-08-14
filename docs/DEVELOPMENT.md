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
docker build -t neprod/woocommerce-dashboard:phase-0 .
```

Migration tests construct a frozen, synthetic Phase 0 database in a temporary directory. They must cover fresh initialization, adoption, repeated upgrade, backup, injected failure, restore, and post-restore use. Never substitute a local or archived database. Operational procedures are in [Database Migrations](MIGRATIONS.md).

Operation-control tests use temporary databases and fictional paths. They must prove conflict rejection before mutation, success and exception cleanup, sanitized persistent errors, notification-failure cleanup, and startup interruption recovery. Resetting the process-local test lock is allowed only in isolated tests. See [Catalogue Operation Control](CATALOGUE_OPERATIONS.md).

Projection tests must use emitted fictional rows and temporary catalogue mounts. They must prove exact collection types and relationships, lossless parent/variation row storage, normalized field parity, portable relative provenance across mount changes, and preservation of existing Product, Variation, and Woo placeholder IDs. They must not alter scanner fixtures to conceal a row-builder discrepancy.

Transactional-ingestion tests inject failures after collection, parent, product-gallery, asset, taxonomy, parent-attribute, variation, variation-attribute, variation-gallery, and operation-item stages. Every case must prove complete rollback of that parent, a sanitized failed history item, and survival of unrelated committed parents. They also cover missing variation-parent rows and update-in-place preservation of internal and Woo-placeholder identities.

Tests must create temporary directories and SQLite databases. Fixtures under `tests/fixtures` must be fictional and contain no copied commercial catalogue text, customer information, live SKU, local personal path, credential, or webhook. Tests must never use the live `.env`, `instance/site.db`, catalogue, output folder, Discord, WooCommerce, WordPress, or internet.

## Git workflow

Use phase-specific branches when appropriate, focused commits, and annotated phase/release tags. Before every commit and push:

1. Review `git status --short --ignored`.
2. Review the exact staged file list and diff.
3. Confirm `.env`, `instance/`, databases, catalogue markers, output, backups, and logs are ignored.
4. Scan staged content for webhook, key, token, bearer, and private-key patterns.
5. Run compile checks and pytest.
6. Build and validate the image using temporary mounts only.
7. Confirm no production application behavior was changed incidentally.
