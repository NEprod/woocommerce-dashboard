# Intake: independent file-preparation tools

## Contract

Select an existing folder within `/intake` → adjust any tool → live proposal →
Accept Changes. The existing folder picker/browser is above the settings on
every tool. Folder switching retains the current tool; tool switching retains
the source. No preparation prerequisite flags or setup session are required.

| Tool | Direct outcome |
|---|---|
| Group Images | Original loose images move to proposed child folders; unrelated files remain |
| Folder Names / SKU Setup | Original directories renamed, including prefix/suffix; optional canonical `sku_prefix` saved without allocating identities |
| Rename Images | Original files renamed using the retained script rules; correct names stay unchanged |
| Product JSON | Existing canonical editor creates/edits shared root `product_info.json`; sparse child overrides preserved |
| Move and Register | Explicit final handoff only: existing readiness/identity validation, transfer once, collection-scoped append registration; retry registration without repeating transfer |

Preparation runs synchronously in the accepting request. It creates no operation
row, persistent queue, worker lease, private transaction directory, Prepared copy
or automatic recovery action. It never calls the scanner or Shopify. Final
catalogue handoff is deliberately different and retains its existing receipt,
cross-filesystem transfer protection and registration retry.

## Confirmed Unraid failure

Inspected baseline `9bf6b574b63b` in `WooCommerceDashboard-dev`, runtime UID 99,
PGID 100, with `/intake` on `fuse.shfs` at
`/mnt/user/data/tlc/09_Development_Products/intake`.

Historical operation `882259ab458b4aa694a715d5e0926328` was an image rename in
`16-Bit Pixel Art Cards`, not grouping. It failed in preparation/parking on
31 October 2026 at 11:02:03. The old no-replace helper called
`renameat2(RENAME_NOREPLACE)`. A disposable probe on that same mount reproduced
`EINVAL` (Errno 22); ordinary `os.rename` and directory fsync succeeded.

Its four originals (`Untitled_3.png`, `Untitled_7.png`, `Untitled_1.png`,
`Untitled_2.png` in their retained product folders) remained at the original
paths and all four SHA256 values matched the approved historical plan. None
had moved or been renamed. The private
`.tlc-intake-txn-882259ab458b4aa694a715d5e0926328` directory was empty.

The previous snapshot treated any private directory as a blocking error, and
acceptance also consulted unfinished journals globally. That made an old failure
block preparation. Current preparation excludes recognized retained private work
with a warning, never traverses or deletes it, and does not gate on historical
rows. Final handoff still blocks until such uncertain work is reviewed.
The empty user-owned directory and its operation evidence are **not deleted**.
Cleanup requires separate merchant approval after rechecking its contents.

## Original naming behaviour

Recovered scripts are `app/utils/group_images_window.py` and
`app/utils/rename_files_window.py`.

* Grouping removes trailing digits from image stems, including single-image
  groups. Current established path sanitisation trims trailing spaces rather
  than reproducing the desktop script's accidental trailing-space directory.
* Image preparation uses lexical original filename order, a two-digit minimum
  counter starting at 01, lowercase extension/name, normalised prefix and the
  last two folder components for deeper paths. A top-level Parent uses the
  selected root's name. Loose root images are left unchanged with a warning.
* This restores original last-two-component/lexical behaviour rather than the
  preceding web implementation's complete hierarchy/natural sorting. Preview
  and acceptance use the same calculation. Already canonical numbered names,
  including galleries above 99, are not prefixed/reordered again.
* Filename prefixes do not allocate SKUs. Existing JSON, counters and scanner
  identities remain authoritative. No new SKU naming convention is introduced.

## Basic safeguards and honest failure behaviour

Authentication, CSRF, permitted roots, symlink rejection, current byte identities,
destination conflicts and settings are checked before acceptance. A short-lived
existing OS flock excludes concurrent app file writers across workers. The plan
is recalculated under that lock. Duplicate stale Accept requests cannot replay
changed work. The mutex is not a persistent work queue or a history gate.

Renames use ordinary `os.rename` after immediate destination checks, as supported
by shfs. This prevents cooperating app writers from overwriting one another; it
does **not** claim atomic no-replace protection against an external process that
changes the filesystem between the last check and rename. Do not concurrently
edit the selected files through another application.

Only rename cycles/case-only changes need short-lived temporary original names
(`.tlc-rename-*`), never staging copies. JSON uses the existing same-directory
atomic file writer. A failure stops immediately and reports completed mappings,
the failed operation and current actual files. A recoverable cycle name is
restored only into a free original slot; unresolved original temporary names are
shown and must not be deleted. Process death can leave some direct changes
completed: reopening shows the actual filesystem and requires a fresh proposal,
not automatic replay. This is intentionally not a transaction/rollback engine.

No change to publisher, scheduler, reconciliation, Orders, package definitions,
normal metadata-save scanners or production automation settings is included.

## Executed real-mount acceptance

The candidate modules/templates were copied to a task-owned `/tmp` overlay and
loaded by the **normal preview/confirm HTTP handlers** in an isolated application
and SQLite database. No installed `/app` files or live database were changed.
Outbound requests were denied and scanner dispatch was guarded to fail.

Fixture: `/intake/TLC-INTAKE-AUDIT-20261031-c86c-runtime-v2`, UID 99:100.
Report: `/tmp/tlc-intake-probe-uc65d8yh/report.json` in the running container.
All three task-owned disposable Intake folders were subsequently identified by
their exact markers and complete inventories, then removed under the merchant's
test-cleanup authorisation. No user folders or historical evidence were removed.

Passed: grouping, directory renaming/SKU prefix, filename renaming, JSON create
and edit, duplicate stale acceptance protection, independent tool entry, top
folder selection, original image hashes. Preparation rows: 0; catalogue imports:
0; scans: 0; Shopify calls: 0. No Prepared/private transaction/copy tree created.

This also exposed a genuine preview transport defect: Flask sorted object keys,
changing JSON text between preview and acceptance. Canonical document transport
now uses JSON text, preserving nested customisation field order; a normal HTTP
round-trip regression covers it. Mac source-root aliases are canonicalised so
successful file changes are not falsely reported failed during result rendering.

This is an actual Unraid filesystem test using isolated candidate HTTP handlers,
**not** a claim that a new image was deployed or the installed live browser ran
the candidate. No final import/registration was run on the real catalogue.

## Browser evidence

Separate disposable local browser contexts exercised desktop 1440×1000 and mobile
390×844: independent entry to every tool, folder switching within Rename, reactive
grouping, folder prefix, filename prefix, guided JSON, explicit final handoff and
real isolated scoped append registration. Both passed without JavaScript errors
or horizontal overflow. Actual original bytes and sparse overrides are also
covered by the Python filesystem tests across all three product layouts.

[Desktop grouping](evidence/intake-file-tools/tlc-intake-desktop-group.png),
[desktop JSON](evidence/intake-file-tools/tlc-intake-desktop-json.png),
[mobile grouping](evidence/intake-file-tools/tlc-intake-mobile-group.png),
[mobile JSON](evidence/intake-file-tools/tlc-intake-mobile-json.png).

## Validation and deployment

Run the bounded suites and Docker check documented in the release report:

Final bounded Python run: **266 passed**, 2,136 warnings reported, no failures or
skips. Focused JavaScript: **38 passed**, no failures. Desktop/mobile workflows
both passed. No baseline-failure waiver was used.

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
node --test tests/javascript/intake-direct.test.mjs \
  tests/javascript/metadata-editor-core.test.mjs tests/javascript/metadata-title.test.mjs \
  tests/javascript/scanner-workspace-client.test.mjs \
  tests/javascript/operations-workspace-client.test.mjs tests/javascript/shopify-publish.test.mjs
python3 tests/docker_intake_direct_smoke.py IMAGE
```

The offline Docker test uses separate disposable mounts, no network, and two/four
workers. It checks actual process death after the first original move, lock
exclusion, reopening with a fresh proposal, the normal HTTP workflow, explicit
isolated append registration and restart persistence. It does not promise
automatic preparation recovery after browser/container termination.

Environment finding: a minimal two-process `flock` probe failed exclusion on
Docker Desktop's Mac-backed bind mount (the second lock succeeded), but passed
on Linux-native container storage (`BlockingIOError` as expected). The initial
two restart-test attempts therefore failed at contention, not at file execution.
Appdata in the corrected test uses a disposable Linux-native persistent Docker
volume; source/catalogue/output remain separate bind mounts. This matches Linux
appdata's lock semantics without changing production locks or claiming Mac bind
locking is safe. Do not place authoritative appdata on a share that lacks working
cross-process file locks.

Final Docker results: **two workers passed; four workers passed**. Each excluded
all competing file writers, preserved four original image hashes through actual
process death and restart, completed the normal HTTP preparation flow with zero
preparation operation rows, then explicitly imported two fixture products with
one scoped append scan. Cross-filesystem handoff was exercised. A second restart
retained product identities, scan checkpoints and receipt IDs; unrelated catalogue
files were unchanged and no additional scan was dispatched. Automatic publishing
was disabled only in these isolated test installations, never altered on Unraid.

After explicit deployment approval, install the immutable `intake-tools-<commit>`
image named in the delivery report. Keep `/app/instance`, `/intake`, `/catalogue`,
`/output` and `/taxonomy` mappings unchanged. Back up appdata and source trees;
no database migration or catalogue rewrite is introduced. Do not change the
working automatic publisher schedule for this Intake release.

Acceptance: use a disposable source to enter each tool directly, switch folders,
check previews against resulting files and bytes, reopen JSON, and confirm no
private preparation trees appear. Final Move/Register affects authoritative
catalogue data and requires a separate deliberate merchant action. No live final
import is part of development acceptance.

Rollback image: `neprod/woocommerce-dashboard:intake-direct-9bf6b574b63b`, digest
`sha256:a46be080585c33d82e8a186a64fbdd01bde444bc31c1e3a7c513b855ecb5fdb4`.
Rolling back code does not undo already accepted original-file renames: use the
paired source backup if those also need reverting. Preserve old uncertain folders
and receipts; do not run their retired preparation continuation actions.
