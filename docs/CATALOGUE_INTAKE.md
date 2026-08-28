# Catalogue Intake

Catalogue Intake is an authenticated workspace for inspecting and safely
preparing image folders before they enter the catalogue. It is deliberately separate from the
scanner, `/catalogue`, generated `/output`, and `/app/instance` state.

Phase 2.5 Milestones 2–6 supply:

- an optional dedicated `/intake` mount;
- an intake-confined folder browser;
- deterministic loose-image grouping previews;
- deterministic prefix-renaming previews;
- complete intake-relative source, folder-tree, destination, and filename views;
- conflict, corrupt-image, unsafe-entry, Parent, and scanner-compatibility diagnostics.
- explicit confirmation of valid grouping proposals;
- copy-first grouped results below `/intake/Prepared/`.

Browsing and previews remain read-only and do not create operation history or
Discord events. Confirmed grouping, folder editing, image renaming, and Prepared
metadata saving are separately gated mutations under one Intake lock. None
moves or deletes loose sources, uploads, imports, scans, creates markers, or
hands anything to `/catalogue`.

## Storage boundary

`/intake` must be a real user-selected mount. The application does not create a
fallback directory in the container layer and does not silently substitute
`/catalogue`, `/output`, or `/app/instance`. If the mount is absent, the rest of
the application remains available and Catalogue Intake shows a controlled
unavailable state.

Only intake-relative paths are rendered. Traversal, absolute paths, encoded
traversal, control characters, symlinks, hard-linked files, devices, sockets,
FIFOs, and other special entries are rejected or displayed as controlled issues.
Authenticated image previews use signed opaque references, verify image content,
and use private, sniff-resistant responses without writing thumbnail caches.

Confirmed operations write only below:

```text
/intake/Prepared/
```

The default is `Prepared/<selected source basename>/`, with visible
duplicate-safe suffixes such as ` (2)` and ` (3)`. The source tree is never the
destination. No result is transferred into the catalogue automatically.

## Confirmed copy-first grouping

`POST /image-preparation/group/confirm` requires authentication, CSRF, the
explicit source-preservation acknowledgement, and a valid preview digest. The
server recomputes the complete proposal and source identities before accepting
it. Changes to paths, sizes, modification times, issues, or the chosen available
destination make the proposal stale and require a new preview.

Only one Catalogue Intake mutation runs at a time under a dedicated lock that
does not replace or alter the scanner operation lock. The operation copies
regular, validated source images without changing names, extensions, contents,
or source metadata into the private operation-owned tree:

```text
/intake/.catalogue-intake-staging/<operation-id>/
```

Normal intake browsing and previews exclude that tree. Each staged result is
checked for the exact expected two-level tree, regular-file status, image
validity, and matching source size. The complete directory is then promoted on
the same filesystem. The application first uses the platform's atomic
no-replace rename. Some Unraid/FUSE mounts reject that specialised flag with
`EINVAL`; while still holding the authoritative Intake mutation lock, the app
rechecks the source/destination device and destination absence before using the
mount-compatible ordinary directory rename. It never uses overwrite-capable
`os.replace()`. Existing results are never deliberately overwritten or merged;
the server selects ` (2)`, ` (3)`, and later suffixes deterministically, and a
reported destination conflict stops safely.

Failure before promotion exposes no completed result and removes only the
staging tree bearing that operation's ownership marker where safe. Cleanup
failure is a bounded warning. Operation history records a controlled failure
stage, safe relative destination, promotion capability, and whether exact
operation-owned staging was cleaned; raw host paths are never retained. Before
another mutation, recognised staging trees
older than 24 hours may be removed conservatively; active operations,
unrecognised/user-created folders, and completed `Prepared/` results are never
cleaned by this policy.

Successful, warning, and failed operations appear in existing bounded operation
history with stages, counts, safe intake-relative paths, and a read-only link to
the provisional result. The terminal status is **Grouping complete — folder
review required**, and the next step is **Review and rename folders**. Clean
completion reuses the scanner-information Discord webhook; warning completion
and failure reuse the scanner warnings/errors webhook. Previews do not notify,
there is only one terminal message, payloads are bounded, and notification
failure never changes the grouping result.

## Grouping preview

The legacy grouping reference removes trailing digits:

```python
base_name = re.sub(r'\d+$', '', filename_noext)
```

The preview shows the exact legacy base and the safe proposed folder separately.
The safe proposal trims leading/trailing whitespace, preserves meaningful
punctuation and Unicode, rejects empty/unsafe results, and detects case-insensitive
or normalization collisions without merging them. Single-image groups are valid
and labelled. A proposed case-insensitive `Parent/` folder is identified as the
scanner-reserved parent-image location and requires deliberate future review.

## Folder Naming and Structure Editor

Authenticated routes below `/image-preparation/folders` select only one direct
child of `Prepared/`. The editor shows the complete current and proposed trees,
safe intake-relative paths, direct image/child counts, inferred Parent ownership,
entered/normalized names, folder-only scanner compatibility, and a preview of
future filenames. Future names use a temporary, non-persisted prefix; image files
are not renamed by this milestone.

Names are NFC-normalized and trimmed while preserving meaningful Unicode,
punctuation, apostrophes, and selected casing. Separators, dot components,
absolute/drive paths, control/null characters, encoded traversal, empty names,
and unsafe reserved filesystem names are blocked. Sibling collisions are checked
case-insensitively and after Unicode normalization. Only one direct child may be
the scanner-reserved Parent directory; nested folders named Parent are ordinary
folders. Non-empty folders cannot be removed.

Confirmation recomputes a deterministic digest over the source folder/image
identities, complete current/proposed trees, rename/create/remove-empty proposal,
Parent state, collision results, and future filename analysis. It then uses the
same dedicated Intake mutation lock and private staging ownership contract as
grouping. The existing working result is copied to staging, folder changes are
applied in an isolated result tree (making swaps and case-only renames safe),
and every image is byte-verified. The visible working result then moves to
operation-owned hidden rollback, verified staging is promoted under the same
visible Prepared name, and rollback is removed only after promoted verification.
Any failed swap restores and verifies the original. Normal folder-review
progression therefore never creates a `(2)` result; deliberate repeated grouping
remains duplicate-safe and may do so.

The bounded operation type is **Catalogue Intake — Edit Folder Structure**. Its
terminal state is **Folder structure confirmed — image renaming required**.
Only safe counts, relative result identity, proposal digest, stage, warning/error
state, and terminal status persist. One terminal Discord summary uses the
existing scanner-information or scanner warnings/errors channel; previews and
individual folder edits do not notify. Notification failure is non-fatal.

## Image Renaming

The legacy filename reference is:

```python
new_name = f"{prefix}_{subfolder_part}_{count:02d}{ext}"
```

followed by lowercasing. Catalogue Intake retains the recognizable underscore
format while rejecting path separators, absolute/drive values, dot segments,
null/control characters, and empty prefixes. Runs of whitespace become
underscores and Unicode is normalized consistently.

Only a direct Prepared result whose latest proven workflow state is **Folder
structure confirmed — image renaming required** is eligible. The preview shows
the entered and normalized prefixes, source extension and
folder, hierarchy type and every contributing component, sequence, legacy name,
recommended name, complete proposed destination, and scanner-compatibility state.
Numbering is deterministic by `(name.casefold(), name)`, begins at `01`, and uses
two digits as a minimum rather than a maximum.

Only a collection-root Parent directory is Parent-owned, recognized
case-insensitively while displaying its real casing. Nested unrelated Parent
folders do not receive reserved ownership. Visible `product_info.json`
`collection_type` and `image_attributes` may improve compatibility confidence;
the workspace does not guess image-attribute order when metadata is absent.

Collision analysis covers exact, case-insensitive, Unicode-normalized,
sequence-reset, Parent/variation, equal-hierarchy, and flattened scanner-output
names. A blocking collision prevents confirmation. Parent filenames use the
visible Prepared result name; variation filenames include every hierarchy
component in deterministic order. Sequence numbering restarts per image-owning
directory and uses two digits as a minimum.

Confirmation revalidates the workflow state, complete tree identities, mappings,
prefix, digest, and any selected lineage cleanup. The working result is copied to
hidden staging. Each image first receives a unique operation-owned temporary
name and is then renamed to its final approved name, allowing case-only changes
and swaps without overwrite. Counts, readability, extensions, complete paths,
and SHA-256 byte identity are verified before the same rollback-protected visible
replacement used by folder editing. Success is **Images renamed — metadata
required**. No JSON, catalogue copy, output copy, scanner call, marker, image
conversion, or image-content change occurs.

Older versioned folder-edit results remain eligible. A superseded predecessor is
offered for cleanup only when operation lineage records direct ancestry, both
recorded tree identities still match, and no later operation references the
predecessor. Exact acknowledgement is required and cleanup occurs only after the
renamed result is verified. Legacy lineage without immutable identities, naming
similarity alone, active/referenced results, and loose source folders are always
preserved. Cleanup failure is a warning and does not invalidate the renamed
working result.

## Prepared Metadata Builder

Authenticated routes below `/image-preparation/metadata` select only a direct
Prepared result whose latest durable state is **Images renamed — metadata
required** or **Metadata complete — validation required**. The builder creates
or corrects only the collection-level `product_info.json`; it does not create
product overrides or resolved projection data. The supported inventory is the
existing scanner contract: identity, pricing/sale, physical data, taxonomy,
descriptions/SEO, ordered attributes and image attributes, existing variation
modifiers, shipping class, and grouped/upsell/cross-sell fields. Exact collection
types remain `Simple`, `Variable Collection`, and `Single Variable`.

Guided mode shows every supported field. Advanced JSON edits the actual authored
object and preserves unknown authored content instead of flattening inheritance.
The exact read-only preview has no operation metadata, IDs, absolute paths, or
invisible defaults. Validation combines the existing JSON Schema with safe SKU,
numeric/date, duplicate-value, attribute/modifier-reference, and folder checks.
Single Variable analysis recognises root `Parent/` case-insensitively, rejects
duplicate case variants, excludes Parent from attribute values, and follows the
authored `image_attributes` order while reporting expected and visible
combinations. Complete scanner validation is not claimed.

Confirmation recomputes the document, findings, existing metadata identity,
workflow state, folder list, and image hashes before checking the deterministic
digest. It acquires the shared Intake lock, copies to operation-owned hidden
staging, atomically writes a UTF-8 newline-terminated file, verifies every image
byte and folder, and uses rollback-protected same-name promotion. Normal save
creates no `(2)` result and no visible backup clutter. Failure restores and
verifies the prior result.

The bounded operation is **Catalogue Intake — Save Metadata**. Logs do not retain
the full JSON or descriptions. Terminal Discord delivery remains bounded and
non-fatal. Success is **Metadata complete — validation required** and the next
step is **Validate prepared collection**.

## Final validation and catalogue handoff

Authenticated routes below `/image-preparation/handoff` accept only a direct
Prepared result whose latest durable workflow state is **Metadata complete —
validation required**. A completed result shows **Review Handoff**; repeating a
handoff requires an explicit fresh validation and confirmation. Readiness is
never inferred from a folder name.

Final validation combines the existing metadata/schema and folder checks with a
complete safe-tree identity, image readability, case/Unicode ambiguity,
flattened filename collision, unsupported entry, symlink, destination, and
mount checks. Blocking errors disable confirmation and have no bypass. Optional
SEO omissions, Draft publishing intent, prefix differences, and a new
destination remain warnings where they do not conflict with the scanner
contract. The proposal digest covers the Prepared workflow and tree identities,
metadata validation, catalogue-relative destination, current destination state,
create/replace action, and whether replacement acknowledgement is required.

Confirmation re-resolves and revalidates everything server-side. Lock order is
the existing catalogue/scanner operation lock followed by the Intake mutation
lock. The complete Prepared tree is copied byte-for-byte into operation-owned
hidden staging beneath the configured catalogue mount and verified before any
final destination appears. Existing destinations are never merged: after
verified staging they move to protected rollback, the staged tree promotes with
the existing no-replace/FUSE-compatible helper, and the promoted tree is
verified before rollback removal. A promotion or verification failure restores
and verifies the original destination; unresolved restoration retains a
controlled recovery state.

The Prepared result is never moved, renamed, rewritten, or deleted. Successful
operation history records **Catalogue handoff complete**, completion time,
catalogue-relative destination, create/replace action, bounded counts and
digests, verification and rollback state, and the next step **Run Append Scan**.
No scanner is invoked, no marker or SKU is created, no SQLite catalogue
projection changes, and `/output` remains untouched. One bounded terminal
Discord summary reuses scanner-information for clean success and scanner-errors
for warning completion or failure; Discord delivery remains non-fatal.

## Determinism and limits

Directories, files, groups, and sequences use `(name.casefold(), name)` ordering.
The digest includes safe intake-relative source identity, size, modification
timestamp, ordered proposals, result name, and issues. Unchanged inputs produce
the same digest; changed inputs produce a different one. It never includes file
contents, image binaries, absolute paths, environment values, or secrets.

One preview is bounded to 5,000 valid images. Image validation is cached by safe
source identity, size, and modification time. Per-file preview data remains
request-scoped and does not grow SQLite.

## Workflow boundary

Catalogue handoff is complete but scanning remains separately gated. A handoff
copies source material only; the user must start Append Scan manually.
