# Woo Publish Preview

Phase 3 Milestone 3 provides an authenticated, read-only planning workspace at
`/woocommerce/preview`. It answers what a later controlled publisher would
create, link, update, leave unchanged, defer, or block. It does not publish,
upload, create taxonomy, or persist an exact-SKU link candidate.

## Authority and request boundary

Catalogue JSON remains authored truth. Scanner rows, products, variations,
images, taxonomy, and relationship edges remain projections. The preview reads
those projections and the configured store, and builds its detailed plan only
in bounded process memory. Opening the workspace performs no Woo request;
generation is an explicit authenticated POST protected by CSRF.

The existing Woo client remains GET-only. Preview reads are limited to exact
stored product IDs, exact SKUs, bounded taxonomy pages and, for an already
resolved variable parent, its bounded variation list. It never downloads the
complete Woo product catalogue or image bodies. Operation history retains a
small summary and digests, not product payloads, remote bodies, route schemas,
credentials, headers, cookies, or full sensitive URLs.

## Scope and classification

Users first estimate one product, selected products, one collection, or all
active products locally. Large scopes require a second explicit confirmation.
The resulting plan classifies each parent as:

- `create`: no stored ID and no exact remote SKU;
- `link_candidate`: one compatible exact-SKU remote product, not persisted;
- `update`: a verified stored identity differs on managed fields;
- `no_change`: managed local and remote fields match;
- `blocked`: identity, type, taxonomy, relationship, SKU, or metadata safety
  prevents an automatic future action;
- `recovery_required`: a stored current-store identity is missing or conflicts.

Matching never uses a title or fuzzy SKU. Identity rows are scoped by a
non-secret fingerprint of the normalized configured store plus its hostname.
Mappings from another store are displayed as conflicts and are not reused.

## Store-scoped identity projection

Revision `0007_woo_sync_identity` adds `WooProductIdentity` and
`WooVariationIdentity`. They retain stable local identity, current-store Woo
IDs, verification/sync state, and last-successful local/remote digests needed by
a later publisher. They are application integration state, not catalogue
authorship, and do not overload scanner-owned `Product.woo_id` or
`Variation.woo_id`. Preview generation itself does not create or update these
identity rows.

## Payload and two-pass plan

The builder maps only the explicit managed-field contract: product identity and
type, publishing intent, content, price/sale dates, dimensions, stock fields,
taxonomy references, attributes, and ordered final image URLs. Variable plans
include existing projected combinations, SKUs, attributes, commercial fields,
and variation-owned images. Unsupported fields and Woo-generated timestamps,
permalinks, and metadata are excluded from comparisons.

Taxonomy uses exact normalized slugs and reports existing, create-required, or
ambiguous dependencies. Images retain parent/variation ownership and order and
use stored final website URLs; no output path, binary, conversion, upload, or UI
fallback becomes payload identity. Ordered local relationship target SKUs are
translated only when a verified current-store Woo ID already exists. Targets
included in the same plan remain `pending_pass_2`; SKU strings are never placed
in Woo `cross_sell_ids` or `upsell_ids`.

The future execution shape is deliberately explicit:

1. Pass 1 resolves/creates taxonomy, parents, variations and media and would
   persist verified IDs only after successful writes.
2. Pass 2 translates ordered local relationship identities to those verified
   Woo IDs and applies cross-sells/upsells.

Milestone 3 displays this shape but executes neither pass.

## Digest and staleness

The deterministic preview digest covers scope, stable product/variation
identities and SKUs, resolved managed values, lifecycle and publishing intent,
taxonomy and terms, stock, image ownership/order/URLs, ordered relationships,
current-store identity state, observed managed remote state, and builder/mapping
versions. It excludes operation IDs, timing and secrets.

Detailed plans expire with the bounded in-memory cache. Their safe operation
summaries remain. While a plan is cached, the UI recomputes its local state
digest and marks it stale when relevant local projection or identity state has
changed. A later publisher must regenerate the plan and compare its complete
digest; it must never execute a cached preview blindly.

## Operational limits

Preview uses the existing process-local operation lock and single-worker
runtime. Catalogue-sized tests cover 500 parents, 5,000 variations and 2,000
relationship edges with select-in batching, bounded exact Woo reads, bounded
operation state, and no per-variation or per-relationship query pattern. One
bounded Discord terminal summary reports counts and readiness only. It contains
no payloads, product lists, credentials, absolute paths, or raw responses.
