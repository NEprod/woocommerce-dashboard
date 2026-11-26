# BOM channel planning

Production & Costing Settings now contains **Channel pricing defaults** and
**Sales channel fees — Etsy**. Existing payment profiles/defaults remain the
Shopify fee source. Saving these settings does not write channel product prices.

Shopify suggestions default to rounding upwards to the next .49 or .99. Explicit
older rounding selections still work; retained scenario snapshots are not
recalculated on read. Etsy starts from the current/chosen Shopify selling price,
or its rounded suggestion when no price exists; add the greater of 15% or £1,
then round upwards. All defaults are editable. A zero uplift is supported.

The shared Decimal pricing engine computes final profit after rounding. Etsy
uses its own fee assumptions: transaction 6.5%, payment 4% + £0.20 per order,
regulatory 0.48%, approximate GBP listing/renewal allowance £0.15 per item and
20% VAT on selected fees. Offsite advertising defaults to zero (disabled).
Each fee's VAT applicability can be changed, including a zero VAT rate. These
are user-approved starting assumptions, not verified account-specific charges.

The estimate assumes one item per order and allocates its full fixed processing
fee. Percentage fees use item selling price only. Gift wrap is not offered and
postage is separate: postage income, actual postage cost, buyer taxes and fees
on postage are not included. Etsy can charge fees on postage even when billed
separately. Listing charges originate in USD; the editable £0.15 allowance is
not a live currency conversion. Fee components display rounded values but total
fees/profit use unrounded Decimal values. Adjust assumptions to actual VAT and
advertising treatment. No tax advice or net business-profit assertion is made.

Official sources checked during implementation:
- https://www.etsy.com/uk/legal/fees/
- https://help.etsy.com/hc/en-gb/articles/115015628847-What-are-Payment-Processing-Fees-for-Selling-on-Etsy
- https://help.etsy.com/hc/en-gb/articles/1500011073202-What-is-a-Regulatory-Operating-Fee

In the Standalone Planner, **Load Existing Product** searches active local
products; select the sellable SKU (variant where applicable). The existing
effective-BOM resolver copies materials, components, packaging, manufacturing
allowances, equipment/batch allocations, labour and pricing assumptions. The
current catalogue selling price replaces any legacy what-if profile price.
Missing/unresolvable BOMs are blocked with an explanation rather than copied
partially. The editable result saves only as a standalone scenario with no
product/profile association. Original catalogue JSON, prices and stock are not
changed. This does not connect to Etsy, publish listings or import orders.

Both catalogue BOM editors show the same channel comparison. Inherit All is
read-only; Inherit Plus/Rewrite retain their existing persistence semantics.
The optional Etsy what-if price is a calculation assumption only, not a live
Etsy price. Existing scenario snapshots retain their original result/settings;
explicit Calculate Preview uses current assumptions and Save retains a new
snapshot. No data migration or additional persistent storage is required.
