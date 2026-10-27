# Variable creation: retained Draft, coherent readback and approved completion

Baseline: `99fb03e7ab2da2f15702881aa4d2bab0e169506f`.

## Original evidence and limits

Merchant evidence: 3DCO-0001 has 14 expected variations and 53 images; uploads
reached 53/53, ProductCreate returned `gid://shopify/Product/16043491033471`, then
gallery pagination raised the revision/correlation error and Shopify showed one
Draft variant. These observations are not an independently obtained API receipt.
The Mac database is an older catalogue projection with no Shopify identity table;
the original Unraid approval/operation evidence is unavailable locally.

The old guard compared **remote** Product id, updatedAt, status and the
tlc_dashboard.publish_attempt correlation metafield across gallery pages. Its old
generic message retained no before/after header values. Therefore the precise
original changing field, values and concurrent activity cannot be reconstructed
from that message. It is not proof that local JSON changed. Do not claim that
Shopify async processing, another worker or a scan definitely caused this case.

Reproduction confirms that an updatedAt-only change between media pages aborts
the baseline workflow after the acknowledged ProductCreate and before its first
variant mutation. ProductCreate creates one initial variant; TLCDashboard updates
that variant and then separately bulk-creates the remaining supported variants.
Thus the failure point explains the observed one-variant shape, conditional on
the retained stage/acknowledgements matching. The earlier "Variable review failed
before remote writes" is a separate preflight path: its root cause is NOT proven
by the later pagination error. Future preflight receipts retain the safe cause.

Shopify documents the [initial-variant-only ProductCreate contract](https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/productCreate)
and [separate bulk variant creation](https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/productVariantsBulkCreate).
The application retains its existing 32-variant/three-option contract; platform
capacity does not authorise destructive restructuring or silently extend TLC limits.

## Read-only original receipt probe (existing Unraid container terminal)

No application import, startup, migration, scan, database write or Shopify request.
No credentials, signed URLs, customer answers or full publishing payloads printed.
Unknown historic snapshot headers remain unknown.

```sh
python - <<'PY'
import sqlite3,json,zlib,base64,hashlib,re
from pathlib import Path
SKU='3DCO-0001'
p=Path('/app/instance/site.db')
if not p.is_file():raise SystemExit('Expected database absent; nothing created.')
c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
c.execute('pragma query_only=on')
def decode(raw):
 d=json.loads(raw)
 if d.get('encoding')=='zlib-json-v1':
  n=d.get('bytes')
  if type(n) is not int or not 0<n<=1048576:raise ValueError('Evidence byte bound invalid')
  z=zlib.decompressobj();v=z.decompress(base64.b64decode(d['content'],validate=True),n+1)
  if len(v)!=n or not z.eof or z.unused_data or z.unconsumed_tail or hashlib.sha256(v).hexdigest()!=d['sha256']:raise ValueError('Evidence integrity invalid')
  d=json.loads(v)
 return d
def safe(value):
 value=str(value or '')[:1200]
 value=re.sub(r'https?://\S+','[URL redacted]',value)
 value=re.sub(r'(?:shpat_|shpca_|shpss_)[A-Za-z0-9_-]+','[credential redacted]',value)
 return value
rows=[]
for r in c.execute('select id,operation_id,state,verification_state,shopify_product_id,updated_at,media_evidence_json from shopify_product_identity where sku=?',(SKU,)):
 out={k:r[k] for k in ('id','operation_id','state','verification_state','shopify_product_id','updated_at')}
 try:
  d=decode(r['media_evidence_json']);plan=d.get('plan') or {};uploads=d.get('upload_inputs');variants=plan.get('variants') or []
  out.update(stage=d.get('stage'),error=safe(d.get('error')),create_attempted=d.get('create_attempted'),
   approved_source_digest=plan.get('digest'),reviewed_variant_count=len(variants),reviewed_image_count=len(plan.get('images') or []),
   uploads_acknowledged=len(uploads) if isinstance(uploads,list) else None,
   upload_attempt_state=(d.get('upload_attempt') or {}).get('state'),created_media_count=len(d.get('created_media') or []),
   completed_steps=d.get('completed_steps'),variant_creates_attempted=d.get('variant_creates_attempted'),
   variant_maps=d.get('variant_maps'),verification=d.get('verification'),observed_status=d.get('observed_status'),
   correlation_hash=hashlib.sha256(str(plan.get('nonce','')).encode()).hexdigest()[:16],
   gallery_snapshot_restarts=d.get('gallery_snapshot_restarts'),
   reviewed_variants=[{'sku':v.get('sku'),'identity':v.get('identity'),'options':v.get('options'),'price':(v.get('managed') or {}).get('price')} for v in variants],
   remote_updated_at_at_review=plan.get('remote_updated_at'),publication_attempted=(d.get('publication_update') or {}).get('attempted'))
 except (ValueError,KeyError,TypeError,zlib.error):out['error']='Evidence invalid; preserve protected recovery.'
 rows.append(out)
ops=[]
q="""select id,operation_type,status,started_at,finished_at,recovery_state,error,scope from catalogue_operation where id in (select operation_id from shopify_product_identity where sku=?) or json_extract(case when json_valid(scope) then scope else '{}' end,'$.sku')=? order by started_at desc limit 8"""
for r in c.execute(q,(SKU,SKU)):
 o={k:r[k] for k in ('id','operation_type','status','started_at','finished_at','recovery_state')};o['error']=safe(r['error'])
 try:s=json.loads(r['scope'])
 except ValueError:s={}
 m=s.get('manual_review') or {};a=m.get('reviewed') or {};live=s.get('live_state') or {}
 o.update(approval_state=m.get('state'),accepted_source_revision=m.get('source_revision'),signed_source_digest=a.get('digest'),
  signed_remote_digest=a.get('remote_digest'),stage=live.get('stage'),heartbeat=live.get('heartbeat_at'),
  publishing_progress={k:v for k,v in (live.get('publishing') or {}).items() if k in ('total_images','uploads_acknowledged','batch','total_batches','bytes_acknowledged','total_bytes','product_create_attempted','shopify_product_id','pending_stage')},worker=s.get('manual_worker'))
 ops.append(o)
tasks=[dict(r) for r in c.execute("select key,state,active_run_id,owner,lease_until,resume_at,last_started,last_completed,last_outcome from scheduled_task where key in ('shopify_manual_review','shopify_publisher','shopify_products')")]
overlap=[];runs=[]
if ops:
 start,end=ops[0]['started_at'],ops[0]['finished_at']
 overlap=[dict(r) for r in c.execute("select id,operation_type,status,started_at,finished_at,recovery_state from catalogue_operation where julianday(started_at)<=julianday(coalesce(?,'now')) and julianday(coalesce(finished_at,datetime('now')))>=julianday(?) order by started_at limit 30",(end,start))]
 runs=[dict(r) for r in c.execute("select id,task_key,trigger,state,started_at,completed_at from scheduled_task_run where julianday(coalesce(started_at,created_at))<=julianday(coalesce(?,'now')) and julianday(coalesce(completed_at,datetime('now')))>=julianday(?) order by created_at limit 30",(end,start))]
print(json.dumps({'database_writes':0,'shopify_requests':0,'identities':rows,'operations':ops,'tasks':tasks,'overlapping_operations':overlap,'overlapping_task_runs':runs},indent=2))
PY
```

Paste this output before another live publishing attempt. It should establish
whether the approved plan really contains 14 identities, all 53 uploads and media
IDs were acknowledged, and variant writes were never attempted. An empty or
ambiguous receipt is not permission to retry. No historic timestamps are invented.

For independently read-only Shopify evidence, an already-authorised GraphiQL
session can execute the following **query** with the retained ID. No permission or
app changes are needed. Repeat with `after` equal to each returned endCursor until
hasNextPage=false; retain each updatedAt/status/correlation value privately.

```graphql
query RetainedDraftInspection($id: ID!, $after: String) {
  product(id: $id) {
    id status updatedAt
    metafield(namespace: "tlc_dashboard", key: "publish_attempt") { value }
    options { id name position optionValues { id name } }
    variants(first: 32) {
      nodes { id sku price selectedOptions { name value } inventoryItem { id sku } }
      pageInfo { hasNextPage }
    }
    media(first: 20, after: $after) {
      nodes { id alt status mediaContentType }
      pageInfo { hasNextPage endCursor }
    }
  }
}
```

Variables: `{"id":"gid://shopify/Product/16043491033471","after":null}`.
Do not paste access tokens or signed upload URLs. If read access is unavailable,
leave remote state unverified; do not substitute a mutation or change permissions.

## Focused correction and retained guards

* An updatedAt-only mismatch discards the ENTIRE variable/product snapshot and
  retries reads, at most twice within the bounded read deadline. It never appends
  new media pages to old variants/options. Persistent churn remains an error.
* Product ID, lifecycle status and correlation changes are not ignored or retried
  as harmless. Safe diagnostics retain exactly which header changed, timestamps
  and hashed correlation values; successful restart diagnostics are bounded.
* Signed source/configuration/identity/remote digests still fence every write.
  Expected remote revision changes after this publisher's acknowledged steps are
  read afresh, not compared with an unrelated stale local fingerprint.
* Existing read-only reconciliation cannot invent or create the missing variants.
  A narrow new Identity/History action prepares a fresh signed completion review
  ONLY for an acknowledged retained Draft stopped before any variant write.
  Preparation is Shopify reads only; it does not adopt mappings or clear recovery.
* Completion requires unchanged original local plan, exact upload hashes/count,
  returned media IDs/order/alt text, owned correlation, a uniquely blank initial
  variant with expected options, no prior/uncertain variant mutation and fresh
  complete remote evidence. The existing initial variant is updated and only
  conclusively absent combinations are bulk-created. No ProductCreate, media
  upload/deletion or destructive variant operation is available on this path.
* Explicit new merchant acknowledgement queues the existing browser-independent
  executor. Source/remote changes after review reject it. Later uncertain variant
  writes cannot reuse this narrow pre-variant continuation; read-only recovery is
  required. Original operation history and recovery evidence are retained.
* Draft is retained until all variants, metadata and media verify. Accepted manual
  async work may finish only its original approved availability after read-only
  processing completes and source checks still pass. Final success requires the
  complete supported workflow, not Create or uploads alone.

No individual/batch media bounds, scanner, JSON inheritance, automatic defaults,
Orders, customer fields, Production Time, variant deletion policy or Shopify
extension/theme changes. No database migration or production JSON rewrite.

## Handling the real retained product

Do NOT delete/recreate product 16043491033471, remove its mapping, re-upload the
53 acknowledged files, press an old confirmation again or clear recovery.
Capture the probe, preserve a full appdata backup and inspect Shopify read-only.
After separately approved deployment, Identity/History → Review missing variant
work performs the strict read-only assessment above. Stop if any prerequisite
fails. If a fresh completion review is possible, inspect the same retained GID,
all 14 options/SKUs/prices and all 53 mapped images before authorising any writes.
No such real approval or recovery has been performed during implementation.

If a variant mutation was attempted/uncertain, media receipts differ, initial SKU
was edited, source changed or Draft was activated externally, this narrow path
intentionally refuses continuation. Do not force it; supply the receipts for a
separate evidence-based assessment. Existing 32-variant/3-option application
bounds remain distinct from Shopify platform capacity.

## Isolated validation and acceptance limits

The bounded publisher/variable/manual-worker/publication suites passed 235 Python
checks. A further 58 media/Operations checks and 36 JavaScript checks passed.
The focused variable-creation suite covers both supported layouts, 14 variants and
53 images exceeding 30 MiB, coherent whole-snapshot restarts, persistent revision
churn, nonce/status/identity conflicts, stale approval, receipt corruption, missing
acknowledgements, local edits and reconciliation exclusion. Totals overlap; they
are not added together as unique tests.

The browser fixture tests read-only inspection, explicit fresh approval, closing
the browser and reopening on another session, retained product identity and exact
14-variant/53-image completion at desktop and mobile widths. It uses isolated
Chromium, not physical iPhone Safari or a real store. The Docker fixture persists
an actual partial mock Draft and all 53 upload receipts in /app/instance, restarts,
then accepts a new signed continuation and verifies all 14 variants without another
ProductCreate or image upload. Existing Orders/package/customisation data checks
are inherited unchanged. No production volume or Shopify transport is used.

Mock success is not real-product acceptance. Original Unraid evidence, actual
Shopify read-only assessment and separately authorised staging/live recovery remain
outstanding. No real product was repaired or activated during this work.
