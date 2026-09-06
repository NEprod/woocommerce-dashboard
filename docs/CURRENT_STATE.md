# Current State

## M5.2 deployment/onboarding follow-up — 2026-09-06 (uncommitted)

Deployment now supplies `PRODUCT_FOLDER=/catalogue`, `OUTPUT_FOLDER=/output`
and `URL_PREFIX`; `INTAKE_ROOT=/intake` and `TAXONOMY_ROOT=/taxonomy` retain their
independent roots. Existing Unraid Intake mapping is unchanged. Docker image and
Compose declarations consume container paths; optional Intake/Taxonomy overlays
require explicit existing host directories. No build/publication occurred.

Settings resolves explicit application configuration before the original SQLite
fallback columns. Explicit blank values are not fallback requests. Deployment
values are never copied into SQLite or overridden by the setup form. No migration
or dependency was added. URL construction remains literal prefix + generated
filename; deployment must supply any intended trailing slash.

Login is unchanged. Authenticated setup uses a wide responsive readiness shell,
existing registry validation and the normal Scanner/Operation Detail workflow.
Initial scans remain explicitly confirmed Append; existing identities retain
reconstruction and intentional Full regeneration with identity warning. No new
scanner mode or taxonomy interpretation exists. Valid read-only registries permit
onboarding; missing/invalid registries block this first-run UI, not scanner logic.
Intake and Woo readiness are informative, not initial-scan prerequisites.

Audit correction: the old completion panel did not persist or enforce successful
initial scanning. New admin setup now creates instance-owned `onboarding.json`;
pending setup cannot open Dashboard until a successful, matching scoped operation
is server-revalidated by explicit Continue. Failures/recovery stay pending. An
absent record preserves legacy-installation compatibility; corrupt records fail
closed. Retain this file with instance backups. See ARCHITECTURE for details.

The user removed the old loose fixture directory and replaced the initially
incompatible legacy examples with six current-format collections under
`deployment/examples/tlc/products/`. Temporary-copy scanning passes: 9 Simple
parents, 6 Variable parents and 39 variations (54 rows), including a regular
Variable Collection and a sparse override. Seven JSON files and 97 images are
represented. Source hashes remain unchanged by tests. Categories are intentionally
not reconciled to the registry in this slice. No scanner workaround or automatic
conversion was included. The registry remains Ready at the digest below.

Focused verification: the related registry/workspace/scanner/reconstruction run
had 120 passes and 11 failures caused by one new readiness variable-name collision.
That collision was fixed; the affected onboarding/setup group then passed **18/18**.
The real initial runner completion test passed; existing-identity onboarding
reconstruction and the corrected six-collection regression additionally passed
**2/2**. Intake deployment/readiness/no-side-effect checks passed **3/3**. The first
replacement-data run exposed only the obsolete five-folder assertion; it now
asserts the exact six approved collection row counts. All affected failures were
resolved and rerun narrowly. Scanner JavaScript: **3 passed**. Python compilation,
four changed template compilations, XML parsing and combined Compose configuration
validation passed. The final local-fallback validation/redaction check passed
**1/1** after control-character path rejection was tightened; changed Python
modules compiled successfully. Final `git diff --check` passed. Branch remains
`develop`; the M5.2 implementation and curated references remain unstaged and
uncommitted. Repository status/diff inspection completed after a cloud-file
availability delay. No full project suite, commit, tag, push or Docker build was
run. M5.3 remains deferred.

## M5.2 ownership acceptance — 2026-09-06

M5.2 remains uncommitted. Generic bring-your-own-registry loading/startup is
confirmed: no automatic registry creation, TLC installation or fallback. The
missing-registry workspace now makes generic file provision primary and TLC
conversion optional. TLC's deployment-owned artifact is
[`deployment/examples/tlc/registry.json`](../deployment/examples/tlc/registry.json),
excluded from Docker context, with manual installation instructions in its
[README](../deployment/examples/tlc/README.md).

Production loader: Ready, schema 1, 56 categories, 7 attributes, 110 terms,
0 Storefront Collections, 0 tags. Canonical digest:
`01d7415e5c55ff233e342480c3b6c5577e0a56248e2198e289ef6f06a29bef61`.
Reviewed artifact definitions are Active; optional interactive imports still
initialize Draft. Original references and dev-fixtures remain untouched.

Focused verification: **77 passed** (M5.1 registry, M5.2 workspace and Unraid
contract), including production artifact validation/source equivalence, fictional
non-TLC startup/workspace, temporary TLC search/hierarchy/terms/editor roundtrip,
stale rejection and unrelated-definition preservation. Existing SQLAlchemy
Query.get deprecation warnings only. No full suite or Docker build was run.
Scanner/product JSON/M4/Woo/Relationships remain unchanged; no migration or
dependency. M5.3 product assignment/resolution and later Woo sync remain deferred.

This document records the completed Phase 1 (`0.2.3`) catalogue-integrity release, the completed Phase 2/2.5 (`0.3.1`) catalogue-management release, and current Phase 3 development. Phase 1 builds on the Phase 0 baseline without changing protected scanner row semantics.

## Released M4 checkpoint and planned M5 — 2026-09-05

Phase 3 M1–M4 is implemented through commit
`76635107047f095f011f93e2a88440a0ed4c5bf2`, tag
`phase-3-m4-variable-publishing`. The released M4 follow-ups add reviewed
local-only product Link/Unlink, explicit post-link/unlink Preview regeneration,
semantic title/category/tag/rich-text reconciliation, verified global child
attribute bindings, actual child variation publishing, missing-child Preview
work detection, verified-parent resume, and ordered variation galleries using
`image.id` plus `gallery_image_ids`. Parent commercial-field ownership and
parent/child image separation are preserved. Unlink removes only current-store
local trust and dependent child identities; no Woo deletion is performed.

Core Variable child publishing, recovery, primary variation images, and secondary
variation galleries are live accepted on WooCommerce 11.0.1 with the native
Variation gallery feature enabled. Release verification recorded 832 Python
tests and 28 JavaScript tests passed. The preceding relationship-test failure
was isolated to a stale test-instance recovery manifest; no Product Relationships
fix was included. Schema head remains `0007_woo_sync_identity`; the M4 follow-ups
added no dependency or migration. These are checkpoint facts, not fresh tests
or live requests performed during this documentation audit.

This dated checkpoint supersedes older implementation-status wording below.
In particular, current default-category resolution includes the authenticated
Woo Admin `default_product_cat` option lookup; settings-only/Store API-only
descriptions are not the complete current contract. Raw shortcode comparison
also handles supported structural equivalence when raw content is available.

**M5.1 and M5.2 are implemented:** read-only Taxonomy Registry foundation plus
authenticated registry workspace and reviewed bootstrap/editing.
Storefront Collection mapping and per-product variation designation remain
unimplemented. The architecture
audit proposes local filesystem definitions plus controlled product assignments,
offline validation, a versioned compatibility path and later reviewed Woo sync.
See [the audit](PHASE_3_M5_TAXONOMY_AUDIT.md) and [Roadmap](ROADMAP.md). File/schema,
destination and migration recommendations still require the indicated approvals.
The audit changed documentation only and did not change setup, scanner, editors,
Woo behavior or existing catalogue files. Pass 2 live acceptance, representative
catalogue/Discord regression and the final Phase 3 stable checkpoint remain
separate closure gates.

### M5.1 delivered foundation — 2026-09-05

`config.py` now exposes environment-configured `TAXONOMY_ROOT`, default
`/taxonomy`; explicit empty means unconfigured. The application does not invoke
the loader at startup and never creates a fallback directory/registry. The new
`app/taxonomy_registry.py` explicitly reads only `registry.json`, applies the
version-1 schema in `app/resources/taxonomy/registry.schema.json`, validates
stable keys/scoped identities/category references/cycles/normalization collisions,
and returns deeply immutable snapshots with deterministic SHA-256 content digests.
Directory-handle reads reject traversal, symlinks, non-regular files and root
overlap; limits bound bytes, nesting, nodes, definitions and category depth.

Statuses are `not_configured`, `root_missing`, `registry_missing`, `invalid_root`,
`invalid`, `inaccessible`, `read_only` and `ready`. Valid read-only snapshots are
available offline; readiness does not mean production population or Woo readiness.
No raw exception, host path or authored value appears in validation diagnostics.
See [Taxonomy Registry contract](TAXONOMY_REGISTRY.md) for the exact shape, limits,
namespace/alias rules and access APIs.

Focused verification command:
`PYTHONPATH=. /tmp/woocommerce-m4-variation-venv/bin/pytest -q tests/test_taxonomy_registry.py tests/test_setup_flow.py`
passed **42 tests**, including existing setup and an isolated Flask instance
startup test that proves the registry is never loaded during startup. After
tightening final-line control-character rejection in the schema,
`PYTHONPATH=. /tmp/woocommerce-m4-variation-venv/bin/pytest -q tests/test_taxonomy_registry.py::test_invalid_definitions`
passed **16 tests** (the affected group, including two added boundary cases).
Together the runs cover **44 distinct cases**: 41 registry/config cases and
3 existing setup cases. Python compilation passed for `config.py`, the loader
and the new test module. Existing setup emitted one SQLAlchemy legacy-API warning; no unrelated
fix was made. No full suite was run.

No scanner/catalogue/product assignment/editor/M4 Woo/Relationships behavior,
database migration, dependency or deployment mount changed. No production seed
was shipped or imported. Real TLC bootstrap, registry workspace/writes, deployment
mount declarations and all versioned product-resolution/projection/Woo integration
remain later gates at that implementation checkpoint. M5.1 was subsequently
committed as `28ac4b676bcd5a35d0ba7587abd90d093a9d59ba` without tag/push/build.

### M5.2 delivered local registry workspace — 2026-09-06

`app/taxonomy_routes.py` adds a dedicated authenticated blueprint, linked from
desktop Metadata navigation and mobile More. Overview shows readiness/schema/
digest/counts, search/pagination and last bounded local registry operation.
Guided category/range/attribute/term/tag editing and Advanced JSON share a
schema-validated signed review/confirm path. JSON drafts survive validation
failure; Advanced includes format, search, line numbers and escaped syntax preview.
Category cycles/dependencies and scoped vocabulary collisions remain blocked.

`app/taxonomy_workspace.py` converts uploaded TLC seed+matching CSV without
retaining uploads, inventing ranges/tags, or applying product assignment rules.
Reference conversion verified **56 categories / 7 attributes / 110 terms**.
Generated stable keys/slugs and conservative Draft/default-visibility-off values
are explicitly reviewed. Existing registry bootstrap is refused; intentional
replacement uses Advanced JSON review and backup instead. No production registry
was written during implementation and no private seed data is packaged.

Writes require CSRF, acknowledgement, user-bound 30-minute review, unchanged
source/directory revision, existing operation lease and directory flock. Verified
backup precedes atomic source replacement and strict readback. Rollback never
overwrites an externally changed version. Known-valid backup retention keeps 10;
failure evidence is retained. External editors must coordinate rather than race
app saves. Invalid existing source/missing mount requires administrator repair,
not silent fallback. See [exact contract and recovery limits](TAXONOMY_REGISTRY.md).

Deployment: new optional `compose.taxonomy.yaml` requires an explicitly selected
existing host directory (`create_host_path: false`); source-controlled Unraid XML
adds optional independent `/taxonomy` and `TAXONOMY_ROOT`. `.env.example` documents
the settings. Startup/Dockerfile/entrypoint do not create or require the root.
Runtime taxonomy and dev-fixtures are excluded from image context. No build ran.

Focused test evidence (existing pinned temporary Python environment):

- `pytest -q tests/test_taxonomy_registry.py tests/test_taxonomy_workspace.py tests/test_unraid_deployment.py::test_unraid_template_has_safe_supported_contract`:
  first run **66 passed, 1 failed**; the new HTML assertion incorrectly expected
  literal `>` rather than Jinja's safe `&gt;`. Test assertion corrected, no rendering
  safety weakened. M5.1's 41 cases and existing Unraid contract passed.
- `pytest -q tests/test_taxonomy_workspace.py`: **29 passed** after correction
  and additional lock/retention/rollback coverage; external TLC reference check
  enabled via `TLC_REFERENCE_ROOT` pointing at the supplied read-only directory.
- `pytest -q tests/test_taxonomy_workspace.py::test_request_bounds_precede_csrf_parsing tests/test_taxonomy_workspace.py::test_authentication_csrf_overview_and_templates tests/test_taxonomy_registry.py::test_application_startup_does_not_require_or_load_registry`:
  **3 passed** after the final taxonomy-only request-bound addition, including
  one new case. Aggregate covered: 41 M5.1 + 30 M5.2 + 1 existing deployment cases.
- `node --test tests/js/taxonomy.test.js`: **4 passed** (format/duplicate-key
  preservation, invalid input, safe bounded syntax highlighting).

All Python commands use `PYTHONPATH=.`. Existing SQLAlchemy Query.get warnings
were not changed. Python/JS syntax, template compilation/rendering, responsive/
semantic structure, XML and diff checks are part of this focused checkpoint.
No full test suite, migrations, dependencies, scanner/product assignment changes,
M4 publishing/recovery/gallery changes, Relationships changes, Woo calls, commits,
tags, pushes or Docker builds occurred. M5.3 remains unimplemented.

## Startup and setup

`run.py` creates the Flask application. The application factory configures Flask-SQLAlchemy, Flask-Login, CSRF protection, the main blueprint, and upgrades SQLite to the current Alembic migration head. `db.create_all()` is no longer used. Missing databases are initialized from migrations; a matching unversioned Phase 0 database is backed up and adopted at the frozen baseline.

A new installation follows `/` → `/setup` → `/initial-settings` → `/initial-scan`. Setup creates the initial administrator and stores the product root, output root, and public image URL prefix. The initial-scan screen classifies the configured catalogue as new, requiring reconstruction, ready, or ambiguous. It shows collection/product/marker/projection counts and enables only a safe recommended action.

Production startup requires an explicitly supplied non-placeholder `SECRET_KEY`.
The application never generates or persists it. `/app/instance`, `/catalogue`,
and `/output` remain three separate required mounts; storage, backup, operation,
temporary-file, and Docker-log limits are documented in
[Storage and Retention](STORAGE_RETENTION.md).

Phase 2.5 optionally recognizes a fourth, real `/intake` mount for authenticated
pre-catalogue image previews. The image and entrypoint do not create a fallback
directory. Missing intake storage never blocks startup or the established app;
the Catalogue Intake workspace simply reports unavailable.

## Catalogue Intake

Phase 2.5 Milestones 2–6 add authenticated routes at `/image-preparation`,
`/image-preparation/group`, `/image-preparation/folders`, and
`/image-preparation/rename`, plus `/image-preparation/metadata`. RC4 adds the
separate `/image-preparation/import-structured` path for a complete existing
folder hierarchy. It copies a digest-revalidated tree through hidden staging
into a new suffix-safe Prepared result while preserving the source. Review mode
enters folder review; final-structure mode enters image renaming after stricter
structure validation. Existing metadata is preserved byte-for-byte and does
not cause later stages to be skipped. These workspaces browse only
the canonical `/intake` root and render intake-relative breadcrumbs, supported,
hidden, corrupt, unsupported, unreadable, and unsafe-entry counts.

Grouping previews show the exact legacy trailing-number base beside the trimmed,
safe proposed folder, identify single-image groups, case/normalization conflicts,
and scanner-reserved Parent proposals, and display every future destination below
`Prepared/<source folder>/`. Rename previews validate an independent filename
prefix, show legacy and recommended names, hierarchy components, Parent ownership,
sequence scope, complete destinations, and global flattened-output collisions.
Visible collection metadata improves compatibility confidence; missing metadata
is reported rather than guessed.

All discovery and proposal ordering is deterministic. Request-scoped proposal
digests change with safe source identity or proposal inputs. Previews write no
files, folders, thumbnails, metadata, scanner markers, database rows, operation
history, or Discord events. They do not invoke the scanner or browse `/catalogue`.

A valid grouping preview may now be explicitly confirmed. The server recomputes
the proposal and digest, acquires the dedicated Intake mutation lock, copies
unchanged source images into private operation-owned staging, verifies the exact
tree, and atomically promotes it without replacement to a duplicate-safe
`Prepared/<source basename>/` result. Source files remain unchanged. Bounded
operation progress and one terminal Discord summary are recorded; notification
failure is non-fatal. Group names remain provisional, and the completed status
is **Grouping complete — folder review required**.

The Folder Naming and Structure Editor now advances the same visible Prepared
working result rather than creating another normal-progression copy. It supports
validated collection/product/variation/Parent renames, new empty folders, and
explicit removal of empty proposed folders. Case/Unicode collisions, unsafe
paths, duplicate Parent variants, non-empty removal, unsupported depth, and
future flattened filename collisions block confirmation. Swaps and case-only
renames are isolated inside operation-owned staging; unchanged image bytes and
the complete tree are verified before rollback-protected same-name promotion.
The terminal status is **Folder structure confirmed — image renaming required**.

An eligible folder-confirmed result can then be renamed in place through the
same hidden staging/rollback model. Final filenames use a validated normalized
prefix, all scanner-relevant hierarchy components, collection-root Parent
ownership, deterministic per-directory sequences, and lowercase source
extensions. A two-stage temporary/final rename supports cycles and case changes
without overwrite. Complete paths, image readability, count, and bytes are
verified before promotion. Proven superseded predecessors may be removed only
after explicit acknowledgement and verified success; uncertain or referenced
lineage is preserved. The terminal status is **Images renamed — metadata
required**.

The Prepared Metadata Builder then creates or corrects the authoritative shared
`product_info.json` in that same working result. It reuses the established
schema and complete collection-field inventory, offers guided and actual-authored
Advanced JSON modes, preserves unknown authored content, and validates folder
hierarchy without invoking the scanner. Save uses deterministic digest
revalidation, the shared Intake lock, hidden staging, atomic metadata writing,
unchanged image/tree verification, and rollback-protected same-name promotion.
The terminal status is **Metadata complete — validation required**.

Final validation and catalogue handoff now revalidate the complete Prepared
tree, metadata, image readability and scanner-facing hierarchy, show the exact
catalogue-relative create/replace destination, and require explicit
acknowledgement. Single Variable image depth follows only the ordered
`image_attributes`: exact sources are Ready, scanner-supported broader sources
and Parent preview fallback are warnings, and genuinely unresolved sources are
blocking. Non-image attributes do not create image-folder levels. The existing catalogue/scanner lock is acquired before the
Intake mutation lock. A byte-identical copy is verified in hidden catalogue
staging; replacement uses protected rollback and no merge. The Prepared result
remains unchanged. Success records **Catalogue handoff complete** and directs
the user to **Run Append Scan** manually. No scan, marker, SKU allocation,
database projection, output write, conversion, or upload occurs.

Catalogue Intake completion and Prepared-result views derive a single prominent
next action from that durable state. Signed result tokens are revalidated on
every navigation request, so stale, missing, failed, interrupted,
recovery-required, and ineligible results cannot enter a later stage. The
mapping is folder review → image renaming → metadata creation/editing → final
validation → Scanner. These GET links perform no mutation, and opening Scanner
after handoff does not start Append Scan.
See [Catalogue Intake](CATALOGUE_INTAKE.md).

## Scanner modes

- **append** processes products without `.scanned` and products carrying `.update`.
- **update** uses the same selection rule and reuses parent/variation SKUs from `.scanned`.
- **full** forces processing and regenerates SKUs using index counters.
- **shared collection refresh** explicitly forces every product in one collection
  while reusing marker identities. A shared JSON editor save invokes this mode;
  ordinary append and individual-update selection remain unchanged.
- **reconstruction** resolves the complete catalogue with marker and database
  identity reuse, disables SKU-index reset, creates a persistent SQLite backup,
  then updates the full projection in one controlled transaction.

Intentional full regeneration remains a separate warning-labelled action. The UI
and route require explicit confirmation because it retains the scanner's existing
SKU-reset behaviour. An empty database never selects it automatically.

The scanner supports exact collection types `Simple`, `Variable Collection`, and `Single Variable`.
Resolved product titles now apply the documented product/shared/folder fallback
contract, treating blank authored titles as absent. Collection display identity
continues to come from the collection folder basename. Append assignment is
protected by isolated multi-collection regression coverage and continues to use
portable source provenance rather than either title.

## Operation control

Append, product update, shared collection update, full, and reconstruction operation types share a non-blocking process-local lock. A conflicting request receives HTTP `409` with the active operation type and identifier before it changes catalogue files. Operation history is persistent and records bounded diagnostic fields and lifecycle counts; startup marks unfinished rows interrupted and requiring review. This control is intentionally limited to the documented single-worker, single-replica runtime.

Routine successful history is retained for at least 180 days and the newest
1,000 entries; resolved failure history is retained for at least 365 days.
Active, pending, unresolved-recovery, and newest-per-type records are protected.
Process memory retains 20 ordinary completed runs, while active/recovery runs and
their completion summaries remain protected. Each live log queue is bounded to
2,000 lines and approximately 2 MiB.

Phase 2 Milestone 7 presents this control through authenticated Scanner,
Operations history, and Operation Detail workspaces. Canonical starts expose
only Append, Update, and intentional Full, require explicit confirmation, and
repeat safe mount/database/lock checks server-side. Operations are sorted and
paginated in SQLite; detail combines durable summaries/items with bounded
process-local progress and redacted log snapshots. Safe retries return to the
confirmed Scanner flow and create a new history record. Cancellation remains
unsupported because process termination would weaken marker/database recovery.
See [Scanner, Operations, and Discord](SCANNER_OPERATIONS.md).

Ordinary scan ingestion adds one operation item per emitted parent. Successful items are committed with their parent transaction. A failed parent is rolled back and receives a separate sanitized failed item; the operation becomes `partial` when other parents succeeded or `failed` when none did.

Production scans stage `.scanned.pending` before database ingestion and finalize `.scanned` only after the corresponding parent commits. Database failures retain/recreate `.update`; marker-finalization failures retain pending identity. The next operation finalizes already committed intents before scanning and retries only unresolved products with preserved parent/variation SKUs. Marker and index JSON replacements are atomic.

Reconstruction does not rewrite a valid `.scanned`, reset `sku_index.json`, or
remove an existing `.update`. Database identity overlays supplement old marker
payloads for newly discovered variation combinations, so repeated reconstruction
does not allocate another SKU. Only genuinely unmarked products stage and finalize
a new marker. Outstanding pre-existing pending state makes the result partial and
`recovery_required` rather than falsely successful.

The authored `product_info.json` contract is now represented by collection and
partial-override JSON Schemas, a complete field inventory, fictional examples,
minimal/type-specific templates, and an authenticated in-app reference. Editor
saves validate before any backup, marker, operation lock, or scan side effect.
This is intentionally not scanner-wide strict enforcement; protected inheritance,
aliases, unknown collection types, and known discrepancies remain unchanged.

## Verified catalogue/database consistency

At the audit baseline, the live local catalogue and SQLite database agreed for 11 parent SKUs and 49 variation SKUs. Database integrity passed. Titles, types, mapped prices, dates, dimensions, descriptions, images, variation attributes, and supported modifier results agreed for the currently ingested subset.

The real catalogue and database are never part of the repository or container image.

## Web UI

Authentication, initial settings, the initial scan screen, collection-grouped
Products browser, raw JSON viewing, JSON editor, and metadata reference exist.

Phase 2 Milestone 1 adds an original responsive application shell based on
semantic design tokens. Bootstrap, application
JavaScript, and the project-owned SVG icon sprite are served locally without a
runtime CDN dependency. The permanent shell uses a desktop sidebar, compact
tablet rail, and a five-destination mobile bottom bar whose More action opens an
accessible off-canvas menu. Workspaces not implemented yet render explicit
`Planned` pages instead of missing templates or misleading functionality.
Legacy route aliases resolve safely. The setup folder browser is authenticated.

`DESIGN_SYSTEM.md` supersedes the interim Milestone 1.1 dark-first treatment.
The application is now light-first: warm canvas, white cards/forms/tables, dark
primary actions, restrained lime and teal, and deep-slate feature panels only
where they clarify hierarchy. JSON textareas, metadata examples, scanner logs,
table group headers, mobile navigation, and honest summary panels retain a
purposeful dark treatment. The original project logo is not redesigned.

Phase 2 Milestone 2 completes the local setup journey without redirecting away
from its result. Initial append/full scans and identity-preserving
reconstruction now finish on an honest summary with projection totals,
warnings, failures, and routes to Dashboard, Products, or the operation detail
already present on the page. A shared accessible operation-progress component
is used by setup and existing product metadata updates.

The scan runner's process-local progress payload retains its Phase 1 keys and
adds presentation-only operation type, stage, current collection, elapsed time,
collection/product/variation counts, warnings, and failures. These observations
do not change scanner selection, row resolution, operation locking, marker
coordination, ingestion, or persistent operation history. Reconstruction
remains synchronous in Phase 2 and publishes the same normalized completion
shape only after its existing controlled operation returns. Durable progress,
background reconstruction, and the full Operations workspace remain deferred.

Phase 2 Milestone 3 replaces the Dashboard placeholder with a read-only
catalogue-health view derived from the existing SQLite projection,
`CatalogueOperation` history, and the process-local operation state. It reports
real collection, parent-product, variation, active/missing, override, metadata
gap, recent-operation, and recently updated-product facts. Catalogue
availability is the share of projected parent products and variations whose
`catalogue_status` is `active`; metadata gaps are simple field-completeness
signals for active parents, not schema validation. Empty databases have honest
initial-scan actions. The Dashboard does not invent trends, sales, users, or a
WooCommerce connection, and does not add persistence or scanner side effects.

Phase 2 Milestone 4 replaces the flat Products table with a read-only,
collection-grouped browser over the existing SQLite projection. Collection
headers report filtered parent, variation, active/missing, and last-update
facts. Parent rows show genuine type, SKU, projected price/range, lifecycle,
thumbnail/fallback, metadata provenance, variation count, timestamp, and the
existing metadata actions. Title/SKU, collection, type, lifecycle, metadata
source, and Dashboard metadata-issue filters are URL-backed and parent results
are paginated on the server. Variation attributes, price, stock quantity,
lifecycle, provenance, and timestamps are fetched only when a variable parent
is expanded. Desktop uses grouped relational rows, while tablet and mobile use
collection-preserving product cards. This milestone adds no model, migration,
scanner, ingestion, marker, SKU, or filesystem behaviour.

The Milestone 4 image-display follow-up serves genuine source images from the
mounted catalogue through authenticated opaque product and variation routes.
The catalogue file is authoritative; Woo-facing `.webp` URLs are hints and may
differ from the source extension or name. Simple and variable parent thumbnails
follow scanner image order, then safe folder discovery. A variable parent with
no usable parent image uses its first ordered valid variation source, while an
expanded variation keeps its variation-specific source and uses the parent only
as a fallback.

Single Variable parent media is discovered from the reserved collection-root
`parent` directory using case-insensitive recognition (`parent/`, `Parent/`,
`PARENT/`, or mixed case). Actual source casing is preserved, and multiple
case-variants are rejected as ambiguous before attribute-folder interpretation.
Parent and variation ownership stays separate in markers,
emitted rows, ordered URL galleries, portable source assets, reconstruction,
and authenticated UI previews. Generated output files are not required for UI
resolution after ingestion.
Products and Dashboard Recent Products share the same safe
fallback presentation.

The Dashboard and Products "Missing images" signal means an active parent has
no safe, resolvable source image on either the parent or any variation. It does
not treat a missing emitted URL as proof that source imagery is absent, and it
does not invent variation-level completeness diagnostics. Woo-facing image
references remain ordinary SQLite text; source image bytes are not copied to
SQLite, `/app/instance`, or the production image.

Phase 2 Milestone 5 adds an authenticated resolved Product Detail workspace and
guided source editors without changing metadata ownership. Product Detail is
read-only: it combines the SQLite parent/variation projection with the
collection metadata source, optional partial product override, bounded relevant
operation history, and catalogue-backed image diagnostics. Parent identity is
shown before ordered variation children. Large variation sets render 24 at a
time and load further pages on demand.

Collection Metadata editing targets the one collection-level
`product_info.json` that may affect several products. Its affected-product
preview is bounded and paginated. Product Override editing targets only the
optional partial document for one product; inherited collection values remain
visible but are written only when the user explicitly enables an override.
Removing an enabled override field reveals its inherited value, and `{}`
remains a valid minimal override. Both editors default to guided contract-aware
fields and expose a deliberate Advanced JSON mode with parsing, formatting,
search, line numbers, highlighted preview, schema validation, duplicate-save
protection, and unsaved-change warnings.

Every save still uses the established validation, operation lock, collision-safe
metadata backup, atomic replacement, `.update`/shared-refresh orchestration, and
scanner projection workflow. Product and variation rows are never edited as the
authoritative source. Source references shown in the UI are catalogue-relative;
absolute host/container paths and temporary `/output` identities are not shown.
Ordered parent and variation image diagnostics keep ownership distinct and show
the stored final website URL as read-only text. The app does not upload,
convert, regenerate, or remotely verify images.

Local catalogue lifecycle and future publishing intent are presented as
separate concepts. `Product.catalogue_status` supplies Active, Missing, and
other existing local lifecycle labels. Resolved metadata `live` supplies
Published or Draft intent and records whether the value came from a product
override, collection metadata through inheritance, or the scanner default.

Phase 2 Milestone 9 completes the targeted release-candidate pass: canonical
product-title fallback, deterministic Append collection-assignment regression
coverage, shared centred action content, UK English interface copy, and mocked
Discord event/routing verification. Scanner cancellation remains unsupported;
only one catalogue mutation may run; live progress persists independently of the
browser; Discord delivery detail is not guaranteed across every restart;
multi-replica mutation execution, WooCommerce synchronisation, image upload or
conversion, filesystem collection management, confirmed pre-catalogue file mutation,
and remote media management remain outside Phase 2. Phase 3 Milestone 1 is
described below.
`Product.published` is the normalized projection of that resolved intent; it is
not evidence that a product currently exists or is published in WooCommerce.

Phase 2 Milestone 6 replaces the Collections placeholder with an authenticated,
server-backed Collections browser and resolved Collection Detail workspace.
Existing `Collection.id` supplies safe route identity while portable relative
paths supply provenance. Browser cards aggregate genuine product/variation,
lifecycle, publishing-intent, override, metadata-health, image-coverage, and
last-update facts with search, filters, sorting, and pagination. Collection
Detail summarizes the authoritative shared metadata source, affected products,
image diagnostics, and bounded scoped operation history, and links to Product
Detail and the existing Collection Metadata Editor. It adds no model or
migration and performs no collection filesystem mutation. See `COLLECTIONS.md`.
Collection-facing UI consistently uses the folder basename from portable
collection provenance as its title. The shared JSON `title` remains product
metadata, and changing it does not rename a collection or alter its integer route
identity. Nested duplicate basenames remain distinct and are disambiguated with
safe relative provenance where needed.

## Database projection

- The active path populates Collection → Product → Variation for all three exact collection types.
- Portable catalogue-relative source and JSON paths are stored separately from runtime absolute paths.
- Every emitted parent and variation row is retained as JSON; commonly queried pricing, inventory, publication, taxonomy, SEO, image, and attribute values are also normalized.
- Existing Product, Variation, and Woo placeholder identities are retained during ordinary row updates.
- Every committed parent's emitted variation set is authoritative. Stale
  variations become `missing` rather than being deleted, and matching rows are
  restored in place if emitted again.
- Products become `missing` only after a completely resolved and successful full
  scan, reconstruction, or collection-limited shared refresh. Append and
  individual product updates never reconcile unseen products.

Missing rows retain internal/SKU/Woo identity, provenance, relationships, and
timestamps. Product restoration matches portable `source_relpath` before SKU;
variation restoration matches its emitted attribute identity before SKU.

Scanner characterization also confirms that variation modifier sale prices are not emitted by the variation row builder, authored shipping class is emitted as blank, list ordering is not stable, unknown collection types yield no rows, editor relationship-key names differ from the row builder, and Woo rows are limited to five attribute slots. These remain protected discrepancies pending separate contract decisions.

## Integrations

WooCommerce-compatible rows and store-scoped verified identity tables exist.
Phase 3 Milestone 1 adds read-only WooCommerce/WordPress REST discovery, and
Milestone 4 adds a separately guarded, explicitly confirmed controlled publisher.
Optional Discord webhooks can receive
scanner start, clean/warning completion, failure, metadata, override, and the
existing product-ingest events. Delivery uses bounded process-local retry and
does not alter scanner success. Delivery state is not durable after restart.

## Known operational risks

- Multi-worker or multi-replica catalogue mutation is not supported; the lock is process-local.
- Scanner execution remains process-local, while bounded live progress and logs
  are persisted with the operation for safe cross-worker presentation.
- `/settings` is an authenticated, read-only view of safe application state,
  mount health, scanner locking/progress, Discord configuration booleans, and
  existing retention rules. It never renders configured paths, webhook values,
  secrets, or an environment dump. Future modules remain availability-aware
  placeholders until their approved milestones are implemented.

The protected scanner discrepancies and intentional full-scan semantics remain
unchanged.

Catalogue Intake warning-only completions retain their next-step navigation.
The shared helper requires zero blocking/failure findings, revalidates the
durable Prepared identity and current stage, and leaves destination validation
authoritative. Bounded grouped warning details appear on Prepared-result cards,
Operation Detail, and completed handoff review without changing validation,
  mutation, scanning, or Discord behavior.

Phase 3 Milestone 1 adds the authenticated `/woocommerce` workspace. Optional
store URL and API credentials are read exclusively from `WOO_STORE_URL`,
`WOO_CONSUMER_KEY`, and `WOO_CONSUMER_SECRET`; missing configuration does not
block startup. Opening the workspace is offline. The explicit Test Connection
action creates one retained operation, discovers the public WordPress REST index,
selects the highest advertised `wc/vN` namespace, and performs minimal bounded
authenticated GET checks for publishing and later resource groups. A central
request guard rejects mutation, TLS verification is enabled, redirects remain
same-origin, and raw indexes/responses are never retained. Verified reads,
advertised write methods, and unverified credential write permission are shown
as separate concepts. Only the bounded Milestone 4 workflow may issue reviewed
product/taxonomy writes; media upload, orders, broad synchronization, and other
remote mutation remain unimplemented.

The Phase 3 Milestone 1 API-index compatibility hotfix applies an explicit 8 MiB
decompressed limit only to the public WordPress `/wp-json/` discovery index.
Ordinary capability reads remain capped at 1 MiB. Streaming counts decompressed
chunks regardless of `Content-Length` or content encoding, closes responses on
abort, and reduces the decoded index to bounded relevant route/method summaries
before any operation state is retained.

New Woo connection tests persist bounded structured capability-limit findings,
including safe status codes and roadmap-derived current/future impact. Exact
limitations now appear in the Woo workspace, Operation Detail, bounded logs,
and one grouped Discord terminal summary. Historical count-only operations retain
a controlled detail-unavailable message. The shared light-card primitive also
resets inherited foreground text to the design-system light-surface token, so
light health metric cards remain readable inside purposeful dark panels.

Phase 3 Milestone 2 adds an entirely local Product Relationships editor to
Product Detail. Ordered cross-sell and upsell SKU lists are authored in the
appropriate catalogue JSON and projected for bounded database search and preview,
prevent self/duplicate/invalid links, retain broken references for repair, and
support recoverable all-file mutual cross-sell families. Scanner-emitted relationship text
columns remain untouched. No Woo request, payload generation, media action,
scanner invocation, or Catalogue Intake mutation occurs. A later two-pass
publisher will persist Woo product IDs and then translate these local edges.

The Milestone 2 follow-up adds the authenticated `/relationships` catalogue
workspace and signed mutual cross-sell family builder. JSON remains the durable
source and SQLite remains a reconstructable, searchable projection. Revision
`0006_relationship_workspace` adds indexed source-kind and last-change metadata
without changing authored relationship shape or scanner fields.

Phase 3 Milestone 3 adds an authenticated `/woocommerce/preview` workspace.
Opening it is offline; only explicit preview generation performs bounded,
cached Woo GET requests. The in-memory plan maps resolved local metadata to the
managed Woo v3 product and variation fields, classifies exact-ID/exact-SKU
identity outcomes, compares only managed remote fields, and separates Pass 1
identity-producing work from Pass 2 relationship IDs. Complete payloads and raw
responses are not persisted. Operation history retains only bounded counts,
store hostname/fingerprint, builder/mapping versions, and deterministic source
and plan digests. Revision `0007_woo_sync_identity` provides the minimal
store-scoped identity projection used by controlled publishing. Preview itself
does not write, create taxonomy, upload media, link IDs, or publish.

Phase 3 Milestone 4 adds controlled two-pass publishing for one to ten explicitly
selected eligible parent products. Final confirmation regenerates the exact
Milestone 3 plan, requires an unchanged digest and current store identity, shows
the bounded write estimate, and requires another acknowledgement when a selected
product has Published intent. Pass 1 resolves exact taxonomy identities, creates
or updates parents and variations, verifies managed fields through follow-up
GETs, and persists store-scoped IDs only after verification. Pass 2 translates
ordered local cross-sell/upsell SKUs to verified current-store Woo IDs;
unresolved targets remain visible and are never guessed. Operations retain
bounded progress, per-product outcomes, and recovery state rather than full
payloads or responses. No DELETE, media upload, scanner, Catalogue Intake, or
catalogue JSON mutation is part of this workflow.

The controlled-publish Operation Detail now has a complete server-normalized
view model throughout queued, running and terminal states and refreshes its
result panel on the terminal live-state transition. New Woo REST failures retain
only bounded structured code/message/status and field diagnostics with stage,
local object context and retry/reconciliation guidance. HTTP 400 remains a
confirmed non-uncertain rejection. Older generic failures remain readable; raw
response bodies, payloads, credentials, headers and full URLs are never stored.

Woo parent and variation dimension payloads now use builder contract
`phase3-m4-taxonomy-reconcile-v1`. Numeric catalogue projection values are serialized as
canonical decimal strings in Publish Preview and the exact controlled write;
remote dimensions use the same normalization for managed-field comparison. A
pre-write guard refuses numeric or non-canonical dimension values locally, and
the builder-version change invalidates earlier previews without changing the
authored JSON schema, scanner projection, or catalogue data.

Controlled publishing resolves stored final website image URLs to existing
WordPress Media Library attachments through bounded GET-only `wp/v2/media`
queries. Only one exact normalized `source_url` match is accepted; parent,
gallery, and variation payloads use the verified attachment `id`, preserve
ordering/ownership, and are revalidated before write. Missing or ambiguous
identity blocks publishing instead of falling back to `src`, preventing normal
controlled publishing from importing duplicate `-1.webp` attachments. No media
upload, deletion, binary persistence, authored media ID, migration, or dependency
was added. When readable through Woo settings, the store's configured default
product category is used only to normalize intentionally empty local categories;
explicit authored categories remain exact-ID managed state.

Taxonomy publishing now reconciles categories, tags, global attributes, and
attribute-scoped terms before every create or resume. Global `pa_` attribute
slugs normalize to the authored identity, successful create IDs are retained
through bounded direct/read-list verification, and uncertain retained writes
cannot trigger duplicate POSTs. Product reads prefer raw edit-context content;
a narrow structural `cg_accordion` comparison avoids false drift when Woo only
returns rendered HTML. Default-category equivalence is shared by known-ID,
exact-SKU, post-write, and recovery paths, with a bounded Store API inference
only when Woo settings omit the configured ID. Operation items and stage logs
now report selected taxonomy failures and zero-work skips accurately.
