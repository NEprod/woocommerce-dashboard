# UI Batch 2 — implementation and validation

19 November 2026. Implemented on `develop`; **not committed or released**.
No Shopify, Cloudflare, Unraid, permissions, Syncthing, catalogue JSON, original
image, real scan marker or production data was changed by Batch 2.

## Pages and shared presentation

Product Workspace now starts with breadcrumbs and one forest-headed overview.
Thumbnail, pricing, local lifecycle, intended/actual Shopify status, last successful
verification and existing eligibility evidence remain distinct. Source/queue/API
detail is available without dominating the first screen. The desktop fixture's
overview measured approximately 484px high after consolidation.

Local taxonomy groups browsing categories, storefront collections and attributes;
registered/legacy and variation-driver information is retained. Effective metadata
uses Field / Resolved Value / Source. Full Source Comparison retains every original
collection/override/effective value, including original HTML. Derived titles and
merged structures use neutral source indicators, not false inheritance labels.
Currency formatting does not round away higher authored precision. Gallery,
variations, relationships and their controls remain; real operation IDs now link
to the existing operation detail page.

Review & Sync immediately renders navigation and the existing form. On safe,
scoped active Simple/Variable GET pages, JavaScript submits the same CSRF-protected
`action=preview` request. Only its returned review region is replaced. Advanced
scope choices, page scroll and disclosures outside that region survive. Rebuilt
review disclosures deliberately start in their documented default state.

Unresolved/ambiguous identities and existing browser publishing receipts pause
automatic preview. Processing identities are not automatically finalised through
the existing preview's readback branch. Manual preview/recovery remains available.
No new endpoint, API query, reconciliation algorithm, queue job or execution mode
was added. Preview can still save comparison evidence as it did before; it never
authorises a Shopify mutation.

Blockers and contextual collection links precede changes. Review tables use
Field / Current Shopify / Proposed Value / Action / Status. Proposed variant rows
are grouped by owner; matching fields start collapsed. Expand/collapse controls
change disclosure state only. Original resource settings, capability evidence,
variant data, media references and all previously exposed fields remain in Full
technical / field evidence. Original signed token, acknowledgement wording and
confirmation fields are retained outside the technical disclosure.

Recovery starts with the real retained state, uncertainty and explicit next step.
IDs, raw stages and handles are expandable. Candidate identity entry, media
mapping approval, partial-variable inspection and signed continuation use the
same existing forms. A gallery requiring explicit adoption approval stays open.
Opening recovery never executes reconciliation or retries a mutation.

## Exact repository files

- `app/commerce_routes.py` — presentation context at the existing render return only
- `app/routes.py` — Product Workspace display labels and exact issue-link context only
- `app/product_presentation.py` — new formatting/grouping/link helpers; no execution decisions
- `app/static/assets/css/custom.css` — small shared operator additions using Batch 1 tokens
- `app/static/assets/js/shopify-preview-ui.js` — new read-only form trigger and disclosure controls
- `app/static/assets/js/shopify-publish.js` — bind dynamically returned forms once; existing submit/poll body retained
- `app/templates/product_detail.html`
- `app/templates/shopify_drafts.html`
- `app/templates/shopify_draft_identity.html`
- `app/templates/includes/_product_overview.html`
- `app/templates/includes/_shopify_review_technical.html` — retained complete review detail
- `docs/UI_OPERATOR_REGISTER.md`
- `docs/UI_BATCH2_VALIDATION.md`
- `tests/test_ui_batch2.py`
- `tests/javascript/shopify-preview-ui.test.mjs`
- `tests/browser/ui_batch2_fixture.py`

Unrelated existing `.wrangler`, development photo configuration and privacy/docs
files remain untouched and uncommitted.

## Actions mapped to existing handlers

| Operator action | Existing destination / handler | Effect and preserved authority |
| --- | --- | --- |
| Review & Sync | `commerce.drafts`, GET `/products/<id>/shopify/review` | Opens review; safe context invokes only the original preview POST |
| Build read-only Preview | Same route, POST `action=preview` → `shopify_drafts.preview` | Original full/inventory/customisations scope; no Shopify mutations |
| Confirm | Same route, POST `action=confirm` → `manual_publishing.accept` | Original token, product binding, required `acknowledge=yes`; unchanged worker/receipt handling |
| Refresh Product | `commerce.refresh_status`, POST `/api/shopify/status` | Same explicit single-ID read-only refresh with CSRF and `force=true` |
| Sync Now | `publishing.sync_now`, POST `/products/<id>/sync-now` | Original `acknowledged=yes` checkbox, default unchecked; queue/authorisation safeguards unchanged |
| Resolve Shopify Recovery / Identity & History | `commerce.draft_identity`, GET `/shopify/drafts/identity/<id>` | Navigation only; retained identity remains protected |
| Reconcile Existing Product | Same identity route POST → existing `service.reconcile` / lifecycle reconcile / verified-product task branch | Explicit Shopify reads and existing local evidence/operation handling; no automatic retry |
| Inspect partial variable work | Same identity route, POST `action=review_creation_completion` | Original read-only inspection; separate signed approval still required for continuation |
| Approve observed media mapping | Same identity route, POST `approval` plus `acknowledge_mapping=yes` | Original token/digest/correlation checks; no automatic adoption |
| View Collection Issue | `commerce.taxonomy_mappings?source=<actual-key>#individual-mapping` | Preselects the exact required existing source; does not link, register or reconcile it |
| View Collection / Edit shared metadata | Existing `main.collection_detail` / `main.collection_metadata_edit` | View or open editor; existing shared save/scan acknowledgement remains separate |
| Edit Product Override | Existing `main.product_edit(..., label='override')` | Opens sparse local override editor; no save on navigation |
| Scheduled Sale Controls | `commerce.sale_review` | Existing product-scoped review/authorisation |
| Correct Saved Package | `commerce.package_correction` | Existing separate reviewed assignment path; same Simple/retained-product visibility |
| Delete from Shopify | `commerce.delete_shopify_product` | Same `can_delete` condition and separate destructive review; never automatically triggered |
| View Shopify Product | Existing `shopify_url` | Opens the same store-bound Admin product URL |
| Relationship search/order/remove/save | Existing `data-*` URLs and relationship client | Same search, local review, confirmation and persistence contracts |
| Relevant operation | `main.operation_detail(operation_id=<real-id>)` | Existing persisted history, no invented IDs or routes |

Automatic preview accepts neither a `review` nor an `acknowledge` field and cannot
submit Confirm. Duplicate clicks while a read is active are ignored. Rebuilding
disables the old approval controls; interruption leaves them disabled until an
explicit successful rebuild. There is no automatic read or write retry. The
publisher's new WeakSet only prevents duplicate UI event binding after replacement;
it does not change payloads, ownership or operation state.

## Regression results

| Run | Passed | Failed | Classification |
| --- | ---: | ---: | --- |
| Original Batch 1 Python set A | 278 | 1 | Same existing Orders downgrade message assertion |
| Original Batch 1 Python set B | 286 | 5 | Same migration assertions and missing local barcode dependency |
| New focused Batch 2 Python | 23 | 0 | Product, source, preview, scope, token, exact child/parent blocker links and recovery presentation contracts |
| New focused preview JavaScript | 10 | 0 | Read-only trigger, interruption, concurrent click, existing receipt, scope and disclosure/event-binding checks |
| Original Batch 1 JS plus new preview tests | 118 | 1 | Same existing `productionDesigner` VM fixture failure |
| Additional publishing/recovery suite | 165 | 3 | Final stable-code repeat; all three failures reproduced on unchanged Batch 1 |
| Additional relationship Python (run together with focused Batch 2) | 46 | 2 | 22 focused + 24 relationship passes; both failures reproduced on unchanged Batch 1 |
| Preview + existing Shopify/relationship JS selection | 35 | 0 | Includes the 10 new tests; not an additional independent pass total |

Expanded-suite baseline failures independently reproduced using the unchanged
Batch 1 commit archive, not attributed to Batch 2:

- `test_shopify_products_workspace.py::test_unknown_is_not_proof_of_remote_absence`
  expects the older “Not yet verified” label instead of Local Draft.
- `test_shopify_products_workspace.py::test_bad_retained_plan_never_breaks_product_list`
  fails while saving its deliberately malformed receipt, before rendering.
- `test_shopify_legacy_availability_recovery.py::test_pending_description_survives_availability_restart_and_then_syncs_separately`
  hits a detached saved-view ORM object in the existing badge path after restart.
- `test_phase3_product_relationships.py::test_migration_is_sku_projection_and_preserves_product`
  pins an older migration head.
- `test_phase3_product_relationships.py::test_mutual_workspace_search_matches_product_detail_and_renders_warnings`
  observes 26 queries against its pre-existing maximum of 25 on the unchanged mutual-workspace route.

These are additional to the six Python / one JS Batch 1 baseline failures. No
backend fixes, test suppression or unrelated expectation changes were made here.
Syntax/compilation and `git diff --check` passed.

The original confirmation form and publishing submission body were also compared
programmatically with Batch 1 and remain unchanged. Publishing engines, worker
ownership/checkpoints, scanner, deployment guards, database models and application
configuration have no Batch 2 diff. The disposable browser server was stopped
after validation; no running deployment was changed.

One intermediate long test run straddled a template/context edit and saw an
undefined presentation helper. The complete stable-code repeat no longer has
that error (165 passed / three independently reproduced baseline failures); the
latest focused tests also pass. It was not dismissed as a baseline failure.

## Real rendered browser checks

The eight supplied PDFs were reviewed, including the user's corrected Girl
Birthday capture. Before browser captures use the unchanged Batch 1 templates and
assets with the same disposable synthetic database; after captures use Batch 2.
The fixture uses fictional Shopify adapters, temporary original images/markers and
SQLite only. It blocks external transport and every execution POST, permits only
read-only preview, and starts no worker. No live UI write is used as a test.

All three core pages passed desktop/tablet/phone overflow and input-labelling checks
at measured content widths of 1371 / 975 / 371px (requested viewports 1440 / 1024 /
390px). No unlabelled visible inputs were found. The 14-variant / 53-image Product
Workspace rendered all 14 cards and 53 gallery records. The review preserved 14
variant groups; Expand and Collapse worked on desktop and phone without changing
product rows or approval scope. Matching rows started closed.

Browser checks confirmed auto-preview reaches Preview ready; recovery context
disables automatic preview and links to its retained identity; confirmations stay
unchecked and required. Pressing Confirm without acknowledgement was blocked by
native validation and opened no publishing dialog. No browser JS errors were
captured. Network interruption/no automatic retry are additionally tested in the
isolated JavaScript suite. Final fixture counters reported zero external requests
and write actions disabled. Temporary viewport overrides were reset.

Safari native automation was interrupted while the user was working; physical
iPhone/native Safari acceptance is not claimed. Browser screenshots also reflect
the in-app browser's scaling; DOM checks, not screenshot width alone, establish
the no-overflow result. Original backend freshness and confirmation checks remain
authoritative on a real deployment.

## Screenshots, limits and next action

The complete before/after desktop/tablet/mobile index is stored at:

`/Users/dalethomas/.codex/visualizations/2026/08/13/019ffaba-fc7c-73b2-a5a2-bc66104c7b6e/ui-batch2/index.md`

Additional captures cover collection blockers, retained recovery, the 53-image /
14-variant state and phone approval controls. They are synthetic UI evidence,
**not** new live Shopify acceptance receipts. Native Safari remains an operator
check. There are no new design/authority decisions requiring expansion of scope.

Review Batch 2 and its known baseline limitations before any commit or development
release. No Docker image has been built/published and no deployment was performed.
