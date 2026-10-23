# Production time — existing customer-fields integration

## Source and capture

Local authoritative `product_info.json` uses `attributes["Lead Time"]`. The existing
informational-attribute publisher resolves inherited plus product-authored terms;
this milestone does not change that resolver, publisher, schema or metadata values.

The **same existing customer-fields app block** reads the published product
`tlc_d0eae6298fe8f4ab829a` list of Lead Time metaobject references. The attribute
namespace comes from the block's existing verified configuration namespace, replacing
`--tlc_customisations` with `--tlc_attributes`. For TLC this is
`app--434537660417--tlc_attributes`. Metaobject `name.value` supplies the wording;
multiple nonblank names retain published order, separated by a comma. No estimate,
fallback, new metafield, customer setting or variant-owned source is introduced.

The compact SVG clock row follows the existing customer controls. The block also
renders when Lead Time exists without enabled customisations. Neither source means
no storefront markup/spacing (apart from the theme editor's setup guidance).
The input belongs to the existing verified form. A static form-associated input
supports native metadata capture; once bound, the existing script owns one hidden
`properties[Production time]` input and maintains it across reset/rebind/FormData.
Visible values use the existing entity-encoding convention. Required answers are
still validated; production time is never a required customer answer. A colliding
customer field label receives the existing duplicate-label suffix, preserving its
answer and private snapshot identity. No other customer's label changes.

Lead Time is PRODUCT-owned: all variants use that product's published wording.
Each cart line captures its own value. No subsequent cart or order re-read uses
the current product metafield. Existing lines without this property stay unchanged.
The value is a client-submitted disclosure snapshot, not tamper-proof commerce data
or authority for prices, dispatch, shipping or fulfilment.

## Access: Liquid is not the Storefront API

This extension uses Liquid only: no Storefront API query/token is added. Current
Shopify documentation explicitly states that `access.storefront` controls the
Storefront API, not Liquid. An unchecked Storefront API access checkbox is **not**
by itself justification to change access for this Liquid implementation:
https://shopify.dev/docs/apps/build/metafields#storefront-permissions
https://shopify.dev/docs/apps/build/metaobjects/data-modeling-with-metafields-and-metaobjects

Previous read-only Admin inspection warned that the Lead Time references were not
storefront-accessible. Verify the rendered draft **as an ordinary customer**, not
just in the editor. If it is blank, inspect the exact referenced records, status,
namespace and Liquid output before proposing any narrowly scoped access change.
No definition/access/scope/product changes have been applied by this milestone.

## Cart rendering: optional small draft-only patch

The app already captures Production time first; Tinker's ordinary property loop
therefore normally displays it before answers on newly captured lines. The small
`shopify/theme-patches/production-time-cart.patch` guarantees that order explicitly
and skips the key in the remaining generic loop. It reads only the captured line
property, never current product metadata. `escape_once` handles both already encoded
app values and historical plain text safely. The shared `cart-products` renderer
is used for full cart and drawer. Other variant/options/property order is untouched.

Before applying, export the **current unpublished** Copy of Tinker. Check the patch
against that copy; stop on mismatch rather than overwriting edits. Do not push a
whole old theme export. No changes to `blocks/buy-buttons.liquid` are needed.

The inspected saved draft already positions the one customer-fields block after
variant selectors and immediately before buy-buttons. It also contains a redundant
`<strong>Production Time</strong><br/>` heading in the accordion text. The open
editor preview had a Lead Time binding absent from that saved template. Do not save
over an unknown unsaved editor draft. After the merchant has saved/reconciled those
draft edits, remove only the obsolete heading/binding from that Text block, retaining
Materials, Production Method and Personalisation. Never alter the live Tinker theme.

## Orders and existing evidence

Shopify retains public line properties in order-line `customAttributes`; existing
Orders import already stores the original pairs. No query, write, DB column or
migration is added. The parser separates Production time from customer answers,
avoiding false additional-answer warnings and false personalised flags on new
production-only lines. A historical typed customer answer actually named Production
time stays a customer answer. Duplicates/conflicts and malformed snapshots retain
their warnings and original evidence. Orders detail displays the captured production
time before answers. Previously imported lines carrying this property are reparsed
read-only; other saved parses and redaction notices remain unchanged.

## Checkout, shortcuts and customer notifications

Public properties are Shopify's native mechanism:
https://shopify.dev/docs/api/liquid/objects/line_item#properties
https://shopify.dev/docs/api/ajax/reference/cart#private-line-item-properties

No checkout app/layout modification is introduced. Native checkout/order-status
property visibility and ordering must be checked on the actual plan/surfaces. The
application cannot guarantee first position in Shopify-controlled checkout.

The existing block suppresses product-form accelerated payment containers **only
when customer fields are enabled**, because wallet shortcuts can bypass the submit
guard. Standard Add to cart stays guarded, while cart checkout/wallets operate on
already captured lines. Lead-time-only products are not subject to this restriction.
No global payment setting changes. Quick-add or alternate forms that do not include
the existing app block remain an existing limitation; do not advertise them as
validated for personalisation. No JS means personalisation still cannot be validated.

Inspect Settings → Notifications → Customer notifications → Order confirmation
without sending a customer email. Locate the existing per-line `line.properties`
(or the template's equivalent loop variable) renderer. If it already emits public
properties, no added renderer is needed. If it does not, the minimal optional
per-line addition is:

```liquid
{% if line.properties['Production time'] != blank %}
  <p><strong>Production Time:</strong> {{ line.properties['Production time'] | escape_once }}</p>
{% endif %}
```

For explicit first-position rendering, place that before the existing public
property loop and exclude `property.first == 'Production time'` in that loop.
Use the actual local loop-variable name. Preserve underscore/private filtering and
all other notification markup. Never use `line.product.metafields` for an old order.
Do not insert a second row if the template already renders it. Shipping notifications
may use different fulfilment-line variables: inspect rather than pasting blindly.
Do not replace whole templates, edit packing slips or send live customer messages.

References:
https://help.shopify.com/en/manual/fulfillment/setup/notifications/email-variables
https://help.shopify.com/en/manual/fulfillment/setup/order-status-page/understanding-order-status-pages

The actual saved notification templates/order-status page have **not** been
verified or changed. The read-only signed-in browser inspection was blocked by the
browser safety boundary; permission is needed before inspecting those live Admin
tabs. No notification success is inferred from local fixtures.

## Separate deployment surfaces

Docker deploys only TLCDashboard's Orders display/parser. It does **not** deploy the
Shopify-hosted app extension or apply theme patches. `shopify app build` validates
locally and does not deploy/release. CLI app info confirms TLCDashboard / Tender
Loving Creations; it reports no configured dev store. A released app extension may
affect installed blocks in live themes even without publishing a theme. Therefore
the extension must remain pending a controlled app-version/draft-preview approval;
do not run a global app release merely to test the unpublished theme.

The cart patch and duplicate-heading correction remain draft-only review artifacts
until the merchant reconciles the open editor draft. No live theme is changed.

## Controlled acceptance (not an assertion of live success)

1. Save/reconcile draft editor edits, retain an export, confirm unpublished theme ID.
2. Arrange a controlled extension preview/release with explicit approval for any
   possible live-block impact. Keep the current app scopes unchanged.
3. Test four products/states: customer fields + Lead Time; Lead Time only; customer
   fields only; neither. Check no empty row/spacing and no invented estimate.
4. Check inherited and product-added Lead Time after normal reviewed publishing.
   Inspect Liquid output as a customer before contemplating access corrections.
5. At desktop/iPhone width, check controls → clock row → Add to cart, variants,
   required/whitespace answers, multiple products, and same-SKU different answers.
6. In drawer/full cart, check Production Time first, one row, answer order, refresh
   and quantity changes. Existing lines without the property must stay unchanged.
7. Confirm personalised product Buy Now is not offered; test actual available cart
   wallets and lead-time-only product shortcuts. Check checkout property retention;
   don't claim Shopify-controlled row order without observing it.
8. With a merchant-authorised test order, confirm Shopify customAttributes and
   TLCDashboard display retain the purchase wording and same-SKU line identities.
   Later product Lead Time edits must not change that captured order.
9. Preview (do not send customer messages) confirmation notifications. Check actual
   order status/account order details with the test purchase. Document any surfaces
   Shopify suppresses and apply only separately reviewed minimal template changes.

Automated scope: API mocks and local cart/checkout fixtures are not real Shopify
checkout, wallet, notification delivery or paid-order acceptance.
