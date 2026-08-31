# Current State

This document records the completed Phase 1 (`0.2.3`) catalogue-integrity release, the completed Phase 2/2.5 (`0.3.1`) catalogue-management release, and current Phase 3 development. Phase 1 builds on the Phase 0 baseline without changing protected scanner row semantics.

## Startup and setup

`run.py` creates the Flask application. The application factory configures Flask-SQLAlchemy, Flask-Login, CSRF protection, the main blueprint, and upgrades SQLite to the current Alembic migration head. `db.create_all()` is no longer used. Missing databases are initialized from migrations; a matching unversioned Phase 0 database is backed up and adopted at the frozen baseline.

A new installation follows `/` → `/setup` → `/initial-settings` → `/initial-scan`. Setup creates the initial administrator and stores the product root, output root, and public image URL prefix. The initial-scan screen classifies the configured catalogue as new, requiring reconstruction, ready, or ambiguous. It shows collection/product/marker/projection counts and enables only a safe recommended action.

Production startup requires an explicitly supplied non-placeholder `SECRET_KEY`.
The application never generates or persists it. `/app/instance`, `/catalogue`,
and `/output` remain three separate required mounts; storage, backup, operation,
temporary-file, and Docker-log limits are documented in
[Storage and Retention](STORAGE_RETENTION.md).

Phase 2.5 optionally recognizes a fourth, real `/intake` mount for authenticated
pre-catalogue image previews. The image and entrypoint do not create a fallback
directory. Missing intake storage never blocks startup or the established app;
the Catalogue Intake workspace simply reports unavailable.

## Catalogue Intake

Phase 2.5 Milestones 2–6 add authenticated routes at `/image-preparation`,
`/image-preparation/group`, `/image-preparation/folders`, and
`/image-preparation/rename`, plus `/image-preparation/metadata`. RC4 adds the
separate `/image-preparation/import-structured` path for a complete existing
folder hierarchy. It copies a digest-revalidated tree through hidden staging
into a new suffix-safe Prepared result while preserving the source. Review mode
enters folder review; final-structure mode enters image renaming after stricter
structure validation. Existing metadata is preserved byte-for-byte and does
not cause later stages to be skipped. These workspaces browse only
the canonical `/intake` root and render intake-relative breadcrumbs, supported,
hidden, corrupt, unsupported, unreadable, and unsafe-entry counts.

Grouping previews show the exact legacy trailing-number base beside the trimmed,
safe proposed folder, identify single-image groups, case/normalization conflicts,
and scanner-reserved Parent proposals, and display every future destination below
`Prepared/<source folder>/`. Rename previews validate an independent filename
prefix, show legacy and recommended names, hierarchy components, Parent ownership,
sequence scope, complete destinations, and global flattened-output collisions.
Visible collection metadata improves compatibility confidence; missing metadata
is reported rather than guessed.

All discovery and proposal ordering is deterministic. Request-scoped proposal
digests change with safe source identity or proposal inputs. Previews write no
files, folders, thumbnails, metadata, scanner markers, database rows, operation
history, or Discord events. They do not invoke the scanner or browse `/catalogue`.

A valid grouping preview may now be explicitly confirmed. The server recomputes
the proposal and digest, acquires the dedicated Intake mutation lock, copies
unchanged source images into private operation-owned staging, verifies the exact
tree, and atomically promotes it without replacement to a duplicate-safe
`Prepared/<source basename>/` result. Source files remain unchanged. Bounded
operation progress and one terminal Discord summary are recorded; notification
failure is non-fatal. Group names remain provisional, and the completed status
is **Grouping complete — folder review required**.

The Folder Naming and Structure Editor now advances the same visible Prepared
working result rather than creating another normal-progression copy. It supports
validated collection/product/variation/Parent renames, new empty folders, and
explicit removal of empty proposed folders. Case/Unicode collisions, unsafe
paths, duplicate Parent variants, non-empty removal, unsupported depth, and
future flattened filename collisions block confirmation. Swaps and case-only
renames are isolated inside operation-owned staging; unchanged image bytes and
the complete tree are verified before rollback-protected same-name promotion.
The terminal status is **Folder structure confirmed — image renaming required**.

An eligible folder-confirmed result can then be renamed in place through the
same hidden staging/rollback model. Final filenames use a validated normalized
prefix, all scanner-relevant hierarchy components, collection-root Parent
ownership, deterministic per-directory sequences, and lowercase source
extensions. A two-stage temporary/final rename supports cycles and case changes
without overwrite. Complete paths, image readability, count, and bytes are
verified before promotion. Proven superseded predecessors may be removed only
after explicit acknowledgement and verified success; uncertain or referenced
lineage is preserved. The terminal status is **Images renamed — metadata
required**.

The Prepared Metadata Builder then creates or corrects the authoritative shared
`product_info.json` in that same working result. It reuses the established
schema and complete collection-field inventory, offers guided and actual-authored
Advanced JSON modes, preserves unknown authored content, and validates folder
hierarchy without invoking the scanner. Save uses deterministic digest
revalidation, the shared Intake lock, hidden staging, atomic metadata writing,
unchanged image/tree verification, and rollback-protected same-name promotion.
The terminal status is **Metadata complete — validation required**.

Final validation and catalogue handoff now revalidate the complete Prepared
tree, metadata, image readability and scanner-facing hierarchy, show the exact
catalogue-relative create/replace destination, and require explicit
acknowledgement. Single Variable image depth follows only the ordered
`image_attributes`: exact sources are Ready, scanner-supported broader sources
and Parent preview fallback are warnings, and genuinely unresolved sources are
blocking. Non-image attributes do not create image-folder levels. The existing catalogue/scanner lock is acquired before the
Intake mutation lock. A byte-identical copy is verified in hidden catalogue
staging; replacement uses protected rollback and no merge. The Prepared result
remains unchanged. Success records **Catalogue handoff complete** and directs
the user to **Run Append Scan** manually. No scan, marker, SKU allocation,
database projection, output write, conversion, or upload occurs.

Catalogue Intake completion and Prepared-result views derive a single prominent
next action from that durable state. Signed result tokens are revalidated on
every navigation request, so stale, missing, failed, interrupted,
recovery-required, and ineligible results cannot enter a later stage. The
mapping is folder review → image renaming → metadata creation/editing → final
validation → Scanner. These GET links perform no mutation, and opening Scanner
after handoff does not start Append Scan.
See [Catalogue Intake](CATALOGUE_INTAKE.md).

## Scanner modes

- **append** processes products without `.scanned` and products carrying `.update`.
- **update** uses the same selection rule and reuses parent/variation SKUs from `.scanned`.
- **full** forces processing and regenerates SKUs using index counters.
- **shared collection refresh** explicitly forces every product in one collection
  while reusing marker identities. A shared JSON editor save invokes this mode;
  ordinary append and individual-update selection remain unchanged.
- **reconstruction** resolves the complete catalogue with marker and database
  identity reuse, disables SKU-index reset, creates a persistent SQLite backup,
  then updates the full projection in one controlled transaction.

Intentional full regeneration remains a separate warning-labelled action. The UI
and route require explicit confirmation because it retains the scanner's existing
SKU-reset behaviour. An empty database never selects it automatically.

The scanner supports exact collection types `Simple`, `Variable Collection`, and `Single Variable`.
Resolved product titles now apply the documented product/shared/folder fallback
contract, treating blank authored titles as absent. Collection display identity
continues to come from the collection folder basename. Append assignment is
protected by isolated multi-collection regression coverage and continues to use
portable source provenance rather than either title.

## Operation control

Append, product update, shared collection update, full, and reconstruction operation types share a non-blocking process-local lock. A conflicting request receives HTTP `409` with the active operation type and identifier before it changes catalogue files. Operation history is persistent and records bounded diagnostic fields and lifecycle counts; startup marks unfinished rows interrupted and requiring review. This control is intentionally limited to the documented single-worker, single-replica runtime.

Routine successful history is retained for at least 180 days and the newest
1,000 entries; resolved failure history is retained for at least 365 days.
Active, pending, unresolved-recovery, and newest-per-type records are protected.
Process memory retains 20 ordinary completed runs, while active/recovery runs and
their completion summaries remain protected. Each live log queue is bounded to
2,000 lines and approximately 2 MiB.

Phase 2 Milestone 7 presents this control through authenticated Scanner,
Operations history, and Operation Detail workspaces. Canonical starts expose
only Append, Update, and intentional Full, require explicit confirmation, and
repeat safe mount/database/lock checks server-side. Operations are sorted and
paginated in SQLite; detail combines durable summaries/items with bounded
process-local progress and redacted log snapshots. Safe retries return to the
confirmed Scanner flow and create a new history record. Cancellation remains
unsupported because process termination would weaken marker/database recovery.
See [Scanner, Operations, and Discord](SCANNER_OPERATIONS.md).

Ordinary scan ingestion adds one operation item per emitted parent. Successful items are committed with their parent transaction. A failed parent is rolled back and receives a separate sanitized failed item; the operation becomes `partial` when other parents succeeded or `failed` when none did.

Production scans stage `.scanned.pending` before database ingestion and finalize `.scanned` only after the corresponding parent commits. Database failures retain/recreate `.update`; marker-finalization failures retain pending identity. The next operation finalizes already committed intents before scanning and retries only unresolved products with preserved parent/variation SKUs. Marker and index JSON replacements are atomic.

Reconstruction does not rewrite a valid `.scanned`, reset `sku_index.json`, or
remove an existing `.update`. Database identity overlays supplement old marker
payloads for newly discovered variation combinations, so repeated reconstruction
does not allocate another SKU. Only genuinely unmarked products stage and finalize
a new marker. Outstanding pre-existing pending state makes the result partial and
`recovery_required` rather than falsely successful.

The authored `product_info.json` contract is now represented by collection and
partial-override JSON Schemas, a complete field inventory, fictional examples,
minimal/type-specific templates, and an authenticated in-app reference. Editor
saves validate before any backup, marker, operation lock, or scan side effect.
This is intentionally not scanner-wide strict enforcement; protected inheritance,
aliases, unknown collection types, and known discrepancies remain unchanged.

## Verified catalogue/database consistency

At the audit baseline, the live local catalogue and SQLite database agreed for 11 parent SKUs and 49 variation SKUs. Database integrity passed. Titles, types, mapped prices, dates, dimensions, descriptions, images, variation attributes, and supported modifier results agreed for the currently ingested subset.

The real catalogue and database are never part of the repository or container image.

## Web UI

Authentication, initial settings, the initial scan screen, collection-grouped
Products browser, raw JSON viewing, JSON editor, and metadata reference exist.

Phase 2 Milestone 1 adds an original responsive application shell based on
semantic design tokens. Bootstrap, application
JavaScript, and the project-owned SVG icon sprite are served locally without a
runtime CDN dependency. The permanent shell uses a desktop sidebar, compact
tablet rail, and a five-destination mobile bottom bar whose More action opens an
accessible off-canvas menu. Workspaces not implemented yet render explicit
`Planned` pages instead of missing templates or misleading functionality.
Legacy route aliases resolve safely. The setup folder browser is authenticated.

`DESIGN_SYSTEM.md` supersedes the interim Milestone 1.1 dark-first treatment.
The application is now light-first: warm canvas, white cards/forms/tables, dark
primary actions, restrained lime and teal, and deep-slate feature panels only
where they clarify hierarchy. JSON textareas, metadata examples, scanner logs,
table group headers, mobile navigation, and honest summary panels retain a
purposeful dark treatment. The original project logo is not redesigned.

Phase 2 Milestone 2 completes the local setup journey without redirecting away
from its result. Initial append/full scans and identity-preserving
reconstruction now finish on an honest summary with projection totals,
warnings, failures, and routes to Dashboard, Products, or the operation detail
already present on the page. A shared accessible operation-progress component
is used by setup and existing product metadata updates.

The scan runner's process-local progress payload retains its Phase 1 keys and
adds presentation-only operation type, stage, current collection, elapsed time,
collection/product/variation counts, warnings, and failures. These observations
do not change scanner selection, row resolution, operation locking, marker
coordination, ingestion, or persistent operation history. Reconstruction
remains synchronous in Phase 2 and publishes the same normalized completion
shape only after its existing controlled operation returns. Durable progress,
background reconstruction, and the full Operations workspace remain deferred.

Phase 2 Milestone 3 replaces the Dashboard placeholder with a read-only
catalogue-health view derived from the existing SQLite projection,
`CatalogueOperation` history, and the process-local operation state. It reports
real collection, parent-product, variation, active/missing, override, metadata
gap, recent-operation, and recently updated-product facts. Catalogue
availability is the share of projected parent products and variations whose
`catalogue_status` is `active`; metadata gaps are simple field-completeness
signals for active parents, not schema validation. Empty databases have honest
initial-scan actions. The Dashboard does not invent trends, sales, users, or a
WooCommerce connection, and does not add persistence or scanner side effects.

Phase 2 Milestone 4 replaces the flat Products table with a read-only,
collection-grouped browser over the existing SQLite projection. Collection
headers report filtered parent, variation, active/missing, and last-update
facts. Parent rows show genuine type, SKU, projected price/range, lifecycle,
thumbnail/fallback, metadata provenance, variation count, timestamp, and the
existing metadata actions. Title/SKU, collection, type, lifecycle, metadata
source, and Dashboard metadata-issue filters are URL-backed and parent results
are paginated on the server. Variation attributes, price, stock quantity,
lifecycle, provenance, and timestamps are fetched only when a variable parent
is expanded. Desktop uses grouped relational rows, while tablet and mobile use
collection-preserving product cards. This milestone adds no model, migration,
scanner, ingestion, marker, SKU, or filesystem behaviour.

The Milestone 4 image-display follow-up serves genuine source images from the
mounted catalogue through authenticated opaque product and variation routes.
The catalogue file is authoritative; Woo-facing `.webp` URLs are hints and may
differ from the source extension or name. Simple and variable parent thumbnails
follow scanner image order, then safe folder discovery. A variable parent with
no usable parent image uses its first ordered valid variation source, while an
expanded variation keeps its variation-specific source and uses the parent only
as a fallback.

Single Variable parent media is discovered from the reserved collection-root
`parent` directory using case-insensitive recognition (`parent/`, `Parent/`,
`PARENT/`, or mixed case). Actual source casing is preserved, and multiple
case-variants are rejected as ambiguous before attribute-folder interpretation.
Parent and variation ownership stays separate in markers,
emitted rows, ordered URL galleries, portable source assets, reconstruction,
and authenticated UI previews. Generated output files are not required for UI
resolution after ingestion.
Products and Dashboard Recent Products share the same safe
fallback presentation.

The Dashboard and Products "Missing images" signal means an active parent has
no safe, resolvable source image on either the parent or any variation. It does
not treat a missing emitted URL as proof that source imagery is absent, and it
does not invent variation-level completeness diagnostics. Woo-facing image
references remain ordinary SQLite text; source image bytes are not copied to
SQLite, `/app/instance`, or the production image.

Phase 2 Milestone 5 adds an authenticated resolved Product Detail workspace and
guided source editors without changing metadata ownership. Product Detail is
read-only: it combines the SQLite parent/variation projection with the
collection metadata source, optional partial product override, bounded relevant
operation history, and catalogue-backed image diagnostics. Parent identity is
shown before ordered variation children. Large variation sets render 24 at a
time and load further pages on demand.

Collection Metadata editing targets the one collection-level
`product_info.json` that may affect several products. Its affected-product
preview is bounded and paginated. Product Override editing targets only the
optional partial document for one product; inherited collection values remain
visible but are written only when the user explicitly enables an override.
Removing an enabled override field reveals its inherited value, and `{}`
remains a valid minimal override. Both editors default to guided contract-aware
fields and expose a deliberate Advanced JSON mode with parsing, formatting,
search, line numbers, highlighted preview, schema validation, duplicate-save
protection, and unsaved-change warnings.

Every save still uses the established validation, operation lock, collision-safe
metadata backup, atomic replacement, `.update`/shared-refresh orchestration, and
scanner projection workflow. Product and variation rows are never edited as the
authoritative source. Source references shown in the UI are catalogue-relative;
absolute host/container paths and temporary `/output` identities are not shown.
Ordered parent and variation image diagnostics keep ownership distinct and show
the stored final website URL as read-only text. The app does not upload,
convert, regenerate, or remotely verify images.

Local catalogue lifecycle and future publishing intent are presented as
separate concepts. `Product.catalogue_status` supplies Active, Missing, and
other existing local lifecycle labels. Resolved metadata `live` supplies
Published or Draft intent and records whether the value came from a product
override, collection metadata through inheritance, or the scanner default.

Phase 2 Milestone 9 completes the targeted release-candidate pass: canonical
product-title fallback, deterministic Append collection-assignment regression
coverage, shared centred action content, UK English interface copy, and mocked
Discord event/routing verification. Scanner cancellation remains unsupported;
only one catalogue mutation may run; live progress persists independently of the
browser; Discord delivery detail is not guaranteed across every restart;
multi-replica mutation execution, WooCommerce synchronisation, image upload or
conversion, filesystem collection management, confirmed pre-catalogue file mutation,
and remote media management remain outside Phase 2. Phase 3 Milestone 1 is
described below.
`Product.published` is the normalized projection of that resolved intent; it is
not evidence that a product currently exists or is published in WooCommerce.

Phase 2 Milestone 6 replaces the Collections placeholder with an authenticated,
server-backed Collections browser and resolved Collection Detail workspace.
Existing `Collection.id` supplies safe route identity while portable relative
paths supply provenance. Browser cards aggregate genuine product/variation,
lifecycle, publishing-intent, override, metadata-health, image-coverage, and
last-update facts with search, filters, sorting, and pagination. Collection
Detail summarizes the authoritative shared metadata source, affected products,
image diagnostics, and bounded scoped operation history, and links to Product
Detail and the existing Collection Metadata Editor. It adds no model or
migration and performs no collection filesystem mutation. See `COLLECTIONS.md`.
Collection-facing UI consistently uses the folder basename from portable
collection provenance as its title. The shared JSON `title` remains product
metadata, and changing it does not rename a collection or alter its integer route
identity. Nested duplicate basenames remain distinct and are disambiguated with
safe relative provenance where needed.

## Database projection

- The active path populates Collection → Product → Variation for all three exact collection types.
- Portable catalogue-relative source and JSON paths are stored separately from runtime absolute paths.
- Every emitted parent and variation row is retained as JSON; commonly queried pricing, inventory, publication, taxonomy, SEO, image, and attribute values are also normalized.
- Existing Product, Variation, and Woo placeholder identities are retained during ordinary row updates.
- Every committed parent's emitted variation set is authoritative. Stale
  variations become `missing` rather than being deleted, and matching rows are
  restored in place if emitted again.
- Products become `missing` only after a completely resolved and successful full
  scan, reconstruction, or collection-limited shared refresh. Append and
  individual product updates never reconcile unseen products.

Missing rows retain internal/SKU/Woo identity, provenance, relationships, and
timestamps. Product restoration matches portable `source_relpath` before SKU;
variation restoration matches its emitted attribute identity before SKU.

Scanner characterization also confirms that variation modifier sale prices are not emitted by the variation row builder, authored shipping class is emitted as blank, list ordering is not stable, unknown collection types yield no rows, editor relationship-key names differ from the row builder, and Woo rows are limited to five attribute slots. These remain protected discrepancies pending separate contract decisions.

## Integrations

WooCommerce-compatible rows and future Woo ID columns exist. Phase 3 Milestone
1 adds a read-only WooCommerce/WordPress REST discovery client, but no publishing
or remote mutation. Optional Discord webhooks can receive
scanner start, clean/warning completion, failure, metadata, override, and the
existing product-ingest events. Delivery uses bounded process-local retry and
does not alter scanner success. Delivery state is not durable after restart.

## Known operational risks

- Multi-worker or multi-replica catalogue mutation is not supported; the lock is process-local.
- Scanner execution remains process-local, while bounded live progress and logs
  are persisted with the operation for safe cross-worker presentation.
- `/settings` is an authenticated, read-only view of safe application state,
  mount health, scanner locking/progress, Discord configuration booleans, and
  existing retention rules. It never renders configured paths, webhook values,
  secrets, or an environment dump. Future modules remain availability-aware
  placeholders until their approved milestones are implemented.

The protected scanner discrepancies and intentional full-scan semantics remain
unchanged.

Catalogue Intake warning-only completions retain their next-step navigation.
The shared helper requires zero blocking/failure findings, revalidates the
durable Prepared identity and current stage, and leaves destination validation
authoritative. Bounded grouped warning details appear on Prepared-result cards,
Operation Detail, and completed handoff review without changing validation,
  mutation, scanning, or Discord behavior.

Phase 3 Milestone 1 adds the authenticated `/woocommerce` workspace. Optional
store URL and API credentials are read exclusively from `WOO_STORE_URL`,
`WOO_CONSUMER_KEY`, and `WOO_CONSUMER_SECRET`; missing configuration does not
block startup. Opening the workspace is offline. The explicit Test Connection
action creates one retained operation, discovers the public WordPress REST index,
selects the highest advertised `wc/vN` namespace, and performs minimal bounded
authenticated GET checks for publishing and later resource groups. A central
request guard rejects mutation, TLS verification is enabled, redirects remain
same-origin, and raw indexes/responses are never retained. Verified reads,
advertised write methods, and unverified credential write permission are shown
as separate concepts. Woo publishing, upload, synchronization, relationships,
orders, and remote mutation remain unimplemented.

The Phase 3 Milestone 1 API-index compatibility hotfix applies an explicit 8 MiB
decompressed limit only to the public WordPress `/wp-json/` discovery index.
Ordinary capability reads remain capped at 1 MiB. Streaming counts decompressed
chunks regardless of `Content-Length` or content encoding, closes responses on
abort, and reduces the decoded index to bounded relevant route/method summaries
before any operation state is retained.

New Woo connection tests persist bounded structured capability-limit findings,
including safe status codes and roadmap-derived current/future impact. Exact
limitations now appear in the Woo workspace, Operation Detail, bounded logs,
and one grouped Discord terminal summary. Historical count-only operations retain
a controlled detail-unavailable message. The shared light-card primitive also
resets inherited foreground text to the design-system light-surface token, so
light health metric cards remain readable inside purposeful dark panels.

Phase 3 Milestone 2 adds an entirely local Product Relationships editor to
Product Detail. Ordered cross-sell and upsell SKU lists are authored in the
appropriate catalogue JSON and projected for bounded database search and preview,
prevent self/duplicate/invalid links, retain broken references for repair, and
support recoverable all-file mutual cross-sell families. Scanner-emitted relationship text
columns remain untouched. No Woo request, payload generation, media action,
scanner invocation, or Catalogue Intake mutation occurs. A later two-pass
publisher will persist Woo product IDs and then translate these local edges.

The Milestone 2 follow-up adds the authenticated `/relationships` catalogue
workspace and signed mutual cross-sell family builder. JSON remains the durable
source and SQLite remains a reconstructable, searchable projection. Revision
`0006_relationship_workspace` adds indexed source-kind and last-change metadata
without changing authored relationship shape or scanner fields.

Phase 3 Milestone 3 adds an authenticated `/woocommerce/preview` workspace.
Opening it is offline; only explicit preview generation performs bounded,
cached Woo GET requests. The in-memory plan maps resolved local metadata to the
managed Woo v3 product and variation fields, classifies exact-ID/exact-SKU
identity outcomes, compares only managed remote fields, and separates Pass 1
identity-producing work from Pass 2 relationship IDs. Complete payloads and raw
responses are not persisted. Operation history retains only bounded counts,
store hostname/fingerprint, builder/mapping versions, and deterministic source
and plan digests. Revision `0007_woo_sync_identity` provides the minimal
store-scoped identity projection needed by a later controlled publisher. No Woo
write, taxonomy creation, media upload, ID linking, or publication exists.
