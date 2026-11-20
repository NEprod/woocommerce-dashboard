# UI foundation and operator-experience register

Batch 1 covers Dashboard, Products, Collection Metadata and shared controls used
by Product Overrides. It extends [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md); it does not
authorise deployment or introduce another styling framework.

## Reusable patterns

- Reuse existing forest/gold/surface, border, radius, spacing and focus tokens.
  `--control-height` is 44px. Page headers have one H1, a short subtitle and grouped
  actions. Products collection headers and the Recent Products table are the
  section/table references. Forms must not cause body-level horizontal scrolling.
- Editor headers use forest/white; descriptions span the form width. Preserve
  native input types, JSON values, HTML, inheritance, GMT/BST and calculations.
  Checkboxes stay native and consistently sized. Keyboard focus remains visible.
- Normal statuses remain Needs Creating, Up to Date, Update Available, Scan
  Required, Processing, Needs Attention and Recovery Required. Preserve Status
  refresh pending and Last check matched qualifiers; stale evidence is not a
  confirmed product difference. A status never authorises a write.
- Show a short reason/action and useful verification time. Detailed receipts
  belong in Identity/History. Keep security, invalid-source, uncertain-write and
  shared-scope warnings visible; move long explanations into contextual help,
  not CSS hiding. Unavailable selections and JSON owners remain explicit.

## Dashboard

All three order figures share one test-order filter and retain the existing
paid/partially-refunded, outstanding-unit and cancellation rules. The default
includes test orders only from authenticated managed-startup proof pinning the
development environment, domain and Shop GID. Production/legacy defaults exclude
them. Explicit include/exclude selection is retained; URL naming cannot prove
environment identity. Orders list and Dashboard use the same saved Orders data,
but the list permits payment/fulfilment filters whereas the outstanding counters
are deliberately narrower. Today uses Europe/London midnight and all payment states.

Only missing/unhealthy webhooks or actionable import errors produce warnings.
Orders and task history retain Sync Now, import history and diagnostics.
Catalogue figures count TLC local collections/products/variations, plus last
verified store-bound links when available. Unknown links are not proof of remote
absence. Draft, Active and channel publication are separate; Active never implies
Online Store availability. Missing local sources belong in Local Catalogue
Health. No Shopify request or scan is made to build Dashboard counts.

## Products

Preserve grouping, thumbnails, prices, variations, pagination, selection,
overrides and review/history actions. `issue=all` means local description/image/
SEO gaps, not every publishing error; both page/API support combined filters.
Compact details link to full evidence instead of repeating revisions and plans.
Scan Required navigates to the correct metadata owner and the existing signed,
scoped local update-scan contract. GET never starts a scan. The editor refuses
unsaved drafts, unavailable owners and missing shared acknowledgement.

Freshness signals differ: publishing compares canonical managed revisions
(JSON ordering/whitespace alone is immaterial), while pricing review additionally
compares source mtimes with `.scanned` and checks pending markers. Syncthing
timestamp changes can therefore stale pricing evidence without proving content
changes. No specific live change is attributed to Syncthing without evidence.
Elapsed Shopify verification freshness alone is not a local-source edit.

## Metadata editor

Section anchors point to real headings, including Origin & Customs and separate
Product Dimensions/Shipping. Markets is normally visible; discovery remains an
explicit read-only action. Unavailable/unverified channels and Markets retain
owning source, saved intent and explicit removal controls.

At >=1200px width and >=700px height the sidebar is sticky and independently
scrollable. Smaller viewports use normal flow. Phone Save/Validate and shared
acknowledgement stay in the dock. Desktop/mobile acknowledgement controls mirror
explicit check and uncheck, calling the same validator and signed save endpoint.
New scan navigation does not implicitly acknowledge sibling products.

Unchanged: sparse overrides/unknown keys, removals, image routing, BOM/inventory
and costs, finished versus packed dimensions, customs/Royal Mail export, customer
fields, Shopify mutations, worker ownership/checkpoints and approval contracts.

## Confirmed Orders-access finding — 19 November 2026

Read-only API evidence: Shop `112056795527`, app `447856541697`, installation
`1264251502983`, API `2026-10`. `read_orders` and all 16 previous effective scopes
are granted; `read_customers` is not. A newly acquired token for the same development
app retrieves the recent test order and minimal order fields. The exact full
`TLCOrderRead` and cart-token variant fail at:

```text
path: order.customer
code: ACCESS_DENIED
message: Access denied for customer field. Required access: `read_customers` access scope.
requiredAccess: `read_customers` access scope.
```

The running dashboard uses deployment-owned client credentials and reports zero
saved orders, last import failed at `order_read`. Startup binds those credentials
to the verified development shop/app. No running token was dumped or printed;
the exact in-memory token was not independently fingerprinted. No import was run.
This is not missing `read_orders`, pagination or order-age access. The importer
correctly refuses a partial snapshot. Test-order filtering was a separate UI issue.

### Which customer data is actually used

- `order.customer { id displayName }`: retained profile reference/display name,
  shown in Orders list/detail, with shipping-name fallback when no customer exists.
  This implemented lookup requires `read_customers`.
- Order `email`, `phone` and shipping name/company/address/city/province/postcode/
  country/phone: fulfilment/contact information shown on detail. There is no
  automatic customer-email sender or profile editor implemented here.
- Private photos bind to Shop, Order, LineItem, Product/Variant, configuration
  revision, upload reference and cart digest, not customer-profile ID. The
  extra scope is not intrinsically required for photo ownership.

Minimal change, subsequently approved and applied: append only `read_customers` to the
development Product app's current scopes. Preserve all existing grants,
extension identity/assets and photo proxy. No `write_customers`, `read_all_orders`,
reinstall, credential rotation or silent removal of customer data is proposed.
The extension is currently preview-only: an approved scope update must preserve
its unchanged asset bundle, not replace it with a bare configuration-only version.

Shopify requires approval of a new scope on the existing store installation.
For development-only apps, the required protected data/fields can be selected in
Dev Dashboard without production review. Check the app's actual distribution and
protected-data settings before applying an approved change. Existing fields need
name/address/email/phone purposes only. The current response shows a scope denial,
not a separate protected-data denial; that does not prove future profile access.

Verified after approval: the same installation now grants all previous scopes
plus `read_customers`; both exact full Orders queries succeed, including customer
ID/display name, without GraphQL errors. The unchanged development customer-fields
assets still render. Released app version: `development-orders-customers-20261119`.
No additional protected-data change was necessary for these queries. The operator
can run existing Orders Sync Now after the manual container update. Preserve bounded recent
catch-up; do not enable automatic publishing or change webhook configuration.

References: [Shopify protected customer data](https://shopify.dev/docs/apps/launch/protected-customer-data),
[scope approval and the 60-day order window](https://shopify.dev/docs/apps/build/authentication-authorization/manage-access-scopes).

## Batch 2 — Product Workspace, Review & Sync, Identity & Recovery

Presentation only. Product/collection authority, scan scope, signed approvals,
Shopify requests, worker ownership/checkpoints, recovery decisions, database schema
and JSON resolution are unchanged. See [UI_BATCH2_VALIDATION.md](UI_BATCH2_VALIDATION.md)
for the exact implementation, action map, tests and screenshot index.

- Put breadcrumbs first, then one forest-headed overview containing title,
  thumbnail, SKU/type/collection, pricing, local lifecycle, intended versus actual
  Shopify state and the last successful verification. Keep automatic eligibility
  concise; its full original evidence remains in Technical Details.
- Use Inherited, Overridden, Mixed sources and Derived source labels. A title
  assembled from folder identity is not falsely labelled entirely inherited;
  merged structures are not falsely labelled a replacement. Show effective values
  by default, retaining every original field in Full Source Comparison. Price
  display never rounds a higher-precision authored value to conceal it.
- Group local categories, storefront collections, and variation/informational
  attributes separately. Assignment editing and registry authoring remain distinct.
- Keep Review & Sync primary. Refresh Product is a Shopify-read comparison, not a
  scan. Sync Now remains a separately acknowledged write under Additional Actions.
  Sale, package correction and deletion retain their own review/confirmation paths.
- Review tables use Field / Current Shopify / Proposed Value / Action / Status.
  Show blockers and proposed changes before notices, matching fields and technical
  evidence. Group variant rows by owner. Matching rows start collapsed; expanding
  them never changes approval scope. Every original resource/capability setting,
  media reference and field remains in the full technical evidence disclosure.
- Auto-preview invokes only the original CSRF-protected `action=preview` form for
  a scoped active Simple/Variable product. It never submits `review`, `acknowledge`
  or Confirm. No new endpoint or publishing/recovery algorithm was introduced.
  Unresolved/ambiguous retained attempts and an existing browser publishing receipt
  pause automatic preview. The manual preview and recovery actions remain available.
- Show loading immediately. Replace only the returned review region, not the page
  header or advanced-scope selections. Interrupted rebuilds keep the old approval
  disabled and require an explicit rebuild; connection loss is not a Shopify
  mutation failure and is never automatically retried. The existing signed-token
  freshness checks still decide whether confirmation is valid.
- Recovery opens with the actual retained state and the explicit next action.
  Shopify IDs, operation handles and raw evidence are in technical disclosures.
  Candidate ID entry, optional media-adoption acknowledgement, missing-variant
  review and any separately approved continuation retain their exact form fields.
  No reconciliation starts on page load. Media needing explicit mapping approval
  remains open for inspection; existing verification is never silently upgraded.
- Link collection issues only to their exact existing `source` selector and
  `#individual-mapping`. Link product history only to real operation IDs. Preserve
  product context on workspace/review/recovery return links.

Responsive checks use the same Batch 1 tokens, review tables and native controls.
Temporary synthetic fixtures block external transport and every execution action.
Actual development/production Shopify acceptance is not inferred from these tests.
Native Safari/physical iPhone acceptance remains an operator check where browser
automation is unavailable or interrupted. Do not expand later page audits here.

### Remaining audit items

Collections/Relationships/Intake list pages, Orders diagnostics, Production
Operations, Shipping Packages and administration remain separate later batches.
Existing baseline migration expectations, taxonomy VM setup and independently
reproduced legacy status/relationship-test failures are documented, not corrected
by this UI-only batch. No release is authorised by this register.

## Later audits and acceptance

Next pages: Collections, Relationships, Catalogue Intake, Orders diagnostics,
Production Operations, Shipping Packages, administration. Do not redesign them in
Batch 1. Electricity/equipment costing and Rescan Product Images remain deferred.

Require signed shared/override save, filters, inherited removal, date/BOM/shipping
regressions and real rendered desktop/tablet/phone checks. Native Safari access
was unavailable during this run; physical iPhone Safari remains a manual gate.
Synthetic local checks do not claim live Shopify acceptance. No release, external
permission changes or Unraid actions are authorised by this register.

## Navigation, Settings and System

Dashboard is a standalone destination. The shared desktop sidebar and mobile More
drawer use the same accordion tree: Catalogue (Products, Collections,
Relationships, Catalogue Intake, Local Taxonomy); Shopify (Orders, Promotions &
Discounts, Discovery); Settings (Main Settings, Scheduled Tasks, Connections);
System (System Status, Scanner, Tasks, Event History); and Operations (Production
& Operations, Shipping Packages). The current section opens for deep links; opening
another section closes it. Existing route URLs remain valid. The existing mobile
bottom shortcuts are Dashboard, Products, Orders, Production & Operations and
More. Production & Operations appears only to administrators, consistent with
its existing route permissions; the full navigation remains in More.

System → Tasks is the operational view of persisted task state and the existing
manual Run Now action. Settings → Scheduled Tasks changes each existing schedule
through its original per-task handler: each task has its own Save task action and
explicit Saved/Unsaved/Not saved feedback. There is no all-or-nothing bulk save.
The automatic publisher still needs its separate store-bound authority. System →
Event History displays existing catalogue receipts and links to durable task-run
history; no records or retention policy are changed.

Settings → Main Settings is a read-only allowlist of effective configuration,
sources and change locations. Container environment/mount settings are changed in
Unraid and require container recreation/restart; existing database-backed settings
retain their established forms. Secrets appear only as Configured/Missing, never
as values. Settings → Connections retains the existing explicit Shopify connection
test and deployment-owned restrictions. System → System Status uses local path,
filesystem, runtime and persisted task evidence only. It does not probe Shopify on
load or access the host Docker socket. Git revision is embedded at image build;
the image tag and host process/worker liveness are unavailable without a separate
restricted integration, so the UI labels them unavailable rather than guessing.
