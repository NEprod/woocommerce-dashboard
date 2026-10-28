# Legacy source checkpoints, reconciliation and visible publisher cycles

## Confirmed starting point

The read-only Unraid investigation of `141748a464d7` found an enabled,
correctly bound hourly publisher. Its two runs selected zero products and made
zero API requests. Four Live products had bootstrap Attention entries with
`requested_revision=unscanned`, no scan-operation binding and zero attempts:
LCBC-0002, 16bit-0001/0002 and ADOC-0001. Older successful ingestion receipts
were not publisher checkpoints. 3DCO-0001 and HERO-0001 had correctly settled
to No Changes; 16bit-0003 correctly projected Local Draft after page reload.
Event History excluded the scheduled-task ledger, explaining the missing cycles.

No production scan, reconciliation, database repair, setting change, Shopify
request or deployment was performed during this development milestone.

## Chosen compatibility approach: A, explicit one-time validation

Queue → product → **Validate legacy checkpoint / containing-collection update**
offers two distinct actions. Validation is an administrator-confirmed POST with
the existing CSRF protection and exclusive catalogue-operation reservation.
It is not an hourly task, scanner, migration or automatic source adopter.

Validation requires all of:

* An active, source-bound, unowned bootstrap Attention job with `unscanned`
  revision, no retained publishing operation, and effective Live intent.
* A successful committed product item matching both SKU and portable source path
  in a retained completed scan. A partially completed batch is acceptable only
  when this exact product committed and the scan has no recovery condition.
* A persisted resolved catalogue row, mounted/constrained JSON, valid existing
  metadata schema, and every selected original image present and supported.
* Current JSON/images whose modification **and change** times do not postdate
  that scan. Restored/copied/changed files that cannot satisfy this conservative
  proof require the existing targeted collection update, not timestamp repair.
* Existing variable projection validation: exact options/SKUs, supported variant
  count, calculated persisted prices/measurements and original gallery sources.
* No conflicting/incomplete/uncertain Shopify identity or operation. Linked
  products must retain complete verified identity evidence.
* Stable file stats during validation and an unchanged queue generation/owner at
  the conditional database update.

The existing local publisher builds/validates its source plan without Shopify
calls, staging uploads, marker writes or scanner dispatch. A separate
`publishing_checkpoint_validation` receipt records who validated the source,
when, the current revision and original scan reference. The original scan is
not edited, invented or redated. The queue references that scan and retains the
separate validation proof. Active proof references are protected from normal
operation pruning. No new database table or migration is needed.

Successful validation makes the Live revision Pending, not verified. The existing
publisher must still perform duplicate/identity checks, source freshness,
authorisation, supported mutations and complete readback. If automation is
paused, no automatic write is authorised by validation alone.

Draft, matched no-op, owned, Recovery Required and uncertain operations cannot
be converted through this action. No source bytes or catalogue projections are
rewritten. Ordinary onboarding, append, editor-scoped, unchanged-folder skip and
explicit full-scan behaviour is untouched. Future completed ingestion continues
calling `queue_product` and `capture(scan_operation=...)` atomically.

## Reconciliation → the same publisher

Task `shopify_products` (Shopify Product Reconciliation) remains independent,
with a 1,440-minute new-install default; persisted merchant intervals are not
changed. At the production inspection its last run was 28 October 12:01–12:03
GMT and next due was 29 October 12:01 GMT. No later live inspection was made.

Daily/manual task runs use the existing read-only status refresh and persisted
`ShopifyProductComparison`. Required remote read failures, missing evidence,
scoped reads and interrupted pagination are not complete equality. Immediate
publisher readback is different: it verifies that an acknowledged mutation
actually achieved its approved result before finalising the operation.

The existing `observe_comparison` hook now reopens an update only for a matching
local checkpoint, full complete comparison, unique matching store/product/source
identity and complete retained verification. It preserves active ownership,
Recovery Required, conflicts, blockers and Draft pause semantics. A CAS protects
against a newer revision/owner. Reconciliation clears obsolete verified-revision
equality when genuine remote drift needs correcting; it does not manufacture a
new local revision or perform the correction itself. The normal publisher does
the subsequent update and persists readback/Up to Date. Missing or stale local
authority remains blocked until explicit validation or a real targeted scan.
Remote deletion is not authority to recreate a replacement.

The Identity/History Reconcile button now accepts a scoped durable manual
reconciliation task for already completely verified identities (no candidate ID
or mapping approval supplied). It does not run recovery verification against an
old desired state when the intent is ordinary drift inspection. Uncertain,
incomplete, deletion and explicitly approved recovery paths stay unchanged.

## Visibility, not another event store

Event History SQL-paginates a union of existing product operations and publisher/
product-reconciliation task runs (10 rows/page). Task entries link to the existing
run detail. Manual versus scheduled reconciliation is explicit. Original product
receipts and recovery links remain unchanged.

The previous 100-completed-runs global retention limit could evict daily results
after frequent five-minute sales/releases. The same existing retention now keeps
up to 100 completed runs **per task**, still expiring history older than 90 days
and preserving active runs. Product/recovery receipt retention is not shortened.

New publisher cycles retain a source-free saved-state eligibility inventory,
timestamp and compact per-product outcome codes in the existing bounded run
JSON. Counts distinguish missing checkpoints, matching state, paused Drafts,
owned/processing work, recovery, pending and other Attention; verified create/
update, no-op, pre-write failure and incomplete verification are separate.
No repetitive per-product skip event or Discord message is created.

Reconciliation retains comparison outcome categories alongside actual checked,
updates, unavailable, deferred, API/throttling counts and existing detailed
product reasons. Only recorded counts are displayed for older runs. Historical
unrecorded exclusions/differences remain explicitly unavailable.

Scheduled Tasks exposes compact execution/product summaries on desktop/mobile.
Product Details and Identity/History separate the queue, scan/checkpoint and last
Shopify comparison from historical failures. Draft queue wording no longer
suggests that ordinary Draft metadata will publish on the next scheduled run.

## Production file responsibilities

* `publishing_checkpoint.py`: explicit local-only validation and durable receipt.
* `automatic_publishing_routes.py`, `publishing_scan.html`: confirmed validation
  action alongside the unchanged existing targeted-scan action.
* `automatic_publishing.py`: safe comparison requeue and bounded cycle outcomes.
* `catalogue_views.py`: retain current validation proof across saved projections.
* `scheduled_tasks.py`: persisted read-only/comparison outcome facts and summaries.
* `operations_views.py`, `publishing_events.html`: paginated combined ledger view,
  truthful summaries and product-authority presentation data.
* `routes.py`, `commerce_routes.py`, product/identity templates and shared
  `_publisher_authority.html`: render that read-only evidence on existing pages.
* Scheduled-task/queue partials: display the same retained facts and Draft pause.
* `utils/operation_control.py`: reserve validation through the existing global
  slot and protect checkpoint/validation receipt references from pruning.

No scanner, Shopify mutation executor, extension, theme, Orders, package, JSON
schema, scheduling interval/default or authorisation-policy implementation changed.

## Merchant acceptance and rollback

These steps are **not executed by this release**. Also complete the existing
`AUTOMATIC_PUBLISHING_ACCEPTANCE.md` and `OPERATIONS_ACCEPTANCE_CHECKLIST.md`.
All Shopify mutation tests require an explicitly authorised isolated staging
store. Do not transplant live remote identities into a different staging store.

| Test | Exact action / expected evidence | Result |
|---|---|---|
| Pause and backup | Merchant pauses automation before validation/scans. Stop the appropriate container to back up the complete `/app/instance` mount plus paired catalogue/taxonomy/output. Record current immutable image/digest. | [ ] Pass [ ] Fail |
| Read-only deployment checks | Verify image revision, policy still paused, existing IDs/receipts, and no automatic local scan after configured restart. | [ ] Pass [ ] Fail |
| Existing matches | 3DCO/HERO show last complete match and No Changes; no new checkpoint is invented and no Shopify write is queued. | [ ] Pass [ ] Fail |
| Legacy validation | For each of the four named Live products, open guidance and explicitly validate. Capture separate validation and original scan receipts. Valid proof yields Pending; unprovable source gives a precise refusal and targeted-scan guidance. Files remain byte-identical. | [ ] Pass [ ] Fail |
| Changed source | In a copied staging catalogue, edit JSON/image after its old scan. Validation refuses; only the normal approved targeted update establishes a new revision. No root/full scan. | [ ] Pass [ ] Fail |
| Local/linked Draft | Confirm 16bit-0003 stays local and linked Draft metadata stays paused. No create/update or routine Discord message. | [ ] Pass [ ] Fail |
| Hourly create/update | With staging authority explicitly enabled, allow the hourly cycle. One eligible new Live product creates once; linked Live differences update existing IDs. Capture task, product receipt, readback and Discord. No Build Preview/Reconcile required. | [ ] Pass [ ] Fail |
| Remote drift, daily | Change one supported field in staging Shopify only; allow daily reconciliation. It reads, records Update Available and queues the valid local revision without scanning/writing Shopify. Next publisher corrects it, verifies and becomes Up to Date. Next reconciliation matches. | [ ] Pass [ ] Fail |
| Remote drift, manual | Repeat through existing manual reconciliation. Same queue/read-only guarantees and same retained identity. | [ ] Pass [ ] Fail |
| Missing/stale/recovery | Reconcile copies with no checkpoint, stale source, missing remote identity, incomplete readback and uncertain mutation. None gains write authority or a duplicate Create. Capture exact blockers. | [ ] Pass [ ] Fail |
| 14 variants / 53 images | Run the existing staging gallery/variant case; verify original order, alt text, identities, 20-image/30-MiB batches, 10-MiB individual bound and no repeated acknowledgements. | [ ] Pass [ ] Fail |
| Disconnect/restart/workers | Close the monitoring browser; use another session. Interrupt staging backend between acknowledged write and verification. Original operation/IDs persist; readback resumes without repeated mutations. Verify two/four-worker single ownership. | [ ] Pass [ ] Fail |
| History and no spam | Find empty/blocked publisher cycles and daily/manual reconciliation in Event History. Follow run details, filters and pagination on desktop/mobile. Historical missing facts remain unavailable. No skip/Draft/empty-cycle Discord spam. | [ ] Pass [ ] Fail |
| Existing lifecycle | Execute onboarding, append, targeted editor/shared saves, manual full scan, Draft/Live, sale/release, manual approval, customisation/Production Time, Orders and packages checks in the existing acceptance plan. | [ ] Pass [ ] Fail |
| Rollback | Pause staging publishing, stop staging container, restore the paired backup if data rollback is needed, pin previous immutable image and preserve identical mounts. Recheck authority before resuming. Never clear recovery to roll back. | [ ] Pass [ ] Fail |

No schema migration is added; a verified remote write cannot be undone by rolling
back an image/database. Real Shopify staging acceptance remains separate from
the isolated mocked and network-disabled Docker evidence.

## Recorded offline validation

* Main focused publisher/scheduler/saved-status/history suites: **192 passed**.
* Checkpoint integration coverage: **25 passing cases**, including both variable
  layouts with 14 variants/53 images, real scheduler dispatch, daily/manual drift
  correction, unchanged source bytes, bad sources and missing/unsafe authority.
  The final run also passed **31 existing manual draft/readback checks** (55
  together), including the verified-identity button and unchanged uncertain
  recovery dispatch. A further four-check focused run verified per-task retention
  and the manual button/recovery branches; those overlapping checks are not added
  again to the unique total.
* Existing scanner/transaction, gallery-byte, variable recovery/readback and
  browser-independent manual-worker safety suites: **152 passed**.
* Adjacent onboarding/marker/scanner, Production Time, Orders and package suites:
  **110 passed**, one pre-existing assertion failure.
* Deduplicating overlapping focused/integration tests gives **491 passing Python
  cases and one baseline failure**, not the sum of repeated runs.
* Focused existing JavaScript suites: **73 passed**. Isolated desktop (1440×1000)
  and mobile (390×844) browser checks passed for tasks, cycle/detail history,
  reconciliation filtering, queue pagination, validation guidance and no
  horizontal overflow. No live browser tabs or production pages were used.
* Network-disabled Docker checks passed with two and four workers, migration head
  `0021_manual_review_worker`, persistent `/app/instance`, original operation and
  identity, separate same-SKU order answers, package IDs, and 14 variants/53 images
  verified after restart without repeating Create, uploads or variant writes.
  A separate legacy validation check retained Pending source authority, original
  scan/validation receipts and identical source bytes across container restart.
* Syntax compilation and `git diff --check` passed.

Baseline failure: `test_shopify_orders.py::test_nonempty_orders_downgrade_requires_approval`
expects older refusal wording; migration 0021 correctly refuses with “Retained
manual approvals require a paired backup; automatic downgrade refused.” The
identical failure was reproduced from an untouched export of starting commit
`141748a464d7259078b05b8797a799b06a5b20fc`. Neither Orders nor its test was changed.
An older Docker script also expected migration head 0020; the current 0021-aware
restart scenarios were used instead. The first local candidate lacked the smoke
report's OCI revision label; labelled candidate runs passed. Neither preliminary
test image was published as a release.
