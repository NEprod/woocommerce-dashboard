# Production, inventory and finished-product costing

## Printable stock cards

Item details offers View Stock Card and Print Stock Card; Inventory Catalogue
rows also link to the preview. Cards render current saved stable information at
2 × 3.5 inches, portrait, without live balances. Code 128 and the text beneath it use the
same permanent item identifier, never a production batch identifier. Missing
barcodes are reported, not allocated. Previewing and printing do not change data.
Use actual size / 100%, no margins or browser headers/footers, and check one page
in the native print preview. Printer-specific paper-size support still applies.
The upper image uses contain sizing (no crop); optional details are compact and
long names are limited to two lines. One card is printed per custom-size page;
there is no built-in business-card sheet/imposition layout. Do not use Fit to page.
The portrait-only SVG is 1.78 inches wide including 12-module quiet zones on each
side, with 0.45-inch bars. The layout refuses modules narrower than 0.20 mm; this
is a print-layout safeguard, not a guarantee for all printer/scanner combinations.
Long codes keep their full identifier but show a warning instead of a squeezed
barcode. Use the existing larger inventory label for those codes. General item,
location and batch labels retain their previous geometry and identifiers. Check
a physical 100%-scale test print with a ruler and your scanner before bulk printing.

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

## Hypothetical material purchase basis

New hypothetical rows default to total purchase cost, purchased quantity/unit,
and quantity/unit used in one proposed finished product. The modal previews the
normalised unit cost and attributed material amount before Apply Row. For example,
£13.99 for 1 kg with 26 g used contributes £0.36374. The same Decimal helper powers
the local read-only modal endpoint and BOM totals; line amounts are not rounded
before aggregation. Material displays retain up to eight decimal places, while
final price outputs retain the established currency rounding.

Compatible units use existing conversion factors. For packs, enter the actual
pack count and pieces per pack. For incompatible units, explicitly enter:
1 purchased unit = N usage units. Never infer sheet yields or pack contents.
Missing purchase quantity/unit and incompatible conversions without a factor
are validation errors. Blank cost stays unknown; an explicit £0 is a known zero.

Known direct unit cost remains a selectable mode; legacy rows reopen in that
mode without reinterpretation. Purchase inputs are optional additive fields in
existing scenario JSON, not new inventory records or schema changes. Row Apply
only changes the unsaved scenario; Save retains the inputs and calculation
snapshot. This change has no stock, barcode, Shopify or deployment effects.

### Production process runtime entry

Process dialogs accept hours and remaining minutes, but saved scenarios still
store total minutes (250 minutes reopens as 4 hours 10 minutes). Select one
finished item or a whole batch/print plate. Identical items split runtime equally
across accepted usable items. Mixed items require an explicit equal split or a
custom percentage of total runtime, then divide by the usable items in that group.
The live preview shares the BOM calculation and uses current profile/tariff values;
unknown electricity inputs stay unknown and estimated wattages remain labelled.
Opening or cancelling a dialog does not rewrite saved assumptions or snapshots.

### Standalone print waste, packaging and price comparisons

The existing `buffer` (default 10%) applies only to manufactured-component recipe
material estimates. It intentionally does not charge direct/hypothetical raw
materials. The separate `print_waste_rate` defaults to 11% and applies only to
standalone raw lines explicitly marked **3D-print raw material**. This is an
operator declaration of process use, not a classification inferred from PLA names.
Unmarked lines, manufactured/purchased components, packaging, electricity and
labour are excluded. Existing scenarios without marked lines acquire no charge.
Blank scenario overrides inherit; explicit zero disables the allowance. The
full-precision allowance enters total cost before pricing, without stock changes.

Packaging & Presentation accepts hypothetical purchase/direct costs or saved
purchased components. Add only packaging additional to any existing recipes/BOM;
the same inventory item cannot be charged in both material and packaging sections.
Unknown costs remain incomplete; absent packaging is labelled not included.
Postage and customer shipping income are not inferred. Nested recipe packaging
and physically identical hypothetical lines cannot be identified automatically.

Advanced Costing Options retain hidden values and show inherited defaults and
active overrides. Opening/cancelling an Add dialog has no row or dirty-state
effect. Rows enter the submitted form only after Apply. Row templates are inert,
so repeated preview/save/reopen cannot generate new unused placeholders. Existing
saved rows are rendered without automatic deletion or database cleanup.

Blank chosen price means no chosen-price profit calculation; explicit £0 remains
a real zero. Previously saved zeroes and snapshots remain zeroes/snapshots. The
suggested-price comparison solves percentage plus fixed fees once. Exact Decimal
recommendations are retained; currency recommendations round upward to a penny,
or optionally to £0.50, £1 or .95, never downward. Profit/fees/margin use the
presented recommendation. Changes to current defaults only affect an explicit
preview/recalculation or save, not stored historical snapshots.

The mobile bottom navigation stays fixed. Planner clearance is measured from its
actual rendered position; modal content scrolls independently with sticky actions.
Browser tests cover phone and reduced viewport heights, not physical iOS keyboard
or printer hardware acceptance.
