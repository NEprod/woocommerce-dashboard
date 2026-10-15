# Shopify integration — Phase 4 M1 / M2 / M3A

## M3A.9.2 — local core editor

Normal shared/override editing covers supported merchandising/shipping/SEO/taxonomy,
variant modifiers and image axes. Admin/CSRF and signed source/dependency patches
protect local saving; scanner completion remains separate from Shopify publication.
Existing fingerprints invalidate effective managed differences, not private notes or
the entire catalogue. Single Variable no longer reads its shared root as an override,
aligning titles with scanner output; retained remote titles may need reviewed Update,
never recreation. No new scopes/migration/auth configuration. Normal2026-10 and opt-in
packed2027-01 remain unchanged. Scheduled writes, customer forms/uploads, paid charges,
creator and media replacements stay deferred. See PRODUCT_EDITOR_CONTRACT.

## M3A.9.1 — editor foundations (no new remote functionality)

The [editor contract](PRODUCT_EDITOR_CONTRACT.md) audits authored fields, reusable
controls and all three layouts. Pure edit preparation and scoped scheduler cursor
isolation need no new scopes, credentials, migration or API version. Theme forms,
customisation definition publishing/order capture and date-aware sales remain
proposals; current scheduled-sale blockers remain. No unattended writes or source
JSON changes are introduced.

## M3A.8.6 — browser-independent product reconciliation

Settings → Scheduled Tasks exposes Shopify Product Reconciliation, enabled by default
every 20 minutes; choose 10, 20, 30 or 60 minutes without restarting. Configuration,
due times, ownership, counters and history persist in SQLite revision
`0013_scheduled_tasks`. No catalogue, identity, credential or authored JSON backfill.

The existing canonical status/Preview comparison is reused for Simple Product,
Variable Collection and Single Variable Product. Required variant pagination,
prices/modifiers, inventory policy/identities, native packed dimensions, media,
taxonomy and attribute capabilities retain their existing readers and safeguards.
The background transport refuses mutations; it never recreates products/variants,
uploads media, repairs definitions or replays uncertain writes.

Only complete, fingerprint/config/identity/mapping-valid successful evidence under
ten minutes old can be skipped. Exactly ten minutes, missing/invalidated/incomplete
evidence and failed checks are eligible; failed attempt backoff does not masquerade
as successful verification. Relevant domain timestamps must all be recent. A reviewed
publish records independent verification immediately and is skipped while recent.
Eligibility uses the run-start cutoff across continuations; newly completed publishing
proof also supersedes previously queued reads. A later slice does not age a skipped
product into the same run merely because processing took time.
Processing can be finalized through existing read-only retained-plan readback; uncertain
operations and mappings still require explicit recovery. Original asynchronous publishing
completion may settle its existing durable publishing notification—not a routine task alert.

Products HTML and browser navigation render saved evidence without automatic Shopify
requests. Manual per-product/visible refresh, identity reconciliation and Review remain.
Complete historical results remain visible as Last check matched / Update Available
(last check) when only their freshness expires. The time shown is the last successful
full comparison, not a failed attempt. Source/identity/mapping invalidation still requires
a new comparison; incomplete or failed reads never borrow that historical equality.
Run Now durably queues one manual run (all active parents by default; the endpoint also
accepts one to 25 explicit product IDs). It bypasses freshness, not recovery, owner or
rate limits. Repeated clicks return the existing run. It does not shift the periodic due
time. Only explicit Run Now gets a finite UI progress window; no always-on browser polling.

Each slice selects at most 25 parents, with up to eight canonical comparisons, at most
120 GraphQL attempts and a 120-second transport budget. The browser's two-comparison
limit remains for manual status requests, not the worker. There are at most 1,000 local
parents per sweep, 60 slices and 20 minutes per run; partial work is recorded accurately.
Continuation uses the same persisted run/queue with a 15-second pause. Excess work
rotates to later scheduled sweeps; a parent repeatedly exceeding local capacity/budget
is deferred after three attempts rather than starving others. No partial read proves equality.

The shared API coordinator records returned requested cost, available/max capacity,
restore rate and observation time against a hashed configuration identity. Background
requests reserve up to 100 cost units (scaled to the actual bucket/query size) for
interactive work and yield before the next query when capacity is low or an interactive
priority signal exists.
Foreground publishing/recovery demand has higher rank than manual status checks;
normal read signals cannot downgrade an outstanding publishing demand. Both outrank
background work. Existing bounded read throttle retries (at most two; three seconds
total wait) remain; mutations never gain
automatic retries. A currently in-flight read is not cancelled. Interactive publishing
may briefly report the existing Busy response until the background slot is released.
Missing cost observations still use strict request/time budgets, not unlimited capacity.
Coordination logging/persistence cannot hide an interactive Shopify mutation receipt.

Task history records scheduled/manual trigger, timestamps, duration, considered/skipped/
checked/matching/update/attention/unavailable/deferred counts, pending IDs, throttling and
redacted errors. Partial runs never advance last-successful-run time. Latest 100 completed
runs expire after 90 days; active runs are protected. No Shopify payload/token is copied.
Routine task completion/failure emits no Discord alert. Existing scanner and reviewed
publishing notifications are unchanged. Orders, sale transitions and new alerts are deferred.

Unraid acceptance: back up instance/appdata, pull the scheduler image, keep mounts,
credentials and default image command, then confirm migration 0013. With browser closed,
allow a due run or queue Run Now and close the browser; reopen task history to inspect
its durable result. Test a fresh verified product skip, an exactly-ten-minute eligible
product, both Variable layouts, an authored change and an existing recovery record.
Confirm read-only checks find changes without applying them. Test duplicate Run Now,
interval persistence across restart, preserved publishing progress/Discord outcomes,
and silent Products navigation. Actual live acceptance is not claimed by mocked tests.

## Attribute definition capabilities — 2026-10-14 follow-up

Customer-facing informational attributes use the existing reviewed registration/repair
workflow. Effective registry roles (including inherited legacy navigation/visible defaults)
select policy, not hardcoded attribute names. Production Method is public when the
registry declares it so. Explicit internal vocabulary stays `NONE` at both storefront
access levels; mixed public/internal values sharing a definition remain blocked.

For configured Admin API **2026-10**, the supported fields are:

| Use | Definition readback | Reviewed input |
|---|---|---|
| Admin/API filtering | `MetafieldDefinition.capabilities.adminFilterable { eligible enabled status }` | `capabilities.adminFilterable.enabled: true` |
| Automated collection-condition eligibility | `MetafieldDefinition.capabilities.smartCollectionCondition { eligible enabled }` | `capabilities.smartCollectionCondition.enabled: true` |
| Product metafield Storefront API access | `MetafieldDefinition.access.storefront` | `access.storefront: PUBLIC_READ` |
| Referenced term-definition Storefront API access | `MetaobjectDefinition.access.storefront` | `access.storefront: PUBLIC_READ` |

The capability inputs belong to `MetafieldCapabilityCreateInput` /
`MetafieldCapabilityUpdateInput`, used by `MetafieldDefinitionInput` /
`MetafieldDefinitionUpdateInput`. Do not use deprecated `useAsCollectionCondition`.
See [capability readback](https://shopify.dev/docs/api/admin-graphql/2026-10/objects/MetafieldCapabilities),
[update input](https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/MetafieldDefinitionUpdateInput)
and [Shopify's capability guide](https://shopify.dev/docs/apps/build/metafields/use-metafield-capabilities).

Schema support and **definition-specific eligibility are separate**. Read-only schema
discovery guards missing inputs/outputs. Existing definitions are checked independently
for both flags and both access settings; only eligible disabled flags become reviewed
changes. Ineligible capabilities or unsupported API fields produce explicit limitations,
not forced mutations. For a new definition, eligibility cannot be inspected before it
exists: the signed review discloses base creation followed by eligibility readback and
conditional enablement of supported flags. Its exact GID and pending update evidence
are committed before the capability mutation. Timeout recovery reads the same identity
and before/desired settings; it never replays or recreates a definition automatically.
If the base-definition creation response is lost, reconciliation may retain the exact
independently verified base identity; unfinished capability settings require a fresh
signed repair. An older signed operation cannot manufacture a new-policy comparison
checkpoint merely by being reconciled after upgrade.

Existing repair patches contain only changed settings. Matching product term references
are not rewritten; definition, entry, product, variant and inventory identities remain.
Access-only historical comparison checkpoints for informational products are invalidated
on upgrade so automatic status checking compares the new policy. Products without such
attributes retain their existing cache policy. No migration; head stays
`0012_shopify_attributes`, with versioned evidence in existing mappings/comparisons.

Flags enabled is not proof that the Admin search index is ready: readback reports
`FILTERABLE`, `IN_PROGRESS`, `FAILED` or `NOT_FILTERABLE`. Non-ready states are disclosed
without repeatedly writing an already-enabled flag. Public read access does **not**
configure Shopify Search & Discovery filters, theme rendering or navigation menus.
Capability mutations create no collections, change no memberships and alter no local
taxonomy. Any separate product/collection actions already in the full signed product
plan remain independently disclosed; do not confirm unreviewed collection actions.

Permissions are unchanged: `read_products` / `write_products`,
`read_metaobject_definitions` / `write_metaobject_definitions`, and
`read_metaobjects` / `write_metaobjects` as needed by existing attribute stages. Capability
patches use `write_products`; access to the term definition uses
`write_metaobject_definitions`. No new authentication, API version or Unraid setting.

Unraid acceptance: pull the immutable capability image or `develop`, keep existing
mounts and credentials, and Refresh Product for an existing informational product.
Review disabled flags and access settings separately. Confirm only the intended signed
changes; check retained IDs/references and unchanged collections (including 16-Bit).
Refresh/build Preview again: matching settings should show No Changes. Inspect Shopify
Admin read-only definitions for flags/access and indexing status; readonly app ownership
is expected. Search & Discovery/filter activation and menu publishing remain separate.

## M3A.8.5.3 — ten-minute status and publishing notifications

Products renders local evidence immediately, then its existing administrator-only
status endpoint compares stale/missing/invalidated parents in bounded steps (25 IDs,
two canonical comparisons per request). No server scheduler, render-time Shopify
calls or continuous expiry polling. A successful **complete** full comparison lasts
ten minutes; product revisions alone cannot renew inventory/media/attribute proof.
Explicit Refresh bypasses freshness, not recovery controls. Failed/throttled reads
retain last successful timestamps and back off. Missing required attribute/native
evidence cannot renew successful full/equality freshness or show Up to Date.

Pending local/stat/schema evidence changes show **Checking Changes**. Only canonical
remote differences show **Update Available**. Stat-only changes automatically resolve
when the same original image bytes and managed fields match. Variable status hashes
exclude unrelated production/customisation notes while retaining calculation inputs,
stable child identities, prices, weights, images and policies. Signed review continues
to bind full source and original image bytes. A no-change Preview and automatic status
use the same projection/checkpoint. Draft status is not Online Store publication;
unresolved publication or other required stages prevent unqualified Up to Date.

Ordinary Products/search/filter/reload, readonly status/Preview/reconciliation,
operation polling and modal interactions send no Discord messages. Existing scanner
start/completion/warnings remain; per-product ingestion discovery is sent only when
the local catalogue record is genuinely new, not for existing-record upserts.

Reviewed Create/Update terminal outcomes enqueue a bounded, versioned event in the
existing `CatalogueOperation.scope`: verified success, definitive failure before
product mutation, partial completion, or Recovery Required. Product title/SKU, create
vs update, completion time, independently known status/variant/image counts and safe
Shopify link are included when proved. Retained images are not labelled new uploads.
Verified metadata success does not imply Live channel publication; deferred work is
separate. Original partial/recovery evidence is retained when later full readback
settles a completed asynchronous publishing operation. Reconciliation of an already
completed operation sends nothing. Pre-milestone operations are not backfilled.

Events are committed with terminal results. SQLite compare-and-swap claims prevent
competing workers from sending the same logical outcome. Existing Discord transport
and channels are reused: `DISCORD_WEBHOOK_SCANS_INFO` for verified success,
`DISCORD_WEBHOOK_SCANS_ERRORS` for attention; `DISCORD_ENABLED=true` enables delivery.
No new webhook or Shopify scope is required. Delivery occurs after database commit,
with one bounded attempt; missing/disabled webhook or delivery failure cannot fail
Shopify publishing. Publishing requests use Discord's supported
[`wait=true` confirmation](https://docs.discord.com/developers/resources/webhook#execute-webhook)
and suppress mentions; the scanner transport keeps its established behaviour.
Operations → original publishing operation shows retained state
and an admin/CSRF-protected **Retry Discord delivery** action. A timeout/service error
or sending claim abandoned for two minutes is uncertain: explicitly acknowledge
possible duplicate external delivery before retrying. There is no automatic retry
of ambiguous sends and no exactly-once external-delivery guarantee. Unsent/failed
events are protected from history pruning. The same envelope supports up to 25 future
product results, without implementing bulk publishing.

No migration; database head remains `0012_shopify_attributes`. No authentication,
API-version or volume-path change: normal Shopify operations use configured 2026-10,
native dimensions remain opt-in 2027-01. No live Shopify/Discord requests are performed
by tests. Live acceptance: visit Products twice within ten minutes (no repeat complete
read/no Discord); revisit after ten minutes (bounded readonly refresh, no Discord);
touch/edit an affected source and distinguish Checking Changes from actual Update;
review and explicitly confirm one known Simple or Variable update, check verified
status and one appropriate publishing notice; repeat readonly checks/receipt polling
and confirm no duplicate. Test disabled Discord independently of publication success,
then use only the explicit Operations retry after restoring configured delivery.

## Customer-facing registry mapping and existing-resource corrections — 2026-10-14

Registry categories/subcategories map to Shopify collections; local hierarchy/order remains
authoritative. Registry storefront_collections map to their dedicated Shopify collections.
Informational attributes are public filter definitions with referenced controlled terms;
variant-driving attributes remain options. Only explicit destination/both roles create term
browsing collections. A filter value does not imply a collection or menu entry.

The supplied TLC registry has seven navigation=true attributes, with no browse_role. The
previous internal fallback ignored that existing declaration. Missing roles now resolve to
filter if navigation or visible_default is true; otherwise internal. Explicit roles always
win. This includes Production Method under the same policy, without a name exception.
Terms inherit the parent unless explicitly overridden. The editor shows effective public
intent and provides an Inherit attribute role choice; new guided attributes start as filters.
No author JSON is rewritten at startup, scan or Preview. Explicitly internal existing fields
remain private: change their role only through the existing reviewed registry editor.

Storefront access belongs to the shared metaobject definition. Mixed internal/public term
roles under one definition cannot be secured by choosing a different product assignment:
publishing blocks with an explanation. Align the vocabulary's roles or keep private data in
a separate explicitly internal attribute. No automatic exposure/splitting/recreation occurs.

For already-created resources, Refresh/Compare reads retained exact IDs, definition access,
terms and actual product references. NONE→PUBLIC_READ corrections appear as separate readable
changes and persist Update Available. Matching references are shown as matching and access-
only repairs do not reassign them. Confirmation preserves parent/variant/media/term GIDs and
uses existing signed/admin/CSRF, durable reservations, uncertainty and independent readback.
Missing write_metaobject_definitions blocks an affected definition repair, not useful read-
only comparison; product-field access needs write_products. No new scopes are introduced.

Term readback must also match the retained definition GID and the access snapshot read
for that definition. A public entry with enabled publishable capability and DRAFT status
blocks storefront equality/Confirm; it is not automatically activated or recreated. This
condition needs explicit entry-publication resolution rather than a misleading Up to Date.

An unchanged product updatedAt is not proof that shared definition access or references still
match. Attributes are rechecked through the existing bounded canonical path when ten-minute
related evidence expires; fresh results are reused. Each status request still handles at most
two full comparisons and 25 parents. Policy/registry/source changes invalidate cached evidence;
GET rendering makes no remote requests. Comparison timestamps are not post-write verification.

Admin read-only app ownership remains intentional and separate from storefront access.
Both definition layers need PUBLIC_READ and referenced entries must satisfy publishable
status where enabled. [Shopify's app-owned access rules](https://shopify.dev/docs/apps/build/metafields/definitions)
and [metaobject filtering requirements](https://help.shopify.com/en/manual/online-store/storefront-search/search-and-discovery-filters)
do not make definitions merchant-owned merely to expose them publicly.

Acceptance: back up appdata and deploy the tested image manually. Open an existing product
using Recipient (then Material/Occasion/Personalisation/Production Method), Refresh/Compare,
and inspect persisted Update Available and the exact access/reference changes. Review and
confirm only intended repairs; check retained IDs, no duplicate creation/uploads and independent
readback. For explicitly internal registry entries, first review the intended public role
locally; never expose internal operational data indiscriminately. A Draft product stays Draft.
Use the existing read-only attribute diagnostic for actual definition/term/value evidence.

Then check Apps → Search & Discovery → Filters for the relevant product-metafield source,
select the term name as label where required, and test a supported theme against genuinely
Online Store-published products. Public data alone neither activates filters nor creates
menus. Filter/theme configuration and full menu publication remain separate; live storefront
success has not been claimed from mocked tests. No live store API access was used in development.

## M3A.8.5.2 — reliable confirmation, Variable comparison and attribute validation

Confirmed root causes (local reproduction, not inferred store failures): a hidden control
named `action` overrides `HTMLFormElement.action`, misdirecting the enhanced confirmation;
the status route excluded Variable parents despite a capable reconciliation service.
Confirmation uses the content attribute and the status query now includes both types.
The form behavior is specified by the [HTML named getter contract](https://html.spec.whatwg.org/multipage/forms.html#dom-form-action).
No live Shopify credentials were available in development, so remote incidents remain
unverified rather than described as permanently fixed.

Admin/CSRF/signature acknowledgement first creates a durable accepted operation receipt,
then existing source/remote revalidation and publishing execute under the same lease.
The record is visible from an independent SQLite connection before remote mutations;
pending identities/stages commit before writes. Validation failure does not leave an
indefinite running receipt; database contention is receipt-unavailable, not Shopify failure.
Exact ticket lookup works beyond the latest hundred operations. Restart-interrupted or
uncertain outcomes require read-only reconciliation, never automatic mutation retry.

Progress shows product/SKU, actual stage, elapsed time, last server response and recorded
completed stages. It does not simulate percentages or use parent verification as proof
that all stages finished. Close stops checking without cancelling the writer; Show publishing
progress/reload reconnects only to the saved receipt. Read requests time out after ten
seconds, back off and pause after five failures; checking pauses at ten minutes. A missing
accepted receipt after thirty seconds explicitly asks for History/log inspection. Do not
resubmit because a receipt/HTTP response was lost. Receipt reads last 24 hours; execution
approval is still 15 minutes, and History remains available afterward.

Variable readers request sixteen nodes/page and assemble complete cursor connections under
four-page/sixty-second/thirty-two-variant limits. Revisions/options/media must stay consistent
between pages; stalled cursors, duplicate/missing identities, malformed/incomplete pages and
overflow are unavailable evidence, never equality or proof that a child should be recreated.
Both Variable Collection and Single Variable Product layouts use this reader. Tests exercise
nine real scanner-generated combinations, retained SKUs, modifier prices/weights/dimensions,
InventoryItem identities, tracked/policy defaults, featured image GIDs and native packed
readback, including differences on the last page. Existing quantities, locations, files,
collection mappings and destructive authorisation are untouched.

Normal product/variant/attribute operations stay on `SHOPIFY_API_VERSION=2026-10`. Only
explicit opt-in packed capability/read/write uses 2027-01. Safe fixed-query diagnostics
record version/status/request count/cost/throttle numbers, without tokens, headers, variables
or raw exception payloads. Explicit read throttling can wait/retry twice (three seconds total
wait maximum). No mutation, authentication failure or timeout is automatically retried.
See [Shopify pagination](https://shopify.dev/docs/api/usage/pagination-graphql) and
[GraphQL rate limits](https://shopify.dev/docs/api/usage/limits).

Attribute readback now additionally checks the referenced term's retained definition GID,
PRODUCT owner, exact app-owned namespace, actual product reference list and reviewed access
on both definition layers. Public assigned entries with publishable capability must be ACTIVE;
disabled publishable capability needs no invented activation. Internal registry roles remain
NONE. Production Method has no name exception: explicitly configured public roles are
honoured like other attributes. No duplicate merchant definitions are created to change
Admin editability. Read-only definition listing/pinning is not proof of assignments or filter
activation. These checks use Shopify's [Metaobject definition/capability contract](https://shopify.dev/docs/api/admin-graphql/2026-10/objects/Metaobject).

No new scopes are requested by this correction. Actual token needs `read_products` and
`write_products` for product/variant writes; `write_inventory` for enabled native writes;
`write_files` for reviewed existing-file alt changes. Informational reads need
`read_metaobjects`, `read_metaobject_definitions`; registration/access repair needs the
corresponding `write_metaobjects`, `write_metaobject_definitions` only when those actions
are required. Existing product assignments use `write_products`. The existing configured
navigation-eligibility check needs `read_publications`; this milestone does not write menus
or publications. Missing actual token scopes are explained, not inferred from app settings.

### Controlled Unraid acceptance and read-only evidence

1. Back up appdata and note the prior immutable image. Deploy the new incident-fix image
   manually using existing volumes/credentials; no migration/configuration change is needed.
2. Products must render local rows immediately, then compare both Simple and Variable parents.
   Review the affected retained Variable: check all nine SKUs/options, effective modifier
   prices/weights/dimensions, actual InventoryItem GIDs, tracking/policy and featured images.
   An unavailable read must not offer a duplicate Create. Uncertain identities stay blocked.
3. Before another uncertain operation, inspect Identity & History and capture fresh reads
   from the container console (substitute the actual SKU, not another similarly named product):

   ```sh
   python -m app.shopify_attribute_diagnostic --sku HERO-0001 --catalogue
   python -m app.shopify_attribute_diagnostic --sku HERO-0001
   ```

   `--catalogue` reports fresh Product/variants/InventoryItems/media and retained stages,
   comparison summaries/request costs; native read is separate and opt-in only. Default
   mode reports actual informational references, entries and definition access/status.
   Both open SQLite mode=ro, start no migrations/workers and save no evidence/operations.
   They require existing container credentials/scopes and the authoritative mounts for a
   local comparison. Diagnostic operation history is bounded to ten matching records from
   the latest hundred publishing records; normal receipt lookup has no such hundred limit.
4. Only after checking evidence, explicitly build/confirm one fresh supported reviewed update.
   Check its receipt is running before slow calls, close/reopen the dialog, and reload: no
   duplicate writer, parent/child/media creation or upload. Last stage/errors must be visible.
   Lost response/recovery uses read-only reconciliation, not pressing Confirm again.
5. For Recipient/Material/Occasion/Personalisation/Production Method, inspect actual product
   reference lists and entries rather than an empty editable-definition list. Review any
   explicitly desired access repair; verify public versus internal roles without blanket exposure.
   Search & Discovery/filter/theme activation remains deferred.
6. The reported Shopify Admin Metafields/Metaobjects loading issue has no demonstrated cause.
   Reproduce without running a Dashboard write, then capture the affected Admin network
   request URL/path, HTTP status, timing and redacted error/console text. Compare another
   browser/private session and an unaffected Admin page. Never share tokens, cookies,
   Authorization headers or an unredacted HAR. Do not delete definitions/terms as a workaround.

This correction adds no migration, scanner change, source rewrite, variant deletion, bulk
engine or live-store repair. Automated mocked verification is not live acceptance.

## M3A.8.5.1 — live acceptance corrections

The previous 64 KiB SQLite product evidence boundary also served as Preview preflight.
Current/before/previous plans duplicated long descriptions and manifests, so a valid
retained product could be blocked without a Shopify request failure. Signed tokens
already contain hashes, not those payloads. Evidence now keeps the same database bound
and supports lossless zlib-json-v1 envelopes with a SHA-256, exact decoded size and
bounded 1 MiB decompression. Legacy v1 JSON still reads unchanged; no migration or
catalogue rewrite. Descriptions have a 256 KiB UTF-8 transport budget; Simple galleries
share the existing twenty-image Variable budget, with twenty-one-node reads detecting
overflow. Existing 10 MiB/file and 30 MiB/upload budgets remain. Nothing is truncated.
Large comparison values use hashes/size references, never a second copy of HTML;
the full before/after remains reviewable and complete recovery evidence is retained.
Older images cannot read a new compressed evidence envelope: do not roll back blindly
after remote operations. Use this image or reconcile with a compatible version.

Attribute capability checks use actual token scopes and required actions. No assigned
information means no attribute reads/scopes. Assignment-only updates to existing terms
need product write permission, not term/definition creation permissions. Required
attribute reads remain protected. Operators may explicitly select **Inventory-only
correction** on Review for a trusted retained Simple/Variable product. Its signed plan
binds source/remote identities and applies only tracked/inventoryPolicy. Attributes,
metadata, collections, media and publication are deferred. Independent readback yields
scoped_verified/inventory_verified, not full verified/Up to Date, and does not renew the
full verification timestamp. Uncertain outcomes use read-only reconciliation, no replay.
Full Review remains available afterward. Native uncertainty still blocks new writes.

App-owned definitions intentionally remain in Shopify Admin → Custom data → Products
→ More actions → View read only definitions. An empty editable list or “No metafields
pinned” does not prove missing values. Inspect the selected product's metafield section
and the referenced entries (Admin Content → Metaobjects), or use the diagnostic below.
Definition creation, term registration and actual product list references are verified
separately. No duplicate merchant-owned definitions are introduced.

Explicit registry browse_role filter/destination/both makes the attribute public;
internal keeps it restricted. There is no Production Method name exception. The user
now explicitly wants Production Method public too: set its browse_role to filter (or
another intended public role) in the existing registry editor, then review a product
using it. The signed plan can update only access on existing exact owned definitions,
with independent readback and uncertainty recovery; terms/GIDs are retained. Definition
access affects every referencing product and is disclosed before confirmation. Internal
attributes elsewhere remain NONE. PUBLIC_READ on both definition layers is necessary;
it does not configure Search & Discovery or a theme. Unexpected publishable DRAFT term
status is reported by the diagnostic rather than assumed storefront-ready.

Fresh read-only diagnostics from the Unraid container console:

```sh
python -m app.shopify_attribute_diagnostic --sku 16bit-0001
```

Repeat with the actual SKUs carrying Recipient/Material/Occasion/Personalisation/
Production Method. The command uses the existing credentials, fresh shop/correlation
checks and actual product metafields/metaobject GIDs, definition access and publishable
status. It opens the existing SQLite database read-only, starts no migrations/workers,
performs no Shopify mutations/reconciliation and saves no evidence. Review the redacted
output before sharing. Development has no live Shopify credentials; these store findings
remain an acceptance gate, not a claimed verified fix.

Product Detail now exposes **Shopify Actions → Delete from Shopify** directly for
administrators with full verified identity and no native uncertainty. Incomplete or
scoped-only identities explain why Delete is unavailable. Separate typed SKU/signature/
CSRF, exact identity and two-absence-read safeguards are unchanged.

Confirm opens a responsive progress dialog. One synchronous confirmed POST remains the
existing writer; CSRF/admin-protected polling reads persisted operation/identity stages.
It does not fabricate percentages or complete before final proof. Session reconnection
polls the same signed receipt, never submits a new write. Repeat submissions of a retained
receipt return its outcome. Successful verification or known processing returns to
Products, preserving a supplied local Products query/context. Partial/recovery/failed
outcomes remain visible with Identity & Recovery / Back to Products. Progress polling
is bounded and depends on the existing runtime serving concurrent read requests; it
does not introduce a background execution engine. Non-JavaScript confirmation remains.

Legacy tlc_dashboard.product_length/product_width/product_height are **still required**
managed individual product/variant measurements and verification evidence. They are
not native packed dimensions or parcel presets. No destructive cleanup; publish_attempt
remains the independent ownership/correlation marker.

Acceptance: back up appdata; deploy this immutable image; test HERO-0001 Review/Update
without editing legitimate source data; test 16bit-0001 inventory-only versus full
attribute permissions; inspect fresh term/value diagnostics; set the explicitly desired
public registry roles and review access changes; verify retained Variable IDs/prices/
weight/media/native proof; locate Delete on a disposable verified Draft; confirm progress,
reconnect and Products return. No live Shopify operation was performed in development.

## M3A.8.5 — inventory, on-demand organisation and lifecycle

### Reviewed inventory correction

The former publisher omitted tracked/inventoryPolicy from both the default-variant
update and additional-variant bulk create, and therefore did not compare them. Shopify
documents [bulk variant inventoryPolicy](https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/ProductVariantsBulkInput)
as DENY by default. This proves a path-dependent omission, not the actual tracked
values of the reported live nine-variant product (not queried from development).
Review now reads both fields, showing the actual before/after per SKU. No explicit
finished-stock policy defaults to tracked:false / CONTINUE. Authored manage_stock:true
retains tracking; backorders yes/notify uses CONTINUE, otherwise DENY. stock_quantity
without explicit policy requires resolution, not inference. Quantities, locations,
fulfilment configuration, SKU/InventoryItem IDs, prices/weight/packed values do not change
as a side effect. Explicit quantity handling remains deferred until a location policy.

### Categories, controlled information and scopes

First confirmed product use can register missing active TLC categories, required
ancestors and explicitly assigned storefront collections, then add/verify memberships.
Preview reads candidates only; similarly named/handled collections require explicit
verified Link. Pending SQLite mapping precedes Create; no unused registry upload,
filesystem-folder collection, automatic rename/removal or duplicate registration occurs.
Partial successes remain retained; existing Taxonomy reconciliation resolves uncertain
collection outcomes before any further approved stage.

Effective informational assignments use controlled app-owned metaobject definitions/
terms and list.metaobject_reference product metafields under `$app:tlc_attributes`.
Type/handle/field identity derives from stable TLC keys, not display names. Only used
terms are registered. Multiple values are retained; explicit informational [] clears
only that owned reference list. Missing source instructions preserve remote values.
Variant-driving attributes remain existing options/SKUs, never extra information variants.
Eight informational attributes/forty terms and existing evidence limits bound each review.
Owned-reference writes use compareDigest CAS plus independent typed readback. Missing
scope/schema, untrusted mapping or conflicting values block the affected reviewed workflow.

Additional attribute scopes:

- read_metaobject_definitions
- write_metaobject_definitions
- read_metaobjects
- write_metaobjects

Product/collection/metafield work retains read_products/write_products; gallery alt
changes retain write_files. Native measurement changes alone need write_inventory.
Navigation eligibility optionally needs read_publications, never write_publications or
write_online_store_navigation. Do not silently change the Shopify app's scopes.
See [definition Create](https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/metaobjectDefinitionCreate)
and [product metafield definitions](https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/metafieldDefinitionCreate).

Resource mappings persist in the additive `0012_shopify_attributes` table; no backfill
or JSON/registry duplication. Unique store/source and store/remote constraints retain
definition/field/term GIDs with bounded versioned evidence and operation references.
Changed registered schema/access is blocked for separate reviewed mapping work rather
than silently replaced. Read-only inspection/reconciliation is in Taxonomy → Attributes.
Uncertain creation cannot replay automatically. Two successful exact app-owned unique
resource null reads, with no previously known remote GID, establish noncreation; any
subsequent attempt needs NEW signed product approval. Known incomplete stages can
continue through fresh signed Update only after independent base/media/membership proof;
base-only proof is not complete publishing verification. Original partial history remains.

### Navigation foundations

Existing registry entries can explicitly set browse_role internal/filter/destination/both.
Optional attribute browse_group and acyclic within-attribute term browse_parent provide
paths such as Occasions > Seasonal > Christmas without another taxonomy. No name-based
visibility inference or global tag registration. Existing registries safely default
internal, regardless of legacy navigation/visible_default flags. Set desired public
roles before first registration; changing existing schema/access needs separate review.
Only destination/both terms propose dedicated browsing collections on reviewed use.
Optional authored primary_category is a directly assigned TLC category key for proposed
breadcrumbs; it replaces no other category and renders no theme breadcrumb yet.

Taxonomy → Navigation Preview performs no API requests on GET. It shows local matching
SKUs, saved destination/identity mappings, independently checked Online Store eligibility
and unresolved publication. Empty/unknown destinations are hidden from the live proposal,
not deleted locally. The 500-product bound is disclosed; category ancestors aggregate
descendants. Baseline menu intentions are retained, while page destinations, New In
periods, Best Sellers rules and filter/theme/menu publication remain unresolved/deferred.

For explicit read-only eligibility checks, configure the exact verified Online Store ID:

```text
SHOPIFY_API_VERSION=2026-10
SHOPIFY_ONLINE_STORE_PUBLICATION_ID=gid://shopify/Publication/<verified-id>
```

The ID must resolve to the Online Store publication/channel in the verified store;
it is not chosen by a guessed product/category ID. Add the optional environment field
manually in Unraid, preserving existing credentials/mounts; the unrelated XML files
are not modified by this milestone. Checks process 25 retained products per request
and count only fresh ACTIVE + exact publishedOnPublication evidence. Draft, local live
and ACTIVE alone never establish visibility. Search & Discovery filter activation and
storefront rendering require separate live verification; this milestone writes no menu.

### Delete, absence and recreation

Product Detail → Remote lifecycle → Review Delete from Shopify opens a separate
destructive workflow. GET is local-only; Review validates fresh exact store/Product/
variant/InventoryItem/correlation identities, title/status/count, expiry and signed
evidence. Confirm requires CSRF/admin and typed exact SKU; ordinary sync approval can
never authorise deletion. Shopify requires write_products and product-delete permission.
[productDelete](https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/productDelete)
is irreversible and removes associated remote variants/inventory/publications and
unshared media. No TLC source, SKU, marker, image, assignment or local product is deleted.

Timeout/incomplete outcomes become deletion uncertainty, never automatic retry. Identity
& History → read-only reconciliation proves survival or absence under the verified
store. Two successful exact null reads are required; API/auth/permission errors and
revision hints are not absence. Archived/unpublished products still exist and retain
IDs. Bounded normal refresh applies the same proof to manual Admin deletion, without
automatically recreating anything.

Confirmed absence archives complete old evidence in protected CatalogueOperation
history before active product/variant/inventory/media bindings are cleared. Needs Creating
then offers fresh duplicate/SKU/handle checks and signed Create with new remote GIDs.
The parent local reservation and existing local child SKUs remain unchanged. Archive
links survive later recreation. No bulk Delete or destructive rollback is implemented.

### Unraid acceptance

1. Back up persistent database/appdata before updating to develop or its unique
   shopify-taxonomy-lifecycle rollback image. Keep existing paths/mounts; verify
   migration head 0012_shopify_attributes. Do not restore a pre-upgrade database after
   remote writes without reconciling newer remote identities.
2. Test connection and required scopes. Normal API stays 2026-10; existing opt-in native
   packed settings alone use 2027-01 RC. No new authentication method is introduced.
3. Review the existing nine-variant parent: inspect actual tracked/policy differences,
   confirm only approved corrections, verify all nine policies and unchanged SKUs/
   InventoryItem IDs/prices/measurements. Recheck No Change; no quantity is invented.
4. Review a product needing an unused category/ancestor and explicit storefront group.
   Verify Create & Join proposals, candidate blocking, preserved existing mappings and
   independent memberships after one confirmation; unused definitions remain local.
5. Configure desired informational browse roles in the existing reviewed registry editor.
   Review the Christmas pet decoration's effective informational assignments and actual
   driver list. Confirm only used terms/owned references; verify no extra variants.
6. Inspect Navigation Preview: correct local paths/usage, Draft not visible, empty local
   entries retained. If configuring publication GID/read_publications, explicitly check
   actual Online Store eligibility. No menu or filter activation should occur.
7. Choose a specifically disposable verified Draft. Inspect exact Delete review, type
   its SKU and confirm once. Verify Shopify absence, local files/SKUs untouched, archived
   old IDs and Needs Creating. Then perform fresh reviewed recreation and inspect NEW
   product/variant/InventoryItem/media IDs with the SAME local SKUs.
8. Separately delete another disposable test Draft in Admin; refresh Dashboard and check
   independent absence detection. Archive/unpublish instead of Delete must retain the
   binding. API failures must remain unavailable/recovery, never Needs Creating.
9. On any partial/uncertain result stop writes, inspect retained stages and use explicit
   read-only reconciliation before another review. Do not induce live timeouts simply
   to manufacture a recovery case. Report differences before widening acceptance.

## M3A.8.4 — reviewed variable parents and compact Products

### Existing catalogue boundaries and supported fields

Use normal Products → Review & Sync for an active Variable Collection child-product
parent or Single Variable root-parent. A filesystem collection is not automatically
one Shopify product. Existing child source identities/SKUs, explicit ordered variation
drivers (legacy all-attribute drivers remain supported), combinations and effective
modifier-derived prices/grams/mm are retained. No source folder, image, JSON, marker,
SKU index or inheritance rule is rewritten. Stale modifier ingestion blocks Review
with targeted-rescan guidance rather than recalculating different publish prices.

Supported per owned variant: price, valid unscheduled sale/compare-at, stable SKU,
resolved weight, requiresShipping/origin when authored/configured, explicit validated
HS code when supplied, product-dimension metafields, and an explicit first resolved
variant-owned media association. Product dimensions are not package presets. Parent
metadata, tags, SEO and explicit standard category reuse the existing field contract.
TLC category ancestors/storefront collection mappings are reused for additive independently
verified memberships; filesystem catalogue groups never become Shopify collections.

One parent per confirmation, 1–3 option drivers and **32 total local/remote variants**
maximum. Original PNG/JPEG/WebP gallery sources are deduplicated by portable source
identity, not filename; up to 20 originals, using existing byte/type/path checks and
staged uploads. Existing 64 KiB evidence also bounds complex plans before writes, so
not every combination of these maxima is accepted. Actual MediaImage IDs/CDN URLs are
retained; no extension substitution, conversion, source rewrite or unchanged upload.

### Create, Update, identities and recovery

Preview performs Shopify reads only and displays options/SKUs, effective per-variant
prices/measurements, media, memberships, blockers and separate native stages. The same
admin/CSRF acknowledgement and signed 900-second source/store/config/remote/taxonomy
review protects execution. First Create checks parent/child SKU and handle candidates;
pending SQLite identity is committed before remote creation. ProductCreate is DRAFT,
with reviewed options and correlation. Its initial variant is checked and updated,
not deleted. Remaining variants use
[productVariantsBulkCreate](https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/productVariantsBulkCreate)
with PRESERVE_STANDALONE_VARIANT. Retained updates use
[productVariantsBulkUpdate](https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/productVariantsBulkUpdate)
with allowPartialUpdates false and only reviewed changed fields.

Non-destructive option-value/variant additions are supported without reallocating local
SKUs. Remote-only variants are preserved. Ambiguous/unmapped SKU/option candidates,
changed retained IDs, option restructuring/renames and variant deletion block writes.
Existing gallery alt/order changes reuse retained media IDs; gallery replacement,
addition/removal and existing variant-media replacement/removal are explicitly deferred.
No automatic deletion, rollback or retry is performed. Scheduled sales, scanner modifier
sale_price (not emitted by the established scanner), stock quantities/tracking/locations,
channel publishing, customer personalisation and bulk products remain outside scope.

No migration: existing bounded v1 ShopifyProductIdentity evidence adds `kind: variable`,
source-identity-keyed variant/InventoryItem mappings, option/value GIDs, attempted-create
keys and per-InventoryItem native proofs. CatalogueOperation records durable stages and
partial outcomes. Parent/store SQLite uniqueness remains unchanged. Restarts/rescans
do not permit another Create. Read-only Identity & History → reconciliation verifies
the original reviewed fields, exact IDs/SKUs/options, memberships and media independently.
Known async media/reordering remains Processing and can finish via Refresh without
upload/write replay. Genuine uncertainty remains blocking until proven. A lost early
Create response can leave unfinished stages: inspect/complete only the original reviewed
draft manually in Shopify, then reconcile; the application does not automatically resume
or invent missing variants. Missing gallery receipts use the existing signed explicit
media-adoption review, bound to fresh remote/local evidence and original image bytes.

### API, permissions and status freshness

`SHOPIFY_API_VERSION=2026-10` remains the ordinary product/variant/media/taxonomy API.
Only packed capability, narrow inventory/variant identity reads and packed measurement
writes use 2027-01 RC, sharing existing credentials and bounded no-retry transport.
No new environment variable, database table or credentials mechanism is introduced.
Required normal scopes: read_products, write_products. Reviewed gallery alt edits use
write_files. Existing file permissions/configuration are retained. Packed reads need
the existing supported read_products/read_inventory access; **write_inventory** is
required for dimension changes, not already-matching read-only evidence.

Native dimensions remain default OFF. To test them deliberately retain:

```text
SHOPIFY_API_VERSION=2026-10
SHOPIFY_NATIVE_DIMENSIONS_ENABLED=true
SHOPIFY_PACKED_DIMENSIONS_USE_PRODUCT_DIMENSIONS=true
```

The last flag is an explicit physical-to-packed measurement policy, valid only when
those measurements accurately represent the packed item. Per-variant cm values derive
from that SKU's resolved mm, target its independently verified InventoryItem and receive
separate pending/observed/operation evidence. Known scope/capability/source restrictions
retain successful base product/media work, but display outstanding native work. An
uncertain native write blocks subsequent writes until read-only original-plan proof.
Per-variant native freshness is ten minutes regardless of Product.updatedAt; bounded
status refresh renews native reads when due, never borrows metadata verification time.

Products GET uses local retained evidence only. Existing automatic/explicit status
requests remain serialized and bounded to 25 parents/two full comparisons per step.
Variable source fingerprints include stable children, effective prices/measurements,
drivers/attributes/media and existing taxonomy/configuration invalidation. One semantic
green/amber/blue/red/neutral badge shows sync, not publication. Actual Draft and intended
Draft/Live are separate; Live intent remains visible and blocks unsupported writes.
Technical IDs/timestamps, native evidence and secondary tools are collapsed. Mobile
uses compact cards/full-width Review actions and fixed-navigation clearance.

### Controlled Unraid acceptance

Back up the existing persistent database/appdata; keep catalogue/database mounts,
credentials and persistent paths unchanged. Use the newly published `develop` or its
commit-specific `shopify-variables-<shortcommit>` rollback tag; do not deploy automatically.
There is no new migration (head 0011), and the existing develop Unraid template/settings
remain applicable. Ordinary API stays 2026-10; do not globally select the RC.

1. Test Connection. Inspect granted read_products/write_products, existing file access
   and optional write_inventory. Never place credentials in product metadata.
2. In Products choose one representative Variable Collection child-parent, not the
   entire filesystem collection. Separately choose one Single Variable root-parent.
   Resolve authored Draft intent through the established editor; never silently change it.
3. Build Preview for one parent. Check every existing child SKU/option combination,
   effective modifier price, shipping mm/g/origin, original filenames/media associations
   and mapped category/ancestor/storefront memberships. Reject unexplained differences.
4. Confirm **one** reviewed draft. In Shopify Admin verify one parent, exact child SKUs,
   actual ProductVariant/InventoryItem IDs, effective prices, original gallery/order/alt
   and intended variant association. Processing may need later read-only Refresh.
5. If opting into native measurements, confirm each packed measurement is physically
   accurate, then verify fresh per-InventoryItem 2027-01 values; metafields do not prove it.
6. Return to Products and rebuild Preview: No Change only after complete managed proof,
   with native/publication work shown separately. Check desktop/mobile badge/details.
7. Make one genuine modifier/price edit using the existing editor/targeted rescan.
   Review Update; confirm once and verify retained parent, variant, InventoryItem and
   media IDs did not change and no unchanged image was uploaded.
8. Separately review one valid additive option-value/variant change within bounds;
   existing SKUs/IDs and remote-only variants must survive. No option rename/deletion.
9. For any uncertain/partial operation, stop writes and use Identity & History's
   read-only reconciliation. Report retained stage/IDs/error; never retry parent Create.
   Do not deliberately fault the live store merely to manufacture a recovery case.
10. Recheck the accepted Simple draft (LCBC-0001/16bit-0001) without recreation before
    widening live acceptance. Report mismatches before further development.

M3A.8.3 Simple/native acceptance is user-reported. Fresh 16bit-0001 inventory proof
matched retained data; the apparent Admin mismatch was inspection of the wrong product,
as confirmed by the user. M3A.8.4 Variable live acceptance is not yet claimed.

## M3A.8.3 — stable product workflow and native packed dimensions

### Processing, Review and overrides

M3A.8.2 live acceptance established successful product metadata/override updates,
creation, reconciliation, taxonomy batches and retained media. Asynchronous media
remained Processing because status reconciliation skipped non-verified identities.
Review described Processing as recovery without reading completion. It now finalises
only known Processing through the same complete original-plan verifier. Finished
metadata/media/memberships persist verified identity and comparison; pending media
remains Processing, not fabricated success/recovery. Automatic checks coalesce for
30 seconds; explicit Review/Refresh is fresh. Genuine uncertain writes still require
explicit reconciliation. Old partial operations remain historical, not rewritten.

Create Override on Product Detail/Products opens the existing product-scoped editor
without an empty-file creation/rescan. Unsaved drafts inherit normally; cancel writes
nothing. Save validates, preserves sparse schema, atomically writes genuine changes,
then uses .update and the existing targeted scan. Equal authored documents (including
an empty new draft) do not create files, operations, markers or duplicate notifications.
Missing folders/shared-file collisions are rejected. The legacy explicit create API
is retained for compatibility, but normal editor-opening navigation no longer calls it.

### Scope, API and permissions

Shopify introduced native [packed dimensions](https://shopify.dev/changelog/posts/manage-packed-product-dimensions-with-the-admin-graphql-api)
in [2027-01](https://shopify.dev/changelog/release-notes/2027-01), currently a **release
candidate for development testing until January 1, 2027**, not the stable production
API. Ordinary product/media/collection operations and SHOPIFY_API_VERSION stay on
2026-10. Only three fixed dimension capability/read/write queries use 2027-01, sharing
the existing validated domain, credentials/token cache and bounded/redacted HTTP client.
Wrong served-version headers, missing schema/units, permission/network/throttle errors
are explicit; no automatic version fallback, API upgrade or mutation retry.

Native measurements use InventoryItemMeasurement.packedDimensions and
InventoryItemMeasurementInput.packedDimensions with
[ObjectDimensionsInput](https://shopify.dev/docs/api/admin-graphql/2027-01/input-objects/ObjectDimensionsInput).
Length/width/height are floats, and unit is the exact
[LengthUnit](https://shopify.dev/docs/api/admin-graphql/2027-01/enums/LengthUnit)
enum **CENTIMETERS**. Native readback normalises all six supported units with Decimal
and 0.000001 mm tolerance. No dimensions-only operation writes weight: existing 50 g
is retained. Product dimension metafields remain compatible and independently managed.

[inventoryItemUpdate](https://shopify.dev/docs/api/admin-graphql/2027-01/mutations/inventoryItemUpdate)
requires **write_inventory**. Reads accept **read_products OR read_inventory**; the
current product integration already requires read_products. Existing write_products
and applicable write_files permissions remain necessary for normal product/media
operations. No app permission is changed automatically and no mutation tests access.
The actual InventoryItem GID is resolved from the retained Simple variant and verified
against product ID, SKU, variant/inventory IDs and original correlation—not inferred
from Product GID. Native-only updates do not change metadata, weight, prices, SKUs,
media, collection memberships, product status or sales-channel publication.

### Configuration and measurement policy

Default behaviour is safe/off. Add these optional Unraid variables manually:

```text
SHOPIFY_API_VERSION=2026-10
SHOPIFY_NATIVE_DIMENSIONS_ENABLED=true
SHOPIFY_PACKED_DIMENSIONS_USE_PRODUCT_DIMENSIONS=true
```

The second flag opts into the RC capability. The third is a separate **explicit
measurement policy**, not an assertion that every physical product equals its packed
size: enable only where existing authored dimensions accurately describe packed items,
and confirm each reviewed product. Otherwise leave it false; capability checks remain
read-only but native writes are blocked until a safe packed source exists. No packaging
values, new authored files or parcel presets are invented. Current authored mm/g and
JSON structure remain unchanged. Separately authored packed metadata is deferred to
the comprehensive schema/editor milestone; do not overwrite product dimensions to
work around a packaging difference.

For the explicitly approved 155 × 155 × 3 mm example the reviewed native input is
15.5 × 15.5 × 0.3 CENTIMETERS. Boundary rounding is 0.0000001 cm; too-small/unrepresentable,
missing, invalid or nonpositive local measurements block native work. Returned zero
dimensions are differences, not equality. Malformed/non-null-contract violations are
untrusted evidence. Enabling these flags does not change authentication precedence,
active platform, global API version, app permissions or existing source files.

### Review, write, evidence and recovery

Normal signed Simple Create/Update shows the optional native stage. Missing native
permission/capability/policy leaves successful product/media intact and native work
outstanding. A later reviewed native-only operation can complete it without recreation.
If media is still Processing, native completion remains blocked until product/media
verification completes. Read-only background finalisation never resumes a write.

Product Detail → **Review packed dimensions**, or
`/products/<id>/shopify/native-dimensions`, provides independent read-only capability
inspection and signed/expiring/CSRF confirmation. This isolated measurement update can
service an existing Live-intended Draft without changing publication; normal metadata
publishing still obeys its existing Draft/Live safeguards. Neither Active status nor
native verification proves Online Store availability.

Existing ShopifyProductIdentity bounded version-1 evidence records the native reviewed
plan, pending reservation **before** mutation, InventoryItem ID, state, readback and
operation reference. CatalogueOperation retains attempts/history and shared locks.
There is no migration. Successful subset evidence has its own ten-minute freshness;
Product.updatedAt is not treated as proof of current inventory dimensions. Native-only
success does not advance metadata verification/full-comparison timestamps or clear
unrelated metadata/membership/publication differences.
An independently matching standalone read-only Preview records native comparison
evidence, without claiming a new metadata write verification. The operation lock
prevents this checkpoint from overlapping publishing. Encompassing product operations
remain partial/review-required when their native mutation is uncertain, even when
product metadata/media have independently succeeded.

Known denied permission/userErrors without mutation receipt are Blocked, not ambiguous
recovery. Timeouts, incomplete mutation receipts and failed post-write verification
remain Uncertain/Pending and prevent another native or normal write. Read-only native
reconciliation must prove the original desired measurements on the exact retained
inventory item; an absent/different read never authorizes a blind retry. Restart or
disabling the capability does not erase unresolved native evidence. No rollback/delete,
media upload or price/weight mutation occurs in native recovery.

### Unraid acceptance

1. Back up stopped persistent appdata/database; retain the previous immutable image and
   all mounts. Deploy the new commit-specific AMD64 test tag manually and hard-refresh
   Dashboard assets. Migration head remains 0011; no live database was modified here.
2. With native capability still off, check LCBC-0001 and 16bit-0001 retain verified IDs
   across Products reload, Review and Identity & History. Create one reviewed test
   Simple Draft with original images. A fresh status read should finalise ready media;
   unfinished media explains Processing. No duplicate Create/upload should occur.
3. Open Create Override on a product. Cancel: no JSON/.update/scan/notification. Save
   one genuine sparse edit: only that folder updates through the established scan;
   comparison invalidates locally then reports real verified differences. Save the same
   document again: no redundant scan. Shared JSON and SKU indexes must stay unchanged.
4. For RC native testing, explicitly add write_inventory in Shopify app scopes yourself.
   Test granted scopes; restart the test container if needed to acquire a fresh client-
   credentials token after scope changes. Preserve existing domain/client credentials.
   Add the two capability/policy flags above only after reviewing packed-size accuracy.
5. LCBC-0001: use its retained Product GID 16009365160319, open Review packed dimensions,
   check capability, correct InventoryItem ID, current/proposed measurements, and confirm
   once only when the 155 × 155 × 3 mm source describes the packed item. Verify native
   Shopify shipping dimensions 15.5 × 15.5 × 0.3 cm, unchanged 50 g/price/SKU/media/status.
6. Check another new Draft's signed native stage and separate proof. Missing scopes or
   pending media should retain the draft and explain outstanding native work, not Create
   again. After enabling/fixing access, build a fresh reviewed completion for the same ID.
7. For a genuine uncertain native outcome, stop writes and use **Reconcile native outcome
   using reads only**. If equality cannot be established, inspect the inventory item and
   report the retained evidence; never repeat the mutation blindly.

No automatic deployment, stable/latest overwrite, main merge, Variable/bulk publishing,
channel/menu operations, shipping-label/package-preset features or M3A.9 editor work.
User-owned Unraid XML remains outside the commit; variables are added manually.

## M3A.8.2 — live acceptance corrections and unified workspace

### Dimensions, supported fields and recovery

The old reader accepted only `mm`, `cm`, `m`, `in`, `ft`, `yd`; current Shopify
[dimension values](https://shopify.dev/docs/apps/build/metafields/list-of-data-types)
use full unit names. Controlled canonical readback reproduced “Remote dimension
evidence is invalid.” New writes use `millimeters`; reads also accept legacy short
codes and uppercase names, convert to Decimal mm and use 0.000001 mm tolerance.
Thus 15.5 centimeters = 155 mm, 0.3 centimeters = 3 mm. Authored JSON stays in mm,
weight remains 50 GRAMS when authored as 50 g. Malformed, nonfinite, nonpositive,
unsupported units and incompatible metafield types never establish equality.

For configured **2026-10**, product dimensions are supported through
[ProductUpdateInput.metafields](https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/ProductUpdateInput),
not native length/width/height in
[InventoryItemMeasurementInput](https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/InventoryItemMeasurementInput).
The latter supports weight/shipping-package identity, not arbitrary individual product
dimensions. Existing configured 2026-01/04/07 versions retain the metafield path and
legacy read compatibility; no API upgrade/native shipping-dimension feature is added.
Keys remain `product_length`, `product_width`, `product_height` in the configured
`SHOPIFY_PRODUCT_DIMENSIONS_NAMESPACE` (default `tlc_dashboard`). They are product
measurements, not parcel dimensions or the Shopify Shipping package editor.

Origin and requiresShipping already map through the reviewed variant inventoryItem
input; [productVariantsBulkUpdate](https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/productVariantsBulkUpdate)
requires **write_products**. No standalone inventory-item update is introduced:
[inventoryItemUpdate](https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/inventoryItemUpdate)
would require **write_inventory**, and is not used here. Inventory reads accept
read_products/read_inventory access; current supported connection/product reads use
read_products. Existing alt edits require write_files. HS codes, native package
configuration and comprehensive JSON fields remain deferred. Do not add broad scopes
just to work around absent authored origin/shipping values.

Main description comes from resolved authored `description`, independently of SEO;
tags retain existing explicit/unspecified semantics. `shopify_product_type` must be an
explicit authored label. Missing values produce readiness warnings; no UK origin,
type, tags or SEO-to-body substitution is invented. M3A.9 will expose these fields
comprehensively, not implemented here. Actual live source JSON was not provided for
the reported missing-field products, so absent Shopify fields alone prove no loss.

Successful product IDs, mutation receipts, original media IDs/URLs and memberships
are retained before later verification. Readback now persists identity/media observations
before dimension parsing and labels field/membership/media verification separately.
Only complete fresh managed evidence advances the product verification timestamp.
Read-only reconciliation reuses the **original retained plan**, including legacy unit
codes, and never creates/reuploads/retries. Missing creation media receipts require a
separate signed fresh local/remote gallery approval with identical original hashes,
order and alt; old reviewed digests/history are not overwritten to manufacture success.
Unresolved fields, missing memberships/media and genuine uncertainty remain blocked.

### Unified Taxonomy and checkbox errors

`/taxonomy` and `/taxonomy/shopify` now share one workspace: Categories, Storefront
Collections, Attributes & Tags, Shopify Sync. Registry counts, hierarchy/order, stable
keys, existing edit/review/save/backup services and retained mappings remain authoritative.
Last-known mapping badges are labelled; individual Inspect/Reconcile provides fresh
remote verification. No GET performs Shopify reads. Product counts are not invented
where assignment/publication eligibility has not been verified.

Search/mapping filters, expand/collapse, Select All/None/Unmapped and explicit Select
Parent and Descendants replace auto-selected cramped checkboxes. Selection starts
empty. Shared ancestors count toward 20 groups, not silently selected descendants;
actual create/join/tag operation counts appear in signed Preview. Product scope remains
25 Simple parents per explicit cursor page. Registry definitions are locally searchable
together within the existing 2000-definition bound; no hierarchy-splitting pagination.
Storefront assignments span folders; folders still imply no merchandising collection.

A populated normal checkbox POST passed controlled reproduction. A **real failing
legacy path** was reproduced: older verified product evidence without `plan.managed`
raised an uncaught KeyError in batch review. It is now explicitly excluded with
individual Review instructions; its identity/mappings are not rewritten. Invalid
selection/cursor/execution input returns actionable validation before remote writes,
and conflicting database reservations are shown without bypassing uniqueness.
Safe diagnostic logs record only known action and exception class, never request
credentials. The original live traceback was unavailable: successful HTTP 200 excerpts
do not prove the historical incident's cause. Report any remaining failure with its
timestamp/action and redacted server traceback, not credentials.

### Unraid acceptance (operator initiated)

1. Back up stopped persistent appdata/database and retain the previous immutable image.
   Select this milestone's immutable AMD64 tag or `develop`; keep all mounts/settings.
   Migration head stays **0011_shopify_taxonomy**; no schema/backfill change is needed.
2. LCBC-0001: open Products → Identity & History. Confirm retained product
   `gid://shopify/Product/16009365160319`. Run **Reconcile using Shopify reads only**.
   Inspect separate field/membership/media checkpoints; verify no new product/image.
   Only after recovery is supported, Review & Sync against the same ID. Check 155 ×
   155 × 3 mm, 50 g and £3.50; native Shopify Shipping dimensions are not this metafield.
   Preserve Live intent/Draft reality; channel publication remains deferred.
3. 16bit-0001: use its existing retained GID, never Create. Read-only reconcile original
   media and Cards/Anniversary Cards/16-Bit memberships through retained IDs. Missing
   receipts require inspecting and explicitly approving the observed gallery. If a
   field/membership is genuinely missing, keep recovery and report it; no blind retry.
4. Taxonomy: inspect Cards, Birthday Cards, Anniversary Cards and 16-Bit saved verified
   mappings; use individual Inspect for current remote evidence. Test local sections,
   ordering/search/filtering and compact editing on desktop/mobile. No mapping should
   disappear on reload; no duplicate collection is created because a local name changes.
5. Shopify Sync → selection: choose a small scope, optionally explicit descendants.
   Verify ancestor counts, read-only proposal and exclusions. Invalid/oversized scope
   must show validation, not 500. Confirm only intended safe stages; inspect durable
   partial results before any continuation. Existing identities/media/prices survive.

No automatic deployment, Create retry, deletion, media conversion, membership removal,
menu/channel publication, variable/bulk product publishing or JSON-editor rewrite.

## M3A.8.1 — automatic product checking and taxonomy sync

### Status policy

Products initially renders saved evidence only. Administrators' browser then submits
bounded read-only refresh steps: up to 25 IDs, two complete canonical comparisons per
request, serialized by existing operation control. Multiple navigations/tabs reuse
persisted results. Manual Product/Visible refresh forces a new comparison; Refresh All
walks ordered Simple-product pages, processing pending IDs before the next page.
The client stops after 100 requests; no uncontrolled fan-out or perpetual polling.
Closing the page pauses new requests; an in-flight bounded read may still complete.

| Evidence | Freshness / action |
|---|---|
| Local supported data | Content-derived effective JSON/shipping/tags/taxonomy plus authorized image stats; changes queue full comparison |
| Product ID/updatedAt/status | Batched GraphQL nodes, ten-minute freshness |
| Full managed fields, media/inventory | M3A.8.5.3: successful complete comparison no more than ten minutes old; effective local changes force it sooner |
| Collection membership | Independent ten-minute check; never extended merely by product updatedAt |
| Publication | Still unverified/deferred; ACTIVE is not Online Store availability |
| Verified remote absence | Ten minutes, invalidated by source/config/identity changes; Create always rechecks SKU/handle/identity |
| Failed reads | Historical evidence retained, no new equality; 30-second exponential backoff capped at 15 minutes |

The existing 0011 bounded version-1 comparison JSON gains revision, full/related/absence,
previous equality and failure timestamps. No migration or independent sync database.
Last full successful comparison, last attempted/failed check, previous verified equality
and complete post-write verified_at remain distinct. Unsupported/unresolved publication
prevents unqualified Up to Date. Before remote comparison, changed evidence shows **Checking
Changes**; only verified differences show **Update Available**. Suitable checked
absence shows **Needs Creating**, never just a missing local GID. Null retained nodes are
missing/inaccessible evidence, not a confirmed deletion or permission to recreate.
Possible local+remote changes are disclosed for operator review; no authored data is
automatically overwritten. Client Credentials transport/expiry/redaction is unchanged.

The fixed [nodes query](https://shopify.dev/docs/api/admin-graphql/latest/queries/nodes)
uses at most 25 IDs, below Shopify's input-array bound. Existing network/response-size
limits apply; rate-limit failures back off rather than automatically retrying mutations.
GraphQL cost is still metered, not free. See [API limits](https://shopify.dev/docs/api/usage/limits).

### Correct organisational model

Filesystem catalogue collections manage scanner/editor/inheritance/SKU organisation;
they **do not automatically become Shopify collections**. The previous M3A.8 inference
is removed without deleting its stored mappings or remote memberships. The registry
still owns categories, stable keys/parents/order/aliases, storefront collections and
tag vocabulary. Existing resolved `categories` and `storefront_collections` assignments
are consumed directly. Child category assignment derives every valid active ancestor;
multiple branches are deduplicated and no extra references are written into JSON.
Inactive/missing/cyclic/ambiguous definitions block affected memberships.

Storefront collection assignments may span catalogue folders; missing assignment
means none, not an inferred folder name. Existing inheritance/sparse overrides apply.
Tags remain separate: unspecified is unmanaged, specified lists use reviewed replacement,
and effective explicit empty lists disclose removals. Registry vocabulary does not
generate tags. Optional standard Shopify category remains separate and is not guessed
or required for unrelated collection sync.

### Taxonomy proposal and execution

Open Taxonomy → Shopify collection mappings. All definitions are discovered locally,
including empty/inactive definitions; cached counts are not fresh remote proof. Select
up to 20 active groups (first 20 initially selected) and **Review Taxonomy Sync**.
Selected categories automatically include required active ancestors within that bound.
Each proposal covers up to 25 active Simple products; an explicit next-page cursor handles
larger catalogues. Review mapped identities, candidates, Create/No Change, ancestor/
storefront membership additions, tags and exact removals. Candidate conflicts require
individual verified Link, not adoption by title. Unverified/recovery products and blocked
groups are visibly excluded; successful supported work can finish **Partial**, not
whole-sync success. Empty local definitions stay available; new remote collections
remain unpublished. No automatic rename, removal, deletion or menu creation.

One signed 15-minute review and CSRF approval starts bounded sequential application.
CatalogueOperation stores the proposal/results/claimed step (version 1, max 128 KiB),
and child creates/product taxonomy writes retain individual operation identities.
Create uses existing SQLite uniqueness, pending-before-request and attempt-marker
readback. Product stages reread store/product identity and the reviewed remote/source
digest, write only tags/memberships and independently verify each target. No price,
shipping, media, product status, standard-category or publication write in the batch.
Standard-category updates remain in the normal signed product Review.

Closing the page pauses later stages. GET history never resumes writes automatically;
**Continue approved stages** is explicit, with one-hour execution approval and source/
configuration checks. Original child collection reviews still expire after 15 minutes;
an expired remaining Create requires a fresh proposal, not an unsafe retry. Failed or
interrupted steps retain successes and show the exact blocker. Inspect interrupted
stage is read-only: first reconcile the uncertain collection marker or product taxonomy
attempt through the existing identity workflow. If a membership genuinely did not land,
resolve that reviewed membership explicitly in Shopify and reconcile; no automatic
retry/rollback. Fresh proposals skip retained verified collections rather than recreate.
Subset taxonomy verification does not advance complete-product verified_at or falsely
certify metadata/media/publication equality; subsequent automatic full comparison does.

Required scopes: read_products for revisions/collections; write_products for approved
manual collection, tag and membership writes. write_files remains only for separately
reviewed existing-file alt changes. No menu/publication scopes are used. The supported
2026-10 collection mutations/source safety checks remain from M3A.8. No credential
values or access tokens are stored in proposals/comparisons or sent to browser code.

### Unraid acceptance

1. Back up stopped appdata; deploy the new immutable tag (or matching develop), keeping
   mounts, database and credentials. No new migration: head stays 0011_shopify_taxonomy.
2. Open Products: rows must appear immediately; bounded Checking progress updates
   statuses. Reopen within freshness: no redundant full comparisons. Refresh Visible
   or Refresh Product is explicit; Refresh All remains usable and bounded.
3. LCBC-0001: verify retained Product/Shop identity and automatic dimensions/weight
   Update Available after comparison. Actual Draft and intended Live stay separate;
   no automatic publish/update. Edit a genuine supported source field, allow targeted
   scan, and check Local Changes Detected → confirmed Update Available.
4. NBNOC-0004: Not Yet Verified until safe SKU reconciliation establishes absence;
   then Needs Creating, not automatic Create. Invalid/missing required metadata remains
   a visible validation blocker. Reopening reuses absence; reviewed Create still rechecks.
5. Taxonomy: inspect Cards/child hierarchy and authored storefront collections separately
   from filesystem groups. The preceding live test only inspected/proposed Cards Create;
   no creation was confirmed and no retained mappings existed then.
6. Review the selected taxonomy scope. Resolve candidate links explicitly; inspect
   proposed parent memberships, tag removals and exclusions. Confirm once only when
   satisfied. Check durable per-stage results and actual unpublished collections in
   Shopify Admin. Product IDs/images/prices/publication must remain unchanged.
7. Verify a child-assigned Simple product belongs to child+parents, and a shared authored
   storefront collection spans different catalogue folders. A product without authored
   storefront collection must gain no invented folder-based merchandising membership.
8. Refresh Products: metadata, memberships and publication must be distinguished.
   Simulate temporary connectivity loss only if safe: historical evidence remains,
   Unavailable replaces current equality, and retries back off. For genuine uncertainty,
   stop writes and use read-only recovery; never repeat a Create blindly.

Live acceptance is still required. Variable/bulk product publishing, menus, actual Live
transitions, automatic remote renames/removals and broader JSON editor changes remain
deferred. M3A.9 complete Create/Edit JSON is next after acceptance, not part of this slice.

## M3A.8 — comparison evidence and reviewed taxonomy synchronisation

### Products status and review

Products/Detail use local evidence only. Fresh read-only Review stores Create/Update/
No Change, changed fields/count, actual status, blockers and collection differences
in a bounded version-1 summary. It survives restart, is reusable for ten minutes,
and is distinct from completed-publish verification. Equality requires unchanged
managed JSON/inheritance, image stats, relevant registry definitions, collection
mappings, product identity and configuration. Expired evidence requires Review;
changed local sources show an available update, not an old change count. A source
edit during reads cannot receive equality from the earlier plan. Recovery and
Processing override cached comparison. Confirm still rereads and validates signed
source/remote/taxonomy evidence before any write.
Failed refresh preserves historical comparison/verification but downgrades current
equality to Verification Unavailable; a successful explicit Review can recover it.
Legacy readback remains valid for its original contract without backfilling equality
for newly managed taxonomy.

LCBC-0001's old Verification Unavailable was caused by reading only completed-publish
evidence while Preview's current comparison was temporary; older operations lacked
the later local-source baseline. Fresh comparisons now have their own durable record.
No legacy identity is backfilled or rewritten. Live intent can now be compared, with
the unsupported Online Store transition clearly blocking Confirm. Matching metadata
plus outstanding publication is Needs Attention, never unqualified Up to Date.

### Existing TLC organisation → Shopify

The schema-1 registry contains stable category keys, parent keys, slugs, aliases,
state/order, storefront collections, attributes/terms and tag vocabulary. Product
assignments resolve from existing shared JSON + sparse overrides, not a new file.
Active assigned categories and storefront collections map by their stable keys;
the enclosing catalogue Collection maps by its stable source-relative folder path,
not a transient SQLite ID. Hierarchy/order stay local. Ancestors are not automatically
joined; later menus may keep parents visible through eligible descendants.

Open **Taxonomy → Shopify collection mappings** (`/taxonomy/shopify`). Review one
source, then explicitly Confirm Create or Link. Create checks retained identities and
bounded title/handle candidates; any candidate blocks Create and must be explicitly
linked by GID after authoritative store/collection checks. Inspect candidates displays
real IDs without saving or authorizing any mapping/write; use a separate signed Link
review for explicit adoption. Only manual product collections are supported: rule-based,
shared, variant and subcollection sources are blocked.
New collections remain unpublished. Renames do not create new identities or rename
remote collections; update the retained mapping explicitly, not the remote title.

Return to the product Review for **Join / Already assigned** memberships, including
multiple collections. Up to 12 groups are supported sequentially per Simple product.
Missing/ambiguous/unverified mappings block Confirm but keep read-only Review useful.
No existing membership is removed and no automatic collection deletion occurs.
Every intended membership is independently read back before complete verification.
Collection mapping/creation is a separate signed operation from product publishing,
so partial completion and its retained operation history remain visible.

Collection Create reserves identity before the request, retains an attempt-marker
metafield and commits a returned GID before readback. Timeout never means absent.
Choose read-only Reconcile with an explicit candidate GID; its marker must match the
retained attempt. Missing marker/ambiguous evidence remains blocked for administrator
investigation. A membership timeout similarly preserves the product's earlier writes;
read-only product reconciliation can verify a join that succeeded. If membership did
not succeed, resolve the missing membership explicitly in Shopify and reconcile;
automatic partial-write resume is not implemented. Do not retry the whole product.

Optional **standard Shopify category** GIDs on mapping Review are validated against
the configured API (including archived-state rejection), shown by full name, applied
only after product Confirm and independently verified. Conflicting assigned mappings
block writes. No name inference. Blank leaves product category unmanaged, not cleared.
Optional authored `shopify_product_type` maps to `productType` independently; no TLC
hierarchical path is silently used as a product type.

### Tags and metadata

Registry tags are an allowed vocabulary, not implicit assignments to every product.
Existing authored tags follow established additive shared/product inheritance and
scanner projection; the publisher rereads that effective authored definition.
Missing `tags` means unmanaged and preserves Shopify tags. Explicit nonempty or
resolved empty `tags` uses reviewed full replacement, with exact remote-only removals
disclosed in Warnings. A sparse product `tags: []` does not erase inherited tags under
the existing merge contract. No automatic tag clearing or generated merchandising.
No demonstrated live NBNOC-0004 source-loss defect is claimed: actual source JSON was
not supplied. Main description remains independent of SEO; absent shipping/origin/
tags remain explicit warnings rather than invented fields. Original WebP media and
IDs/CDN URLs remain untouched on metadata/collection updates.

### Permissions, persistence and acceptance

`read_products` supports collection/standard-category reads; `write_products` supports
manual collection creation and product joins. `write_files` remains required only for
reviewed existing-file alt changes. Missing scopes block affected Confirm, not useful
read-only comparison. No menu/publication permissions are requested here. Basic is
compatible; Shopify excludes Starter/Retail for collection creation. The existing
supported collectionCreate(input)/collectionAddProducts APIs are deprecated but remain
available in 2026-10; authoritative source checks cover the newer collection model.
See [collectionCreate](https://shopify.dev/docs/api/admin-graphql/latest/mutations/collectionCreate),
[collectionAddProducts](https://shopify.dev/docs/api/admin-graphql/latest/mutations/collectionAddProducts)
and [collection sources](https://shopify.dev/docs/api/admin-graphql/latest/interfaces/CollectionSource).

Migration `0011_shopify_taxonomy` adds store-scoped unique collection identities and
comparison summaries, each JSON record versioned and capped at 32 KiB. Existing Woo/
Shopify product identities are untouched; no No Change backfill. Back up stopped
persistent appdata before upgrading (same mounts/database). Startup migrates using
existing deployment flow; verify the database's `alembic_version` is
`0011_shopify_taxonomy`. Do not downgrade/drop operational tables after remote writes
without preserving their identities/history. Previous image remains a rollback option,
but preserve the upgraded database and mappings rather than restore an old backup and
lose new Shopify identities. No credentials are stored in either new table.

Unraid acceptance:

1. Back up stopped appdata; deploy the milestone's immutable test tag or matching
   `develop`, preserving all mounts/credentials. Confirm migration and Test connection.
2. Products → LCBC-0001 → Review & Sync: confirm retained Product GID
   `gid://shopify/Product/16009365160319`, build Preview and inspect dimensions/weight
   Update. Return to Products: Update Available and comparison time/count should show.
   If actual authored intent is Live, Confirm must remain blocked; do not falsify it.
3. If genuinely intended Draft, resolve displayed mappings and Confirm its Update once.
   Check the same Shopify product, original media IDs and complete readback. Build a
   new Review: No Change, then Products Up to Date only without remaining blockers.
4. NBNOC-0004: build Create Preview only; inspect reviewed main copy versus SEO, missing
   shipping/origin warnings, original WebP ordering and assigned category/collection/tag
   values. No write occurs until explicit Confirm and all blockers resolve.
5. Choose a Simple product with actual TLC assignments. Review missing mapping Create
   or explicit Link in Taxonomy, verify the new unpublished collection/retained GID,
   then rebuild product Review. Inspect multiple memberships, tags (including removals)
   and optional explicit standard category; Confirm one supported Draft operation.
6. Check memberships/tags/category in Shopify Admin; reconcile processing/uncertainty
   read-only. Restart and ensure identities/comparisons survive. Check mobile primary
   action clearance and collapsed Matching/Images/Advanced sections.

No automated live writes; new taxonomy/member/update acceptance is still pending.
Online Store publication, menus/empty-menu filtering, Variables, bulk and broader
reference-safe taxonomy editing remain deferred. Next: complete existing Create/Edit
JSON forms after acceptance, not a second metadata system.

## Product UX and metadata completion

Product Detail now uses a compact shared-token Shopify card: **Review & Sync**,
View on Shopify and Refresh / History. Up to Date denotes matching local source
against retained independently verified managed evidence; its timestamp is explicitly
**cached**, never a fresh GET on page render. Draft status and Online Store publication
remain separate from authored intent. Recovery blockers remain visible under Evidence
& blockers. Review shows readable field values and matching/change actions; raw API
plans and source references are collapsed under Advanced Details. Mobile change rows
become cards and primary actions span the available width above bottom navigation.

Metadata trace: collection/product `product_info.json` is merged by
`merge_product_json` (scalar overrides, additive tags), then scanner CSV carries
Description/Tags to ingestion's Product/Tag projection. `product_document` resolves
the same sources again for the immutable Shopify snapshot. The publisher prefers
resolved authored description/price/shipping over an older projection, maps main
`description` to `descriptionHtml`, and maps `meta_description` independently to SEO.
There is no SEO-to-main-description substitution. Unspecified shipping/origin and
absent tags remain unmanaged; empty main description now carries an explicit warning.
Explicitly authored empty description remains a reviewed empty value, not generated copy.

The reported NBNOC-0004 Preview is not its source JSON. In a Create review, the
**before** value is absent because no remote target exists. This cannot establish
that an authored description was lost. The genuine display defect was nullable
compare-at `None → None` being labelled Change; it is now Already matching. Regression
fixtures verify inherited main copy, sparse overrides, SEO independence, 50 g weight,
110 × 100 × 6 mm dimensions, tags, and explicitly authored shipping/origin. Check the
actual resolved **reviewed** value for NBNOC-0004 during Unraid acceptance; no unprovided
live JSON values or missing descriptions/tags/origin have been inferred.

Original WebP bytes, filenames, alt text and projected gallery order are retained.
Shopify media IDs/CDN URLs still come only from returned API evidence, never extension
substitution. No image conversion or source writes were introduced.

LCBC-0001 with `live: true` still blocks writes to its retained Draft. A metadata-only
mode that deliberately leaves publication intent unsatisfied is deferred to a separate
reviewed operation contract, alongside verified Online Store publication. Never change
authored intent to bypass the blocker. No ACTIVE → Live inference or channel writes.
Shared visible branding is TLCDashboard; legacy assets and persistent/repository/image
names remain intact. Unraid testing can now use Docker `develop`; each tested build
also has an immutable commit-specific rollback tag with the same image digest.

## Shopify-first Products workspace

Open **Products**, find LCBC-0001, then **Review Shopify Changes** (also on Product
Detail). The product-scoped workspace uses the existing publisher, not another
catalogue: build its read-only Preview, review Update/No Change, and confirm once only
when changes are supported. Retained verified identities never enter Create. Identity
/ Refresh opens the existing read-only reconciliation and retained operation evidence;
uncertain writes stay blocked. First creation checks remote SKU candidates through
bounded [Shopify productVariants discovery](https://shopify.dev/docs/api/admin-graphql/2026-10/queries/productVariants)
at review and confirmation; candidates are not silently adopted.

List/Detail/Dashboard GETs do not contact Shopify. No Change there is explicitly the
last verified managed state with matching local metadata/image stats, not a claim
that remote state was just checked. Preview still hashes bytes and reads current
remote fields. Source fingerprints live only in existing versioned bounded operation
evidence; legacy records without that baseline display Verification Unavailable with
their retained identity and timestamp. No migration/backfill or authored rewrite.

`live: false` is required for draft writes. `live: true` is displayed as Live intended,
but blocked until reviewed Online Store channel publication is implemented; missing
or invalid intent must be resolved in the existing collection/override editor. Do not
change a genuinely intended Live product to Draft merely to bypass this limitation.
Shopify status and channel publication are separate; publication remains Not verified.

Selection (individual, collection's visible products or eligible page products)
prepares individual review links only. It does not authorize or execute a batch.
Variable publishing, collections/navigation, actual Live transitions and storefront
customisation remain deferred. Existing M3A.5 fields/media limitations below apply.

### Unraid acceptance from normal Products

1. Back up persistent appdata/database and use the new commit-specific test image,
   preserving every existing volume/environment value. No migration is added here.
2. Test Shopify connection with the installed app's existing `read_products` and
   `write_products` scopes; add `write_files` only for reviewed alt-text changes.
3. Open Products → LCBC-0001 → View Product. Confirm retained Product ID
   `gid://shopify/Product/16009365160319`; legacy evidence may require Review for
   current comparison. Inspect authored/inherited live intent, shipping and metadata.
4. If intended Draft, ensure the resolved authored `live` is explicitly false through
   the established editor. Save only intentional edits; let targeted rescan complete.
5. Review Shopify Changes → Build read-only Preview. Expect Update or No Change,
   never Create for the retained verified product. Resolve recovery via Identity first.
6. Review changed/matching/unsupported fields and media actions; explicitly confirm
   a supported Update once. No Change needs no write. Do not retry uncertain writes.
7. Verify the same product ID in Shopify Admin, shipping/SEO/pricing/dimensions and
   original media IDs/URLs. Use Identity / Refresh for processing or recovery.
8. Return to Products and build fresh Review: expect No Change after successful
   complete readback. Check mobile action visibility and selection-only review queue.
   Report discrepancies before bulk or collection publishing proceeds.

The operational fingerprint reuses the Simple publisher's supported field mapping;
changes only to deferred customisation/category metadata do not falsely imply a
publishable Update. Such definitions remain visible locally and unsupported in Review.

## M3A.5 — complete managed Simple drafts and reviewed updates

Implemented, mocked-test verified; **live update acceptance pending**. The original
M3A live test below remains creation/media acceptance only. `/shopify/drafts`
now previews Create, Update or No Change; unsafe/uncertain identities show an
explicit recovery blocker. A retained product never falls back to Create, even
if it disappears remotely or configuration points at a different store. Verified
legacy mappings are read without rewriting their store keys/domain. Only one
active local Simple parent and one remote Simple variant are supported; DRAFT
only. No sales-channel publication, Variables, collections or category writes.

### Managed product information

| Authored/resolved source | Shopify target / policy |
|---|---|
| Title, description | `title`, `descriptionHtml`; same filesystem resolver |
| Stable SKU | Retained default variant / inventory item SKU, never new local allocation |
| Regular/sale price | No sale: regular → price, compare-at cleared. Valid unscheduled sale below regular: sale → price, regular → compare-at. Invalid/equal/higher sale or scheduled sale dates block review. Removing a sale restores regular price. No automatic sale scheduler. |
| Weight in grams | `inventoryItem.measurement.weight`, unit `GRAMS`; readback compares equivalent weight units |
| Shipping intent, origin | `requiresShipping`, `countryCodeOfOrigin`; absent values are unmanaged. Origin is checked against the configured API's authoritative `CountryCode` enum, not only its format. GB is never implicit. |
| Optional vendor | `vendor` only when explicitly authored/configured in resolved metadata |
| Tags | Reviewed full tag-list replacement, including remote-only tags; absent tags stay unmanaged |
| `meta_title`, `meta_description` | Shopify SEO title/description when authored; supported limits 70/320 characters |
| Optional `handle` | Explicit lowercase slug; uniqueness checked. Updates preserve attempt correlation, do not create redirects. Without authored handle, existing URL stays unchanged. |
| Dimensions in millimetres | Product metafields of type `dimension`, keys `product_length`, `product_width`, `product_height` in namespace `tlc_dashboard` |
| Original image gallery | Create uploads original PNG/JPEG/WebP; update retains IDs/URLs and unchanged source hashes, with reviewed order/alt changes |

Dimension namespace is optionally configured with
`SHOPIFY_PRODUCT_DIMENSIONS_NAMESPACE` (default `tlc_dashboard`). Values are typed
JSON such as `{"value":155,"unit":"mm"}`. Namespace/key upserts reuse existing
metafields; no definitions are automatically created. Incompatible existing field
types block review. These are **individual product dimensions**, not finished
parcels, package presets or Shopify Shipping label measurements. Changing namespace
does not delete old fields. Zero/absent dimensions are omitted. Missing optional
fields never manufacture defaults or silently clear remote information. Current
vendor/handle are forward-compatible Advanced JSON values, validated by publishing.

Inventory tracking, quantities, locations, fulfilment services and shipping package
configuration are never written. Standard categories, collections, relationships,
customer-facing customisation and scheduled-sale automation remain deferred.

### Review, execution and recovery

Preview reads connection/store identity, the current complete remote Simple state
and original local hashes. It shows changed/matching fields, warnings, unsupported
features and media actions. Country enum and handle checks add bounded reads only
when needed. Confirm is administrator-only, CSRF-protected and signed for 15 minutes.
It revalidates local/configuration/identity/remote digests under the existing
operation lock. Only the reviewed changed fields are mutated; source/configuration
is rechecked between write steps. A No Change plan has no confirmation/write token.

Pending/update-attempt evidence is committed before mutations to the existing
`ShopifyProductIdentity` record. Bounded version-1 evidence retains previous verified
plan provenance, variant/inventory IDs, media receipts, completed stages and job
receipt where applicable. CatalogueOperation history is retained, not rewritten.
Independent readback must prove the whole managed contract, IDs, correlation and
gallery before state becomes verified. No migration, source rewrite or Woo change.

Update, readback or transport failure retains earlier successful steps and blocks
another write. Explicit reconciliation uses only reads, serialized against writes,
and does not retry/delete/recreate anything. A complete match verifies the reviewed
result; incomplete/partial/different results remain uncertain. An unchanged read
alone does not prove that an uncertain delayed mutation cannot still arrive. Inspect
the retained draft and correct the reviewed fields in Shopify Admin if necessary,
then reconcile again. Historical partial/interrupted operations remain visible.

Unchanged images are never staged or re-uploaded on update. Image replacement,
addition/removal, missing mappings or remote-only media block review rather than
delete/overwrite. Alt uses `fileUpdate` and requires **`write_files`**; the existing
`read_products` / `write_products` scopes cover product/variant read/update. A shared
Shopify file's other uses can reflect its alt change; this is disclosed at review.
Gallery reorder is asynchronous: an incomplete order stays Processing until an
explicit read observes it. No background retry/poll storm. Actual returned CDN URLs
remain evidence; extensions are never rewritten. API contracts:
[inventory fields](https://shopify.dev/docs/api/admin-graphql/latest/input-objects/InventoryItemInput),
[alt updates](https://shopify.dev/docs/api/admin-graphql/latest/mutations/fileUpdate),
[gallery order](https://shopify.dev/docs/api/admin-graphql/latest/mutations/productReorderMedia).

### Live acceptance: update the retained LCBC-0001 draft

1. Back up persistent appdata/database and retain the previous image tag. Deploy
   the unique M3A.5 AMD64 test image manually, keeping all current volume mappings.
   No new migration is required; existing 0010 remains authoritative.
2. Keep the verified store configuration. If changing alt text, release/install
   an app version granting `write_files` too; test connection (restart to obtain
   a fresh Client Credentials token after a scope change if necessary).
3. Use existing metadata editor/Advanced JSON to review accurate origin, shipping,
   weight, descriptions, prices/SEO/vendor/tags/dimensions. Use the offered affected
   update workflow where projection changed. Never change source filenames/SKU.
4. Open `/shopify/drafts`, select `LCBC-0001` and build read-only Preview. Verify
   retained Shop `gid://shopify/Shop/103940129151` and Product
   `gid://shopify/Product/16009365160319`; the plan must say Update or No Change,
   not Create. Stop if the identity is missing or a recovery blocker appears.
5. Inspect every field/tag replacement/handle/media action and confirm once. Check
   the same draft in Shopify Admin: SKU, customer price/compare-at, shipping/origin,
   SEO/vendor/tags, product dimension metafields and retained image IDs/URLs.
6. If Processing, use read-only reconciliation after the gallery job finishes.
   If uncertain, do not confirm again; reconcile and report the retained operation.
7. Successful verification followed by fresh Preview must show No Change. Confirm
   that no duplicate product or unchanged-image upload occurred. Report differences
   before continuing to category/collection mapping.

## M3A.4 catalogue snapshot readiness

The existing resolver supports optional `country_of_origin`, `requires_shipping`
and `customisation`; see [schema and inheritance](PRODUCT_INFO.md). Advanced JSON
uses existing validation/rescan controls. No user catalogue is automatically
rewritten. Optional `DEFAULT_COUNTRY_OF_ORIGIN` can be supplied through Docker
`.env` or a manually added Unraid variable; leave blank unless reviewed and accurate.
Product/collection origin overrides it; no shipping intent is invented.

The immutable snapshot exposes `shipping` (grams/mm, origin/provenance, shipping
intent), `seo`, optional authored `vendor`/`handle`, `storefront_collections`,
resolved `taxonomy`, `customisation`, and each variant's projected weight/dimensions.
Existing descriptions/prices/images/identities remain available. Vendor/handle
are retained forward-compatible metadata, not newly formalised editor fields.
No new DB columns/migration. These fields are ready for a future publisher,
**not silently added to M3A's remote write contract**. M3A still creates/verifies
one Simple draft's existing title/description/SKU/price/media contract. Shipping,
SEO, sale policy, standard categories and collections require reviewed managed
updates. Retained identities still block duplicate creates. Customisation does not
create variants, forms, proofs or customer uploads.

Existing Taxonomy editing remains available. Reference-impact review for category
path changes and customer-navigation eligibility are deferred; drafts or unknown
Shopify publication cannot make customer menus visible. Registry hierarchy/order
remains the future authority, never an independent navigation file.

Connection/discovery remain read-only. M3A adds separately reviewed creation of
one Simple draft with original images; the user has now accepted that core live
path (details below). Complete publishing remains unfinished. Woo remains a
temporary fallback; no runtime rename or catalogue migration has occurred.

## TLCDashboard transition assessment 2026-10-12

### Direction and live evidence

TLCDashboard is exclusively for Tender Loving Creations. Shopify is the intended
permanent commerce platform, not one half of a permanent dual-platform product.
Keep Woo as a working fallback without new features until a separate retirement
milestone. Do not implement generic plugins, public distribution, multi-business
support or a platform-switching migration workflow.

User-reported live evidence (not a new API call by this assessment): Client
Credentials authenticated Shop `gid://shopify/Shop/103940129151`, API `2026-10`,
with `read_products` and `write_products`. SKU `LCBC-0001`, £3.50, was created as
unpublished draft `gid://shopify/Product/16009365160319`. Original local PNG upload
and product were visibly confirmed in Shopify Admin. Explicit read-only
reconciliation reached retained stage `verified`. This proves core Simple create,
media and reconciliation, not shipping completeness, updates, Variable products,
modifiers, collections/categories/navigation, personalisation, orders or bulk sync.
Full-lifetime automatic token renewal was not part of the reported live test.

The user observed `tenderlcreations.myshopify.com` failing identity comparison and
`jrg9sr-yy.myshopify.com` successfully verifying this Shop. Preserve that evidence
and working configuration. M3A.2's domain-alias correction is mocked-test verified,
not yet separately live accepted; do not attribute the successful original-domain
test to the alias fix. Store GID remains authoritative; authentication alone
never proves a configured domain belongs to a store.

### Catalogue authority, intake and projection

The physical layouts are fixed: Simple Product (`collection_type: Simple`),
Variable Collection (`Variable Collection`) and Single Variable Product
(`Single Variable`). Labels are not instructions to rename stored enum values.
Collection-root shared JSON, optional Simple/Variable Collection overrides,
Single Variable root JSON and reserved case-insensitive Parent image ownership
remain exactly as implemented. Do not move folders/images, allocate replacement
SKUs, rewrite JSON or change marker semantics.

`intake_structured_import.py` safely copies reviewed existing trees into Prepared;
existing JSON is preserved byte-for-byte. `intake_metadata_builder.py` validates
and explicitly authors missing definitions through the existing staged writer.
Prepared validation/copy handoff precedes an explicit catalogue scan. Existing
metadata/override editors use `product_info.py`, `metadata_workspace.py` and safe
JSON writers; inherited defaults are not flattened into product overrides.
The legacy `/api/override/create` route operates on an already projected product
and cannot bootstrap a catalogue lacking collection JSON; it creates only `{}`
when missing. Fresh folder import must therefore use the pre-scan Intake/editor
path, not assume this projected-product action can create shared requirements.
This is not automatic generation of unknown product details.

Important fresh-install gap: `_eligible_operation` in the Prepared metadata builder
requires a successful image-renaming or previous metadata operation; structured
import alone is not eligible. A pure no-renaming authoring path for every existing
missing-shared-JSON tree is therefore not established by this assessment. Preserve
original names and source folders; separately verify/approve a review-only folder
classification and missing-JSON authoring entry rather than forcing renaming or
inventing metadata. This does not block publishing already valid/scanned products.

`scan_runner.py` plans scope/locks/history; `scanner.py` resolves shared metadata,
children, modifiers and images; `sku_manager.py` retains marker-based identity and
atomic indexes. `ingest.py` projects complete parent units transactionally into
Collection/Product/Variation, attributes, images, assets and taxonomy memberships.
`product_info.json` stays authoritative; SQLite is projection/identity/operations.
`commerce.py` freezes authored and projected state for Shopify. Shopify draft
publishing reads authorized original bytes, not generated WebP output URLs.

### Evidence-backed optimisation candidates, not implementation

1. `ingest_rows_to_db` always runs `_scan_sku_folder_index` (full `os.walk`) even
   with `source_folders`; `_source_context` is then called for every indexed SKU,
   reloading the same collection JSON repeatedly. First candidate: operation-local
   source/context cache for affected parents, with proven fallback parity.
2. `catalogue_snapshot` calls the authored resolver, then lazily traverses ORM
   attributes/images/children. Future batch publishing should eager-load bounded
   parent scopes once; M3A deliberately handles one parent, so no current query
   storm or measured timing is asserted.
3. SKU index load/save occurs during variation allocation. This is deliberate
   durable identity behaviour, not permission to batch counters. Image byte hashes
   are reread at Confirm intentionally for source freshness; do not cache across
   that boundary. No measured startup bottleneck was established.

Baselines before scanner optimisation: `test_scanner_contract_characterization`,
`test_phase0_scanner_output_parity`, `test_scanner_resolution`,
`test_m52_scan_ingest_regression`, `test_transactional_ingest`,
`test_ingest_mapping`, `test_reconstruction`, `test_single_variable_parent_images`,
`test_source_image_resolution`, `test_product_info_contract` and relevant Intake
tests. Select cases proving same IDs/SKUs/combinations/modifiers/inheritance/images,
scope selection and marker/database recovery; older Phase 0 discrepancy tests
are historical characterisation, not a mandate to restore obsolete durability.

Detailed discovery, skipped/unchanged, new/updated, missing/invalid JSON, SKU,
modifier matches, variation, warnings and completion logs must survive. Preserve
`utils/discord.py`, existing webhook names and bounded non-fatal delivery,
`ingest.notify_ingest_product`, metadata/Intake events and operation terminal hooks.
Woo-specific `notify_woo_publish_*` formatters/callers can retire only after shared
Discord infrastructure is retained. Avoid duplicate generic/specialized terminal
messages. New Shopify/order/proof notification types are deferred.

### Field gaps and minimum backwards-compatible authoring

| Information | Existing authority / M3A status | Planned boundary |
| --- | --- | --- |
| Title, description, SKU, regular price | Authored/inherited + projection; M3A sends and verifies these | Preserve resolver; review HTML/short-description display policy, do not concatenate silently |
| Weight, dimensions | Existing authored grams/mm; projected parent/modified variants; M3A omits them | Variant InventoryItem weight; typed dimensions metafields, not assumed parcel dimensions |
| Sale price/dates | Existing authored/projected fields; M3A omits them | Reviewed price/compare-at policy; scheduled sales separately |
| Origin, requires shipping, vendor | Not recognised canonical authored fields | Optional `country_of_origin`, `requires_shipping`, `vendor`; collection defaults/overrides; reviewed configurable GB origin only when accurate |
| SEO title/description | Existing `meta_title`, `meta_description`; M3A omits | Shopify SEO fields; optional authored `handle` separate from existing correlation handle/recovery |
| Category, tags, collections | Existing local categories/tags/`storefront_collections`; M3A omits | Explicit standard-category mapping and operational collection identities; never convert Dashboard Collection into remote membership implicitly |
| Images, alt/order | M3A uploads original PNG/JPEG/WebP, retains actual IDs/URLs and verifies order/alt | Live proved one PNG, not multi-image or WebP cases; variant media deferred |
| Inventory | Existing projected defaults are not reviewed Shopify stock/location policy | Separate tracked/backorder/location decision; do not publish invented stock |
| Personalisation/photos/proof/recipe | No canonical typed authoring/publishing contract established | Later optional `personalisation`, `proof_required`, `production_recipe` definitions after separate schema approval; no bulk JSON rewrite |

No authored fields are added by this assessment. Existing optional overrides and
units remain; do not require every new field on every product. Update the existing
schemas/inventory/editor only for approved fields. All remote IDs, upload receipts,
store identity and verification evidence stay in operational storage. Product
dimensions are not proof of packaging dimensions or carrier eligibility.

### Product types and customisation

Simple maps to one Shopify product/default variant. Variable Collection maps each
existing parent to its Shopify product, not the filesystem collection to a single
product. Single Variable maps its existing root parent/child projection directly.
Keep child SKUs and local source identities; map remote variant IDs separately.
Only explicit variation drivers produce options; informational attributes become
typed metadata/taxonomy, not additional combinations. Absence of
`variation_attributes` retains current legacy resolution; `[]` stays meaningful.

Shopify currently supports up to three options and normally 2,048 variants per
product; read actual Shop resource limits before planning writes. The known 2×7
14-child projection fits. The local proposal UI's 1,000-child bound is a separate
current workflow limit. Never silently truncate larger local products. Preserve
exact/most-specific modifier price, weight and dimensions; the characterised
modifier sale-price emission discrepancy needs its own approved decision before
claiming sale-modifier parity. M3A.4 extends the immutable snapshot with projected
variant weight/dimensions without re-running the scanner.

Names/age/messages/dropdowns/checkboxes should normally be storefront form inputs
and line-item properties, not variants. Photo uploads need a verified private file
handling/retention contract; arbitrary uploaded URLs are not production evidence.
Conditional/required input validation and proof consent require theme/server work.
Properties do not authorize additional charges. Paid customisation needs real
priced variants/add-on line items and checkout validation, not a client-set price.
Basic-plan custom apps cannot assume Shopify Functions availability (custom apps
containing Functions require Plus). Do not buy an app or implement that feature now.

### Taxonomy, completeness and controlled updates

The mounted `TAXONOMY_ROOT/registry.json` remains the sole vocabulary/hierarchy:
stable keys, parent keys, aliases, order, category paths, storefront collections,
attributes and scoped terms. Keep its reviewed atomic writer. Local categories can
drive reviewed Shopify collections and nested menu items; Shopify collections
are flat membership resources. Shopify's standard product category is a separate
Shopify taxonomy selection mapped explicitly to existing local intent, not an
automatic match to local category names. Proposed standard-category mapping should
extend the current authority, never create a competing manually maintained file.
Keep remote collection/menu IDs outside JSON; menu eligibility/empty-collection
filtering belongs in a future reviewed navigation plan. Tags remain readable tags.

M3A creates only; existing identities block another Create. An update must read
the retained Shop/product/default-variant identity, compare current managed fields,
show signed scoped field diff, revalidate source/config/remote freshness at Confirm,
record pending operation evidence before mutation, update by retained ID and read
back the complete approved contract. Never create-on-missing, delete-on-failure,
blindly retry uncertain writes or reupload unchanged verified media. Preserve
unmanaged remote fields, stock and sales-channel publication. Local page rendering
should remain immediate; bounded explicit/incremental reads supply remote evidence.

**Single next milestone recommendation: Simple shipping-ready draft + reviewed
update**, one parent per operation, DRAFT only. Reuse existing weight/dimensions;
approve minimal optional shipping/origin fields with inherited defaults, explicit
GB review, typed dimensions metafields and full readback. Update the live retained
draft without a second Create or image replacement. Add bounded versioned operation
evidence using the existing identity table; stop for approval if a schema change is
genuinely required. No Variables, taxonomy/menu writes, rename, scanner optimisation,
inventory quantity changes or bulk publishing. Confirm API scopes required for the
chosen inventory-item mutation before changing installed app permissions.

Then merchandising/SEO/tags/sale fields; Variables/modifiers/media; reviewed
collections/categories/navigation; customisation; orders/proofs/production;
Shipping labels/fulfillment/tracking. Shopify now documents `shippingLabelPurchase`,
but eligibility, shipping terms, required order/fulfillment scopes and
`buy_shipping_labels` permission must be independently confirmed before that later
financial workflow; it is not included in current `read_products/write_products`.

### Theme, naming and eventual Woo retirement

`custom.css :root` already centralises semantic palette/spacing/radii; common
navigation, cards, filterbars, buttons and statuses reuse it. Remaining fixed RGBA
values in header overlays/focus rings/shadows/Intake/relationship controls and
`#F0BBC2` blocking borders would resist a palette swap. Small future work should
tokenise these semantic derivatives, align legacy SCSS/Bootstrap mappings, and
handle fixed SVG/PNG artwork separately. Keep current appearance; Shopify pages
must reuse shared components. No redesign or vendor CSS rewrite.

Names remain: local `woocommerce-dashboard`, GitHub `NEprod/woocommerce-dashboard`,
Docker `neprod/woocommerce-dashboard`, Compose service `app`/container
`woocommerce-dashboard`, Unraid `my-woocommerce-dashboard.xml`, appdata
`/mnt/user/appdata/woocommerce-dashboard/instance`, template/image/support/raw URLs,
brand filenames/page titles/footer, Discord default name and branding assertions.
No tracked CI workflow directory was found; audit any external build automation,
Hub credentials/token repository permissions, repository webhooks, raw asset URLs
and deployment scripts before rename. Local absolute paths may exist in external
IDE/task/build settings; portable catalogue paths must not be rewritten to follow
the repository directory rename.

After shipping-ready Simple acceptance, approve names/availability separately:
`tlc-dashboard`, `NEprod/tlc-dashboard`, `neprod/tlc-dashboard` are proposals only.
First snapshot/backup source, DB and mounts; change visible branding and assertions
in one bounded commit; rename GitHub/update remotes and external integration URLs;
create a new approved Docker repository and publish a unique test artifact while
retaining the old repository/tags; introduce a parallel Unraid template. Keep
existing host appdata/catalogue/output/intake/taxonomy paths, internal container
mounts, port, environment keys and credentials unchanged. Stop old container before
mounting its writable DB in the replacement; never run two writers over that state.
Rename local checkout last with tooling references updated. Test rollback against
backup and prior image; newer schemas may prevent older-image reuse without DB
restore. Do not rely solely on GitHub redirects or delete the previous artifacts.

Eventual removal candidates: `woocommerce_connection.py`, `woo_*` publishing/
preview/live-state/taxonomy/identity services, Woo routes/templates/JS, Woo adapter,
credentials and platform controls. Retain `catalogue_images.py`,
`taxonomy_assignments.py`, registry/writer, metadata resolver, scanner/markers/SKU,
SQLite projection/operations, `utils/discord.py` and source safety. Scanner still
emits Woo-style rows and uses `csv_writer.py`; that is shared catalogue plumbing,
not safe to delete by filename. Capture an immutable recoverable checkpoint before
cleanup. All listed Shopify publishing/customisation/taxonomy/order/proof/
production/shipping/fulfillment gates must pass first. Historical Woo identity and
operation records are not deleted without separate approval.

Official contracts consulted: [InventoryItemInput](https://shopify.dev/docs/api/admin-graphql/latest/input-objects/InventoryItemInput),
[Product input](https://shopify.dev/docs/api/admin-graphql/latest/input-objects/ProductInput),
[variant limits](https://shopify.dev/docs/api/admin-graphql/latest/objects/ProductVariant),
[three options](https://help.shopify.com/en/manual/products/variants/add-variants),
[cart properties](https://shopify.dev/docs/api/ajax/reference/cart),
[Functions plan restrictions](https://shopify.dev/docs/apps/build/functions),
[Shipping label purchase](https://shopify.dev/docs/api/admin-graphql/latest/mutations/shippingLabelPurchase).
These are planning contracts, not evidence of live acceptance for those features.

## Installation and configuration

Choose WooCommerce or Shopify during first administrator creation. An installation
without `instance/commerce.json` defaults to WooCommerce. Connection settings cannot
switch an established installation; activation/migration needs a later reviewed
workflow. No Shopify credentials are required to boot.

Administrators use **Settings → Commerce connection** (`/settings/commerce`) to
enter `shop-name.myshopify.com`, an installed app's **Admin API access token**, and
a pinned API version. Version choices currently cover 2026-01 through 2026-10;
the default is 2026-10. This allowlist needs maintenance as versions retire.
Client secrets and Storefront API tokens are not Admin API credentials. Obtain an
appropriate installed-app token using Shopify's supported authentication flow;
Manual Admin tokens remain operator-managed. M3A.1 additionally supports automatic
Client Credentials acquisition/renewal through Docker/Unraid (below).
Request `read_products` for discovery and `write_products` for M3A.
Connection success reports granted scopes, not readiness for future publishing.

### M3A.2 store domain verification

Connection validation requests the authenticated Shop GID, `myshopifyDomain`,
`primaryDomain.host` and connected `domains.host`. The configured myshopify host
must exactly match one of those authoritative hosts; successful authentication
alone is insufficient. Shopify can retain an original myshopify domain while
another is the storefront primary. The connected-domain list is currently a
deprecated but available GraphQL field in the pinned supported versions; review
its replacement when updating the API allowlist.

New draft mappings use the stable Shop GID to derive their store key. Existing
domain-keyed mappings are found by their persisted Shop GID without rewriting
them, preserving duplicate prevention and recovery across verified aliases.
Reconciliation still rejects a different Shop GID. Changing the configured host
after Preview requires a fresh Preview; signed confirmation remains strict.
No migration or live store mutation is needed for this correction. Live connection
acceptance against the user's two domains remains to be performed in Unraid.

References: [Shop GraphQL contract](https://shopify.dev/docs/api/admin-graphql/latest/objects/Shop)
and [Shopify domain changes](https://help.shopify.com/en/manual/domains/domain-type/change-primary-domain).

**Test connection** never saves. **Test and save** retests before atomic persistence
and leaves the active platform unchanged. A blank token retains the saved token
only for the same domain. Changed settings revisions require reload/review.
Normal authentication, administrator authorization and CSRF protect the route.

Tokens are not redisplayed, logged by this integration or placed in authored JSON.
Saved configuration uses private 0600 `instance/commerce.json`, not encryption at
rest. Protect the instance volume and backups. No extra secret backup is created.
Optional deployment variables are `SHOPIFY_STORE_DOMAIN`,
`SHOPIFY_ADMIN_ACCESS_TOKEN` and `SHOPIFY_API_VERSION`. Any nonempty variable makes
this whole configuration deployment-owned/read-only in the UI; provide a complete
domain/token pair. Compose now explicitly passes all three optional variables
from `.env`; the Unraid template has matching optional fields (token masked).
Blank values preserve Dashboard settings. A partial environment configuration
never borrows saved credentials. Deployment values are not redisplayed on the
connection page; testing is read-only and saving/overwriting them is refused.
Environment configuration does not activate Shopify or change the installation
platform. Credentials remain separate from all product identity records.

## M3A.1 — Client Credentials authentication

Shopify supports this server-side grant for a **Dev Dashboard app installed on a
store in the same Shopify organization as the app**. This fits the intended
TLC-owned app/store deployment only if that organization condition is true;
installation alone is not proof. Live connection acceptance must confirm it.
Apps serving other organizations require a separately scoped authentication flow;
the application does not invent a fallback or OAuth redirect workaround.

In Dev Dashboard create/select the app, configure its version with `read_products`
and `write_products`, release/install the version on your own store, then open
**Settings** for the **Client ID** and **Client Secret**. The Client ID identifies
the app; it is not an access token. An already installed compatible app does not
need reinstalling merely to change TLCDashboard authentication. Scope changes
may require the normal Shopify version/permission approval process.

For Docker/Unraid, set:

```dotenv
SHOPIFY_STORE_DOMAIN=your-shop.myshopify.com
SHOPIFY_CLIENT_ID=<Dev Dashboard Client ID>
SHOPIFY_CLIENT_SECRET=<Dev Dashboard Client Secret>
SHOPIFY_ADMIN_ACCESS_TOKEN=
SHOPIFY_API_VERSION=2026-10
```

Both client fields are masked in the Unraid template. Compose passes them through;
no actual credentials belong in repository files. To switch from manual tokens,
remove/blank the deployment Admin API token, add both client fields and restart
the container. Keep appdata and the selected platform unchanged. For manual-token
mode, blank both client fields and provide domain/token. Both modes present,
partial client pairs, invalid domains or missing deployment credentials are
rejected; no values are combined with saved Dashboard configuration. With all
Shopify environment variables absent/blank, protected manual Dashboard settings
continue to work. Deployment-owned settings can be tested but never overwritten
or redisplayed by the Dashboard.

The existing Shopify transport posts form-encoded `grant_type=client_credentials`,
Client ID and Client Secret to the validated store's `/admin/oauth/access_token`.
It validates `access_token`, `expires_in` and `scope` from Shopify. Tokens live only
in a process-private bounded cache (16 credential/store keys) and are renewed on
demand up to 60 seconds before returned expiry. Short lifetimes use a proportionate
margin. No refresh token is required: renewal repeats the documented grant.
A lock coalesces simultaneous acquisition; failed authentication is held for
30 seconds to avoid storms. Restart discards cached tokens and reacquires on the
next Shopify request; no background scheduler or token persistence is introduced.

The token is attached only to Admin GraphQL requests. Staged binary uploads still
use Shopify's signed upload parameters without Admin tokens/client secrets.
Preview fingerprints use stable credentials, not acquired tokens: normal renewal
does not invalidate a review, but changed credentials still require fresh Preview.
No short-lived tokens are stored in authored JSON, identities, operation evidence
or configuration files. Errors are generic/redacted, responses bounded to 64 KiB
for authentication, connect/read timeouts 5/15 seconds, redirects/proxy inheritance
disabled. Authentication requests are never automatically retried. A GraphQL 401
invalidates the cached token and starts the cooldown; it does **not** replay the
failed read/write. An uncertain draft remains subject to existing reconciliation.

Open **Settings → Commerce connection → Test connection**. This obtains/caches a
token and reads shop identity/scopes; it does not publish. On failure check app
installation, same-organization ownership, credentials, granted scopes and
connectivity. Correct the environment/restart or wait out the cooldown before
another explicit test. Client Credentials are not universal merchant OAuth;
multi-worker deployments have separate caches, while the supported container
continues to use one worker with four threads. Docker administrators can inspect
environment values, so secure container access and backups.

Official contracts: [Client Credentials Grant](https://shopify.dev/docs/apps/build/authentication-authorization/client-credentials-grant),
[access tokens](https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens).

## M2 discovery — implementation awaiting live acceptance

M1 was checkpointed locally at `e2b454bb2a3eef85475fb635a3d85e03576a36dc`.
M2 was checkpointed locally at `9b01d63ca4bdbc3aa175e2112983c9031486da5a`;
neither checkpoint was pushed/released. M3A is source checkpointed locally;
temporary commit-tagged AMD64 image acceptance is separate from release approval.

Open **Settings → Commerce connection → Inspect Shopify catalogue**, or
`/shopify/discovery`. Shopify-selected installations also have a Discovery sidebar
destination. Administrators can inspect a prepared Shopify connection while the
installation remains WooCommerce. GET renders the shell without a remote call;
each CSRF-protected button makes one fixed read query. Test connection, Retrieve
products and Retrieve collections use the same adapter/transport as M1.

Products expose Shopify GID, title, handle, status, plain/escaped HTML descriptions
and option names/values. Inspect a product for variants (GIDs, SKU, price,
compare-at price, selected options and position) and media. Inspect variant media
for its associations. These are remote observations, not local identities or a
promise that matching SKUs authorize linking. No database mapping/import occurs.

Each list/detail connection retrieves at most 20 records per action. Product
detail shows 20 variants and 20 media; each variant initially shows up to five
media IDs and flags further associations. Separate **Next variants**, **Next
product media**, **Inspect variant media / Next variant media**, and **Next
members** controls expose additional pages without hidden fan-out. Pagination
evidence must be complete/advance; missing evidence is not an empty catalogue.
No automatic exhaustive traversal, background refresh or unbounded accumulation.

Media exposes Shopify media GID, content type, processing status, and for images:
file status, MIME type, image ID, actual CDN URL, dimensions, alt text and original
source URL when Shopify returns it. Processing images can have no URL yet. Order
is the returned connection order within the page, with the Shopify cursor—not
an invented absolute position on later pages. Original-source URLs can be signed
and short-lived. Nothing is fetched from those URLs; they are inspection links.

The current MediaImage/MediaImageOriginalSource contract has no separate original
filename field. A decoded URL basename is displayed only as a **hint**, never
proof of the original upload or a local filename. Local path/name/extension
remain unmapped. Shopify may optimize delivery independently of file extension;
the code never substitutes `.webp`, strips URL parameters or reconstructs URLs.
It never downloads, converts, renames, uploads or modifies local image metadata.

Collections expose GID/title/handle/description/sort order and paged product
membership. The still-supported legacy `ruleSet` field identifies automated
legacy rules when present; absent rules are labelled honestly rather than assumed
to prove a manual type in the evolving collection-source contract. Modern source
classification is deferred. No parent/child category tree is inferred. Shopify
menu navigation is a separate API and is not queried here; the local registry
continues to define intended storefront hierarchy. No registry mutation occurs.

`read_products` is required for product, variant, product-media and collection
reads (a granted write_products scope also permits reads, but this milestone
does not need it). No write, inventory, order or navigation scopes are requested.
Live acceptance requires the user's installed-app Admin token, domain, pinned
version and populated/empty-store read tests; mocked tests are not that evidence.

Official discovery contracts:
[MediaImage](https://shopify.dev/docs/api/admin-graphql/latest/objects/MediaImage),
[original source](https://shopify.dev/docs/api/admin-graphql/latest/objects/MediaImageOriginalSource),
[ProductVariant](https://shopify.dev/docs/api/admin-graphql/latest/objects/ProductVariant),
[Collection](https://shopify.dev/docs/api/admin-graphql/latest/objects/Collection).

## Network and safety boundary

Discovery accepts only allowlisted fixed GraphQL **queries**; its HTTPS POST
contains no GraphQL mutation. The separate M3A confirmation service permits only
stagedUploadsCreate, productCreate and productVariantsBulkUpdate. Canonical
myshopify domains only, no redirects, inherited
proxy/netrc disabled, connect/read timeouts 5/15 seconds, response limit 64 KiB for
connection and 1 MiB for discovery, with bounded response iteration. No automatic
retries: HTTP 429 and GraphQL THROTTLED stop with a wait/manual-retry message,
discarding partial evidence rather than looping or hammering the query budget.
Authentication, timeout, HTTP,
rate-limit, malformed evidence and version/domain mismatch errors are redacted and
actionable. Discovery cannot write. M3A does not write inventory quantities,
collections, orders, menus or sales-channel publications.

Woo adapters retain the current publisher path. Shopify-selected installations
guard Woo workflows instead of invoking them with Shopify credentials. Preparing
Shopify settings on Woo does not change existing mappings or publishing.

The recursively immutable local catalogue snapshot reuses resolved filesystem
metadata and projected rows. It preserves local product/child identities, SKU,
type, pricing, image and taxonomy references. It performs no remote reads, source
writes, scanner execution, SKU allocation or ORM autoflush. M3A remote platform
IDs have separate mappings, never authored JSON.

## M3A — reviewed single Simple draft, awaiting live acceptance

Open **Settings → Commerce connection → Review one Simple Shopify draft**, or
`/shopify/drafts`. This administrator-only proof of concept may use prepared
Shopify credentials while Woo remains active. Page GETs make no remote calls.
Preview reads the immutable local snapshot/original images and verifies Shopify
store identity/scopes using reads only. It shows local identity/SKU, description,
price (in Shopify store currency), DRAFT status and ordered original filenames,
extensions, references, sizes and alt text. All projected parent images are
included; there is no image-subset editor in M3A.

Confirmation requires CSRF, explicit acknowledgement and a signed 15-minute
Preview. Source/projection, original byte hashes, credentials and store identity
are rechecked. The existing CatalogueOperation lock/history is reused. A durable
pending Shopify identity is committed **before the first mutation**, and a
create-attempt marker is committed before productCreate. Preview alone never
reserves identities or uploads anything.

Approved migration `0010_shopify_product_identity` follows `0009_woo_live_state`
and adds only `shopify_product_identity`. It stores verified store GID/key/domain,
local product/source identity/SKU, nullable Shopify product GID, operation reference,
reviewed digest, state/verification state and timestamps. SQLite enforces unique
store/local-product, store/source and store/remote-product pairs. Version-1 media
evidence is bounded to 64 KiB in the writer and database; it retains reviewed
local references/hashes, returned media IDs/URLs, processing observations and
recovery stage/error. No credentials or signed upload targets are stored there.
No backfill; existing Woo mappings are untouched. Nullable SET NULL references
preserve the identity reservation if local projection/history rows are removed.

Original supported PNG, JPEG and WebP bytes are read only from authorized catalogue
paths, checked for format/extension consistency, then sent through Shopify staged
multipart upload targets without the Admin token. There is no local conversion,
rename, download or source write. Bounds: one active Simple parent, no child rows,
one to six images, 10 MiB each / 30 MiB total, title 255 characters and description
8,000 characters (also subject to the evidence bound). Signed targets are not
retained. Product creation associates media in reviewed order with reviewed alt
text; the sole default variant then receives SKU/price. A unique operation handle
and correlation metafield distinguish this attempt from arbitrary matching SKUs.

Independent product readback verifies store/product correlation, DRAFT status,
title/description, sole variant SKU/price, media IDs/order/alt and processing state.
Actual returned Shopify CDN URLs are stored unchanged; an upload extension is not
a promise about browser delivery format. READY media with returned URLs yields
verified; asynchronous processing remains partial/processing until explicit read
reconciliation. Strict description comparison may require inspection if Shopify
normalizes submitted HTML; it never silently accepts a mismatch.

Official write contracts:
[stagedUploadsCreate](https://shopify.dev/docs/api/admin-graphql/latest/mutations/stagedUploadsCreate),
[productCreate](https://shopify.dev/docs/api/admin-graphql/latest/mutations/productCreate),
[productVariantsBulkUpdate](https://shopify.dev/docs/api/admin-graphql/latest/mutations/productVariantsBulkUpdate).

### Partial failures and recovery

Known/uncertain creation permanently blocks another create through this workflow,
including after restart or an empty search. There is no automatic write retry,
write-resume, rollback or remote deletion. Failures before productCreate was even
attempted permit a new reviewed operation; orphan staged uploads are not deleted.
Startup marks interrupted operations using the existing recovery contract; durable
Shopify identity reservations survive.

Open the retained identity page and **Reconcile using Shopify reads only**. Known
IDs are independently read. Unknown IDs use the exact unique handle or an explicit
candidate product GID, then require the operation correlation. If creation timed
out before media receipts were retained, the operator must inspect the observed
IDs/URLs/order and explicitly approve that mapping; signed fresh remote/local
digests are checked before adoption. Missing/wrong fields, ambiguous candidates,
failed media or incomplete evidence stay blocked. Correct the retained draft in
Shopify Admin where needed, then reconcile. Historical CatalogueOperation results
are not rewritten by reconciliation; current identity verification is separate.

### Manual acceptance — one real TLC product

1. Back up the persistent app database/instance volume before upgrading to 0010.
   Configure the installed app's Admin token with `write_products` (which also
   permits product reads), domain and supported version through Docker/Unraid or
   protected settings. Test connection; do not change the active platform.
2. Choose one scanned active Simple product with stable SKU, valid price and one
   to six original PNG/JPEG/WebP parent images on the readable catalogue mount.
   Record its local metadata/image hashes and current Woo mapping if applicable.
3. Build Preview at `/shopify/drafts`; review fields, filenames/order/alt and
   price in the store currency. Confirm once with the explicit acknowledgement.
4. Inspect the retained identity/operation and Shopify Admin: one unpublished
   DRAFT, correct SKU/price, original gallery order/alt. Do not publish to channels.
   Use read-only reconciliation if images are still processing.
5. Check verified product/media GIDs and actual returned URLs, unchanged source
   files/SKU/Woo identities, and that a second create is refused. For failures,
   follow the retained identity instructions rather than pressing Create again.

Categories/collections/navigation, controlled attributes, tags, relationships,
inventory quantities, Variable products, updates to existing Shopify products and
full synchronization are deliberately omitted. No extra dependency was added.

## Verification and next milestone

Focused tests use fictional stores/mocked transport, including setup persistence,
credential protection, transport failures, immutable snapshots and targeted Woo
compatibility. Real Shopify connectivity remains unverified until the user supplies
their own installed-app credentials and performs the read-only connection test.

Next: live acceptance of M3A's single Simple draft, then a separately scoped
reviewed update/variant publishing milestone; do not reuse Woo IDs or reconstruct
image URLs. Deferred:
inventory, orders, navigation editing/sync,
customisation fields, variant-media mapping, webhooks, merchant OAuth and reviewed
platform switching. Client Credentials renewal is implemented in M3A.1; other
merchant OAuth/token lifecycle flows remain deferred. Only the approved M3A
identity migration is introduced; M3A.1 adds no migration.

Official contracts: [GraphQL Admin API](https://shopify.dev/docs/api/admin-graphql),
[API versioning](https://shopify.dev/docs/api/usage/versioning),
[access scopes](https://shopify.dev/docs/api/usage/access-scopes), and
[authentication](https://shopify.dev/docs/apps/build/authentication-authorization).
