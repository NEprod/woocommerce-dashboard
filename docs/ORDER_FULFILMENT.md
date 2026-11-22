# Order Fulfilment and existing-app order integration

Local development implementation; nothing in this document has been deployed or
registered remotely. No customer email transport or public customer progress page.

## Operator entry points

Production & Operations → **Order Fulfilment** (`/production/orders/`). An order
number and its `TLC-ORDER-<existing local order ID>` Code 128 token resolve through
one lookup to `/production/orders/<id>`. The original `/orders` snapshot remains
the commerce/private-assets view. Searching, scanning and printing are read-only.

The verified existing store/product/variant identity determines the local product.
Its effective JSON BOM resolves actual purchased quantities and option/variant
rules, never a title guess or a manually selected replacement product. Ambiguous
identity, conflicting BOM or absent workflow classification requires configuration.

The existing collection/product BOM editor now exposes optional
`production.workflow`: `type` (card, print, assembly, laser, stock, mixed),
`proof_required`, `personalised`, `needs_picking`. Sparse collection/product/variant
policy inheritance is preserved. No existing JSON is rewritten on read. Set
assembly for separately stocked 3D components; direct print/laser/card routes can
issue their actual BOM materials during production. They cannot pass quality
checking with outstanding issue requirements. There is no new costing resolver.

Proofs are manually prepared/sent outside this application, with opaque private
document references and recorded approval evidence. Sent is not approved. Each
revision retains history. Purchase-time proof preferences also enforce the gate;
an operator override is explicit and audited. Existing private customer assets
remain accessible only through the authenticated original Orders view.

Each line progresses independently. The aggregate cannot become ready for dispatch
until all current lines are ready/dispatched. Start work, stock confirmation,
quality, packing and actual carrier handoff all require confirmation and evidence.
Picking is distinct from irreversible physical manufacturing commitment. Routine
local cancellation after commitment/picking requires an authorised exception and
stock/refund reconciliation. This is not a legal cancellation determination and
does not block Shopify Admin, statutory remedies or other authorised integrations.
Changed commercial lines, refunds, cancellations or early external fulfilments
hold affected work; frozen BOMs are not silently replaced. Re-plan requires a
controlled reconciliation and returns of picked stock; old evidence is retained.

### Exact stock movement point

Start Picking creates claims only. Confirm Pick posts **one existing ledger use**
and links its immutable receipt to the order requirement. Batch scans issue that
specific batch; permanent codes can issue only unbatched opening stock. The same
writer fence protects claims, available stock, batch balances and idempotency.
Assembly, quality, packing and dispatch do **not** deduct the components again.
Controlled physical returns post compensating ledger reversals and signed pick
evidence, putting the work on hold. Generic reversal of an order pick is blocked
to prevent order progress and physical stock diverging.

The A4 pick sheet has no unnecessary customer addresses, private asset tokens or
proof URLs. Multi-page sheets repeat table headings. Native browser printing and
the existing Code 128 renderer are reused; stock-card print dimensions are untouched.

### Replenishment

`/production/orders/replenishment` and dashboard pills derive exclusive
healthy/low/out categories from ledger stock minus claims. Manufacturing/ordering
are action overlays. Unpicked demand, minimum/target, planned batches and expected
purchase plans are shown separately. Claims are not subtracted a second time from
the demand calculation. Plans never become physical stock. Completed manufacturing
continues through existing batch receipt; purchase plans link an existing Receive
Stock receipt explicitly (no extra receipt is posted), or can be cancelled with
evidence. Reversed linked receipts reopen the incoming shortfall. Suggestions are
estimates, not supplier orders or automatic printer jobs.

## Scheduled order reconciliation

Settings → Scheduled Tasks → **Shopify Order Synchronisation** uses the existing
`shopify_orders` task, leases, Run Now, execution history, checkpoints and importer.
Migration 0029 restores this previously retired recurring task to enabled, every
15 minutes **once**. Subsequent restarts preserve operator enable/interval choices.
Normal scheduling retains the application's startup grace. A narrow read-only
Orders lane runs even when managed publishing automation is off; this neither
enables other scheduled tasks nor grants write authority. Manual Run Now
uses the identical reconciliation service. No independent scheduler is introduced.

The existing updated-at index is paginated with a two-hour overlap, bounded initial
lookback and revision readback. Only a completely processed window advances its
successful checkpoint; partial failures resume without losing their watermark.
Order IDs and stable line IDs upsert the existing commerce tables. Replays do not
reset local production, duplicate jobs or post inventory. Retained incomplete
webhooks trigger the same canonical order reader before the normal polling window.
API throttling/backoff and overlapping-run rejection remain the existing task logic.
Older Shopify orders remain subject to the existing approved lookback/scopes;
`read_all_orders` is not requested automatically.

## Webhook settings and authority

Settings → **Shopify Webhooks** (`/settings/shopify-webhooks`) offers local Add/Edit
modals and pencil actions. Cancel/Escape never saves. Local Save never registers a
Shopify subscription. Remote Verify, Register/Update and Remove Exact Subscription
are separate actions on saved settings; mutation needs explicit confirmation.
Version checks and a bounded pending-operation lease prevent concurrent edits.
Registration first reads the complete app-scoped subscription list, avoids an
identical subscription, retains its exact remote identity and verifies readback.
Removal refuses a subscription whose identity/topic/destination changed elsewhere.
Uncertain network outcomes are labelled, not optimistically declared successful.

Supported pinned topics: orders/create, orders/updated, orders/cancelled,
orders/paid, orders/fulfilled, orders/partially_fulfilled, refunds/create,
fulfillments/create and fulfillments/update. Orders require the existing approved
order scopes; fulfilment topics additionally require `read_fulfillments` or
`read_marketplace_orders`. No scope is silently expanded. API and delivery version
are pinned to **2026-10**; subscription readback checks the actual delivery version.
App-managed subscriptions must be changed through the existing app's configuration,
not duplicated with a second app or silently adopted cross-app subscription.

## Required external setup — manual, not performed

The Tailscale dashboard cannot receive public Shopify deliveries. The existing
customer-photo Cloudflare Worker/R2 is **not** an order queue and is untouched.
Reuse the existing isolated Flask WSGI gateway
`app.order_webhook_gateway:create_gateway()` behind approved public HTTPS, e.g. a
narrow Cloudflare Tunnel route. Expose **only**
`POST /webhooks/shopify/orders`; keep `/events` and `/ack` on an authenticated
private service route where possible. Never route the private dashboard publicly.
The gateway has no dashboard credentials, order API client or inventory routes.

Prepare a private persistent gateway directory and configure these existing
gateway settings securely (deployment environment `TLC_WEBHOOK_*`):

| Setting | Required value/purpose |
| --- | --- |
| DATABASE | Absolute SQLite file under an approved persistent private mount; parent must exist, file mode 0600. Do not invent an Unraid host path. |
| DOMAIN | Exact connected `*.myshopify.com` domain. |
| SECRET | **The existing app client secret** for Shopify HMAC; never a newly invented duplicate signing secret. |
| KEY | Fernet key shared with the private consumer, encrypts relay payloads at rest. Separate role from Shopify signing. |
| TOKEN | At least 32 random characters shared with private consumer, authenticates event transfer/ack, not Shopify HMAC. |

Use an approved production WSGI server and HTTPS edge, persistent private mounted
disk, request limit at least 2 MiB for this single endpoint (gateway rejects larger),
and outbound private-dashboard connectivity to the relay. Configure only the
approved receiver hostname/DNS/Tunnel route; **nothing alters DNS or Cloudflare**.
Private dashboard existing settings:

| Setting | Value |
| --- | --- |
| SHOPIFY_ORDERS_WEBHOOK_URL | Relay service base URL (not the final `/webhooks/shopify/orders` delivery path); consumer appends `/events` and `/ack`. |
| SHOPIFY_ORDERS_WEBHOOK_TOKEN / KEY | Match gateway transfer token/encryption key, configured through existing secret deployment conventions. |
| DOMAIN / STORE_ID | May derive from the existing app connection + successful order sync; explicit legacy values must match. |
| SECRET | Derived from existing saved app client secret first. Legacy manual-token deployments may supply the actual existing app signing secret via this established setting, not a separate signature scheme. |

In the **existing** Shopify app set webhook delivery API version 2026-10 and the
selected topics to `https://<approved-public-receiver>/webhooks/shopify/orders`.
The local table's endpoint is the final public delivery URL, distinct from the
private consumer service base. Receiver reachability is unverified until real
authenticated delivery. Remote registration is separate from local enablement,
HMAC receipt and successful reconciliation; none is labelled green by assumption.

The receiver validates exact raw-body HMAC, expected shop, delivery ID, supported
topic/version and bounded content before durable encrypted insertion/acknowledgement.
Private transfer validates HMAC again. Deduplication and existing lease/complete
snapshot checks handle replay, stale/out-of-order and incomplete events. Partial
refund/fulfilment messages schedule canonical readback, not a second importer.
Queue is bounded at 10,000 unacknowledged events, rate limited at 60 requests/sec;
raw encrypted bodies expire after seven days and acknowledged dedup rows after
90 days. Expiry is an attention/reconciliation marker, not silent successful loss.

## Optional production-status projection

Disabled by default. Explicit Settings authorisation verifies the current shop
with `read_orders` AND `write_orders`; changed credentials require reauthorisation.
Managed deployments additionally retain `TLC_DEPLOYMENT_ALLOW_WRITES` and
`TLC_DEPLOYMENT_ALLOW_AUTOMATION` for this outbound worker. Do not toggle these
globally without separately reviewing existing publishing policies. Read-only
15-minute order polling does not require either write/automation permission.
The existing scheduler handles a bounded durable outbox with per-order leases,
retry visibility and read-first recovery. It writes only:

`tlc_operations.production_status`, type `single_line_text_field`, owner Order.

Names are human-readable; mixed orders never claim all work is complete. Compare
digests and exact shop/order/metafield readback verify writes. No tags, selling
prices, native fulfilment/financial/cancellation status or other metafields change.
Local progress does not depend on Shopify availability. In Shopify Admin →
Settings → Custom data → Orders, define/pin this single-line-text field to make it
visible to staff. That is **not** automatic customer-facing visibility.

Reference: [Shopify subscription creation](https://shopify.dev/docs/api/admin-graphql/latest/mutations/webhookSubscriptionCreate),
[subscription input](https://shopify.dev/docs/api/admin-graphql/latest/input-objects/WebhookSubscriptionInput),
[supported topics/scopes](https://shopify.dev/docs/api/admin-graphql/latest/enums/WebhookSubscriptionTopic).

## Persistence, boundaries and acceptance

Additive migrations 0028/0029 store workflow lines, requirements/claims, immutable
events/picks, proof revisions, replenishment plans, status outbox, webhook settings,
projection authority and readback state in the **existing configured SQLite file**.
Existing commerce inbox/checkpoints and inventory ledger remain authoritative.
No new dashboard mount or destructive migration; gateway persistence must be
manually verified at its deployment. Downgrade refuses to erase retained evidence.
The repository development template binds `/app/instance` to
`/mnt/cache/appdata/woocommerce-dashboard-dev/instance`; the database remains
`/app/instance/site.db`. This mapping is verified in repository configuration,
not by inspecting or modifying the running Unraid container. Existing `/catalogue`
JSON and private customer-upload mappings are unchanged. New store-bound webhook
and projection-authority rows also participate in the existing deployment identity
audit, preventing a mount containing another store's authority from passing startup.

### Development release preflight

The final release audit re-ran the 306-test relevant Python suite and the
publishing/deployment suite (53 passed, the same one pre-existing old-draft failure).
The exact failing node is
`tests/test_unraid_acceptance_defects.py::test_old_draft_finding_projection_does_not_fabricate_verification_or_hide_failures[old_draft_channels]`.
An isolated archive of unchanged base commit
`50cc44d255669c8db215ebcce90098bc343e9125` reproduces the identical Attention-versus-Draft
assertion. No unrelated publishing implementation or assertion was changed.

`tests/test_order_release_migrations.py` additionally opens the local existing
database read-only, creates a disposable backup, upgrades that copy to 0027 and
adds representative inventory/ledger/order rows **only to the copy**. A second copy
is upgraded separately through 0028 and 0029. All rows in 53 pre-existing tables
remain identical, including inventory, ledger, orders/lines and an active scheduled
run; only the documented Orders task enable/15-minute/next-due fields change.
All other task settings remain identical. Integrity/foreign-key checks pass,
the paired migration backup exists, and subsequent restart preserves an explicit
operator-disabled/30-minute setting. The original database's size and modification
time are unchanged. A synthetic historical-database version also passes in pytest.

Neither migration inserts webhook subscriptions, projection authority, status
outbox entries or production jobs. Subscription registration remains an explicit
Settings operation; outbound metafield writes require separate authority and
deployment guards. Customer communication delivery is not implemented. Existing
configured legacy delivery settings are not erased or replaced.

`tests/docker_order_fulfilment_smoke.py` is the offline candidate-image gate:
linux/amd64, full embedded revision, 0027→0029 migration, configured persistent
mounts, UID/GID 100, UMASK 002, authenticated pages, identical manual/barcode lookup,
stock/order preservation and container restart. It passes no host Shopify
credentials, publishes no ports and uses `--network none`. It does not modify the
running Unraid container or claim live managed-Shopify credential verification.

There is no newly invented postage/fulfilment writer. Repository shipping support
includes a Click & Drop catalogue export, not verified label purchase/carrier
handoff. Operators record actual local dispatch and use the established external
Shopify/Royal Mail/Click & Drop process separately; local dispatch never fulfils
Shopify. No customer messages, secure approval portal or legal-policy automation.

For manual acceptance use development/test orders only: configure a card requiring
proof, an assembly with separately stocked components, a direct print/laser route
and a mixed order. Verify manual/scanned lookup, proof revision gate, partial batch
picks, shortage planning/receipt/resume, compensating return, QC/packing/handoff,
status summary and audit. Verify polling disabled/enabled/run now and failed
watermark recovery. Deploy/verify public receiver and approve existing app scopes
separately before any live delivery or explicitly authorised metafield write.
Physical phone camera/handheld scans and real printer output require manual testing.
## Local validation — 22 November 2026

No live Shopify requests, messages, postage purchases, Unraid changes or release
operations. Disposable catalogues/databases and mocked Shopify endpoints only.

- Relevant Python suite: **306 passed** (Order Fulfilment/integrations, canonical
  order sync/webhooks, stock/batches, Scheduled Tasks/progress, BOM inheritance,
  catalogue BOM workspace, stock cards and private customer photos).
- JavaScript regression suite: **18 passed** (Orders, task controls, product BOM).
- Actual headless Chrome checks: desktop 1440×1000 and mobile 390×844. Manual
  lookup and simulated native camera decoding open the exact same job; neither
  changes audit/stock state. Webhook Add/Cancel/Escape/Save/Pencil work without
  accidental records. Mobile fixed navigation, no horizontal overflow, bottom
  scroll clearance, native dialog focus and a reduced 390×430 keyboard-sized
  viewport were checked. Confirmed partial picking + duplicate replay posts one
  ledger issue. This is not physical iPhone/Zebra or real camera-image testing.
- Generated pick-sheet PDF: one A4 page (594.96×841.92 pt) for the synthetic order.
  No physical printer or live multi-page order acceptance was claimed.
- Additional deployment/catalogue tests: **30 passed, 1 pre-existing failure**:
  `test_old_draft_finding_projection_does_not_fabricate_verification_or_hide_failures[old_draft_channels]`
  in `tests/test_unraid_acceptance_defects.py` expects Draft but receives
  Attention. Reproduced unchanged in an isolated archive of base commit
  `50cc44d255669c8db215ebcce90098bc343e9125`; publishing logic and that assertion
  were not modified.
- The pre-existing per-second gateway limit test now freezes the gateway clock,
  retaining its 429 assertion without depending on 70 disk-backed requests
  finishing in one real second. Migration/polling assertions were updated only
  for the intentionally restored 15-minute task and retained-history guard.
- SQLAlchemy deprecation and pytest temporary-directory cleanup warnings remain;
  no new functional failure is suppressed.
- Application restart against the same temporary persistent SQLite file preserves
  webhook settings, authority, order history and operator-disabled/30-minute task
  configuration. No container recreation or real appdata migration was performed.
- `git diff --check` and syntax checks for the changed JavaScript passed.

Reproduce the principal suite with `PYTHONPATH=/tmp/tlc-inventory-test-deps
python3 -m pytest -q --disable-warnings` and these files:
`tests/test_order_fulfilment.py`, `tests/test_order_integrations.py`,
`tests/test_shopify_orders.py`, `tests/test_orders_webhooks.py`,
`tests/test_inventory.py`, `tests/test_scheduled_tasks.py`,
`tests/test_scheduled_task_progress.py`, `tests/test_product_bom.py`,
`tests/test_product_bom_options.py`, `tests/test_catalogue_bom_workspace.py`,
`tests/test_inventory_stock_cards.py`, `tests/test_customer_photos.py`.
The temporary dependency path is a local test-runtime aid, not deployment config.
Run browser fixture `tests/browser/order_fulfilment_fixture.py` on loopback 7522,
then `node tests/browser/order_fulfilment.browser.mjs` using the installed local
Playwright/Chrome paths in that test. All non-loopback requests are blocked.

### Intended changed files

- `app/__init__.py`
- `app/deployment_guard.py`
- `app/inventory/product_bom.py`
- `app/inventory/service.py`
- `app/models.py`
- `app/order_integration_models.py`
- `app/order_integrations.py`
- `app/order_models.py`
- `app/order_operation_queries.py`
- `app/order_webhook_gateway.py`
- `app/order_webhook_routes.py`
- `app/order_webhook_security.py`
- `app/order_webhooks.py`
- `app/order_work.py`
- `app/order_work_models.py`
- `app/order_work_outbound.py`
- `app/order_work_routes.py`
- `app/routes.py`
- `app/scheduled_tasks.py`
- `app/shopify_connection.py`
- `app/shopify_orders.py`
- `app/static/assets/css/inventory.css`
- `app/static/assets/js/catalogue-bom.js`
- `app/static/assets/js/order-work.js`
- `app/static/assets/js/webhook-settings.js`
- `app/task_presentation.py`
- `app/templates/dashboard.html`
- `app/templates/includes/_scheduled_task_row.html`
- `app/templates/includes/_scheduled_tasks_mobile.html`
- `app/templates/includes/navbar.html`
- `app/templates/inventory/_fields.html`
- `app/templates/inventory/base.html`
- `app/templates/inventory/home.html`
- `app/templates/order_work/detail.html`
- `app/templates/order_work/index.html`
- `app/templates/order_work/replenishment.html`
- `app/templates/order_work/sheet.html`
- `app/templates/order_work/webhooks.html`
- `app/templates/orders/detail.html`
- `app/templates/scheduled_tasks.html`
- `docs/ORDER_FULFILMENT.md`
- `migrations/versions/0028_order_fulfilment.py`
- `migrations/versions/0029_order_integrations.py`
- `tests/browser/order_fulfilment.browser.mjs`
- `tests/browser/order_fulfilment_fixture.py`
- `tests/test_order_fulfilment.py`
- `tests/test_order_integrations.py`
- `tests/test_order_release_migrations.py`
- `tests/docker_order_fulfilment_smoke.py`
- `tests/test_orders_webhooks.py`
- `tests/test_shopify_orders.py`

The initial local-only handover was based on
`50cc44d255669c8db215ebcce90098bc343e9125` on `develop`, before the separately
authorised development release. The three pre-existing customer-photo files are
excluded and their SHA-256 hashes match the initial inspection. Release only
the reviewed commit as `develop` and `order-fulfilment-<short-commit>`, passing the
full revision as both `GIT_REVISION` and OCI revision label. Verify matching
remote digests and run the offline startup/restart smoke before publishing.
Do not deploy or modify the running Unraid container.
