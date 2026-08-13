# Development

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

Use placeholder/fabricated directories and keep `DISCORD_ENABLED=false`. Application startup creates database tables, so never point development or tests at the live instance directory unintentionally.

## Checks

```bash
python -m compileall app tests
pytest
docker build -t neprod/woocommerce-dashboard:phase-0 .
```

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
