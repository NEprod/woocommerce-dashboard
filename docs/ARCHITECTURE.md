# Existing Architecture

## M3A.8.5.2 — durable acceptance and complete read evidence

The route validates admin/CSRF and the signed 900-second product review, reserves and
commits the existing operation lease before slow revalidation, then passes that request-
scoped lease through existing Simple/Variable/inventory writers. Pending product/resource
reservations and stage commits still precede mutations. There is no new worker/queue,
second API client, migration or write retry. SQLite is not held in a write transaction
across network calls. A finally-path marks unfinished accepted operations failed before
product writes or recovery-required afterward. Startup interruption handling is unchanged.
Read-only receipts match the durable review hash directly in JSON scope; reading approval
outlives execution approval (24 hours versus 15 minutes). Expired receipts use History.

The modal reads server evidence and tracks a single confirmation. DOM named controls cannot
choose the confirmation destination: the URL comes from `getAttribute('action')`, verified
same-origin. Closing aborts polling only; saved tickets reconnect to read receipts, not writes.
Database-busy/missing-receipt states never mean a failed Shopify mutation. Final full verified
identity plus succeeded operation is required for the completed outcome; scoped/partial
proof and native uncertainty remain authoritative.

`ShopifyAdapter.read_variable` assembles at most four cursor pages within sixty seconds,
preserving the thirty-two-variant contract and checking unchanged parent evidence. Only
complete nodes reach canonical identity/field/media comparison. The status route includes
both supported parent types; fresh cache/revision/related-field logic is shared. Observation
failures classify retained missing variants separately from identity conflicts and unavailable
reads; no observation silently repairs/adopts/recreates a retained remote identity.

The same transport records bounded safe request/cost diagnostics. Only explicit read
THROTTLED/HTTP429 gets up to two retries, at most three seconds total waiting; connection
tests and mutations remain single-attempt. Fixed native contracts alone use 2027-01.
Attribute readback proves definition GIDs, exact PRODUCT owner/app namespace, actual lists
on the requested product and both storefront definition access and optional publishable
status. The standalone diagnostic loads configuration without normal application startup
and opens the existing SQLite database with mode=ro; it cannot persist reconciliation.

## M3A.8.5.1 — complete evidence and scoped publishing feedback

Large complete publishing evidence uses a lossless versioned zlib/hash/size envelope
under the existing 64 KiB identity column bound and bounded 1 MiB expansion; legacy
JSON remains supported. Comparison indexes hash oversized values without becoming
write authority. Explicit inventory-only review retains the full old plan and separate
scoped receipt, never advances full verification. Confirm's local receipt ticket is
stored in CatalogueOperation scope; progress/reconnection reads durable stages, and
repeated receipt submissions cannot launch a second writer. Synchronous publishing,
signed source/remote checks and recovery leases remain authoritative. No new migration.
Existing owned attribute access updates are narrow signed writes, not term/definition
replacement; independent identity/schema/access readback is required. The diagnostic
CLI deliberately opens SQLite read-only and avoids normal application startup.

## M3A.8.5 — first-use resources and exact remote lifecycle

The existing ShopifyAdapter transports new fixed 2026-10 resource, CAS assignment,
publication-read and productDelete contracts. Existing auth/redaction/timeouts/no-retry
and the separate native 2027-01 allowlist are unchanged. Shared field mapping now
manages tracked/inventoryPolicy explicitly while never emitting quantities/locations.

`shopify_taxonomy.register_required` runs inside the signed product operation lease,
reuses registry sources, canonical candidate review and collection validation, and
reserves ShopifyCollectionIdentity before mutation. Planned missing collections are
signed as Create & Join; actual GIDs are retained and memberships independently read.
No similarly named candidate is silently adopted, and no remote-only membership is removed.

`shopify_attributes` resolves only effective informational assignments. Existing variant
drivers remain options. Deterministic app-owned type/handle/field keys and TLC identity
fields anchor controlled metaobjects; product lists reference verified Metaobject GIDs.
SQLite ShopifyAttributeIdentity (0012) enforces store/source and store/remote uniqueness,
16 KiB versioned resource evidence and operation references. No authoritative tree is
duplicated. Registration/assignment stages persist before writes; metafieldsSet uses
compareDigest CAS. Exact readback checks types, definition IDs, term identity and lists.
Registry/source/resource changes invalidate reusable comparison; Products GET remains
local and resource mappings load in one bounded query rather than per-product reads.

Explicit recovery distinguishes unresolved registration, known noncreation and verified
base work with remaining informational stages. Only successful exact owned reads can
resolve a resource; absence requires two reads and no former known GID. Remaining base
fields/media/memberships are independently verified before a new signed Update is offered.
No old write plan is automatically replayed, and base_verified is not full publish proof.

`shopify_navigation` derives paths/usage from the same registry and source assignments.
Explicit browse roles and within-attribute parents do not change variant generation.
An optional primary_category must reference a directly assigned active category. Offline
proposals count at most 500 local parents; fresh exact Online Store evidence requires
ACTIVE plus publishedOnPublication under configured Publication GID. Explicit bounded
read checks persist comparison evidence, never write menus or activate storefront filters.

`shopify_lifecycle` is a separate signed destructive authorisation. Fresh verified store,
exact parent/correlation and child identities precede productDelete; two authenticated
null reads establish absence. Complete old evidence is archived in permanently protected
lifecycle operations before the reusable parent reservation becomes remote_deleted.
Old child GIDs disappear from active evidence, not history. Fresh Create reuses local
source/SKU reservation but performs duplicate checks and obtains new remote IDs. Transport
failures and archived/unpublished states do not clear identities. No local deletion occurs.

## M3A.8.4 — bounded variable stages in the existing publisher

`shopify_variables.py` is a helper of existing `shopify_drafts` Preview/Confirm/
reconcile, not another API client or catalogue. Immutable snapshots expose scanned
children, stable source identities, effective prices/measurements and original image
diagnostics. Pure existing scanner calculation functions detect stale ingestion before
Review; no scan, allocation or authored write occurs. Only unordered category/tag sets
are canonicalised in variable fingerprints; ordered option drivers remain ordered.

The same signed 900-second review, CSRF/admin guards, store identity, configuration/
source/remote/taxonomy digests and CatalogueOperation lease protect one parent operation.
SQLite parent uniqueness reserves a pending identity before uploads/Create. Bounded
v1 evidence adds `kind`, `variant_maps`, `option_maps`, `variant_creates_attempted` and
`variant_native`; no table or migration is added. Maps retain actual ProductVariant,
InventoryItem, ProductOption/Value and MediaImage GIDs, source/SKU/selected options,
stage/operation/timestamps and original-byte hashes. Preflight reserves room within
64 KiB for final/uncertain receipts, with 32 total variants and 20 source images maximum.

ProductCreate supplies reviewed options/DRAFT/correlation and original staged media.
The initial variant is independently checked and updated, never deleted. Subsequent
bulk Create uses PRESERVE_STANDALONE_VARIANT; bulk Update has allowPartialUpdates false
and only reviewed changed fields. Remote-only variants/memberships stay untouched.
Owned variant/media/field/membership verification is independent. Partial evidence and
historical operations survive; explicit recovery reads the original retained plan,
never retries writes. Missing media receipts require the existing signed gallery
adoption review. Absent unfinished variants can require manual completion of original
reviewed stages before verification; absence is not permission to recreate the parent.

Normal parent/variant/media/merchandising queries use configured 2026-10. Only fixed
packed capability, correlated variant/inventory reads and inventory measurement writes
use 2027-01 RC through existing auth/transport. Each actual InventoryItem has separate
pending-before-write and independent observed packed proof. Native freshness is ten
minutes even with unchanged Product.updatedAt. Known native restrictions preserve base
verification; uncertainty blocks further writes. Products GET remains local-only;
existing incremental status requests allow 25 parents/two full comparisons per step.

## M3A.8.3 — independent native dimensions and operation finalisation

`finalize_processing` only accepts known asynchronous Processing identities, checks
the original operation is not still writing, and uses existing full retained-plan
readback. Automatic refresh counts this against its bounded full-read budget and
coalesces repeated checks for 30 seconds. Genuine uncertain/pending writes are never
automatically recovered. Preview describes unfinished media rather than inventing recovery.

Override editor GET is source-read-only. Product save supports a validated missing
product-owned override at the established source folder, atomically writes only changes,
then registers ProductAsset and follows existing targeted-update orchestration. Empty
new drafts/identical documents are no-ops. No source folder creation or shared-file
overwrite is allowed through the new product override path.

`shopify_native_dimensions` owns an optional reviewed measurement sub-contract, not
another catalogue/publisher/framework. Fixed capability/read/write queries share the
same ShopifyAdapter transport/auth cache; only these can override the endpoint to
2027-01. The global configured version remains stable. Schema/scopes and correlated
product/variant/inventory IDs must be established before the single native mutation.
The input contains measurement.packedDimensions only. Durable bounded v1 evidence
under identity.native_dimensions records pending-before-write, blocked/verified/uncertain
state, original reviewed dimensions, IDs, timestamps and operation reference. Native
operations share the existing CatalogueOperation lock; restart never authorizes replay.
Reconciliation is read-only, compares the original native plan and retains history.

Native cm values are Decimal-derived from authored mm only under explicit deployment
policy; no package values are inferred by default. Native freshness is ten minutes and
independent of Product.updatedAt. Subset success only rebinds identity/native evidence
and removes proven native differences in an existing comparison; it does not renew full
metadata/media/membership timestamps. Normal publication blockers remain authoritative.

## M3A.8.2 acceptance corrections

`shopify_simple_fields.dimension_mm` normalises canonical/legacy dimension metafield
units to Decimal millimetres (0.000001 mm tolerance); new writes emit `millimeters`.
It is independent of gram weight mapping and writes no package dimensions. Retained
plans are not rewritten. `apply_simple_readback` saves trusted identity/media observations
before parsing fields, checkpoints fields/memberships/media independently, and advances
`verified_at` only after complete evidence. Missing receipts require explicit signed
current-local and retained-remote digests and identical original gallery before read-only
adoption. Recovery never resumes mutations or rewrites historical operations.

`taxonomy_routes.workspace_context` provides one offline registry/mapping presentation
for `/taxonomy` and retained `/taxonomy/shopify` workflows. Shared panels/semantic CSS
reuse reviewed writers, not another taxonomy. Legacy product plans lacking the managed
contract are excluded from batch subset review with individual Review guidance. Keys
and cursor are checked before connection reads; 20-group/25-product bounds remain.

## M3A.8.1 incremental reconciliation and taxonomy execution

`shopify_status.py` reuses the existing adapter and canonical read-only Preview.
POST `/api/shopify/status` is administrator/CSRF-protected; GET pages stay local.
The existing catalogue-operation lease serializes reads against writes/scans, and
persisted freshness/backoff prevents repeated work across tabs. Requests contain
at most 25 visible IDs or one ordered Refresh All page, with two full comparisons
per step. Browser steps are finite and update existing cards without a framework.
The production single-worker runtime uses existing operation locking; no new queue,
worker or independent sync database. Closing navigation cancels further client steps;
an already-running bounded server request may finish safely.

Comparison JSON distinguishes full comparison time, product revision observed/check
time, related membership check time, cached absence, previous equality and failures.
Successful revision checks do not advance full comparison/post-write timestamps.
Local resolved content fingerprints, mapping/identity evidence and configuration
invalidate equality. Related memberships are re-read independently; media/inventory
require complete comparison at most 30 minutes apart. Publication remains explicitly
unverified/deferred. A null retained node means missing/inaccessible, not permission
to Create. Read errors cannot clear uncertain publishing recovery.

`shopify_taxonomy_batch.py` derives registry ancestors without modifying authored
references. Catalogue Collection rows are excluded from storefront source discovery.
Existing folder mappings are retained but no longer contribute managed memberships.
An explicit proposal uses existing CatalogueOperation scope (version 1, 128 KiB max)
with signed child reviews and bounded product pages. No new migration. Approved batch
steps use durable claimed/in-flight state and serialized execution; each child has
its own operation/identity/readback. Fresh GET history only offers explicit Resume.
Expired/changed approvals require a new review, preserving completed results.

Taxonomy-only product attempts reserve durable evidence before tag/membership writes,
preserve IDs/media/publication/metadata, and verify only the approved subset. Genuine
uncertainty blocks further writes and is reconciled read-only through the existing
identity UI. Full product verified_at is not changed by subset verification; subsequent
canonical comparison establishes complete supported sync. Candidate conflicts, inactive
ancestors, unverified products and unavailable scopes are visible exclusions. No remote
rollback, membership removal, arbitrary linking, automatic rename or menu publishing.

## M3A.8 operational comparisons and Shopify collection mappings

`shopify_comparison.py` stores a bounded version-1 summary in
`shopify_product_comparison`, unique by verified Shop GID + stable source path.
It records read comparison time separately from `ShopifyProductIdentity.verified_at`.
Products loads identity/comparison/mapping rows in batches, makes no Shopify requests,
and revalidates effective managed source, authorized image stats, relevant taxonomy
definitions, mapping evidence, identity evidence and configuration fingerprints.
Ten-minute freshness is informational, never write authorization. Source changes
during reads cannot inherit equality from the older reviewed plan. Signed Preview
still hashes bytes, rereads remote state and binds collection actions to confirmation.
Comparison summaries retain the mapping fingerprint captured before remote reads,
not whatever mapping is current when those reads finish; concurrent mapping edits
invalidate equality rather than borrowing evidence from an older mapping.

`shopify_taxonomy.py` projects the existing registry's active category and storefront
collection keys, plus catalogue Collection.source_relpath. Its operational
`shopify_collection_identity` mappings are store/source and store/remote unique;
neither the registry nor authored JSON acquires remote IDs. Optional validated
standard taxonomy IDs belong to these mappings, not a replacement TLC category tree.
Mapping Review is read-only; signed/CSRF Confirm is serialized by the existing
CatalogueOperation lock. Create reserves a durable pending row, stamps a correlation
metafield and retains returned IDs before independent readback. Uncertain Create
requires explicit candidate + matching attempt marker. Link never adopts a title
match and failed read-only Link leaves no create reservation. Operation history remains.

Simple product Preview reads each desired mapped manual collection and verifies
membership. Sequential Join writes are durably staged; existing/remote-only
memberships are preserved. Verification checks all intended memberships before
claiming complete success. An interrupted join can resolve through independent
read-only reconciliation; genuinely missing membership remains recovery-required,
not blindly resumed. Rules/shared/variant/subcollection sources are unsupported.
Migration 0011 adds only these operational tables, with bounded JSON and no backfill;
scanner/JSON/SKU/marker/Discord and existing product mappings are unchanged.

## Shopify-first normal Products presentation

`shopify_products.py` adapts local Shopify identity/evidence for the existing paged
Products browser and detail/dashboard views. Identity reads are batched; page GETs
never perform remote discovery. A local metadata/authorized image-stat fingerprint
is carried in existing versioned, bounded reviewed-plan evidence, not source JSON.
It detects local changes without rehashing every image during list rendering.
Signed Preview still hashes bytes and independently reads Shopify; operational
badges are timestamped last-known evidence, not a reviewed write authorization.

Scoped `/products/<id>/shopify/review` calls the existing Simple publisher and uses
the same CSRF/administrator/signed expiry, SQLite reservations, operation lock and
readback/reconciliation protections. Missing identity is not proof of remote absence;
bounded SKU-candidate reads block new creates when remote candidates exist.
Live transitions and bulk execution remain unsupported, not implicitly authorized.

Woo-only publishing/live-state/identity/taxonomy-sync/client services, their legacy
routes/templates/tests, environment settings and historical SQLite evidence remain
temporarily for rollback. Normal navigation and Products no longer expose them.
Do not delete shared metadata_workspace, catalogue_images, taxonomy_assignments,
taxonomy registry/writer, catalogue snapshots, publishing-intent helpers, operation
control/history, scanner/SKU/import utilities, atomic writers or Discord delivery
merely because they currently import or coexist with Woo-named code. Retire those
dependencies through a separate reviewed cleanup after the Shopify replacement
workflows are live accepted. Historical records must not be silently deleted.

## TLC-only transition policy — 2026-10-12

TLCDashboard targets Shopify permanently for Tender Loving Creations. The small
existing adapter/configuration boundary remains implemented compatibility, not a
mandate for a permanent dual-platform system. Do not extend switching, generic
plugins or multi-business abstractions. Woo stays operational until an independently
approved cleanup; retain filesystem catalogue, scanner/SKU/recovery, taxonomy,
image ownership, operation history, logging and Discord independently of Woo.
Remote product/media/collection/menu identities remain operational SQLite state,
never authored JSON. See [transition assessment](SHOPIFY_INTEGRATION.md#tlcdashboard-transition-assessment-2026-10-12).

## Phase 4 M1 platform boundary

`commerce.py` selects a small WooCommerce/Shopify adapter. Existing Woo publishing
continues through its proven services, not a new generic publisher. Shopify
connection/discovery queries use `shopify_connection.py`; authenticated
settings routes live in `commerce_routes.py`. Shopify installations cannot execute
Woo workflow routes. Catalogue/scanner/SKU/taxonomy authority is unchanged.

`commerce_config.py` owns version-1 `instance/commerce.json`: active platform plus
optional Shopify configuration. Missing settings mean WooCommerce; invalid
settings never silently fall back. Only first-admin setup chooses the platform.
Directory locking, the existing same-directory atomic writer, private 0600 files,
symlink rejection and revision checks protect saves. Tokens are plaintext in
private storage, excluded from browser redisplay; protect volume access/backups.
Deployment-provided Shopify variables override as a complete configuration unit,
never combining a saved token with an environment domain. No database migration.

`CatalogueSnapshot` recursively freezes resolved authored metadata, local identity,
SKU/type, projected attributes/variations and image references. It uses existing
metadata inheritance and suppresses ORM autoflush, without writing source or
remote identities. Platform switching and merchant OAuth remain deferred.
M3A.1 adds optional same-organization Shopify Client Credentials to this same
adapter: process-private bounded tokens, expiry-aware renewal, serialized
acquisition and failure cooldown, with manual-token compatibility. Tokens never
enter settings, authored files or operation evidence. Preview fingerprints remain
credential-based rather than acquired-token-based. No migration or publishing
contract changes are needed.

M3A's separate `shopify_drafts.py` implements a signed, 15-minute single Simple
Preview/Confirm using this snapshot and the existing CatalogueOperation lock.
Only explicit confirmation can use the adapter's allowlisted staged-upload,
productCreate and sole-variant SKU/price mutations. Original authorized bytes
are uploaded without source conversion; discovery/reconciliation remain read-only.

Approved migration 0010 adds only ShopifyProductIdentity, independent of Woo:
verified store GID/key/domain, local/source identity, nullable remote GID,
operation, reviewed digest, state/timestamps and bounded version-1 media evidence.
Database store-scoped product/source/remote uniqueness plus committed pending and
create-attempt records prevent duplicate creates after uncertain outcomes/restart.
Independent draft/variant/media readback verifies the result; missing creation
media receipts need fresh explicit mapping review. No authored IDs, automatic
retry, deletion or historical-operation rewriting. Credentials remain in existing
environment/private configuration, never this table. See
[Shopify integration](SHOPIFY_INTEGRATION.md) for limits and manual acceptance.

## Shared Woo operational evidence

`woo_live_state.py` supplies the four operational product surfaces. Concepts remain
separate: Woo Identity is trusted store linkage; Verified Sync State is last
independently proven whole managed-product state; Woo Live State is informational
GET reconciliation; Publish Preview is the mandatory reviewed write plan.

Approved migration 0009 adds nullable `WooProductIdentity.live_state_json`, a
versioned record bounded to 4 KiB. Local-source/dependency and managed verification
fingerprints are separate from existing publisher hashes. No backfill infers
verification. Proof is recorded after expected children and Pass 2 complete, not
at parent identity upsert. Local changes invalidate proof; incomplete stages and
pending relationships cannot create No Change. Recovery takes precedence.

Recovery precedence means genuine unresolved operation/identity recovery, not a
permanent latch on a read-only observation. Transient incomplete reads retain
last-known/unavailable state and can be refreshed. Strictly later complete verified
publishing for the same product/store/trusted identity may supersede an older
failure for current product health; historical rows and taxonomy candidates remain
unchanged and reviewable. Uncertain or truncated evidence cannot be superseded by
that rule. Preview alone never clears operation recovery or records live proof.
The client exposes Checking only during active requests; terminal success, failure,
deadline and exhausted busy retries clear it without automatic publishing.

One authenticated/CSRF-protected incremental request checks one active parent,
reusing canonical Preview comparisons without an operation or execution token.
Ten-minute shared GET cache, bounded result cache and nonblocking per-app refresh
lock prevent repeat work in the existing one-worker deployment. Manual force
bypasses cache; 60-call and 20-second between-call budgets retain transport timeouts.
Persistent observations retain known drift independently of the last verified
baseline. Failed refresh preserves useful last-known evidence. More than one app
worker would require shared coordination before claiming cross-worker deduplication;
worker configuration is unchanged. There are no automatic identity/authored/Woo
writes beyond the approved informational SQLite evidence.

## Current checkpoint — 2026-09-11

M5.6 verified product taxonomy integration and reviewed driver proposals are
complete/live accepted. CURRENT_STATE records exact Simple/Variable evidence;
ROADMAP defines the next Final Polish / Operations UX milestone. Historical
guard/deferred descriptions below refer to earlier stages, not current capability.
Driver proposals reuse source resolution, registry naming/validation, signed
review, operation locking and the backed-up atomic writer; they never publish or
run Taxonomy Sync automatically. Dependent-route Integration Health probes remain
factual probe limitations, not missing product-publishing support.

## M5.6 verified product taxonomy boundary — 2026-09-08

`woo_product_taxonomy.py` composes the existing confined source resolver, immutable
registry snapshot and `WooTaxonomyIdentity` contracts; it does not write either
authored source or identity state. It returns legacy mode when the resolved
`variation_attributes` field is absent. Explicit contracts require active registry
definitions, current-store verified IDs, matching definition digests and exact term
scope. Parent category dependencies are checked against current verified parent IDs.
An immutable snapshot supplies all assignment matching for each contract.

Preview overlays categories/brands/global attributes onto the unchanged M4 payload
builder. Attribute visibility retains projected intent (registry default only when
not projected); authored option ordering is retained. Children come solely from
existing scanner-projected rows: selections must cover the exact explicit driver
Cartesian set, with unique combinations and no informational axes. Validation is
bounded to 1,000 combinations, without generating rows or SKUs. Variable with no
drivers is blocked; Simple with explicit empty drivers publishes informational data.

Controlled definitions bypass M4 taxonomy discovery/create. Preview and execution
perform at most 200 distinct trusted-ID GETs each, checking stored canonical identity
digests and category parent/scope before any product mutation. Execution also
rechecks the entire reviewed local explicit contract. Registry/source/mappings
participate in Preview freshness and safe-resume contracts. Brands and Simple
global attributes use semantic managed readback; existing parent/child verification,
identity persistence, images and relationships remain in the M4 path.

Tags are an intentional exception: explicit products retain the M4 tag planner,
exact compatible reuse, safe creation and readback/error handling. No Tag identity
table or requirement is added. Legacy taxonomy/payload behavior is unchanged.
The M5.3 blanket guard described historically below is superseded by these checks.

## M5 definition sync and range assignment boundary — 2026-09-07

Storefront Collection assignments use the previously proposed
`storefront_collections` readable-name array; missing inherits and present
(including empty) replaces. Resolution stays filesystem-based. No filesystem
Collection reinterpretation, scanner/ingestion change or product projection
table is introduced. Registry definitions map to Woo Brands, not a custom taxonomy.

`woo_taxonomy_sync.py` reuses Woo transport/store identity, registry reviewed
replacement and the catalogue operation lease. A narrow GET/POST endpoint policy
allows only categories, attributes, scoped terms and Brands; PUT is restricted to
reviewed attribute order_by / scoped-term menu_order corrections. Explicit signed
Preview/review/confirm detects changes to store/registry/identity/remote state.
Bounds and staged parent-first rounds are documented in TAXONOMY_REGISTRY.
Confirmed imports write registry first and mappings second; partial failure
retains the registry, never performs a fake cross-file rollback.

Migration 0008 adds `WooTaxonomyIdentity`: current-store key, taxonomy kind,
scoped local key, scoped Woo ID, verification digests/time and uncertain-create
state. Existing Woo product/variation identities cannot be reused because of
their required product FKs. Uncertain reservations have no trusted remote ID;
successful independent readback establishes it. Unique local/remote constraints
prevent double claims. Operation history records completed steps on failure.

The existing M4 payload builder and explicit M5.3 guard are not changed. Definition
identity state is not yet consumed by product publishing. Live Brands capability
must be checked on the deployment; local editing never requires it.

## M5.3 local assignment boundary — 2026-09-06

`app/taxonomy_assignments.py` reads the confined authored collection/override
sources and resolves readable category paths/names, attributes and scoped terms
against the immutable registry. Case/Unicode/whitespace normalization is matching
only; ambiguous matches remain unrecognised and neither authored file is changed.
Product Detail uses this read-only view. Dashboard Collections remain filesystem
groups, not Storefront Collections.

Existing category/tag list inheritance stays additive. Attribute objects keep
whole-object replacement between sources. Only `variation_attributes` adds a
replacement-array rule, preserving explicit `[]`; missing inherits a shared
designation or retains the legacy all-attributes Variable interpretation.
Simple explicit contracts require no drivers. Explicit Single Variable drivers
must include existing image axes when generating children; folder/image logic is
unchanged. At most five explicit drivers are accepted to prevent truncation in
existing child row slots. Informational assignments can exceed five.

`build_variations` selects only designated axes for opted-in sources. Ingestion
uses existing ProductAttribute rows for all explicit-contract authored attributes,
including Simple informational data. The per-product designation stays in authored
source, resolved on demand, not in a new database column. Existing child rows and
missing-child reconciliation remain the projection/recovery mechanism. Legacy
projection is unchanged. No migration or registry mutation occurs.

Registry adoption opens the existing authenticated, CSRF-protected signed review
and verified backup/atomic registry writer in another tab. It never saves product
assignments. The editor retains its draft, refreshes registry options locally,
and separately saves assignments through the established metadata source writer
and scan operation. A failed registry save cannot alter product metadata; a failed
subsequent metadata save retains the successfully created registry definition.

Temporary publishing boundary: any resolved presence of `variation_attributes`,
including empty, rejects Preview through PreviewError before configuration/remote
reads. A selected scope containing such a product is rejected as a whole; select
legacy products separately if needed. Execution rechecks authored opt-in before
the first Woo request, covering a changed source after confirmation. Existing
freshness/acknowledgement/lock contracts remain active. `_product_payload()` and
Woo attribute mapping/verification semantics are unchanged. M5.6 must implement
per-attribute semantics before removing this guard.

## M5.2 configuration and onboarding boundary — 2026-09-06

`Settings.product_folder`, `output_folder` and `url_prefix` retain their existing
SQL column names but resolve `PRODUCT_FOLDER`, `OUTPUT_FOLDER`, `URL_PREFIX`
application configuration first. Config reads environment at process startup;
restart after deployment changes. None means local SQLite fallback; explicit
blank means deployment-owned/not ready. ORM writes to configured properties are
rejected. Startup may create an empty Settings row for existing consumers, never
copying deployment values into fallback columns. No schema change exists.

Intake keeps its established independent `/intake` contract. Catalogue/output
mounts and public prefix are declared in Docker/Compose/Unraid, not chosen again
in Docker first-run forms. Local installs may edit only unowned fallback fields.
Readiness never echoes nonstandard directory paths, credentials or URL queries.
The image URL concatenation implementation remains untouched.

The old initial scan was explicit Append (or identity-preserving reconstruction,
or explicitly confirmed Full regeneration). Its success panel was client-side;
no durable success gate existed. New first-run admin creation writes bounded
version-1 `/app/instance/onboarding.json` via the existing atomic-file helper.
It stores a pending token, then successful operation ID and completion status,
not product data or secrets. Reads reject symlinks/corruption. The existing
operation scope carries that token and a configuration digest; Continue checks
both, terminal success/no failures/no recovery, and current readiness. Partial
warning-only results with no error/failure/recovery can complete. No duplicate
scan is required. Scope/history expiry before confirmation requires safe retry;
completed state remains durable independently of routine operation retention.

Legacy installs without this file are not retroactively gated. Missing/corrupt
pending state must not be manually deleted to bypass setup: restore instance
backup when damaged. All new setup state must remain with the database on the
persistent instance mount. The supported single-worker deployment is unchanged.

`/initial-scan` redirects to `/scanner?initial=1`; its obsolete dedicated template
is removed. The existing runner/services, confirmations, scope/recovery rules,
progress and results remain authoritative. Taxonomy availability is a first-run
route check only. Scanner business logic does not consume the registry. No Woo
request is needed for readiness. M5.3 must still separately version informational
attributes versus product-level variation designation without duplicate globals.

## Registry ownership clarification — 2026-09-06

The taxonomy workspace operates on whichever valid authored registry is mounted.
Startup and missing-registry states never install defaults. `/taxonomy` is
independent persistent authored storage, not catalogue/output/instance state.
TLC conversion is an optional explicit adapter. Its generated
[deployment artifact](../deployment/examples/tlc/registry.json) is excluded from
Docker, manually installed only for TLC, and is never a runtime fallback.
See [provenance/install contract](../deployment/examples/tlc/README.md).
M5.3+ must separate product-level variation designation from all assigned global
attributes without duplicating global definitions; current scanner/product/M4
contracts are unchanged.

For the dated released Phase 3 M4 checkpoint, see [Current State](CURRENT_STATE.md).
The [M5 Taxonomy Registry architecture audit](PHASE_3_M5_TAXONOMY_AUDIT.md) records
the proposed next extension, its observed compatibility constraints and approval
gates. It does not replace the implemented source/scanner contracts below.
M5.1 now provides the explicitly invoked, read-only
[local taxonomy registry loader](TAXONOMY_REGISTRY.md); no scanner or Woo path
consumes it. Future changes must
keep authored definitions and product assignments in filesystem JSON, distinguish
Storefront Collections from the existing Collection model, and document each
approved contract change alongside its implementation.

The M5.1 loader reads a bounded schema-v1 `registry.json` under configured
`TAXONOMY_ROOT` (default `/taxonomy`) and returns immutable snapshots plus content
digests. Readiness is observational: startup never loads/requires taxonomy, and
the loader never creates/repairs files or writes database state. Stable local
definition keys and category/term scopes are validated independently of Woo.
Directory-descriptor confinement rejects symlinks/overlapping roots. Registry
UI/import/save were added separately in M5.2; product assignment semantics,
projection and remote sync remain unimplemented. No migration or dependency
was needed for this boundary.

M5.2 registers a dedicated `taxonomy` blueprint with authenticated local CRUD,
Advanced source editing and TLC seed/CSV conversion proposals. Signed reviews
bind user, proposal and original source/directory revision. The registry writer
uses the existing bounded operation history/lease plus directory locking,
descriptor-confined verified backups/staging and atomic replacement/readback.
Filesystem registry remains authoritative; the audit record is not a replacement
or projection of it. See [registry contract](TAXONOMY_REGISTRY.md) for concurrency,
retention and failure/recovery limits. No product/scanner or Woo code consumes
the registry. Separate optional Compose/Unraid mounts never become startup gates.

```text
Filesystem and product_info.json
        ↓
Scanner
        ↓
Resolved Woo-compatible rows
        ↓
SQLite ingestion
        ↓
Flask web UI
```

## Flask application

`app.create_app()` is the application factory. It initializes SQLAlchemy, login management, CSRF protection, routes, and the database tables. `run.py` is both the development entry point and Gunicorn import target.

Existing HTTP routes live in `main` in `app/routes.py`; M5.2 registry-only routes
live in `taxonomy` in `app/taxonomy_routes.py`. Jinja
templates provide the UI over bundled Bootstrap assets. The Phase 2 shell uses
one semantic-token stylesheet, project-owned SVG symbols, and a small local
JavaScript controller; it does not require Bootstrap, jQuery, fonts, or icons
from a public CDN at runtime.

The authenticated shell uses a desktop sidebar, tablet icon rail, mobile primary
bottom navigation, and a secondary More drawer. Navigation groups Dashboard,
Catalogue, Scanner/Operations, Metadata, System, and Future workspaces. Incomplete workspaces use one shared
`Planned` template and never imply that a backend integration exists. Legacy
route aliases redirect to an appropriate safe workspace.

## Scanner

An authenticated request first acquires the catalogue operation lock, creates an in-memory run record, and starts a daemon thread. The thread reads the `Settings` row, enumerates collection folders, invokes the scanner, ingests generated rows, and updates progress. Server-Sent Events stream log lines while browser polling reads counts and status. Editor mutations acquire the same lock before changing JSON or marker files.

Before ordinary selection, the thread recovers any `.scanned.pending` whose recorded parent transaction already committed. Selected products atomically stage pending marker intent while retaining an existing `.scanned` and `.update`. After each parent transaction, the coordinator either finalizes `.scanned` and removes `.update`, records `database_recovery_required`, or records `marker_recovery_required`. Pending identity is catalogue-local and contains only the established marker payload plus bounded coordination fields.

Run state and mutual exclusion are process-local. Persistent operation rows provide history and interrupted-run diagnosis, not a distributed lock. This is why the Phase 1 container remains limited to one Gunicorn worker and one application replica. See [Catalogue Operation Control](CATALOGUE_OPERATIONS.md).

Phase 2 exposes a backward-compatible presentation view over that existing
process-local state. Legacy `total`, `done`, `status`, and `summary` fields
remain intact; normalized operation, progress, timing, and count objects drive
one shared accessible component across setup and metadata-triggered updates.
The added stage/current-item fields are observational only. They neither add a
queue nor persist live progress, and reconstruction remains the existing
synchronous controlled operation.

The authenticated Dashboard is composed by `app/dashboard.py`. Its queries are
read-only views over Collection, Product, Variation, ProductImage, and
CatalogueOperation records, supplemented by the existing process-local active
operation observation. The route does not cache, migrate, reconcile, or mutate
catalogue state. Summary and completeness values are derived at request time;
recent lists are deliberately bounded.

The authenticated Products browser is composed by `app/products_browser.py`.
`/api/edit_products` remains the backward-compatible parent endpoint while
adding collection groups, genuine summary facts, supported URL filters, and
server-side pagination. Correlated aggregate queries provide variation counts
and price ranges without loading child rows. The separate authenticated
`/api/products/<id>/variations` endpoint loads ordered variation attributes and
other projected child facts only after expansion. Both paths are read-only;
existing metadata editor, raw-source, override creation, and override deletion
routes remain the action authority.

Products and Dashboard thumbnails use the authenticated opaque route
`/catalogue-images/products/<id>`; expanded variation previews use
`/catalogue-images/variations/<id>`. SQLite continues to store the scanner's
Woo-facing image URL and portable source provenance rather than image bytes.
For UI display, files beneath the configured catalogue mount are authoritative;
emitted URLs are filename hints because uploader conversion can change the
extension to `.webp` and may change the upload name.

`app/catalogue_images.py` follows scanner-supported folders only. Parent
resolution uses the ordered primary gallery mapping, the product shortcut,
remaining ordered parent images, recorded `.scanned.images_used`, then safe
direct-folder discovery. If no parent source resolves, the first ordered valid
variation image becomes the parent thumbnail. Variation routes preserve their
own Single Variable image-attribute folder identity and fall back to the parent
only when necessary; Variable Collection variations retain the scanner's shared
parent-image behaviour.

Single Variable collections reserve the semantic name `parent` at the collection
root for the parent primary/gallery set. Its directory match is case-insensitive,
preserves the actual spelling in portable provenance, and rejects multiple
case-variants as ambiguous. Configured image-attribute names define variation
directory depth in order; every case-variant of `parent` is excluded from that hierarchy. Ingestion
keeps the scanner-generated website URLs and positions in `ProductImage` and
`VariationImage`, and records confined portable source identities as image
`ProductAsset` rows. UI resolution prefers those persisted source identities,
then compatible marker/URL discovery, and uses a variation preview only when no
usable genuine parent source remains.
PNG, JPG, JPEG, and WebP sources are accepted
case-insensitively by extension. Traversal, symlink escape, unsupported and
invalid files are rejected, and catalogue paths are never returned to the
browser.

`app/metadata_workspace.py` is the read-only composition boundary for Product
Detail and metadata source editors. It resolves collection and override JSON by
portable catalogue-relative identity, confines reads beneath the configured
catalogue root, applies the protected `merge_product_json()` behaviour, and
presents collection/override/resolved comparisons without mutating the
projection. `/products/<id>` is the canonical Product Detail route;
`/collections/<id>/metadata` edits the shared source and the established
`/edit_products/<id>/edit/<label>` compatibility route opens the same guided
editor. The old save endpoint remains the only write authority.

Gallery routes extend the opaque authenticated image interface with a bounded
ordered index for product- and variation-owned sources. They recalculate each
confined mapping and never accept a filesystem path from the request. Stored
website URLs remain read-only diagnostics. A parent preview fallback shown for
a variation is explicitly labelled and never creates a variation image URL.

Product Detail loads at most 24 variation children initially. Further detail
uses `/api/products/<id>/detail-variations`; collection editor previews use the
paginated `/api/collections/<id>/affected-products`. Relationships required by
each page are select-in loaded so page size, rather than catalogue size, bounds
work and avoids per-row database access.

`app/collections_workspace.py` is the read-only aggregation boundary for
`/collections` and `/collections/<id>`. It reuses the existing `Collection`
projection and integer identity, validates the portable shared metadata source,
uses grouped correlated counts, and bounds ordinary image checks to the
rendered collection/product page. Collection Detail paginates affected parents
without loading their Variation relationships. Source-image coverage uses
portable `ProductAsset` rows through the same confinement and content checks as
the opaque image routes. The filesystem catalogue and collection
`product_info.json` remain authoritative; this view does not add an authored
database collection layer.

`app/collection_identity.py` is the presentation boundary for collection names.
It derives the visible title from the basename of existing portable collection
provenance, keeps `Collection.id` as route identity, and never treats the shared
JSON `title` field as a collection title. The latter remains authored shared
product metadata. This resolver performs no filesystem reads or mutations.

## Persistence

SQLite stores users, settings, the complete emitted parent/variation row projection, exact Collection → Product → Variation relationships, a reconstructable product-relationship index, images, attributes, taxonomy, JSON provenance, operation history, and dormant integration-oriented models. Product folders, authored JSON, scanner markers, and SKU counters remain independent filesystem state.

Product relationships are filesystem-authored metadata, separate from protected
scanner-emitted row fields. Stable target SKUs and explicit order live in the
product-specific `relationships` block. SQLite indexes those edges with a
nullable target Product resolution for search and reverse lookup. Missing target
SKUs remain repairable, and reconstruction recreates the index. Mutual families
use coordinated staging, persistent intent, verified backups, deterministic
promotion, rollback/recovery, and the catalogue lock. No Woo client is invoked.

Each collection and product has a POSIX-style source path relative to the configured catalogue root. This is the portable identity/provenance representation and remains stable when the catalogue mount point changes. Existing absolute path columns remain runtime locators for filesystem routes; they are not portable identity. Parent and variation `resolved_row_json` retain every key/value actually emitted by the protected scanner, while normalized columns and related tables provide common query fields.

Ordinary append/update ingestion groups emitted variation rows beneath their emitted parent row. One SQLite transaction covers the collection relationship, parent projection and provenance, galleries, JSON assets, taxonomy, parent attributes, variations, variation attributes and variation galleries, stale-variation reconciliation, plus that parent's successful operation-history item. A stage failure rolls that parent graph back and records a separate sanitized failed item; parents committed by earlier transactions remain committed. Stale variations are soft-marked `missing` in that transaction and restored in place when their emitted identity returns.

Product-level reconciliation is a separate post-ingestion transaction and is
allowed only after an exhaustive scope has resolved, every selected parent has
committed, and marker finalization has succeeded. Deliberate full scans cover the
catalogue; shared JSON saves explicitly force every product in one collection and
cover only that collection. Expected filesystem products are preflighted and
compared with emitted parents. Missing, empty, unreadable, invalid, or partially
resolved scope prevents product reconciliation. Append and individual update scans never
treat unseen products as missing.

## Reconstruction

Setup detection reads only the configured repository-fixture catalogue and the
current projection. It distinguishes no identities, existing marker/pending
identities, an existing projection, and malformed/unavailable state. Ambiguous
state blocks identity-generating actions rather than falling back to full scan.

Reconstruction acquires the catalogue-operation lock, preflights every collection,
override JSON object, marker, and expected parent, then resolves all scanner rows
before touching the projection. It forces selection with SKU reuse enabled and
counter reset disabled. Valid markers are read but not rewritten. Database
identity overlays add safely matched variation identities that are newer than an
old marker payload.

After complete resolution, SQLite is integrity-checked and backed up beneath the
active instance directory. Collection, product, variation, provenance, taxonomy,
and lifecycle changes are applied in one transaction. Users, settings, operation
history, internal IDs, and Woo placeholders are outside replacement or are updated
in place. A parent/replacement failure rolls the transaction back; the verified
backup remains. New-product pending markers finalize only after commit. Existing
pending recovery makes the operation partial. Discord and Woo integrations are
not invoked.

Alembic owns SQLite schema initialization and upgrades. Application startup must reach migration head before requests are served. A structurally matching unversioned Phase 0 database is backed up and stamped at the frozen baseline; an unknown schema is rejected. Migration backups cover SQLite only and do not make filesystem catalogue state transactional.

Atomic replacement prevents torn JSON files but does not make SQLite, marker files, SKU indexes, and processed images one transaction. Operational backup and recovery must include both the instance mount and catalogue/output mounts.

## External services

Discord notifications are outbound webhook POST requests configured exclusively
through runtime environment variables. WooCommerce connection discovery and
preview use a GET-only transport. Controlled publishing is isolated behind a
publisher-only transport that permits authenticated same-origin POST/PUT/PATCH
requests, refuses DELETE and mutating redirects, bounds request/response bodies,
and never forwards credentials across origins. Each selected parent is verified
after its write before its store-scoped identity is persisted; relationship
writes occur only in Pass 2 after safe identity resolution. Authored catalogue
JSON remains the source of truth and is never rewritten by publishing.
