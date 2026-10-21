# Organisation inheritance and native Shopify Product Type

The authoritative files remain the existing collection and product
`product_info.json` documents. No database migration or catalogue rewrite is needed.

## Normal product additions

Registry pickers in Product Overrides now append stable references in a sparse
`taxonomy_additions` object. They do not copy the inherited attribute object or
collection list into the child. For example (use your actual registry keys):

```json
{
  "taxonomy_additions": {
    "categories": ["cat-birthday-cards"],
    "storefront_collections": ["range-spring"],
    "attributes": {
      "attr-occasion": ["term-mothers-day"],
      "attr-recipient": ["term-mum"]
    }
  },
  "shopify_product_type_key": "cat-cards"
}
```

Inherited terms stay first, followed by local additions in authored order.
Matching names/aliases resolve to registry identities before deduplication. New
groups and terms use their stable keys; names are resolved from the mounted
registry, not inferred or generated from labels. Registry storage must remain
available for these references. Unknown identities fail closed with a local
diagnostic; they are never silently substituted.

Remove a product-addition row to remove only that local reference. Removing all
local additions restores inheritance. This cannot delete a parent's terms.
Shared collection informational additions use the same representation. An
informational addition cannot edit a variation driver. For legacy implicit
variation axes, the effective resolver preserves the existing axes before
adding new informational groups; this does not rewrite authored JSON.

## Explicit historical replacement

Existing `attributes` objects and `storefront_collections` arrays retain their
historical explicit replacement meaning. Their labelled legacy controls and
Advanced JSON remain available. An explicit empty collection array still clears
the inherited local collection list; an explicit attributes object still replaces
the inherited object. Shared additions in that explicitly replaced field are not
reintroduced. New local additions may append to the explicitly replaced base.

Existing `categories` and `tags` lists remain additive, with deterministic
parent-first ordering rather than the old unordered set. Variation drivers,
image axes, modifiers, prices, weights and shipping configuration are unchanged.

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
