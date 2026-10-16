# M3A.9.5 — storefront customisations: staged setup and acceptance

## Readiness and boundaries

Four separate states must not be confused:

1. Local authoring: existing templates/pins/shared JSON/product overrides.
2. Reviewed Shopify configuration: product-owned public JSON independently read back.
3. Theme activation: merchant installs the Shopify-hosted app block on a chosen template.
4. Purchase acceptance: actual cart, checkout and resulting order retain correct answers.

This release implements/tests 1–2 and supplies staged theme-extension source/fixtures.
It does **not** claim 3–4 have happened. The merchant has no linked CLI project yet and
is considering Tinker; actual installed theme name/version and app-block placement
remain unverified. No live Shopify writes, theme changes, checkout or Discord sends
were performed during development. An offline order-shaped fixture is not a real order.

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

- Visible `Label · stable_id`: readable selected labels/answers.
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

## Shopify CLI and extension deployment (merchant-controlled; not performed here)

Docker publication does not install Shopify-hosted assets. Do this locally on a machine
with Shopify CLI 3+, its current supported Node runtime, Ruby/Bundler and app-development
permission. Do not expose the Unraid admin port or send Client Secrets into Liquid.

1. Follow Shopify's existing Dev Dashboard app migration/link guide below. Create an
   extension-only CLI workspace with `shopify app init`; link with `shopify app config link`
   to the **existing installed TLC app**, not a replacement app. Inspect imported app
   settings before deploying—do not change OAuth credentials/scopes/application URLs
   accidentally. Keep generated local config/credentials outside Git here.
2. In that linked workspace run `shopify app generate extension`, select **Theme app
   extension**, and name it TLC customisations. Keep Shopify's generated extension
   identity/uid; copy this repository's blocks/ and assets/ into it and use its theme
   type/name/handle configuration. Remove only the generated demo block in that new
   extension, not other existing extensions. Do not invent an app ID or extension uid.
3. Run `shopify app build`. This repository validates source/schema/offline browser
   behaviour; an authenticated CLI bundle/build was unavailable here and is a required
   deployment gate. Resolve any Shopify theme-check errors before proceeding.
4. If needed preview against a suitable test/dev store and explicitly chosen draft
   theme using `shopify app dev --theme <verified-theme-id>`. Do not accept an implicit
   theme replacement or automatically deploy to the live storefront.
5. Run `shopify app deploy --no-release`; inspect the staged version in Dev Dashboard.
   Record this repository's exact Git revision with that app version. Release the selected
   extension app version only after explicit review. The Docker image revision is not
   evidence of the deployed Shopify app version. Keep the previous app version for rollback.

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
