# Inventory images and persistent storage audit

## Image upload and editor behaviour

The former limits were `_image` in `app/inventory/routes.py` (2 MiB, 12 MP,
640-pixel JPEG) and the inventory request bound in `app/__init__.py` (3 MiB).
Only inventory item-editor requests now allow 26 MiB, including multipart
overhead; each source image is independently capped at 25 MiB. Other inventory
requests retain their 3 MiB bound, form memory remains 128 KiB and parts 125.
Read-only preview endpoints have a tighter 16 KiB JSON bound.

`app/inventory/images.py` validates contents, not uploaded names/MIME claims.
JPEG, PNG and WebP are decoded with Pillow. HEIC/HEIF is available only if the
optional `pillow_heif` decoder is already installed; it is **not** in the current
requirements/runtime and is clearly reported unsupported. No dependency or
container build is added by this task.

One still image, at most 60 MP, is decoded at a time per process. JPEG draft
decoding reduces peak memory; EXIF orientation is applied. The image is resized
proportionally to at most 1600 pixels without upscaling and encoded as WebP
quality 85, retaining alpha. A fresh pixel image strips EXIF/GPS/XMP/comments and
source names. Output size is quality-driven, not forced to 150–400 KB; complex
textures/noise may exceed that range. Sources and output are bounded memory
buffers; Werkzeug may spool multipart files temporarily and closes them with
the request. No upload filename becomes a server path.

**Storage deliberately remains the existing `inventory_item.image` SQLite
blob**, not a new image directory/registry. The persistent file is
`/app/instance/site.db`. Item metadata and the new image commit together in the
same transaction; validation/processing/storage failures roll back to the old
record. The original source is not retained. Old JPEG blobs and permanent
`/production/item/<id>/image.jpg` URLs remain valid; responses now use actual
JPEG/PNG/WebP content types and `nosniff`, with existing authenticated access and
private/no-store caching. No appdata directory is publicly served. There are no
new image filenames, mounts, environment variables or migrations.

The material specifications selector is **display-only**, not a category system.
Automatic mode uses saved filament/sheet fields, known category hints and the
sheet stock unit. Explicit Filament/Sheet/Other/Both choices control visibility;
hidden inputs are neither disabled nor erased. Existing mixed specifications can
be shown together. No specification display preference is persisted separately.

Purchase unit-cost preview calls the same Decimal `purchase_unit_rate` and
conversion rules used by inventory's saved purchase-basis cost. It is not a
second browser formula or stock receipt. Weighted receipt-history cost remains
authoritative for existing stock valuation. Blank cost is unknown; zero is zero.

## Representative optimisation measurements

Synthetic high-resolution fixtures (not real customer photographs), one recorded
run; noise generation can vary sizes slightly between runs:

| Source | Original bytes | Optimised bytes | Output dimensions |
|---|---:|---:|---|
| Detailed gradient/noise JPEG | 6,444,419 | 255,418 (249 KiB) | 1600 × 1200 |
| High-entropy PNG | 15,366,101 | 648,790 (634 KiB) | 1600 × 1200 |

Mean for these two fixtures: 452,104 bytes (442 KiB), 1600 × 1200. Small fixtures
retain their original dimensions; an alpha fixture retained its 150 × 100 size
and transparency. The complex PNG intentionally exceeds the aspirational size
target rather than being aggressively degraded.

## Storage audit and evidence level

This is a repository/template audit plus isolated restart/container tests, **not
a live Unraid mount inspection**. Host paths below are the checked-in development
template values, not a claim about the running server. Production mappings are
deliberately blank for operator selection. No live path was moved or rewritten.

| Data category | Current application write path | Container mount | Checked-in development host path | Persistence evidence / correction |
|---|---|---|---|---|
| Inventory, barcodes, ledger, batches, material/component images, BOM scenarios, costing/equipment/fee profiles | `site.db` and SQLite journal/WAL beside it | `/app/instance` | `/mnt/cache/appdata/woocommerce-dashboard-dev/instance` | Template bind confirmed; isolated persistence tested. Verify actual running bind manually. No new image mount. |
| Users, orders, local promotions, Shopify mappings/approvals/readback/operations, product BOM settings | Same `site.db` | `/app/instance` | Same appdata path | Same authoritative database; no duplicate store or schema change. |
| Commerce/onboarding JSON, protected auth files, relationship recovery journals and instance locks | `commerce.json`, `onboarding.json`, `relationship-transactions/`, relevant lock/auth files | `/app/instance` | Same appdata path | Existing instance-relative writers/permissions retained. Signing keys/credentials untouched. |
| SQLite recovery points | `backups/` beside database, recovery markers | `/app/instance` | Same appdata path | Existing secure atomic backup/retention facilities. Back up appdata externally too. |
| Authored collection/product/variant metadata, option overrides, source images, `.scanned`, `.update`, SKU indexes and metadata backups | Files below configured `PRODUCT_FOLDER`; rollback/staging beside affected catalogue files | `/catalogue` | `/mnt/user/data/tlc/09_Development_Products/products/` | Template bind confirmed; existing root/symlink/identity safeguards unchanged. No catalogue writes for image uploads. |
| Intake originals, Prepared results, private staging and rollback work | Configured `INTAKE_ROOT`, `Prepared/`, `.catalogue-intake-staging/` and operation-owned work | `/intake` | `/mnt/user/data/tlc/09_Development_Products/intake/` | Optional bind; Compose requires `compose.intake.yaml` when this feature is used. Do not create/enable an unmounted `/intake`. Existing stale-work cleanup/retained recovery evidence remains. |
| Taxonomy registry and registry backups | `registry.json` and controlled adjacent replacements/backups | `/taxonomy` | `/mnt/user/data/tlc/09_Development_Products/taxonomy/` | Optional bind; Compose requires `compose.taxonomy.yaml`. Docker ENV alone is not persistence. |
| Publisher technical logs | `logs/publishing.log` and rotation/lock files | `/app/instance` | Same appdata path | Existing 512 KiB rotation + 3 backups; SQLite retains detailed evidence. |
| Gunicorn/application stdout/stderr | Docker logging driver | Docker-managed host logs | Host Docker storage, not an appdata directory | Compose + both XML templates configure `local`, 10 MiB × 5, compression. Verify actual container log options. |
| Inventory stock cards, barcode SVGs, printable pages, browser downloads | Rendered HTML / BytesIO responses; final download belongs to user's device | None required | Not applicable | No server-retained print assets/files. Identifiers remain in SQLite. No multi-card/PDF service added. |
| Legacy WordPress output / CSV utility | Historical `/output`; generic `write_csv` has no active application call site | Legacy `/output` entry still exists in XML | `/mnt/user/data/tlc/09_Development_Products/uploads/` | No new dependency on it. Historical files and template left intact; no automatic removal. |
| Legacy standalone desktop utilities | Operator-selected folders in e.g. `utils/group_images_window.py` | Not part of registered web/scheduler runtime | Not verified | These GUI helpers are not launched by the Docker app. Their arbitrary local selections are not a persistent-root guarantee; assess separately before standalone use. |
| Private customer uploads | Existing authenticated Worker/Cloudflare R2; local order references/evidence in SQLite | External private store + `/app/instance` for references | R2 deployment/bucket not verified here | Never copied into inventory/static/appdata image routes. Existing credential/store-bound access retained. Customer-photo development files untouched. |
| Temporary upload/optimisation data | Request-owned system temp spool and in-memory buffers | Ephemeral by design | Not applicable | Per-request 26 MiB / per-image 25 MiB bounds; request closes temporary streams. No retained source/temp image files. |
| Python/package caches and runtime hooks | No application cache writes intended; Docker sets `PYTHONDONTWRITEBYTECODE=1` | None required | Not applicable | Build/package caches are image/build-time, not business data. Operator-installed hooks (e.g. Tailscale's own state/logs) are outside this repository and need separate live retention/mount verification. |

Managed startup already verifies separate marked storage roots, environment
identity, SQLite location and unsafe aliases. That does **not** prove a live bind
exists from a source checkout alone. An unmounted `/app/instance` can still hold
a legacy/unmanaged installation's database in the container layer and would lose
it on replacement. The same risk applies to manually enabled unmounted optional
roots. Do not copy/migrate/delete anything automatically to address this.

If a live mapping is missing, stop replacement plans, back up its contents,
confirm the existing host directory/permissions/environment identity, copy with
verification to that agreed location, and then configure the matching bind with
the operator. Do not invent a production path or point it at development data.

## Read-only operator checks (manual, no change applied)

```sh
docker inspect WooCommerceDashboard-dev --format '{{range .Mounts}}{{println .Destination .Source .RW}}{{end}}'
docker inspect WooCommerceDashboard-dev --format '{{json .HostConfig.LogConfig}}'
```

Confirm Application Data maps `/app/instance` read/write to the **existing** dev
appdata directory, Product Catalogue maps `/catalogue`, and enabled optional
features map `/intake` and `/taxonomy`. No new mapping is required for photographs.
Compose has appdata/catalogue binds in its base file and opt-in overlays for the
other two. There is no bundled nginx/Traefik request limit; Gunicorn has no
smaller configured body cap here. The live proxy/Tailscale path is not inspected.
If it imposes a smaller limit, configure that route for at least a 26 MiB request
(for nginx, a scoped `client_max_body_size 26m`), then test manually. No Unraid,
proxy, app-installation or credential changes are made by this task.

Physical iPhone/Safari, printer/scanner and live-Unraid acceptance remain manual.
The automated browser tests use desktop and phone Chromium viewports, and the
container persistence test uses an existing image with current source mounted
read-only and a disposable temporary appdata bind—no image build or deployment.

## Changed files

- Backend: `app/__init__.py`, `app/inventory/images.py`,
  `app/inventory/routes.py`, `app/inventory/service.py`.
- Editor: `app/templates/inventory/edit_item.html`,
  `app/templates/inventory/upload_error.html`,
  `app/static/assets/js/inventory-item-form.js`, `app/static/assets/css/inventory.css`.
- Tests: `tests/test_inventory.py`, `tests/test_inventory_image_uploads.py`,
  `tests/browser/material_upload_fixture.py`, `tests/browser/material_upload.browser.mjs`,
  `tests/docker_material_storage_smoke.py`.
- Documentation: this audit and `docs/STORAGE_RETENTION.md`.
