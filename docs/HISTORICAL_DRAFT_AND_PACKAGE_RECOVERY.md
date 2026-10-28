# Historical Draft Attention and package recovery

## Confirmed real evidence (28 October 2026)

Unraid was inspected read-only at `cb09486dba63`; automatic publishing was disabled.
No reconciliation, scanner, mutation, database repair or deployment was performed.

### LCBC-0001

The queue retains failed-before-write operation
`32acfde61c024cde8c81a310b605d7e5`. The retained identity is
`gid://shopify/Product/16009365160319`; local intent and last remote observation
are Draft. Its old full comparison is not fresh enough to authorise settlement.

The demonstrated gap was integration: comparison observation and the view worker
did not consume this particular old Draft-preflight Attention entry. The existing
settlement path now supports it, with the same CAS/identity/ownership protection.

Settlement requires all of:

- The exact known publication-prerequisite error and original automatic preflight
  operation, failed, no recovery, with explicit before-write evidence in both scope
  and summary. A failed write is never inferred to be a preflight.
- One verified identity bound to this product/source/store, with no pending,
  uncertain or interrupted domain or accepted operation.
- A fresh full, complete, configuration/identity/mapping/source-bound comparison
  observing Shopify Draft, and current authored Draft intent.
- The comparison/source revision still matches the queued revision; no owner,
  concurrent accepted operation, source edit or genuine conflict supersedes it.

Only the queue becomes `paused`, with a bounded pause proof in its existing view
projection. Requested/verified revisions, scan checkpoints, identity evidence,
metadata differences and the original failed operation are not rewritten. It is
not marked Up to Date. The active incident projection can exclude that exact
resolved preflight, while Operations History preserves its error and Discord receipt.
The normal view scheduler handles clean projections too; unchanged ineligible
evidence is checked once, not continuously every five minutes.

## Live action requiring separate approval

After the merchant deploys the tested immutable image, keep automation paused and
take an appdata backup. Authorise a **targeted read-only Shopify Status Refresh**
for LCBC-0001 only, through the existing product/status reconciliation UI. The
verified-identity Reconcile route uses this read-only comparison task; do not use
Sync Now, Build Preview, recovery confirmation or any publishing approval.

Expected Shopify mutation: **none**. Expected local writes: the read-operation
receipt, fresh comparison, then a derived queue pause and catalogue projection.
Allow the existing Saved Catalogue Views worker (five-minute schedule) to run;
do not enable automatic publishing to force it. Confirm Draft/identity unchanged,
pending metadata retained and Needs Attention removed. If evidence is incomplete,
conflicting or source-stale, Attention must remain with no automatic retry.
This is not a scan and does not change source JSON or artwork. Historical receipts
remain present. If the result is unexpected, stop and inspect evidence; do not
manually edit SQLite or blindly repeat publishing.

## 16bit-0002 — still protected, not automatically recovered

Product `16010570662271`, variant `58770588369279`, InventoryItem
`57207133438335`, operation `5b0c73fae2464db4933cbcf43b967040`.
The selected local package is TLC C5+ Board Back Envelope
(`c476b503-94de-59b0-9db9-f345fe35059e`), mapped to
`gid://shopify/ShippingPackageV2/116237992319`. Read-only inspection confirmed the
store, target, reviewed link revision and InventoryItem binding agree. The link
was entered manually and is explicitly **not API-verified**.

Managed product readback matches, but no assignment intent or acknowledgement
exists in the retained package receipts. Missing evidence does **not** prove that
a call did not happen. The original exception cannot be reconstructed retroactively.

The existing implementation can verify product/variant/InventoryItem identity,
managed product fields and native measurements. It can retain an explicit
`inventoryItemUpdate` acknowledgement bound to the exact InventoryItem/package.
It cannot independently read the assigned package back. Shopify's 2026-10
[measurement input](https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/InventoryItemMeasurementInput)
accepts `shippingPackageId`, whereas the documented
[measurement output](https://shopify.dev/docs/api/admin-graphql/2026-10/objects/InventoryItemMeasurement)
does not expose that assignment. This inspection used public documentation, not
the merchant's Shopify API. Packed dimensions and product equality are not package proof.

Safest next step, separately authorised: inspect that exact variant's shipping
package in Shopify Admin, without saving. Capture the product/variant identity
and visible assignment, if Shopify exposes it. A matching package name alone or
the existence of a saved package is insufficient evidence of this assignment.

If no conclusive evidence is available, retain Recovery Required. The current
application has no supported manual-attestation action that turns an Admin
observation into a genuine API acknowledgement. Do not forge one or edit SQLite.
Any deliberate new one-variant assignment requires the merchant's explicit decision
about the earlier unknown outcome, a fresh signed source/identity-bound plan and
a separately reviewed safe continuation. It is not authorised by this release.
There is no ProductCreate, unlink, deletion, package-map edit or replay here.

## Acceptance and automation

- Confirm deployed revision and automation still paused.
- After separately approving the LCBC-only read, verify queue paused, metadata
  still pending, exact same Shopify Draft identity, original failed receipt intact.
- Inspect 16bit-0002 only; Recovery Required remains until adequate evidence and
  an explicitly approved supported resolution exist.
- Test Draft→Live only in an isolated staging store using the existing safeguards.
- Do not resume live automation merely to clear either warning. The publisher
  continues protecting Recovery Required and Draft metadata; it cannot safely
  resolve the missing package evidence by itself.

No migration, new queue service, scanner trigger or publishing mechanism is added.
Back up `/app/instance` and retain the previous immutable image before any manual
deployment. Roll back with the preserved image and paired appdata backup if needed;
do not reset identities, receipts or scanner state.

## Isolated validation

- 17 new focused cases: manual/scheduled reconciliation and real view-task claim,
  clean/stale projection scheduling, paused Draft persistence, protected ownership,
  source/identity conflicts, missing package acknowledgement protection, later
  Draft→Live using the same identity, and acknowledged package work surviving
  restart/readback without another assignment.
- 141 affected publishing/checkpoint/package/reporting/sale/release checks passed.
- 22 saved-view and targeted scanner-save checks passed.
- 20 Operations health/reporting checks passed. 31 relevant JavaScript checks passed.
- Total: 200 distinct Python checks across these runs. No live Shopify calls.
- The existing `test_complete_drift_read_reopens_pending_work_but_not_draft_metadata`
  and `test_new_live_requirement_blocks_existing_verified_live_until_corrected`
  failures were reproduced on untouched `cb09486dba63`. They concern Preview-only
  drift reopening and policy selection of already-matched products, not this
  historical Draft settlement. Neither was removed or changed to claim a green
  application-wide suite.

Only `automatic_publishing.py` (existing evidence-backed settlement),
`catalogue_views.py` (bounded worker eligibility/pause-proof preservation) and
`operation_reporting.py` (active incident projection) change application behaviour.
Tests and this decision/acceptance document are the other intended files.
