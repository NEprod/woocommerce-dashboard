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
local scenarios without creating catalogue products. Tools → Bundles multiplies
saved finished-product unit costs by quantity, adds bundle-only costs, and charges
the fixed transaction fee once. Promotion Plans retains its local-only boundaries;
saved product costing profiles are reused where present.

Equipment minutes mean time attributable to ONE finished product, not a whole
multi-item plate. Labour means hands-on time only. Manufacturing allowance applies
only to manufactured-material estimates; it never changes physical consumption.
Unknown inputs withhold complete cost/profit suggestions rather than counting as
free. Markup and margin are different; prices remain the operator's choice.

Current estimates follow current inventory rates/settings. Historical production
receipts retain actual consumption and costs. Scenario documents retain inputs,
not a guarantee that a future reopened estimate has unchanged rates.

## Storage and deployment

Migration 0027 is additive: batch identities, batch tags on ledger movements,
costing settings, equipment, local scenarios and per-SKU planning profiles. It
preserves existing identities, receipts and unbatched balances. Existing database
backup/startup migration mechanisms apply. Destructive downgrade is refused.
No new required environment variable or Unraid mount is introduced. Do not change
the running container automatically. Camera hardware and physical printed labels
should be manually accepted on the development container after deployment.
