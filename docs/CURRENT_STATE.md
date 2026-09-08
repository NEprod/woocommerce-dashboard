# Current State

## M5 taxonomy definition sync — live accepted, 2026-09-08

User live acceptance confirms the grouped Categories / Attributes & Terms /
Storefront Collections → Woo Brands workflow, useful dependency/conflict and
per-item result reporting, and term menu_order reconciliation. A reviewed batch
of **45 definitions completed successfully** in one confirmation; the hard review
cap remains 50. Confirmation attempts all reviewed eligible selections, retains
earlier successes and reports any failed/uncertain/not-attempted work. Transient
GET retry is bounded; POST/PUT are never blindly retried. No delete reconciliation.
Global attribute-definition numeric ordering is not synchronised because the
documented Woo v3 contract does not expose it.

Taxonomy Woo IDs remain store-scoped SQLite state, never authoritative authored
JSON. The approved two-range edit to the deployment-owned TLC example registry
is included in this checkpoint, not installed as an application default.
This acceptance supersedes pending live-test notes below. Next is M5.6 product
publisher integration; the M5.3 explicit-contract guard stays until that integration
is proven. No product publisher/scanner/relationship semantics changed here.

Closeout verification on 2026-09-08: **40 focused Python tests passed** using
`PYTHONPATH=. /private/tmp/woo-taxonomy-release-20260908/bin/pytest` with
`tests/test_taxonomy_sync_acceptance.py tests/test_woo_taxonomy_sync.py
tests/test_taxonomy_sync_views.py
tests/test_m53_taxonomy_assignments.py::test_new_contract_preview_blocks_before_any_woo_read
tests/test_m53_taxonomy_assignments.py::test_execution_guard_rechecks_new_opt_in_after_legacy_confirmation
-q --disable-warnings --maxfail=1`.
JavaScript `node --test tests/javascript/metadata-taxonomy-client.test.mjs
tests/javascript/taxonomy-sync.test.mjs`: **14 passed**; both changed JS files
passed syntax checks. All 12 changed Python files compiled and 6 changed templates
parsed. Diff check passed. No full suite was needed. Existing pytest cleanup
warnings remained non-failing. Docker engine availability was restored before
closeout; no implementation files changed after verification. Checkpoint tag:
`phase-3-m5-taxonomy-sync`. The release uses one AMD64/ARM64 build for that immutable
image tag and `develop`; stable/latest/release are not promoted.

## Taxonomy sync live-acceptance corrections (uncommitted)

The grouped workspace is reported working live. This correction adds reviewed
ordering-only writes: registry term `order`, falling back to its zero-based array
position when absent, maps to Woo `menu_order`; global attributes use
`order_by=menu_order`. Wrong ordering is not Verified. Corrections require a
trusted identity, exact name/slug/scope and independent GET readback. Numeric
global attribute-definition order is not exposed by the documented v3 API.

Unverified category parents previously skipped candidate claiming, incorrectly
letting their possible children enter Woo-only import validation. Those candidates
now remain with their local dependency/conflict rows. Diagnostics identify local
parent keys, expected/observed Woo parent IDs, possible match counts or colliding
local keys. The example Cake Toppers is valid beneath `cat-cake-and-party`; its
live remote parent/mapping was not supplied, so the precise live state is not
assumed or rewritten.

The five-item stop was removed after live UX feedback. Confirmation again attempts
the entire reviewed selection (maximum 50), including the full reviewed import
proposal. A dedicated result page shows selected/attempted/succeeded/failed/
uncertain/not-attempted counts and individual reasons; earlier successes persist.
Access, verification or persistence failure stops further work explicitly.
Parent GETs are reused only within this operation for up to 15 seconds, and
invalidated after local global-attribute writes. Fresh exhaustive scoped discovery
and independent item readback remain: typically 3 requests/create, 2/link, plus a
shared short-lived parent GET for terms. Category parents are checked in the fresh
category list already fetched for each action. Confirmation re-plans with the
original 60-request budget; execution gets 60 + 4 × selected requests (maximum
260), avoiding an artificial 60-call cutoff for valid 50-item batches. Shared
3.05s connect / 8s read timeouts remain unchanged. Transient GET retries once
after 250ms; POST/PUT never retry. Large synchronous operations can still exceed
browser/proxy time limits; no background runner or hidden tiny batches were added.
Shapes-style conflicts now report only actual name/slug/parent mismatches or the
precise owning local key. Stale mappings report stored and current match IDs,
state, scope and definition changes separately. The supplied Shapes message lacks
the remote candidate name/slug and ownership state, so its exact live failing
field cannot be inferred; the next Preview exposes it. Cake Toppers' verified-parent
dependency remains intact. Product/scanner behaviour and the M5.3 guard are unchanged.

Attempt-all follow-up verification: `test_taxonomy_sync_acceptance.py`,
`test_woo_taxonomy_sync.py`, `test_taxonomy_sync_views.py`: 34 passed. Additional
precise-diagnostic, parent-reuse/readback and failed-result rendering tests:
3 passed (12 deselected), giving 37 distinct focused cases. Changed Python and
templates compile/parse and diff check passes. JavaScript was unchanged and not
rerun. No full suite, live Woo calls, Docker build or release actions.

Focused verification (no full suite): acceptance + existing sync + grouped-view
pytest modules: 31 passed; after adding import-work-unit and successful GET retry
coverage, acceptance module alone: 11 passed (33 distinct sync/UI cases across
these overlapping runs). M5.3 `new_contract_preview or execution_guard`: 3 passed,
23 deselected. Existing `taxonomy-sync.test.mjs`: 4 passed. Changed Python and
templates compile/parse; diff check passes. Existing pytest temporary-directory
cleanup warnings were emitted after successful runs; no cleanup code was changed.

## M5 local ranges + reviewed definition sync — 2026-09-07 (uncommitted)

M5.3 checkpoint is `645271f632e773382cb608c181c6aad354b4a9bd`, tagged
`phase-3-m5-taxonomy-assignments`. This bounded follow-up completes the missing
local `storefront_collections` assignment and implements reviewed definition
reconciliation for categories, global attributes, scoped terms and Storefront
Collections → Woo Brands. Dashboard filesystem Collections remain separate.

The existing metadata editors support registry choices, legacy visibility,
separate reviewed registry additions, sparse inheritance and explicit range
removal (`[]`). Product Detail resolves these assignments from authored source.
No scan-time registry import or product payload integration is added.

Taxonomy → Woo Sync (`/taxonomy/sync`) performs no reads on page load. Explicit
POST Preview, review and acknowledged confirmation use existing auth/CSRF,
signed 30-minute reviews, current-store identity, registry revisions, operation
locking/history and safe registry replacement. Batches contain 1–50 selections;
imports are reviewed separately from creates/links. Parent categories and global
attributes must first be verified, then a fresh Preview unlocks children/terms.
This deliberately uses staged rounds, not a new background orchestration system.

Woo Sync UX polish: compact overview plus Category, Attribute/Term and Storefront
Collection views reuse one explicit Preview without additional discovery on view
switches. Hierarchy/group selection and state filters separate Link/Create,
Verified, Issues and reviewed Woo-only imports. Selection count and atomic group
selection enforce 50 in UI and backend. Existing request budgets remain unchanged;
50 selections are not a guarantee that every batch fits the remote request budget.
Temporary browser acceptance of the grouped presentation remains required.
Focused polish verification: `test_taxonomy_sync_views.py` plus the existing
sync route auth/CSRF/freshness case: 5 passed; M5.3 preview/execution guard
selection: 3 passed (23 deselected); `taxonomy-sync.test.mjs`: 4 passed.
Changed Python/template compilation, JavaScript syntax and diff checks passed.
No full suite, live Woo calls or Docker build were run for this polish pass.

Migration `0008_woo_taxonomy_identity` adds one store-scoped mapping table because
product/variation identities require product FKs and cannot hold registry keys.
Created IDs are trusted only after separate readback. A durable uncertain-create
reservation prevents blind retries after interruption. Verified identity drift,
ambiguity and missing resources block rather than overwrite/delete. Completed
steps survive later failure; retained operation summaries explain partial work.

This environment has no Woo URL/credentials configured, so **no connected-store
Brands capability or live sync has been verified**. The current Woo controller
documents `wc/v3/products/brands`; each Preview requires advertised GET/POST plus
valid bounded readback before Brand actions appear. Unavailable Brands does not
prevent category/attribute work or local editing. Hierarchical Brands are not
silently flattened into flat local ranges. Tags remain deferred.

The user's pre-existing edit to `deployment/examples/tlc/registry.json` is
preserved, not part of this implementation. No dependencies, scanner/ingestion,
M4 payload, relationship, image, onboarding or deployment changes are included.
The M5.3 publishing guard remains. Next is separately approved product publisher
integration only after definition-sync live acceptance; no release in this slice.

Focused verification: **27 passed** (18 new sync/range/migration cases plus
9 existing authored-contract/registry safety cases), **7 M5.3 compatibility cases
passed, 19 deselected**, and **10 editor JavaScript tests passed**. Changed-file
compilation passed for **9 Python files** and **5 templates**; JS syntax and
`git diff --check` passed. Pytest emitted existing warnings and temporary-directory
cleanup warnings after success; no full suite or real Woo call was run.

Exact final Python selections (runner: `PYTHONPATH=.
/tmp/woocommerce-m4-variation-venv/bin/pytest`; both with
`-q --disable-warnings --maxfail=1`):

```text
tests/test_woo_taxonomy_sync.py
tests/test_product_info_contract.py::test_complete_example_and_templates_cover_the_contract
tests/test_product_info_contract.py::test_every_inventory_field_has_the_required_contract_classification
tests/test_taxonomy_workspace.py::test_readonly_stale_edit_and_unsafe_symlink
tests/test_taxonomy_workspace.py::test_signed_confirmation_safety
tests/test_taxonomy_workspace.py::test_postwrite_mismatch_keeps_verified_backup_and_reports_failure
```

Separate M5.3 selection: `tests/test_m53_taxonomy_assignments.py -k
'sparse_inheritance or actual_metadata_save or advanced_unknown or new_contract_preview or execution_guard'`.
JS: `node --test tests/javascript/metadata-taxonomy-client.test.mjs` and
`node --check app/static/assets/js/metadata-editor.js`.

## M5.3 checkpoint acceptance — 2026-09-06

The M5.2 initial-scan ingestion fix and M5.3 local taxonomy assignments/polished
editors are accepted for the combined `phase-3-m5-taxonomy-assignments` checkpoint.
The initial Append fix passed live testing: products committed, markers finalized,
and neither the relationship-authority error nor transaction cascade recurred.

Dale's clean browser control test used an untouched existing Variable product.
Adding registry-backed informational attributes with **Use for variations OFF**
retained the genuine drivers, existing combinations and every variation SKU:
`variations_created = 0`, `variations_missing = 0`; existing variations updated
normally. The earlier product edited with pre-polish drivers was contaminated
test state, not evidence requiring a further scanner/SKU change. Polished editor
browser acceptance is now complete, superseding the pending note below.

The explicit `variation_attributes` contract (including `[]`) remains intentionally
blocked from Woo publishing until separate per-attribute payload/verification
integration. Legacy M4 publishing remains unchanged. Reviewed Woo taxonomy sync
is the next separately approved slice; no sync or publisher integration is included.

Final checkpoint verification: **39 Python tests passed** (13 M5.2 + 26 M5.3)
using `PYTHONPATH=. /tmp/woocommerce-m4-variation-venv/bin/pytest
tests/test_m52_scan_ingest_regression.py tests/test_m53_taxonomy_assignments.py
-q --disable-warnings --maxfail=1`; **8 JavaScript tests passed** using
`node --test tests/javascript/metadata-taxonomy-client.test.mjs`.
All **12 changed Python modules** and **6 changed templates** compiled;
`node --check app/static/assets/js/metadata-editor.js` and `git diff --check`
passed. Pytest reported existing warnings and a temporary-directory cleanup
warning after success; no test failed. No full repository suite or live Woo
requests were run. Earlier dated notes below preserve implementation history.

## M5.3 editor consolidation — 2026-09-06 (polish, uncommitted)

Dale confirmed the preceding M5.3 core editor functionality in a temporary Docker
browser test. This local polish consolidates each attribute's name, terms,
registration/legacy badges and “Use for variations” checkbox into one row.
The separate editable driver list is removed. Row interaction explicitly adopts
or updates `variation_attributes`; the final unchecked driver preserves `[]`.
Viewing a legacy source does not opt in. Inherited designation remains sparse
until edited. A compact status/adoption action supports an explicit all-informational
contract; Advanced JSON still exposes the actual source contract.

“Add from taxonomy registry” pickers now sit in their respective Category and
Attribute sections, without a second assignment list. Legacy adoption links live
with the authored rows. Image attributes have a separate image-routing section.
Scoped CSS uses existing slate headers, white rows, compact badges, spacing and
responsive stacking; no dashboard-wide styling or business-logic change.

Verification: **8 editor JS tests passed** with
`node --test tests/javascript/metadata-taxonomy-client.test.mjs`; **7 Python
tests passed, 19 deselected** with the existing focused runner and
`tests/test_m53_taxonomy_assignments.py -q -k 'actual_metadata_save or advanced_unknown or registry_matches or new_contract_preview or execution_guard' --disable-warnings --maxfail=1`.
A local headless Chrome smoke check used the actual rendered fictional editor,
blocked network requests and confirmed legacy no-opt-in, row uncheck/empty-array,
row check, scoped registry assignment, informational new row and 375px attribute
panel containment, with no page errors. This is not live Docker acceptance.

Only `metadata_editor.html`, its taxonomy-choice partial, `metadata-editor.js`,
new editor-scoped `metadata-taxonomy.css`, the two focused test files and this
status note changed in the polish pass. No application Python, scanner/ingestion,
M5.2 fix, registry writer, Woo guard/payload, schema, inheritance, image or
relationship code changed. Therefore the 13-case scan-ingest group and curated
scanner suites were not rerun. No dependency or migration was added.
Another separately approved temporary rebuild/browser acceptance is required;
no commit, push, tag, Docker build or Woo sync occurred in this pass.

## M5.3 local assignments — 2026-09-06 (implemented, uncommitted)

This update supersedes the older “M5.3 not started” status below. M5.2 was
checkpointed at `30c3e6124680ec79d37f144769ca197de53ae69a`. Dale reports the
subsequent uncommitted initial-scan fix is live accepted on the temporary
`m5-2-initial-scan-fix-test` image. That root-precedence/transaction fix remains
intact; this task did not build or publish any image.

The bounded M5.3 slice implements registry-backed category and scoped
attribute/term choices in Collection Metadata and Product Override, a local
assignment resolver, read-only Product Detail recognition/driver display and
grouped categories/visible term lists in Taxonomy. Unknown assignments remain
visible as legacy; Advanced JSON preserves them. Registry creation/adoption is
an explicit separate-tab M5.2 review/ack/save, followed by local choices refresh
and a separate metadata save. Failure never causes a cross-file destructive
rollback. Normal scans never add definitions or rewrite authored vocabulary.

Category/tag inheritance stays additive; attribute objects keep replacement
between shared and sparse source. Present `variation_attributes` replaces
inherited drivers and preserves `[]`. Missing retains legacy behaviour. Explicit
Simple contracts have informational attributes and no drivers. Only selected
axes generate new-contract Variable children; existing image axes must remain
compatible. Existing row capacity limits explicit drivers to five, while all
informational attributes can project to existing ProductAttribute rows.
No migration, dependency, registry schema change or new Woo payload mapping.

The approved temporary guard rejects an opted-in Preview scope before Woo reads
and rechecks execution before its first request. Both non-empty and empty arrays
are guarded. The existing Preview flash now displays the precise reason. Legacy
M4 payloads, media reuse, relationships and publishing semantics are unchanged.
Per-attribute publisher integration must be separately implemented/tested before
removing this guard. The source resolver shares one configured root lookup per
Preview scope; the existing 500-parent query/request budget remains green.

Focused verification (no full suite):

- `PYTHONPATH=. /tmp/woocommerce-m4-variation-venv/bin/pytest tests/test_m53_taxonomy_assignments.py -q --disable-warnings --maxfail=1` — **26 passed**.
- Same pytest runner with `tests/test_m52_scan_ingest_regression.py tests/test_taxonomy_registry.py tests/test_taxonomy_workspace.py -q --disable-warnings --maxfail=1` — **88 passed, 1 skipped** (optional external TLC source pair not configured); includes all **13 M5.2 scan-ingest regressions**.
- Same runner with `tests/test_product_info_contract.py tests/test_phase2_milestone5.py tests/test_json_merge.py tests/test_onboarding_deployment.py::test_current_scanner_on_temporary_curated_products -q --disable-warnings --maxfail=1` — **45 passed**. The six-collection temporary-copy scan was justified by changed explicit-axis selection: unchanged **15 parents / 39 variations / 54 rows**, original source hashes preserved.
- Same runner with `tests/test_phase3_woo_publish_preview.py -q -k 'variable_child_uses_verified_global or safe_resume_reuses_verified_variable or large_fixture_has_bounded or variable_parent_precedes_variation or controlled_confirmation' --disable-warnings --maxfail=1` — **6 passed, 125 deselected**.
- `/Applications/ChatGPT.app/Contents/Resources/cua_node/bin/node --test tests/javascript/metadata-taxonomy-client.test.mjs` — **4 passed** (missing/empty distinction, sparse drivers, exact comma-containing terms, Advanced-to-guided override state).

Total: **165 distinct focused Python cases passed, 1 optional skip; 4 JS cases
passed**. Initial implementation failures (syntax/import shadowing, repeated guard
lookups, complete-reference inventory and invisible Preview flash) were corrected
and their affected tests rerun. Pytest reported pre-existing temporary-directory
cleanup warnings; no Product Relationships or cleanup change was made.

Final checks passed: Python compilation for the 12 changed/new Python modules
and tests, compilation of all 6 changed/new templates, editor JavaScript syntax,
and `git diff --check`. Actual route tests also rendered metadata, Product Detail,
registry and Preview screens. No full browser interaction/live editor acceptance
is claimed by these checks.

M5.3 is ready for a separately approved temporary-image/live editor check. No live Woo calls,
commit, tag, push or Docker build occurred. Next: approve bounded registry-only
Woo taxonomy synchronization (existing proposed M5.5), with store mappings and
destination decisions reviewed first. Full M5.4 roles/ranges and M5.6 publisher
integration remain deferred; historical Phase 3 closure gates remain open.

Changed-file inventory (including preserved M5.2 fix):

- Resolver/projection: `app/taxonomy_assignments.py` (new), `app/metadata_workspace.py`, `app/utils/json_utils.py`, `app/utils/scanner.py`, `app/utils/ingest.py`.
- Metadata contract/save: `app/product_info.py`, `app/routes.py`, `app/resources/product_info/field_inventory.json`, `app/resources/product_info/schemas/{collection,override}.schema.json`, `app/resources/product_info/{examples,templates}/complete.json` (fictional references only).
- Workspace: `app/taxonomy_routes.py`, `app/static/assets/js/metadata-editor.js`, `app/static/assets/css/taxonomy.css`, `app/templates/metadata_editor.html`, `app/templates/includes/_metadata_taxonomy_choices.html` (new), `app/templates/product_detail.html`, `app/templates/taxonomy/{base,index}.html`.
- Temporary guard only: `app/woo_publish_preview.py`, `app/woo_controlled_publish.py`, `app/templates/woocommerce_preview.html`.
- Tests: `tests/test_m53_taxonomy_assignments.py`, `tests/javascript/metadata-taxonomy-client.test.mjs` (new); preserved `tests/test_m52_scan_ingest_regression.py` (uncommitted M5.2).
- Documentation: `docs/ROADMAP.md`, `docs/CURRENT_STATE.md`, `docs/ARCHITECTURE.md`, `docs/TAXONOMY_REGISTRY.md`, `docs/PHASE_3_M5_TAXONOMY_AUDIT.md`.

## M5.2 initial-scan regression fix — 2026-09-06 (uncommitted)

Following checkpoint `30c3e6124680ec79d37f144769ca197de53ae69a`, focused
live-style tests reproduced the reported first-parent relationship-authority
failure and subsequent SQLAlchemy transaction cascade. Discovery was working.
Ingestion selected the raw SQLite `Settings.product_folder` column, bypassing
M5.2's deployment-aware instance property used by discovery and relationship
validation. Empty/stale persisted fallback values therefore lost source context.
Both Append and reconstruction ingestion now honour explicit `PRODUCT_FOLDER`
(including explicit blank), using SQLite only when deployment does not own it,
without opening the scoped parent transaction during root lookup.

The independent cascade existed in the M4 ingestion code: sanitizing the error
again after failure-item commit queried Settings and implicitly began another
transaction. Sanitization now completes within failure bookkeeping and its safe
string is reused for logging. A failed parent rolls back independently; the next
parent can commit. Relationship authority/path checks and marker recovery are
unchanged. No scanner discovery, authored metadata, Woo, M4 or M5.3 contract changed.

Verification: new regressions initially **6 failed / 3 passed**, reproducing the
exact two live errors. After the fix **9 passed**; **4 additional cases passed**
for reconstruction precedence, explicit blank/fallback lookup and failed-first
marker recovery/retry. **22 existing focused regressions passed** across complete
parent rollback, marker ordering/retry, relationship authority/projection and
onboarding. Total **35 distinct passing cases**. Python compilation and
`git diff --check` passed. No full suite, six-collection rescan, live Woo access,
commit, tag, push or image build ran. Temporary AMD64 retest is recommended next
as `neprod/woocommerce-dashboard:m5-2-initial-scan-fix-test`, not yet built.

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
