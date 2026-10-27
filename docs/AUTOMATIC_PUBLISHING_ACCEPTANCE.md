# Automatic Shopify publishing — merchant staging acceptance

For the operations-acceptance follow-up, also complete
[OPERATIONS_ACCEPTANCE_CHECKLIST.md](OPERATIONS_ACCEPTANCE_CHECKLIST.md). It adds
the compact operations views, measured reconciliation profiles, scoped bootstrap
guidance, bounded large-gallery continuation and 0019→0020 upgrade/rollback checks.

This is a development candidate, **not permission to enable publishing on the
live store**. Automated results use fictional Shopify responses and disposable
catalogues. Every real-store test below remains unchecked until performed on an
explicitly approved, isolated staging store. Do not promote main on mock evidence.

## Safe staging preparation

1. Record the immutable image, digest, revision and migration head from the
   release report. Keep the current immutable image available.
2. Stop only the staging container while backing up its **entire `/app/instance`
   mount**, copied catalogue (including JSON, SKU indexes and markers), output and
   taxonomy mounts. Keep a paired pre-upgrade backup. Do not move to `/config`.
3. Use separate staging mounts and credentials. Do not give a staging container
   access to the live appdata or live catalogue. For a new staging installation,
   use fresh appdata with copied authoritative catalogue JSON/SKU indexes; do not
   relabel retained live Shopify identities as staging identities.
4. Start the candidate. Automatic publishing is off by default. Check Settings →
   Commerce identifies the intended **staging** store. Never paste credentials
   into a report. Test Discord against a designated staging channel.
5. Prepare fixtures: one Simple product, a Variable Collection child, a Single
   Variable product, inherited customisation/template fields, two distinct
   personalisations of the same SKU, a linked package, and one invalid product.
   Keep each fixture's original JSON, SKUs and expected variant identities.
6. Only after explicit staging approval: Settings → Automatic Shopify Publishing,
   enter the exact staging myshopify.com domain and enable. This grants separate
   durable authority; a page visit or ordinary task toggle does not grant it.
7. Use Settings → Scheduled Tasks for intervals/history; Automatic Publishing for
   queue/events; each event links to the original operation and notification
   receipt. Record timestamps, IDs and actual readback in the evidence fields.

Common expectations: no customer answers or credentials in notifications/logs;
no silent identity replacement; no false freshness checkpoint; uncertain writes
stay Recovery Required. “Verified” always means the existing required readback
completed. A saved-package acceptance is not independent package readback.

## Thirty real staging tests

Each result must include local JSON/database, dashboard, Shopify, receipt and
Discord observations. “No mutation/event” is a meaningful expected result.

### H01 — First onboarding

Precondition: fresh staging appdata, copied catalogue, publishing off.
Actions: complete normal setup and initial catalogue discovery; inspect Products.
Expected: existing onboarding scan discovers fixtures once; stable copied SKUs;
saved projections and local source health; no Shopify creation or success alert.
Evidence: scan ID, product/SKU counts, absent remote writes.
Result: [ ] Pass [ ] Fail. Cleanup: retain this staging catalogue for H02.

### H02 — Configured restart and grace

Precondition: H01 complete; record IDs, queue, receipts and source-file hashes.
Actions: restart staging; inspect before ten minutes, then after ten minutes.
Expected: no local full scan/onboarding; no automatic timers during grace; saved
state survives. Due work resumes after grace, not once for every missed interval.
Evidence: source hashes, IDs, task history, startup/due times.
Result: [ ] Pass [ ] Fail. Cleanup: no marker/index reset.

### H03 — Append scan

Actions: add one copied fixture folder; run the existing New Catalogue/append scan.
Expected: one new local identity, existing IDs/SKUs unchanged, new revision queued
atomically with ingestion; no unrelated full scan. Draft creates nothing remote.
Evidence: scan scope, old/new IDs, queue row and source JSON.
Result: [ ] Pass [ ] Fail. Cleanup: keep fixture or archive through supported UI.

### H04 — Product Override edit

Actions: change one child description; Save Metadata; reopen.
Expected: only sparse child JSON changes; containing-collection update scan;
parent JSON unchanged; current revision pending, not falsely Up to Date. If Live
and authorised, next hourly run updates that product and verifies the change.
Evidence: child/parent diff, scan scope, operation and before/after readback.
Result: [ ] Pass [ ] Fail. Cleanup: restore the authored description normally.

### H05 — Shared Collection inheritance

Actions: edit one shared informational term, then a storefront collection; save.
Expected: collection-scoped scan; inherited effective fields queued on affected
children, not copied to their JSON. Shopify review/publishing preserves remote
identities, unmanaged memberships and Product Type's independent single selection.
Evidence: parent/child JSON, affected queue revisions and membership readback.
Result: [ ] Pass [ ] Fail. Cleanup: restore the original shared assignments.

### H06 — New Draft

Actions: save/append-scan a new Draft with incomplete optional metadata; run due
publisher or acknowledged Sync Now.
Expected: local Draft/paused; no productCreate, no remote assignment, no verified
creation event or Discord success. Local editing remains available.
Evidence: JSON, queue state and zero mutation receipts.
Result: [ ] Pass [ ] Fail. Cleanup: keep for H07.

### H07 — New Live / hourly creation

Actions: make H06 eligible and Live; select desired supported channels; save/scan;
allow the hourly publisher to run (do not use Review & Sync approval).
Expected: exactly one Create, complete required readback, retained Product/Variant/
InventoryItem IDs, verified event and one detailed creation message. An unchanged
later hourly run sends no mutation or duplicate message.
Evidence: hourly run ID, queue revision, remote IDs, receipt and Discord reference.
Result: [ ] Pass [ ] Fail. Cleanup: keep linked fixture for H08–H11.

### H08 — Changed-only Live update

Actions: change only its title/description; save/scan; run hourly publisher.
Expected: changed-field mutation only; no create, image upload or unrelated
variant rewrite; verified before/after event and one update message.
Evidence: source diff, exact mutation fields, readback and same remote IDs.
Result: [ ] Pass [ ] Fail. Cleanup: restore the title through normal save/scan.

### H09 — Live → Draft

Actions: change linked fixture to Draft and also edit its description; save/scan.
Expected: priority status-only mutation to the same Shopify product; description
not sent. Channel selections remain authored for reactivation. Event identifies
Draft/status-only scope, not renewed full metadata equality.
Evidence: unchanged remote description, Draft readback and notification.
Result: [ ] Pass [ ] Fail. Cleanup: retain Draft for H10.

### H10 — Edit while linked Draft

Actions: change description, price and customisation additions while Draft;
save/scan; allow an hourly run.
Expected: accumulated local changes remain pending/paused; no metadata writes,
no new identity and no claimed update-success event. Draft remains editable.
Evidence: sparse JSON, requested vs verified revision, unchanged remote values.
Result: [ ] Pass [ ] Fail. Cleanup: retain changes for H11.

### H11 — Draft → Live

Actions: restore Live and intended channels; save/scan; run publisher.
Expected: accumulated H10 changes publish and verify; original Product, Variant
and InventoryItem IDs reused; activation/update event and one detailed message.
Evidence: original/current IDs, field readbacks, receipt and notification.
Result: [ ] Pass [ ] Fail. Cleanup: retain linked fixture.

### H12 — Scheduled release

Actions: keep a fixture Draft; set Optional scheduled Live release in UK time
after grace; save/scan. Keep an unrelated changed Live product pending before its
hourly due time. Observe before/after the five-minute due check.
Expected: no early release; only due fixture's JSON owner receives Live/ACTIVE,
using backup + existing confined collection scan; priority scope publishes only
affected due products. Shared JSON stays unchanged for a child release. Hourly
schedule is not brought forward for unrelated work. Verified release receipt/message.
Evidence: due row, local backup/JSON, scoped scan, priority run and Shopify readback.
Result: [ ] Pass [ ] Fail. Cleanup: clear future schedules; never rewrite receipts.

### H13 — Sale activation and revert

Actions: configure future start/end and sale price; save/scan; observe both due
boundaries, then edit/cancel a future schedule and repeat. Include a shared sale.
Expected: existing pricing/modifiers and date-only semantics retained; due-only
index; correct price/compare-at start and revert, distinct verified sale events;
no stale cancelled event, unrelated scan or early unrelated product publication.
Evidence: UK/UTC times, due revisions, exact prices, receipts and messages.
Result: [ ] Pass [ ] Fail. Cleanup: restore original prices and clear future dates.

### H14 — Coalescing and edits during publication

Actions: save five edits before the next hour; then save another revision while
one approved staging operation is processing.
Expected: one desired queue row per product; latest pre-run value published;
in-flight completion verifies only its own revision, leaving newer work pending.
No lost edit, duplicate creation or stale green status.
Evidence: requested/running/verified revisions, generation and operation IDs.
Result: [ ] Pass [ ] Fail. Cleanup: restore original source via normal save.

### H15 — Manual Review & Sync

Actions: pause automatic mode; build signed review; make a further local edit and
try the old approval; build a fresh review and explicitly approve.
Expected: stale approval rejected; readable changed/already-matching fields;
fresh signed approval retains readback/recovery safeguards and shared locking.
Evidence: rejected approval and completed original receipt; no forged auto token.
Result: [ ] Pass [ ] Fail. Cleanup: discard old browser review; keep receipts.

### H16 — Manual Sync Now

Actions: with automatic mode off, save/scan one eligible Live fixture; acknowledge
Sync Now on its detail page. Repeat once while pending. Try an unscanned source edit.
Expected: bounded selected-product queue, immediate manual run even during startup
grace, no duplicate work. Unscanned source is blocked with a collection-update
instruction; no blind adoption of raw files as write authority.
Evidence: manual grant/scope, operation, remote readback and rejected request.
Result: [ ] Pass [ ] Fail. Cleanup: scan outstanding source edits normally.

### H17 — Merchant-configured Live requirement

Actions: choose one optional TLC Live-required field in publishing settings; remove
it on a fixture, including an already-published Live fixture. Save/scan; then fix it.
Expected: incomplete Draft remains saveable; Live receives precise attention,
not deletion/unpublishing. Inherited valid values satisfy policy. Correction and
successful revalidation resolve the active preflight issue; history is retained.
Evidence: selected policy, field cause, unchanged remote state then verified fix.
Result: [ ] Pass [ ] Fail. Cleanup: restore the agreed requirement policy.

### H18 — Image add/remove/reorder

Actions: copy/add/reorder images, scan the containing collection; inspect review
before execution. Also test an over-bound complete gallery.
Expected: source revisions detect changes; no silent truncation/removal. Supported
alt/reorder changes use existing paths. Unsupported existing-product media add/
replace/remove remains explicitly blocked; no invented capability or success.
Evidence: image lists, exact blocker or changed operations, no lost originals.
Result: [ ] Pass [ ] Fail. Cleanup: restore fixture images and rescan.

### H19 — Variant update and package safety

Actions: change one size-specific price/dimension/customisation definition where
supported; preserve unrelated options. Choose a linked package, then an unlinked one.
Expected: stable variant/inventory identities; changed-only supported fields;
finished vs authored packed dimensions remain separate; package Shopify acceptance
is labelled accepted, never independently read back. Unlinked package stays local.
Evidence: option/ID map, fields, package acknowledgement and any exact blockers.
Result: [ ] Pass [ ] Fail. Cleanup: restore fixture values/selection.

### H20 — API throttling

Precondition: use a sanctioned staging proxy/mock; do not stress the real API.
Actions: return a throttle/cost-shortage before a write; advance to saved retry time.
Expected: durable bounded backoff; interactive capacity reserve; no failed-read
freshness renewal or replay storm; only required work resumes.
Evidence: attempt counts, next_attempt, request counts and task state.
Result: [ ] Pass [ ] Fail. Cleanup: remove fault injection.

### H21 — Timeout during creation

Precondition: sanctioned mock/proxy only.
Actions: simulate connection loss after productCreate may have been accepted.
Expected: Recovery Required, retained before-write nonce/identity evidence; no
automatic Create retry or duplicate after another edit/restart/refresh. Use only
the existing safe recovery review, not a reset/clear command.
Evidence: uncertain original receipt, no subsequent create, recovery decision.
Result: [ ] Pass [ ] Fail. Cleanup: resolve the exact fixture receipt explicitly.

### H22 — Docker interruption during publishing

Actions: on staging only, stop during a bounded fixture operation; restart with
the same `/app/instance`. Repeat after verified terminal receipt but before queue
completion using approved fault injection.
Expected: no stale owner completion; ambiguous write protected; exact completed
receipt reused without replay; provably unattempted work remains pending.
Evidence: leases, original receipts, revisions, product count and notification count.
Result: [ ] Pass [ ] Fail. Cleanup: wait grace; resolve only affected recovery cases.

### H23 — Multiple workers

Actions: run staging with two web workers, then three/four; submit duplicate Sync
Now and due publisher/read tasks. Do not run a second production installation.
Expected: shared durable task/operation ownership; no simultaneous conflicting
publisher/reconciliation work, duplicate product or duplicate Discord delivery.
Evidence: owners/leases, run scopes, request/creation counts and one event per result.
Result: [ ] Pass [ ] Fail. Cleanup: restore original worker configuration.

### H24 — Daily reconciliation

Actions: inspect the 1440-minute default independently of the 60-minute publisher;
perform an explicit staging read check; introduce one controlled remote discrepancy.
Expected: bounded readback, resumable progress, no JSON rewrite or automatic local
scan. Conclusive drift reopens scanned Live work for its next publisher; mere
discovery does not push immediately. Uncertain mutations remain protected.
Evidence: schedules, read scope, comparison checkpoint and reopened queue revision.
Result: [ ] Pass [ ] Fail. Cleanup: correct controlled drift through the intended flow.

### H25 — Discord unavailable/recovery

Actions: use a staging webhook that rejects a send; later restore it. Separately
simulate ambiguous acceptance with a mock.
Expected: verified Shopify result survives delivery failure; known failure retries
with bounded durable backoff; sent messages do not duplicate; ambiguous sends
require explicit acknowledgement before retry. Full safe details stay in receipts.
Evidence: delivery claim/state/attempts and message operation references.
Result: [ ] Pass [ ] Fail. Cleanup: restore the webhook; review uncertain sends.

### H26 — Queue pause/resume

Actions: queue Live edits and due events, pause Automatic Publishing; wait beyond
their due time. Resume using the exact staging domain confirmation.
Expected: no automatic product writes during pause; queue/due events retained;
resume catches up safely without replaying every missed interval. Manual signed
Review and acknowledged Sync Now remain separately authorised.
Evidence: policy epoch, queue, due states, remote timestamps and catch-up receipts.
Result: [ ] Pass [ ] Fail. Cleanup: leave automatic publishing paused after staging.

### H27 — Navigation under load

Actions: while a bounded staging publish/read batch runs, open Dashboard/Products,
filter, paginate and inspect Needs Attention at desktop and iPhone width.
Expected: saved statuses, no per-navigation full image/source walk, Shopify call,
local scan or write; accurate counter/list agreement; pending/unknown evidence is
explicit, not falsely Up to Date. Compare response measurements with report.
Evidence: timings, request/SQL counts, screenshots and absent scan/mutation records.
Result: [ ] Pass [ ] Fail. Cleanup: none; do not clear caches/markers as a workaround.

### H28 — Customer fields and Production Time

Actions: review/publish inherited template + collection + product-added fields;
open a staging product, submit required answers and add to cart twice with different
answers. Inspect cart/checkout/order including Production time snapshot.
Expected: existing field IDs, ordering and validation; no inherited-field mutation;
human-readable review; Production Time remains independent. No app-extension,
theme, scopes or app-version deployment is part of this candidate.
Evidence: definitions/readback, required-field behaviour and two distinct line items.
Result: [ ] Pass [ ] Fail. Cleanup: cancel only the designated staging order.

### H29 — Orders and integration regression

Actions: import/refresh that staging order with the existing workflow; check both
same-SKU personalisations, proof policy, finished dimensions and package receipts.
Expected: separate order lines/answers; Production Time snapshots unchanged; no
publishing task mutates Orders, fulfilment, rates, inventory or store-default parcel.
Evidence: imported line IDs, personalisation rendering and retained package evidence.
Result: [ ] Pass [ ] Fail. Cleanup: use existing staging order lifecycle only.

### H30 — Backup and rollback rehearsal

Actions: pause staging publishing; stop staging; preserve current appdata/catalogue;
restore the **paired pre-0019 appdata/catalogue backup** with its previous immutable
image into separate staging mounts, then start and inspect IDs/orders/packages.
Expected: original schema/data/identities restored; no automatic scan or publishing.
Rolling back only the app image is NOT supported across migration 0019; downgrade
is deliberately refused. Rollback does not undo Shopify changes, so reconcile
remote state safely before any subsequent writes. Never restore over live data.
Evidence: backup checksums, image/digest/revision, migration head and preserved IDs.
Result: [ ] Pass [ ] Fail. Cleanup: keep both tested backup sets; leave staging paused.

## Live activation gate

Provide completed results and unresolved risks to the merchant. Confirm the exact
live store and backup/rollback plan. **Live automatic activation and main promotion
each require separate explicit approval.** Do not infer approval from deploying a
development image or from successful mocked tests.
