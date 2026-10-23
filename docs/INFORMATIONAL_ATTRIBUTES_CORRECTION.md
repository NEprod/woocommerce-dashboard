# Informational attribute editor contract

The canonical storage and inheritance rules are documented in
[Organisation inheritance](ORGANISATION_INHERITANCE.md).

Shared Collection and Product Override JSON both use `attributes` for
informational assignments. Effective cards merge inherited and locally authored
groups/terms by registry identity, in deterministic parent-first order. A child
stores only its own values. Normal assignment never activates replacement mode.
Removing a local term preserves its inherited counterpart. Categories, tags and
storefront collections use the same additive ownership rule in their normal
top-level fields. There is no secondary storage model or conversion system.

Assigning Lead Time → 3–5 Days must immediately display a Registered /
Informational card with product-local provenance, alongside inherited Material,
Personalisation, Production Method and Style / Theme. Adding another Material
term updates one card, without copying Card into the child JSON. Save Metadata,
reopen and complete the normal affected scan; the parent JSON remains unchanged.
Repeat on mobile and verify the fixed Save Metadata bar remains accessible.

Variation-driving options retain their existing explicit/implicit contract and
separate advanced controls. Informational assignments cannot create new axes.
Product Type, modifiers, prices, weights and shipping are unchanged.

## Existing Shopify contract

Full Product Review & Sync calls `shopify_attributes.assigned/preview` using the
effective `product_document`. Registered informational groups use PRODUCT-owned
`list.metaobject_reference` metafields in `$app:tlc_attributes`, pointing to
controlled term metaobjects. Definitions, entries, values, access and retained
identities are reviewed and verified under the existing signed approval flow.
Native variant options remain separate. Internal attributes retain NONE
storefront access; public attributes use the existing PUBLIC_READ policy.
Storefront rendering is separate; this does not install a theme block.

Mocked tests verify all inherited and child-owned groups reach review, then
become matching after approved mocked writes/readback. No new Shopify mapping or
mutation is introduced. Missing scopes, invalid identities, incomplete evidence
and recovery receipts retain their existing safeguards. Opening the editor or
saving locally never writes Shopify.

No database migration or production JSON rewrite is performed. The merchant
manually removes obsolete source fields before deployment. Back up appdata and
catalogue JSON and use the immutable tested development image. Unraid deployment
and live Shopify publication remain merchant-controlled.
