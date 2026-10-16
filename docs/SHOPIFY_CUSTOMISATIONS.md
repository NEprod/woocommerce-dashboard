# M3A.9.5 — storefront customisations: setup and acceptance

## M3A.9.5.2 — read-only historical order capture

The cart correction tlc-customisations-cart-604e504d4cae (1170069815297) was separately
released and confirmed Active. The merchant subsequently reported a test-mode order
#TLC-1001 with two independent 16bit-0001 lines and private snapshots. The Orders
foundation parses the exact existing schema, preserves labels/typed answers/revision,
and does not modify the extension or need product configuration republication.

The new read_orders scope must be staged unreleased and separately approved before
live import. Fixture/browser/Docker checks do not establish actual #TLC-1001 values.
Storefront validation, accelerated-checkout limitations, app UID and theme controls
remain unchanged. See [Orders acceptance](SHOPIFY_ORDERS.md) for the two-line check.

## M3A.9.5.1 — cart validation and readable properties

M3A.9.5 was released as `tlc-customisations-m3a-9-5-20261016` (version
1169951948801). The merchant confirmed its block renders on unpublished Copy of
Tinker, including separate Freddie/5 and Ellie/6 cart lines. The reported blank-name
addition is a real defect. The correction must be staged **unreleased** for approval.

### Confirmed causes and correction

Shopify's [Horizon component delegation](https://github.com/Shopify/horizon/blob/main/assets/component.js)
invokes on:submit handlers during **document capture**. Its
[product form](https://github.com/Shopify/horizon/blob/main/assets/product-form.js)
builds FormData and starts AJAX before a later form-target listener can cancel it.
Our old form-capture validation ran too late: the offline regression recorded one
cart-add request with an empty name, followed by the validation error. The previous
helper-only browser fixture did not model the earlier theme handler.

Separately, the hidden input named id shadows form.id. Using that property for the
app fields' form attribute broke external control association and native Enter/HTML
validation. The fix reads getAttribute('id') on bind/reset and tests input.form identity.

The guard now runs at window capture **before document capture**, regardless of script
registration order, and handles only event.target === its verified owned form. Invalid
submissions stop before the theme/request; valid submissions use the normal theme or
native handler. There are no global click blockers, key overrides, fetch/prototype
patches or replacement cart APIs. Native Enter works with corrected association.
Listeners are removed/rebound on disconnect or form replacement. Validation reads
current controls, rejects required whitespace, clears stale validity and removes stale
owned properties from serialization rather than retaining an old answer.

Real Chromium and touch/mobile-emulated WebKit tests use a fictional local cart server,
with document capture registered before the app asset. They cover click/tap/Enter,
native requestSubmit, synthetic AJAX submit, independent products, quantity changes,
reload, replacement/reconnect/reset and simulated checkout/order-attribute retention.
The exact installed theme asset/version was not fetched: the local Themes panel
stalled and the merchant explicitly authorised proceeding without a draft preview URL.
Shared Shopify source plus the reproduced mechanism are code evidence, not a claim of
corrected live Safari acceptance. Physical iPhone Safari remains an acceptance gate.

### Public labels and historical identities

New visible keys are escaped, bracket-safe **field labels only**, for example:

```text
Recipients Name: Freddie
Anniversary Years: 5
```

Duplicate labels receive readable numbered suffixes without dropping answers. A label
beginning with '_' is prefixed with "Answer " so it cannot hide an answer or overwrite
reserved private properties. Existing HTML-entity escaping remains unchanged.

_tlc_configuration and _tlc_personalisation remain private line-item properties. JSON
v1 retains product_id, revision, stable field/choice IDs, type, original label, typed
value and display text; additive property_key records the exact visible cart key.
Old snapshots without property_key remain interpretable by retained ID and label.
No authored schema, template, inheritance, SKU, variant or public configuration changes.

Horizon cart-products.liquid excludes underscore-prefixed properties; drawer and full
cart share that renderer. Shopify documents private properties as
[hidden at checkout but retained in Admin and Liquid/Ajax](https://shopify.dev/docs/api/ajax/reference/cart#private-line-item-properties).
[LineItem.customAttributes](https://shopify.dev/docs/api/admin-graphql/latest/objects/LineItem#field-LineItem.fields.customAttributes)
is the future order-API surface. Tests round-trip that shape, not an actual order.
Live order readback requires a separately authorised test order and appropriate access;
no read_orders scope is added. Private is not secret/tamper-proof. Other themes require
their own visibility check. Existing cart lines keep old technical keys until manually
removed/re-added by the merchant; there is no automatic cart rewrite.

### Accelerated checkout and server-side limits

Buy with Shop can bypass the product-form submit path through Shopify-owned payment
controls. This script does not claim universal enforcement there. TLC's installation
was confirmed as Basic. Shopify's [Functions availability rules](https://shopify.dev/docs/apps/build/functions#availability-of-shopify-functions)
permit public App Store apps containing Functions on any plan, but custom-app Functions
require Plus. Cart and Checkout Validation Functions are the supported server-side
mechanism; deployment in this private app on Basic is not assumed supported. A suitable
public validation app or another supported arrangement requires separate evaluation.
No new app, scope, validation Function or checkout UI is introduced.

Merchant mitigation: on the **unpublished personalised product template**, open Product
information → Buy buttons and turn off **Show accelerated checkout buttons** (exact
wording can vary), then save only the draft theme. Disable incompatible quick-add and
featured-product purchase shortcuts there. Do not disable Shop Pay/payment methods
globally. See Shopify's [template-level instructions](https://help.shopify.com/en/manual/online-store/dynamic-checkout/add-buttons).
No theme setting is changed automatically. Direct cart requests, form.submit(), disabled
JS, quick-add and third-party flows remain outside this product-page guard.

### Staging and controlled acceptance

Stage `tlc-customisations-cart-<shortcommit>` using the existing CLI config and UID,
--no-release and exact source-control URL. Compare settings against the active version
above; leave it active. No Docker rebuild or migration is needed for extension-only
runtime changes. A staged version is **not available** merely by opening a draft theme.
Separate approval to release the inspected candidate must precede hosted acceptance:

1. Release the exact inspected candidate only after approval. Confirm its UID, unchanged
   app configuration and Active state. Rollback target is the released version above.
2. Open Copy of Tinker and confirm its Draft badge; do not publish it. Keep one TLC
   block on the main product form, using the existing enabled pilot configuration and
   namespace `app--434537660417--tlc_customisations`. No configuration republish needed.
3. Disable accelerated checkout for this personalised draft template as described above.
4. Use a clean test basket or manually remove only approved old pilot lines. Blank or
   whitespace Recipients Name: click/tap Add to Cart and test Enter. Expect a visible
   error and **zero** new items. Test physical mobile Safari and desktop Chromium.
5. Add Freddie/5, then Ellie/6. Verify two distinct lines and readable labels in drawer
   and full cart. Increase Freddie's quantity without changing Ellie's data. Reload and
   navigate to checkout review **without placing an order**; no technical keys should
   display. Check a normal product and supported variant/form rerender behaviour.
6. Stop on connection errors or incompatible shortcuts. Actual order-API readback needs
   a separately authorised test order; neither theme edits nor orders occur in staging.

Rollback only after approval: from shopify/, run
`shopify app release --path . --version tlc-customisations-m3a-9-5-20261016` and verify
it is Active. This restores the earlier asset, not product/cart/order data. It also
restores the original validation defect, so do not sell through the affected flow.

## Readiness and boundaries

Four separate states must not be confused:

1. Local authoring: existing templates/pins/shared JSON/product overrides.
2. Reviewed Shopify configuration: product-owned public JSON independently read back.
3. Theme activation: merchant installs the Shopify-hosted app block on a chosen template.
4. Purchase acceptance: actual cart, checkout and resulting order retain correct answers.

The Docker milestone implements/tests 1–2 and supplies staged theme-extension source/fixtures.
It does **not** claim 3–4 have happened. Local CLI linking and a clean extension build
were subsequently verified on 16 October 2026, as recorded below. Actual installed
Tinker name/version and app-block placement remain unverified. Unreleased extension
staging is separate from release; no live product mutation, theme change, checkout
or Discord send is part of the staging checkpoint.
An offline order-shaped fixture is not a real order.

Tinker is a Shopify Horizon-collection theme. Its current shared Horizon source uses
`BuyButtons-ProductForm-SECTION` and native `FormData(form)` submission. The block
defaults to that form ID; a different theme/section needs its explicitly verified ID.
Verify actual installed Tinker behaviour before enabling sales; source compatibility
is not a live-store test.

## Authoring, publication and status

In the normal product/shared editor → Customer Customisations, set storefront intent
only for the chosen pilot. Absence inherits/defaults false; false explicitly disables.
Keep stable field/option IDs, pin revisions and sparse patches. Save Locally, wait for
targeted scan completion, then Products → Review & Sync. Saving never publishes.

For an existing verified product choose **Customer-configuration-only review**; do not
also select inventory-only. Inspect the public fields, enabled state, revision and
shared definition access. Explicitly confirm once. The scope changes only configuration
and access, not prices, variants, product status/channels, media or taxonomy. New
products use the existing full reviewed Create. Full reviews may include public config.

Identity & History retains `storefront_configuration`: state, enabled, revision,
checked_at, definition_id, metafield_id and Shopify-returned expanded namespace.
`customisation_update` retains the signed scoped plan/outcome. Pending stages are
committed before writes. A verified configuration is not proof of theme activation.
The editor displays cached configuration state/local pending changes. Normal full
comparison remains required before claiming all product domains are Up to Date.

Read-only recovery never replays an uncertain set/create. Definition-only uncertainty
can resolve to **review_required** if reads establish the definition and durable evidence
proves no value request was sent. A new signed review may then send that unsent value.
Uncertain value writes remain blocked until exact independent readback matches; a read
of old/absent data does not prove the timed-out request failed. No automatic rollback,
definition recreation, deletion or ID adoption is performed.

## Public contract and customer answers

Product metafield: `$app:tlc_customisations.configuration`, JSON; app-owned,
Admin MERCHANT_READ / Storefront PUBLIC_READ. Liquid uses its actual expanded
`app--NUMBER--tlc_customisations` namespace, copied from retained readback, not the
OAuth Client ID. Definition access is shared; only reviewed product values change.

Public JSON includes schema_version 1, enabled, ordered fields and deterministic
revision. Allowed field content: stable id/type, label, required, order, help text,
placeholder, type-specific bounds, defaults and sanitized choices (id/label/value).
Private production/operational data, unknown keys, template IDs/pins, proof policy,
filesystem paths and full authored JSON are excluded. Labels/help are intentionally
public—never put secrets there. Existing evidence compression/size limits apply before
writes; no new arbitrary field-count cap, database table or credential store.

Supported controls: text, multiline, number, dropdown, radio, checkbox, checkbox group,
date and colour (palette or colour picker). Existing aliases are canonicalized. Disabled
or removed inherited fields do not render. All enabled photo/file fields block reviewed
configuration publication, including optional uploads. There is no file input, public
upload endpoint, public customer file storage, signed upload URL or scan/retention flow.

The app block associates controls with the exact main product form; it never writes
the theme's variant ID or replaces native cart requests. Native line-item properties:

- Visible `Label`: readable selected labels/answers (M3A.9.5.1; older carts keep their
  original `Label · stable_id` keys until explicitly re-added).
- `_tlc_configuration`: immutable public configuration revision.
- `_tlc_personalisation`: JSON v1 containing product_id, revision and a historical
  field snapshot (stable id, type, label, typed value, readable display).

The underscore fields are private cart properties by Shopify convention, **not secret
or tamper-proof**. Customer data is untrusted, not proof of authorisation. Preserve the
snapshot on each line; never resolve an old order against today's template. Shopify
order LineItem.customAttributes carries these properties; order import is deferred.
The same variant with different properties forms distinct cart lines; quantity edits
must address the cart line key, not only variant ID. This block does not edit cart lines.

Visible values/labels use HTML-entity encoding, including slashes: current Horizon cart
source interpolates properties without explicit escaping and has a special /uploads/
link branch. This prevents customer markup/link injection through that path. Typed raw
answers remain in the private JSON. The contract records visible_encoding=html_entities.
Other themes that escape again may show literal entities; check apostrophes, ampersands,
angle brackets, multiline values and cart drawer/full cart/checkout/order presentation.
Do not weaken escaping or claim compatibility until that is checked on the chosen theme.

## Shopify CLI setup verified locally — 16 October 2026

The existing app was linked using its merchant-confirmed Client ID, not by creating
or replacing an app:

- App: TLCDashboard, `gid://shopify/App/434537660417`.
- Client ID: `f9498b5a7e0393d9cddbb49defb4cacd` (public identifier, not a secret).
- Organisation: Tender Loving Creations (`240230265`).
- Local workspace: `shopify/`; imported configuration: `shopify/shopify.app.toml`.
- CLI 4.9.4, Node 24.22.0, esbuild 0.28.1, npm workspaces.

The initial import and a fresh read-only pull are semantically identical: 11 scopes,
no optional scopes, legacy install flow false, app URL
`https://shopify.dev/apps/default-app-home`, embedded false, no redirect URLs,
webhook API 2026-10 and no configured webhook subscriptions. Scope ordering was
normalised only. Do not replace these values with web-app scaffold defaults.
Distribution is not represented in TOML. The fresh CLI organisation reports
BusinessPlatform and Dev Dashboard shows the store-owned Tender Loving Creations
organisation, with a single installed Basic-plan TLC store, jrg9sr-yy.myshopify.com.
There is no Partner Distribution card. Shopify's [distribution documentation](https://shopify.dev/docs/apps/launch/distribution/select-distribution-method)
confirms store-organisation apps use the Installs card rather than selecting Partner
public/custom distribution. This existing installation model is preserved, not converted.
No new scope, public Unraid endpoint, OAuth callback, webhook or app reinstall is needed.
The store installation was matched to the Client ID supplied by the merchant; no
Unraid Admin token was retrieved or rotated for an independent installed-token query.
The preflight version list shows `tlcdashboard-5` as active, with four older inactive
versions. Its exact version GID is `gid://shopify/Version/1168958980097`. Its Dev
Dashboard detail lists app details, access scopes and webhook API only: no existing
theme extensions. The intended staging difference is one new theme extension, not
replacement/removal of another extension. Recheck this rollback reference immediately
before release; another administrator may update it.

esbuild's global install warning concerned npm's install-script allowlist; its
postinstall exited 0 and the native compiler worked. No global npm-policy change or
CLI reinstall was necessary. Actual Theme Check exposed two packaging issues: absent
`locales/` and a 12043-byte script exceeding the 10000-byte app-block threshold.
An empty default locale and a reproducible asset build correct those issues. Readable
source is now `shopify/src/tlc-customer-fields.js`, byte-identical
to the original implementation. The generated asset is 8778 bytes. Shopify CLI added
the extension's local uid; preserve it, do not regenerate a duplicate extension.

Staging validation additionally rejects source directories inside the extension:
only assets/, blocks/, locales/ and snippets/ are deployable. Readable src/ therefore
lives at workspace level, outside extensions/. The build and tests use that source
and still generate exactly the same deployable JavaScript asset.

`npm run build -- --no-input` regenerates assets, enforces the byte limit and runs the
Shopify app build/Theme Check. Configuration validation returned valid=true/issues=[];
the corrected build had no Theme Check errors. Tests against the generated asset:
23 JavaScript checks, 20 focused Python cases and 14 offline real-browser assertions.
No live theme/cart/order acceptance is claimed. The setup/build checkpoint can be
committed to develop independently of the Docker release, which remains at 550c0e7.
The generated JavaScript SHA-256 is
`809f841f5f3505449fc770244896ade3c44a9c6b574cd1d113306e5f3fb70e79`.

Local `.shopify/` state and node_modules are ignored. Login credentials remain in
Shopify CLI's normal protected user storage, not app TOML, Liquid or repository files.

## Unreleased staging and subsequent release/rollback

Docker publication does not install Shopify-hosted assets. The linked CLI workspace
already exists; no `app init`, new app or replacement extension is needed. The tested
CLI build succeeded using its bundled Theme Check; no Ruby/Bundler upgrade was required.
Do not expose the Unraid admin port or send Client Secrets into Liquid.

1. Review/checkpoint the local setup/build files first. In `shopify/`, run
   `npm ci --ignore-scripts`, then `npm run build -- --no-input` with CLI 4.9.4 available
   on PATH. Run `shopify app config validate --path . --no-input` as a final check.
   Do not edit the generated asset; edit src/ and rebuild. Do not regenerate its uid.
2. Re-read and review the existing app's current settings immediately before deployment.
   Stop if scopes, URLs, auth, webhooks, distribution, existing extensions or definitions
   would change or be removed unexpectedly. The CLI workspace is extension-only;
   no shopify.web.toml or new server is required. No dev store or URL-update mode was set.
3. Approved staging target: `tlc-customisations-m3a-9-5-20261016`. After committing
   and pushing the verified setup, run from shopify/:

   ```sh
   shopify app deploy --path . --no-release --no-input \
     --version tlc-customisations-m3a-9-5-20261016 \
     --message "M3A.9.5 customisation extension staging; no release" \
     --source-control-url "https://github.com/NEprod/woocommerce-dashboard/commit/<verified-source-commit>"
   ```

   Inspect the staged app version in Dev Dashboard; do not use force/allow-deletes or
   accept unrelated changes. It must contain only the reviewed configuration and
   TLC customer customisations extension, retaining its uid above. Confirm the staged
   version is not Active and tlcdashboard-5 remains Active. If it reports configuration
   changes other than adding the theme extension, stop before any release.
4. Releasing that exact version is a second explicit approval step. Use Dev Dashboard
   or the following **only after approval**, from shopify/:

   ```sh
   shopify app release --path . --version tlc-customisations-m3a-9-5-20261016
   ```

   Review the interactive configuration/extension diff; do not add force or bypass
   flags. Verify the selected version is Active and product synchronisation still
   connects read-only. App-version release is separate from Docker publication and
   does not activate a theme block. Keep tlcdashboard-5 for rollback. If a rollback
   is separately approved, release the prior version:

   ```sh
   shopify app release --path . --version tlcdashboard-5
   ```

   This removes the new extension from the active app version; Shopify may ask for
   explicit confirmation of that removal. Do not bypass it. Verify tlcdashboard-5
   is Active again. Rollback does not undo product metafield writes or cart/order
   properties, and theme-block placement needs a separate merchant-controlled check.
5. In Shopify Admin, use an unpublished Tinker copy and add one block to a supported
   product template, as below. No `app dev`, theme push/publish or live-theme modification
   is part of this plan. A future dev-session flow needs separately approved store and
   URL-update settings; do not allow automatic URL changes to the installed TLC app.

No app reinstall or new Admin API permissions are required for the JSON product
metafield: read_products + write_products are used. CLI staff/app release permission
is separate. No write_themes, read_orders, proxy, customer data or upload scope is added
to TLCDashboard. Existing media/inventory/publication requirements remain unchanged.

## Draft Tinker activation and controlled purchase acceptance

1. Keep a database/appdata backup and current immutable Docker image. Update only the
   Unraid develop image after review, retaining the same database/catalogue mounts,
   credentials and API versions. No new XML/environment variable or storage mount is
   needed. Migration head remains 0015_customisation_templates.
2. Confirm existing editor/products/startup/sales still work. Verify a Single Variable
   title is `Folder | Authored title`, identical components appear once, missing root
   title falls back to folder. No SKU, source image or Shopify identity changes.
3. Pick ONE existing retained verified pilot product. Add a required Name plus an optional
   message/choice. Set storefront intent true; save/scan locally. Confirm Shopify did
   not change. Build configuration-only Review, inspect exactly the public allowlist,
   acknowledge, confirm once. Check independent readback/revision/GIDs in History.
4. In Shopify Admin → Online Store → Themes, add/duplicate Tinker as an **unpublished
   draft theme**. Record its exact version. In Customise, open a product template that
   supports @app blocks; add **TLC customer fields** near the main Buy buttons. Copy the
   returned namespace into its setting. Leave form ID empty for the verified Horizon
   convention; otherwise enter the actual main product form ID. Add one block only.
   If the section cannot host @app, stop and choose a supported placement—no live patch.
5. Use a dedicated personalised-product template where possible. Disable theme quick-add,
   featured-product purchase shortcuts and accelerated/dynamic Buy Now for the pilot
   through merchant-reviewed theme settings. The block does not globally alter buttons.
   Preview that draft theme and verify mobile layout, labels/defaults/order/required
   constraints and all chosen field types. A connection error means do not sell it.
   Use a separately approved storefront-eligible test product for cart/checkout tests;
   do not silently make an existing Draft product Live or change channel assignments.
6. Add Ellie, then Jack for the same SKU/variant. Verify separate cart lines, change
   quantities/remove one line and check the other is unchanged. Repeat for a Variable
   Collection and Single Variable pilot: switch variant, verify variant/SKU and answers
   are attached to the intended line. Check cart drawer and full cart; theme section
   re-render/back navigation must not lose or duplicate values.
7. Complete a merchant-approved test checkout/order on an appropriately configured test
   store/payment method—do not take a real payment unintentionally. Inspect Shopify Admin
   order line properties and, when authorised, LineItem.customAttributes. Match each line
   ID/variant/name/typed answers/revision with the retained cart snapshot. Actual order
   persistence is a mandatory acceptance gate, not established by offline fixtures.
8. Revise one label after that order, publish only through a fresh review; confirm the
   old order keeps its original snapshot. Test a disabled/tombstoned inherited field,
   an access-only repair, disabled storefront intent and a required upload blocker.
   Do not activate the live theme until supported and bypass paths have been reviewed.

## Required-input security limits and deferred work

HTML/JavaScript validation covers the supported main form, including Horizon native
AJAX FormData. It is **not** server-side security. Direct cart requests, disabled JS,
quick-add/featured cards, dynamic checkout, Shop Pay, third-party cart code, Storefront
API and existing cart lines can bypass this placement. Missing/malformed answers must
be held for manual resolution; do not automatically produce an invalid order. No custom
cart/checkout validation Function is deployed. Shopify custom-app Functions may require
Plus; do not assume entitlement or claim universal required-field enforcement.

No live customer upload service, malware scanning/retention/order-file association,
paid extras, proof request/approval emails, Orders dashboard or order automation is
included. `proof_policy` remains local workflow metadata, not an automatic customer
control. A future customer approval checkbox must be an explicitly authored field.
No theme/menu/collection/variant creation is performed by the scoped config action.

## Disable and rollback

On the draft theme remove/disable the app block (or revert the saved draft template).
Use a reviewed storefront_enabled=false configuration to disable that product's renderer
without deleting Shopify definitions/values. Do not leave a customer-facing personalised
product purchasable without its required input path. Preserve historical order properties.
Revert the selected Shopify app version separately in Dev Dashboard if required, and
restore the prior immutable Docker tag independently. Rolling back Docker does not
undo remote JSON/configuration writes; retain the database backup and operation evidence.
No destructive database downgrade, product/definition deletion or automatic retry.

## Official contracts checked

- [Existing app → Shopify CLI](https://shopify.dev/docs/apps/build/cli-for-apps/migrate-from-dashboard)
- [Theme extension build/deployment](https://shopify.dev/docs/apps/build/online-store/theme-app-extensions/build)
- [Theme block configuration](https://shopify.dev/docs/apps/build/online-store/theme-app-extensions/configuration)
- [Metafield ownership/access](https://shopify.dev/docs/apps/build/metafields)
- [Metafields CAS](https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/metafieldsSet)
- [Native product properties](https://shopify.dev/docs/storefronts/themes/architecture/templates/product/overview)
- [Cart properties](https://shopify.dev/docs/api/ajax/reference/cart)
- [Order line customAttributes](https://shopify.dev/docs/api/admin-graphql/2026-10/objects/LineItem)
- [Shopify Functions availability](https://shopify.dev/docs/apps/build/functions)
- [Tinker](https://themes.shopify.com/themes/tinker/presets/tinker)
- [Horizon product form](https://github.com/Shopify/horizon/blob/main/assets/product-form.js)
- [Horizon cart display](https://github.com/Shopify/horizon/blob/main/snippets/cart-products.liquid)
