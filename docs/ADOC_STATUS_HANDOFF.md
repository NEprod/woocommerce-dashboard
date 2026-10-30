# ADOC status acceptance — live findings and narrow correction

Investigated read-only across 29–30 October 2026 (UK/GMT). Starting revision:
`03d400c82bd7ad7106d3b124afe542b4f61c8654`.

## What actually happened

The deployed revision matched. Persistent mounts remained `/catalogue`, `/intake`,
`/output`, `/taxonomy`, `/app/instance`. Automatic publishing remained disabled
(policy revision 6). No live scan, reconciliation, source/database repair,
publication, package action, settings change or container restart was initiated.

The new ADOC shared JSON really contains an enabled, required `dogs_name` text
field, order 0, minimum length 2, maximum length 16, label “Dogs Name”, help
“Check spelling.” and placeholder “Enter dogs name here”. All four products
inherit the same definition; there is no field-identity collision or validation
failure. A network-blocked minimal read-only application confirmed the current
source fingerprints exactly match the stored checkpoints for all four products.

The affected-collection scan `6a4ae4f5d6a540929baad9313e1caed0` ran at
23:51:27–23:51:47 on 29 October: four products and 16 variations updated, zero
errors, zero warnings. It processed one collection, not the full catalogue.

The existing follow-up read-only comparison ran at 23:51:52–23:52:09, operation
`9b28387655e64f289cd8e3e316dd9053`. All four comparisons completed, without a
Shopify mutation, and retained the following current outcomes:

| Product | Current source/checkpoint fingerprint | Comparison | Identity / result |
| --- | --- | --- | --- |
| ADOC-0001 | `2778782172fb46bee85dcf5d77e15e4f14f044eb8d9919756d979b59d4a7519e` | 23:52:02 | Retained `gid://shopify/Product/16012545425791`; Update Available |
| ADOC-0002 | `aaf60814fba0c7d07adc240bf459c2f1ec1dd4ce95af1e2fa5f9ddfaf0f655a4` | 23:52:04 | No retained linked product; Needs Creating |
| ADOC-0003 | `737468a1d73140b86c421bf9cf5c16f39be1b7ef8079a287924db4524ab26389` | 23:52:06 | No retained linked product; Needs Creating |
| ADOC-0004 | `1850673285bf0448a832a20934e42c4f6fc3abe993e33e81d3fb668e3c0d534b` | 23:52:09 | No retained linked product; Needs Creating |

Each comparison was complete, with no current refresh failure; all four source
validations had no blockers. The queue was Pending, not Attention/Recovery.
ADOC-0001's comparison identified Product Type, Lead Time and customer configuration
differences. No write was performed to resolve those differences.

## Exact display failure

The container started at 23:48:47. Scheduled work is intentionally held for ten
minutes, while explicitly queued manual read work can run during grace.

The scan's saved status had reused the older failed-comparison message. The new
successful comparison invalidated that saved view, but did not rebuild it. Thus
current source/queue/comparison evidence agreed while the list retained
Verification Unavailable and `view_pending=1`.

The existing view worker eventually ran at 23:58:58–23:59:03, after grace. Without
any intervention, all four views became current (`view_pending=0`). Reloading the
real Product Details and Products pages then showed Update Available for ADOC-0001
and Needs Creating for ADOC-0002/3/4. A previously open page also retained its old
HTML until reload. No matching worker exception appeared in the bounded Docker
log slice inspected.

This was **not** lost customisation data, a failed scan, a missing checkpoint or a
continuing source-validation defect. It was a stale derived-status handoff, made
conspicuous by startup grace and old loaded page content.

## Smallest correction

1. During a normal scan, when the queue has successfully resolved the current
   local revision but a previous comparison is unavailable, label the derived
   view **Status refresh pending**. Keep the existing unavailable write gate;
   this does not grant publishing permission or invent remote evidence.
2. At the existing read-only refresh completion boundary, call the existing
   `catalogue_views.capture` for checked, non-pending products and commit under
   the existing owner guard/write fence. Do not wait for a second worker to
   copy the already-conclusive result. Deferred products remain deferred; actual
   new failures remain unavailable and recovery evidence is not cleared.

Only `app/catalogue_views.py` and `app/shopify_status.py` need production changes.
No scanner, publisher, scheduler cadence/grace, Shopify mutation or JSON schema
changes are involved. Historical operation records are not rewritten.

This is not synchronous publication on Save. The scan records local authority;
the existing read-only comparison establishes the current Shopify difference.
Each completed stage updates its saved status without the extra view-worker wait.
An already-open page still needs its existing refresh/reload to display new data.

## Why the previous tests missed it

The earlier lifecycle test began with valid fields and explicitly ran the separate
view worker after comparison. Its invalid-configuration test recovered by disabling
customisations, rather than saving a valid shared field across four existing variable
products with retained generic failures. Neither asserted that the read task itself
persisted the current list status while the view worker was still in startup grace.

`tests/test_adoc_status_handoff.py` now reproduces that exact sanitised sequence:
four existing variable parents (four variants each), one retained Shopify identity,
legacy generic errors, actual signed shared save, actual collection scan, valid
inherited `dogs_name`, exact checkpoint matching, a discarded database/application
session, and a new worker claiming the persisted read task during startup grace.
It fails on the original release (saved status stays unavailable) and passes after
the correction without running the view worker. Save makes zero Shopify calls;
the follow-up comparison makes no mutations, and automatic publication stays paused.
The worker restart here is a new owner/application session; separate offline Docker
smokes exercise real two-/four-worker container restarts and retained evidence.

One existing reconciliation test expected an uncreated local Draft to say Needs
Creating, then Not yet verified after an absence-cache deadline. The first assertion
also failed on the untouched starting revision. Its expectations now remain Local
Draft, consistent with the established intent contract; the API recheck and cache
assertions are retained. No Draft behaviour was changed in production code.

Focused/bounded release checks: 136 Python tests passed across the ADOC handoff,
edit/publish lifecycle, source checkpoints, metadata scan scope, Draft settlement,
read-only reconciliation, scheduler and saved-view suites. The relevant editor,
customisation and Products JavaScript checks passed (92 tests). Pytest also
reported 4,586 warnings; there were no remaining failures in these final selected
suites.

Both network-isolated Docker upgrade/restart smokes passed with persistent
`/app/instance`: two workers retained one accepted package assignment and its
original uncertain history without replay; four workers completed the same
14-variant/53-image operation after restart with no repeated Create, upload or
variant write and no manual Reconcile. Orders, package identities, customer
answers and dimensions survived. Schema remained `0021_manual_review_worker`;
this correction introduces no migration.

## Live proof and acceptance

The current live ADOC records already recovered naturally; no repair/rescan/replay
is needed. Do not publish another image merely to flush them.

For the corrected candidate, keep automation paused and preserve the current mounts:

- [ ] In an isolated fixture/staging catalogue, retain an older failed comparison.
- [ ] Save a valid shared `dogs_name` field using the normal editor.
- [ ] Observe the containing scan complete with the new revision and no Shopify call.
- [ ] While remote comparison is pending, display Status refresh pending, not the
      old source-unavailable diagnosis. Do not claim Up to Date or permit a write.
- [ ] When the existing queued read completes, reload Products: one linked update
      and three uncreated Live products must show their correct current statuses,
      with no dependence on the later view-worker cycle or expiry of startup grace.
- [ ] Reopen/restart workers: source revisions, identities and statuses persist;
      actual incomplete reads/recovery still block publication.
- [ ] Keep original failed operations in History. Do not rescan, reconcile, clear
      queues or write Shopify merely to prove that the saved view changed.

Production deployment and any real publishing remain separately merchant-controlled.
