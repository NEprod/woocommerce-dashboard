# Production, inventory and finished-product costing

## Printable stock cards

Item details offers View Stock Card and Print Stock Card; Inventory Catalogue
rows also link to the preview. Cards render current saved stable information at
4 × 3 inches, without live balances. Code 128 and the text beneath it use the
same permanent item identifier, never a production batch identifier. Missing
barcodes are reported, not allocated. Previewing and printing do not change data.
Use actual size / 100%, no margins or browser headers/footers, and check one page
in the native print preview. Printer-specific paper-size support still applies.

Inventory remains local. Existing purchased/material receiving, location scans,
immutable stock history, idempotent confirmations and compensating reversals are
reused. No planner or settings action sends Shopify requests or changes prices.

## Manufacturing

Create Production reserves one Code 128 label per physical run. Per-component
sequences start at 0001 and are never deleted or reused. Planning adds no stock.
The plan retains the whole-batch material recipe; subsequent recipe edits do not
change an existing batch's consumption defaults.

Scan a batch in Scan / Look up, then Complete Production. Edit accepted/failed
pieces and actual consumption, choose source/destination locations, review and
confirm. Zero accepted output can still consume materials. Material deductions,
accepted stock and the completion receipt commit together. Repeated confirmation
returns the original receipt; a fresh completion is refused.

Use / Remove Stock on the batch records one batch-tagged ledger movement. Overall
stock and batch stock are sums of the same ledger, not independent counters.
Legacy unbatched stock remains available. An ordinary removal/count/transfer may
not consume batch stock without its batch identity. Use Stock History reversal
with a reason for compensating corrections; original evidence stays intact.

Recommendations subtract already planned output from the preferred target gap.
They are estimates and never create runs or reserve/consume material automatically.

## Costing

Settings → Production & Costing stores the 10% manufactured-material allowance,
electricity tariff, hands-on hourly rate, markup, default fees and other allowance.
Equipment profiles can be edited or archived. Initial power values are explicitly
provisional; unknown laser/chiller/fan/pump values remain blank and block complete
estimates when selected. Optical laser output is not electrical consumption.
Fee profiles identify total applicable charges; do not enter a second Shopify fee
if already included by the provider. Retain old profiles and add a new profile for
a changed arrangement rather than silently reinterpreting past assumptions.
Until applicable defaults are explicitly saved or a fee profile is selected,
financial profit/fee-adjusted suggestions are withheld. Unit production costs
remain reusable by bundles without charging constituent transaction fees.

Product Editor retains its existing BOM inheritance/variant rules. The linked
Finished-product Profitability page saves processing assumptions against the
retained product/SKU. Its inventory lines are read-only projections of that BOM.
The editor's live BOM preview uses the same estimator. Saving planning assumptions
does not rewrite product JSON, set a selling price or move stock.

Tools → Standalone BOM also permits direct raw materials. Such lines are additional
to materials inside manufactured-component recipes. Save, reopen and duplicate
local scenarios without creating catalogue products. Tools → Promotions & Discounts
opens the original `/promotions/` mix-and-match planner with its existing calculations
and local-only boundaries. The duplicate `/production/bundles` editor is retired;
old page bookmarks redirect to Promotions & Discounts and cannot create scenarios.
Any saved duplicate bundle scenarios remain in the database unchanged, readable
through their existing scenario bookmarks but no longer editable or listed in the
BOM planner. No scenario is converted into a promotion. Shared costing helpers
remain available; this navigation cleanup does not change pricing calculations.

Equipment minutes mean time attributable to ONE finished product, not a whole
multi-item plate. Labour means hands-on time only. Manufacturing allowance applies
only to manufactured-material estimates; it never changes physical consumption.
Unknown inputs withhold complete cost/profit suggestions rather than counting as
free. Markup and margin are different; prices remain the operator's choice.

New standalone scenario saves retain a calculation snapshot, costing defaults and
equipment context. Reopening shows that saved result. Editing inputs / Calculate
Preview explicitly calculates with current rates; Save records the new result.
Legacy scenarios without snapshots are identified as such; their original historic
results cannot be reconstructed. Historical production receipts remain unchanged.

## Costing workspace interactions (pending user acceptance)

Equipment and fee profiles use shared table panels and pencil-edit native dialogs,
following the application's package-editor dialog pattern. Cancel/Escape discards
dialog drafts. Save is explicit; invalid input stays in the dialog with feedback.
Equipment archival requires confirmation and retains references. Fee edits retain
their profile ID and use settings-version checks.

Planner inventory, hypothetical-material and process tables have dynamic Add,
pencil Edit and Remove actions. Apply Row only updates the unsaved scenario; it
does not save inventory or produce stock. Hypothetical costs are provisional and
allowed only in standalone scenarios. Blank hypothetical costs remain unknown.

Per-item timing remains the default. Batch timing requires usable units and an
explicit allocation: identical items, an explicitly chosen equal mixed-item split,
or an attributable share of the job followed by division by that group's usable
units. Failed pieces are reflected by reducing usable output. Select concurrent
electrical devices separately; their runtime never becomes hands-on labour.

Known estimated/specification wattages produce a provisional result; missing
wattages, tariffs or material prices produce an incomplete result. The existing
manufactured-material allowance and fee-aware pricing equations are unchanged.

Scenario Rename uses the name field and Save. Duplicate creates a new scenario.
Delete requires confirmation and removes the scenario from the saved list through
an additive JSON archive flag; the historical record is retained.

Manual acceptance: edit/cancel an equipment profile, save and reopen it; repeat
for a fee profile. Add a hypothetical material and two concurrent process rows.
Compare seven usable units with five usable units using the same batch runtime.
Save, change defaults, reopen the scenario and verify the saved result remains;
then explicitly recalculate. Check keyboard Escape/Tab and phone modal scrolling.
No commit or Docker release is authorised for this UI-overhaul review.

## Storage and deployment

Migration 0027 is additive: batch identities, batch tags on ledger movements,
costing settings, equipment, local scenarios and per-SKU planning profiles. It
preserves existing identities, receipts and unbatched balances. Existing database
backup/startup migration mechanisms apply. Destructive downgrade is refused.
No new required environment variable or Unraid mount is introduced. Do not change
the running container automatically. Camera hardware and physical printed labels
should be manually accepted on the development container after deployment.
