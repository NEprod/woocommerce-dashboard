# Informational attribute editor correction

## Cause and bounded change

The organisation release saved registry selections in `taxonomy_additions`, but
`metadata-editor.js` continued rendering cards from `valueFor('attributes')` only.
The separate additions summary was correct about source scope but was not an
effective attribute editor. Shared additions and child additions were absent from
the familiar cards even when already persisted. Assignment also created redundant
child additions for terms already inherited.

The editor now projects the current base cards plus shared additions plus local
additions into the same normal cards, merging by registry attribute/term identity.
Assign/remove immediately rerenders that list without resetting other form edits.
Term labels identify inherited, collection and product-local sources. Local terms
have removal controls on their card; new local groups retain move/remove controls.
Combined registered values are read-only text inputs: use the existing registry
picker/removal controls to edit these additions, not a whole-object replacement.
Unmodified legacy base cards retain their existing editing controls.

The projection is deliberately not serialization. Effective terms never become an
`attributes` replacement merely by being displayed. Shared saves own only the
shared source; child saves retain sparse `taxonomy_additions.attributes` keys.
Legacy explicit `attributes` replacement/clear continues suppressing parent
attributes/additions; the existing replacement toggle makes that intent visible.
Variation drivers and separately authored price/weight modifiers are unchanged.

No scanner, inheritance resolver, schema, database migration, or publisher change
was necessary. No production JSON was inspected or rewritten during development.
Thus we cannot certify whether the merchant's particular Lead Time selection was
already saved; the former code *could* save it despite not displaying its card.

## Existing Shopify contract (not a new capability)

Full Product Review & Sync already calls `shopify_attributes.assigned/preview`
using effective `product_document`, not just raw product overrides. It supports
registered informational groups as PRODUCT-owned `list.metaobject_reference`
metafields in `$app:tlc_attributes`, pointing to controlled term metaobjects.
Definitions, entries, assignment values, access and retained identities are
reviewed and verified separately under the existing signed approval workflow.
Native variant options are distinct. Internal registry attributes still publish
through this contract with storefront access NONE; public attributes use the
existing reviewed PUBLIC_READ policy. Neither automatically adds a theme block.

Mocked full review of four inherited groups plus sparse Lead Time proposes all
five missing assignments, then becomes matching after approved mocked writes and
readback. No publisher omission was reproduced. Missing scopes, invalid registry
identities, incomplete evidence or retained recovery states remain visible
blockers; inventory-only review explicitly defers informational metadata.
Unchanged assignments need no mutation. Check the informational registration and
assignment section, not only native Product Type rows. The existing bound remains
eight informational groups / forty assigned terms per product.

No new Shopify definitions, scopes, app release or theme work has been introduced.
Live storefront display and the merchant's remote metafields were not verified.
Native Product Type remains an independently reviewed top-level category label;
collections, category mappings, tags and shipping behaviour are untouched.

## Validation and acceptance

Focused tests cover immediate projection, alias/identity deduplication, removal,
source scopes, no effective-to-authored leakage, legacy replacement, all three
catalogue layouts, signed save/reopen/rescan, fresh database rebuild from copied
JSON and the existing mocked reviewed informational publication. Bounded tests
retain Product Type, modifiers, Shopify publishing, package assignments, Orders,
customisations and authentication checks. The offline Docker harness extends the
existing two-worker upgrade/restart chain with inherited and local informational
JSON, while retaining package IDs, Orders line identities and recovery receipts.

On Unraid, first back up existing appdata/catalogue and deploy the immutable
`informational-attributes-<commit>` image only after merchant approval. No new
mount or migration is required (head remains `0018_orders_webhooks`).

1. Open an individual Product Override. Confirm the shared four groups appear.
2. Assign Lead Time → 3–5 Days. A normal Registered / Informational card should
   appear immediately with variations unchecked and product-local provenance.
3. Add another Material term. There must still be one Material card with both
   terms. Assigning the same or an inherited term again must not duplicate it.
4. Save Metadata, reopen and complete the normal affected scan. Confirm the
   shared JSON is unchanged and the child contains only its local additions.
5. Separately test Shared Collection Data with its normal affected-products
   confirmation. A sibling with no explicit replacement must inherit the change.
6. Open full Shopify Review & Sync. Missing informational assignments should be
   reviewed through the existing registration/assignment section; matching ones
   need no write. Inspect explicit blockers or inventory-only scope if omitted.
   Review is read-only; do not confirm live writes during acceptance unless
   separately intended and approved. Do not expect storefront display to appear
   automatically.
7. Repeat assignment/removal and saving at mobile width. Existing source-level
   replacement records stay intentional: do not toggle replacement just to add
   an informational term.
