# Operations and automatic publishing — practical merchant acceptance

Use this alongside `AUTOMATIC_PUBLISHING_ACCEPTANCE.md`. No item below has been
accepted against a real Shopify staging store by the automated test run.

**U = read-only acceptance on Unraid with automatic publishing disabled.** Do not
enable a toggle, run a scan or submit Sync Now as part of a U test.
**S = separate staging store and separate copied catalogue/appdata mounts, with
explicit merchant approval for staging writes.** Never repoint retained live IDs
at another store or use the live storefront as an experiment.

Before S tests: record the immutable image/digest, back up all actual mounts,
confirm `/app/instance`, check the intended staging domain and use a staging Discord
channel. Create Simple, Variable Collection and Single Variable fixtures. Include
53 original images, inherited template/collection customisations, a linked package
and two different personalisations of one SKU. Record original Shopify IDs.

Each test's checkbox is deliberately blank. Capture operation IDs and timestamps,
not credentials, customer answers or signed upload parameters.

## 01 — Dashboard and Products performance [U]

Preconditions: publishing paused, configured catalogue available.
Action: time Dashboard and Products twice, paginate/filter, reopen a product.
Local: accurate saved statuses/counts; pending health explicitly labelled, no scan
starts on browsing. Shopify: no page-render requests or mutations. Queue/events:
no new publishing execution from GET. Discord: no routine browsing message.
Evidence: timings, product/page counts, request diagnostics and screenshots.
Result: [ ] Pass [ ] Fail. Cleanup: none.

## 02 — Compact tasks and retained schedules [U]

Preconditions: record existing configured intervals before upgrade.
Action: open Scheduled Tasks overview; open each task's Details/settings and
History without pressing Run Now or Save.
Local: compact overview, last/duration/next/state/outcome, UK GMT/BST times. Sales
and releases are distinct. Retained intervals (including 60) stay unchanged;
recommended defaults are daily reconciliation, hourly publisher, five-minute
sales/releases. No scheduled local scan. Shopify: no GET request or mutation.
Queue/events: history preserved, viewing does not start work. Discord: none.
Evidence: before/after interval list and desktop/mobile screenshots.
Result: [ ] Pass [ ] Fail. Cleanup: none.

## 03 — Queue pagination and filters [U]

Preconditions: publishing paused; known pending/attention products available.
Action: filter by state/trigger and SKU; follow Next/Previous, Product and Receipt.
Local: exact filtered total, ten records per page, intended versus verified actual
state distinct, specific reasons and next eligible/retry timing. Shopify: no read
or mutation from queue rendering. Queue/events: no changes from viewing.
Discord: none. Evidence: total, returned SKUs, filters and links.
Result: [ ] Pass [ ] Fail. Cleanup: clear filters only.

## 04 — Event History and original receipts [U]

Preconditions: retained publishing/recovery history.
Action: open Event History from navigation; search/filter/page; open an event,
its original operation and product.
Local: UK times, product/SKU, trigger/type/result, verification and Discord state;
actual persisted changes visible. Old missing values explicitly unavailable.
Shopify: no request or mutation. Queue/events: historical records unchanged.
Discord: viewing never resends delivery. Evidence: one verified and one recovery
receipt, one older incomplete record, filtered totals and mobile view.
Result: [ ] Pass [ ] Fail. Cleanup: none; do not retry uncertain receipts.

## 05 — Bootstrap evidence and scanner contract [U guidance; S execution]

Preconditions: one pre-migration product without a publisher scan revision.
Action: U: open its collection-scan guidance and inspect actual collection path.
S: confirm that scoped update; separately test onboarding, configured restart,
append and Product Override/Shared Collection saves.
Local: no evidence invented from marker/date alone; existing collection update
processes its shared JSON and children only. Configured restart does not full scan;
append/editor scans retain established scope. Shopify: scan itself does not grant
write authority. Queue/events: real scan commits source revision; original history
remains. Discord: truthful existing scan outcome, no routine warning spam.
Evidence: source checksums, scan scope/receipt, stable SKUs and queue revision.
Result: [ ] Pass [ ] Fail. Cleanup: retain S fixture; never reset markers/indexes.

## 06 — Live-required fields [S]

Preconditions: optional staging policy configured; valid Draft and invalid Live.
Action: remove one selected requirement from the Live fixture, save/scan, dispatch;
correct it and save/scan again.
Local: Draft remains editable; only affected Live product blocked with exact field.
Shopify: no invalid publication or forced unpublish; corrected eligible state may
publish after approval of staging authority. Queue/events: blocked then verified,
original failure retained. Discord: actionable failure then verified result, no
duplicate unchanged warning. Evidence: field, revisions, receipts and readback.
Result: [ ] Pass [ ] Fail. Cleanup: restore fixture and optional policy.

## 07 — New Draft [S]

Preconditions: new unique SKU, no remote identity.
Action: append-scan a Draft; allow due publisher or acknowledged Sync Now.
Local: Draft/paused, pending authored information retained. Shopify: no Create.
Queue/events: no verified creation receipt. Discord: no success message.
Evidence: SKU, zero remote mutation and queue state.
Result: [ ] Pass [ ] Fail. Cleanup: retain fixture for 08.

## 08 — New Live / hourly publishing [S]

Preconditions: 07 eligible, staging authority explicitly granted, startup grace over.
Action: change to Live and save/scan; wait for hourly due work without manual review.
Local: queued → bounded execution → verified. Shopify: exactly one new product,
correct managed fields and intended availability after readback. Queue/events:
verified creation and durable original IDs. Discord: one detailed verified success.
Evidence: task/run/product/variant/inventory IDs, changes and readback.
Result: [ ] Pass [ ] Fail. Cleanup: retain fixture for transitions.

## 09 — Changed and unchanged Live [S]

Preconditions: 08 verified.
Action: edit description/price, save/scan and publish; run again unchanged.
Local: changed-only work then up to date. Shopify: supported changed fields only;
second run makes no unnecessary write. Queue/events: one verified update, no
invented second update. Discord: one detailed update, quiet unchanged run.
Evidence: actual before/after and mutation counts.
Result: [ ] Pass [ ] Fail. Cleanup: restore authored values normally.

## 10 — Live → Draft [S]

Preconditions: linked Live, record all IDs and metadata.
Action: save Draft intent and allow priority publishing.
Local: priority status transition, accumulated metadata retained. Shopify: existing
product becomes Draft, not deleted/recreated; no unrelated metadata change.
Queue/events: status-only verified scope. Discord: clear verified Draft transition.
Evidence: same IDs, status readback, unchanged metadata.
Result: [ ] Pass [ ] Fail. Cleanup: keep Draft for 11.

## 11 — Local edits while linked Draft [S]

Preconditions: 10 complete.
Action: edit description, price, customisation/collection; save/scan; allow hourly run.
Local: changes retained, Draft metadata paused/update needed. Shopify: metadata
unchanged, same Draft identity. Queue/events: no false verified metadata update.
Discord: no publication success for paused data.
Evidence: JSON, effective projection, remote before/after and pending revision.
Result: [ ] Pass [ ] Fail. Cleanup: keep for 12.

## 12 — Draft → Live [S]

Preconditions: 11 accumulated edits, IDs recorded.
Action: set Live, save/scan and allow publisher.
Local: accumulated supported changes published and verified. Shopify: same product,
variants and inventory items reactivate. Queue/events: real verified changes and
availability, not creation. Discord: one useful verified update/transition.
Evidence: identity comparison, remote fields, event and delivery receipt.
Result: [ ] Pass [ ] Fail. Cleanup: restore Draft if fixture no longer needed.

## 13 — Immediate Sync Now with automation paused [S]

Preconditions: scanned eligible change; global automatic authority paused.
Action: acknowledge Product → Sync Now for one fixture.
Local: exact revision/scope queued promptly; unrelated products untouched.
Shopify: supported change after safety checks/readback only. Queue/events: manual
trigger and verified original operation. Discord: one scoped verified result.
Evidence: queue grant/revision, task scope and receipt.
Result: [ ] Pass [ ] Fail. Cleanup: leave global automation paused.

## 14 — Signed manual Review & Sync [S]

Preconditions: supported scanned change.
Action: inspect readable review and approve; separately change source after preview
and verify stale approval is rejected.
Local: normal signed approval unchanged; unsaved/stale data blocked. Shopify: only
fresh approved changes. Large gallery continuation requires fresh reviewed approval
or acknowledged Sync Now, not replaying a terminal confirmation request.
Queue/events: actual scope/readback retained. Discord: configured publishing policy,
no false success on stale review. Evidence: approval/result and rejected stale case.
Result: [ ] Pass [ ] Fail. Cleanup: discard stale token; do not share it.

## 15 — Daily reconciliation and request profile [U read-only Run Now only with merchant approval; otherwise S]

Preconditions: catalogue/remote state known; record selected interval.
Action: deliberately run reconciliation; open its run profile and compare product
conclusions with Shopify. Repeat unchanged when appropriate.
Local: completed inspection may retain product findings; counts/current incidents
remain distinct. Shopify: read-only, complete required evidence, no false freshness.
Queue/events: one durable task history; reference reuse/request/cost/latency/retry
totals visible; old unmeasured runs say unavailable. Discord: actionable-only reads.
Evidence: profile JSON, real cost observations, timings and affected findings.
Result: [ ] Pass [ ] Fail. Cleanup: preserve merchant interval; no blanket reset.

## 16 — Scheduled release [S]

Preconditions: eligible Draft with a near-future UK release time, confirmed DST offset.
Action: save/scan; wait for the release task after grace; inspect independent history.
Local: due-only authoritative local save and containing-collection scan, then queue.
Shopify: same/new appropriate identity publishes only through authorised publisher.
Queue/events: release task separate from sales; publication verification separate
from local release save. Discord: verified release once, never before readback.
Evidence: due time, local JSON owner, scan/run/publish receipts and IDs.
Result: [ ] Pass [ ] Fail. Cleanup: cancel future fixture release through editor.

## 17 — Sale start and expiry [S]

Preconditions: linked Live with valid future sale start/end and normal price.
Action: save/scan and observe five-minute sale task at both boundaries.
Local: existing price calculations, focused affected products only.
Shopify: correct sale then restored effective price; no unrelated product changes.
Queue/events: distinct start/end triggers with actual prices and verification.
Discord: one verified start and one expiry/revert.
Evidence: UK/UTC boundaries, before/after price, compare-at and receipts.
Result: [ ] Pass [ ] Fail. Cleanup: restore authored schedule through supported flow.

## 18 — Discord failure and delivery recovery [S]

Preconditions: staging notification destination only.
Action: cause one known delivery failure, restore destination and inspect retry;
separately use an isolated uncertain-delivery fixture, not live webhook experiments.
Local: verified publication remains verified. Shopify: no mutation replay merely
to resend Discord. Queue/events: known failure retries boundedly, uncertain delivery
retained without automatic resend. Discord: no duplicate accepted messages.
Evidence: operation/delivery states, attempts, actual staging message IDs.
Result: [ ] Pass [ ] Fail. Cleanup: restore staging destination; keep uncertainty.

## 19 — Images, including 53 [S]

Preconditions: copied supported originals within 10 MiB/file; new 53-image SKU,
including a gallery exceeding 30 MiB total. Upload slices remain limited to 20
files and 30 MiB; Shopify API resolution/aspect-ratio checks apply per image.
Action: create through publisher; observe 20-image upload slices, processing and
readback; interrupt between acknowledged batches and resume. Test unchanged and
reordered gallery separately. Attempt add/remove on an existing gallery in review.
Local: all 53 retained, correct alt/order, acknowledged uploads not resent; uncertain
uploads require review. Shopify: one product, 53 unique media identities, no duplicate
uploads/creation after continuation. Existing add/remove/replace remains explicitly
unsupported; no destructive rebuild. Queue/events: progress is not success; actual
creation only after full verification. Discord: no success per upload batch.
Evidence: all media IDs/order/alt, uploaded count, task/receipts and readback.
Result: [ ] Pass [ ] Fail. Cleanup: archive staging fixture through reviewed tools.

## 20 — Variants [S]

Preconditions: both variable layouts with recorded SKUs/options/IDs.
Action: update a supported price/dimension/option field; test retained remote-only
variant protection and a projection outside TLC's 32-variant capability in review.
Local: identities/modifiers unchanged; exact application limitation shown.
Shopify: existing variants retained; no destructive list replacement. Shopify's
2,048-platform maximum is not advertised as TLC publisher support.
Queue/events: verified supported changes or specific blocker. Discord: actual
verified result/actionable failure only.
Evidence: variant/inventory maps, review and readback.
Result: [ ] Pass [ ] Fail. Cleanup: restore supported fixture projection.

## 21 — Throttling, failed reads and uncertain writes [S]

Preconditions: mocked throttle/failure support or explicitly controlled staging proxy;
never exhaust live API capacity deliberately.
Action: inject a required read failure/throttle, then an ambiguous Create response.
Local: truthful defer/failure, bounded backoff; no false freshness checkpoint.
Shopify: reads retry only under supported policy; uncertain mutation never blindly
replayed or auto-created again. Queue/events: original evidence/Recovery Required
retained. Discord: useful action-required message, not routine warning spam.
Evidence: budgets, cost/throttle profile, attempts and uncertain receipt.
Result: [ ] Pass [ ] Fail. Cleanup: restore transport; resolve only with safe evidence.

## 22 — Multiple workers and edits during publication [S]

Preconditions: isolated staging mounts; test two, three and four web workers.
Action: queue edits rapidly, run conflicting publisher/reconciliation tasks, edit
again while a supported publication is executing.
Local: one durable owner, revisions coalesce; newer edit survives old completion.
Shopify: no duplicate products or overlapping unsafe mutation. Queue/events:
original IDs/receipts retained; latest desired work remains pending until verified.
Discord: one result per actual verified operation.
Evidence: claims/leases, requested/running/verified revisions, IDs and messages.
Result: [ ] Pass [ ] Fail. Cleanup: return staging to approved worker settings.

## 23 — Restart, persistence and pause/resume [S]

Preconditions: recorded jobs, schedules, orders, package IDs and events; backup made.
Action: pause; restart between acknowledged batches, then after a mocked ambiguous
write; inspect ten-minute grace and deliberately resume staging authority.
Local: `/app/instance` persistence, no full local startup scan, pending revisions
retained. Shopify: known acknowledgements resume safely; ambiguous writes do not
replay. Queue/events: IDs, receipts and Discord state survive. Discord: no duplicate
delivery from restart. Evidence: before/after hashes, IDs, schema and task times.
Result: [ ] Pass [ ] Fail. Cleanup: leave staging publishing paused.

## 24 — Backup and rollback [S rehearsal; U backup only with merchant approval]

Preconditions: known previous immutable `automatic-publishing-4d2348e6256e` image and
paired pre-0020 appdata/catalogue/taxonomy/output backup.
Action: stop only staging; preserve current state; restore paired backup and old
image into separate staging mounts. Never restore over live data experimentally.
Local: original IDs/queue/events/data intact. Shopify: local rollback does not undo
remote changes; reconcile before any further write. Queue/events: no fabricated
success or erased recovery. Discord: no automatic resend. Downgrade is refused;
application-only rollback across migration 0020 is unsupported.
Evidence: backup checksums, old/new digest, migration and data counts.
Result: [ ] Pass [ ] Fail. Cleanup: retain both backups; leave automation paused.

## 25 — Existing customer experience and business data [S]

Preconditions: template + collection + product-added fields, Production Time,
two distinct same-SKU customer personalisations, linked packaging, existing Orders.
Action: review/publish supported metadata, exercise staging product/cart/checkout,
import the two staging orders and reopen their lines/packages.
Local: canonical inheritance, required flags/order, finished/packed dimensions,
package IDs and separate personalisations unchanged. Shopify: existing customer
field block/Production Time behaviour and signed safeguards preserved; no app/theme
deployment. Queue/events: truthful actual changes and separate delivery state.
Discord: no customer answers or credentials. Evidence: field/line IDs, redacted
screenshots, property names and package mapping.
Result: [ ] Pass [ ] Fail. Cleanup: archive only staging fixtures; keep live untouched.

## Activation gate

Send results and unresolved limitations to the merchant. Real staging costs,
latencies, 53-image processing behaviour and rate limits require actual acceptance;
mock timings are not network guarantees. **Live activation, Unraid deployment and
main promotion each require their own explicit approval.**
