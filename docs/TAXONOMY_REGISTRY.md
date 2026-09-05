# Local taxonomy registry contract — version 1

M5.1 implements read-only loading and validation only. This document describes
the implemented file contract; ROADMAP and CURRENT_STATE track slice status.
It does not change product_info.json or authorize registry editing/import/sync.

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

M5.1 does not modify Docker, Compose, Unraid mounts or `.env.example`. Mount
declarations, permission presentation and installation instructions are an M5.2
deployment gate. A read-only mount is sufficient for this loader.

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

## Deferred integration

M5.2 may add the local registry workspace and a reviewed importer/writer after
its own authorization. Product controlled assignments and explicit variation
designation require the later versioned resolver/projection contract first.
Store-scoped Woo taxonomy identity, hierarchy sync, Brands/range destination,
Preview digests and publishing integration remain later slices. None of the
current catalogue, scanner, Product Relationships, media or M4 identity/publisher
code consumes this registry yet.
