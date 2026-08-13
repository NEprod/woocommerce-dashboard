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
