# Phase 1 Acceptance and Publication Gates

This checklist supplements the approved Milestones 0–10 without reducing their
individual acceptance criteria. Phase 1 must not be merged, tagged, or published
until every milestone is complete and its focused commit history remains intact.

## Functional acceptance

- Protected scanner behavior, SKU generation/reuse, inheritance, append, update,
  and deliberate full-scan behavior remain characterized and unchanged unless a
  separately approved replacement is introduced.
- SQLite is a complete projection of emitted resolved scanner output, with active
  Collection → Product → Variation relationships and portable provenance.
- Ordinary ingestion is transactional per complete parent, rolls back all parent
  relationships and child rows on any stage failure, preserves unrelated
  committed parents and existing internal/Woo identities, and records affected
  parents with sanitized outcomes. Reconciliation, missing/restored state, shared
  updates, and reconstruction meet their approved identity and recovery criteria.
- Metadata documentation, separate schemas, fictional examples, and in-app
  reference resources accurately describe active, alias, ignored, editor-only,
  Woo-only, legacy, and planned fields.
- Fresh initialization, synthetic Phase 0 upgrades, repeated upgrades, injected
  failures, persistent backups, restoration, reconstruction, and identity
  preservation pass using temporary databases and repository fixtures only.

## Source and image boundaries

- Git retains application source, migrations, tests, fictional fixtures, schemas,
  documentation, examples, and runtime reference resources.
- Production images contain runtime migrations, schemas, templates, and in-app
  reference resources.
- Production images exclude tests, test fixtures, development dependencies,
  `.env`, databases, backups, catalogue data, output, `.scanned`, `.update`,
  `sku_index.json`, caches, Git data, credentials, and secrets.

## Multi-platform publication gate

No Docker Hub tags may be published, replaced, or modified during Milestones 4–9.
At the final Milestone 10 publication gate:

1. Build and push these three tags from the same merged and tagged commit using
   Docker Buildx or an equivalently reliable multi-platform builder:
   `neprod/woocommerce-dashboard:phase-1`,
   `neprod/woocommerce-dashboard:0.2.0`, and
   `neprod/woocommerce-dashboard:latest`.
2. Prove each published manifest contains both `linux/amd64` and `linux/arm64`.
3. Prove all three tags resolve to the same multi-platform manifest digest.
4. Pull and start the `linux/amd64` image successfully for Unraid compatibility.
5. Pull and start the `linux/arm64` image successfully for Apple Silicon.
6. On both architectures, prove Gunicorn starts, migrations reach head, and
   `/setup` responds using only temporary mounted instance, catalogue, and output
   directories.
7. Inspect both platform images and prove all production inclusion/exclusion
   boundaries above.
8. Compare the existing `phase-0` and `0.1.0` references before and after
   publication and prove neither tag was modified or republished.

Phase 0's current single-platform limitation is historical. It must not be fixed by
overwriting a Phase 0 tag; compatibility is delivered through the new Phase 1
multi-platform tags.
