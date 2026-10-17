# Orders repair and webhook delivery — M3A.9.5.5

## Verified boundaries

The existing Orders importer, order GIDs, line GIDs and purchase-time TLC snapshots
remain the commerce foundation. No order-writing action, product change, Discord
notification, catalogue scan or shipping activation is added. API2026-10 is retained.
Product native packed dimensions alone remain opt-in2027-01.

The historical16Oct22:37:49UTC API Requests=0 counter was unreliable: the old exception
handler reloaded progress without recording this slice's requests. A fresh live field
probe now establishes the current failure: minimal Order reads succeed, while
order.customer returns ACCESS_DENIED explicitly requiring read_customers, absent from
the token. Email, phone, shipping address and line properties succeed; no protected-
data approval rejection or schema error is reported. The earlier historical exception
itself was not retained, so that exact historical run is not independently proven.
The merchant approved only read_customers plus unreleased staging. Existing scopes,
app/extension identities, settings and complete-order validation remain unchanged.
Release and installation authorisation require subsequent separate approval.

Approved permission-only candidate staged on17Oct2026:
`tlc-orders-customer-access-20261017`,
`gid://shopify/Version/1170981650433` (inactive/unreleased).
Its sole configuration change is adding read_customers to the12existing declared
scopes. Remote readback confirms the existing app modules and theme extension are
retained. `tlc-orders-foundation-7679198d2b0c` remains active. No installation
permission grant is implied by staging. After separately approved release, approve
any Shopify installation prompt, obtain a fresh token through the existing connection
flow, verify read_customers in granted scopes, then rerun the read-only Orders probe
and manual Sync Now. No Dashboard image change is needed for this scope correction.
No merchant Admin credential is available in development. CLI app-management access
is not an Orders token. Use the protected History diagnostic after deployment, or
`python -m app.orders_probe` inside the container. This diagnostic does not initialise
the application/database or run migrations; it reads one recent order sample and
prints only safe categories/phases/counts, never order/customer properties.

Fixed failure categories distinguish configuration, missing scope, authentication,
access denied, protected customer data, schema, rate limiting, network, pagination,
invalid response and persistence. Stale imports are counted separately. No remote
exception/message/body/variables are persisted. Authentication HTTP calls are separate
from physical GraphQL requests, including failed/throttled requests. A missing
read_orders grant requires existing installation approval, not a duplicate app.

## Startup/manual importer

The runtime supervisor queues startup work once before web workers start. Existing
SQLite task CAS/lease/fences ensure one owner; two Flask workers do not each import.
Orders and Products checkpoints are separate. Existing paused/failed page evidence
resumes; startup does not reset the window. Slice limits70requests/80seconds,
5orders(default1–10),10indexIDs,25lines/page,1000lines/order and10shipping services
remain. A backlog continues through paused slices, not a recurring API schedule.

First import uses SHOPIFY_ORDERS_INITIAL_DAYS(default30,bound1–60). Restart/manual
imports use the last complete checkpoint minus two hours, clamped to the normal
60-day access window. A fixed upper cutoff30seconds before run start allows indexing
settling; the next authorised catch-up covers that boundary. A checkpoint advances
only after complete pages/order graphs commit. Page failures keep prior successful
checkpoints and already imported orders. Strict revision readback and pagination
completeness remain. No read_all_orders or write_orders is requested.

Orders → Sync Now queues incremental work; Repair recent history explicitly rereads
the configured initial window, preserving existing history. It coalesces with active
work rather than altering another run. Current complete API reads reconcile known
pending-event order identities when their source revision is covered. Unknown/malformed
event identities require administrator investigation, not an automatic destructive
clear. Events received after an API read began cannot be cleared by that older read.
An older in-flight response cannot overwrite newer or equal-revision evidence committed
after its read began. There is no recurring five-minute Orders enqueue path, even if
an old database enabled flag is set. Products/sales scheduling is unchanged.

## Delivery architecture (implemented, inactive)

Shopify → dedicated public HTTPS gateway → encrypted durable event queue → private
Unraid outbound HTTPS consumer → encrypted SQLite inbox → existing order persistence.

The gateway is `app.order_webhook_gateway:create_gateway()`; it is NOT registered on
the Dashboard, launched by the container supervisor, or exposed through an extra port.
It has only `/webhooks/shopify/orders`, `/events`, `/ack`. It needs no Admin token,
Orders database, catalogue or customer-edit access. It is an event queue, not a second
Orders database. Host it separately only after merchant approval. Never route public
traffic to the private Dashboard/Unraid login or expose the existing Docker port.

HMAC-SHA256 checks the exact raw body using the existing app client secret, with
constant-time comparison. Pin shop domain, topic and API2026-10; require delivery ID.
Retain Event-ID/Triggered-At in the encrypted envelope. HMAC does not sign headers:
the configured single-store app association is additionally pinned; do not reuse this
private deployment for unrelated stores. No age-based rejection loses offline queued
events; durable delivery-ID dedupe and source updated_at control replay/staleness.
Same ID/different body or topic is rejected. Same order revision/different complete
content is held for reconciliation instead of choosing by arrival order.

Receipt commits before HTTP200. Transfer acknowledgement occurs only after durable
local inbox receipt, not after processing. Retry after a lost acknowledgement dedupes.
One leased private consumer pulls at most one encrypted event per15seconds, processes
up to10 local events, and never calls Shopify Orders API. Queue polling is NOT Orders
API polling. API/Discord clients are absent from the webhook processor. A queue outage
does not prevent processing already durably received events. Lease60seconds protects
the bounded transfer/processing; expired owners cannot commit order graphs.

Limits:2MiB webhook body;4MiB encrypted transfer;gateway60requests/second globally;
10,000 unacknowledged deliveries. Apply additional hosting-layer abuse controls and
monitor capacity. Queue full returns503, not an optimistic acknowledgement. Payloads
are encrypted with Fernet authenticated encryption; gateway bearer token>=32characters
protects transfer/ack. HTTPS certificate validation enabled, no redirects/proxy/netrc.
Payload ciphertext is purged after7days on the next receiver/consumer maintenance
activity; provision host-level retention maintenance for a suspended gateway/consumer.
Applied/stale payloads are erased immediately
locally, acknowledged gateway payloads erased immediately. Expired gateway payloads
transfer explicit metadata markers, never silently disappear; local UI requires
reconciliation. Gateway dedupe tombstones retained90days; local delivery metadata is
retained for audit. Provision a private0700gateway directory,0600database and encrypted
backups, no body/header logging. This is not an external exactly-once delivery guarantee.

## Payload coverage

Orders/create,updated,cancelled,paid use an explicit snake_case adapter, never pass
REST-shaped JSON off as GraphQL. Complete unchanged-quantity, no-refund/no-discount
snapshots map money bags, totals, shipping, contacts, statuses, properties and IDs
through the existing strict normaliser. Derivations are limited to demonstrably
unadjusted unit×quantity, zero discounts/refunds and unchanged shipping. Same SKU on
two line GIDs remains two historical answer sets. Product deletion does not erase lines.

Refunds/create alone is not a complete order snapshot. Edited/discounted/refunded,
missing properties, omitted financial/customer fields, unsupported status or incomplete
snapshots remain encrypted and marked Needs manual reconciliation. They NEVER clear
the last complete order graph or make an automatic API fetch. A later sufficiently
complete order event or authorised API reconciliation may resolve them. Thus not every
Shopify commercial event can be fully applied without API evidence; that limitation
is explicit rather than weakening the importer. Failures after5local persistence
attempts become dead-letter; transient pending events retry through the existing
worker. Redaction tombstones continue to suppress reintroduced private order data.

## Configuration and activation approval

No public destination or hosting resource has been approved. No subscription is
active and no app version was staged against a placeholder. Shopify supports HTTPS,
Google Pub/Sub and Amazon EventBridge delivery; the latter require actual merchant
cloud accounts/IAM/cost review. This implementation uses an isolated HTTPS gateway;
managed-bus adapters are not claimed implemented. Tailscale alone is not public delivery.

Gateway-only environment (a separately approved service):

| Variable | Meaning |
|---|---|
| TLC_WEBHOOK_DATABASE | Absolute file in private persistent event-only directory |
| TLC_WEBHOOK_SECRET | Existing TLCDashboard app client secret, not an Admin token |
| TLC_WEBHOOK_DOMAIN | Verified myshopify.com domain |
| TLC_WEBHOOK_KEY | Fernet key generated securely once; preserve across restarts |
| TLC_WEBHOOK_TOKEN | Strong separate gateway-transfer bearer credential |

Private Unraid optional configuration:

| Variable | Meaning |
|---|---|
| SHOPIFY_ORDERS_WEBHOOK_URL | Approved HTTPS gateway base URL, no query/userinfo |
| SHOPIFY_ORDERS_WEBHOOK_SECRET/DOMAIN/KEY/TOKEN | Same pinned signing/encryption/transfer settings |
| SHOPIFY_ORDERS_WEBHOOK_STORE_ID | Previously API-verified store GID |
| SHOPIFY_ORDERS_STARTUP_ENABLED | true by default; explicit false disables startup only |

Secrets belong in protected deployment configuration, never Git/templates/browser.
Without all delivery settings, no queue request occurs and UI says Webhook delivery
not configured. Last successful startup, manual, webhook receipt/processing and pending/
incomplete counts are independent. A successful connection is not proof of live updates.

After approving hosting, real URI and operational monitoring, prepare this subscription
in a separate candidate of the already linked app (do NOT apply directly now):

```toml
# Keep existing [webhooks] api_version = "2026-10".
[[webhooks.subscriptions]]
topics = ["orders/create", "orders/updated", "orders/cancelled", "orders/paid", "refunds/create"]
uri = "https://APPROVED-GATEWAY/webhooks/shopify/orders"
```

Do not deploy this literal placeholder. Retain current app clientID,all existing scopes,
URLs,install settings and same tlc-customisations UID/asset. These order topics use
read_orders; read_customers is separately required by the existing order.customer
detail selection, not by the webhook topics themselves. Stage via existing CLI
`shopify app deploy --no-release --path shopify --version <approved-candidate-name>`;
inspect complete diff/extension and report exact version for separate release approval.
Review protected-customer-data/compliance and hosting retention obligations first.
Releasing a subscription version does not automatically authorise missing installation
permissions. Rollback to the previous app version removes new subscriptions; disable
private queue config to stop consumption without deleting retained orders.

## Backup, deployment and acceptance

Back up the established persistent appdata SQLite volume (database+WAL consistently),
existing configuration and new encryption keys; no mount path changes. Migration0018
adds event/delivery operational tables and sync diagnostics/timestamps; disables only
recurring Orders work, retaining manual/paused receipts and0016/0017 records. Existing
migration framework backs up before upgrade. No destructive downgrade; restore the
matching backup/image together when rollback is necessary. Reconcile after restoring
an old backup to avoid gaps. Protect/delete historical backups under the same PII policy.

1. Deploy the tested develop or immutable image manually using existing mounts/env.
   Never run an old image against a newer migrated DB for rollback without review.
2. Confirm0018, Products/sales tasks unaffected and no recurring Orders schedule.
3. Inspect startup Orders task; on failure read category+phase+next action. Confirm
   actual read_orders grant. Run the read-only probe if the original issue persists.
4. Use Sync Now/explicit recent-history repair. Include test orders and open#TLC-1001:
   expect1order,2lineGIDs,SKU16bit-0001,Ellie/6 andFreddie/5; compare actual status/totals
   with Shopify (original7.00subtotal+2.29shipping=9.29). Fixtures are NOT live proof.
5. Sync twice and restart: IDs/answers persist, incremental checkpoint overlaps2h,
   no reimport of full history. Disconnect Shopify: saved Orders remain viewable.
6. Before gateway approval, confirm warning: live automatic updates are inactive.
7. After separately approved hosting/subscription release, independently verify
   authentic receipt, encrypted durability, queue ack, local processing and correct
   line snapshots. Check request trace shows zero Orders GraphQL calls for events.
8. Exercise duplicate/redelivery, offline Unraid replay and incomplete event handling
   with an approved test mechanism; no refunds/order edits are authorised by this guide.
9. Wait beyond5minutes and verify no Orders API polling. Products/sales may still
   legitimately call their own APIs. No Discord message is expected for these reads.

Official references: [2026-10 event topics](https://shopify.dev/docs/api/webhooks/2026-10),
[HMAC/delivery verification](https://shopify.dev/docs/apps/build/webhooks/verify-deliveries),
[delivery transports](https://shopify.dev/docs/apps/build/webhooks/delivery-structure),
[app configuration](https://shopify.dev/docs/apps/build/cli-for-apps/app-configuration).
