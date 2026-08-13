# Database Migrations and Recovery

SQLite schema changes are managed by Alembic. Application startup resolves the configured database path, verifies or migrates it to the current migration head, and only then serves requests. `db.create_all()` is not used.

## Fresh database

For a missing or empty database, startup applies every revision from the beginning. The Phase 1 migration foundation has one revision:

- `0001_phase0`: the frozen Phase 0 schema.

The baseline revision contains its own schema declarations. It does not import the current ORM model to create tables, so later model changes cannot rewrite the historical baseline.

## Adopting an unversioned Phase 0 database

An existing database without `alembic_version` is adopted only when every expected Phase 0 table and column matches the frozen signature. Adoption performs these steps:

1. Create a consistent SQLite backup with the SQLite backup API.
2. Verify the backup with `PRAGMA integrity_check`.
3. Stamp the database at `0001_phase0` without recreating its tables.
4. Upgrade from the baseline to the current head.
5. Verify the resulting revision and database integrity.

An unknown or partial unversioned schema is rejected rather than guessed. Phase 1 tests exercise only a synthetic Phase 0 database; no real database is inspected or migrated during development.

## Backups

Before adoption or a versioned upgrade, a backup is written under `instance/backups` by default using a name like:

```text
site.pre-migration-20260813T120000.000000Z.sqlite3
```

Already-current and fresh databases do not create migration backups. Backups are runtime data and must not be committed or baked into the image.

## Manual upgrade and restore

Stop all application processes before a manual migration or restore. In a local checkout or running image:

```bash
python -m app.database upgrade /app/instance/site.db
python -m app.database restore /app/instance/site.db \
  --backup /app/instance/backups/site.pre-migration-TIMESTAMP.sqlite3
```

Restore validates the backup, copies it through the SQLite backup API to a temporary destination, verifies that copy, and atomically replaces the target database. After restoration, run the upgrade command again before restarting the application.

Always back up the database and filesystem catalogue together at an understood consistency point. Migration backup does not include authored JSON, `.scanned`, `.update`, `sku_index.json`, source images, or generated output.

## Failure behavior

If a migration fails, application startup fails rather than serving against a partially understood schema. The raised migration error includes the backup path when one was created. The original pre-migration state remains recoverable from that backup. Do not repeatedly restart or manually edit the database; stop the application, preserve both files, restore the verified backup, and diagnose the failed revision using synthetic reproduction where possible.
