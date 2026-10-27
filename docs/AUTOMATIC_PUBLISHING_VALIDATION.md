# Automatic Shopify publishing — development validation

Date: 27 October 2026. Starting commit: `c779d0290aa9b7526a8bf79a87fc0c0c7e77e0f6`.
This is offline/mock development evidence, **not staging or live Shopify acceptance**.
Automatic publishing remains disabled by default. No live store, theme, extension,
Unraid deployment, main branch or stable/latest image was changed.

## Resumption and defects corrected

The interrupted tree already contained the authority, queue, automatic executor,
schedules, due-event index, saved views, UI and tests. That work was retained.
The read-only operations probe's saved-thumbnail route registration was present
and is covered by a query-only SQLite regression and the Docker probe.

Final validation additionally established and corrected:

* Accepted asynchronous media processing was not safely completing automatically.
  Strict known-processing receipts now resume the existing read-only finaliser,
  then perform a fresh authorised plan. They do not replay Create, uploads or
  variant creation. Genuine uncertainty still requires recovery. Simple and both
  variable-layout fixtures retain all original identities.
* Retry/readback work now remains in the same durable scheduled run. Counts use
  unique targets and retain a separate attempt count, rather than reporting a
  new creation per poll or implying completion while readback is outstanding.
* A newer scanned edit survives an older completion, known failure or retry;
  uncertainty cannot be bypassed by editing. Priority work has a bounded scope
  and does not consume the unrelated hourly deadline.
* Comparison invalidation no longer holds a SQLite write fence while renewing
  a lease through another connection. Real task/fence tests exercise this path.
* Saved views retain the existing indexed title when source JSON is unavailable,
  rather than inventing a folder-derived title. Mounted sources still use the
  existing canonical title resolver. No product JSON is changed by rendering.

## Automatic-publishing acceptance matrix

| Behaviour | Offline/mock result | Real staging |
|---|---|---|
| New Draft | Local, paused, no Create | H06 pending |
| New Live | Create, required readback, availability, verified event | H07 pending |
| Changed Live | Only supported changed fields; unchanged revision does not write | H08 pending |
| Live → Draft | Status-only transition; retained metadata and identity | H09 pending |
| Draft metadata edits | Retained locally, no metadata mutation | H10 pending |
| Draft → Live | Accumulated changes; same product/variant/inventory IDs | H11 pending |
| Media processing | Read-only continuation, bounded polling, one verified completion | H07/H22 pending |
| Coalescing/newer edits | One desired job/product; newer revision not lost | H14 pending |
| Review & Sync / Sync Now | Original signed approval retained; separate explicit immediate grant | H15/H16 pending |
| Hourly / daily / five-minute work | Distinct durable schedules; ten-minute startup grace | H02/H07/H12/H13/H24 pending |
| Sale/release | Only indexed affected products; existing price/source/scan paths | H12/H13 pending |
| Ownership | Simultaneous claim, commit fences, transport authority per write | H23 pending |
| Restart/uncertain write | Exact verified receipt reused; ambiguous Create never replayed | H21/H22 pending |
| Requirements | Configured Live prerequisites block only affected product | H17 pending |
| Discord/events | Verified actual changes persisted; known failed delivery retries; ambiguous delivery not resent | H25 pending |
| Reconciliation | Complete remote drift can reopen scanned desired work; not ambiguous writes | H24 pending |
| Scanner | Existing onboarding/append/targeted paths; no periodic folder scan or GET scan | H01–H05 pending |
| Saved catalogue views | No product-source/image walk, remote call or DB write during tested GETs | H27 pending |

The full merchant checklist has exact actions, expected results, evidence and
Pass/Fail fields for thirty staging scenarios in
[AUTOMATIC_PUBLISHING_ACCEPTANCE.md](AUTOMATIC_PUBLISHING_ACCEPTANCE.md).
No staging store was available. Every real-store checkbox remains unchecked.

## Automated results

* Earlier bounded affected suite: **257 passed**.
* Final publisher/safety/due/view/scheduler group: **118 passed**.
* After the final title-fallback correction: **159 passed**, including Dashboard,
  canonical titles, saved views, publisher, safety, due events and scheduler.
  These groups overlap; their counts must not be added as distinct tests.
* JavaScript: **310 passed**, including ten isolated existing Tinker browser tests.
  The initial restricted Chrome launch failed; it was rerun successfully using
  the installed Playwright runtime and permitted isolated browser process. No
  dependency installation or extension/theme modification was needed.
* Desktop 1440×1000 and mobile 390×844 Chrome: publishing controls, default-off,
  rejected missing domain confirmation, acknowledged Sync Now, release editor,
  no horizontal overflow or browser errors. No user tabs were used. WebKit/iPhone
  Safari and real storefront checkout still need merchant staging acceptance.
* Python compilation and `git diff --check`: passed.

### Full-suite reconciliation (not an all-green full suite)

Untouched starting commit, archived separately: **2,611 passed, 43 failed,
1 skipped**. Candidate full run: **2,689 passed, 32 failed, 1 skipped**.
Comparison by exact test identity found three new title-fallback failures and one
unrelated template assertion. The title defect was corrected in application code;
all three now pass. Rerunning the exact 32 failures gives **3 passed, 29 failed**:

* **28 failures also reproduce at the untouched starting commit**. They include
  old Woo/branding/routes, old title/severity/scan-scope wording, old migration-head
  and downgrade assumptions, pre-existing query budgets, old taxonomy clear/replace
  assumptions, and the existing media-recovery/Live-intent assertions. They are
  retained, not silently marked passed or fixed by weakening working behaviour.
* **1 failure is caused by the merchant's pre-existing uncommitted Unraid template**
  omitting optional client configuration expected by
  `test_docker_examples_expose_optional_shopify_configuration`. The archived
  committed template passes. All three unrelated Unraid/template files and their
  checksums were preserved and excluded from this release.

The starting commit's 15 other failures no longer fail after affected fixture
updates/current approved contracts or bounded-query improvements. This does not
mean all historical assertions were repaired. The JUnit files preserve individual
failures/messages for audit. No second full-suite rerun is implied after the small
title correction; the affected 159 tests and exact residual rerun cover it.

## Docker, persistence and rollback

`tests/docker_automatic_publishing_smoke.py IMAGE WORKERS` uses disposable mounts
and `--network none`. Two, three and four workers were exercised before and after
restart, upgrading the fixture from `0018_orders_webhooks` to
`0019_automatic_publishing`. It verifies the dedicated scheduler and grace effect,
non-root workers, retained database backup, query-only operations probe, local
pages/authentication, six stable package IDs/GIDs, Orders with two distinct
same-SKU personalisations, customisation/Production Time data, review/recovery
receipts, queue/due/event records and publishing log persistence. No publishing
authority is granted by this test. Final committed-image identity and registry
publication evidence are supplied in the release report.

Migration 0019 adds authority, queue, due/event and derived-view storage. Appdata
stays at `/app/instance`. The default reconciliation interval changes from 20 to
1440 minutes only where it still equals 20; other custom intervals are retained.
Publisher defaults to disabled/60 minutes; existing sale checks remain five.

Before upgrading any installation, stop its container and back up the **entire
actual appdata mount plus the matching catalogue, taxonomy, output and old
immutable image identity**. Do not delete appdata or replace it with a blank DB.
Downgrade is deliberately refused. Rollback across 0019 requires that paired
pre-upgrade backup and old image, tested on separate mounts first. Application-only
rollback is unsupported and no local restore reverses Shopify mutations.

## Saved-view performance

Reused the prior navigation audit's 100/500/1,000-product fixtures (including
840 history records and all three layouts), with complete source projections
prepared before measuring GETs. Values are local instrumented warm medians in
milliseconds, not production latency guarantees or Shopify network throughput.
Tests/builds were also active; in particular the 500-product sample had higher
host contention. Prior baseline measurements are retained, not reconstructed.

| Products | Dashboard baseline → candidate | Products data baseline → candidate |
|---:|---:|---:|
| 100 | 352.11 → 54.32 | 276.71 → 19.04 |
| 500 | 1,528.80 → 208.44 | 505.94 → 52.51 |
| 1,000 | 3,010.71 → 134.01 | 823.04 → 22.47 |

Dashboard SQL falls from 418/1,670/3,236 to **56** at each size; Products data
from 328/330/334 to **23**. Each measured request loads one consistent registry
snapshot, inspects **zero product image folders**, and performs **zero database
writes**. Baseline image-folder inspections were 100/500/1,000 per request.
Candidate peak process RSS was 144.33/140.19/139.61 MiB respectively, including
fixture preparation; this is not a per-request allocation measurement.

The mocked real-task transport/fence creation check consumed 30 simulated API
requests in approximately 0.217 seconds. It proves budgeting/lease coverage,
not real Shopify publishing throughput. Rate limiting and real-store volume
remain H20/H27 staging tests.

## Known boundaries / next gate

Existing managed-gallery bound 20, request-size bounds, unsupported existing-image
replacement and destructive variant changes remain actionable blockers, not silent
partial publication. Packed fields still require authored data/capability, and
package mutation acceptance is not independent readback. Pre-upgrade products
without a provable scanned queue revision require the ordinary containing-collection
update scan; enabling does not adopt arbitrary source changes as write authority.

Use separate staging mounts and store, complete H01–H30, review limitations and
backup/rollback evidence, then seek explicit live activation approval. Do not
enable automatic publishing on the live store or promote main on this mock evidence.
