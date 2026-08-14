# Architecture Decision Log

## Phase 0 decisions

1. **Protect scanner behaviour.** Phase 0 adds characterization tests and infrastructure but does not refactor scanning, SKU allocation, markers, JSON merging, or ingestion.
2. **Authored metadata remains in JSON/filesystem structures.** The current application resolves those sources into downstream rows.
3. **SQLite is not independently authoritative for authored product metadata.** It is the queryable resolved/application store.
4. **WooCommerce is downstream.** No live API authority is introduced in Phase 0.
5. **Runtime data is excluded.** Real catalogues, scanner markers, databases, secrets, backups, and generated output are never committed or baked into images.
6. **Containers use mounted persistence.** `/app/instance`, `/catalogue`, and `/output` are runtime mounts.
7. **The initial container uses one Gunicorn worker.** Scan state and threads are process-local and SQLite/background work has not been designed for multiple workers. Four threads allow ordinary request concurrency within that single process.

No new source-of-truth or schema design decision is made here.

## Phase 1 approved constraints

1. **Scanner discrepancies remain characterised, not silently corrected.** Database parity means parity with emitted scanner rows. Internally resolved but un-emitted values are not promoted into SQLite.
2. **Cross-store consistency is recoverable, not atomic.** SQLite transactions cannot atomically include processed images, `sku_index.json`, `.scanned`, or `.update`. Phase 1 will record and recover incomplete finalisation explicitly.
3. **Ordinary ingestion uses a complete-parent boundary.** Each parent, its collection relationship, metadata, images, taxonomy, assets, variations, attributes, and variation images form one database transaction. An unrelated successful parent need not roll back when a later parent fails.
4. **Reconstruction is distinct from full scanning.** Reconstruction must reuse `.scanned` identities; intentional full scanning retains its current SKU-regeneration implications and requires an explicit choice.
5. **Catalogue-mutating operations remain single-process.** A non-blocking process-local lock rejects concurrent mutations, while persistent rows record bounded history and interrupted state. History is not treated as a distributed lock, queue, or multi-worker coordinator.
6. **Alembic owns schema evolution.** The frozen `0001_phase0` revision initializes new databases and is the adoption point for an exact unversioned Phase 0 schema. `db.create_all()` has no remaining startup role.
7. **Migration adoption is conservative and recoverable.** Unknown unversioned schemas are rejected. Adoption and later upgrades create a verified SQLite backup before schema-version state changes.
8. **Interrupted operation rows require review.** Startup marks unfinished operations interrupted after migrations complete. It does not infer that filesystem side effects and SQLite were atomically rolled back; the next catalogue operation uses pending intent and committed item state to recover markers safely.
9. **Catalogue-relative paths are portable identity and provenance.** Collection identity is the POSIX-style path relative to the configured catalogue root. Product and JSON relative paths use the same root. Absolute columns remain runtime locators only, and changing a mount point must not create a new collection.
10. **The emitted row is the parity boundary.** Parent and variation rows are stored losslessly as JSON, with common fields normalized for queries. SQLite does not promote internally resolved values that the protected row builder did not emit.
11. **Marker coordination uses durable intent, not rollback fiction.** Production scans atomically stage `.scanned.pending`, commit SQLite per parent, then atomically finalize `.scanned` and remove `.update`. Failures retain identity and explicit recovery state. SKU counters and processed images are allowed to remain advanced/present.
