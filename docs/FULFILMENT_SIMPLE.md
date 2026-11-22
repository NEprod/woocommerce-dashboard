# Simple Order Fulfilment

Order Fulfilment remains attached to imported Shopify orders. Manual lookup and
barcode lookup reopen the same local line records; no manufacturing route is
required. Existing route metadata, BOM inheritance and costing are retained.

## Operator controls

The working sheet uses the existing dark-green table headers and status pills.
Products, quantities and captured personalisation are shown alongside the stage
selector. Customer email has Copy Email and a mailto link (no server-side sender).
Routine status, stock confirmation and uncommitted refresh need no typed reason;
their operator/timestamp/action are still audited. Correction tools stay collapsed
and retain their reasons/authorisation checks.

The camera icon opens the existing on-device barcode scanner. Scanning selects
the stock source; Confirm Materials Used / Confirm Picked posts the deduction.
A sole stocked location is preselected; multiple locations require a choice.
Reversible stock picking can be recorded while a proof is pending, but required
proof approval still gates physical production/completion.

Attach Proof accepts JPEG/PNG/WebP images up to 10 MB, optimises them using the
existing inventory image helper, and stores the private result on the existing
proof version in persistent SQLite. Previous versions remain available only to
authorised order operators. Attachment is not sending or approval. PDFs are not
accepted by this image upload. Migration 0031 adds one nullable binary column;
existing proof references and history remain valid. No new volume is needed.

- Update Status records a per-line stage, actor, reason and timestamp using the
  existing workflow history. It never issues or returns inventory.
- Printed is a separate explicit print confirmation, not a production stage.
- Confirm Materials Used / Confirm Picked issues the selected requirement using
  the existing stock ledger, physical location, batch validation and retry key.
- Produced, Picked and later completion stages require outstanding inventory
  requirements to have been confirmed. Whole-order packing/dispatch also checks
  the other current lines. These are safety checks, not manufacturing routes.
- Dispatch requires actual carrier handoff and a dispatch/tracking reference.
  It does not submit a Shopify fulfilment or buy postage.

Validated purchased proof Yes/Required/No overrides collection defaults. Unknown
legacy evidence retains the previous fallback and is labelled for review;
inconsistent proof evidence still requires an audited override. Proof Sent
records an opaque private reference and sending channel; Proof Approved requires
a recorded sent proof, explicit approval confirmation, channel and evidence.
Nothing sends customer email. Historical decisions are not recalculated on read.

Advanced / Troubleshooting retains refresh, hold, cancellation, reconciliation,
proof overrides and stock returns. Refresh is explicit and cannot rebuild picked
or committed requirements. For an older Needs Configuration order, correct any
actual identity/BOM problem and use this refresh; a missing route is no longer an
error. Held/terminal work retains its existing correction safeguards.

The normal BOM editor no longer exposes the collection proof flag. Optional
legacy route/picking metadata and finished-stock selection are collapsed; saved
values are preserved. No workflow manager or new database migration is added.

## Printing

The existing A4 sheet uses compact print-only typography and tables, with customer
details and the unchanged Code 128 order barcode together at the bottom. Each
bulk order starts on a new page. Long orders may span pages. Browser users should
choose A4, 100% scale and turn off browser headers/footers; physical printer
margins remain printer-controlled. Generating a sheet never confirms printing.

Focused automated tests use disposable catalogue, order and ledger fixtures.
Browser checks cover desktop/mobile status and stock controls plus one-card,
two-card and bulk-two-order PDF layouts. They are not physical iPhone, printer
or live Shopify acceptance tests.
