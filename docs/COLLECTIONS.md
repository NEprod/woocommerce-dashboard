# Collections Workspace

Phase 2 Milestone 6 exposes collections as first-class catalogue entities at
`/collections`. The workspace is authenticated, read-only, and derived from the
existing scanner projection. It is not a filesystem manager and does not add
collection creation, deletion, rename, move, or bulk mutation operations.

## Identity and authority

The existing `Collection.id` is the safe route identifier. Portable
`source_relpath` and `shared_json_relpath` values identify catalogue provenance;
absolute runtime paths are never rendered. The filesystem catalogue remains
authoritative, collection-level `product_info.json` remains the authored shared
metadata source, sparse product override files remain product-owned, and SQLite
remains a scanner-generated projection.

The visible collection title is the basename of that portable collection path.
Nested collections therefore display their final directory component, while the
full relative path remains provenance and disambiguates duplicate basenames.
`product_info.json["title"]` is a shared **product** title default, not a
collection title. Editing it can update inheriting products through the existing
workflow, but it does not rename the folder, change `Collection.id`, change the
route, or create another collection. Missing shared product titles remain a
metadata-health issue. Historical rows without safe portable identity use their
existing collection name and then the controlled `Untitled collection` fallback.

## Browser

The browser aggregates projected product and variation counts, local catalogue
lifecycle, resolved publishing intent, override counts, metadata health, image
coverage, and last product updates. Search, filters, sorting, and page sizes of
25, 50, or 100 are server-backed. Ordinary image diagnostics load only products
for the rendered page; an image filter or issue sort performs the broader check
needed for an exact result.

Collection-name search and sorting use the shared folder-derived display title;
search may also match portable provenance and the shared product title. Products
grouping and filtering, Product Detail, Collection Detail, and Collection
Metadata Editor all use the same resolver so their collection labels cannot
drift.

Representative imagery is deterministic: the first valid genuine parent image
from the ordered eligible products, then another parent candidate, then the
first valid variation source only when no parent source is usable, then the
project-owned placeholder. Images use the authenticated opaque catalogue route;
`/output` is never a display dependency.

## Collection Detail

`/collections/<id>` summarizes the shared source, validation state, collection
type, SKU prefix, taxonomy and variation configuration, SEO completeness,
product/variation counts, local lifecycle, resolved publishing intent,
overrides, and image coverage. Its affected-product list is paginated and links
to Product Detail. Editing continues through the existing Collection Metadata
Editor; no second editor is embedded.

Catalogue state describes the local scanner lifecycle. Publishing intent is the
resolved future WooCommerce intent (`live: true` means Published intent and
`live: false` means Draft intent). Neither view claims current remote publication.

Recent activity is a bounded, sanitized view of matching collection operations
plus exhaustive full/reconstruction operations. The Operations redesign remains
Milestone 7.

## Image and Parent-directory contract

Image coverage uses persisted `ProductAsset` provenance and ordered
`ProductImage` records. Parent and variation ownership remains separate. The
reserved Single Variable Parent directory is recognized case-insensitively
(`parent/`, `Parent/`, `PARENT/`, or mixed case), while physical casing remains
intact in portable references. Multiple variants remain a controlled ambiguity
error, and the reserved directory is excluded before attribute interpretation.

No image binary, output path, or new collection-thumbnail field is stored.
Woo-facing URL generation, output copying, marker state, reconstruction, and SKU
behavior are unchanged.
