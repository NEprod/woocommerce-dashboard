# Product Override save scan scope

Save Metadata previously selected `shared_collection` plus a collection path for
Shared Collection saves, but selected the broad `update` mode without a path for
Product Overrides. That mode enumerates the catalogue and can encounter unrelated
staging folders.

Both save kinds now reserve `shared_collection_update` and call the existing
`start_scan(..., scan_mode="shared_collection", collection_relpath=...)` pathway.
The existing scanner performs normal shared/override resolution, ingestion,
identity reuse, marker finalisation and collection-scoped lifecycle reconciliation.
No scanner algorithm, manual scan endpoint or scheduled scan dispatch changed.

For Shared Collection saves, the existing target-parent calculation is unchanged.
For Product Overrides, the existing authorised shared-source resolver finds the
collection JSON. Its resolved directory must be an immediate collection folder
inside the configured catalogue root and must equal the override product folder's
parent. Root scopes, mismatched owners, missing shared files and escaping paths
are rejected before saving. Scope is checked again under the operation lease.
There is no full-catalogue fallback.

The existing catalogue-operation lease remains the concurrency mechanism. An
active operation returns HTTP 409 before another save/scan starts. This is
conservative global serialization, not a new per-folder queue or coordinator.

## Acceptance on Unraid (merchant-controlled)

1. Back up appdata/database and source JSON; manually select the immutable release
   image. No automated Unraid deployment is performed.
2. Save a small Product Override edit in a known Simple collection. Its operation
   should report one collection folder and `shared_collection` mode. Confirm no
   unrelated staging/collection folders appear in that run's scan log.
3. Reopen the product and confirm the edit, inherited values, SKU and variants are
   intact. The collection's shared JSON must remain unchanged.
4. Repeat for a Variable Collection child, then confirm an ordinary Shared
   Collection save still uses its same collection-scoped scan. Single Variable
   products continue using their single shared JSON owner, not child overrides.
5. Confirm Review & Sync sees the current local comparison without approving any
   Shopify write. Known unrelated product findings remain truthful and unchanged.
6. While a scan is active, a second save should report the existing active-operation
   conflict rather than launching another scan. Wait for completion and retry.

Automated coverage uses isolated fixtures, mocked Shopify access and a
network-disabled two-worker Docker upgrade/restart with disposable mounts. No
production catalogue, Shopify settings, JSON schema or database migration changes.

Validation: 187 focused/bounded Python tests passed, covering Save Metadata,
source-path safety, real scoped ingestion, existing manual scan modes, operation
ownership, marker recovery, projection, titles and scheduled tasks. Offline Docker
verified a real override save processes one collection while unrelated invalid
staging JSON is untouched, with the result persisting through restart.
