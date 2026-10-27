# Original image byte-batching correction

Starting develop: `938a31630a4bd5147bcb89df848bfa7c0c17d9a1`.

## Cause and correction

`shopify_drafts.common_plan` added every selected original's byte size and rejected
the gallery above `MAX_TOTAL = 30 * 1024 * 1024`. This was a TLC operation-safety
bound, not a Shopify aggregate-gallery limit. The previous 53-image mock used
tiny originals, so it tested the file-count boundary but not this byte boundary.
The new real-sized regression fails on that preflight check before the correction.

No binary gallery is sent in a GraphQL mutation. `stagedUploadsCreate` receives
file metadata; each original is uploaded separately to its approved staged target.
The product-creation mutation receives the ordered staged resource URLs and alt
text, not all original bytes. Preflight already reads/verifies/hashes one file at
a time, retaining metadata rather than the entire gallery in memory.

The unchanged **30 MiB budget now applies per acknowledged upload slice**, together
with the existing maximum twenty files. A prefix is selected using both limits;
once either is reached, the existing durable `GalleryPending` continuation is used.
No global safety limit is increased. An individual file still cannot exceed the
existing TLC 10 MiB transport bound, which is stricter than Shopify's file ceiling.
Original PNG/JPEG/WebP validation remains; API resolution/aspect-ratio checks are
explicit (20 megapixels, at most 100:1), with filename and actual size/dimensions
in actionable errors. No original is compressed, resized, skipped or reordered.

Manual signed Review & Sync and automatic publishing call the **same** uploader.
Manual continuation uses a fresh reviewed approval; the same confirmation ticket
remains idempotent. Automatic work uses the existing fenced queue and authority.
Acknowledgements, exact source hashes, pending-before-binary receipts, nonce,
duplicate-SKU/product protection, async processing and complete readback are
unchanged. An uncertain upload or failed post-create read is not a safe retry.
Readback processing never restarts acknowledged uploads.

The existing unsupported add/remove/replace path for already-linked galleries is
not expanded. Existing alternate text/order and media identity protections remain.
No scanner, schedule, Draft/Live, JSON, Orders, theme/extension or schema changes.

Primary Shopify API requirements:
[Manage media](https://shopify.dev/docs/apps/build/product-merchandising/products-and-collections/manage-media).
The general Help Center product-media page lists some broader limits/formats;
this correction does not advertise those as additional supported TLC capabilities.

## Measurements and real-gallery limitation

The merchant reports a collection folder around **275 MB**, with originals around
**4–6 MB** each. Folder size is not necessarily the selected gallery total, but
this is consistent with the aggregate preflight rejection. The configured Mac
catalogue is empty; its old `3DCO-0001` row lacks a source path. The actual 53
selected Unraid files were **not independently measured here** or modified.

Real uncompressed-PNG fixtures include 53 × 1280×1280 RGB originals (approximately
4.69 MiB each, 248 MiB total), tested through manual and automatic publication.
They need nine byte-bounded slices rather than three count-only slices. Another
five-file fixture uses approximately 9 MiB/file: it splits 3+2, despite being below
twenty files. Smaller and >30 MiB 53-file galleries are also covered. Mock adapters
assert actual file-byte counts and hashes, count/byte bounds, one product creation,
complete media IDs, original order/alt text and no duplicate uploads.

## Optional exact read-only measurement on Unraid

Inside the existing container terminal, the following uses the current gallery
selection helpers with a read-only SQLite connection. It does **not** call
`create_app`, migrate, scan, write source/appdata, start workers or contact Shopify.
It only lists filenames, sizes and dimensions. Stop if the actual database path
differs from the established `/app/instance/site.db`.

```sh
python - <<'PY'
import json
from pathlib import Path
from flask import Flask
from config import Config
from PIL import Image
from sqlalchemy import event
from app import db
from app.models import Product
from app.catalogue_images import product_image_diagnostics, _source_context
from app.shopify_variables import gallery_diagnostics
p=Path('/app/instance/site.db')
if not p.is_file(): raise SystemExit('Database not found; no file created.')
view=Flask('read-only-gallery-measurement')
view.config.from_object(Config)
view.config['SQLALCHEMY_DATABASE_URI']='sqlite:///file:'+str(p)+'?mode=ro&uri=true'
db.init_app(view)
with view.app_context():
 event.listen(db.engine,'connect',lambda c,r:c.execute('pragma query_only=on'))
 product=Product.query.filter_by(sku='3DCO-0001').one()
 context=_source_context(product)
 if not context: raise SystemExit('Actual catalogue/source context unavailable.')
 root,folder,_=context
 rows=product_image_diagnostics(product)
 if product.product_type=='variable': rows=gallery_diagnostics(product,rows)
 measured=[]
 for index,row in enumerate(rows):
  reference=row.get('source_reference')
  if not reference:
   measured.append({'position':index,'error':'Selected source unavailable'});continue
  source=(root/reference).resolve(strict=True)
  source.relative_to(root);source.relative_to(folder)
  item={'position':index,'filename':source.name,'bytes':source.stat().st_size}
  with Image.open(source) as image:
   item.update(width=image.width,height=image.height,format=image.format)
  measured.append(item)
 total=sum(r.get('bytes',0) for r in measured)
 print(json.dumps({'sku':product.sku,'selected_count':len(rows),'measured_count':sum('bytes' in r for r in measured),'total_bytes':total,'total_mib':round(total/1048576,3),'database_writes':0,'shopify_requests':0,'images':measured},indent=2))
PY
```

## Acceptance and rollback

Validation: 18 focused gallery/probe cases and 161 bounded publisher, variable,
manual review, worker-fence and recovery cases passed; ten publishing-client
JavaScript tests passed. The focused cases include original-sized galleries,
explicit single-file errors, actual binary-transport timeout without retry/token
leakage, failed complete readback retaining its product ID, partial acknowledgements,
manual HTTP signed confirmation/idempotency, automatic async processing and both
variable layouts. Existing tests cover newer pending revisions surviving older
completion and simultaneous workers claiming only one conflicting task.

Measured 53-file fixture: 4,918,158 bytes/file; **260,662,374 bytes (248.587 MiB)**
total. Its nine slices were eight × 29,508,948 bytes and one × 24,590,790 bytes,
all below 31,457,280 bytes (30 MiB). A fresh-process manual test reported peak RSS
**419.70 MiB**, including Python/pytest, fixture construction, validation, repeated
previews and mocked publication—not an upload-only memory measurement. No
production/Unraid memory claim is inferred. Application plans retain image
metadata/hashes, not a whole-gallery binary buffer; each original read is still
bounded by the unchanged 10 MiB individual-file limit.

The two-worker, network-disabled Docker upgrade/restart smoke passed with actual
candidate code and disposable `/app/instance`/catalogue mounts. Five approximately
9 MiB originals uploaded 3 before restart and exactly 2 after, using their durable
acknowledgements; the mock ledger rejects duplicate sources. No product creation
or Shopify network request was made by this upload-layer smoke. Full creation and
async readback use mocks in the focused tests. Existing package IDs, two same-SKU
order personalisations, review/recovery receipts, fields/dimensions and default-off
authority remained intact. No new migration was required. Early smoke failures
were fixture setup defects (missing parent directory/deployment-owned path), not
application changes; those fixture paths/configuration were corrected.

Keep automatic publishing paused. Back up the existing `/app/instance`, catalogue
and other actual mounts before a merchant-approved upgrade. No new migration is
introduced: baseline and correction both use `0020_operations_acceptance`.
Retain `operations-acceptance-938a31630a4b` and a matching backup for rollback.
Do not roll back across active/uncertain work or assume an image rollback undoes
remote media/product creation. Review retained receipts before any retry.

On a separate staging store, review the 53-file gallery, confirm continuation,
check every slice is at most 30 MiB/twenty files, and capture all 53 media IDs,
order, alt text and final readback. A gallery total above 30 MiB must not block
review; an invalid individual file must still block with its filename/reason.
Leave live automation disabled until separately approved.
