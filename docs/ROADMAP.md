# Roadmap

## M5.3 accepted checkpoint — 2026-09-06

M5.3 local controlled assignments and consolidated editors are browser accepted
for `phase-3-m5-taxonomy-assignments`, together with the live-accepted M5.2
initial-scan ingestion fix. A clean untouched Variable product retained all
existing variation SKUs and combinations when informational attributes were
added with **Use for variations OFF** (`variations_created = 0`,
`variations_missing = 0`). No further scanner/SKU correction is warranted by
the earlier contaminated test product.

This acceptance supersedes the review/pending status in the dated notes below.
The explicit-contract Woo publishing guard remains intentional. Next is the
separately approved, bounded M5.5 registry taxonomy-sync work; publisher mapping,
verification and guard removal remain M5.6. Deferred M5.4 work and the existing
Phase 3 closure gates remain open; this checkpoint does not close all of M5.

## M5.3 bounded local assignments and safety boundary — 2026-09-06

Implemented for focused review, not committed/released: registry-backed category
and scoped attribute/term choices in existing metadata editors, explicit reviewed
legacy/new-definition adoption, local matching/projection, explicit variation
drivers and a small grouped Taxonomy display. The approved bounded scope brings
the relevant editor work from proposed M5.4 forward into M5.3; it does **not**
implement all of the earlier versioned-assignment proposal or renumber history.

Readable category names/paths and attribute-name/term arrays remain authored
assignments. Categories remain additive across sparse overrides. Missing
`variation_attributes` retains legacy behaviour; present arrays replace inherited
drivers, including `[]`. New-contract products are blocked before Woo Preview
reads and rechecked before publication writes. Legacy M4 payloads are untouched.
No registry writes occur during scanning; no migration/dependency is introduced.

M5.2 checkpoint `30c3e6124680ec79d37f144769ca197de53ae69a` is committed. Its
subsequent initial-scan fix remains uncommitted and is preserved here; Dale has
confirmed successful live retesting with the temporary initial-scan image.

Next approval: the existing **M5.5 reviewed Woo taxonomy sync** scope, bounded
to registry definitions and store identities; settle identity persistence and
supported destinations first. M5.6 per-attribute publisher/verification integration
must be separately tested before removing the opt-in guard. Unimplemented M5.4
items (primary roles, ranges and broader adoption UX) remain deferred. Existing
Phase 3 closure gates below, including Relationships Pass 2 acceptance, remain open.
See CURRENT_STATE for actual focused verification and TAXONOMY_REGISTRY for the
implemented local contract.

## M5.2 configuration/onboarding checkpoint — 2026-09-06

Implemented but uncommitted: deployment-owned catalogue/output/image prefix,
preserved Intake mount, local Taxonomy readiness before explicit initial scanning,
wide authenticated setup shell, normal Scanner/Operation Detail reuse, and
success-only first-run completion bookkeeping. No scanner/product/Woo semantics
changed. Legacy installations without onboarding state remain supported.

Curated TLC references replace loose development fixtures. The user supplied
current-format replacements for the initial legacy files; all six collections
now pass temporary-copy scanning (15 parents, 39 variations), with original source
hashes preserved. See the [inventory](../deployment/examples/tlc/products/README.md)
and [Current State](CURRENT_STATE.md) for exact focused tests/results. Configuration
and onboarding are ready for final checkpoint review and deployment acceptance;
no commit/image publication has occurred. M5.3 has not started; historical M1–M4
records and outstanding Phase 3 closure gates below remain unchanged.

## M5.2 bring-your-own-registry clarification — 2026-09-06

Local workspace/readiness/editing is generic; no automatic registry creation or
TLC installation. TLC deployment data is separately provided at
[`deployment/examples/tlc/registry.json`](../deployment/examples/tlc/registry.json),
outside the image. Ready validation confirms 56 categories, 7 attributes and
110 terms, no ranges/tags. See [Current State](CURRENT_STATE.md) for focused
verification and [artifact instructions](../deployment/examples/tlc/README.md)
for digest/manual persistent-mount installation. M5.2 awaits commit/deployment
acceptance; M5.3 remains versioned product assignment/resolution semantics, with
Woo taxonomy sync later. Existing Phase 3 closure gates and M1–M4 history below
remain unchanged.

## Current checkpoint and next scope — 2026-09-05

This dated section supersedes older status shorthand below without renumbering
or rewriting completed milestone history. Phase 2/2.5 is released as `v0.3.1`;
Phase 3 Milestones 1–4 are implemented. The current released development
checkpoint is `76635107047f095f011f93e2a88440a0ed4c5bf2`, tagged
`phase-3-m4-variable-publishing`. M4 Variable child creation, resume/recovery,
reviewed Link/Unlink and primary/secondary variation images are complete. Core
Variable publishing and galleries are live accepted on WooCommerce 11.0.1 with
the native Variation gallery feature enabled. Checkpoint verification: 832
Python tests and 28 JavaScript tests passed. This is not a Phase 3 stable release.

**Phase 3 M5 — Taxonomy Registry & Controlled Catalogue Metadata: M5.1 read-only
foundation and M5.2 registry workspace implemented; M5.3+ deferred.** The previously recorded roadmap
did not assign a numbered M5. This scope adds authored local taxonomy definitions
for categories, Storefront Collections/named ranges, navigation attributes,
attribute terms and tags; controlled product JSON assignments; informational
attributes and explicit per-product variation designation; offline scanner
validation/resolution and projection; controlled metadata editors; reviewed Woo
taxonomy synchronization; and Preview/publisher/reconciliation integration.
Storefront Collections remain distinct from filesystem/dashboard Collections.
Incremental synchronization, media upload and broad remote management are not
silently included in this milestone.

See [M5 architecture audit and proposal](PHASE_3_M5_TAXONOMY_AUDIT.md) for observed
contracts, decisions requiring approval, evidence, file impact and focused tests.
Proposed implementation slices (future work, not historical milestones):

1. **M5.1 — implemented:** optional `TAXONOMY_ROOT` (default `/taxonomy`),
   schema-v1 `registry.json`, bounded read-only loading, immutable snapshots,
   content digests and safe readiness/validation diagnostics. See the
   [implemented registry contract](TAXONOMY_REGISTRY.md). No startup load/gate,
   scanner, product JSON or Woo integration; no migration or dependency.
2. **M5.2 — implemented:** authenticated guided/Advanced registry editing,
   signed review/confirm, stale-source guards, verified backups/atomic save,
   reviewed TLC JSON+CSV bootstrap and optional persistent mount declarations.
3. **M5.3:** approved versioned resolution/projection and necessary reviewed schema
   extension, protecting legacy rows, variations and source identity.
4. **M5.4:** controlled shared/override editors and explicit adoption review.
5. **M5.5:** reviewed Woo taxonomy sync after resolving range destination.
6. **M5.6:** Preview/publisher/reconciliation integration and M4 regressions.
7. **M5.7:** representative adoption, live acceptance and approved checkpoint.

The resolver/projection slice precedes writable new product-editor semantics;
registry definition editing alone may precede it. Every slice must update this
roadmap, CURRENT_STATE and its affected contract docs with delivered scope,
verification, decisions and the next gate. Detailed proposal choices are not
approved merely by appearing here. The audit itself was documentation-only;
M5.1 implements only its separately approved foundation.

M5.1 focused verification: registry/config plus existing setup tests passed
(42 tests); after the final schema correction the affected definition group
passed 16 tests, including two new boundary cases (44 distinct cases overall).
Python compilation and documentation/schema checks accompany
the slice. No full regression, Docker build or publication is part of M5.1.
Real TLC seed import remains a later reviewed step, not an empty production
taxonomy endpoint. M5.2's explicit importer now converts the supplied references;
production import still requires review/confirmation. The next gate is M5.3
versioned local resolution/projection; product editors must wait for that contract.

### M5.2 local registry checkpoint — 2026-09-06

Implemented [registry workspace/write contract](TAXONOMY_REGISTRY.md), no product
or Woo integration. Supplied TLC read-only conversion verified 56 categories,
7 navigation attributes and 110 terms; no ranges/tags invented. Optional Compose
overlay and Unraid `/taxonomy` mapping leave existing startup/deployment optional.
Focused verification covered M5.1, workspace auth/CSRF/CRUD/review/backup/failures,
seed conversion, templates, responsive structure and deployment XML; see exact
run results in CURRENT_STATE. No full suite, Docker build or publication.

### Outstanding Phase 3 closure gates

- Product Relationships Pass 2 live acceptance; do not infer this from parent
  or child publishing acceptance.
- Representative real-catalogue regression, including accepted recovery/Unlink,
  taxonomy, variable combinations, prices and ordered media.
- Final Discord webhook/notification regression.
- Final Phase 3 stable checkpoint, separately approved verification/promotion.

M4 recovery/Unlink remains recorded as completed; it needs regression coverage,
not reimplementation. Historical stable and immutable tags remain protected.

## Previously recorded phase and milestone history

- **Phase 0 — Secure baseline, documentation, tests and containerisation:** complete baseline scope for version `0.1.0`.
- **Phase 1 — Database ingestion parity and catalogue integrity:** complete in `0.2.0`. Scanner characterization, migration/recovery foundations, operation control/history, catalogue projection/provenance, complete-parent transactions, recoverable marker coordination, lifecycle reconciliation, identity-preserving reconstruction, the metadata contract, and final release verification are complete.
- **Phase 2 — Catalogue management UI:** complete as a release candidate on `develop`. Milestone 1
  establishes the semantic design system, neutral branding, responsive
  navigation shell, local UI assets, authenticated folder browser, and safe
  placeholder/compatibility routes without changing the scanner or schema.
  Milestone 1.1 established the interim contrast baseline. The approved design
  reconciliation replaces its dark-first styling with the canonical light-first
  system, permanent sidebar/mobile shell, accessible tables, and dedicated dark
  hierarchy/code states without changing application behaviour.
  Milestone 2 completes setup-result and shared operation-progress presentation.
  Milestone 3 adds the genuine read-only catalogue-health Dashboard over the
  existing projection and operation records. Milestone 4 replaces the legacy
  flat catalogue table with the genuine collection-grouped Products browser,
  URL-backed supported filters, server-side parent pagination, and lazy
  variation previews.
- **Phase 2.5 — Pre-catalogue image preparation:** in progress on `develop`.
  Milestone 1 audited the two legacy Tk utilities. Milestone 2 adds an optional
  `/intake` mount plus authenticated, deterministic, strictly read-only folder,
  grouping, rename, and scanner-compatibility previews. Milestone 3 adds explicit
  digest-revalidated, copy-first grouping through private verified staging into
  duplicate-safe provisional `Prepared/` results while preserving every source.
  Milestone 4 adds the copy-first Folder Naming and Structure Editor. Milestone 5
  corrects normal progression to one visible rollback-protected Prepared working
  result and adds deterministic, two-stage, byte-preserving image renaming with
  conservative lineage-proven predecessor cleanup. Milestone 6 adds guided and
  Advanced JSON authoring, exact preview, folder-aware validation, and
  rollback-protected same-name `product_info.json` saving. Milestone 7 adds
  complete scanner-aware validation and a digest-protected, copy-only catalogue
  handoff using hidden staging, safe replacement rollback, preserved Prepared
  source, and an explicitly separate manual Append Scan.
- **Phase 3 — WooCommerce integration:** Milestone 1 implements environment-only
  credentials, secure read-only API discovery, capability auditing, and bounded
  health history. Milestone 2 adds the entirely local Product Relationships
  source of truth for ordered cross-sells and directional upsells, plus a
  catalogue-wide health workspace and signed mutual-family builder. Milestone 3
  provides a read-only, digest-bound Woo payload preview with store-scoped
  identity resolution and a two-pass publish plan. Milestone 4 implements its
  first bounded execution path: persist verified Woo product/variation IDs in
  Pass 1, then translate local relationship edges into `cross_sell_ids` and
  `upsell_ids` in Pass 2. Incremental synchronization, media upload and broad
  remote management remain later milestones.
- **Phase 4 — Extended WooCommerce updates and synchronization:** planned.
- **Later — Website/header automation, scheduling, notifications, and business dashboard modules:** planned.

Phase 1 is the current catalogue-integrity release. Future work must continue to treat its scanner contract and database/recovery behaviour as protected baselines.

## Phase 2 execution plan

Phase 2 uses sequential approval gates on the long-lived `develop` branch:

1. Design system, neutral branding, navigation shell, local assets, safe routes.
   Milestone 1.1 is a visual correction within this completed scope and is not
   the start of Milestone 2.
2. Setup completion and unified operation-progress presentation foundation.
   **Complete on `develop`.** Setup retains its completion summary and next
   actions; existing scan/update surfaces share a normalized, accessible live
   presentation over the protected process-local runner state.
3. Catalogue health dashboard. **Complete on `develop`.** Genuine projection,
   metadata-completeness, operation, attention, and recent-product data replace
   the placeholder without adding analytics or persistence.
4. Products browser with collection grouping and lazy variation previews.
   **Complete on `develop`.** The browser derives counts, lifecycle, pricing,
   images, provenance, and timestamps from the existing projection. Variable
   detail is fetched only on expansion; no scanner or persistence contract is
   changed.
5. Product detail and metadata provenance experience. **Complete on `develop`.**
   Resolved read-only Product Detail, collection-scoped guided metadata editing,
   intentional partial product overrides, explicit Advanced JSON, ordered image
   diagnostics, and bounded affected-product/variation loading all reuse the
   existing save and scanner update authority.
6. Collections browser and collection workflows. **Complete on `develop`.**
   Genuine collection aggregates, metadata health, lifecycle/publishing-intent
   summaries, image coverage, affected-product pagination, contextual operation
   history, and cross-navigation reuse the existing projection and editor.
7. Scanner workspace and operation history UI. **Complete on `develop`.**
   Authenticated confirmation/readiness, persisted operation browsing, bounded
   live diagnostics, safe retry preparation, and optional hardened Discord
   notifications reuse the existing operation schema and protected scanner.
8. Settings, accessibility, responsive refinement, and future placeholders.
   **Complete on `develop`.** Authenticated read-only configuration health,
   mount readiness, safe Discord state, navigation consistency, and targeted
   accessibility/responsive polish use existing runtime authorities without a
   schema change.
9. Targeted catalogue/UI corrections, final documentation, contract and
   Docker/Unraid verification, and a release-candidate image. Stable `v0.3.0`
   promotion remains explicitly gated on manual Unraid approval.

Milestones 1–8 publish immutable multi-platform development images plus the
moving `develop` image. They do not update stable or historical image tags.

## Phase 1 execution plan

The approved Phase 1 work remains divided into Milestones 0–10:

0. Verify the Phase 0 baseline, branch, tests, image, and source boundaries.
1. Characterize and protect scanner contracts using isolated fictional fixtures.
2. Add migrations, conservative Phase 0 adoption, persistent backups, and restoration.
3. Add single-process catalogue-operation locking and persistent operation history.
4. Activate Collection → Product → Variation projection, field parity, and portable provenance.
5. Make ordinary ingestion atomic per complete parent product. **Complete.**
6. Add recoverable marker/database orchestration without claiming cross-store atomicity. **Complete.**
7. Reconcile variations and missing/restored products, including shared collection updates. **Complete.**
8. Add identity-preserving reconstruction distinct from intentional full regeneration. **Complete.**
9. Formalize the complete metadata contract, schemas, examples, and in-app guidance. **Complete.**
10. Complete all acceptance, migration, reconstruction, source-boundary, Docker, publication, and documentation checks. **Complete.**

Milestones 4–9 must not publish, replace, or modify any Docker Hub tag. Final publication belongs exclusively to Milestone 10 after all preceding acceptance criteria pass.

Phase 1 production publication must use one multi-platform build result for `neprod/woocommerce-dashboard:phase-1`, `neprod/woocommerce-dashboard:0.2.0`, and `neprod/woocommerce-dashboard:latest`. The shared manifest must include `linux/amd64` and `linux/arm64`. Existing `phase-0` and `0.1.0` tags are immutable and must not be republished. See [Phase 1 Acceptance](PHASE_1_ACCEPTANCE.md).
