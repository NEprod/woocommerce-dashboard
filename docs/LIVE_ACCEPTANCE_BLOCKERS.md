# Focused live acceptance correction — 29 October 2026

Follow-up: the separately approved practical package-only correction is now
documented in [PACKAGE_CORRECTION.md](PACKAGE_CORRECTION.md). The earlier design
limitations below describe the previous release; historical receipts remain intact.

## Confirmed evidence; no live repair performed

Deployed `4c3e24505b76`, automatic publishing paused. New manual operation
`1009613f0f9a453d979999bb73e65d66` retained `write_attempted=false`, diagnostic
`BackgroundYield`, and the previous identity's `verified` stage. It stopped during
preflight, before reserving the identity or attempting a mutation. The generic
exception handler converted a scheduler deferral into a terminal uncertain-update
message. Its original receipt is retained, not retrospectively rewritten.

The real `16bit-0002` identity remains product `16010570662271`, variant
`58770588369279`, InventoryItem `57207133438335`. Original operation
`5b0c73fae2464db4933cbcf43b967040` still requires review. Managed product fields
were independently verified; package intent/acknowledgement was not retained.
Shopify Admin showed Store default — TLC Small Parcel Box, not the locally selected
TLC C5+ Board Back Envelope (`ShippingPackageV2/116237992319`). This proves a
current mismatch, not what happened during the historical uncertain attempt.

## Corrections and their boundaries

- `commerce_routes.py`: verified linked identities use the existing scoped durable
  current-product comparison task even when a legacy receipt lacks newer completion
  domains. Uncertain identities retain their recovery path. No publishing grant is
  issued by a read. Historical Draft settlement still requires fresh complete,
  source/configuration/identity-bound comparison and all existing safety predicates.
- `shopify_drafts.py`: propagate a preflight `BackgroundYield` to the existing
  worker only when no mutation was attempted and this operation has not reserved
  the identity. Yields after uncertain writes remain conservatively protected.
- `manual_publishing.py`: ordinary manual approval and execution cannot bypass an
  unresolved queue/original operation recovery state. Explicit signed variable
  creation completion keeps its existing separately guarded workflow. Safe worker
  deferral waits on the same accepted operation, not a new confirmation.
- `publishing_progress.py`, `shopify_progress.py`, `shopify-publish.js`: distinguish
  waiting workers, historical stopped-before-write yields, current verified product
  scope and outstanding package evidence. Never borrow earlier verified stages as
  proof of a new mutation. Existing historical failure statuses are not rewritten.
- `operation_detail.html`: legitimate identity-only/product-less operations render
  without building a URL from a missing product ID.
- `shopify_package_assignments.py`: definite userErrors retain bounded redacted
  Shopify reasons. No uploader, mapping, package definition or API changed.

No schema migration, scanner trigger, JSON format, schedule, Orders, storefront,
Production Time, app extension or automatic publishing eligibility change.

## Package acknowledgement contract

The existing implementation persists exact package/variant/InventoryItem/source
intent before its mutation. A conclusive `inventoryItemUpdate` acknowledgement
with matching InventoryItem and empty userErrors becomes **Shopify Accepted**,
`independently_verified=false`. It requires no unavailable package readback.
Matching accepted assignments are not repeated. Transport ambiguity or incomplete
acknowledgements remain uncertain, and independent product verification is retained
separately. Definite rejection is not acceptance. Product equality is not package
proof and cannot clear the original recovery operation.

## 16bit-0002: distinct correction requires separate bounded authorisation/work

There is no supported manual-attestation/package-only recovery action that can
conclusively finalise this historical missing-acknowledgement case. Ordinary Sync
must remain blocked. Do not edit SQLite, pretend an acknowledgement exists, retry
the old operation, create/unlink a product or repeat metadata/media writes.

Minimum follow-up design, not implemented by this release:

1. Merchant reviews the exact current Admin mismatch and explicitly authorises a
   **new** one-variant package correction, acknowledging the unknown old outcome.
2. Reuse the current operation ledger, same-product ownership, source/configuration,
   package-link and variant/InventoryItem guards; fresh signed approval binds all
   targets and references the original protected operation.
3. Persist new intent before a single assignment. Keep old receipts immutable.
   Exact acknowledgement means Accepted, never API Verified. Ambiguity protects
   both receipts and never automatically replays. No ProductCreate/media/metadata
   mutation is authorised by that action.
4. Only a separately designed evidence-backed resolution can supersede the old
   package warning; it must preserve its history and verified product metadata.

A manual Admin package edit alone does not produce an API acknowledgement in TLC.
It must not be used to silently clear the database recovery flag.

## Merchant acceptance after optional deployment

Back up `/app/instance`; retain the previous immutable image and paired backup.
Keep automation paused. Deploy manually only after reviewing the release report.
No automatic deployment is performed by this work.

- Authorise one LCBC-only read if needed: Product Details → Refresh Product, or
  the corrected verified-identity Reconcile route. No Preview/Confirm/Sync Now.
  Verify fresh comparison, same Shopify Draft identity, local pending metadata,
  eventual paused queue via normal view worker, and unchanged historical failure.
  Respect the existing ten-minute restart grace; do not force tasks or scans.
- Open the read-only operation detail and inspect the retained result. Identity-
  only receipts must no longer return HTTP 500.
- Inspect 16bit-0002 without writes. Its original recovery remains blocked; a new
  ordinary approval must explain that existing blocker before any new mutation.
- In an isolated staging store only, test a safe preflight capacity deferral and
  resumed signed update, definite package acceptance/rejection and lost response.
  Accepted must not request unsupported readback; uncertain must never replay.

Do not resume automation solely to clear these historical warnings.

## Isolated regression evidence

- 11 new real-evidence-shaped checks passed, including actual manual scheduler
  entry/resume, legacy identity route to comparison and view settlement, protected
  prior recovery at acceptance/execution, missing-product operation pages, package
  acknowledgement/rejection and operation-specific modal evidence.
- 58 manual worker/Draft-settlement/finalisation checks and 91 package, variable
  verification, automatic publisher and checkpoint integration checks passed.
  Total across these three non-overlapping groups: 160 Python checks.
- 21 publishing-modal/Operations JavaScript checks passed. Syntax compilation and
  whitespace/diff checks passed. No application-wide suite is claimed green.
- Existing offline Docker restart checks use disposable mounts and network-none;
  14 variants/53 images retain the same identity/operation without repeated Create,
  uploads or variant mutations. No migration is introduced; appdata stays in
  `/app/instance` and existing Orders/package records remain intact.
