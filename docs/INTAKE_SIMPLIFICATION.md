# Intake simplification — implementation and acceptance

## Inspection (baseline 59ad54a32e95)

| Stage | Existing implementation | Old physical paths |
|---|---|---|
| Discovery / preview | `image_preparation` | `/intake/<selected>` (read only) |
| Group | `grouping_preview`, `intake_grouping` | loose source → private full copy → `Prepared/<name>`; originals retained |
| Folder editor | `intake_folder_editor` | Prepared → private image copy → rename → same-name tree swap / rollback |
| Images | `rename_preview`, `intake_image_renamer` | Prepared → private image copy → temporary renames → same-name tree swap |
| JSON | `intake_metadata_builder` | full Prepared tree copy merely to replace `product_info.json` |
| Handoff | `intake_handoff` | Prepared → catalogue private full copy → catalogue tree, optionally replacing an existing collection |
| Registration | existing scanner | separate merchant append scan; handoff did not register |

These copies protected originals and allowed atomic whole-tree promotion. In-place
operations must instead retain a durable file-move intent, exclude competing
workers, validate content identities, and allow explicit interrupted-operation
continuation. Cross-filesystem import still requires a temporary, hidden,
verified destination on the catalogue filesystem before atomic promotion.

### Naming contract recorded before implementation

* Grouping removes trailing digits from the stem, trims the resulting folder
  name, preserves inner spaces/case, and includes single-image groups. Image
  types: JPG/JPEG/PNG/WebP. Existing unrelated subfolders are preserved.
* Folder proposals support per-folder paths, explicit empty-folder creation and
  removal, whitespace normalisation, reserved Parent semantics and collision
  validation. No prefix/suffix bulk folder operation currently exists.
* Image naming uses the existing `rename_preview`: normalised lowercase prefix,
  underscore-separated normalised folder hierarchy, per-folder ordered sequence
  `01`, lowercase original extension. Parent imagery uses the containing folder
  name. The original script enumerates sorted filenames; it does **not** extract
  a numeric identity from the old name. Preserve the implemented numbering rule.
* Filename prefix is not SKU assignment. Canonical `sku_prefix` plus the existing
  scanner/SKU index allocates `PREFIX0001`, etc. Do not allocate new identities
  during filename preparation. Existing product JSON and SKU indexes must remain
  intact when already present.
* Canonical shared JSON is at collection root; sparse product JSON stays beneath
  each product. Simple, Variable Collection and Single Variable layouts retain
  their existing scanner semantics.

### Registration boundary

The first real-filesystem integration test demonstrated that `shared_collection`
uses update semantics and cannot allocate all variations for a newly imported
variable collection. Registration therefore uses:

```python
start_scan(app, run_id, scan_mode='append', collection_relpath=installed_name,
           scope={'initiating_source': 'intake_registration',
                  'intake_operation_id': handoff_operation_id, ...})
```

The existing collection-path validation/enumeration is now also applied when
append receives an explicit `collection_relpath`. This is a small dispatch
extension, not a new scan mode or algorithm. It selects exactly the installed
collection and retains append ingestion, SKU allocation, markers, checkpoints,
logs and Discord. Missing, root or escaping scope fails closed. Plain append,
manual full scan, editor collection updates, startup and scheduled publishing
triggers are unchanged. No Shopify publication is triggered.

## Existing unfinished work

Never delete or auto-convert Prepared, rollback or private staging directories.
Visible Prepared children can be selected as ordinary Intake sources, independently
of historical stage gates. Old private/uncertain work remains available in its
original operation history and requires inspection; do not guess which copy is
authoritative. No catalogue or database migration is required.

## Implemented direct workflow

The existing Catalogue Intake page is the only entry point. All five stages are
independent links. Historical bookmarks still resolve, but old copy/replacement
confirmation payloads cannot authorise the new direct-file executor.

| Stage | Accepted action | Physical outcome |
|---|---|---|
| Group images | Existing trailing-number grouping proposal | Original files moved into children of the same source folder |
| Folder names / SKU setup | Existing path/name/Parent validation; small prefix/suffix convenience | Original folders/files renamed in place; no SKU allocation yet |
| Rename images | Existing prefix/hierarchy/numbering proposal | Original files renamed; unchanged canonical names are a no-op, including galleries over 99 images |
| Product JSON | Existing guided/advanced editor and canonical validation | Only source-root `product_info.json` replaced; sparse child overrides and unrepresented authored properties retained |
| Move and register | Explicit no-overwrite handoff | One final catalogue tree; source removed only after verified transfer; collection-scoped append registration |

The original copy/wizard HTTP handlers were removed. Pure legacy planning and
history helpers remain for existing receipts and regression coverage, not as a
second active browser workflow. No old private workspace is cleaned by these
new actions. Historical wording describes what happened at the time; its
read-only next links lead to current stages.

Reactive previews use the same server plan as acceptance, with a 300 ms debounce,
out-of-order response protection, and immediate disabling of Accept after a real
setting change. There is no Generate Preview/confirmation-modal chain. Acceptance
recalculates the plan under existing cross-worker ownership and checks its digest.
Digests bind original file bytes, relevant settings and configured roots. Changing
files/settings invalidates a preview. CSRF and authentication remain required.

The existing scanner, not Intake, owns SKU identity allocation. Handoff previews
respect an existing SKU counter and the Single Variable one-parent layout. No SKU
index is written merely to preview. Authored exact image/path references block a
rename for review rather than being silently rewritten.

### File and recovery safeguards

* Preparation parks only the files being moved in an operation-owned temporary
  directory inside the original source. It does not copy an image tree. Existing
  no-replace filesystem primitives handle cycles and case-only renames.
* The small phase journal is stored in the existing operation scope, with existing
  Intake flock/catalogue ownership. There is no new database table, migration or
  background queue. Each durable phase can be explicitly continued after restart.
* Byte verification, inventory checks, root/path confinement and case/Unicode
  collision checks protect originals. Symlinks, hardlinks and special files are
  rejected. File/directory modes are retained; a cross-filesystem final root also
  receives the original root permissions before promotion.
* A same-filesystem final move is direct and non-overwriting. An EXDEV transfer
  uses an owned hidden destination workspace, fsynced temporary files, complete
  byte verification and atomic promotion. A temporary transfer is necessary for
  integrity, not a permanent Prepared/catalogue duplicate. Interrupted partial
  files remain hidden from scanner discovery.
* Successful transfer is durably recorded before proven original files are
  removed. Unexpected additions or changes stop cleanup. No automatic rollback
  deletes a successfully installed collection.
* A failed transfer has an explicit continuation using the retained plan and a
  linked operation; its original failed receipt remains unchanged. Preparation
  continuation retains the previous error/status in recovery history.
* If registration fails, installed files remain. **Retry Registration** reuses
  the existing scanner. Its linked running/completed scan is reused, including
  the dispatch/receipt-link crash gap. A linked scan's own SKU-index updates are
  not mistaken for altered authored source. JSON/images still must match.
* A restarted operation is shown as interrupted, with an explicit continuation,
  not indefinitely Running. Reopening a result does not restart work. Notification
  errors do not undo a successful file operation; uncertain delivery is not replayed.

No automatic catalogue/Prepared migration occurs. Do not install while an old
file operation is actively running. Back up unfinished Intake trees and appdata.
For a normal visible `Prepared/<child>`, select that child and open any relevant
stage. For private/rollback/uncertain old work, inspect the existing receipt and
copies first; automatic authority guessing and deletion are intentionally absent.

### Explicit limits / remaining acceptance

* Plans are bounded to 5,000 entries and hash real file contents. Very large source
  trees can take longer to preview; there is no persistent stale file cache.
* Unsupported/unsafe catalogue entries or exact authored file references require
  merchant review. They are not silently discarded or rewritten.
* A changed/ambiguous interrupted tree may require manual inspection/restoration
  from backup before its retained plan can continue. There is no unsafe Force button.
* Tested hosts are macOS for local tests and runnable Linux/amd64 Docker. A host
  without the required safe no-replace primitive must fail, not fall back to overwrite.
* No production Unraid catalogue, real Shopify API or live Discord delivery was used.
  The evidence below is real disposable filesystem/browser/Docker acceptance, not
  proof against a merchant's shares or every filesystem implementation.

## Executed validation (31 October 2026)

### Real-filesystem and affected regressions

```sh
python3 -m pytest -q tests/test_intake_direct.py \
  tests/test_phase25_intake_preview.py tests/test_phase25_folder_editor.py \
  tests/test_phase25_image_renamer.py tests/test_phase25_metadata_builder.py \
  tests/test_phase25_catalogue_handoff.py tests/test_phase25_structured_import.py \
  tests/test_phase25_safe_grouping.py tests/test_phase25_next_step_navigation.py \
  tests/test_metadata_scan_scope.py tests/test_scanner_contract_characterization.py \
  tests/test_scanner_resolution.py tests/test_phase0_scanner_output_parity.py \
  tests/test_automatic_publishing.py tests/test_manual_publishing_worker.py \
  --disable-warnings --tb=short
```

**260 passed, zero failures** in the final bounded Python run (69.27 seconds).
Warnings were reported, not suppressed as failures; no baseline failure waiver
was needed. Coverage includes all three product layouts with actual images and
real append ingestion; no mocked move for the main
lifecycle. It asserts original image hashes and preparation inodes, canonical JSON
and child override preservation, correct SKUs/checkpoints, Products API visibility,
unrelated-collection exclusion, registration retry, stale previews, source/setting
changes, unsafe links, rename cycles/case-only roots, interruptions at each persisted
preparation phase, and no network writes.

Fifty old tests exclusively exercising the retired copy/rollback/wizard HTTP
contract were removed, not marked skipped or claimed as baseline failures. Their
replacement is the direct-file suite plus actual browser and container workflows.
Naming/schema/history/helper safety tests remain. Initial failures included obsolete
UI assertions, test-fixture defects, and the genuine variable append/update mismatch
described above; these were distinguished and corrected, not hidden.

### JavaScript and browser

```sh
node --check app/static/assets/js/intake-direct.js
node --check app/static/assets/js/prepared-metadata.js
node --test tests/javascript/intake-direct.test.mjs \
  tests/javascript/metadata-editor-core.test.mjs tests/javascript/metadata-title.test.mjs \
  tests/javascript/scanner-workspace-client.test.mjs \
  tests/javascript/operations-workspace-client.test.mjs tests/javascript/shopify-publish.test.mjs
python3 tests/browser/intake_direct_fixture.py
# In another terminal, using the already installed Playwright and Chrome:
node tests/browser/intake-direct.browser.mjs
```

38 focused JS checks passed. Both 1440×1000 desktop and 390×844 mobile ran the
complete reactive group → folder prefix → filename prefix → guided JSON → final
move → actual scoped registration → Products API flow. No page JavaScript errors
or horizontal overflow. The local fixture denies outbound HTTP; a separate isolated
browser context never touches live Safari. Visual review corrected a stale metadata
sidebar so validation now updates with the live proposal.

Screenshots: [desktop grouping](evidence/intake/tlc-intake-desktop-group.png),
[desktop JSON](evidence/intake/tlc-intake-desktop-json.png),
[desktop registration](evidence/intake/tlc-intake-desktop-registered.png),
[mobile grouping](evidence/intake/tlc-intake-mobile-group.png),
[mobile JSON](evidence/intake/tlc-intake-mobile-json.png),
[mobile registration](evidence/intake/tlc-intake-mobile-registered.png).

### Offline Docker

```sh
docker buildx build --platform linux/amd64 --load \
  -t neprod/woocommerce-dashboard:intake-direct-validation .
python3 tests/docker_intake_direct_smoke.py neprod/woocommerce-dashboard:intake-direct-validation
```

Two and four Gunicorn workers, existing runtime/scheduler, disposable `/app/instance`,
Intake/catalogue/output mounts, `--network none`, no credentials. The script:

1. Holds the real Intake flock and proves two/four competing processes are excluded.
2. Terminates a real preparation process with `os._exit(78)` immediately after an
   atomic park, then restarts the container. This is process death, not a caught
   mocked exception and not a claim that a browser owns the work.
3. Logs in through real HTTP/CSRF and explicitly continues the retained operation.
4. Completes the real folder/image/JSON/handoff workflow through HTTP workers.
5. Exercises actual EXDEV transfer between separate mounts, verifies hashes,
   exactly two products, one scoped append, usable checkpoints and no Prepared copy.
6. Restarts again and verifies Products, receipt IDs, source bytes, persisted
   registration and automatic publishing still disabled; no restart scan is added.

## Deployment and merchant acceptance

The release is **not automatically installed on Unraid**. Use the exact immutable
`neprod/woocommerce-dashboard:intake-direct-<commit>` image in the delivery report.
It and moving `develop` must resolve to the same remote digest; the final image is
Linux/amd64 and its embedded revision must match the pushed develop commit.

1. Wait for existing work to finish. Back up `/app/instance`, Intake, catalogue and
   generated output together. Keep current volume mappings, ownership and secrets.
   No database migration or automatic file conversion is introduced here.
2. Keep automatic publishing paused for acceptance. Update only the development
   container image, retaining its `/app/instance`, `/intake`, `/catalogue`, `/output`
   mappings. Do not run a full scan as part of restart.
3. Create a **new disposable Draft** test collection in Intake, never a copy of a
   live catalogue folder. Use four small original images with two numbered groups.
4. Open each stage independently. Check reactive proposals, then Accept grouping,
   folder names, image names and canonical JSON. Confirm originals stay in place
   and no Prepared copy appears. Reopen JSON and verify retained authored settings.
5. Preview Move and register: inspect destination, SKUs, images, JSON and validation.
   Accept once. Confirm source disappears only after transfer, one catalogue tree
   remains, linked scoped append succeeds and new Draft products appear in Products.
6. Confirm no Shopify write, no unrelated scan and no changed publishing cadence.
   Inspect Operations/registration logs. Restart and confirm identities/receipts
   persist without an automatic full scan. Do not deliberately kill live file work.
7. If registration is incomplete, inspect its exact error and use Retry Registration
   only once the cause is resolved. Do not reimport, delete installed files or
   force-clear receipts. Leave uncertain legacy work untouched for inspection.

Rollback image (retained unchanged):
`neprod/woocommerce-dashboard:variable-finalisation-59ad54a32e95`

Known remote index digest:
`sha256:1fc2414b2de052cb28587935ebd6899196e655ddc7830013d96d72e477f9d850`.

Stop new Intake work before reverting the image. An image rollback cannot undo
accepted file moves/renames. If reversing accepted filesystem changes is necessary,
restore the **paired appdata + Intake + catalogue + output backup** while stopped;
do not mix an old database with new file identities. Never let the old copy-based
UI attempt to resume a new direct-file journal. No live Shopify rollback is needed
for Intake because Intake performs no Shopify writes.
