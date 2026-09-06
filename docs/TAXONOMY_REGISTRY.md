# Local taxonomy registry contract — version 1

M5.1 implements read-only loading and validation. M5.2 adds authenticated local
registry editing and reviewed bootstrap; the version-1 file contract is unchanged.
ROADMAP and CURRENT_STATE track slice status. M5.3 adds local product assignment
resolution/editing below without changing registry v1. Woo sync remains deferred.

## M5.3 local assignments (implemented, not a registry schema change)

Definitions belong in `registry.json`; assignments remain in existing
`product_info.json` files. Registry keys and Woo IDs are not written into product
assignments in this slice. Registry choices use full category paths and readable
attribute/term names. Unique historical names/aliases can match for display;
unknown or ambiguous values remain visible as **Legacy · Not in taxonomy registry**.
Matching does not rewrite values. Scan never imports or normalizes vocabulary.

```json
{
  "attributes": {
    "Occasion": ["Birthday"],
    "Size": ["Small", "Large"]
  },
  "variation_attributes": ["Size"]
}
```

Missing `variation_attributes` is legacy. A present list is explicit opt-in;
`[]` means no drivers and survives guided/Advanced saving and override merging.
Only exact assigned names are allowed; duplicate/missing names fail validation.
Simple products allow informational attributes with an empty driver list.
Explicit drivers are limited to five by the existing child-row representation;
the local informational-attribute projection is not restricted to five. Existing
Single Variable image axes must stay driving when generating children. Existing
SKU, folder, image ownership/order and modifiers are not redesigned.

Collection defaults and sparse overrides retain their existing category/tag
additive semantics and attribute-object replacement semantics. Driver arrays
replace, never union. To remove an inherited category, edit its collection source;
an unrelated override save never copies inherited taxonomy into the override.

The metadata editor shows saved/resolved recognition separately from its draft.
Choose a registry category, or a scoped attribute and term, to add an assignment;
remove the old draft assignment to replace it. Existing unrecognised rows remain
editable. Registry creation/adoption links prefill a proposal, never write.
Complete the existing registry review/acknowledgement first, then refresh choices
in the still-open metadata editor, select and save separately. Draft/active
definitions are selectable; deprecated definitions are retained in existing
assignments but excluded from new choices. Registry creation alone is not product
adoption or Woo approval. Term links always use their selected attribute key.

Registry failure leaves assignments untouched. Metadata failure after registry
success leaves the definition available, not destructively rolled back. Advanced
JSON remains supported and preserves unknown vocabulary; no automatic seed/import
or mass migration is performed. The complete fictional example/template now
demonstrates explicit drivers; ordinary legacy templates remain unchanged.

**Temporary Woo guard:** products resolving an explicit driver field cannot be
previewed/published until informational-versus-variation mapping is implemented
in the later publisher slice. Both empty and non-empty lists are guarded before
Woo requests. Legacy products keep M4 behaviour. Taxonomy sync, Storefront range
destinations, primary/secondary category roles and broader assignment versioning
remain future work, not implied by the local resolver.

## Configuration and invocation

`Config.TAXONOMY_ROOT` reads the `TAXONOMY_ROOT` environment variable at normal
configuration import time; its default is `/taxonomy`. An explicit empty value
means not configured. The app does not create this directory or a registry file,
does not load it during startup, and does not require it for existing workflows.
Changing the environment in a running deployment requires normal config reload.

`app.taxonomy_registry.load_configured_registry()` explicitly loads the current
Flask-configured registry. It checks separation from the configured catalogue,
output, instance and Intake roots using existing local Settings/configuration.
It performs no database writes and no Woo calls. The core
`load_registry(root, excluded_roots=...)` API is independently usable without a
Flask context or database. Roots are trusted application configuration, never
arbitrary browser input. The only filename it accepts is `registry.json`.

Use a separate absolute POSIX directory. `/`, relative paths and `..` components
are rejected. Every directory component and the registry file are opened without
following symlinks; configure a physical path rather than a symlink alias (for
example, on macOS use `/private/tmp/...` rather than `/tmp/...`). Directory handles
pin the file read to the inspected path. Non-regular files, including FIFOs,
are rejected without waiting for a writer. Default runtime roots and explicitly
supplied/current app roots cannot contain, equal, or be inside the taxonomy root.

M5.1 did not modify deployment. M5.2 adds an optional Compose overlay and Unraid
path/config declarations; see [Docker](DOCKER.md) and [Unraid](UNRAID.md).
A read-only mount is sufficient for the loader; confirmed edits require write
permissions. Neither the image nor startup creates `/taxonomy` or a fallback.

## Document shape

The exact JSON Schema is
`app/resources/taxonomy/registry.schema.json` (Draft 2020-12 using the existing
jsonschema dependency). All five top-level properties are required:

```json
{
  "schema_version": 1,
  "categories": [
    {"key": "stationery", "name": "Stationery", "slug": "stationery", "state": "active", "parent": null}
  ],
  "storefront_collections": [],
  "attributes": [
    {
      "key": "finish", "name": "Finish", "slug": "finish", "state": "active",
      "navigation": true, "visible_default": true,
      "terms": [{"key": "matte", "name": "Matte", "slug": "matte", "state": "active"}]
    }
  ],
  "tags": []
}
```

This is a fictional structural example, not a production seed. Empty definition
arrays are valid during authoring; readiness means structural availability, not
that production taxonomy has been populated or approved. The real TLC seed must
later be imported through a separately reviewed workflow. M5 does not end with
an empty production registry, and M5.1 does not auto-import any reference files.

Every definition has `key`, `name`, `slug`, and `state`. Optional properties:
`order` (integer 0–1,000,000), `aliases` (at most 20 labels). State is `draft`,
`active` or `deprecated`; these are validated data, not implemented publishing
permission or lifecycle-editing behavior. Unknown properties are rejected,
including Woo IDs. No names/slugs/keys are generated or silently repaired.

- Top-level stable keys are unique across all four kinds. Keys start with an
  ASCII lowercase letter, followed by lowercase letters/digits/underscore/hyphen,
  at most 96 characters. They must remain stable when display names change;
  cross-revision rename enforcement belongs to the later writer.
- Terms use the same definition shape; their keys, labels and slugs are scoped
  to the containing attribute. A term key may repeat under another attribute.
- Names/aliases are nonblank Unicode text, maximum 191 characters, without ASCII
  control characters. NFC normalization, casefold and whitespace collapse are
  used only to detect label/alias collisions. Authored strings remain unchanged.
- Slugs are explicit lowercase ASCII alphanumeric segments separated by hyphens,
  maximum 96 characters. Uniqueness is per taxonomy kind, or per attribute for
  terms. These are local schema constraints, not a claim of complete Woo-specific
  slug/configuration validation; remote attribute constraints belong to sync.
- Categories additionally require `parent` (null or another category key).
  Missing/wrong-kind parents and cycles are rejected. Names cannot contain `>`.
  Names are resolved to full ` > ` paths for collision checking; repeated leaf
  names under different parents are allowed. Category aliases represent full
  historical paths, not unscoped leaf synonyms. Alias/path collisions are errors.
- Attributes additionally require boolean `navigation`, boolean
  `visible_default` and a `terms` array. Neither boolean designates variation use.
  This slice has no product assignments or variation-generation semantics.

Limits: 1 MiB file size, 2,000 total definitions including terms, at most 500
terms per attribute, 32 category levels, 32 levels of JSON nesting and 30,000
parsed value nodes. JSON object keys are unique at every depth. UTF-8/malformed
JSON, non-finite numbers, unsupported versions and unsafe structures are rejected.
Only the first bounded validation issue is returned, without raw values or host
paths. No raw exception text is surfaced. A file modified in place while read
returns `changed_during_read`; no invalid/stale-cache fallback is used.

## Result and snapshot

`RegistryResult` is frozen and exposes `status`, `issues` (tuple), `snapshot`
(or null), and `available`. `RegistryIssue` is frozen with `code`, `path` and
safe `message`. Paths describe `registry.json` or schema locations, not host paths.

| Status | Meaning | Snapshot available |
|---|---|---|
| `not_configured` | Empty/unset explicit loader root | No |
| `root_missing` | Configured directory does not exist | No |
| `registry_missing` | Directory exists but registry.json does not | No |
| `invalid_root` | Unsafe root, overlap, symlink/non-directory ancestor | No |
| `invalid` | Unsafe file, bad JSON/version/schema/references/collisions/limits | No |
| `inaccessible` | Read denied/failed or content changed during read | No |
| `read_only` | Valid snapshot, observed storage not writable | Yes |
| `ready` | Valid snapshot, observed storage writable | Yes |

Writability is observed through access/mode checks, never a probe write; it is
not a promise that a future save succeeds. This entire slice is read-only even
when the status is `ready`. Snapshot construction freezes nested dictionaries as
mapping proxies and arrays as tuples. No mutable parsed references escape.

The SHA-256 digest is computed from canonical JSON with sorted object keys and
compact separators. Whitespace/indentation/object-key ordering do not change it;
array order and authored content do. The loader does not normalize away content
changes or insert missing optional defaults. Each explicit call reloads; a prior
snapshot remains immutable even when the authored file changes.

## M5.2 workspace and review contract

Authenticated routes (all POSTs use existing global CSRF protection):

| Route | Behavior |
| --- | --- |
| `GET /taxonomy` | Readiness, schema/digest, counts, latest local operation; kind/search/page filters, 25 definitions per page. |
| `GET/POST /taxonomy/edit/<kind>` | Guided add/edit/remove proposal for categories, storefront_collections, attributes, terms or tags. `key` selects a definition; terms require `attribute` scope. POST validates and reviews, never saves. |
| `GET/POST /taxonomy/advanced` | Full source editing, preserved invalid drafts, format/search/line numbers/safe syntax preview; explicit replacement review. |
| `GET/POST /taxonomy/import` | Missing-registry-only upload of combined TLC seed JSON plus matching category CSV; conversion preview, not installation. |
| `POST /taxonomy/confirm` | Explicit acknowledgement, revalidation and registry-only save under the shared local operation lease. |

The new `taxonomy` blueprint is separate from `main`; navigation uses the same
desktop/sidebar and mobile More shell. Its request limits are applied before
CSRF body parsing: 4 MiB request, 2 MiB form-memory limit, 50 form parts; individual
documents/uploads still have the M5.1 1 MiB bound. No paths are accepted from the
browser or shown as host paths. Configured source is labelled
`TAXONOMY_ROOT / registry.json`. Valid read-only state allows browsing, not saving.
Missing root, invalid existing registry or unsafe storage requires administrator
repair/configuration outside this workspace; the UI never creates directories or
silently repairs invalid source. A valid existing registry may be intentionally
replaced via Advanced JSON, but never through the bootstrap button.

Guided editing retains stable keys, exposes name/slug/state/order/aliases,
category parents and attribute navigation/default-visibility flags. Terms are
edited within their existing attribute. Category dependencies/cycles are rejected
before review. Removing an attribute explicitly removes its contained terms in
the reviewed document. Product usage is not indexed: the UI warns that removal
does not repair external assignments and recommends deprecation. Advanced full
replacement shows the complete old/new documents and added/changed/removed
definition counts; it cannot infer a rename from removal plus a new key, so keys
must be deliberately preserved by the author. No purported product impact count
or propagation is implemented.

### Reviewed TLC bootstrap

Inputs are uploaded/read as bounded bytes and not retained as files. Combined
seed requires `registry_type: tlc_taxonomy_seed`, integer schema version 1,
categories and navigation_attributes. CSV columns must be exactly name, parent,
slug, category_path, level, sort_order. Every category record must agree with the
CSV (including hierarchy/order), with no duplicates or ambiguous parent names.
Derived hierarchy/path/depth is checked after conversion. Names, existing category
slugs, input arrays and numeric order are preserved. Attribute/term names are
not rewritten; navigation flags come from the source.

Deterministic proposed keys: `cat-<source-slug>`, `attr-<generated-slug>` and
attribute-scoped `term-<generated-slug>`. Generated slugs use NFKD ASCII folding,
lowercase and hyphen-separated alphanumerics; unsupported/overlong or colliding
identities block conversion rather than gaining arbitrary suffixes. Review shows
all generated identities. All imported definitions start **Draft**; attribute
default visibility is **off**, explicitly explained before confirmation. These
are conservative reviewed initialization choices, not inferred product semantics.
Aliases are not invented. The guide's examples/conflicting spelling do not add
new vocabulary. Storefront Collections and tags are empty because the supplied
seed does not provide definition lists; they can be authored in the workspace.

The supplied read-only references were converted and validated in a focused test:
**56 categories, 7 attributes, 110 terms**, 0 ranges, 0 tags. Attribute term counts:
Occasion 26, Recipient 32, Age / Milestone 20, Personalisation 3, Material 10,
Production Method 7, Style / Theme 12. This is verification of the importer, not
a claim that a deployed registry was populated. Original TLC references are not
application resources. The separately generated deployment artifact described
below is excluded from the image; there is no demo/fallback registry.

### Concurrency, backups and recovery

Review tokens expire after 30 minutes and bind the authenticated user, exact
proposed bytes/digest, review mode and original source revision. Revision binds
the configured root plus directory device/inode and original bytes (stronger than
semantic digest alone: even externally reformatted JSON invalidates review).
The immutable M5.1 loader/schema remains the readiness authority. Confirmation
validates again; altered payload, root change, stale revision, missing acknowledgement
and no-op identical-byte saves fail safely. Replaying a successful changed-source
review cannot overwrite newer content. Preview/import POSTs make no authored write.

`taxonomy_registry_update` reuses the existing operation lease/history and retention;
scope holds action and counts only, not documents or host paths. A directory
`flock` additionally serializes registry writers across processes. The supported
application deployment remains single-worker/single-replica as before. External
editors must not write concurrently: POSIX atomic replace is not an OS-wide
compare-and-swap against uncooperative external writers. The service checks source
bytes and configured directory identity again immediately before replacement.

All file operations use pinned directory descriptors with no-follow traversal,
fixed/generated basenames and bounded regular-file reads. Existing source gets a
unique `.registry-backup-<uuid>.json` backup, byte-verified and schema-validated
before replacement. Private `.registry-stage-<uuid>` data is fsynced and verified.
Replacement is same-directory atomic; first installation uses a no-clobber hard
link so it never overwrites a file that appeared after review. Filesystems must
support these operations and directory locking; unsupported mounts fail closed,
without a fallback that weakens safety. New source/backup files use mode 0600.
The mount must permit the configured runtime UID; no recursive chmod/chown occurs.

Readback validates exact bytes and schema before success. On verification failure,
rollback restores the verified backup only when the visible source is still this
operation's written version; an externally changed/corrupt version is not blindly
overwritten. Backup evidence is retained for administrator review and the operation
fails. There is no automatic crash replay: startup's existing operation recovery
marks interrupted history; inspect source/backup and start a fresh review.
Successful saves retain the newest 10 valid, exact-pattern application backups;
manual/invalid/symlink files are never retention candidates. Retention inspection
is bounded to 2,000 directory entries and failures log one safe warning without
misreporting a successful save as failed. Failed saves do not prune recovery evidence.
Normal temporary staging is cleaned on exit; crash leftovers require manual review.

Filesystem replacement and SQLite audit commit are not a distributed transaction.
If audit finalization fails after a verified write, inspect/reload the actual source
before retrying; never assume the source stayed unchanged solely from a request
failure. No raw source, credentials or sensitive path is logged. M5.2 adds no
Discord event/routing or Woo requests.

## Deferred integration

M5.2 onboarding now displays this same loader's readiness/counts before the
explicit initial catalogue scan. A valid read-only registry is sufficient;
missing/invalid source needs mounting/correction and Recheck. Nothing creates or
copies a registry. This is not scanner taxonomy integration: existing scan and
product contracts remain unchanged, and legacy installations remain usable.
Deployment and first-run details are recorded in ARCHITECTURE and DOCKER.

### Ownership clarification — 2026-09-06

The application is generic and supports **bring your own registry**. Startup
does not require, create or install a registry. A missing file remains missing;
normal workspace requests never invoke TLC conversion. The missing-registry UI
prioritizes providing your own file, with TLC conversion explicitly optional.

TLC's reviewed authored artifact is
[`deployment/examples/tlc/registry.json`](../deployment/examples/tlc/registry.json),
not application defaults. See its [installation/provenance README](../deployment/examples/tlc/README.md).
Actual production-loader validation: version 1, Ready, 56 categories, 7 attributes,
110 terms, 0 Storefront Collections and 0 tags. Content digest:
`01d7415e5c55ff233e342480c3b6c5577e0a56248e2198e289ef6f06a29bef61`.
The artifact activates reviewed definitions; the optional interactive converter
retains its Draft initialization. No product or Woo state is inferred.

Manually place your authored file in the persistent taxonomy host directory as
`registry.json`, bind that directory to `/taxonomy`, and configure
`TAXONOMY_ROOT=/taxonomy`. Nothing installs TLC data on startup or moves product
files. The root remains separate from catalogue, output and instance storage.

Future M5.3+ semantics must support Occasion/Recipient as informational attributes
alongside Size with only Size designated in `variation_attributes`. These remain
the same global attributes, not duplicate concepts such as "Variation Size".
No such product/scanner/Woo integration is implemented in M5.2.

M5.2 provides the local registry workspace/importer/writer only. Product controlled assignments and explicit variation
designation require the later versioned resolver/projection contract first.
Store-scoped Woo taxonomy identity, hierarchy sync, Brands/range destination,
Preview digests and publishing integration remain later slices. None of the
current catalogue, scanner, Product Relationships, media or M4 identity/publisher
code consumes this registry yet.
