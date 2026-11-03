# HS Code: existing editor and Shopify lifecycle

## Storage and editor

HS Code is a text field beside Country of Origin in the normal shared/product
metadata editor. It accepts 6–13 ASCII digits, retaining leading zeros, or blank.
The existing override switch, signed local patch, validation and reload behaviour
are reused. No new customs editor, classifier, lookup, setting or required field.

```json
{"harmonized_system_code": "490900"}
```

This spelling already existed in the variable publisher; no second key was
introduced. Shared JSON supplies a scalar default; a product scalar overrides it.
Product JSON stays sparse. Variation-specific customs authoring was not supported
before and is not introduced. All applicable variants receive the resolved
product code. Prices, identities, modifiers, packages and quantities are unchanged.

Absent local fields remain valid and unmanaged. Existing explicit blank strings
also remain unmanaged, never instructions to erase Shopify data. The Guided
editor's existing blank/removal behaviour removes the local key: a product then
inherits its collection code, or becomes unmanaged if none exists. Clearing an
override with a different inherited code therefore produces a real update.

**Remote erasure is not supported in this release.** The existing customs editor
has no distinct intentional Shopify-clear contract. No destructive meaning is
assigned to blank/null and no new clear workflow is invented. Null is rejected
with a field-specific validation error. An actual remote deletion would require
separate explicit intent and validated API clearing behaviour. Country of Origin
semantics are unchanged. No real JSON was rewritten; only the complete reference
example/template gained a blank optional entry to cover the documented schema.

## Lifecycle and immediate local detection

The existing sequence is unchanged:

1. Save the authoritative shared/override JSON through the existing editor.
2. The existing containing-collection scan resolves the source and commits the
   normal publishing revision/checkpoint. No Shopify request or write occurs.
3. That scan's existing `queue_projected` handoff queues its bounded **read-only**
   comparison. The existing saved-view task persists the resulting status.
4. A linked eligible Live product with a differing code displays **Update
   Available**, including `HS Code: Not set → 490900` (with a variant prefix where
   applicable). No manual full scan, Discovery, Build Preview or hourly publishing
   run is needed to discover the change. During the existing read/status refresh,
   “Checking Changes” or “Status refresh pending” may appear; no fresh equality is
   invented before comparison. Existing startup grace and task cadence remain.
5. Only an existing authorised hourly publisher cycle or explicit Review & Sync
   writes Shopify. No save-triggered publisher or new background process exists.
6. The existing independent readback checks every managed InventoryItem code.
   Complete verification persists the retained identity/comparison/queue result
   and Up to Date. Subsequent unchanged cycles skip writes.

No new database column, migration, scan mode, queue, scheduler or fingerprint
policy version was added. The common managed projection already contributes to
the Simple source fingerprint; the variable source fingerprint already includes
`harmonized_system_code`. The resolved immutable shipping snapshot now exposes it
alongside origin. No managed HS field is emitted for absent/blank data, so merely
upgrading does not enrol old unspecified Shopify customs values into management.

## Shopify mapping and safeguards

API remains 2026-10. The existing `productVariantsBulkUpdate` path used for origin
receives `inventoryItem.harmonizedSystemCode` on the exact retained variant, whose
InventoryItem identity is validated by existing preview/readback. Creates use the
existing variant configuration stage; updates send only changed fields. No product,
variant, InventoryItem or media is recreated for a customs edit.

The common Simple read query now selects `harmonizedSystemCode`; the Variable read
already selected it. Missing fields/invalid readback cannot prove equality. A
partial, rejected, lost or interrupted mutation follows existing receipts and
recovery rules, not automatic replay. The existing inventory-only signed review
also treats HS Code like origin, without pretending scoped verification is full
product synchronisation.

Shopify documents the string field and length in
[InventoryItemInput](https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/InventoryItemInput)
and the existing mutation's `write_products` requirement in
[productVariantsBulkUpdate](https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/productVariantsBulkUpdate).
The checked-in TLC app already declares read/write products and inventory scopes.
No app configuration, scope, URL, extension, theme or permission was changed.
No live token grants were inspected; existing preflight still reports missing
permissions before any authorised write.

## Why each production extension point changed

| File | Narrow purpose |
|---|---|
| `catalogue_fields.py` | HS validation and resolved shipping snapshot |
| Both product-info schemas, inventory and complete reference JSON | Optional canonical text field, accurate field inventory/examples |
| `product_info.py` | Clear HS-specific schema validation message |
| `metadata_editor.html`, `metadata-editor.js` | One normal text input and existing sparse editor roundtrip |
| `shopify_simple_fields.py` | Common HS projection, readable label, complete typed readback |
| `shopify_variables.py` | Remove its duplicate HS-only mapping in favour of the same common contract |
| `shopify_connection.py` | Add the field to the existing Simple read query only |
| `shopify_inventory_review.py` | Include it alongside origin in the existing optional signed inventory scope |
| `shopify_comparison.py` | HS-specific before → after text in saved change summaries |

Scanner, ingestion, automatic publisher, scheduling, identity/recovery code,
Country of Origin policy, media, package assignments, Orders and Intake code are
not modified. The Simple API test double now includes the queried nullable HS
field; it no longer represents a response omitting that field.

## Executed test evidence

The focused tests use isolated catalogue/SQLite fixtures and controlled Shopify
adapters. They exercise normal HTTP editor saves, actual collection scans,
persisted revisions, the existing comparison/view scheduler, and only then the
existing hourly publishing entry point. Both Simple and Variable Collection
shared/override saves and clearing an override back to inheritance are covered.
Variable projection/update/partial-outcome tests also cover Single Variable.

Other focused cases include six-/13-digit/leading-zero codes; blank/absent data;
invalid lengths/types/punctuation/Unicode digits; sparse JSON; every variant's
readback; unchanged skips; Draft protection; explicit inventory-only scope;
rejections, ambiguous/partial writes and no duplicate create/media/variant writes.
The existing delayed-readback/replacement-worker scenario is reused with HS Code,
preserving original operation identity and acknowledged writes across fresh DB
sessions. Existing 14-variant/53-image, package, manual-worker and automatic
publishing regressions are included in the bounded affected run.

Desktop/mobile browser tests used separate disposable contexts, not Safari or
production. They verified inherited disabled fields, clear invalid-code feedback,
text/leading-zero preservation, actual Save → scoped scan → reload, restoring
inheritance, and no horizontal overflow or JavaScript errors.
[Desktop](evidence/hs-code/tlc-hs-desktop.png),
[mobile](evidence/hs-code/tlc-hs-mobile.png).

The Docker check reuses the existing old-appdata/Orders/package/customisation/
publisher smoke harness, then saves HS through the real editor and scoped scan.
It checks source/checkpoint/view persistence with two/four workers and restart,
automatic publishing disabled, no network and disposable mounts. Remote read-worker
dispatch is mocked in that container-only persistence probe to prevent an unmocked
Shopify read racing fixture saves. Actual read-worker dispatch and status outcomes
are covered separately by the controlled-adapter lifecycle test, not claimed as
Docker Shopify acceptance.

Initial Docker attempts exposed legacy fixture assumptions: missing Draft/Live
intent correctly produced Attention; the helper expected old migration 0019
rather than existing head 0021; a new scan legitimately replaced its old queue
trigger and cancelled a synthetic release with no authored date; a global page
variable had been reused by older checks. These fixture expectations were
corrected, without weakening production safeguards. The validation build also
needs an explicit image revision label for the inherited reporting helper.

One affected test is a **confirmed baseline failure**:
`test_shopify_product_ux.py::test_live_intent_remains_blocked_without_mutating_source`.
It expects old “Live intent” blocker wording. The exact same failure reproduced
on an isolated archive of starting commit `512e17b840f374ea788f10694db144816614ad70`.
Its token remains absent; no write is authorised. That unrelated assertion and
production publication rules were not changed to make a test green.

## Safe deployment and merchant acceptance

Final local validation (3 November 2026):

* **59 passed** in the final HS/schema/scanner-parity run (638 warnings):
  `tests/test_hs_code.py`, `tests/test_product_info_contract.py`,
  `tests/test_phase0_scanner_output_parity.py`.
* **39 JavaScript checks passed**, plus syntax checking. Both actual desktop and
  mobile browser workflows passed with zero page errors and no overflow.
* The bounded affected run reported **404 passed and the one baseline failure
  above**. It covered metadata saving/scoped dispatch, catalogue extensions,
  Simple/Variable publishing, package assignments/correction, automatic publishing,
  source checkpoints, saved views, edit/publish lifecycle, delayed verification,
  manual workers and scanner resolution. Additional sale/publication tests passed.
  These runs overlap; they are not presented as an inflated combined total.
* **Two-worker and four-worker offline Docker upgrade/restart checks passed**:
  explicit local Draft, actual HS save/scoped ingestion, matching source revision,
  saved view/editor roundtrip, appdata/IDs/receipts/packages/Orders/customisations
  retained; automatic publishing off and no external network. Existing migration
  head remains `0021_manual_review_worker`; no HS migration was introduced.

Reproducible focused commands:

```sh
python3 -m pytest -q tests/test_hs_code.py tests/test_product_info_contract.py \
  tests/test_phase0_scanner_output_parity.py --disable-warnings --tb=short
node --check app/static/assets/js/metadata-editor.js
node --test tests/javascript/metadata-editor-core.test.mjs \
  tests/javascript/metadata-title.test.mjs tests/javascript/shopify-publish.test.mjs \
  tests/javascript/package-id-save.test.mjs tests/javascript/package-manager.test.mjs \
  tests/javascript/scanner-workspace-client.test.mjs
python3 tests/browser/hs_code_fixture.py
node tests/browser/hs-code.browser.mjs
python3 tests/docker_hs_code_smoke.py IMAGE 2
python3 tests/docker_hs_code_smoke.py IMAGE 4
```

No deployment, live source edit, scan, reconciliation or Shopify mutation has been
performed for this milestone. Install only after approval, using the immutable
`hs-code-<commit>` image from the final report. Preserve all existing mounts,
especially `/app/instance`; retain a paired appdata/catalogue backup and the
previous `intake-tools-512e17b840f3` rollback image. No HS-specific migration.

1. On staging, choose a linked Live Simple product with trusted current evidence
   and no recovery block. Record product/variant/InventoryItem IDs and old code.
2. Enter an actually appropriate merchant-approved 6–13 digit code beside origin.
   Save once. Confirm only its collection scan runs and no Shopify write occurs.
3. Let the existing read-only status/view refresh finish (respect startup grace).
   Confirm Update Available and the HS before/after without Discovery/full scan
   or running the publisher to discover the change.
4. Explicitly approve Review & Sync, or wait for the already authorised hourly
   cycle. Verify only intended InventoryItems changed; no image/package/price/
   quantity writes or identity changes; complete readback and Up to Date persist.
5. Let another unchanged cycle run: no update. Repeat on a variable product and
   inspect every applicable variant InventoryItem, not just the first.
6. Confirm shared inheritance and a distinct local override. Disable that override:
   the normal local refresh must identify any resulting inherited HS difference.
7. Confirm uncreated Draft stays local; linked Draft retains pending metadata.
   Leave absent/blank HS values unmanaged. Do not interpret blank as remote erase.
8. Restart staging workers: source, checkpoints, IDs, comparison and receipts
   survive, with no startup scan or duplicate publication.

Mocked verification is not live Shopify acceptance. No automated HS classification
or destructive remote clearing is claimed. Rollback code does not reverse an
already authorised Shopify customs update: restore intended local data and use
the normal approved workflow if a merchant needs to revert that value.
