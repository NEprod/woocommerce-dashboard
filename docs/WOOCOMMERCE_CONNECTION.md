# WooCommerce Connection and Read-Only Discovery

Phase 3 Milestone 1 adds an authenticated `/woocommerce` workspace for bounded,
read-only store discovery. It does not publish products, upload media, modify
taxonomies, change store settings, or verify that the configured credentials can
write.

## Runtime configuration

Set these variables in the container runtime and restart the container:

```text
WOO_STORE_URL=https://shop.example.com
WOO_CONSUMER_KEY=runtime-secret
WOO_CONSUMER_SECRET=runtime-secret
```

`WOO_STORE_URL` must be an HTTPS store origin. A supplied `/wp-json/` suffix is
removed safely. Embedded URL credentials, query strings, fragments, unsupported
schemes, and malformed hosts are rejected. The Consumer Key and Consumer Secret
are environment-only secrets: the application does not save them in SQLite,
files, operation history, HTML, JSON, logs, or Discord.

The Unraid template masks both credential fields. Limit the key to the least
privilege appropriate for discovery. If WooCommerce reports a key scope, that is
shown separately from verified access; no write permission is inferred or tested.

## Test Connection

Opening the workspace never contacts the store. The authenticated, CSRF-protected
**Test Connection** action creates one bounded operation and performs only GET
requests. The request policy rejects POST, PUT, PATCH, and DELETE.

The test:

1. validates and normalises the store URL;
2. requests the public WordPress REST index;
3. discovers the highest registered `wc/vN` namespace;
4. issues minimal authenticated reads with `per_page=1` where supported;
5. records bounded capability and latency summaries;
6. emits at most one terminal Discord summary.

TLS verification is enabled. Connect/read timeouts, response size, redirects,
namespace lists, capability rows, and stored history are bounded. Redirects are
followed only on the configured origin; credentials are never forwarded to
another origin. Raw API indexes and response bodies are neither rendered nor
persisted.

The public `/wp-json/` discovery index has a separate 8 MiB decompressed-body
limit because plugin-heavy WordPress sites may register thousands of routes.
Ordinary WooCommerce and WordPress capability responses retain the smaller
1 MiB limit. Both policies stream in 64 KiB chunks and count the bytes produced
after HTTP decompression; `Content-Length` is recorded when valid but is never
trusted as the enforcement boundary. Missing, incorrect, chunked, gzip, Brotli,
and deflate transfer metadata cannot bypass the decompressed limit. Responses
are closed after success or abort, and the raw index is discarded immediately
after namespaces and relevant route methods have been summarized.

Required publishing reads are Products, Product categories, Product tags, and
Product attributes. A failure in those resources fails the health check. Later
resources classify Product variations and Attribute terms as variation-publishing
requirements and Media as a media-synchronisation requirement. Orders, Customers,
and System status remain future/diagnostic capabilities. A limitation in these
later or optional resources may produce **Connected with limitations** without
falsely failing the four current product-publishing reads.

Capability rows distinguish:

- **Read access verified** — a safe authenticated GET succeeded;
- **Write methods advertised by API** — route metadata only;
- **Write permission not verified** — no write execution occurred.

New connection operations also retain one bounded structured finding for each
limited later/optional capability. A finding contains only its stable key and
label, requirement class, discovered/read state, safe HTTP status, severity,
continuation flag, concise current/future impact, and recommended action. The
WooCommerce workspace, Operation Detail, safe operation log, and the single
terminal Discord notification use those same findings. Raw bodies, complete
route schemas, headers, cookies, credentials, and sensitive URLs are never part
of a finding. Earlier operations without structured findings show an explicit
historical-detail fallback instead of attempting reconstruction.

Operation history uses the established retention and redaction policies. It
stores only safe host/status/capability/latency summaries, not credentials,
cookies, authorization headers, raw URLs with query credentials, or the complete
REST index.

## Troubleshooting

- **Not configured:** set all three runtime variables and restart.
- **TLS failure:** correct the store certificate; certificate verification is not
  disabled by the application.
- **Authentication rejected:** verify the Consumer Key and Consumer Secret and
  their read access without pasting them into logs or support messages.
- **Woo namespace absent:** verify WooCommerce and its REST API are enabled.
- **Connected with limitations:** review the named limitation cards and capability
  table. Each finding explains whether it affects current publishing or only a
  later feature. Required product reads have still passed.
- **Rate limited:** wait before running another manual test. The application does
  not repeatedly probe or benchmark the store.
- **REST index exceeds the discovery limit:** a plugin-heavy site registered an
  unusually large route index above 8 MiB. The operation reports only safe byte,
  encoding, and endpoint-category diagnostics; it never stores the index body.

Later Phase 3 milestones may consume these discovered capabilities. This
milestone does not implement WooCommerce synchronization.
