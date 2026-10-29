# Explicit saved-package correction — 29 October 2026

## Confirmed real evidence (read-only; no repair performed)

16bit-0001 retained an explicit successful `inventoryItemUpdate` acknowledgement
from operation `5956d07659c440f5a21307405abb57b9`, 19 October 2026 07:12:19 UTC.
It assigned `ShippingPackageV2/116237992319` to its known InventoryItem. Later
publication sees that exact accepted target and does not repeat it. This path is
unchanged: API2026-10 writes `measurement.shippingPackageId`, not a product
metafield, weight or package-definition change.

16bit-0002 retained Product `16010570662271`, Variant `58770588369279`,
InventoryItem `57207133438335`. Its managed product update was verified, but
operation `5b0c73fae2464db4933cbcf43b967040` has no package intent or acknowledgement.
The original exception cannot be reconstructed. This is **unknown outcome / missing
evidence**, not proof that Shopify accepted or rejected a package mutation.
The later manual attempt `1009613f0f9a453d979999bb73e65d66` recorded
`write_attempted=false` (already corrected worker-deferral reporting).

The merchant's read-only Admin inspection showed Store default — TLC Small Parcel
Box, rather than the authoritative TLC C5+ Board Back Envelope. That establishes
a current mismatch, not the outcome of the old attempt.

## Correction contract

Product Details → **Correct saved package** → **Check identity and review package
— reads only** → inspect the exact variant in Shopify Admin → check the explicit
fresh approval → **Approve package correction once**.

The first check reads Shopify identity, variant/InventoryItem, SKU and correlation.
It does not read a package assignment (the supported API cannot expose it).
The signed approval binds the current local plan/source, credentials/store,
mapping revision, retained identity, exact variant/InventoryItem, package and old
operation evidence. Changed source/mapping/identity/ownership invalidates approval.
Current managed-product verification and source checkpoint are required; other
uncertain domains cannot be cleared by this action.

The existing manual worker and global task/operation lease own execution, not the
browser. A new operation records durable intent before **one** call to the existing
package mutation. It never creates a product, changes a variant definition,
uploads artwork, publishes metadata or changes availability.

- Exact success response: **Shopify Accepted**, never API Verified. The new receipt
  links the original protected operation; active recovery can then be superseded.
- Explicit rejection: retain bounded Shopify errors and both operation receipts.
- Lost response, interruption or ambiguity: keep Recovery Required; never replay
  automatically. Another attempt requires another deliberately approved review.
- Restart after a committed acknowledgement: finish locally from that exact
  proof; do not call the package mutation again.
- Unchanged already-acknowledged assignment: no new assignment is offered.

Original operations are not edited, deleted or reclassified. Current issue counts
derive supersession only from a successful, exactly bound new acknowledgement.
Metadata comparison timestamps and scan checkpoints are not renewed. Normal
comparison/publication eligibility is reopened, not falsely marked Up to Date.

This correction is deliberately limited to one retained Simple product/variant,
which covers 16bit-0002. Existing Variable package publishing remains unchanged.
Ordinary publishing still cannot bypass unresolved recovery. Splitting a fully
approved ordinary operation into independent metadata-only work during package
uncertainty is not introduced; successful correction restores ordinary updates.

## Production-file changes

- `shopify_package_assignments.py`: reuse the same single-assignment submission;
  signed correction, exact acknowledgement finalisation and derived supersession.
- `commerce_routes.py`, new package-correction template, product-state include:
  admin/CSRF-protected read-only review and explicit fresh approval using the
  existing publishing modal; GET never contacts Shopify.
- `manual_publishing.py`, `shopify_drafts.py`: dispatch only the signed package
  scope through the existing durable worker; resume acknowledgements, never writes.
- `operation_reporting.py`: exclude conclusively superseded package incidents from
  current attention, while preserving original history.
- `shopify_notifications.py`, `publishing_events.py`, operation-detail template:
  package-only Accepted outcome, actual acknowledgement, original receipt link;
  no new product verification or historical changes claimed.

No migration, scanner trigger, JSON authoring, Orders, package definitions,
Draft/Live, schedule, theme, customer-fields extension or Production Time change.

## Merchant acceptance and rollback

1. Keep automatic publishing paused. Back up `/app/instance` and retain the prior
   immutable image `publishing-acceptance-8826bd8c1816`. Preserve every volume mapping.
2. Deploy the new image manually; no local scan or live repair is initiated by this
   development work. Respect normal startup grace for the background worker.
3. Open 16bit-0002 → Correct saved package. Verify C5+ envelope, mapped package
   `gid://shopify/ShippingPackageV2/116237992319`, product/variant/inventory IDs above.
4. Review reads only. If any identity/source/other-domain blocker appears, stop;
   do not edit JSON merely to get past the guard.
5. Only when ready to authorise the actual write: inspect that exact Admin variant,
   check the fresh approval and approve once. Expected mutation: one package input
   on InventoryItem `57207133438335`; no other product writes.
6. Wait for Shopify Accepted. Inspect Operations/Event History and the immutable
   original receipt link. Confirm the selected package in Admin without editing.
7. After acknowledgement, an ordinary fresh reviewed update should no longer be
   blocked by the old package incident. Draft metadata remains paused under normal
   automation rules. Verify unrelated products remain unchanged before considering
   automatic publishing resume.
8. If rejected/uncertain, stop and retain both receipts. Do not press ordinary Sync
   repeatedly. A new correction is not a rollback and needs fresh explicit approval.

No automatic rollback mutation exists. Do not roll back an executing correction.
For an image rollback, retain current receipts/appdata first and use the prior
immutable image with a compatible paired backup only after assessing any new
remote acknowledgement. Never restore an older database to erase write evidence.

Real Shopify acceptance remains merchant-authorised and outstanding. This release
does not execute the real 16bit-0002 correction or resume automation.

## Validation scope and known baseline failures

The focused package/manual-worker/finalisation/live-blocker/Operations suite has
105 checks. Bounded automatic publishing, variable verification, gallery-byte,
saved-view and publishing-safety checks add 107 distinct checks. In that bounded
group 105 pass and two fail identically on the untouched `8826bd8c1816` baseline:

- `test_complete_drift_read_reopens_pending_work_but_not_draft_metadata`: the
  preview-only drift path leaves a previously verified job verified rather than
  reopening pending. This correction does not change reconciliation/queue policy.
- `test_new_live_requirement_blocks_existing_verified_live_until_corrected`: an
  already-verified product remains skipped after a newly required field is set.
  Required-field policy re-evaluation is not changed by this package action.

These are recorded limitations, not a claim that the entire application suite is
green. No new failing regression is accepted. The existing 21 publishing-modal /
Operations JavaScript checks and isolated 1440px desktop / 390px mobile preview
check are also run. Docker tests use `--network none`, disposable mounts and two /
four workers: upgrade/restart persistence, exact package acknowledgement without
replay, retained original receipts, 14 variants / 53 images, Orders and packages.
No live Shopify mutation or Unraid deployment is part of validation.
