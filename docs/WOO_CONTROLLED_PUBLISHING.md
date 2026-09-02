# Controlled Woo Publishing

Phase 3 Milestone 4 is the first intentionally narrow WooCommerce write path.
It publishes only one to ten explicitly selected parent products from a current
Milestone 3 preview. There is no catalogue-wide publish action.

## Confirmation and authority

Catalogue files remain authored truth. The final confirmation regenerates the
preview with the same mapping code and refuses execution unless its complete
digest, selected store fingerprint and eligible actions still match. It shows
the selected products, create/update/no-change counts, taxonomy and variation
work, image URLs, relationship count, warnings, blockers and bounded write
estimate. Products with Published intent require a second explicit live-state
acknowledgement. Starting the operation is CSRF-protected and uses the existing
process-local mutation lock.

## Two passes

Pass 1 resolves exact category, tag, global attribute and term identities,
creating only unambiguous missing entries. It then creates or updates parents
and variations in dependency order. Each write is followed by a GET that checks
the reviewed managed fields. Store-scoped Woo IDs and digests are persisted only
after verification; an exact-SKU conflict is never silently linked.

Taxonomy resolution is resolve-before-create and resume-safe. Categories, tags,
global attributes, and attribute-scoped terms reuse one exact compatible remote
identity; a conflicting or ambiguous authored slug blocks publication. Woo's
`pa_` prefix on global attribute slugs is treated as a representation detail,
not a different authored identity. A successful create response supplies the
first candidate ID, followed by bounded direct verification and collection
reconciliation. If existence remains uncertain, the retained remote object is
reported as recovery-required and is never blindly recreated on Safe Resume.

Pass 2 resolves the ordered authored cross-sell and upsell target SKUs to
verified IDs for the same store. It applies and verifies those ID lists without
changing relationship JSON. A target without a safe verified ID remains a
visible pending relationship. Direction and authored order are preserved.

Stored final website image URLs remain catalogue-authored references. Publish
Preview performs a bounded authenticated GET against `/wp-json/wp/v2/media`,
narrows candidates by final filename, and accepts only one exact normalized
`source_url` match on the configured store. The reviewed payload uses the
existing WordPress attachment `id`, never `src`. Parent/gallery order and
variation ownership are preserved. Missing, ambiguous, cross-store, or
unreachable media identity blocks that product; controlled publishing never
falls back to importing the URL. Identity is revalidated before a write and
post-write verification compares returned attachment IDs. No media binary is
uploaded, converted, read from `/output`, deleted, or stored locally.

When permitted, the configured Woo default product category is read from the
bounded `wc/v3/settings/products` response. If local categories are intentionally
empty and Woo returns only that verified default ID, comparison treats the two
states as semantically equal without altering authored JSON. Explicit local
categories continue to require exact resolved Woo category IDs. Where that
setting is not exposed, one authenticated, bounded Store API product read may
identify the single default category Woo deliberately omits from the public
category list. Any ambiguity remains a visible managed difference.

Product reads use edit context where Woo supplies raw managed rich text. The
authored shortcode source remains the write payload and catalogue truth. If a
store returns rendered content only, comparison recognizes only the supported
`cg_accordion` wrapper and compares ordered titles and structured inner content;
changed words, order, links, or list content remain real differences. No
shortcode is executed and rendered HTML is never written back locally.

Woo product and variation dimensions use one shared payload contract. Authored
catalogue values remain unchanged, while preview generation canonicalizes
`length`, `width`, and `height` as plain decimal JSON strings (using an empty
string for an absent dimension). The same normalization is applied to managed
remote comparison, preventing numeric/string representation alone from causing
an update. Builder version `phase3-m4-taxonomy-reconcile-v1` makes earlier
comparison plans stale. The publisher rejects numeric or non-canonical dimensions and
any image `src` before issuing a write request.

## Failures and recovery

WooCommerce cannot provide one transaction across taxonomy, products,
variations and relationships. The operation therefore records honest bounded
per-product progress and keeps already verified successful IDs. A transport
failure after a mutating request triggers exact-ID or exact-SKU reconciliation
before retry can proceed. Ambiguous outcomes become recovery-required; they are
never blindly recreated and no compensating DELETE is issued. Resume regenerates
the original preview and refuses changed local state, changed identity state or
a changed configured store.

Operation history retains digests, counts, stages, verified IDs, safe action
labels, pending relationship summaries and sanitized failures. It never retains
complete payloads, raw responses, credentials, headers, cookies or full route
schemas. Discord receives one bounded terminal summary and is non-fatal.

Stage messages are driven by actual verified/skipped counters. Every selected
parent receives a bounded operation item before taxonomy work, so a dependency
failure remains visible without claiming that parents, variations, media, or
relationships were verified.

Operation Detail uses a server-normalized publish result for queued, running,
terminal, recovery and older historical records. Empty in-progress summaries
therefore render zero/unknown values safely, and the result panel refreshes once
when live polling observes the terminal transition. A failed refresh leaves an
explicit manual-refresh action; it never restarts the operation.

For a Woo REST error, the publisher may retain only a bounded diagnostic made
from the response's documented `code`, `message`, safe HTTP status, and bounded
`data.params`/`data.details` field messages. The diagnostic also records the
request method, publishing stage, local SKU/title when available, object class,
retry classification, remote-verification state and timestamp. Raw bodies,
payloads, response headers and URLs are excluded. HTTP 400 is a confirmed
payload/metadata correction failure, not an uncertain write. Authentication,
permission, rate-limit, transient and transport-uncertain failures retain
different guidance; transport-uncertain writes still require reconciliation.
Older operations without structured diagnostics retain a controlled generic
fallback.

## Request boundary

Discovery, media identity, settings, and preview requests remain GET-only. The publisher-only client allows
authenticated same-origin POST, PUT and PATCH to the selected Woo namespace.
DELETE is forbidden. Cross-origin redirects are blocked and mutating redirects
are not replayed. TLS verification, response/body bounds and diagnostic
redaction remain mandatory.

## Manual acceptance

Automated tests use fictional mocked Woo responses only. For a first real-store
acceptance, select one fresh Draft-intent Simple product whose final WebP URL
already exists in the configured store's Media Library. Generate a new preview,
confirm it shows an existing attachment ID and an `id`-based payload, then open
Final Confirmation and verify the store host and digest. Publish once and confirm
no `-1.webp` duplicate appears, the operation records one verified Woo ID, the
remote product is Draft, and a new preview becomes `no_change`.
Repeat separately with one controlled variable product. Review recovery state
before any retry and do not use production products for initial acceptance.
