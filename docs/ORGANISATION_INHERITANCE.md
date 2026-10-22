# Organisation inheritance and native Shopify Product Type

The authoritative files remain the existing collection and product
`product_info.json` documents. No database migration or catalogue rewrite is needed.

## Normal product additions

Collection and Product Override JSON use the same four normal fields:
`categories`, `storefront_collections`, `tags` and informational `attributes`.
These combine additively. A child stores only its own assignments, never copies
the collection's inherited values. For example:

```json
{
  "categories": ["Cards > Anniversary Cards"],
  "storefront_collections": ["Anniversary Cards"],
  "tags": ["Anniversary"],
  "attributes": {
    "Occasion": ["Anniversary"],
    "Recipient": ["For Wife", "For Husband"]
  },
  "shopify_product_type_key": "cat-cards"
}
```

Inherited terms stay first, followed by local additions in authored order.
Matching names/aliases resolve to registry identities before deduplication. New
groups and terms selected through the registry use their canonical readable
values in normal JSON. Registry identities and aliases prevent duplicate cards
and assignments. Unregistered historical values remain visible and preserved.

Remove a product-addition row to remove only that local reference. Removing all
local additions restores inheritance. This cannot delete a parent's terms.
Shared collection informational assignments use the same representation. An
informational addition cannot edit a variation driver. For legacy implicit
variation axes, the effective resolver preserves the existing axes before
adding new informational groups; this does not rewrite authored JSON.

## Compatibility and scope

Informational attributes and collection lists no longer have a full-object
replacement mode. Empty child lists/objects contribute nothing; they do not
erase collection values. Remove a parent assignment by editing Shared Collection
Data, not by clearing a child. This deliberately corrects the earlier replacement
interpretation of these fields, following the merchant's additive contract.

Existing `taxonomy_additions` remains readable. Reading, opening or rescanning
does not rewrite source files. Editing a registry assignment folds only locally
owned compatibility entries of that field into its normal field on Save Metadata;
unrelated fields and the parent remain unchanged. Unresolved compatibility keys
block the touched edit rather than silently dropping data. Saved edits use the
existing revision check and source backup. No bulk conversion or migration runs.

Variation-driver options retain their existing explicit/implicit contract and
separate advanced controls. Adding information cannot create another variation
axis. Image axes, modifiers, prices, weights, shipping and all other override
semantics are unchanged.

Registered informational attributes use the existing reviewed Shopify JSON
metafield publication, definitions/access checks and readback. This correction
changes the effective input, not the publisher. Storefront rendering is separate.
Collection mappings and existing remote identities retain their current safety
checks. Tags retain the existing reviewed full-list contract, including its
warning that publishing can replace remote-only tags; saving locally never does.

## Product Type

`shopify_product_type_key` is one stable top-level category key. The editor offers
only non-deprecated parent categories from the authoritative registry, not free
text, subcategories, Shopify standard categories or variant types. The scalar
inherits from the collection; a product override selects one other parent.
An unresolved saved key is retained and shown for review, not replaced with the
first dropdown option. Safe local edits do not require Product Type.

The publisher resolves the key to the parent's current display name and uses the
existing native Shopify `productType` field. Existing read queries, signed
Review & Sync, create/update mutations and independent readback already support
that field. A parent rename changes the comparison fingerprint and invalidates
stale approval. Matching values need no mutation. No new API version or scope is
introduced. Missing/deleted/subcategory identities block only publication of the
invalid authored configuration, never invent a value.

Old `shopify_product_type` labels remain compatible in JSON and are shown as
retained legacy values, without a free-text guided input. An explicit historic
child label continues to override a newly selected parent key until that child
chooses a registry-backed key. Unspecified Product Type preserves remote state.
Removing a key restores inheritance/legacy behaviour; it is not a remote-clear
operation. Remote Product Type clearing is not added in this milestone.
The save endpoint also restricts new selections to registered parents and new
type authorship to the registry-key field. Unchanged legacy labels or unresolved
historical keys do not block unrelated local edits.

TLC category assignments, multi-collection memberships, tags and optional
Shopify standard-category mappings remain independent. The existing reviewed
taxonomy workflow still decides whether a collection may be created or joined;
this change creates no automatic remote collections and removes no remote
memberships. Product Type is never sent on variant payloads.

## Acceptance

Use a backed-up product and collection: add an informational term, category and
collection in Product Override; save/reopen/rescan. Confirm the parent JSON is
unchanged and inherited values remain. Select Product Type from a parent
category, then inspect Review & Sync. Only merchant-approved publication writes
Shopify. Verify the native type after publication. Check one variable product's
existing SKUs, options, prices, weights, dimensions and packages remain unchanged.

## Regression evidence for the additive correction

The Anniversary source supplied by the merchant had six Recipient terms in
`attributes` and Occasion in `taxonomy_additions`. The prior whole-object merge
suppressed the parent's informational groups (and shared additions) whenever that
child `attributes` object existed. The editor's replacement gate repeated that
ownership assumption. The four-field resolver and effective-card display now
agree on the additive contract; the publisher receives the resolved result.

Focused/bounded checks cover normal and compatibility JSON, all three product
layouts, shared-source byte preservation, local-only removal, driver/modifier
isolation, fresh database reconstruction, and mocked reviewed Shopify readback.
Desktop and 390×844 browser workflows use a disposable catalogue with all outbound
HTTP disabled. They exercise assignment, removal, Save, Reopen and Rescan, including
the six Recipient terms, inherited Lead Time and local Occasion. Registry dropdowns
are bounded to the mobile editor width. The separate theme browser harness's
missing Playwright package is an existing environment limitation; it is not
installed or changed for this release.

Run the network-isolated two-worker upgrade/restart chain with
`python3 tests/docker_organisation_smoke.py IMAGE` against the built release image.
It retains Orders, separate personalisations, package IDs, operation/recovery
evidence and normal sparse organisation JSON across restart. No schema change is
needed. Deploy only after backing up existing appdata and authoritative JSON;
there is no automatic Unraid deployment or Shopify mutation.
