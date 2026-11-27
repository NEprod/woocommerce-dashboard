# Shopify product URL handles

Product SEO titles now use the same resolved product/folder + collection title
as the normal product title when the product has no nonblank SEO-title override.
Collection-wide SEO copy no longer replaces a child product's SEO title.
Explicit product SEO titles (including single-scope root products) still win.
Generated SEO titles are limited to 70 characters; the normal product title is
unchanged. Meta descriptions and saved catalogue JSON are unchanged. The normal
review shows the exact SEO title before writing it.

New simple/variable parents use the explicitly authored SEO `handle`, or a
lowercase ASCII slug derived from the final customer-facing product title.
Decorative punctuation and spaces become single hyphens. Non-ASCII-only titles
fall back to a product/SKU handle. Title handles are limited to Shopify's length
bound. When another product occupies a generated title handle, a stable SKU
suffix is tried; a second collision blocks publishing. Explicit handles never
receive a silent suffix. Incomplete or ambiguous lookup evidence blocks writes.

New drafts have readable URLs immediately. Draft status and sales-channel
activation remain separate. Creation correlation still uses the retained nonce,
attempt metafield and exact Shopify identities; the URL is not the sole identity.
Interrupted creation uploads preserve their retained handle, and uncertain
creates still require reconciliation rather than another create.

Verified products with an exact legacy `tlc-draft-<32 lowercase hex characters>`
handle receive a clean suggestion in the normal full Review & Sync. The update
targets the retained product ID and sets `redirectNewHandle=true`. Variant IDs,
SKUs, media and prices are not recreated because of a URL correction. Existing
readable handles stay unchanged when titles change unless a new handle is
explicitly authored. Inventory-only and other restricted operations do not
include URL corrections. The resolved handle is bound into manual/automatic
execution proofs; changing availability after review invalidates the proof.

Old comparison fingerprints are invalidated by a handle-policy revision. Legacy
identities are shown as Update Available pending fresh read-only comparison.
The existing automatic publisher checks a bounded set of retained jobs on its
normal unrestricted run and queues legacy URL policy updates for ACTIVE local
intent. Its configured enablement, scan authority, exclusions, leases and
recovery gates remain mandatory. Draft-intent products remain paused according
to the existing policy and can be corrected manually. No task or live syncing
is enabled by installation. Derived-cost checkpoints may advance to the new
policy only when the exact prior source basis still matches; source edits never
borrow earlier scan authority.

Storefront/category collections already use their taxonomy slugs; that publisher
is unchanged. Internal attribute-browse collections deliberately use stable
hashed `tlc-...` handles; those identities are not draft placeholders and are
not renamed. No automatic collection URL migration is included.

Shopify reference: https://shopify.dev/docs/api/admin-graphql/latest/input-objects/ProductUpdateInput
(`handle` and `redirectNewHandle`). Tests use mocked Shopify endpoints only.
