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
12. **Presence reconciliation is soft and explicitly scoped.** A committed
    parent's emitted variations are complete for that parent. Products are
    reconciled only by a successfully resolved exhaustive catalogue operation or
    a collection-limited shared refresh. Rows are marked `missing`, never deleted,
    and are restored by portable identity before protected SKU identity.
13. **Shared JSON saves use explicit collection orchestration.** Simple and
    Variable Collection child-marker selection is not widened globally. A shared
    save targets its portable collection path, force-refreshes every child using
    existing marker identities, and leaves unrelated collections untouched.
14. **Empty SQLite does not imply identity reset.** Setup inspects catalogue
    markers independently. Existing `.scanned` or pending identity selects
    reconstruction; malformed or unavailable state blocks action. Full regeneration
    remains separately named, warned, and explicitly confirmed.
15. **Reconstruction is a controlled in-place projection replacement.** Complete
    pre-resolution precedes a persistent verified backup and one SQLite transaction.
    Valid markers and counters are not reset or rewritten. Database identity
    overlays provide idempotence for safely matched rows newer than marker payloads.
16. **Metadata schemas protect editor writes, not scanner input globally.** The
    collection and partial-override schemas formalize known types and unsafe
    structures while top-level unknown fields remain warnings. Legacy editor
    upsell/cross-sell spellings are documented and warned but not normalized.
    Unknown collection types and every characterized scanner discrepancy remain
    unchanged pending an explicit contract decision.

## Phase 2 approved constraints

1. **Phase 2 is a presentation and workflow phase.** Scanner resolution, SKU
   allocation, database projection, migrations, reconstruction, marker recovery,
   and operation semantics remain protected.
2. **Visual identity is centralized and project-owned.** Semantic CSS variables
   derive from the WooCommerce Dashboard logo palette. Local Bootstrap,
   application JavaScript, and an original SVG symbol set replace runtime CDN
   dependencies and business-specific styling.
3. **Incomplete modules identify themselves honestly.** Safe `Planned` pages
   replace missing templates and never claim Woo, order, automation, analytics,
   collection, settings, or operation functionality before its approved
   milestone.
4. **Responsive navigation follows one information architecture.** Desktop and
   mobile navigation expose the same grouped destinations with keyboard focus,
   focus return, reduced-motion support, and no hover-only actions.
5. **Folder enumeration requires authentication.** The existing setup folder
   picker remains an administrator workflow and is not a public route.
