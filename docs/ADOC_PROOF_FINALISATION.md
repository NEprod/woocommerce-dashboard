# ADOC proof update: variable finalisation correction

## Observed, not inferred

On 30 October 2026, deployed revision `d7c30f439bac`, manual operation
`09393348f4e84ffab9e640aa6f50ba86` updated ADOC-0001's existing customer
configuration. Its Optional proof setting and required `dogs_name` field saved
correctly. Collection update `81a9ec93884b4bcaa28022305529f3dc` completed for
four parents / sixteen variations without errors. The approved, queued and
compared local revision agreed (`e9530eb5…`); another scan was not indicated.

At 19:00:08 a preflight capacity yield generated an incorrect Failed Before Write
event. The same signed operation continued, verified the configuration at
19:00:59 and recorded a complete matching comparison at 19:01:08. Subsequent
finalisation failed. The original non-Shopify exception was replaced by a generic
message and is **not recoverable from the retained evidence**. Its trigger must
not be claimed to be a specific timeout, Shopify rejection or ownership loss.

The outer variable handler then overwrote newer committed verification evidence
with an earlier decoded snapshot: the verification wait reverted to `base` and
product verification referred to an older operation. The configuration's own
acknowledgement/readback remained retained. Shopify product identity
`gid://shopify/Product/16012545425791` was not lost or recreated.

## Narrow correction

- `shopify_variables.confirm`: let pre-write scheduling yields reach the existing
  durable task runner without terminal failure/notification; restore a genuinely
  unwritten reservation where necessary. On other exceptions use the latest
  committed receipt, with operation-bound, redacted diagnostics.
- `shopify_variable_readback.complete`: preserve committed acknowledgements and
  verification for unknown exceptions, including worker continuations outside
  `confirm`. Unknown errors remain Recovery Required; acknowledged read-only
  verification deferrals use the existing pending continuation. A yield after a
  completed readback is not an uncertain mutation.
- No new queue, scheduler, migration, scanner trigger, Shopify capability, theme
  release, JSON schema or source-authority bypass.

## Validation

The regression uses existing variable fixtures, the full signed manual HTTP
approval and scheduler entry, not the configuration-only shortcut. Both Variable
Collection and Single Variable layouts cover normal proof update, preflight yield,
final-readback yield with DB-session restart, unknown readback exception and
temporary mismatching readback. It asserts one configuration write, same product,
variants/media, no unrelated writes, retained historical receipt, current-stage
diagnostics and continued recovery protection for unknown errors.

171 focused Python tests and 31 JavaScript checks passed. Existing network-isolated
two-worker package-correction and four-worker verification Docker smokes passed
across real container restarts with `/app/instance` persistence. The latter retains
14 variants / 53 images without repeating Create, uploads or variant writes.
Initial new-test failures were detached fixture references after the deliberate
session restart; those assertions were corrected. No selected regression remains
failing. No claim of real Shopify purchase acceptance is made.

## Approved deployment and product recovery boundary

The merchant subsequently authorised updating WooCommerceDashboard-dev and fixing
the affected product. Preserve mounts and appdata, take a paired appdata backup,
retain the prior immutable image, and keep automatic publishing paused.

After verifying the new revision, use ADOC-0001's existing Identity & History
read-only Reconcile action. Expected Shopify mutations: zero. It must verify the
existing identity, all four variants, three images, configuration and availability.
Do not retry publication, select packages, scan, edit JSON or clear SQLite rows.
If complete, the existing settlement mechanism may mark the original operation
resolved while preserving its prior outcome and notification receipts, and emit
its normal resolved notification. If any domain differs or cannot be verified,
retain protection and report the precise finding; do not force settlement.

ADOC-0002/0003/0004 were uncreated Live products with legitimate Needs Creating
states, not failed copies of ADOC-0001. Do not create those products as a side
effect of this recovery. Do not resume automation without merchant approval.
