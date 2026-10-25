# Human-readable customer customisation review

## Why the old review showed hashes

`shopify_connection.CUSTOMISATION_VALUE` already reads the complete `value` JSON
and `compareDigest` of the app-owned `$app:tlc_customisations.configuration`
metafield (resolved by Shopify to its app-owned namespace). The publisher's
`shopify_customisations.attach` retains that complete before snapshot and the
effective desired public configuration in its signed plan. However, `_summary`
reduces them to enabled state, field count and revision. `review_rows` previously
formatted those summaries generically, so different revisions did not explain
the changed settings.

No new Shopify query, metafield, scope, stored configuration, migration or write
is introduced. No live store was queried during development. Complete definitions
are supported by the existing readback; each actual review uses its own snapshot.

## Presentation-only correction

`customisation_review.compare` is a pure, reusable explanation of that snapshot,
not a new equality/publishing decision. It matches stable field IDs and reports:

- Added/removed fields with labels, types, required/optional state and expandable
  field settings.
- Label, type, required flag, placeholder, help text, choices, defaults, text/number/
  date/selection bounds and whole-number changes.
- Changed relative field order, with actual old/new positions. Inserting/removing
  another field alone does not label every remaining field as reordered.
- Enabled/disabled changes independently of field changes.
- Already-matching fields, with their stable IDs.

The renderer uses array order. Changes only to numeric ordering bookkeeping,
revision hashes or equivalent implicit defaults do not invent customer-visible
field changes. A technical difference can still retain the original `Change`
action. Technical Details retains both revision hashes and the reviewed public
JSON. Unknown, duplicate, invalid, missing or revision-only previous field data
produces an explicit details-unavailable explanation, not fabricated additions.
Verified metafield absence is distinguished from missing readback evidence.

The existing review section and styling are reused. The generic hash-heavy row
is replaced by one readable section, not duplicated in other/matching tables.
Long values and expanded hashes wrap on mobile; remote text is HTML-escaped.

## Unchanged contracts

Template → Shared Collection → Product Override additions still resolves through
the original resolver and publisher. No inherited fields are copied into child
JSON. Definitions, revisions, compare-digests, signed approvals, revalidation,
post-write readback, idempotency and recovery remain unchanged. The helper neither
mutates its inputs nor authorises or performs writes. Production Time, answers,
private personalisation snapshots, the app extension, theme, Orders, scanner,
organisation fields, shipping and all other publishing stages are untouched.

## Validation and evidence

Validated on 25 October 2026: 54 focused Python checks and 201 additional bounded
checks passed, alongside 38 JavaScript tests and all 12 desktop/mobile scenarios.
The two baseline failures described below were reproduced separately on an
untouched export and left unchanged. Syntax and whitespace checks passed.

Focused tests cover every supported public setting, additions/removals/reordering,
multiple changes/fields, effective defaults, full template/collection/product
resolution, signed approval, no-write previews, readback and matching no-op reviews.
Existing storefront validation, separate personalisations and Production Time
tests remain in the bounded regression selection.

Run the isolated `tests/browser/customisation_review_fixture.py` server and
`tests/browser/customisation-review.browser.mjs` with installed Chromium/Playwright
paths. Twelve checks cover six scenarios at 1440×1000 and 390×844: changed/added,
matching, unavailable definitions, hash-only, reordered and long/escaped values.
No POST or non-loopback request is permitted. Evidence is generated under
`/tmp/tlc-customisation-review/`. Mobile is Chromium emulation, not physical Safari.

The two existing draft-test assertions below fail identically on untouched baseline
`63492015f6b0df208f829674aa69ed19c17e51bb`: they omit the existing required
`dimension_definitions_read` call. No safety read or unrelated test was changed:

- `test_preview_is_read_only_and_confirmation_required`
- `test_async_processing_rechecks_reads_only`

`tests/docker_customisation_review_smoke.py` extends the current network-isolated
two-worker upgrade/restart harness, using disposable catalogue/database mounts.
It verifies readable review from the persisted effective canonical configuration,
alongside existing package IDs, receipts, order personalisations and Production Time.

## Minimal Unraid acceptance

1. Back up appdata and record the previous immutable image. Deploy the reported
   `customisation-review-<commit>` tag manually, retaining all mounts/settings.
2. Open a product's Review & Sync and build a read-only preview. Check the field
   explanation against an already-authored local change and its current Shopify
   configuration. Do not approve a write merely to test the new presentation.
3. Confirm inherited fields appear in the effective review; unchanged fields stay
   matching. Inspect Technical Details for both original revision hashes.
4. On mobile check expanded details, long values and the existing approval control.
5. If previous definitions are unavailable, expect the explicit explanation, not
   guessed additions. Recovery cases must still use the original safe workflow.

No Unraid deployment or live Shopify mutation is performed by this work.
