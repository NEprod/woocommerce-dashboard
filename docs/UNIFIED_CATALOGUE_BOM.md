# Collection and product BOM workspaces

The **Production & Costing** section is embedded in the existing metadata editor:

- `/collections/<collection_id>/metadata` (collection JSON owner)
- `/edit_products/<product_id>/edit/shared` (same collection editor)
- `/edit_products/<product_id>/edit/override` (sparse product JSON owner)

The inventory and process row templates, transactional pencil modals, server-side
material preview, hours/minutes adapter and Decimal profitability service are
shared with the Standalone BOM & Viability Planner. Catalogue editors do not
expose hypothetical materials or custom purchase prices. Purchased packaging is
selected from existing inventory; no stock is moved.

## Saving and inheritance

Use **Save Metadata** to persist to the existing authoritative `product_info.json`
owner, with the existing signed revision, backups and scoped scanner handoff.
Preview, import review and Apply Row/Import only affect the unsaved working draft.

Product modes use the existing vocabulary internally:

| Editor mode | `production.mode` | Behaviour |
| --- | --- | --- |
| Inherit All | `inherit` | Collection BOM and processing inherited read-only; no inherited copy saved |
| Inherit Plus | `add` | Collection requirements retained; local lines/processes/labour added |
| Rewrite | `replace` | Product-specific replacement; collection source untouched |

Legacy documents without a main mode retain their original additive resolution.
Existing exact option and retained-SKU adjustment rules still resolve through the
same resolver. Intentional same-item quantity merging is retained; the row modal
requires confirmation before adding an already present item/profile. Existing
local modifications are editable and exclusions can be explicitly reset. Import
blocks ambiguous duplicate inherited requirements instead of guessing intent.

Switching modes asks for confirmation when there is local work. Inactive mode
drafts are kept in the current editor session, not written into the wrong JSON
mode. Reloading/leaving the editor discards unsaved drafts. Save only persists the
selected mode.

## Compatible JSON additions

No database migration or read-time source rewrite is introduced.

- Optional `production.costing` stores equipment usage, hands-on minutes/rate,
  material allowances, markup, other direct costs, fee profile and price rounding.
- Existing line IDs/operations remain authoritative. Optional `purpose` marks
  packaging; `print_material: "yes"` explicitly marks additional direct raw
  material for the separate print-waste allowance.
- Variant layers may carry processing overrides. Existing option-level rules
  remain inventory adjustments, not a second pricing model.
- Additive processing adds equipment lines, labour minutes and other direct costs;
  scalar rates/percentages use normal inherited/explicit-zero overrides. Rewrite
  resets inherited collection requirements, including collection option/SKU BOMs;
  product-authored exceptions still apply. The metadata validator accepts and
  validates these structures without unknown-field warnings.

The separate **Cost source** remains the established material-only Shopify Cost
per item contract (or explicit manual/unconfigured source). Full internal
electricity/labour/allowance costing is not automatically exported. Review & Sync
and its verification/authority boundaries are unchanged. Catalogue selling prices
are used for comparison, never written by this workspace.

Existing database-only processing profiles remain a fallback for untouched older
BOMs, including their saved what-if price. Explicit JSON costing takes authority;
explicit Inherit All uses the collection configuration instead of a local legacy
profile. The old profile and standalone historical snapshots are not rewritten.
For an untouched product with an older local profile, the read-only preview calls
out that compatibility fallback and offers **Confirm Inherit All — use collection
costing**. This authors only the explicit inheritance choice; it does not delete
the retained legacy profile.

## Import Saved Test BOM

Select a locally saved standalone scenario and review its requirements, costing
inputs, retained snapshot/context, current recalculation and effective result.
The saved revision is shown; no timestamp is invented where storage lacks one.

Import is all-or-nothing. Hypothetical materials/packaging, unresolved or archived
references, incompatible units, incomplete costs, unsupported fields and ambiguous
duplicates/conflicting overrides block Apply with reasons. Equipment runtime,
allocation, inventory IDs, packaging, allowances, labour, markup and fee selection
are preserved. Unsupported custom costs are not silently dropped. A scenario's
selling price is deliberately not mapped to catalogue pricing.

Inherit All disables import. Inherit Plus appends reviewed local requirements;
Rewrite/collection import replaces the working main BOM after confirmation.
Existing exception rules and Cost source survive a main-BOM import. Cancel leaves
the editor unchanged; Apply Import still requires normal Save Metadata.

## Local acceptance checks

1. Open a collection's Production & Costing section; add registered Santa parts,
   packaging, X2D profile/runtime and labour. Check preview, then Save Metadata.
2. Reopen it and confirm validation and all fields. Compare existing selling price
   and full internal cost against the separate Shopify material cost projection.
3. Open a child override. Inherit All must be read-only. Switch to Inherit Plus,
   add requirements, then switch modes and verify confirmation/draft restoration.
4. In Rewrite, review/import an inventory-backed scenario. Cancel once, then Apply
   and Save Metadata. Confirm the collection source remains unchanged.
5. Try importing a hypothetical scenario; Apply must be disabled with a reason.
6. Repeat on mobile. Fixed bottom navigation remains fixed; tables use compact
   responsive rows, and row modals retain internal scrolling and safe cancellation.

Automated browser fixtures use temporary SQLite/catalogue roots and forbid outbound
HTTP. No physical-phone testing or Shopify writes are needed.
