# Publishing finalisation correction — staging acceptance

Baseline: `74ef9c2dcd1880051a82ef977c2534ee1b6b4d4a`.
These corrections do not repair production receipts or authorise recovery.
Use an isolated staging store/catalogue for every write test below. Pause live
automatic publishing before deployment/acceptance; the application does not change
the merchant's persisted automation setting. Back up `/app/instance` and retain
the previous immutable image as a pair before upgrading. No migration is added.

## Corrected contracts

- An explicitly unconfigured native measurement is not a pending write. Configured
  blocked, pending and uncertain measurements still prevent complete success.
- A fresh, full, identity/configuration/source-bound Draft comparison may pause a
  linked Draft without entering mutation preflight. It does not renew any evidence,
  verify pending metadata, or bypass approval/verification for Draft→Live.
- Creation followed by activation is reported as Created. A package acknowledgement
  from an older operation is labelled historical, not a current package write.
- Verified managed-product changes can be shown within a partial/recovery event.
  Overall recovery, unverified package input and reviewed-but-unproved intent remain
  distinct. Current readback proofs are operation-bound.
- Unexpected exceptions retain bounded category, stage and a safe structural reason.
  Arbitrary exception payloads, SQL, signed URLs and customer answers are not copied.
  Historical errors are not backfilled, cleared or reclassified by this release.

## Acceptance checklist

| Check | Exact staging action | Expected result / evidence |
|---|---|---|
| [ ] Live create | Create a simple Live fixture with no authored packed dimensions; enable the existing native capability setting only in staging if needed. Publish through normal approval/automation. | One Draft creation, configured activation and complete readback; Succeeded / Created, verified queue. No native-dimension write for an unconfigured target. Capture operation ID and retained Shopify ID. |
| [ ] Configured dimensions | Use a separate fixture with explicitly authored packed dimensions and a deliberately unavailable staging capability. | Actual configured blocker remains visible; no false Succeeded or fabricated measurement verification. |
| [ ] Linked Draft | Use a linked Draft with pending metadata and a complete current Draft comparison; include a legacy incomplete identity receipt only in isolated fixtures. Run normal publisher in staging. | Paused; no status/metadata write, Create or new failure notice. Pending local metadata and original identity retained. |
| [ ] Draft→Live | For a fully supported verified staging Draft, change intent through the existing editor, finish its normal targeted scan and publish. | Existing identity activated; accumulated eligible metadata published and verified. Full preflight remains mandatory. A legacy/incomplete receipt is not upgraded by the Draft no-op. |
| [ ] Historical package acknowledgement | Assign a staging package through the existing approved workflow, then perform a separate metadata-only update. | First operation: Shopify Accepted, not independently package-verified. Later operation: verified product update; prior package proof labelled historical. No second assignment. |
| [ ] Late package uncertainty | Use the mocked failure regression, never induce a timeout on the live store. | Managed-product verified stages/changes visible; package pending/uncertain or not attempted accurately distinguished; overall Recovery Required; no replay after restart. |
| [ ] Large variable product | Publish an isolated 14-variant/53-image fixture. Close/reopen the browser and interrupt the disposable backend between acknowledged writes and readback. | Same operation/product/variant/media identities; no duplicate acknowledged writes; final verification completes without manual Reconcile. |
| [ ] History and mobile | Open the resulting Event History detail and Operations receipt on desktop and mobile. | Created/Updated correct, verified stages and deferred package stage readable, diagnostics bounded, no contradictory overall Verified heading on Recovery Required. Capture screenshots. |
| [ ] Restart/persistence | Restart only the disposable test container with the same `/app/instance` mount. | Queue, identities, package evidence, operations, customisations and Orders retained; no unrequested scans or automatic authority granted. |

Keep live acceptance read-only until these staging checks pass. No routine retry,
Reconcile or activation is needed for already verified ADOC-0001, 16bit-0001 or
LCBC-0002 solely to correct old notification wording.

## Real 16bit-0002 — separate authorisation required

Do not clear its Recovery Required state, edit its receipt, or replay assignments.
Existing package support writes `InventoryItem.measurement.shippingPackageId` and
retains exact variant/inventory/package acknowledgements before/after the call.
It has **no independent package-assignment readback** and no supported tool to
manufacture an acknowledgement from an ordinary product comparison.

The original operation retained managed-product verification but no package intent
or acknowledgement. The original exception cannot be recovered retroactively by
this code change. An authorised assessment should first inspect Shopify Admin for
variant `58770588369279`, InventoryItem `57207133438335`, product `16010570662271`
and selected ShippingPackageV2 `116237992319`, alongside the original operation
`5b0c73fae2464db4933cbcf43b967040`. Admin inspection is evidence for a merchant
decision, not automatic database settlement. If explicit completion is needed,
assess a narrowly reviewed existing-identity continuation separately. This release
does not implement a new package recovery mutation or weaken the missing-ack guard.

## Deployment and rollback

After staging approval: pause automation, stop the container, take an appdata backup,
select the verified immutable image (preserving existing mounts), start and inspect
the embedded revision, Products, queue and Events without triggering jobs. Do not
expect historical receipts to change. Resume automation only by merchant approval.
To roll back, stop first and restore the previous immutable image plus the matching
appdata backup if restoration is required; never reset identities or recovery rows.

## Recorded offline validation

- Final reporting/package run: **48 passed** (including 20 receipt-shaped new checks).
- Affected scan/checkpoint/package/Orders run: **127 passed**.
- Adjacent automatic publisher/operations/manual worker run: **92 passed**.
- Bounded publishing/native/variant/sale/release run: **276 passed, 2 failed**.
  Both failures reproduced on the untouched starting revision with the same assertions:
  `test_complete_drift_read_reopens_pending_work_but_not_draft_metadata` expects a
  Preview alone to reopen drift; `test_new_live_requirement_blocks_existing_verified_live_until_corrected`
  expects policy changes to select an already matched product. They are not fixed
  or dismissed as newly passing in this focused release. The actual full
  reconciliation integration is covered by the passing affected suite.
- JavaScript: **42 passed**. Python syntax and whitespace/diff checks passed.
- Desktop/mobile: five disconnect/reconnect boundaries, 53 uploads, one creation,
  same completed operation; separate partial-event checks show verified work,
  protected recovery and safe diagnostics without horizontal overflow.
- Existing offline two-worker Docker migration/restart fixture passed with 14
  variants and 53 images acknowledged before restart, same-operation completion,
  no duplicate uploads/Create/variant writes, and no manual Reconcile. Orders,
  packages, customisations, queue and receipt persistence checks passed.

Totals above are per test run and overlap; they are not a unique-test sum. The first
new restart test exposed a test-only detached ORM fixture reference; reloading the
fixture product after session replacement corrected it, and the final run passed.
Real Shopify staging acceptance is still required; all automated mutations used mocks.
