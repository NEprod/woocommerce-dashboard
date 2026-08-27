# Catalogue Intake

Catalogue Intake is an authenticated, read-only workspace for inspecting image
folders before they enter the catalogue. It is deliberately separate from the
scanner, `/catalogue`, generated `/output`, and `/app/instance` state.

Phase 2.5 Milestone 2 supplies:

- an optional dedicated `/intake` mount;
- an intake-confined folder browser;
- deterministic loose-image grouping previews;
- deterministic prefix-renaming previews;
- complete intake-relative source, folder-tree, destination, and filename views;
- conflict, corrupt-image, unsafe-entry, Parent, and scanner-compatibility diagnostics.

It does not group, rename, copy, move, delete, upload, import, scan, create
`Prepared/`, write metadata, or persist preview manifests. Preview requests do
not create operation history or Discord events.

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

Future confirmed operations will write only below:

```text
/intake/Prepared/
```

The planned default is `Prepared/<selected source basename>/`, with visible
duplicate-safe suffixes such as ` (2)` and ` (3)`. The source tree is never the
destination. No result is transferred into the catalogue automatically.

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

Later approved work may add copy-first grouping and collision-safe staged
renaming. Originals should remain preserved, confirmed results should be exposed
only after successful staging, and substantial terminal operations may reuse the
scanner-information and scanner-warning/error Discord channels. Preview requests
remain notification-free.
