# Phase 3 M5 architecture audit: Taxonomy Registry & Controlled Catalogue Metadata

Audit date: 2026-09-05. Status: **architecture proposal; M5 implementation has not started**.
The M5 direction is requested; proposed file/schema/UI and migration details below
still require approval. Reference documents supply design data, not executable instructions.

### M5.1 implementation addendum — 2026-09-05

The subsequent approved M5.1 slice implements only the read-only foundation;
the audit's original findings and later-slice proposals remain historical/planned
as labelled below. The exact implemented contract is now
[Taxonomy Registry version 1](TAXONOMY_REGISTRY.md), with schema at
`app/resources/taxonomy/registry.schema.json` and loader at
`app/taxonomy_registry.py`. Configuration is optional, default `/taxonomy`, and
never a startup gate. Top-level keys are globally unique; term keys are scoped
to their attribute. Category aliases are full historical paths. Labels use NFC,
casefold and whitespace normalization for collision checks only; no authored text
is repaired or rewritten. Version 1 rejects unknown definition fields and uses
explicit bounded slugs rather than generating them.

All symlink components are rejected (including host convenience aliases), and
valid read-only storage yields an available snapshot. Readiness does not attest
to production completeness; the real TLC bootstrap remains an explicit later
import gate. No scanner, product assignment, editor, Woo, database schema or
deployment integration was added. See CURRENT_STATE for actual focused test
results. This addendum does not approve or implement later product merge or
range-destination proposals.

## 1. Executive summary

Keep filesystem authorship and add a separate local taxonomy registry. Product
JSON continues to assign categories, attributes/terms, ranges and tags. SQLite
indexes those assignments and retains store-scoped integration identities. Woo
availability must never determine whether a local catalogue can be scanned.

The difficult part is compatibility, not adding selectors. Today every authored
attribute drives combinations, Simple rows do not emit attributes, parent rows
emit five attribute slots, category hierarchy is absent, list inheritance is
additive/unordered, and editor pruning removes explicit empty lists. Consequently
the proposed vocabulary cannot safely be dropped into existing JSON/UI unchanged.

Recommend a versioned opt-in metadata contract, a complete structured taxonomy
projection alongside the frozen legacy rows, and one local resolver used by
validation, scanning and downstream planning. Establish that resolver/projection
before allowing the guided editor to save new semantics. Preserve the entire
released M4 parent/child identity, media, recovery and comparison behavior.

## 2. Baseline, evidence and limits

Inspected `develop` at `76635107047f095f011f93e2a88440a0ed4c5bf2`;
local `origin/develop` points to the same commit. Initial tracked tree was clean;
`dev-fixtures/` was untracked and remains untouched. No remote Git refresh or
live Woo request was needed for the audit. The user reports M4 live acceptance
on WooCommerce 11.0.1 with native Variation gallery enabled, with 832 Python and
28 JavaScript tests at the release checkpoint. These suites were not rerun here.

Read the four supplied Desktop files: the taxonomy JSON, category CSV,
navigation-attribute CSV and Category Decision Guide. The additionally mentioned
`woocommerce-navigation-structure.md` was not supplied at a known path and was
not found in the targeted Desktop filename search; no navigation requirements
are inferred from that missing document. No private reference files are copied
into the repository. Public Woo documentation/code was consulted for API facts;
it does not prove this store's enabled features or permissions.

## 3. Current-state architecture and code evidence

| Boundary | Actual contract / principal evidence |
|---|---|
| Authored source | `product_info.json`, source folders and scanner identity markers. `docs/PRODUCT_INFO.md`, `app/utils/json_utils.py:merge_product_json`. |
| Shared/override resolution | Shared object copied first. Override scalars/objects replace; lists union through `set`, losing ordering. Special title inheritance. Sparse overrides apply to Simple/Variable Collection products; Single Variable is its own collection-root product. |
| Scanning | `app/utils/scanner.py:scan_collection` dispatches exact `Simple`, `Variable Collection`, `Single Variable`; `build_variations` takes the Cartesian product of **all** `attributes` names/options. No `variation_attributes` contract exists. |
| Emitted rows | `app/utils/csv_writer.py`: categories/tags become comma-separated strings. Simple builder emits no attribute slots; Variable parent emits first five slots; child rows emit selected attributes. The five-slot limit is an application legacy limitation, not an asserted Woo REST limit. |
| Ingestion | `app/utils/ingest.py:_sync_taxonomy` splits commas, resolves Category/Tag by name, replaces product memberships. `_sync_product_attributes` reads `ATTR_SLOTS = range(1, 6)` and removes stale projected attributes. `resolved_row_json` preserves what was emitted, not arbitrary omitted authored metadata. |
| SQLite | `app/models.py`: Category/Tag have unique name, slug and non-store-scoped legacy `woo_id`. No category parent/primary role. ProductAttribute has name, text values, visibility/global flags and position, but no variation-use flag. No global attribute/term registry or storefront-range model. |
| Read UI | `app/metadata_workspace.py`, `app/collections_workspace.py`, Product Detail and collection pages compose source/projection views. `app/product_relationships.py:search_products` searches existing category/tag/attribute projection; its behavior is protected. |
| Editing | `app/routes.py:product_save_json`, `app/product_info.py`, schemas/inventory and `metadata-editor.js`. Guided/Advanced modes share validation, source ownership and save path. `_deep_merge` is a same-file merge, distinct from scanner inheritance; `_prune` removes empty containers/null. Shared saves refresh a collection; overrides write `.update` and start update scanning. |
| Woo | `woo_publish_preview.py`, `woo_controlled_publish.py`, `woo_managed_comparison.py`, `woocommerce_connection.py`. Bounded discovery/planning, reviewed writes, exact identity reconciliation; no authored Woo IDs. |

Schemas allow unknown top-level fields and validate editor saves; scanner loading
does not globally enforce the editor JSON Schema. Simply adding schema fields
would neither enforce a vocabulary on external edits nor change generation.
The editor's current save/start legacy routes include CSRF exemptions: new
taxonomy mutation endpoints must use explicit CSRF protection; auditing this
does not authorize an unrelated authentication rewrite.

## 4. Current taxonomy lifecycle

### Categories and tags

`categories` and `tags` are string arrays in shared/override JSON. Inheritance is
additive; an override `[]` cannot remove inherited entries. They pass through
comma-separated emitted fields into name-based Category/Tag rows and unordered
many-to-many memberships. Commas inside labels cannot round-trip losslessly
through this path. No Primary/Secondary Category field exists.

The editor offers free-text repeatable lists; no controlled registry or fuzzy
suggestion authority exists. A string such as `Cards > Birthday Cards` is not
parsed into parent/child identities: it becomes a single projected category
name/slug. Category labels that repeat under different parents cannot use the
current globally unique Category.name as their canonical registry identity.

Preview `_taxonomy_plan` reads bounded `products/categories`, `products/tags`,
`products/attributes` and attribute-scoped terms. It matches canonical slugs
and compatible names, reports existing/create-required/ambiguous, and builds
Woo ID references. The publisher rechecks before create, retains create IDs,
performs bounded readback/reconciliation and blocks unresolved dependencies.
Category/tag create payloads currently contain only `name` and `slug`; category
parent hierarchy is neither sent nor verified. A typo can become a reviewed
create-required taxonomy dependency because there is no permitted vocabulary.

Managed comparison treats category/tag ID membership as unordered. Empty local
categories equal remote default-only membership only with dynamically resolved
current-store context. The accepted implementation includes authenticated
`/wp-json/wc-admin/options?options=default_product_cat`; old documentation
describing only settings/Store API fallback is incomplete. Primary category is
not a standard managed role in this application's Woo payload; do not infer it
from REST array order or promise an SEO-plugin primary-category integration.

### Attributes, variation designation and images

`attributes` is an object mapping names to non-empty arrays of scalar values.
For Variable products all names drive combinations, modifier matching and child
identity. Parent ProductAttribute visibility/global flags come from emitted
rows (Variable builder sets both true). Publisher `_product_payload` sets
`variation` from the **product type for every attribute**, not a per-attribute
designation. Simple informational attributes authored today are not emitted by
`build_simple_product`, so they do not reach ordinary projection/publishing.

The current global attribute create contract is `type=select`,
`order_by=menu_order`, `has_archives=false`. `taxonomy_row_compatible` considers
these settings when checking identity. Navigation/filter intent must not be
equated with archive enablement: changing `has_archives` globally would turn
otherwise accepted attributes into conflicts. Attribute slugs are synthesized
by a lightweight whitespace/case/underscore function, with semantic `pa_`
normalization; labels such as `Style / Theme` need an explicit safe registry
slug rather than assuming the current function handles arbitrary punctuation.

Global attributes resolve before their terms; terms are always read/created
beneath the verified parent attribute ID. Child `_resolved_variation_payload`
uses verified `{id, option}` for globals and `{name, option}` for custom
attributes. Terms have IDs in resolution state, but child selections use the
term option, not a term-ID substitute. Expected children come from explicit
scanner-projected `Product.variations`, never a second publisher Cartesian model.

Single Variable `image_attributes` defines folder levels in its configured
order; root `Parent/` is reserved case-insensitively. It is not the full list of
variation axes. Existing code accesses the first image axis in each child's
selected attributes, so excluding an image axis from a new variation selection
is incompatible without a separately approved image model. Variable Collection
retains its existing shared-image convention. New navigation labels must not
rename image folders, alter marker attribute tokens, reorder axes or rewrite
modifier keys implicitly.

## 5. Dashboard Collection versus Storefront Collection

`Collection` owns filesystem provenance (`root_path`, unique portable
`source_relpath`, shared JSON path), unique SKU prefix, collection type and
child products. UI display uses the folder-derived collection identity. One
product belongs to one such group; scanning and shared refresh follow it.
No Woo storefront collection is currently emitted or managed from this model.

A Storefront Collection is a many-product design range independent of physical
group, SKU prefix, category and product type. A product may belong to more than
one range if approved. **Do not reuse or rename the existing Collection table.**
Use an explicit registry kind `storefront_collection`, UI label “Storefront
Collections”, and product field `storefront_collections`. Keep filesystem
collection/group labels unambiguous in editors and filters.

Woo Brands is a plausible destination, not an approved mapping. Official Woo
documentation describes core Brands enabled by default from 9.6; its controller
uses `products/brands` and `product_brand`, and the product v3 resource documents
brand assignments. Repository capability specs do not probe brands and the
managed payload has no brands field. Version alone cannot prove live availability.

Recommend: keep local range identity destination-neutral; first evaluate core
Brands through a later explicit authenticated capability review and Dale's
approval of its merchandising meaning. Brands can expose brand terminology,
archive links and structured data; a named range need not be a manufacturer.
If that semantic choice is unsuitable, a dedicated REST-exposed custom taxonomy
requires separate WordPress work. A reserved global non-variation range
attribute is a lower-cost filtering option but has attribute rather than named
range archive semantics. Do not silently turn ranges into tags/categories or
implement multiple adapters before selecting one.

Sources: [Woo Brands announcement](https://developer.woocommerce.com/2025/01/17/enabling-brands-update-for-woocommerce-9-6/),
[Brands REST controller](https://woocommerce.github.io/code-reference/files/woocommerce-includes-rest-api-controllers-version3-class-wc-rest-product-brands-controller.html),
[v3 Products](https://developer.woocommerce.com/docs/apis/rest-api/v3/products/),
[v3 Categories](https://developer.woocommerce.com/docs/apis/rest-api/v3/product-categories/).

## 6. Registry root, organization and ownership — proposal

Use `TAXONOMY_ROOT=/taxonomy`, separate from catalogue, output, instance and
Intake. Recommend **one versioned `/taxonomy/registry.json` initially**, containing
categories, storefront collections, attributes with nested terms, and tags.
At 56 categories/110 starting terms this is small and human-readable. One atomic
replacement gives a consistent vocabulary snapshot and avoids multi-file
transaction/recovery machinery. Workspace tabs need not dictate file splits.
Split later only for evidenced size/concurrency needs, with a snapshot manifest.

Stable opaque-in-meaning local keys (readable immutable identifiers) identify
definitions; names/slugs are editable properties, never IDs. Parent references
use keys, not labels. Terms are keyed within their attribute. No Woo IDs, store
hosts, credentials, response data or runtime paths belong in authored registry.
Category full paths and depth derive from the parent tree, not duplicate fields
that can disagree. Registry order is presentation order, separate from category
membership comparison and variation image order.

Allow external human editing and controlled authenticated dashboard editing.
Read a bounded, strictly parsed UTF-8 document; reject duplicate JSON keys,
invalid references/cycles and normalization collisions. Expose readiness states
missing/invalid/read-only/ready, with no fallback registry silently created in
the image. Support read-only mounts for offline validation; writes require a
writable root. Reject path traversal, symlink escape and overlapping roots.
Snapshot/hash once per operation; refresh on content changes. Invalid updates
must be visible, never silently replaced by an authoritative stale cache.

Registry saves: server validation, explicit confirmation, expected-digest check
under the shared operation lock, verified backup beside the authored file,
atomic replacement, bounded operation/Discord history, no automatic Woo sync.
Reference-impact review precedes rename/deprecation. No cross-file atomicity
claim when a later adoption operation updates many product files: use a durable
transaction manifest and recovery design, with explicit scope/confirmation.
Reuse atomic/backup patterns, not Product Relationships internals/state.

Docker proposal for later implementation: add an explicit host mount setting
`TAXONOMY_FOLDER_HOST`, map it to `/taxonomy`, and pass `TAXONOMY_ROOT`. Extend
Compose, `.env.example`, Unraid XML and readiness display together. Permissions
follow PUID/PGID; no recursive ownership change of authored shares and no seeded
fallback at startup. Existing installations remain usable without the new mount
until adoption is selected. Runtime registry/backups must be excluded from image
context and covered by external mounted-data backups. SQLite backup alone cannot
restore authored taxonomy, just as it cannot restore authored catalogue JSON.

## 7. Concrete authored contracts — proposals, not implemented formats

Example registry (abbreviated vocabulary, all keys local):

```json
{
  "schema_version": 1,
  "categories": [
    {"key": "cat-cards", "name": "Cards", "slug": "cards", "parent": null, "state": "active", "order": 1},
    {"key": "cat-birthday-cards", "name": "Birthday Cards", "slug": "birthday-cards", "parent": "cat-cards", "state": "active", "order": 2}
  ],
  "storefront_collections": [
    {"key": "range-16-bit", "name": "16-Bit", "slug": "16-bit", "state": "active"}
  ],
  "attributes": [
    {
      "key": "attr-recipient", "name": "Recipient", "slug": "recipient",
      "navigation": true, "visible_default": true, "state": "active",
      "terms": [{"key": "daughter", "name": "For Daughter", "slug": "for-daughter", "aliases": [], "state": "active"}]
    },
    {
      "key": "attr-design", "name": "Design", "slug": "design",
      "navigation": false, "visible_default": true, "state": "active",
      "terms": [
        {"key": "train", "name": "Train", "slug": "train", "state": "active"},
        {"key": "church", "name": "Church", "slug": "church", "state": "active"}
      ]
    }
  ],
  "tags": [{"key": "tag-new-season", "name": "New Season", "slug": "new-season", "state": "active"}]
}
```

Recommend preserving readable current product assignment shapes, with explicit
version opt-in; canonical labels/full category paths resolve to stable registry
keys. Registry aliases support old labels without silent source rewrites.
Assignments are references, not repeated definitions. Do not store a second
shadow copy of assignments in a `registry_ids` block.

Example **Simple product override**, inheriting its existing shared identity:

```json
{
  "taxonomy_contract": "registry-v1",
  "categories": ["Cards > Birthday Cards"],
  "primary_category": "Cards > Birthday Cards",
  "storefront_collections": ["16-Bit"],
  "attributes": {"Recipient": ["For Daughter"]},
  "variation_attributes": [],
  "tags": ["New Season"]
}
```

For an existing Variable product, the new part can instead be:

```json
{
  "taxonomy_contract": "registry-v1",
  "attributes": {"Design": ["Train", "Church"], "Recipient": ["For Daughter"]},
  "variation_attributes": ["Design"],
  "image_attributes": ["Design"]
}
```

This is only appropriate where the established image layout already uses
Design. It is not permission to reshape existing folders. Definition-level
navigation, default visibility and per-product variation use are different
properties. Global registry attributes should be global Woo taxonomies;
legacy custom attributes remain supported during adoption rather than silently
converted. Do not introduce a second “Variation Design” definition.

## 8. Product compatibility and merge contract

Keep unversioned files on the exact legacy resolver. For adopted files, propose
these explicit semantics in a small shared resolver, without changing the
generic legacy `merge_product_json()` behavior:

| Field in `registry-v1` | Proposed shared/override rule |
|---|---|
| `categories`, `tags`, `storefront_collections` | Missing override inherits; present array replaces the complete membership, including `[]`. |
| `primary_category` | Missing inherits; present value replaces and must be a member; explicit null clears where policy permits. Never derive primary from list order. |
| `attributes` | Preserve existing whole-object replacement, not deep per-attribute inheritance. Missing inherits. UI makes complete-object override explicit. |
| `variation_attributes` | Missing override inherits an explicit shared designation; present array replaces, including `[]`. A resolved adopted document must have a designation; no “all attributes” fallback. |
| `image_attributes`, modifiers, title and commercial metadata | Preserve protected behavior; validate compatibility with the selected axes and existing sources. Do not rename or reorder implicitly. |

Adoption must preview the fully resolved before/after result for every affected
product. Changing a shared contract opts descendants into different resolution,
so reject mixed-version/ambiguous scope until all inherited overrides have been
reviewed. Removing a shared explicit variation list must not fall back to all
attributes. Empty designation on a Variable product with expected children is
an error requiring review; on Simple it expresses informational attributes only.

New fields cannot be sent through today's `_prune` unchanged. Add field-aware
preservation of meaningful empty/null values only for the new contract, and
share it across guided and Advanced saves. Keep invalid drafts intact. Do not
flatten inherited defaults into sparse overrides. Validate external JSON with
the same resolver before scan side effects. Assignment labels containing commas
must use the new structured projection, not comma splitting.

## 9. First-run/setup

Current startup applies migrations and operation/relationship recovery; it does
not scan. `/setup` creates/logs in the first admin. `/initial-settings` persists
only `Settings.product_folder`, `output_folder`, `url_prefix`, then redirects
to `/initial-scan`. That GET shows marker-aware readiness; buttons explicitly
POST Append/Full or reconstruction. There is **no immediate automatic first scan**.
Woo credentials are environment-only and tested in a separate explicit workflow.

Propose: admin → local mount/config readiness → Taxonomy workspace/import/review
→ local vocabulary validation → explicit Append or identity-preserving
reconstruction → product validation → Preview. Woo connection and optional
reviewed taxonomy sync are a parallel optional branch, not a scanner gate.
Do not put credentials into Settings or turn setup into a credential editor.

Environment/config owns the taxonomy root (no Settings migration solely for a
path). Registry existence/schema and content digest provide readiness; bounded
operation history records adoption. Do not gate existing installations on a
new mandatory setup wizard. Registry unavailable in compatibility mode warns;
adopted products requiring it cannot publish, and invalid products must not have
markers/SKUs/output advanced as though validation succeeded. An exhaustive scan
with invalid inputs must not falsely mark products/children missing.

## 10. Validation policy — proposal

| Condition | Controlled authoring / adopted publishing behavior |
|---|---|
| Unknown category, range, attribute or term | Blocking assignment error. Offer bounded suggestions, never auto-accept. Explicit “add definition” goes through registry review first. |
| Unknown tag | Warning while drafting/importing; explicit add-and-use is allowed after registry save. Unregistered tags cannot silently create remote terms at publish. |
| Case/Unicode/whitespace variants | One defined comparison policy (NFC, casefold, whitespace). Suggest canonical spelling; ambiguous aliases/definitions block. Preserve meaningful accents/punctuation; never fuzzy-merge identities. |
| Duplicate assignment | Identify in review; reject ambiguous duplicates, offer explicit deduplication. Registry keys and kind/slug identities must be unique in their scopes. |
| Rename | Stable key retained, explicit old-label/path alias, impact preview. Referenced variation/image labels need a separate identity/folder/modifier plan; no automatic rename propagation. |
| Delete | Prefer deprecate/tombstone. Keep references visible and repairable; refuse hard removal while referenced. Never cascade-delete authored assignments or Woo objects. |
| Missing primary | Legacy empty categories remain allowed with a warning/default-category comparison. Adopted products require one explicit primary before publishing. |
| Invalid primary/secondary | Primary must be registered and assigned; secondary distinct and registered. A third category warns rather than blocks solely on count. |
| More than two categories | Explain the guide's recommendation; preserve real extra memberships rather than silently truncate. |
| Variation designation references unassigned attribute | Blocking; each chosen axis has valid non-empty options. Informational terms never become child selections. |
| Image axis excluded from variation axes | Blocking for the current supported layout. No automatic folder/model rewrite. |
| Registry missing/invalid/changed after review | Visible readiness/staleness error; no remote create and no reuse of a stale permissive vocabulary. |

During adoption, validation-only mode reports legacy unknowns without making
existing catalogue JSON unusable. Once enforcement is explicitly enabled,
publishing must use registry-approved values; legacy unknown structured values
are reviewed/registered first. Compatibility parsing must not remain a silent
route around controlled remote creation. Local draft/diagnostic loading can
remain possible while a product is ineligible to publish.

## 11. Woo taxonomy sync

Reuse the current authenticated client, store fingerprint, operation lock/history,
bounded error/redaction, resolve-before-create, direct create-ID verification,
`pa_` normalization and retained-write reconciliation. Do not copy four new
independent resolvers. Extract shared primitives only when the sync slice needs
them, keeping existing publisher regression tests on the same service.

The new planner starts from a validated registry snapshot, not whatever strings
happen to appear in products. It must distinguish readable-complete-empty from
unavailable/forbidden/truncated discovery. Current Preview may represent a
failed taxonomy GET as an empty candidate list; that is insufficient evidence
for a registry sync to declare absence. Pagination completeness needs an explicit
bounded result and a block/rescope response when limits are reached.

Create categories in parent-before-child order, send verified `parent` IDs and
compare the hierarchy. Resolve attributes before scoped terms; validate explicit
slugs against Woo constraints before any create. Registry permission to define
a value and remote permission to synchronize it are separate confirmations.
Navigation use must not force archive/config changes outside the managed
contract. Existing remote terms/config conflicts require review rather than
rename workarounds. Range mapping remains disabled until its destination is
approved and authenticated capability/assignment readback is demonstrated.

A taxonomy-sync review records create/reuse/conflict counts, exact changes,
snapshot digest and store; final confirmation rereads/revalidates. Successful
identities are stored locally only after verification. Retained uncertain
writes reconcile on retry; never DELETE or automatically rename. One bounded
terminal notification, no per-term spam. Syncing definitions never publishes
products, uploads media or mutates product assignments.

Later product publishing consumes approved definition identities and the same
resolver. Recommended default: require explicit taxonomy sync for newly added
definitions; any retained on-demand create path must require registry approval
and its own reviewed create scope. It may never implicitly add vocabulary.
Registry changes and destination mapping changes must invalidate cached Preview
and final confirmation using content digests, without adding eager per-product
Woo reads.

## 12. SQLite and migrations

No migration is needed for M5.1 registry loading/validation or basic file-backed
registry search/editing. Existing JSON/schema/atomic helpers and operation
envelopes suffice for these bounded steps; no new dependency is needed.

The complete M5 model **does justify a later reviewed migration**. Existing
Category/Tag rows lack hierarchy/stable keys/store scope; ProductAttribute has
neither a variation-use flag nor structured lossless term identity; ranges do
not exist. Do not repurpose legacy `Category.woo_id`/`Tag.woo_id` or overload
WooProductIdentity to store taxonomy state.

Proposed compact relational extension, with final DDL approved in M5.3:

- `TaxonomyEntry`: reconstructable definitions indexed by immutable registry
  key/kind, with parent/attribute scope, display name, authored slug and state.
  Terms are a kind scoped to their attribute. No global unique-name restriction.
- `ProductTaxonomyAssignment`: reconstructable product-to-entry memberships,
  attribute/term associations, primary role, visibility/variation use and position
  where meaningful. Keep missing/deprecated references diagnosable; do not
  cascade away authored assignments. Distinguish category/range/tag membership
  from attribute-term membership rather than using one ambiguous parent column.
- `WooTaxonomyIdentity`: current store + registry key + route/parent namespace
  + verified remote ID and verification/digest/recovery metadata. A term ID
  without its attribute and store is not a reusable identity.

These tables replace no authored authority and do not duplicate the existing
product/variation identity tables. Rebuild registry/assignment indexes from
registry plus resolved product sources. Preserve legacy emitted-row projections
and consumers during adoption; UI/search must deliberately read the versioned
taxonomy view so it does not miss informational attributes. Do not simply append
sixth-plus attributes to the legacy CSV or claim omitted values were emitted.

Use a structured resolved metadata envelope at the scanner/ingest boundary for
the opt-in contract, retaining registry/source digests and provenance and all
attributes. It is an explicit extension of the parity boundary for adopted
files, not a silent change to frozen legacy rows. Ingest it with the complete
parent transaction and reconstruction path. Precise table/index choices are
reviewable in M5.3 before migration implementation; do not promise “no migration”
for the completed feature solely to avoid representing these requirements.

Database deletion loses store integration trust/history unless restored from
backup. Authored definitions/assignments survive on their mounts; remote mappings
must then be reverified, never guessed from numeric IDs in authored JSON.
Reconstruction preserves applicable existing integration state. Include taxonomy
root in backup/export documentation; no new Woo export is proposed.

## 13. Workspace/editors

Add a Taxonomy workspace with Categories, Storefront Collections, Attributes &
Terms, and Tags tabs. Show hierarchical paths, state, usage/impact counts, local
validation and separate Woo readiness. Registry-only operations work offline.
Controlled add/rename/deprecate and a separate sync review reuse operation and
design-system patterns. No registry definition is edited indirectly by typing
into a product assignment.

Existing collection/override editor guided lists become searchable registered
selectors only once the opt-in resolution is ready. Display full category paths,
explicit primary, optional secondary and reviewable extras. Attribute rows reuse
one global definition with term multiselect and a per-product variation toggle.
Keep inherited/source comparisons, override enablement, Advanced JSON validation,
draft preservation and affected-product pagination. Product Detail remains a
resolved view and links to the correct source editor. A range selector must not
change the product's physical collection.

Use semantic surface tokens, keyboard-operable selection with explicit labels,
focus return, inline field errors and screen-reader status. Existing relationship
pickers provide interaction examples but are not the new taxonomy service and
must not be refactored as part of this work. Verify desktop/tablet/390px layout
without introducing new frontend packages. Catalogue Intake keeps its schema
compatibility through the shared contract; no Intake workflow redesign.

## 14. Seed and adoption assessment

Read-only seed analysis confirmed 56 category objects matching all six CSV
columns after normalizing null/numeric serialization; seven root categories;
no duplicate category names/slugs/paths and no missing named parent. JSON and
navigation CSV each contain seven attributes with 110 starting terms. Counts:
Occasion 26, Recipient 32, Age / Milestone 20, Personalisation 3, Material 10,
Production Method 7, Style / Theme 12.

Use this as a **reviewed bootstrap input**, not the authoritative runtime schema.
It has no immutable keys, attribute/term slugs or lifecycle/aliases; category
parent/name/path/depth duplicate information; ranges/tags are described rather
than supplied as definition arrays. Convert deterministically in a dry-run import
with counts/collision/diff review. No automatic seed import on startup.

The guide includes examples such as `Recipient: Daughter` versus the seed's
`For Daughter`, and suggests `Home & Décor > Decals & Stickers` absent from the
category seed. These need reviewed aliases/new definitions, not fuzzy automatic
merging. Craft Supplies is explicitly conditional in the guide; importing its
definitions does not prove every category should be promoted in navigation.
Design/Size/Finish/Build Type and live variation vocabularies are not exhausted by
the seven navigation attributes; inventory and register those separately. Avoid
forcing existing `Style` into `Style / Theme` or changing folder-linked labels.

Adoption order: import/review definitions; offline validation-only inventory;
resolve spelling/hierarchy conflicts; preview each shared/override resolved
assignment and explicit variation set; back up; opt in one fixture/group;
identity-preserving update/reconstruction; compare before/after SKUs, combinations,
media and Woo plans; expand deliberately. Never choose intentional Full as a
taxonomy migration tool. Renames touching variation identity remain gated.

## 15. M4 regression risks and required guardrails

- Informational attributes accidentally adding combinations or blocking child
  matching: explicit axes, unchanged legacy path and expected-child count tests.
- Loss of sixth-plus attributes or comma-bearing terms: lossless versioned
  projection, never a five-slot/CSV-only path for new semantics.
- Shared list union, whole-object override or empty pruning changing intent:
  dedicated adopted merge tests, explicit empty preservation and scoped rollout.
- Registry canonical names altering `.scanned` matching, modifiers or image
  paths: retain generation/source tokens; reviewed identity-affecting adoption.
- Hierarchy/slug changes binding wrong remote taxonomies: stable registry keys,
  parent-scoped verified identities, no blind rename or creation.
- New `variation` flags/visibility absent from freshness: digest all resolved
  managed taxonomy and registry revisions through Preview/final confirmation.
- Store scope or required-discovery failure being treated as absence: block
  uncertain identity, bound calls, preserve Safe Resume and local-only Unlink.
- Media/gallery regression: preserve per-variation ordered `image.id` and
  `gallery_image_ids`, strict readback, no parent gallery mixing and no `src`.
- Repeating the earlier test-environment problem: isolate Flask instance and
  recovery manifests in new tests; no Product Relationships fix is part of M5.

Parent prices/stock remain variation-owned as in M4; child prices, stock,
dimensions, verified global selections, trusted identity timing, missing-child
promotion, parent-only linking, safe unlink and two-pass relationship safety
are mandatory regression checkpoints, not redesign opportunities.

## 16. Documentation/roadmap audit and changes

Primary tracking: `docs/ROADMAP.md` (sequence/scope), `docs/CURRENT_STATE.md`
(delivered behavior). Technical authority: `ARCHITECTURE.md`, `DATA_MODEL.md`,
`DECISIONS.md`, `PRODUCT_INFO.md` plus runtime field inventory/schemas,
`SCANNER_CONTRACT.md`, `WOO_PUBLISH_PREVIEW.md`, `WOO_CONTROLLED_PUBLISHING.md`,
`WOOCOMMERCE_CONNECTION.md`, `PRODUCT_RELATIONSHIPS.md`, `COLLECTIONS.md`,
`CATALOGUE_OPERATIONS.md`, `CATALOGUE_INTAKE.md`, `STORAGE_RETENTION.md`,
`DOCKER.md`, `UNRAID.md`, `DESIGN_SYSTEM.md`; README is the entry point.

The inspected roadmap contains Phase 3 M1–M4 descriptions but **no numbered M5**.
It defers incremental synchronization/media/broad management to later work and
also retains older Phase 1/2/2.5 status wording. Do not invent a former M5 number
or reassign past milestones. CURRENT_STATE records Phase 2/2.5 v0.3.1 completion
but ends before the released Link/Unlink and gallery checkpoint. Some older
metadata/Woo docs still describe publishing as future or omit wc-admin default
category lookup; record dated superseding checkpoint notes, then update affected
contracts when implementing each slice.

This audit adds this report, a dated current-checkpoint/M5 section to ROADMAP,
a released M4/M5-planned status section to CURRENT_STATE, and an architecture
pointer. Historical milestone descriptions remain intact. No implemented M5
claims, API schema changes, private seed files or executable changes are added.

Every M5 slice must update ROADMAP and CURRENT_STATE and its affected contract
documents with exact delivered scope, changed schema/merge rules, evidence,
deferred decisions and next gate. Closure retains Product Relationships Pass 2
live acceptance, representative real-catalogue regression, final Discord
webhook/regression and final Phase 3 stable checkpoint. M4 recovery/Unlink is
accepted per the supplied checkpoint, not reopened as uncompleted work; retain
it in representative regression. Do not declare Pass 2 live accepted by inference.

## 17. Implementation slices and file impact map

These are subdivisions of newly scoped M5, not historical milestone numbers.

| Slice | Scope and gate | Likely files/modules |
|---|---|---|
| M5.1 Registry/config foundation | Approve schema; bounded read-only loader, immutable snapshot, structural validation, missing/invalid/read-only readiness. Offline only; no scanner/editor save integration or migration. | New `app/taxonomy_registry.py`, `app/resources/taxonomy/` schemas, `config.py`, focused tests; docs. Deployment mounts can follow in M5.2. |
| M5.2 Registry workspace | Authenticated local browse/import-preview, then explicit backed-up atomic registry save, collision/impact review and readiness. Optional mount/config/UI. No product metadata semantics change. | New taxonomy workspace/templates/JS; `routes.py`, navigation/settings; atomic/backup/operation utilities reused; Compose, Unraid XML, `.env.example`, image exclusions and Docker docs. |
| M5.3 Versioned resolver and projection | Review/approve minimal migration, implement opt-in merge/validation, complete structured metadata envelope, projection and reconstruction. Legacy rows/SKUs/images exact; no writable adoption UI until this passes. | `product_info.py`, schemas/inventory, new taxonomy resolver/projection modules, `models.py`, reviewed migration; scoped `scanner.py`, `csv_writer.py` only if needed, `ingest.py`, `reconstruction.py`, coordinator readiness. |
| M5.4 Controlled product editors | Read-only adoption diff first, then explicit scoped opt-in/save; searchable selections, primary roles, ranges, terms and variation designation, Advanced parity and empty values. | `metadata_workspace.py`, `routes.py`, `metadata_editor.html`, `metadata-editor.js`, Product/Collection read views, shared form validation; no Intake redesign. |
| M5.5 Reviewed Woo taxonomy sync | Settle range destination/capabilities. Shared bounded registry-driven taxonomy plan/confirm/execute/readback, category hierarchy, local store mappings and resume. | `woocommerce_connection.py`, narrowly shared taxonomy primitives from preview/publisher, new sync service/templates, operation/Discord adapters, proposed identity model from approved migration. |
| M5.6 Preview/publisher integration | Consume resolved controlled assignments, per-attribute variation use, full global terms/ranges, freshness/reconciliation; forbid unknown-vocabulary creation and retain all M4 safety. | `woo_publish_preview.py`, `woo_controlled_publish.py`, `woo_managed_comparison.py`, relevant templates; metadata/projection view adapters. |
| M5.7 Adoption/live acceptance/checkpoint | Representative offline reconstruction and approved live taxonomy/product selection; required review reports, documentation, final verification/publication only on separate authorization. | Focused/acceptance tests, project status/contract/deployment documentation; no unrelated features. |

A registry UI may precede scanner changes because it edits definitions only.
Product controlled-value saving must follow M5.3. If M5.5 is needed sooner for
capability evidence, perform read-only discovery separately; that never authorizes
product adoption or remote writes.

## 18. Focused test strategy

- M5.1: valid/missing/invalid/oversized roots/files, UTF-8/duplicate JSON keys,
  confinement, symlink escape, root overlap, key/slug/alias collisions, category
  cycles/parents, attribute-scoped term uniqueness, deterministic digest and
  immutable snapshots; prove no writes/network/startup scan dependency.
- M5.2: auth/CSRF, keyboard selection, draft preservation, stale-digest rejection,
  read-only mount, atomic old-or-new registry visibility, verified backup failure,
  explicit seed dry run, bounded notifications and lock contention.
- M5.3: legacy frozen scanner rows unchanged; explicit versus absent/empty axes;
  Simple informational attributes; Variable informational attribute does not add
  children; more than five attributes and comma/Unicode terms round-trip;
  sparse inheritance, selected image axes, unchanged marker/SKU/modifier identity,
  complete-parent rollback, invalid exhaustive scans, reconstruction parity and
  proposed migration backup/restore. No Woo dependency.
- M5.4: actual shared/override routes, full resolved impact before opt-in, explicit
  primary/secondary/extras, unknown add-new workflow, exact registry refs, Advanced
  JSON equality, empty/null preservation, no hidden scan on draft validation;
  desktop/tablet/390px structure and accessibility using existing frameworks.
- M5.5: mocked parent-first categories, exact scopes/slug collision, denied or
  truncated discovery never equals absence, global attributes and scoped terms,
  retained create/retry, store change, stale registry, no DELETE/media/product
  writes; range adapter only for a proven approved REST contract.
- M5.6: known identity and exact-SKU Preview, No Change versus genuine taxonomy
  drift, default-category legacy behavior, Simple/Variable informational values,
  per-axis parent flags, real child POST/readback, media/gallery isolation,
  missing-child resume, Link/Unlink, relationship safety and query/request caps.
- M5.7: one agreed final regression at the checkpoint, isolated instance/mounts,
  then separately approved live taxonomy and product acceptance. No real store
  in automated tests; no full-suite repetition per edit.

This audit ran read-only seed consistency inspection and documentation diff/link
checks only. It did not run application test suites, scans, migrations, builds,
live Woo requests, commits, tags or pushes.

## 19. Decisions and open gates

**Already directed:** five distinct concepts; filesystem definitions plus product
assignments; no authored Woo IDs; local/offline scanner; same global attribute
with per-product variation use; controlled creation; preserve M4; documentation
at every slice. These do not need re-approval as general principles.

**Dale's approval needed before dependent implementation:** single registry file
and proposed key/label reference schema; opt-in replacement/empty-list merge
semantics; primary-category enforcement timing and multiple ranges; range-to-Woo
destination/merchandising meaning; deprecation/rename policy; proposed normalized
projection and store-taxonomy migration; explicit-sync default versus reviewed
registry-approved on-demand creation. M5.1 can proceed only on approval of its
own contract; later choices need not block the pure loader.

**Answerable by implementation inspection/tests:** exact module extraction,
index/DDL shape and snapshot caps, scope of shared editor/Intake validator reuse,
how existing fixtures preserve scanner parity, dependency tracking to mark
affected projections stale. **Needs later store/environment evidence:** Brands
route/permissions and theme presentation, archive/filter behavior, actual
existing taxonomy collisions. **Needs missing design input if still relevant:**
`woocommerce-navigation-structure.md`. None justifies live access in this audit.

## 20. Recommended next Codex prompt — first slice only

> Implement Phase 3 M5.1, Taxonomy Registry/config foundation only, after approval
> of the M5 audit's single-file registry contract. Start from the current working
> tree, preserving the documentation audit and untracked dev-fixtures. Read
> docs/PHASE_3_M5_TAXONOMY_AUDIT.md, ROADMAP and CURRENT_STATE. Confirm develop and
> inspect existing changes; do not restart M4 or change its behavior.
>
> Add environment/config TAXONOMY_ROOT (default /taxonomy) and a bounded, read-only
> registry loader for registry.json with a versioned schema, stable local keys,
> categories with parent keys, storefront collection definitions, global
> attributes with scoped terms, tags, explicit slugs and lifecycle/aliases.
> Implement pure validation, immutable operation snapshots/content digests and
> missing/invalid/read-only/ready diagnostics. Reject duplicate JSON keys, cycles,
> missing references, normalization collisions and unsafe root/path/symlink
> access. Preserve original display text and never generate Woo IDs. Do not
> create a fallback registry or load it as a new startup requirement.
>
> Use small fictional tests. Supplied TLC seeds stay read-only external references;
> no automatic import or product assignment writes. No UI mutations, scanner or
> ingestion changes, editor semantics changes, Woo calls/sync/publishing, migration,
> dependency, Docker/Unraid edits, relationship work or M5.2 implementation.
> Run only focused registry tests and necessary config regression, Python syntax
> and git diff --check. Update roadmap/current state and the registry contract
> with actual scope/results/remaining gates. Do not run the full suite, commit,
> tag, push or build. Return exact files, tests and unresolved contract choices.
