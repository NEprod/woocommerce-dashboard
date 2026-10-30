# Existing proof customisations — integration and acceptance

## Inspected baseline and the missing link

Local develop and the running Unraid container were both
`4e32742babb35d1b3f1344e13b49ac1f9f2e6929`. Only read-only container inspection
and Shopify Admin navigation were performed. No live scans, comparisons, writes,
orders, messages, settings changes or deployments were initiated.

Shopify Admin identified live theme **Tinker 4.3.0**, renamed **Live**, ID
`201872474495`, and unpublished **Copy of Live**, ID `202396205439`. Craft is not
the installed theme. The working source is the existing `tlc-customisations`
theme app extension, not a second Craft Custom Liquid personalisation form.
Safari temporarily reported a locked Mac, then worked on retry. Follow-up read-only
inspection confirmed one saved `shopify://apps/tlcdashboard/blocks/customer-fields`
block in `templates/product.json`, namespace `app--434537660417--tlc_customisations`,
empty custom form ID and placement between the variant picker and buy buttons.
The saved `snippets/cart-products.liquid` already loops over nonblank public line
properties and hides underscore-prefixed properties. No actual checkout, order or
sent-email acceptance is claimed.

The editor, canonical JSON, scalar proof inheritance and scoped scanner already
supported `customisation.proof_policy`: `none`, `optional`, `required`.
`public_configuration` explicitly omitted that key, and an existing test asserted
its absence and unchanged public fingerprint. Consequently the publisher correctly
found no Shopify-facing difference. The app block had no proof controls or capture.

## One existing storage and publishing path

```json
{"customisation":{"storefront_enabled":true,"proof_policy":"optional",
 "fields":{"dogs_name":{"type":"text","label":"Dog's Name","required":true}}}}
```

No local schema, inheritance, JSON storage or scanner changes are necessary.
Shared scalar proof settings retain existing product-override precedence;
template/shared/product customer fields retain their protected additive ordering.
Only public, enabled proof policy is added to the existing app-owned product
`$app:tlc_customisations.configuration` JSON. No new metafield or permission.

`none` remains omitted, including the default, so existing no-proof public values
and revisions are unchanged. Disabled storefront configuration stays disabled and
does not publish/render a proof requirement. Proof-only enabled Optional/Required
is supported; enabled-empty No Proof remains a configuration error. Disabled
customer upload fields remain blocked as before when enabled.

Save → existing containing-collection scan → saved revision → existing bounded
read-only comparison → Update Available → hourly authorised publisher or fresh
signed manual approval → existing CAS write/readback → Up to Date. Save and scan
make no Shopify request; no new publishing trigger, scheduler or eligibility rule.
Review & Sync explains Digital proof: No proof / Optional / Required rather than
only a changed configuration hash. All six directed transitions change revision.

## Storefront patch: the same block, not another component

Review these files together:

- `shopify/extensions/tlc-customisations/blocks/customer-fields.liquid`: one proof
  container after `data-tlc-fields`, before the unchanged Production Time row.
- `shopify/src/tlc-customer-fields.js` and its generated extension asset: same custom
  element, form, validation, native/AJAX FormData and section-reconnection handling.
- No theme files, theme settings or notification templates have been edited remotely.

No Proof: no proof controls, no proof property or snapshot addition.

Optional: after customer fields, “Would you like a digital proof before we make
your item?”, with “Yes, send me a proof for approval.” and “No, proceed without
a proof.” Neither is preselected; the existing guarded Add to cart requires one.

Required: after customer fields, “A digital proof will be sent for your approval
before we make your item.” No radio buttons; capture `Proof Required`.

Production time remains the first captured public property, customer answers keep
their order, and Digital proof follows them. Existing form ID, selected variant,
quantity, media, pricing and delivery presentation are untouched. A customer field
also labelled Digital proof is retained, with the proof property receiving a
unique suffix. Its exact property key is retained in the private snapshot.

The captured public `properties[Digital proof]` and private `_tlc_personalisation`
proof object belong to each cart line, not browser-only state or an order note.
Policy/choice/display/configuration revision are purchase snapshots; later product
edits do not reinterpret old lines. This is customer-submitted preference data,
not tamper-proof fulfilment authorisation.

Tinker's existing full-cart/drawer public-property loop already renders the new
property. No cart rewrite or additional renderer is needed. The existing optional
Production Time ordering patch is unchanged. Inspect actual installed cart output
before applying any further patch.

### If Craft is installed separately

Use this same app block in a supported product-information section that permits
`@app`; do not replace an existing customisation form. Set its existing Product
form ID setting to the **actual rendered main form ID** (Craft/Dawn commonly use
`product-form-SECTION_ID`; verify rather than pasting that placeholder). Tinker's
default remains `BuyButtons-ProductForm-SECTION_ID`. The unchanged binding rejects
the wrong form/section. Craft's normal cart/drawer public-property loops should
retain the value; this has not been validated against an installed Craft theme.
Stop if a separate legacy Custom Liquid form is present: reconcile that placement
before adding a block, rather than installing competing controls.

## Checkout, Orders and email boundaries

Public line properties are Shopify's supported cart/order mechanism. Standard
checkout controls its own presentation/order; no checkout layout extension or
Basic-plan checkout modification is introduced. Product-page accelerated wallet
containers remain suppressed by the **existing** enabled-customisation guard
because they can bypass form validation. Customers use guarded Add to cart, then
normal/accelerated cart checkout on captured lines. No global payment setting.
Direct cart APIs, JS-disabled purchase, quick-add/featured forms without the block
and arbitrary third-party purchase paths are not server-side validated; disable
those paths for personalised/proof products in the separately reviewed theme.
Do not claim universal required-proof enforcement or enable an untested bypass.

Existing Orders import stores original `customAttributes`. Its parser now recognises
the separate proof snapshot, cross-checks the visible property, and displays it
without pretending it is an authored customer field. Invalid, missing or conflicting
proof evidence stays available with a warning, never silently discarded. Production
Time and customer answer parsing are otherwise unchanged. No database migration.

Read-only inspection of the actual saved order-confirmation email found the public
property loops **inside gift-card-only conditions**. Ordinary product lines currently
omit these preferences. This is a confirmed missing email stage, not a cart or order
capture defect. No email template was changed, and no email was sent; Revert changes
remained disabled and the unchanged notification preview was reopened afterwards.

The review-only `shopify/notification-patches/order-line-properties.liquid` adds safe
public-property display for the ordinary branch without duplicating the existing
gift-card renderer. After backing up the notification, installation requires
**separate approval**:

- Before each existing `{% if line.gift_card and line.properties["__shopify_send_gift_card_to_recipient"] %}`,
  insert the supplied snippet unchanged. Three ordinary `line` branches were observed
  at source lines 500, 1196 and 2519 on 30 October; verify current context, not just offsets.
- Before the equivalent `component.gift_card` guard (observed at line 2191), insert
  the same snippet with its first assignment changed to
  `{% assign tlc_notification_line = component %}`.
- Do not change the existing gift-card blocks, private-property filters, RTL wrappers,
  prices or order summaries. Do not insert where a future template already has an
  ordinary public-property renderer. Preview all delivery branches and a gift-card
  fixture, then separately authorise any test email.

The addition uses captured properties with `escape_once`, not today's metafields.
It includes proof, existing customisations and Production Time only when actually
present on that purchased line. Actual email delivery/rendering still needs acceptance.

Orders is a read-only purchase viewer, not a proof delivery/approval system. No proof
file storage, approval links, approval state machine, approval/customer email sending,
production hold automation or fulfilment action is implemented here. Future work
must use purchased line ID/product/variant, captured revision and proof choice,
retain purchase history, and treat missing/conflicting preferences as manual review.

## Validation and safe deployment

Automated evidence uses copied fixture JSON, temporary databases and controlled
Shopify/Discord doubles, not the production store. The full lifecycle test executes
actual signed shared saves, containing scans, queued reads and hourly scheduler
entry for all six transitions, retaining one Shopify product identity, publishing
configuration once per change, verifying readback, restarting DB sessions and
skipping the next unchanged cycle. Additional tests cover manual variable updates,
local Draft protection, proof-only config, collisions, malformed evidence and
imported independent order-line snapshots. Browser contract tests exercise native
and Tinker-style AJAX submission, radio validation, variant/form replacement,
reset, separate cart lines/products, drawer/cart display and a **simulated** checkout.
They do not create a Shopify order or establish real email acceptance.

The desktop and 390-pixel mobile Chromium runs each passed all twelve browser
scenarios; optional/required screenshots were visually checked for readable
controls and overflow. This is not native iPhone Safari or a live Tinker checkout.
Both existing network-isolated Docker smokes passed with two/four workers and
`/app/instance` persistence: proof policy and separate order preferences survived,
accepted package assignment was not replayed, and the same 14-variant/53-image
operation completed after restart without repeated Create/upload/variant writes.

One unrelated Orders downgrade test also fails on the pristine starting commit:
`test_nonempty_orders_downgrade_requires_approval` expects older exception wording,
but migration 0021 safely refuses downgrade because retained manual approvals need
a paired backup. No migration or downgrade guard was changed; this baseline failure
is reported separately and excluded from the final bounded regression run.
Final bounded results: **237 Python tests passed, one baseline test deselected**
(2,888 warnings), and **123 focused JavaScript checks passed**. There were no
introduced failures in those selected suites. `shopify app build` completed with
the size diagnostic described below; no deploy/release command was executed.

Shopify's documented suggested JS budget is compressed. The asset is approximately
11.8 KB raw / 4.35 KB gzip. The installed CLI still emits a raw-size
`AssetSizeAppBlockJavaScript` diagnostic at 10 KB, although `shopify app build`
completes. This diagnostic is retained and disclosed, not disabled; no loader,
extra script or second storefront component was introduced to hide it.

Keep automation paused during coordinated deployment. Docker does **not** deploy
the Shopify-hosted extension. Do not let the publisher send proof policies while
the old extension, which ignores them, is still serving customers.

1. Back up `/app/instance` and retain the previous immutable dashboard image.
2. Deploy the dashboard candidate only under merchant control, automation paused.
   Preserve all current mounts; no scan/reset/migration is required for this feature.
3. Separately review/build the extension, then prepare an unreleased named app
   version (`shopify app deploy --no-release` only after approval). Review the
   retained size diagnostic before release. Creation is not activation. An app
   release can affect **live** installed blocks even without publishing a theme.
4. Use an isolated development store/draft theme preview with explicitly authorised
   fixture products for the checks below. No configured separate dev store was
   established during this task. Do not test purchase mutations on the live store.
5. Release the extension only after explicit approval and staging acceptance.
6. Publish test proof configurations only via the existing scheduled/manual
   authorised path, then approve normal production rollout and resume automation.

| Check (capture product/variant, revision, cart properties and operation ID) | Expected | Result |
| --- | --- | --- |
| Save each of six proof transitions; reopen; scoped scan completes | JSON persists, same scope/checkpoint; zero save-triggered Shopify calls | [ ] |
| Existing queued comparison, Live versus Draft | Live Update Available; Draft protected; proof summary readable | [ ] |
| Authorised manual/hourly sync, then unchanged next cycle | Same identity; configuration readback; Up to Date; no repeated write | [ ] |
| None, Optional (unselected/Yes/No), Required on product page | Nothing / required explicit selection / message without radios; fields unchanged | [ ] |
| Change variant, replace/reload section, mobile 390 px | Correct variant and exactly one property set; no overflow | [ ] |
| Two personalisations of same variant and different products | Independent proof/answer snapshots and cart line keys | [ ] |
| Drawer/full cart and authorised staging checkout | Readable public preference, no private fields; no missing proof through supported path | [ ] |
| Complete authorised staging test order | Exact line `customAttributes`, original choice/revision visible to staff | [ ] |
| Customer notification preview, then separately authorised test email | Public proof visible once; no proof for None; historical snapshot, not current metafield | [ ] |
| Restart two/four workers at retained operation boundary | Same identity/receipt, no duplicate writes; statuses survive | [ ] |

No real storefront proof, checkout, order, email or deployment success is claimed
until those separately authorised live/staging checks are recorded.

Rollback: pause publishing, retain operation evidence and restore the prior image
with its compatible appdata backup if required. The extension version is rolled
back separately; Docker rollback does not reverse Shopify metafield writes. Revert
proof policies through normal reviewed sync before serving an extension that cannot
render them. Do not delete identities, orders or receipts.

References: [product form properties](https://shopify.dev/docs/storefronts/themes/architecture/templates/product/overview),
[cart properties](https://shopify.dev/docs/api/ajax/reference/cart),
[notification variables](https://help.shopify.com/en/manual/fulfillment/setup/notifications/email-variables),
[theme-extension limits](https://shopify.dev/docs/apps/build/online-store/theme-app-extensions/configuration).
