# Catalogue Intake

Catalogue Intake is an authenticated workspace for inspecting and safely
preparing image folders before they enter the catalogue. It is deliberately separate from the
scanner, `/catalogue`, generated `/output`, and `/app/instance` state.

Phase 2.5 Milestones 2 and 3 supply:

- an optional dedicated `/intake` mount;
- an intake-confined folder browser;
- deterministic loose-image grouping previews;
- deterministic prefix-renaming previews;
- complete intake-relative source, folder-tree, destination, and filename views;
- conflict, corrupt-image, unsafe-entry, Parent, and scanner-compatibility diagnostics.
- explicit confirmation of valid grouping proposals;
- copy-first grouped results below `/intake/Prepared/`.

Browsing, grouping previews, and rename previews remain read-only and do not
create operation history or Discord events. A confirmed grouping is the only
current mutation: it copies unchanged image bytes into a provisional grouped
result. It does not rename images or folders, move or delete sources, upload,
import, scan, write metadata, or hand anything to `/catalogue`.

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
the same filesystem with a no-replace atomic rename. Existing results are never
overwritten or merged; the server selects ` (2)`, ` (3)`, and later suffixes
deterministically. A race at promotion fails safely rather than replacing data.

Failure before promotion exposes no completed result and removes only the
staging tree bearing that operation's ownership marker where safe. Cleanup
failure is a bounded warning. Before another mutation, recognised staging trees
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

## Rename preview

The legacy filename reference is:

```python
new_name = f"{prefix}_{subfolder_part}_{count:02d}{ext}"
```

followed by lowercasing. Catalogue Intake retains the recognizable underscore
format while rejecting path separators, absolute/drive values, dot segments,
null/control characters, and empty prefixes. Runs of whitespace become
underscores and Unicode is normalized consistently.

The preview shows the entered and normalized prefixes, source extension and
folder, hierarchy type and every contributing component, sequence, legacy name,
recommended name, complete proposed destination, and scanner-compatibility state.
Numbering is deterministic by `(name.casefold(), name)`, begins at `01`, and uses
two digits as a minimum rather than a maximum.

Only a collection-root Parent directory is Parent-owned, recognized
case-insensitively while displaying its real casing. Nested unrelated Parent
folders do not receive reserved ownership. Visible `product_info.json`
`collection_type` and `image_attributes` may improve compatibility confidence;
the workspace does not guess image-attribute order when metadata is absent.

Collision analysis covers exact, case-insensitive, Unicode-normalized, deeper
hierarchy, sequence-reset, Parent/variation, existing prepared result, and
flattened scanner-output names. A blocking collision prevents a Ready state.

## Determinism and limits

Directories, files, groups, and sequences use `(name.casefold(), name)` ordering.
The digest includes safe intake-relative source identity, size, modification
timestamp, ordered proposals, result name, and issues. Unchanged inputs produce
the same digest; changed inputs produce a different one. It never includes file
contents, image binaries, absolute paths, environment values, or secrets.

One preview is bounded to 5,000 valid images. Image validation is cached by safe
source identity, size, and modification time. Per-file preview data remains
request-scoped and does not grow SQLite.

## Future milestones

The next milestone may add the separately approved Folder Naming and Structure
Editor. Image renaming, metadata creation, final prepared-layout validation,
catalogue handoff, and scanning remain separately gated. Grouped folder names in
the current result are deliberately provisional.
