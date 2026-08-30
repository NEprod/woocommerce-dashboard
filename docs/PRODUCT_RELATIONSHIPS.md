# Local Product Relationships

Phase 3 Milestone 2 manages cross-sells and upsells as authored catalogue JSON.
It does not contact WooCommerce, create a Woo payload, or invoke the scanner.

## Identity and ownership

The durable contract is an ordered SKU list:

```json
{"relationships":{"cross_sells":["TARGET-SKU"],"upsells":["PREMIUM-SKU"]}}
```

Simple and Variable Collection products own this block in their sparse product
override. A Single Variable collection owns it in the collection-root
`product_info.json`; no unsupported second override layer is invented. SQLite's
`ProductRelationship` table is a rebuildable index containing source product,
target SKU, nullable resolved target product, relationship type, and position.
Scanner-owned `Product.cross_sell_ids` and `Product.upsell_ids` are never edited.

If the new product-specific block is absent, projection falls back to resolved
legacy `crosssells` and `upsells`. A new block is a complete replacement; new
and legacy lists are never merged. The editor identifies legacy-derived values
until an intentional save establishes the new contract.

A missing target retains its SKU and appears as repairable rather than being
silently deleted. Database deletion/reconstruction recreates ordered projection
rows from the catalogue JSON.

## Validation and operations

Search is local. Self-links, duplicates, unknown additions, missing additions,
and direct reciprocal upsells are blocked. Archived, draft-intent, and incomplete
targets remain selectable with warnings.

Every mutation requires server preview and explicit confirmation. A one-product
save uses a complete staged document, validation, verified backup, atomic
replacement, and the established `.update` marker. Mutual cross-sells stage and
validate every document, record persistent recovery intent, create verified
backups, promote deterministically, and roll all promoted documents back if any
later step fails. Projection refresh happens only after every authored document
succeeds. Incomplete rollback remains recovery-required. Operation history and
one bounded Discord terminal summary contain counts, never target lists.

## Future Woo publishing

Publishing remains a later two-pass milestone:

1. publish products and persist returned Woo product IDs locally;
2. resolve each authored target SKU, translate it to the persisted Woo ID, and
   populate Woo `cross_sell_ids` and `upsell_ids`.

Grouped products, bundles, composites, and recommendation engines remain out of
scope.
