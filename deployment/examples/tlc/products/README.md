# Curated TLC catalogue references

These supplied real-format folders are the preferred TLC integration/regression
references alongside `../registry.json`. They are reference repository data,
not runtime catalogue, Intake or startup defaults. Nothing copies them into a
mount automatically. Tests must use temporary copies for scanning, markers,
output or metadata edits; never mutate these source examples to satisfy a test.

## Supplied inventory and compatibility — 2026-09-06

The user replaced the initial reference set with six current-format collections.
Each contains one collection-level `product_info.json`; 16-Bit Pixel Art Cards
also contains a sparse override at `Girls Birthday Level Complete/product_info.json`.
Simple, regular Variable Collection and Single Variable contracts are represented.

| Collection folder | Supplied contract / coverage | Current scanner result |
| --- | --- | --- |
| 3D Christmas Ornament Set | Current Single Variable; Style and Build Type image attributes; modifier-driven combinations | 1 Variable parent + 14 variations |
| Personalised Hero Print | Single Variable; Style and Size image attributes, Parent and nested Hero A/B/C image folders | 1 Variable parent + 9 variations |
| Adorable Dog Ornaments | Variable Collection; shared attribute combinations and four per-product image folders | 4 Variable parents + 16 variations |
| Naughty But Nice Ornament Set | Simple; shared metadata and four per-product image folders | 4 Simple products |
| 16-Bit Pixel Art Cards | Simple; three product folders and one sparse override | 3 Simple products |
| Lego Brick Cards | Simple; shared metadata and two product folders | 2 Simple products |

The replacement dataset passes the current scanner on a temporary copy: **15
parent products (9 Simple, 6 Variable), 39 variations, 54 rows**. It contains 7
product_info.json files and 97 source images. Hashes prove tests did not mutate
source files. The earlier three legacy CSV-style definitions failed validation;
the user supplied replacements, not an application compatibility workaround.
Scanner interpretation was not loosened and no M5.3 semantics were added.
Finder `.DS_Store` files are incidental and ignored by Git.

Categories remain exactly as supplied and are not claimed to match the registry;
the user identifies them as future controlled-assignment work. Do not normalize
or repair these values merely to pass M5.2. There is no missing current product
type/override fixture. After the M5.3 contract is approved, add a reviewed example
covering mixed informational and variation-driving attributes in that versioned
contract; do not fabricate alternate production JSON shapes. Informational/navigation
attributes alongside explicit product-level variation designation remain future
versioned resolution work, not a shape assumed by these examples today.

## Reference priority

1. Actual implemented production contract.
2. `deployment/examples/tlc/registry.json`.
3. These curated real-format product examples (with compatibility limitations above).
4. Temporary copies when mutation is required.
5. Small fictional unit fixtures for isolated edge cases.

An incompatible example is evidence to report and review, not permission to
rewrite canonical data or change scanner behaviour in an unrelated slice.
