# Product verification regression repair (after8f83e6e)

## Confirmed code causes and evidence boundaries

The inspected branch was develop, HEAD8f83e6e72d7f2fac88014bdfb9191d5e634ce85d.
The existing local/remote immutable package-assignment image was linux/amd64 at
sha256:5742b1317f9d520de7003bf36671c64cae3206d6644cc167551ac6188ab8119b,
with that exact revision. This is not proof of the image actually running on Unraid.
Development has no configured merchant Admin API credentials or access to the
production database/logs. Live counts/messages are merchant-supplied observations.

Two defects are reproduced using isolated fictional catalogues and mocked Shopify:

1.8f83e6e added `package_assignments_unverified` to
   `shopify_comparison.record()`'s incomplete-evidence gate and an unconditional
   matching-product unavailable override in `shopify_products.product_state()`.
   The flag includes absent manual IDs and **accepted** assignments, which can never
   have independent public readback. Valid metadata/media/variant/definition reads
   consequently could not renew full/related/equality checkpoints. Simple and both
   Variable layouts reproduce this failure before the correction.
2.1728a413 introduced independent packed targets and `unconfigured` metadata, but
   native comparison/status consumers still treated globally enabled2027-01 as a
   required target for every product. A capability failure for a product with **no
   authored packed size** generated the reported generic incomplete-evidence path.
   Variable status also demanded per-variant packed equality for unconfigured sizes.
   This predates the package-assignment release; it is not a restored fallback to
   finished dimensions. A capability flag does not author a measurement.

The repeated generic message specifically originated from publication-read failure,
informational attribute diagnostics or an enabled/blocked native read. The package
flag alone did **not** emit that message. It was wrong to infer the entire live
failure was the package flag. The exact failing component for each live SKU still
requires retained diagnostics; no historical production execution is fabricated.

## What changed / what remains required

Write-only optional package outcomes no longer invalidate otherwise complete product
readback. Review still shows ID-required/pending/accepted-only/manual-clear states.
Pending assignment writes remain Update Available; unknown/pending mutation outcomes
remain Recovery Required through unchanged identity, preview and reconciliation guards.
An accepted package is **not** independently verified. Its warning remains alongside
the normal product comparison; Up to Date refers to the readable managed fields.
Durable assignment receipts/history, no-resend checks and partial publishing outcomes
are unchanged. No assignment/default/package/catalogue write is performed by refresh.

`native_required()` limits mandatory native readback to actual authored targets,
including invalid/incomplete authored targets. Unconfigured products/variants retain
their remote measurements and historical warnings; no native equality, clearing or
finished-to-packed substitution is invented. Configured sizes still require capability,
correct InventoryItem/variant identity and independent measurement readback. Existing
uncertain native writes remain protected, even if local configuration is removed.

Finished-size metafield values and definition/access settings remain separate actual
reads and reviewed actions. A missing definition or NONE→PUBLIC_READ is Update Available
when its absence/current setting was read successfully, not an evidence failure. A
failed read, replaced definition identity or incompatible type still blocks verification.
Successful shared definition setup is recognised by subsequent reads; no policy bypass.

Ten-minute success freshness is still based on successful full/related/revision evidence,
not an attempted request or HTTP200. Incomplete legacy summaries cannot be reused as
fresh cache entries. The next bounded comparison re-reads and replaces them; no global
reset, cursor clear, identity deletion or timestamp repair is required. Existing valid
records remain valid for their original scope/age. Concurrency/revision fencing remains.

## Gallery message

`shopify_drafts.common_plan()` emitted “Select a product with1–20…” since6d8bc809
(13 October), before both recent releases. It is a production full-gallery safety
gate, not a test fixture instruction. It triggers on zero projected original images
or more than20. `catalogue_images.product_image_diagnostics()` resolves authorised
catalogue sources/markers/assets; Variables also use the complete parent/child gallery.
An absent mount, ambiguous source layout or genuine over-bound gallery can fail it.
Which occurred on the merchant's affected product is not established from that text.

The replacement reports either zero resolved images and mount/source checks, or the
actual count and limit. Missing/unsafe/corrupt/unresolved files, byte limits, media
identity/order and full pagination remain enforced. Nothing is truncated or ignored.
No scanner changes or automatic catalogue rescans are part of this repair.

## Specific diagnostics and safe collection

New incomplete summaries retain `missing_evidence` component/reason records and a
specific failure, rather than just the generic sentence. Scheduled history groups
identical errors, with bounded per-SKU reasons under Recent product outcomes. Older
history remains historical and is not rewritten.

Run **inside the existing container** (no credentials/customer details in output):

```sh
python -m app.shopify_attribute_diagnostic --sku 16bit-0001 --verification
```

This mode uses an existing SQLite database opened mode=ro, no application startup,
migration, scheduler, API request or database writes. It reports retained state,
checkpoint times, component failures, gallery counts/unresolved count and package
receipt state only, not metadata values, descriptions, order data, tokens or image URLs.
Pass `--instance /app/instance` if needed; keep the actual persistent mount unchanged.
It does not independently prove current remote equality. The older `--catalogue` mode
performs fresh read-only product API checks and should only be shared after checking
its catalogue output; it is not needed for the narrow diagnostic above.

For exact running image identity, on the Unraid host substitute the actual container:

```sh
docker inspect --format '{{.Image}} {{index .Config.Labels "org.opencontainers.image.revision"}}' <container>
```

Do not share environment dumps, full docker inspect, tokens, HARs or customer records.

## Deployment and recovery acceptance

1. Stop TLCDashboard and take a consistent backup of existing appdata/database (or
   the established SQLite online backup). Keep database/catalogue/output mounts and
   all current credentials/environment unchanged. Migration head remains0018_orders_webhooks;
   no migration or destructive data conversion is introduced.
2. Change only the image tag to `shopify-reconciliation-repair-<release shortcommit>`
   from the completion report. Pull/update/start through Unraid. No XML/new mount,
   Shopify app/theme/scope change, shipping change or automatic deployment is needed.
3. Confirm the embedded revision. Allow startup/bounded scheduled Product reconciliation,
   or explicitly Refresh one known Simple product and one Variable parent. This is
   read-only: do not press publishing confirmation. Do not run a destructive rescan.
4. Use16bit-0001 and an affected ornament Variable parent. Check complete matching
   fields→Up to Date; actual value or definition/access differences→Update Available;
   actual denied/incomplete evidence→Verification Unavailable with the named reason.
   Authored packed sizes without available capability must still remain outstanding.
5. Inspect Recent product outcomes and `--verification`: full/related/revision times
   advance only after valid reads. Within ten minutes a subsequent scheduled run skips
   successful comparisons; failed ones do not acquire a success checkpoint. An old
   incomplete receipt must be replaced by a real comparison, not marked green on upgrade.
6. Inspect Optional Package separately: accepted remains accepted-only; unknown remains
   Recovery Required; absent ID needs a manual ID. No repeated assignment write or
   Discord event should occur from these read-only actions. Check all six packages,
   saved IDs and product/variant identities remain present.
7. Restart and confirm evidence/receipts, Orders' separate same-SKU personalisations,
   customisations and task configuration persist. Do not change products/orders remotely
   for this acceptance. A mount/image failure requires fixing that exact source issue,
   not clearing markers, verification records, SKUs or the database.

Rollback uses the retained prior immutable8f83e6e image with the same0018 database;
it reintroduces these verification defects and is not the preferred repair. It does
not undo accepted Shopify assignments or external state. Never roll back the database
merely to change application images; preserve new operation/Orders records and backups.

## Verification record

Focused regression cases were run failing before the relevant fixes (four package
cases; an unconfigured-native capability failure). Follow-up tests cover valid and
failed checkpoints, genuine differences/denial, definition/access actions, preserved
unknown receipts, gallery gates and bounded scheduler reporting. Browser fixtures and
offline Docker use disposable synthetic data with outbound access blocked; they are
not live verification of the merchant's examples. Exact final test counts, revision,
tested image digest and any remaining live evidence limitations are in the release
completion report. No Shopify mutation or real Discord delivery is used in testing.
