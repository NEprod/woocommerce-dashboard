# Packaging & Shipping — M3A.9.5.4

## Honest release boundary

Local Packaging Management, product/shared/variant authoring, inline package creation,
solo recommendations, actual mixed-item/multi-parcel planning, simulator, versioned
merchant rate tables and read-only Orders suggestions are implemented. **Checkout
pricing is not fixed or independently verified by this release.** No public callback,
shipping profile/default change, carrier registration, permission change, label purchase
or fulfilment is enabled. Saving JSON never authorises a Shopify write.

## Confirmed store audit (16 October 2026)

Authenticated read-only Dev Dashboard: existing TLC installation, **Basic**. Shopify
Admin: one General profile, all products, one fulfilment location, one UK zone;
Royal Mail **Calculated**, Tracked 24 and Tracked 48; Carrier accounts: None.
All seven requested packages already exist. Default: TLC 6x6 Card Mailer (Envelope).
No settings were saved and no live product/order/checkout mutations were performed.
Admin visibility is not public API identity verification: no package GIDs were returned
by the visible list. Local records remain unlinked, never linked by title alone.

Official sources checked:

- [Packages and native packed product size](https://help.shopify.com/en/manual/fulfillment/setup/packaging/setting-up-packages).
- [CarrierService eligibility, protocol and timeouts](https://shopify.dev/docs/api/admin-graphql/2026-10/queries/carrierService).
- [Third-party carrier-calculated shipping](https://help.shopify.com/en/manual/fulfillment/setup/shipping-rates/third-party-carrier-calculated-shipping).
- [Package update](https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/shippingPackageUpdate).
- [Inventory measurement input](https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/InventoryItemMeasurementInput).
- [Royal Mail through Shopify](https://help.shopify.com/en/manual/fulfillment/fulfilling-orders/shipping-labels/shipping-carriers/royal-mail).

### Exact remote checkout blockers

There is no supported per-checkout API here for injecting TLC's parcel plan into the
existing Shopify Royal Mail service. Solo variant package assignments apply to
single-item orders only. Shopify's own packed-size algorithm can select a saved **Box**
for a shipment, but excludes envelopes/soft/carrier packages. It falls back to the
default when the default is not a Box, any item lacks packed size, no Box fits, or
platform item/package-count limits are exceeded.

TLC currently has an Envelope default and no Box longer than 35 cm; a flat 42 cm A3
cannot fit those Boxes. C3 is an Envelope, excluded from native box selection. These
are documented unsafe fallback conditions, not proof of a historical checkout's
internal computation. Native selection's applicability to this exact Royal Mail rate
path still requires controlled checkout acceptance; no multi-parcel/TLC-rule guarantee.

A separately approved native trial requires a **real measured A3-capable Box**, a
deliberately approved Box default and accurate packed size/weight for every physical
item. Never mislabel a physical envelope as a Box or assume prints can roll. Test the
actual two-card+A5+A3 checkout against independent parcel/charge evidence, including
missing-size/no-fit fallbacks. No default change or automatic activation occurs here.

Using **TLC's actual complete packing algorithm** at checkout requires a separate
CarrierService. Basic is ineligible. Documented options: Advanced/Plus, or Grow with
annual billing / paid CCS enablement (confirm exact UK terms with Shopify; no unverified
price quoted). Existing app scopes lack read_shipping/write_shipping. No gateway,
authorised external Royal Mail quote source or approved fallback is configured.
Functions are not a parcel quote API; custom-app Functions on Basic are not a workaround.

Manual reviewed profiles/real weight bands/conservative flat rates may reduce interim
risk but cannot prove basket geometry. Different profiles can combine charges. Do not
falsify product weights, silently remove Tracked24/48, or invent a safe flat-rate price.
Current checkout remains unchanged pending merchant approval of a specific proposal.

### Package API boundary

2026-10 documents package update/default/delete mutations and a write-only inventory
measurement shippingPackageId. A tested public list/read/create/assignment-readback
contract is absent. The in-app capability refresh introspects the actual configured
store/schema through the existing bounded transport, recording store/plan/scopes and
field availability. No generic query/GID is guessed. Failure retains prior evidence.
The development machine has no configured merchant Admin API credentials for a live
schema probe; this must be run in the configured installation. No authenticated
package API readback is claimed from documentation or Admin observation alone.

Mutation availability is not safe verified sync. Package creation/linking/update/default
and inventory-package assignment are explicitly **blocked**, not falsely reported
successful. Copyable external measurements and exact Admin setup instructions are
provided. Do not recreate the seven already present packages. New packages can be
created locally and manually in Admin → Settings → Shipping and delivery → Packages;
independent public ID/readback support is needed before enabling remote links/writes.
No remote deletion is offered. No additional permission version is staged because
shipping permission approval alone would not resolve the blockers. Existing app,
12 declared scopes, Orders access and customisation UID/assets remain unchanged.

## Initial catalogue and local management

Migration **0017_packaging**, after 0016_shopify_orders, inserts seven deterministic
UUIDv5 identities once. Restart/scan/upgrade never resets them or duplicates edits.

| Package | Type | External size cm | Provisional tare |
|---|---|---|---|
| TLC 6x6 Card Mailer | Envelope | 18 × 18 × unknown | 40 g |
| TLC C3 Reinforced Mailer | Envelope | 45.7 × 32.4 × unknown | 40 g |
| TLC C4 Rigid Mailer | Envelope | 32.4 × 22.9 × unknown | 40 g |
| TLC C5 Large Letter Box | Box | 23 × 16 × 2 | 40 g |
| TLC Large Postal Box | Box | 35 × 25 × 10 | 200 g |
| TLC Medium Postal Box | Box | 25 × 18 × 8 | 150 g |
| TLC Small Postal Box | Box | 18 × 13 × 6 | 100 g |

All start Measurements needed, automatic=false, unlinked. Unknown is not zero.
Measure usable interiors, filled envelope thickness, exteriors, tare, safe contents/
gross weight limits, clearance and protective material weight. Explicit zero clearance/
protection weight is permitted only when accurate. Confirm dimensions/tare measured,
flat/fragile/material suitability, stackability and automatic eligibility separately.

Packaging supports search/sort, add/edit, duplicate (new UUID/no copied remote link),
on-demand current assignments and recent usage, confirmed archive and copyable native
fields. Version compare-and-swap rejects stale edits. Archive never deletes remote or
historic data; dependent product assignments require attention. Costs are integer GBP
pence; dimensions/weights are decimal strings in cm/g. Soft/rolled packages may be
stored, but deformable geometry/automatic rolling is deliberately manual-only.

## One authored product packaging contract

Intake, existing Product/Shared Editor and variant modifier editor use one reusable
component/modal. Product information remains product_info.json, not package SQLite.
Collection packaging → sparse product packaging → matching variant modifier packaging
merge by property, including dimensions. False remains false; missing inherits;
selected_package_id:null explicitly clears inherited selection. Removing an override
restores inheritance. Variant selection reuses exact/longest modifier matching and
insertion-order ties without changing modifier prices, SKUs or combinations.
New packaging-only modifier rules are excluded from the pricing selector so they
cannot mask an existing partial price/weight rule. Every historical rule, including
explicit empty exact rules, retains its previous semantics and detailed logging.

Illustrative fictional data, not measured TLC values:

```json
{"requires_shipping":true,"packaging":{
  "packed_dimensions_cm":{"length":"42","width":"29.7","height":"0.3"},
  "weight_g":"150","measured":true,"orientation":"flat","classification":"flat",
  "clearance_cm":"0.2","protection_weight_g":"10","fragile":false,
  "may_share":true,"ship_separately":false,"may_stack":true,"stack_limit":10,
  "requirements":["rigid backing"],"selected_package_id":null,"override_reason":""
},"variation_modifiers":{"Size=A3":{"packaging":{"ship_separately":true}}}}
```

Manufacturing/product dimensions remain mm, finished packed item dimensions are cm,
per-face protective clearance is additional, and carrier parcel exteriors are separate.
No artwork-to-packed assumption. Authored weight_g overrides individual shipping weight;
otherwise existing resolved product/variant weight is reused. Package tare is added
per parcel, never folded into a product's weight. Additional protection weights are
included explicitly in planner gross weights. Requires shipping uses existing defaults.

Use recommendation/override explicitly; ordinary edits never overwrite a manual
choice. A selected solo package is a conservative packing constraint, not proof of
remote assignment or combined fit. The one inline modal preserves unsaved product
values and position; Cancel changes nothing. Saving a package selects a locally valid
definition, not an automatic-ready guarantee; it neither saves nor scans the product.
Import review uses the existing Intake metadata editor, not another creator.

### Existing Shopify publisher

SHOPIFY_NATIVE_DIMENSIONS_ENABLED=true retains the existing signed/CSRF/identity/
durable evidence/readback/uncertainty workflow on **2027-01 only**. Explicit measured
packed_dimensions_cm takes priority, independently per variant. Incomplete/unmeasured
explicit size blocks fallback; absence retains legacy product-dimension reuse only
with its existing explicit opt-in flag. Other operations stay **2026-10**. Managed
shipping-weight/packed-size fingerprints change when their effective values change;
private packaging notes do not invent Shopify metadata. Save Locally never publishes.
Native matching evidence is not checkout or package-preset verification.

## Planner, simulator and uncertainty

Pure Decimal largest-face-first 3-D guillotine packing creates disjoint free cuboids.
Right/front splits preserve free floor; top splits are limited to the fully supported
item footprint. Orientation, full-support stack limits (including supporting items),
clearance, flat/fragile/material approval, sharing/separate rules, quantity and gross/
contents weight limits must pass. No volume-only fit, bending, diagonal/soft packing,
rolling or unsupported bridging. A feasible arrangement may be rejected conservatively.

Three deterministic package-choice heuristics produce feasible complete plans, ranked
by verified safety, available complete service price, parcel count, material cost and
stable tie-break. No global optimum claim. Up to two candidate alternatives are retained
in evidence, not guaranteed distinct. Bounds: 200 physical units, 64 package candidates,
60,000 orientation checks. Limit/missing-data/no-fit returns no invented postage.

Unknown internal size/thickness cannot be rescued by estimate mode. Complete estimated
dimensions can be explored explicitly but **never quoted for automatic rating**.
Missing dimensions/weight/protection/orientation/shipping intent gives actionable errors.
Explicit nonphysical items are excluded. The default mailer is never a fallback.

Simulator uses local product/variant search and quantities, not current Shopify reads.
It shows parcels, contents, weights, dimensions, rates, rejected packages and warnings.
Synthetic two-card+A5+A3 fixtures consolidate four units into a measured 45 × 32 × 2 cm
usable package, 390 g gross using fictional weights. A3 plus separately shipped fragile
ornament splits into two parcels; every parcel is charged. Production seed interiors
remain unmeasured and cannot claim those results. No real checkout test was performed.

## Pricing sources and Royal Mail

Shopify Shipping prices use Shopify's Royal Mail account; no authorised external
discounted-price API was established. No negotiated/public prices are seeded. Enter
actual agreed all-in customer charges with a source/contract label, UTC effective/
expiry dates, explicit Tracked24/48 dimensional/weight bands, handling and optional
packaging charge. Revisions are immutable UUIDs. VAT/surcharges/transit guarantees
are not invented. Every parcel needs a valid band; charges sum across the entire plan.
Missing/expired rates or missing included material costs block a complete quote.

Conservative format hints: large letter 35.3×25×2.5 cm/750 g; small parcel
45×35×16 cm/2 kg; medium parcel 61×46×46 cm/20 kg. Approved service-band limits also
apply; hints aren't carrier-contract eligibility. Verify current service rules, restricted
goods, Northern Ireland/Windsor constraints and destination coverage. This release has
UK/GBP simulator context only, no regional or international live quote. Label quotes
as merchant tables, not live Royal Mail/Shopify Shipping.

## Future public gateway — approval required

Do not expose Unraid/dashboard/SQLite/catalogue files. A separate isolated HTTPS gateway
should receive versioned allowlisted variant identities, measurements/rules/packages/
rates, not customer answers or order-writing credentials. Add strict body/item limits,
secrets, callback authentication/request controls, redacted logs, rate limits, monitoring
and internal deadlines. Do not assume CarrierService uses webhook HMAC or Tailscale
is public. Shopify documents dynamic 10/5/3-second read deadlines by request volume,
no automatic callback retries and caching. Avoid per-callback Shopify queries. Quote
every parcel under consistent GBP minor-unit service codes; fail without an invented
rate. No availability guarantee for a home-hosted gateway.

Activation needs CCS eligibility, approved exact scope diff staged on the existing app,
verified rate source, gateway/failure testing, General profile/zone/duplicate-service review,
and actual checkout acceptance. A new carrier must not be assumed automatically attached
to the profile in 2026-10. Backup rates appear only when calculated rates fail with no
other eligible option; always-eligible flat rates may still be selected. Agree a safe
fallback and preserve existing Tracked24/48 rollback before any activation.

## Orders, security, persistence and rollback

Orders suggestions map exact store/product/variant GIDs and preserve historical order/
line GIDs and outstanding quantities, not SKU/name. Same-SKU lines stay separate.
Current measurements are suggestions, not historical measurement assertions. Missing/
deleted/uncertain mappings block complete plans. No labels/fulfilment/order mutations.

Package/rate/plan/evidence tables use existing persistent SQLite appdata/mounts. Plans
snapshot package versions, geometry, item identities/placements, quantities, weights,
rates/source/version and warnings. Later package edits/archive never change a saved plan.
No addresses/emails/customer answers enter packing records. All routes require existing
admin/session protection, POST CSRF and private/no-store responses. Request bodies are
bounded to 256 KiB before CSRF parsing. No new Discord event, scanner or worker.

Back up site.db with existing SQLite-aware backup tooling plus catalogue before upgrade.
The migration runner backs up the old database. No destructive downgrade: restore the
pre-0017 backup with the previous image for rollback; do not delete operational history.
No XML/mount/environment replacement is needed. Local original images, JSON, SKU indexes,
.scanned/.update, product/variant/InventoryItem IDs, sales and Orders remain unchanged.

## Controlled acceptance

1. Deploy tested immutable image manually after backup, preserving mounts. Confirm 0017
   and seven unverified seeds, unchanged after restart. Do not recreate remote packages.
2. Measure packages, protection and limits; confirm readiness locally.
3. Test inline add/cancel in Intake and Product/variant editor with unsaved fictional
   values; confirm no JSON save/scan/Shopify publish occurs until separately requested.
4. Author accurate packed values in the proper scope; save locally → targeted scan.
   Review only intended Shopify native/weight changes via existing signed workflow.
5. Simulate card/A3/mixed basket/fragile split; verify fit and every weight/rate. Unknown
   data must block, not fall back. Enter real rate tables only with verified sources.
6. Confirm Orders retains separate line identities, restart persistence and offline views.
7. Refresh capabilities explicitly in configured installation. Missing shipping scopes or
   absent readback never becomes a successful package sync.
8. Stop for merchant approval of a specific native Box trial or custom carrier rollout.
   Record existing profile/default/Tracked24/48 and exact rollback first.
9. Final gate: actual two-card+A5+A3 Shopify checkout, controlled UK destination, independent
   package/charge evidence; then missing sizes/no-fit/multi-parcel/failure cases. A displayed
   rate alone does not prove geometry. No checkout fixed/live claim before this passes.

## Verification evidence

Focused synthetic packaging tests cover seeds/readiness/CRUD, geometry/stacking,
A3/mixed/multiple parcels, weight/protection, rates/expiry, inheritance and both native
measurement paths, request protection and historical order-line identity. A bounded
existing Python regression covers publishing, native dimensions, both Variable
layouts, Orders, editors/Intake, scanner contracts, sales/startup, migrations and Discord.
Two old Orders migration assertions were aligned with 0017 without relaxing rollback
protection. JavaScript tests cover sparse controls, preserved IDs/false values, modal
boundaries and existing editor/sale/order/designer behaviour.

Native Chrome desktop/mobile (390px) fictional browser checks confirm local package
save/cancel preserves unsaved product title, package selection returns to the editor,
modal actions remain visible and the complete mixed basket yields one feasible parcel,
390 g, with rates unavailable rather than fabricated. No horizontal overflow was seen.
These are not Safari or live checkout tests. The offline Docker script extends the
existing two-worker Orders smoke with 0017, package/plan persistence and local pages;
no network is available to its disposable containers. Release results are recorded in
the final delivery report, not inferred from source/test existence.
