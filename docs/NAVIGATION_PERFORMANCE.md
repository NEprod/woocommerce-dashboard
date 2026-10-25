# Focused navigation performance correction

Baseline: `aa3a0c9e7c2d38dbe1345a036e773e99d85c9a21`, develop.

## Implemented: registry reuse in two read-only views

Dashboard and Products repeatedly resolve effective product metadata. Each
organisation merge called `load_configured_registry`, securely opening, parsing
and validating the same registry again. At 1,000 products this meant 4,024 reads
for Dashboard and 1,300 for Products data.

`taxonomy_registry.registry_read_view` now gives only `build_dashboard_data`
and `build_products_data` an immutable, lazily loaded registry result. It exists
only during that GET builder and is discarded in `finally`. Nested builders
share the result; concurrent requests do not. Errors are reused consistently
within the view, never replaced by an earlier successful snapshot.

There is no persistent cache, new worker, asynchronous rendering or schema
change. The next request reopens and validates the registry. Editor saves,
Shopify reviews/mutations, scanner and other readers are not opted in and retain
their existing fresh-read behaviour. Secure path/symlink checks are unchanged.

## Not implemented: page-only global image checking

The existing Products `Needs attention` card is a **catalogue-wide** unique-product
count, including missing source images. Its image filter must return the same
affected records regardless of pagination. There is no authoritative persisted
safe-image availability projection that can replace those live checks.

Limiting inspection to 50 displayed records would hide off-page image-only gaps.
Using nonempty database image URLs would also falsely accept missing, corrupt or
unsafe sources and miss discoverable local originals. Neither is acceptable.
The new off-page regression test protects the existing exact count and filter.

Image scope therefore remains unchanged. Completing that optimization requires
separate agreement about global-summary freshness or semantics; no new cache,
background job, metadata format or partial count was introduced here. Products
still benefits substantially from the registry correction while inspecting the
same images through the same resolver.

## Before/after benchmark

Same isolated audit generator and product-layout mix as the original audit:
100/500/1,000 products, 128/608/1,208 variants, 15/63/123 collections, valid registry,
shared JSON and sparse overrides, real tiny image fixtures, 840 synthetic retained
operation rows. Native M1 Mac, Python 3.13, file-backed SQLite; not Unraid/Docker
performance measurements. Shopify HTTP blocked. Each timing is median of three
warm requests; separate template-cold requests and profiles were also collected.
Before and after runs were sequential, not concurrent workloads.

| Products | Dashboard original audit ms | Fresh before → after ms | Reduction | Products data original audit ms | Fresh before → after ms | Reduction |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 1391.9 | 1142.8 → 352.1 | 69.2% | 1039.5 | 1160.7 → 276.7 | 76.2% |
| 500 | 5908.7 | 5880.1 → 1528.8 | 74.0% | 2024.3 | 2002.0 → 505.9 | 74.7% |
| 1000 | 10922.0 | 11422.2 → 3010.7 | 73.6% | 3307.5 | 3535.7 → 823.0 | 76.7% |

Products HTML remains small: 3.9 → 3.4 ms, 4.0 → 4.3 ms and 5.8 → 4.9 ms.
The data response, not the initial HTML shell, controls when rows become ready.

| Products | Dashboard registry loads | Products data registry loads | Dashboard SQL count | Products data SQL count |
|---:|---:|---:|---:|---:|
| 100 | 424 → 1 | 400 → 1 | 841 → 418 | 727 → 328 |
| 500 | 2024 → 1 | 800 → 1 | 3693 → 1670 | 1129 → 330 |
| 1000 | 4024 → 1 | 1300 → 1 | 7259 → 3236 | 1633 → 334 |

Image-context inspection counts are deliberately unchanged:

| Products | Dashboard calls / distinct product folders | Products data calls / distinct product folders |
|---:|---:|---:|
| 100 | 106 / 100 before and after | 150 / 100 before and after |
| 500 | 506 / 500 before and after | 550 / 500 before and after |
| 1000 | 1006 / 1000 before and after | 1050 / 1000 before and after |

These count image source-context resolutions and unique product IDs, not every
physical disk access or decoded gallery image. The 50 visible rows account for
the additional Products calls; recent products account for Dashboard extras.

Peak process RSS, including fixture creation and profiling, was 157.5 → 162.8 MiB,
140.2 → 179.0 MiB and 171.3 → 170.1 MiB. These independent-process peaks vary and
do **not** demonstrate a memory reduction or predict production RAM usage.

## Validation and limitations

Focused tests reproduce repeated registry reads before the correction and cover
one read per opted-in page, immutable snapshots, fresh registry edits/errors on
later requests, concurrent-request isolation, nested/error cleanup and exact
uncached-versus-reused catalogue results with clocks held fixed. Authored JSON
bytes are unchanged by these views.

Bounded regressions cover Dashboard health, listings/filtering/pagination,
safe images, registry security, organisation inheritance, Product Editor saves,
Shopify reviews and token-rotation safeguards, and operations reporting. The
existing offline Docker smoke chain covers upgrade, two Gunicorn workers,
restart/persistence, products, packages, orders, customisations and retained
operation/recovery evidence; the new smoke adds registry-read budgets.

Two pre-existing Products assertions fail on the untouched baseline too:

- `test_products_javascript_defines_mobile_and_accessible_expansion_hooks`
  expects an obsolete `publishing_intent_label` JavaScript reference.
- `test_products_api_groups_collections_and_reports_genuine_counts` expects
  the old two-key price response and includes a missing variation in its range.

They are not silently fixed, xfailed or deleted. The bounded run excludes these
two named baseline failures; all remaining outcomes are reported separately.

Candidate results: 266 bounded Python checks passed, with those two independently
reproduced baseline assertions deselected; 28 focused JavaScript checks passed.
Twenty isolated Chromium navigations (five pages, cold/warm, desktop/mobile
width) returned 200 with no overflow or long tasks. Products rows were ready in
852–883 ms at 1,000 products; this is local Chromium, not physical iPhone Safari.
The measured baseline/after GETs and retained browser request samples recorded
zero SQL writes. Docker upgrade/restart results are reported with the final
image revision after the build.

No source JSON, inheritance rules, image discovery/publication, Shopify sync,
Orders/shipping/authentication logic, database schema, layout, CSS or branding
was changed. No live Shopify or Unraid action was performed.

## Minimal Unraid acceptance

After approval, retain the current image reference and back up appdata using the
existing procedure. Select the verified immutable `navigation-performance-...`
image reported with this release, retaining all current mounts/settings. Do not
reset the database or scanner markers; no new migration is required.

Compare Dashboard and Products while idle. Check catalogue/issue totals against
the previous release; inspect an off-page image-only issue via its filter. Open
an inherited Product Override and a read-only Shopify review; verify unchanged
values and images. A legitimate registry edit should appear on the next page
request; never edit the registry merely to run a benchmark. Retain normal signed
approval before any Shopify write. Actual Unraid timings remain to be measured.
