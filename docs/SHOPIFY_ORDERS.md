# Shopify Orders — M3A.9.5.2

## M3A.9.5.5 supersedes the original polling model below

Orders no longer poll the Admin API every five minutes. Startup/manual catch-up,
the optional encrypted webhook delivery path, safe diagnostic categories, privacy,
configuration and activation gates are documented in [ORDERS_WEBHOOKS.md](ORDERS_WEBHOOKS.md).
Migration0018 adds operational evidence only;0016 Orders and0017 Packaging survive.
The historical M3A.9.5.2 schedule/release descriptions below are not current activation
instructions. The active permission baseline has since been merchant-released as
tlc-orders-foundation-7679198d2b0c, GID1170296569857. Merchant Connection Test now
reports read_orders granted; that does not establish why an earlier import failed.

## M3A.9.5.4 boundary

The final solo-packaging scope leaves Orders Foundation unchanged. Cancelled basket
and fulfilment-time packing tools are not installed. Existing order/line identities,
historical personalisations, private storage and import scheduler remain intact.

## Authority, permissions and release gates

Shopify owns order commerce, payments, quantities, edits, cancellation, refunds and
fulfilment. TLCDashboard imports a protected local snapshot; it cannot create, edit,
refund, cancel or fulfil an order. Future local production/proof/material/picking
records must be separate, keyed to these durable order/line identities. Reconciliation
must never replace those future local records or reinterpret answers using today's
catalogue/template. No production-ready flag is invented by this milestone.

The only requested scope addition is **read_orders**, retaining all existing 11
scopes. No write_orders, read_all_orders, read_customers, theme-write or webhook scope
is added. Normal queries use the configured Admin API (2026-10); opt-in product packed
dimensions alone keep their existing 2027-01 policy. See Shopify's official
[Order](https://shopify.dev/docs/api/admin-graphql/2026-10/objects/Order),
[LineItem](https://shopify.dev/docs/api/admin-graphql/2026-10/objects/LineItem),
[orders pagination/search](https://shopify.dev/docs/api/admin-graphql/2026-10/queries/orders)
and [protected customer data requirements](https://shopify.dev/docs/apps/launch/protected-customer-data).

The linked TLCDashboard app is unchanged (App 434537660417, client ID
f9498b5a7e0393d9cddbb49defb4cacd). The working tlc-customisations extension UID is
32a543f0-9ad7-6dfe-9a1e-d4437bf366175766d7e5. The permission candidate must be staged
with **--no-release** and inspected before separate merchant release approval.
The active baseline is tlc-customisations-cart-604e504d4cae (1170069815297).
No staging action approves installation permissions or changes a theme/product.

After explicit permission-version release approval, the merchant must approve any
Shopify installation permission prompt. Do not reinstall the app, rotate credentials
or make a duplicate app. Restart the Dashboard after approval to obtain a fresh Client
Credentials token with the current grants; use Connection → Test, then Orders → Sync
Now. Confirm read_orders in the actual current token scopes. Customer name, email,
phone and address also depend on Shopify's protected-customer-data access. A denied
or partial GraphQL response is not an empty store: the run fails safely and preserves
existing snapshots. Review access in the existing app rather than requesting broad
extra scopes speculatively. Nothing here authorises a permission release automatically.

## Durable storage and historical answers

Migration **0016_shopify_orders**, after 0015_customisation_templates, adds:

- shopify_order: store-scoped Shopify GID, name, source revision/timestamps, statuses,
  test flag, narrowly scoped contact/delivery data and original/current monetary bags.
- shopify_order_line: parent order FK, stable line GID, purchased title/SKU/variant,
  quantities, prices, original customAttributes and parsed historical TLC answers.
- shopify_order_sync: verified source store/domain, bounded ID-only pending queue,
  cursor, fixed import window, checkpoint, attempt/success timestamps and safe error.
- One shopify_orders task in the **existing** durable scheduled-task coordinator.

Unique store/order and parent/line constraints preserve identities. Indexes cover
created order, name, SKU, store, operational status and line-parent access. An
order-only parent guard also enforces referential safety on legacy SQLite connections
without globally changing scanner/catalogue foreign-key behaviour. A complete
order graph and queue progress commit together. An older source updatedAt cannot
overwrite a newer imported order. Only a complete, revision-consistent paginated
read may mark a previously present line historical/removed; the line is retained,
not deleted. An inaccessible order is not proof of remote deletion.

Two purchases with the same SKU/variant remain separate by order GID + line GID.
_tlc_configuration is the extension's plain SHA-256 revision string;
_tlc_personalisation is its existing JSON schema_version 1 envelope, including
product_id, revision, fields and optional visible_encoding. Parse typed values,
purchase-time labels, field IDs, choice IDs, readable choice labels and property_key;
never resolve against today's product. Legacy snapshots without property_key remain
readable. Entity-encoded public properties are decoded once only when the snapshot
declares that encoding, then escaped normally by Jinja. Invalid schemas/fields,
identity/revision disagreements or conflicting public/typed answers produce warnings.
Original properties remain available in administrator-only technical details; valid
ordinary customer answers are retained even without a usable private snapshot.
These buyer-supplied snapshots are historical evidence, **not cryptographic proof**.

No full raw order, billing/payment instrument, IP, session or browser payload is stored.
No order/customer data is sent to Discord. Import errors do not log remote exception
text, GraphQL variables, customer strings, tokens or payloads. Protected Orders routes
require an authenticated administrator and send no-store/private responses. The main
Dashboard shows order summaries only to administrators.

## Import, scheduling and recovery

Orders → **Sync Now** is a CSRF-protected POST which durably queues a read-only run.
It does not run Shopify requests during page rendering. The existing in-container
scheduler continues after browser close/disconnect. Default interval is five minutes;
Settings → Scheduled Tasks → select Orders from the Orders page to change/disable it.
The supported intervals are 5, 10, 20, 30, 60 minutes. Manual Sync Now also works when
the automatic schedule is disabled. There is no new worker, public webhook or startup
catalogue scan. Products reconciliation and authorised sale transitions remain separate;
sale priority, shared API budgets, owner leases and fencing are reused.

Defaults/optional Docker environment settings:

| Setting | Default | Supported bound |
|---|---|---|
| SHOPIFY_ORDERS_INITIAL_DAYS | 30 | 1–60 days |
| SHOPIFY_ORDERS_BATCH_SIZE | 5 | 1–10 orders per slice |

Initial import is a bounded recent updated_at window. read_orders ordinarily exposes
only orders from the last 60 days, regardless of our locally retained history.
Every subsequent completed window advances a per-store checkpoint with a two-hour
overlap for delayed/indexed updates. The upper cutoff is fixed at run start minus
30 seconds; new activity is picked up on the next run. This is not unlimited history
and does not guarantee capture of an arbitrarily delayed change older than the overlap.

Index pages contain at most 10 order IDs; complete detail reads have 25 lines/page,
at most 40 pages (1000 lines/order), and at most 10 shipping services. Connection
sizes bound the selected monetary/reference query cost. A final order
revision query rejects a change during pagination. Limits fail closed, retaining the
last complete snapshot. Each slice uses the existing capacity gate with a 70-request,
80-second maximum and yields to interactive work. A large page may require several
15-second-paused continuations. Requests retain existing bounded timeout/throttle
handling. Source data that exceeds bounds needs a reviewed targeted importer change,
not silent truncation. API/throttle/budget failure never implies successful freshness.

The pending ID queue/cursor survives failed runs and restarts. Use Sync Now after
fixing access/network problems; already committed orders are not duplicated. A failed
order holds the window/checkpoint until retried successfully rather than silently
skipping it. Last complete sync advances only when the whole fixed window finishes;
last attempt, per-run checked counts and import history are separate evidence.
Missing read_orders sets Authorisation Required and pauses automatic retries until
explicit Sync Now. If Shopify is unavailable, previously imported orders still open.

## Operator views and metrics

Orders uses local paginated records (25/page), newest first, with order-number/SKU
search, payment, outstanding/fulfilled, test and personalisation filters. Detail shows
shipping, contact data, financial totals, each independent purchased line, original
revision, actionable parsing warnings and technical evidence. Fixed Shopify Admin
links use only the authenticated store/domain retained at import. No order-write
buttons exist. Mobile rows become cards and reuse shared custom.css design tokens.
While a run is active, bounded local-only status polling provides completion feedback;
refresh the local list to see newly imported orders. It never polls Shopify directly.

Dashboard defaults exclude test orders, with an explicit Include test orders link:

- Unfulfilled paid orders: not cancelled, PAID/PARTIALLY_REFUNDED, outstanding units >0.
- Items awaiting fulfilment: sum outstanding **units**, not lines, for those orders.
- Orders today: created within Europe/London midnight-to-midnight (GMT/BST aware),
  all payment states, same test-order setting. Not a paid/production-ready count.
- Recent orders: five newest local orders under that same test-order setting.
- Orders sync: latest complete window, current task and actionable failure/access state.

## Persistence, privacy, backup and rollback

Use the **existing** persistent appdata mapping at /app/instance/site.db and its
/app/instance/backups directory. /data is a conceptual appdata location, **not** a
new required mount. Do not rename or replace any current Unraid host/container path.
No mandatory environment variable or XML change is needed for the defaults.

Stop all workers/container for a consistent external backup, or use the established
SQLite backup API. Preserve commerce.json/session configuration and database together;
back up the authored catalogue separately as before. Automatic versioned upgrade
makes a verified private pre-migration backup. Database and backups now contain order
PII and customisations: restrict host/network access and include them in retention and
deletion policy. No new public service is required. Do not commit or share these files.

Restoring a backup preserves its order/line GIDs and checkpoints; subsequent Sync Now
replays a bounded overlap safely. Records newer than the restored backup are recovered
only while Shopify still grants access to them. Keep a current backup before rollback.
Nonempty Orders downgrade is deliberately refused. Prefer a forward fix; do not drop
tables to run an old image. A full pre-migration restore loses later local imports and
requires controlled reconciliation. Never delete historical operational data casually.

The explicit administrator maintenance hook redact_local_order clears contact,
address, original properties and parsed answers while retaining commerce/GID identity
and a durable redacted_at tombstone. Re-import cannot restore that PII. There is no
automatic customer-data deletion UI/webhook or retention timer yet. Redaction requires
a separately authorised maintenance procedure, including handling historical backups
and restored copies; it is not called by normal sync or tests against production.

## Controlled Unraid / #TLC-1001 acceptance

1. Approve the separately staged app permission version first. Verify exact +read_orders
   diff, unchanged extension UID/asset, app identity, installation, URLs and webhooks.
   Release is a separate explicit merchant decision, not part of Docker deployment.
2. Back up appdata/database and catalogue; record the previous image digest. Pull the
   immutable shopify-orders-foundation-<commit> image (or tested develop), preserve all
   volumes, credentials, PUID/PGID and settings. Do not change the live theme or order.
3. Start the container. Confirm migration 0016_shopify_orders; do not manually edit DB.
   Test Shopify connection and check read_orders in the current token. If denied,
   approve Shopify's installation access and protected customer-data prerequisites;
   restart to drop cached tokens, then Sync Now. Never paste secrets/customer data.
4. Open Orders, include test orders, Sync Now, inspect import history. Search TLC-1001.
   Exactly one order must exist; open it and compare name, test flag, statuses, totals
   and shipping against Admin. Read-only import does not itself change Shopify.
5. Check **two independent line GIDs**, both SKU 16bit-0001: Ellie / Years 6 and Freddie /
   Years 5. Confirm typed values, historical field IDs, both private source properties
   and configuration revision in protected technical details. Do not merge by SKU.
6. Sync again: same order and line identities/counts, no duplicate. Restart Unraid:
   same saved information. If API access is unavailable, saved Orders must remain
   viewable while sync reports the failure honestly. Do not alter credentials/order
   merely to simulate an outage; offline failure tests cover that case.
7. Check Dashboard with/without test orders. Counts are local commerce summaries, not
   proof of picking/production readiness. Confirm existing Products/sales/editor still
   work, and Orders visits/imports create no Discord or scanner activity.

The merchant reported successful test checkout #TLC-1001 before implementation. Offline
fixtures and Docker smoke checks are **not** evidence of importing that actual order.
Live acceptance remains pending permission release/authorisation and Unraid deployment.

## Deferred

No order writes, production/picking, proofs, shared materials, labels, Royal Mail,
fulfilment, customer uploads, automatic notifications, public webhook endpoint,
unbounded/older-order history or bulk product publishing. The read_order interface
can accept authenticated future webhook hints, but no internet-facing shortcut exists.
