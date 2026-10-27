# Acknowledged Shopify writes and durable verification completion

Baseline: f768088d74dc5932b57c3503fdb6c119eb715e89. No live Shopify writes.

## Evidence and diagnosis

Merchant evidence says retained product 16043491033471 received the missing
variants, the operation reported failure, then explicit Reconcile established a
linked, current Live product. The original Unraid receipt/database is not available
on the Mac. The exact production exception, timing and transition remain unverified.
No repair or additional publishing of that already-working product is requested.

Code tracing and isolated reproduction establish:

1. Variable update/create responses were checked for userErrors, but their returned
   variant/inventory IDs were discarded. Mapping relied on a subsequent remote
   read. A complete but stale read still showing the initial variant produced
   "Reviewed variant creation remains incomplete/uncertain" after successful writes.
   The worker stopped instead of waiting for readback. Later Reconcile could read
   the complete result and establish the missing mappings.
2. Native/package receipt saves can occur after metadata comparison is recorded.
   The final comparison must bind the final receipt hash, not an earlier snapshot.
   Final verification now occurs after those stages; blocked domains preserve
   metadata evidence without renewing their own incomplete evidence.
3. The manual receipt endpoint reports active queued/running/waiting work as
   `processing`, but the modal treated every processing response as terminal. It
   could stop polling and remove its reconnection ticket at acceptance. The explicit
   background-active flag now keeps it polling; legacy non-continuing processing
   receipts retain their existing meaning. There is still one confirmation POST.

These are proven code defects/reproductions, not proof that every one occurred in
the merchant's original operation. Do not attribute that live incident to Safari,
Shopify propagation, a scan, changed source, lost ownership or stale projection
without the receipt evidence below.

## Read-only inspection in the existing Unraid container terminal

This does not start/import the application, migrate SQLite, scan folders, modify
history or contact Shopify. It prints allowlisted operational data only. Existing
older receipts naturally lack the new acknowledgement/wait marker; absence does
not prove a mutation failed.

```sh
python - <<'PY'
import sqlite3,json,zlib,base64,hashlib,re
from pathlib import Path
SKU='3DCO-0001'
p=Path('/app/instance/site.db')
if not p.is_file():raise SystemExit('Expected database absent; nothing created.')
c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on')
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
def load(raw):
 d=json.loads(raw)
 if d.get('encoding')=='zlib-json-v1':
  n=d.get('bytes')
  if type(n) is not int or not 0<n<=1048576:raise ValueError('Invalid evidence bound')
  z=zlib.decompressobj();b=z.decompress(base64.b64decode(d['content'],validate=True),n+1)
  if len(b)!=n or not z.eof or z.unused_data or z.unconsumed_tail or hashlib.sha256(b).hexdigest()!=d['sha256']:raise ValueError('Invalid evidence integrity')
  d=json.loads(b)
 return d
def safe(v):
 t=str(v or '')[:1200];t=re.sub(r'https?://\S+','[URL redacted]',t)
 return re.sub(r'(?:shpat_|shpca_|shpss_)[A-Za-z0-9_-]+','[credential redacted]',t)
identities=[];ops=[];comparisons=[];jobs=[]
for r in c.execute('select id,product_id,operation_id,source_identity,state,verification_state,shopify_product_id,verified_at,updated_at,media_evidence_json from shopify_product_identity where sku=?',(SKU,)):
 o={k:r[k] for k in ('id','product_id','operation_id','state','verification_state','shopify_product_id','verified_at','updated_at')}
 try:
  d=load(r['media_evidence_json']);plan=d.get('plan') or {};v=plan.get('variants') or []
  o.update(stage=d.get('stage'),error=safe(d.get('error')),expected_variants=len(v),mapped_variants=len(d.get('variant_maps') or {}),
   expected_images=len(plan.get('images') or []),acknowledged_uploads=len(d['upload_inputs']) if isinstance(d.get('upload_inputs'),list) else None,
   completed_steps=d.get('completed_steps'),variant_creates_attempted=d.get('variant_creates_attempted'),
   variant_write_acknowledgements=d.get('variant_write_acknowledgements'),verification_wait=d.get('verification_wait'),
   verification=d.get('verification'),observed_status=d.get('observed_status'),checked_at=d.get('checked_at'),
   reviewed_source_digest=plan.get('digest'),receipt_digest=digest(r['media_evidence_json']),
   publication_state=(d.get('publication_update') or {}).get('state'),publication_completed=(d.get('publication_update') or {}).get('completed'),
   intended_status=(plan.get('publication') or {}).get('intent',{}).get('status'))
 except (ValueError,KeyError,TypeError,zlib.error):o['error']='Invalid retained evidence; preserve protected recovery.'
 identities.append(o)
 for comp in c.execute('select compared_at,summary_json from shopify_product_comparison where source_identity=?',(r['source_identity'],)):
  s=json.loads(comp['summary_json']);comparisons.append({'compared_at':comp['compared_at'],
   'classification':s.get('classification'),'actual_status':s.get('actual_status'),'read_complete':s.get('read_complete'),
   'identity_digest_matches':s.get('identity')==digest(r['media_evidence_json']),'full_compared_at':s.get('full_compared_at'),
   'equality_at':s.get('equality_at'),'refresh_failure':s.get('refresh_failure') and safe(s['refresh_failure'].get('message'))})
 for job in c.execute('select state,operation_id,requested_revision,verified_revision,view_pending,view_json,error from shopify_publish_job where product_id=?',(r['product_id'],)):
  view=json.loads(job['view_json'] or '{}');status=view.get('state') or {}
  jobs.append({'state':job['state'],'operation_id':job['operation_id'],'revision_matches':job['requested_revision']==job['verified_revision'],
   'view_pending':job['view_pending'],'saved_state':status.get('state'),'saved_label':status.get('label'),'error':safe(job['error'])})
q="""select id,status,started_at,finished_at,recovery_state,products_attempted,products_succeeded,products_failed,error,scope from catalogue_operation where operation_type='shopify_draft_publish' and (id in (select operation_id from shopify_product_identity where sku=?) or json_extract(case when json_valid(scope) then scope else '{}' end,'$.sku')=?) order by started_at desc limit 10"""
for r in c.execute(q,(SKU,SKU)):
 o={k:r[k] for k in ('id','status','started_at','finished_at','recovery_state','products_attempted','products_succeeded','products_failed')};o['error']=safe(r['error'])
 try:s=json.loads(r['scope'])
 except ValueError:s={}
 live=s.get('live_state') or {};m=s.get('manual_review') or {}
 o.update(resolved_previous_outcome=s.get('resolved_previous_outcome'),manual_state=m.get('state'),
  accepted_source_revision=m.get('source_revision'),signed_source_digest=(m.get('reviewed') or {}).get('digest'),
  stage=live.get('stage'),heartbeat=live.get('heartbeat_at'),worker=s.get('manual_worker'),
  discord=[{'event':k,'state':v.get('state'),'attempts':v.get('attempts')} for k,v in (s.get('shopify_discord_events') or {}).items()])
 ops.append(o)
tasks=[dict(r) for r in c.execute("select key,state,active_run_id,owner,lease_until,resume_at,last_outcome from scheduled_task where key in ('shopify_manual_review','shopify_publisher','shopify_products')")]
print(json.dumps({'database_writes':0,'shopify_requests':0,'identities':identities,'comparisons':comparisons,'jobs':jobs,'operations':ops,'tasks':tasks},indent=2))
PY
```

Please send this output, not a new publishing attempt. It distinguishes an actual
mutation ambiguity, acknowledged work awaiting verification, stale comparison,
manual reconciliation resolution, pending queue state and notification outcome.
No existing history or working product is to be cleared or replayed.

## Implementation contract

The same signed review, source/configuration guards, task leases, original
operation ID and mutation executor remain. Returned variant IDs/SKUs/options and
InventoryItem IDs are strictly matched to requested identities and committed
alongside each acknowledged variant step. A response with missing/ambiguous IDs or
unknown transport outcome remains protected, never a candidate for write retry.

After required metadata mutations acknowledge, a small marker in the EXISTING
identity receipt records pending verification, its phase, original operation and
source digest. Existing workers perform bounded reads without repeating product,
image or variant mutations. Known pre-update SKU values, temporarily missing
acknowledged IDs, managed-field propagation and fully acknowledged availability
readback may wait. Changed correlation, product/inventory identity and unreviewed
options remain conflicts. Every successful claim still requires full verification.

Read-only inspection cannot activate a Draft. Remaining original approved
availability/native/package stages run only in the accepted worker's existing
authority context. A failed/uncertain mutation is never converted into a read wait.
After the final receipt save, full verification records the matching final
identity fingerprint and invalidates the existing saved view. Newer local changes
are not overwritten or declared synced using an older source.

Pending verification has no failed-product counter or terminal failure notification.
Final success requires all managed domains and approved actual Draft/Live status.
Discord uses the existing durable outcome and delivery deduplication, not a new
notification framework. Optional unsupported stages remain explicitly deferred,
not silently successful. The existing saved-view worker refreshes Products from
the authoritative comparison; no merchant Reconcile is needed for normal delayed
readback within the supported bounds.

## Bounds and remaining recovery requirements

Twelve unsuccessful verification observations or ten minutes without conclusive
evidence stop automatic continuation in Recovery Required. No ProductCreate,
upload, variant or acknowledged publication write is replayed to fix a read.
Persistent mismatches, changed source/configuration, schema/authentication errors,
unknown mutation acknowledgements, native/package uncertainty and unsupported
destructive operations still require appropriate review/recovery. This is deliberate,
not a promise that all failures can be resolved unattended.

Receipts created before this release that are already uncertain/failed are NOT
automatically adopted or cleared. The merchant's now-working product remains
untouched. Native iPhone Safari and actual Shopify propagation remain staging
acceptance; mocks are not independent production evidence.

## Safe staging acceptance

1. Keep automatic publishing off on live; capture the read-only probe and back up
   the existing /app/instance and catalogue/output/taxonomy mounts before manual
   deployment. No database migration or mount relocation is introduced.
2. On a separately authorised isolated staging store, approve a partial Draft
   completion with 14 variants/53 images. Record the original operation ID.
3. Close Safari/reopen the modal and Operations from another session. Active
   processing must continue polling or reconnect without another confirmation.
4. Verify acknowledgement counts/IDs, Pending verification rather than Failed,
   eventual Up to Date, complete correct options/SKUs/prices, intended actual
   status, one product and no duplicate uploads/variant writes. No Reconcile step.
5. Separately interrupt the backend after acknowledged writes, then restart with
   the same appdata. Observe read-only continuation and the same operation ID.
6. Test uncertain transport separately: it MUST retain Recovery Required and never
   replay. Test a persistent conflict: it must not become green or send success.
7. Confirm saved Products status, Identity/History, original operation and Discord
   agree. Preserve receipts and evidence. Do not experiment on 3DCO-0001.

Rollback uses the immutable f768088 image after reviewing/draining active work;
both releases retain schema 0021. Keep current identity/acknowledgement data. The
old image cannot continue the new verification marker, so do not roll back over
an active operation or restore old receipts across newer/uncertain remote writes.

## Changed production files and validation scope

* shopify_variables.py: persist matched acknowledgement IDs and perform final
  verification after receipt-changing stages; preserve strict identity guards.
* shopify_variable_readback.py: bounded phases in existing identity receipts and
  operation IDs, using the existing executor/fences. No new infrastructure/table.
* manual_publishing.py: resume those reads, finalise the same operation and keep
  pending/recovery counters and progress truthful.
* automatic_publishing.py: the same continuation under existing automatic authority;
  retain the original operation link when read-only completion finishes the work.
* shopify_drafts.py: dispatch its existing read-only finaliser to pending verification.
* shopify_products.py: accurate Pending verification wording, with unchanged status
  classes and ordinary non-continuing media behaviour.
* shopify_progress.py: distinguish active background work for the modal and name
  its pending verification stage accurately.
* shopify-publish.js: retain polling/reconnection for explicitly active work, with
  exactly one confirmation POST. No modal layout or CSS changes.

Completed bounded checks: 221 Python regressions, 32 focused completion checks,
24 automatic-publishing checks and 37 JavaScript checks (overlapping groups, not
an additive unique-test total). The automatic group includes completion entirely
inside read-only finalisation without losing its original operation ID. Browser
checks use isolated Chromium, not native Safari. Docker checks use network-disabled
disposable mounts, not Unraid or real Shopify.

Six failures introduced during the first implementation concerned existing native
permission/uncertainty and deferred-availability semantics. They were regressions,
not baseline failures: targeted six-case reruns and the later bounded run passed
after correction. No failing assertion was waived as obsolete. Final release
evidence records exact-image Docker results and any remaining limitations.
