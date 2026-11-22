# Taxonomy collection artwork and sales channels

In **Taxonomy → Storefront collections**, edit a definition and choose a JPEG,
PNG or WebP image (up to 25 MB). The existing image optimiser preserves aspect
ratio, limits the longest side to 1600 pixels and strips metadata. **Save
collection** saves the local definition and image reference together in one
action, then reopens the editor with the saved image. Removal and Advanced JSON
still use the existing review/confirmation flow. Categories do not require artwork.

Images are immutable, content-addressed WebP files beneath
`/app/instance/taxonomy-images`; `registry.json` retains an optional
`image_sha256` reference only for storefront collections. Existing registries
remain valid. Back up the registry and persistent instance storage together.
Cancelled reviews can leave an unreferenced image; no automatic deletion or
existing-image migration is performed. Image retrieval requires dashboard login.

In **Taxonomy → Shopify Sync → Publish collections · images & all sales
channels**, select up to 20 categories/storefront collections. Create or
explicitly link missing Shopify mappings using the existing workflow first.
Review shows every discovered APP-catalog sales channel and any artwork update.
Explicit confirmation uploads the image through Shopify's staged upload flow,
publishes the collection to all reviewed channels, and independently reads back
both results. Existing published channels and images without a local assignment
are preserved. Removing a local image assignment does not delete Shopify artwork.

Collection creation/mapping remains separate from live publication. Saving local
taxonomy or loading a page does not contact or change Shopify. Product prices,
product publication, inventory, memberships, menus and Shopify app permissions
are not changed by this action. Required scopes are `read_publications`,
`write_publications` and `write_products`; configured image-upload permissions
must also allow staged uploads. No permission is granted automatically.

Changed source/identity/channel evidence invalidates a review. Failures retain a
partial operation rather than reporting success or retrying blindly. Open a new
review to inspect current remote state; successful prior channel/image updates
are detected and skipped. There are no live Shopify writes in automated tests.
