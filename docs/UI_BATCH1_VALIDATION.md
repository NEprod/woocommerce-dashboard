# Batch 1 implementation and validation — 19 November 2026

Status: Batch 1 implemented on `develop` and approved for a development-only
release. Unrelated Cloudflare/customer-photo working-tree files are preserved.
The separately approved development app permission addition is verified below.
No Unraid deployment, production change or Orders import was performed.

## Changes

Dashboard: shorter Orders overview, shared test-order filter with verified
development default, actionable webhook/import warnings, cached store-bound
linked parent/variant counts, separate Draft/Active/channel-publication evidence,
missing-source counts in Local Catalogue Health, contextual connection/discovery
links instead of the large synchronisation instruction panel.

Products: aligned controls and action groups, concise evidence disclosures with
full history links, Scan Required navigation to the existing scoped local scan,
and the confirmed `issue=all` API KeyError corrected. Existing filters,
collection grouping, pagination and permissions remain unchanged.

Metadata: standard forest section headers, full-width descriptions, normal visible
Markets section, real section anchors and optional non-scrolling current-section
highlight, titled Origin & Customs, consistent controls and native checkboxes,
sticky desktop editing sidebar, phone Validate/Save dock with explicit shared
acknowledgement. The two acknowledgement controls mirror check and uncheck.
The local scan refuses unsaved drafts and missing scope acknowledgement.

No publishing engine, transport, worker, scheduler, signed review, recovery,
schema, inventory/BOM, image-upload or customer-customisation implementation was
changed. Server-side existing revision/scope validation remains authoritative.

## Exact repository files

Application:

- `app/dashboard.py`
- `app/orders_workspace.py`
- `app/products_browser.py`
- `app/routes.py`
- `app/static/assets/css/custom.css`
- `app/static/assets/css/metadata-taxonomy.css`
- `app/static/assets/js/metadata-editor.js`
- `app/static/assets/js/products-browser.js`
- `app/templates/dashboard.html`
- `app/templates/edit_products.html`
- `app/templates/metadata_editor.html`
- `app/templates/orders/overview.html`
- `app/templates/packaging/_product.html`

Tests/documentation:

- `tests/test_phase2_dashboard.py`
- `tests/test_ui_foundation.py` (new)
- `tests/javascript/packaging.test.mjs` (models new validation-status DOM and asserts it)
- `tests/javascript/metadata-editor-ui.test.mjs` (new)
- `tests/browser/ui_foundation_fixture.py` (new, disposable localhost data only)
- `docs/DESIGN_SYSTEM.md`
- `docs/UI_OPERATOR_REGISTER.md` (new)
- `docs/UI_BATCH1_VALIDATION.md` (this file)

## Automated results

| Suite | Passed | Failed | Finding |
| --- | ---: | ---: | --- |
| Dashboard, Products, Orders/webhooks, metadata/save/scan, inheritance, shipping editor, status dependencies | 278 | 1 | Existing Orders downgrade-test message expectation |
| Manual worker, shared continuation, Markets, publication/sales, sale editor, inventory, packaging, customisation designer | 286 | 5 | Existing migration expectations and missing local barcode library |
| Relevant JavaScript, including metadata taxonomy | 108 | 1 | Existing VM fixture omits `productionDesigner` |

All new Batch 1 focused tests pass (10 Python, 3 JavaScript). All 96 tests in the
targeted JavaScript set excluding the independently baseline-failing taxonomy
fixture pass. No new regression remains in these runs.

The initial pre-change Python set was 161 passed / 1 failed. Five additional
broader failures were separately reproduced in a complete unchanged-HEAD archive
under a temporary directory. The JavaScript taxonomy failure was also reproduced
against unchanged HEAD. No baseline failures were edited away or skipped to
present an all-green result.

Baseline Python failures:

- `test_shopify_orders.py::test_nonempty_orders_downgrade_requires_approval`
- `test_shopify_publication_sales.py::test_migration_fresh_upgrade_uniqueness_and_restart`
- `test_inventory.py::test_unassigned_upgrade_keeps_manual_location_and_all_existing_rows[False]`
- `test_inventory.py::test_unassigned_upgrade_keeps_manual_location_and_all_existing_rows[True]`
- `test_inventory.py::test_label_svg_uses_real_code128_and_qr_and_shares_identifier`
- `test_packaging.py::test_six_seeds_unknown_measurements_restart_and_no_cancelled_tables`

The assertions pin older migration heads/error messages; current head is
`0025_promotions`. The label test lacks the local `barcode` module. No migrations
or dependencies were changed in this UI batch.

`git diff --check`, changed Python compilation and changed JavaScript syntax
checks passed.

## Real rendered checks and screenshots

Before desktop references are the three supplied full-page PDFs (rendered PNGs);
before phone captures are the running development UI. After captures are an
isolated synthetic local fixture, **not an Unraid deployment**. The fixture blocks
outbound API requests and uses its own temporary SQLite/catalogue. Its test-order
default is legacy/excluded because it has no authenticated real store proof;
the verified-development default is covered by isolated application tests.

Nine page/viewport checks passed: three pages at desktop, tablet and phone sizes.
Measured content widths were approximately 1371, 975 and 371px, with no page-level
horizontal overflow. Full descriptions matched the form width and Markets was
visible at every editor size. Sidebar position was approximately 12px from the
viewport top while editing lower dimension fields, with Save visible at 328px.

Phone interaction checks passed: explicit check/uncheck mirrors desktop; Save
without acknowledgement is blocked; acknowledged no-change Save rewrites no
source and starts no scan; invalid price validation retains the entered draft.
No browser JavaScript errors were captured during these checks. Native Safari
access was unavailable, so actual iPhone Safari/keyboard acceptance is outstanding.
The disposable localhost fixture was stopped after validation.

Screenshot files are retained in the session's `ui-batch1` visualisation folder:

- `dashboard-before.png`, `products-before.png`, `editor-before.png` — full supplied PDF references
- `<page>-before-mobile.jpg` — current running development UI
- `<page>-after-desktop.jpg`, `<page>-after-tablet.jpg`, `<page>-after-mobile.jpg` — synthetic preview
- `editor-sticky-desktop.jpg` — lower-field/sidebar view

## Orders-access finding and approved correction

See [the operator register](UI_OPERATOR_REGISTER.md#confirmed-orders-access-finding--19-november-2026)
for exact fields, evidence, scopes and production-isolation requirements.

`read_orders` is granted to the verified development installation. The recent test
order is readable, but exact full import queries return `ACCESS_DENIED` at
`order.customer`, explicitly requiring `read_customers`. The current dashboard
has zero saved orders and a failed `order_read` receipt; excluding tests on
Dashboard was an independent display problem, not the import cause.

Only the already implemented `customer { id displayName }` lookup requires that
additional scope. Order contact/delivery fields are used for operator display;
there is no implemented automatic customer-email sender. Photo ownership binds
shop/order/line/product/cart evidence, not the customer profile ID.

The operator separately approved adding **only `read_customers`** to the development
Product app, retain all existing scopes and unchanged extension assets/proxy, and
approve the existing installation's new permission. Check protected-data field
configuration first; development-only access is supported without production
review, but the app's exact distribution/protected-field selection has not been
independently verified here. No extra customer-data scopes, `read_all_orders`,
reinstall or token rotation is justified by current evidence.

Applied development app version `development-orders-customers-20261119`
(`gid://shopify/Version/1248747749377`). Its released module configurations were
read back before release: only `app_access.scopes` gained `read_customers`.
All existing proxy, home, branding and webhook settings were preserved. The
development preview received the same scope with all five unchanged, hash-checked
customer-fields assets included; no other preview module was replaced.

Fresh client-credentials verification confirmed the same Shop, App and existing
installation, all 16 previous grants plus `read_customers`. `TLCOrdersIndex`
returned the development test order; both exact `TLCOrderRead` and
`TLCOrderReadWithCart` succeeded with customer ID/display name readable and no
GraphQL errors. No further protected-data permission or scope was needed for
these implemented queries. Customer data and credentials were not logged.
The existing Anniversary storefront fields, including photo upload, still render.

After manually updating the development container, the operator runs existing
Orders Sync Now. No import was run by this release task.
The running in-memory token was not extracted; the live UI confirms managed client
credentials and its import phase, while API reproduction used fresh credentials
for the same verified app/installation.

## Release readiness / next pages

Approved for development-only publication. The final repeat produced the same
278/1 and 286/5 Python results and 108/1 JavaScript result documented above, with
no new failures. This is not a claim of full live acceptance: after manual image
update, complete desktop/mobile and physical iPhone Safari acceptance and run
Orders Sync Now. No production image or Unraid deployment is authorised.

Next UI audits: Collections, Relationships, Catalogue Intake, then Orders,
Production Operations, Shipping Packages and administration, using the register.
