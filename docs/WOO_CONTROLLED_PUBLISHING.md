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

Pass 2 resolves the ordered authored cross-sell and upsell target SKUs to
verified IDs for the same store. It applies and verifies those ID lists without
changing relationship JSON. A target without a safe verified ID remains a
visible pending relationship. Direction and authored order are preserved.

Stored website image URLs are included in the reviewed managed payload. This
milestone performs no binary upload, conversion, output-folder read, or media
library deletion.

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

Discovery and preview clients remain GET-only. The publisher-only client allows
authenticated same-origin POST, PUT and PATCH to the selected Woo namespace.
DELETE is forbidden. Cross-origin redirects are blocked and mutating redirects
are not replayed. TLS verification, response/body bounds and diagnostic
redaction remain mandatory.

## Manual acceptance

Automated tests use fictional mocked Woo responses only. For a first real-store
acceptance, select one safe Draft-intent Simple product in a staging Woo store,
generate and review its Publish Preview, open Final Confirmation, verify the
store host and digest, then publish. Confirm the operation records one verified
Woo ID, the remote product is Draft, and a new preview becomes `no_change`.
Repeat separately with one controlled variable product. Review recovery state
before any retry and do not use production products for initial acceptance.
