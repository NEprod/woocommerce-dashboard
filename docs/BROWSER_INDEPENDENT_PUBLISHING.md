# Browser-independent manual publishing and durable progress

Baseline: `48f157fc27d8c69fcf589737f14d4aaed0e59e0f`.

## Proven code defects versus original production evidence

The baseline `/products/<id>/shopify/review` confirmation POST called the publisher
synchronously inside the web request. After one 20-file/30 MiB slice,
`GalleryPending` deliberately stopped execution and retained the acknowledged
prefix. It did **not** schedule a continuing manual worker. Another fresh approved
review was needed to continue. Browser status/log GETs never executed work.
The previous automatic publisher already had independent continuation, but the
manual HTTP path did not. Consequently “Gallery uploads in progress — no
ProductCreate attempted” could remain indefinitely even after intentional slice
completion, with no actual worker heartbeat. HTTP 200 polling is not liveness.

These implementation facts fit the report, but do **not** establish the original
Unraid operation's exact acknowledgements, lease history or termination cause.
The real operation ID/database/logs were unavailable on the Mac. No original
operation was replayed, deleted, cleared or remotely mutated. No product-associated
media is expected before ProductCreate; staged originals are separate resources.
Do not infer zero successful uploads from the absence of a Shopify product.

## Read-only original-operation probe

Run this in the **existing container terminal**, without deploying this candidate.
It imports no application, opens SQLite read-only and contacts no Shopify service.
It prints bounded safe summaries, not raw scopes, signed URLs, tokens, review
payloads or customer answers. Stop if the actual database path differs.

```sh
python - <<'PY'
import sqlite3,json,zlib,base64,hashlib
from pathlib import Path
SKU='3DCO-0001'
p=Path('/app/instance/site.db')
if not p.is_file():raise SystemExit('Established database not found; nothing created.')
c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
c.execute('pragma query_only=on')
def proof(raw):
 d=json.loads(raw)
 if d.get('encoding')=='zlib-json-v1':
  size=d.get('bytes')
  if type(size) is not int or not 0<size<=1048576:raise ValueError('Invalid evidence byte bound')
  stream=zlib.decompressobj();content=stream.decompress(base64.b64decode(d['content'],validate=True),size+1)
  if len(content)!=size or not stream.eof or stream.unused_data or stream.unconsumed_tail or hashlib.sha256(content).hexdigest()!=d['sha256']:raise ValueError('Invalid compressed evidence')
  d=json.loads(content)
 return d
identities=[]
for r in c.execute('select id,operation_id,state,verification_state,shopify_product_id,updated_at,media_evidence_json from shopify_product_identity where sku=? order by updated_at desc limit 5',(SKU,)):
 item={k:r[k] for k in ('id','operation_id','state','verification_state','shopify_product_id','updated_at')}
 try:
  d=proof(r['media_evidence_json']);images=(d.get('plan') or {}).get('images');uploads=d.get('upload_inputs')
  item.update(stage=d.get('stage'),product_create_attempted=d.get('create_attempted'),
   selected_images=len(images) if isinstance(images,list) else None,
   uploads_acknowledged=len(uploads) if isinstance(uploads,list) else None,
   upload_attempt_state=(d.get('upload_attempt') or {}).get('state'),
   last_upload_acknowledgement=d.get('last_upload_ack_at'),batch=d.get('upload_batch_number'),batches=d.get('upload_batch_total'))
  if isinstance(images,list) and all(type(i.get('size')) is int for i in images):
   item['total_bytes']=sum(i['size'] for i in images)
   item['bytes_acknowledged']=sum(i['size'] for i in images[:len(uploads)]) if isinstance(uploads,list) else None
 except (ValueError,KeyError,TypeError,zlib.error):item['evidence_error']='Evidence invalid; retain recovery, do not retry.'
 identities.append(item)
ops=[]
query="""select id,status,started_at,finished_at,products_attempted,products_succeeded,products_failed,recovery_state,error,scope from catalogue_operation where operation_type='shopify_draft_publish' and (id in (select operation_id from shopify_product_identity where sku=?) or json_extract(case when json_valid(scope) then scope else '{}' end,'$.sku')=?) order by started_at desc limit 8"""
for r in c.execute(query,(SKU,SKU)):
 item={k:r[k] for k in ('id','status','started_at','finished_at','products_attempted','products_succeeded','products_failed','recovery_state','error')}
 try:s=json.loads(r['scope'])
 except ValueError:s={}
 live=s.get('live_state') or {}
 item.update(execution_path='durable manual worker' if s.get('manual_review') else 'automatic worker' if s.get('automatic_publishing') else 'legacy request-scoped / unrecorded',
  stage=live.get('stage') or (s.get('operation_summary') or {}).get('stage'),heartbeat=live.get('heartbeat_at'),
  retained_summary={k:v for k,v in (s.get('operation_summary') or {}).items() if k in ('stage','identity_id','state','mode','create_attempted','uploads_acknowledged','images','native_dimensions','package_assignment','write_attempted')},manual_state=(s.get('manual_review') or {}).get('state'))
 ops.append(item)
tasks=[dict(r) for r in c.execute("select key,state,active_run_id,lease_until,resume_at,last_started,last_completed,last_outcome from scheduled_task where key in ('shopify_publisher','shopify_manual_review')")]
print(json.dumps({'database_writes':0,'shopify_requests':0,'sku':SKU,'identities':identities,'operations':ops,'tasks':tasks},indent=2))
PY
```

Paste the result before starting a new real operation. A known acknowledged prefix
with ProductCreate=false is different from a pending binary request or uncertain
Create. The latter require the existing protected recovery workflow. Missing
historical timestamps/batch numbers mean unknown, not zero or failed.

## Focused implementation

* Confirmation validates the signed ticket/admin acknowledgement, commits the
  exact reviewed proof and current source revision to its original operation,
  and queues the existing durable task executor. It returns HTTP 202 promptly.
* A manual-only disabled scheduler slot is added by forward migration 0021.
  It has no recurring schedule and grants no automatic publishing authority.
  Existing hourly/daily/five-minute settings and ten-minute startup grace remain;
  explicit accepted manual work can execute during the existing manual grace path.
* The worker uses existing task leases, transactional fences and catalogue lock.
  It rechecks the retained administrator, approved connection and source revision
  before writes. It executes the original approved plan, never a newly generated
  broader plan or a forged manual signature. Closing/expiring a browser session
  does not revoke an already committed operation; deleting/revoking its admin or
  changing source/connection stops further writes.
* One operation ID spans every upload slice and readback. The task correlation is
  committed before the first remote call. Safe acknowledged pre-create prefixes
  resume; a pending binary upload, uncertain Create/update or insufficient receipt
  never becomes an automatic retry. Legacy unfinished operations are not adopted
  or replayed automatically after upgrade.
* Progress/events reuse bounded `CatalogueOperation.scope.live_state` and the
  existing logs endpoint (500 lines/256 KiB), not a second event store. Snapshot
  updates join existing acknowledgement commits; batch start/preparation add safe
  boundaries. No per-byte logs or writes from browser GET/status polling.
* Counts/bytes are acknowledged original files only. Batch boundaries are stable
  prefixes using 20 files and 30 MiB; partial/time-limited slices retain that batch
  identity. No transfer percentage is invented inside a single upload.
* Worker health comes from the actual durable task owner/lease, not polling. An
  expired owner, missing heartbeat or overdue waiting task becomes Stalled.
  Healthy async processing/readback and bounded waiting are shown separately.
  Old request-scoped upload receipts explain that no continuing worker was retained.
* Full verification, package acceptance-only wording and scope-limited manual
  updates remain intact. Unknown historical values are labelled unknown.

## Acceptance and release safety

No live Shopify write, original-operation replay, automatic activation, theme,
Product Editor, scanner algorithm, JSON schema, Orders or media-limit change.
`/app/instance` remains authoritative. The automatic policy stays default-off.

Before merchant-approved upgrade, capture the probe above and back up all actual
appdata/catalogue/output/taxonomy mounts. Use the new immutable image, preserve
`gallery-byte-batching-48f157fc27d8` and its paired pre-0021 backup. Downgrade is
refused; do not roll an older image against the upgraded database. Do not restore
receipts across uncertain remote writes without recovery review.

On an isolated staging store: approve a >30 MiB 53-image gallery once, record its
operation ID, close the phone/browser, reopen Operations on another device, and
check acknowledged uploads/bytes, lease/stage, one confirmed product ID, final
readback and original order/alt text. Test backend restart separately from browser
closure. Never press retry based only on HTTP 200 or elapsed time. Native iPhone
Safari/store acceptance remains merchant staging work; mocks are not live proof.

## Validation evidence and limits

* 379 bounded Python regressions passed; 20 focused worker/probe/fencing tests
  passed (these overlap, not an additive total). Nineteen publishing/Operations
  JavaScript checks passed. Compilation and diff whitespace checks passed.
* The browser fixture used 53 unchanged 1280×1280 originals, approximately
  248.587 MiB total. Five isolated Chromium contexts were closed/reopened at
  accepted, upload started, acknowledged batch, pending media processing and
  active readback boundaries. The same operation completed with 53 uploads and
  one mocked Create. Mobile 390×844 and desktop 1440×1000 screenshots were captured;
  external requests were blocked. This is browser-disconnect simulation, not
  physical iPhone Safari or live Shopify acceptance.
* Backend termination is tested separately using abrupt SystemExit before an
  upload acknowledgement and after a remote Create before response persistence.
  Expired-owner replacement retains Recovery Required without another upload or
  Create. Safe acknowledged prefixes resume. Concurrent two/four-owner claim tests
  permit one owner; late old owners cannot save acknowledgement or success.
* Real network-disabled Docker startup/upgrade/restart tests passed with two and
  four web workers. The existing executor accepted one signed approval, uploaded
  six original files, restarted the container using `/app/instance`, then uploaded
  only the remaining seventh and performed one verified mocked Create. This
  >30 MiB fixture exercises persistence, not live transport. The disposable
  container's scheduler alone is paused while test transport is injected; no
  running Unraid container is controlled. Existing package IDs/GIDs, same-SKU
  personalisations, customisations, original receipts and default-off policy pass
  the inherited data checks.
* Two old scanner assertions in `test_phase2_milestone7_live_progress.py` still
  fail and were reproduced on an untouched archive of baseline 48f157f: they expect
  older Completed-with-warnings wording/status rather than the established scanner
  reporting semantics. They are not counted as passes and were not changed.
* The nine-variant mock borrowed the real adapter reader but lacked its existing
  complete-gallery helper. Its fixture now supplies that same helper, preserving
  the real pagination/completeness checks. Native dimensions, package acceptance
  wording, signed manual reviews and source/identity assertions still execute.
* An SQLite contention defect exposed by moving saved-package execution into a
  worker was corrected locally: renew uses the existing transactional fence if
  that executor already owns a pending-receipt write transaction. Remote calls
  still occur after durable receipt commits, without holding a SQLite writer.

Actual original Unraid acknowledgements/worker termination remain unverified
until the read-only probe is supplied. Old unfinished work is not automatically
adopted. Restore a stopped scheduler/runtime before expecting queued work to
continue; status GETs cannot repair or renew it. A stalled/uncertain record is not
an invitation to press Confirm again. Scope-limited or still-unverified domains
remain explicitly incomplete rather than being declared fully synchronised.
