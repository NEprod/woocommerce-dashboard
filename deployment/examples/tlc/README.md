# TLC authored taxonomy deployment data

## Repository reference data versus runtime data

This directory and [products/](products/README.md) form the curated TLC reference
dataset. The former loose development fixture directory is retired. Reference
priority is the implemented contract, this registry, the curated product
examples, temporary copies for mutation, then fictional isolated unit fixtures.
The product README records current compatibility gaps rather than rewriting them.

Runtime `/intake`, `/catalogue`, `/output`, `/taxonomy` and `/app/instance` are
separate deployment-owned mounts/state, not these repository example directories.
No example is automatically copied to any runtime root. Scanner tests only use
temporary copies. Runtime product JSON stays in its existing catalogue folders.

`registry.json` is TLC's reviewed deployment-owned vocabulary, not an application
default, fallback, runtime resource or automatically installed seed. Other users
provide their own compatible registry. This directory is excluded from the Docker
build context and is not copied by the Dockerfile or entrypoint.

## Validated artifact

- Schema version: **1**; actual production loader status: **ready**.
- Categories: **56**; attributes: **7**; attribute terms: **110**.
- Storefront Collections: **0**; tags: **0**.
- Canonical content digest: `01d7415e5c55ff233e342480c3b6c5577e0a56248e2198e289ef6f06a29bef61`.
- File SHA-256: `593a7e3e644e05519be1e39d94a02e4301cfbee663ff3f0c0645de6e4c9e3d28`.

The loader digest ignores JSON object-key ordering/serialization whitespace; it
therefore differs from the exact-byte file hash. Validation uses the M5.1 loader,
not just JSON Schema. Automated workspace edits use temporary copies only.

## Provenance and deterministic keys

Converted from the supplied combined TLC JSON and cross-checked against the
category CSV; the decision guide supplies interpretation, not additional terms.
Source SHA-256 fingerprints:

- Combined JSON: `43559adfda957aa73b9cf1014dcb07236e12735c1acc4764eff4dc015a9b5394`.
- Category CSV: `47fb63db947aa35726884739400db214c65ccf68b4e394735f4abfbadcfc902a`.
- Decision guide: `827b5b3b501154a91dbecc108fbfe0334e4775913e46a36c261c82d227003192`.

Category keys are `cat-<source slug>`. Attribute keys are `attr-<generated slug>`;
term keys are `term-<generated slug>`, scoped to their attribute. Generated slugs
use NFKD ASCII folding, lowercase and hyphen-separated alphanumeric sequences.
Collisions/invalid identities are rejected, not silently suffixed. Names,
category slugs, hierarchy and ordering, and term ordering are preserved.

Term counts: Occasion 26; Recipient 32; Age / Milestone 20; Personalisation 3;
Material 10; Production Method 7; Style / Theme 12. No vocabulary is invented
from illustrative guide examples. The reviewed deployment definitions are Active.
The optional interactive converter still starts imported definitions Draft.
Attribute visibility defaults remain conservatively off; this does not implement
product assignment or Woo visibility semantics.

## Manual TLC installation

1. Prepare a persistent host taxonomy directory, separate from catalogue, output
   and instance storage. Do not edit it concurrently through another editor.
2. Review and back up any existing registry before intentional replacement.
3. Copy this authored file into that directory as `registry.json`.
4. Bind the directory to container `/taxonomy`; set `TAXONOMY_ROOT=/taxonomy`.
   Allow the configured container UID to read/write it for workspace editing.
5. With an approved M5.2 image, open Taxonomy and verify Ready, counts and digest.
   Keep the host directory and its backups across container replacement.

This task does not install the file on the live host or publish an image. Product
`product_info.json` files remain in their existing catalogue folders. Never copy
this file automatically for unrelated installations.

See [registry contract](../../../docs/TAXONOMY_REGISTRY.md),
[Docker](../../../docs/DOCKER.md) and [Unraid](../../../docs/UNRAID.md).
