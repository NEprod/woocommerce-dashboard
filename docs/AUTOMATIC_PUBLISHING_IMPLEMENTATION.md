# Automatic Shopify publishing — implementation record

Starting develop: `c779d0290aa9b7526a8bf79a87fc0c0c7e77e0f6`.
Starting migration head: `0018_orders_webhooks`. Existing appdata remains
`/app/instance`; no move to `/config`. The unrelated Unraid/template changes
remain excluded. No live Shopify writes or automatic activation are authorised.

## Phase 0: existing architecture

```
Onboarding / append / manual scan / editor collection-scoped save
  -> existing scanner -> atomic complete-parent ingestion -> SQLite catalogue
  -> queue_projected -> scheduled bounded Shopify read comparison

Manual signed Review & Sync
  -> current local plan + remote comparison + signed evidence
  -> catalogue operation reservation -> changed-field executor
  -> durable pre-write identity receipt -> mandatory readback -> history/Discord

app.runtime -> Gunicorn (default 1 worker / 4 threads)
            -> one app.scheduler_worker -> durable ScheduledTask leases
```

The scanner retirement module already prevents normal configured startup scans.
Initial onboarding, append and explicitly requested scans remain authoritative.
Both Product Override and Shared Collection saves already resolve a confined
containing collection path and dispatch the same `shared_collection` scan.

`shopify_products.source_fingerprint` and `shopify_comparison.fingerprint`
already cover effective inherited metadata, variants, taxonomy, customisations,
publication and image statistics. Preview additionally hashes image bytes.
Identity and comparison evidence is durable, but ordinary catalogue views still
reconstruct source documents to decide whether retained evidence is current.

The manual Simple/Variable executors already reserve durable operation slots,
revalidate plans/remote digests, write pre-mutation receipts and independently
read back. Collection membership and attribute ownership safeguards remain in
those executors. Existing-product media replacement/add/remove is explicitly
unsupported; automation must not silently bypass that blocker.

The scheduler has durable per-task ownership/fences and bounded read slices.
Defaults currently reconcile every 20 minutes (configurable to 60), not daily.
There is no ten-minute boot grace. Background mutations are currently forbidden
except separately authorised price transitions. Sales already have durable
per-product authorisation and due rows; that price engine must be reused.

Discord events/claims are durable in operation scope, with ambiguous delivery
protected from blind retry. Current successful routine messages are suppressed;
verified automatic publishing needs an explicitly distinguishable event policy.

## Requirement/gap matrix

| Area | Finding | Action |
|---|---|---|
| Onboarding, append, targeted/full scans | Already implemented correctly | Preserve existing discovery/identity/scope tests |
| JSON/inheritance, templates, modifiers, Production Time | Already implemented correctly | Reuse canonical effective resolver; no extension changes |
| Local managed fingerprint | Implemented; expensive during GET | Reuse in scan/worker projections, not a new authority |
| Persisted current status and scan-derived image health | Missing complete fast read projection | Supporting work; not a prerequisite for first publisher tests |
| Manual Review & Sync and signed/readback safeguards | Already implemented correctly | Preserve signature gate; share only lower-level execution |
| Changed-only Simple/Variable metadata | Implemented | Reuse exact managed diff and replacement safeguards |
| Existing-product media replacement/add/remove | Unsupported current capability | Retain actionable blocker unless a tested safe path is added |
| Automatic authorisation | Missing | Explicit durable, store/config-bound opt-in; default off |
| Durable pending queue/coalescing | Missing general publisher queue | SQLite jobs, revision/generation fencing, existing task/operation leases |
| New Draft / linked Draft pause | Manual behaviour differs from automatic contract | Explicit automatic eligibility; retain accumulated revision |
| Create/Update/Draft transitions | Existing executors, no automatic dispatcher | Reuse executor with separate automatic authority, never forge manual signature |
| Manual immediate Sync Now | Missing queued immediate action | Same queue, explicit admin authority and CSRF protection |
| Hourly publishing / daily reconciliation | Behaviour differs | Separate durable task schedules and bounded batches |
| Startup grace / catch-up | Grace missing; leases/catch-up exist | Ten-minute runtime grace, no missed-interval replay storm |
| Scheduled sales | Existing authorised focused price engine | Preserve; index automatic due work without full catalogue loops |
| Scheduled releases | Missing | Revision-bound due index, existing local-save/targeted resolution path |
| TLC Live requirements | Policy missing | Empty merchant-configured policy, no invented required list |
| Publishing event details | Existing durable receipts, incomplete automatic presentation | Actual before/after fields and verified per-product events |
| Notifications retry | Existing delivery claims | Preserve ambiguous-delivery protection; add bounded safe retries |
| Operations/task UI | Existing compact views | Extend, not a separate framework |
| Pause/resume | Missing automatic control | Retain work, do not stop editing/scanning/manual review |
| API limits | Current publisher capability bounds exist | Do not assume higher API maxima imply supported operations |
| Persistence/upgrade | Existing SQLite/migration backups | Forward additive migration; preserve receipts and mount paths |
| Navigation performance | Request-scoped registry reuse already shipped | Preserve; benchmark persisted views separately |
| Worker concurrency | Existing durable task + catalogue reservation | Reuse; keep default web worker count unchanged |
| Staging / live activation | No staging access established | Mocks/offline first; explicit merchant staging/live approval required |

## Delivery order and acceptance gate

Automatic publishing is the primary deliverable. First implement and test durable
authority/queue and **Create, Update, Live→Draft, Draft pause, Draft→Live,
coalescing, Sync Now, readback/recovery and notifications**. Extend scheduler
defaults and due events around that dispatcher. Persisted fast catalogue views
support the feature; do not substitute a navigation-only release.

Each internal stage needs focused evidence. No final completion claim or main
promotion is appropriate until the automatic-publishing matrix passes. The full
97-item regression inventory and separate 30-scenario human acceptance plan from
the merchant brief remain the final coordinated acceptance criteria.

## Implemented contract

`automatic_publishing` is disabled on migration. Enabling requires an administrator,
exact store-domain confirmation and a successful read-only connection/permission
check. Authority is bound to the store, configuration digest, administrator and
policy epoch. Every real transport mutation rechecks that grant and its owner.
Manual Sync Now has an explicit CSRF-protected, scanned-revision-bound grant; it
works with automatic mode off. Manual Review & Sync still validates its original
signature, acknowledgement, source/remote digests and readback contract.

Complete-parent ingestion atomically upserts one desired publishing revision per
product, without a new scan trigger. Repeated edits coalesce. A running revision is
separate from requested/verified revisions. Late success, known failure or retry
cannot discard a newer scanned edit. Genuine uncertainty remains protected across
new edits. Initial pre-upgrade rows without a provable queue scan revision stay
explicitly unscanned: use the normal containing-collection update, not a forced
startup/full scan or blind adoption of changed source files.

| Authored state | Automatic behaviour |
|---|---|
| New Draft | Local only, paused, no productCreate |
| Eligible new Live | Existing duplicate checks and Create executor, then required readback and authored availability |
| Unchanged verified Live | No full fetch or mutation solely because an hourly timer ran |
| Changed Live | Existing changed-only planner/executor; no implicit gallery/variant deletion |
| Linked Live → Draft | Priority status-only operation; metadata and channel selections retained |
| Linked Draft edits | Pending local revision retained; metadata publishing paused |
| Draft → Live | Fresh comparison and accumulated supported changes; same product/variant/inventory identities |
| Accepted media still processing | Bounded read-only finaliser; no Create/upload replay; fresh authorised plan after readiness |
| Ambiguous mutation | Recovery Required; no automatic replay or clearance |

Hourly publisher, daily reconciliation and five-minute due-event checks reuse
ScheduledTask leases and the catalogue-operation reservation. A transactional
cross-task claim prevents publisher/reconciliation/sales/order-worker overlap.
The supervisor supplies one ten-minute startup deadline to all children; explicit
manual work is separate. No configured startup full local or forced Shopify scan
is added. Missed intervals coalesce rather than replaying every missed timer.

Priority release/sale/Draft work has a bounded product scope and does not advance
the unrelated hourly schedule. A genuine hourly batch already due may include
the priority products. Existing signed sale authorisations retain their ownership
and price engine. New due rows index only known schedule timestamps/revisions.
Release writes the authoritative JSON owner through existing signed-local-edit,
mandatory backup, atomic replace and containing-collection scan mechanisms. It
does not turn the publisher into a periodic local scanner.

Publishing batches with blocked or uncertain requested targets are partial;
ordinary Draft pauses are not failures. Read-only refreshes retain their separate
completed-with-findings semantics. No incomplete product evidence renews freshness.
Known automatic media-processing receipts are presented as awaiting readback,
not as ambiguous writes; original receipt fields remain untouched. Old protected
recovery cases lacking that exact new proof are not reclassified.

## Supporting saved views

Dashboard, Products and issue drill-down use saved source health/effective display
documents and states. They do not reconstruct product folders or call Shopify.
One registry snapshot and bounded mapping/configuration reads guard saved evidence.
Registry/mapping changes invalidate the rebuildable view, not JSON. Unknown
upgrade health remains explicitly incomplete until bounded background projection.
Titles and time-dependent sale display reuse the same canonical effective data;
prices are recalculated against the current clock, not frozen at capture time.
Fresh complete comparisons are not obscured by a merely pending queue entry.

This data is a derived view, never write authority. Source JSON remains the only
authored source; source/digest checks run again before publication. Direct source
changes still require the established scan paths for new automatic write authority.

## Events and delivery

Terminal product events retain operation identity, product/revision, actual safe
before/after fields and original delivery receipt. Customer configuration changes
reuse the readable field comparison, not revision hashes. Known processing is a
distinct event, not a successful publication. A resumed creation links the prior
processing operation and notifies only after complete verification.

Existing per-operation delivery CAS is authoritative. Known delivery failures have
bounded backoff; uncertain/sending acceptance is never automatically resent.
Discord failure cannot invalidate a verified Shopify publication. Routine reads
remain quiet. Detailed manual-publish events use the configured publishing-event
opt-in; acknowledged Sync Now/automatic operations carry their own context.

Technical identifier-only logs live at `/app/instance/logs/publishing.log`, with
512 KiB rotation and three backups, protected file permissions and cross-process
rotation locking. Full recovery/identity evidence stays in SQLite, not in rotated
logs. Customer answers, payloads, credentials and raw configuration are not logged.

## Capability and upgrade boundaries

The original supported complete-gallery bound (20) and 30 MiB operation bound are
not raised speculatively. Existing-product image add/replace/remove and destructive
variant restructuring remain explicit blockers. No image/variant is truncated.
Shopify acceptance of a linked package is not independent package readback.
Native packed dimensions require independently authored packed measurements and
the existing capability gate; finished dimensions never become a silent fallback.

Migration 0019 adds only authority/queue/due/event/view storage and tasks. Existing
20-minute reconciliation configurations move to 1440 minutes; other custom
intervals are preserved. New publisher is 60 minutes and disabled. Sales remain
five minutes. Existing DB, IDs, Orders, package records, receipts and mounts remain.
Downgrade deliberately refuses: use the **paired pre-0019 appdata/catalogue backup
and old immutable image**, never an application-only rollback. A local rollback
does not reverse Shopify mutations. See `AUTOMATIC_PUBLISHING_ACCEPTANCE.md`.

## Automated inventory mapping

These are the original brief's test IDs, not an assertion of real-store acceptance.
Exact totals, baseline failures and final image evidence belong in the validation
report. Existing tests are reused rather than duplicating working infrastructure.

| Brief IDs | Coverage / evidence |
|---|---|
| 1–10 scanner | setup/onboarding, override-scan, metadata editor, scanner/ingest and source fixtures; queue upsert in the complete-parent transaction |
| 11–21 fingerprints/status | `test_automatic_publishing`, `test_catalogue_saved_views`, title/sale/source-image, reporting and navigation suites; no-GET-source/remote-call tests |
| 22–32 Draft/Live | Simple and both variable-layout automatic create/update/reactivation tests; uncertain Create protection; media-processing continuation |
| 33–42 scheduler | scheduled-task, startup and publishing-safety suites; exact grace, scoped/manual work, simultaneous claim and commit fencing |
| 43–52 due events | `test_publishing_due`: real confined release scan for all three layouts, cancellation, sale boundaries, stale guards and UK/DST validation |
| 53–66 publishing | existing Simple/Variable/native/optional-package/readback suites plus authority-per-write, mock transport budget, backoff and newer-revision/coalescing tests |
| 67–73 manual | signed Draft/update/review/progress suites, stale-token/digest guards, CSRF/admin tests and scoped Sync Now while automatic mode is off |
| 74–80 readiness | opt-in requirements and inherited effective source; existing Live receives attention, not unpublishing; supported API limits remain genuine blockers |
| 81–90 events/storage | publishing-safety, Discord, readable customisation diff, identifier-only rotation, uncertain-delivery and offline restart/upgrade fixtures |
| 91–97 performance | reused 100/500/1000-product benchmark fixtures, no-GET-walk checks, real task transport counters, desktop/mobile fixture browser and multi-worker Docker smoke |

No real staging Shopify acceptance or live automatic activation is claimed by this
mapping. The thirty merchant tests are deliberately still unchecked.
