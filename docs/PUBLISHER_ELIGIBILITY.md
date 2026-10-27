# Automatic publisher: scanned revisions and no-op work

Baseline: 31156379009ec9bd9ac010ffca841e5a9ea587a2. No live Shopify writes.

## Confirmed defects and correction

The regression tests fail on the baseline before the production correction:

* A completed variable rescan fingerprints ORM Numeric values before commit as
  `45`, `70`, etc. Reloading the same unchanged database rows yields `45.000`,
  `70.000`. The previous string-based hash changes despite equal measurements.
  Workspace/queue fingerprints now canonicalise those numeric values only;
  signed source/plan guards and actual measurements are unchanged.
* The queue's variable image references followed relationship load order, whereas
  approved galleries use menu order and ID. Queue references now use that same
  order. Actual image paths, sizes, mtime and ctime remain fingerprint inputs.
* Complete matching manual publication/reconciliation did not settle the pending
  queue. A saved Up to Date view could coexist with eligible pending work. Exact
  scanned-revision equality now settles that work atomically. Existing pending
  rows use the exact same persisted catalogue status projection as Products.

Up to Date skips source inspection completely. A pending old revision already
satisfied by the persisted matching catalogue state is consumed as a no-op. If
the old scan hash differs from the comparison, it is NOT marked newly verified
and the newer comparison is NOT adopted as scan evidence. The operation cannot
write. Processing and blocked saved statuses also skip source inspection (known
acknowledged readback continuations retain their original safe executor).

Source freshness, signed plan, identity and mutation checks still apply to
executable publishing work at the boundary. Unknown or genuinely different
scanned/source revisions cannot borrow newer manual read evidence for a write. No
revision is invented, renewed or adopted by the scheduler. An old erroneous scan
revision requires the existing focused collection scan, not a silent upgrade or
full scan. Publication/required-field checks and protected recovery still apply.

Unchanged verified products are not selected by the normal pending/retry query.
Stale matching jobs settle as persisted queue no-ops, with no mutation, new
publication receipt, Discord success or renewed remote freshness timestamp.
Conclusive drift can still reopen the same scanned revision for targeted work.
CAS settlement cannot consume a newer scan revision or another worker's claim.

Selection hashes, generation, trigger, scan receipt and comparison checkpoint are
retained in existing scheduled-run items; source-guard receipts retain the same
diagnostic plus explicit `write_attempted: false`. No new storage/schema or event
system. Preflight failures have no parent mutation-item rows because no parent
mutation executor ran; the root receipt retains the blocked product and reason.
Saved catalogue views label this source-freshness condition **Scan Required**.
No page loads or idle scheduling acquire new image-walk work.

## Original Unraid evidence limitation

The operation ID/SKU/database for the reported incident is not available locally.
The defects above are reproducible, but do not prove that the real source was
unchanged, which one affected that operation, or whether other concurrent work ran.
The older receipt may not contain the newly added selection diagnostics. Preserve
the original failed receipt; do not retry a mutation or clear recovery to investigate.

## Narrow read-only probe (existing Unraid container terminal)

Paste this directly into the TLCDashboard container console. Optionally set the
known operation ID; otherwise it selects the last ten exact source-guard failures.
It imports neither Flask nor the application, writes nothing, scans nothing and
makes no Shopify requests. It outputs only operational identifiers/hashes/statuses,
not JSON customer configuration, credentials, answers or signed approvals.

```sh
python - <<'PY'
import sqlite3,json
from pathlib import Path
OPERATION_ID = ''
p=Path('/app/instance/site.db')
if not p.is_file(): raise SystemExit('Database absent; nothing created.')
c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True); c.row_factory=sqlite3.Row
c.execute('pragma query_only=on')
def obj(value):
 try: return json.loads(value or '{}')
 except (ValueError,TypeError): return {}
ops=c.execute('select * from catalogue_operation where id=?',(OPERATION_ID,)).fetchall() if OPERATION_ID else c.execute(
 "select * from catalogue_operation where error like 'Local source changed after its completed scan.%' order by started_at desc limit 10").fetchall()
result=[]
for op in ops:
 s=obj(op['scope']); a=s.get('automatic_publishing') or {}; pid=s.get('product_id') or a.get('product_id')
 out={k:op[k] for k in ('id','operation_type','status','started_at','finished_at','recovery_state','products_attempted','products_succeeded','products_failed')}
 out.update(product_id=pid,sku=s.get('sku'),mode=s.get('mode'),write_attempted=s.get('write_attempted'),
  selection=s.get('selection'),accepted_revision=a.get('revision'),trigger=a.get('trigger'),
  retained_items=c.execute('select count(*) from catalogue_operation_item where operation_id=?',(op['id'],)).fetchone()[0],
  discord=[{'event':k,'state':v.get('state')} for k,v in (s.get('shopify_discord_events') or {}).items()])
 if pid:
  j=c.execute('select * from shopify_publish_job where product_id=?',(pid,)).fetchone()
  if j:
   out['job']={k:j[k] for k in ('product_id','state','trigger','requested_revision','verified_revision','running_revision','generation','owner','attempts','operation_id','scan_operation_id','updated_at','view_revision','view_pending')}
   v=obj(j['view_json']); state=v.get('state') or {}
   out['saved_view']={k:state.get(k) for k in ('state','label','compared_at','verified_at','evidence_stale')}
   out['comparisons']=[]
   for r in c.execute('select compared_at,summary_json from shopify_product_comparison where source_identity=?',(j['source_identity'],)):
    d=obj(r['summary_json']); out['comparisons'].append({'compared_at':r['compared_at'],**{k:d.get(k) for k in (
     'local','classification','read_complete','scope','invalidated','full_compared_at','equality_local','equality_at','actual_status','config','identity')}})
   if j['scan_operation_id']:
    scan=c.execute('select id,status,started_at,finished_at from catalogue_operation where id=?',(j['scan_operation_id'],)).fetchone()
    out['scan']=dict(scan) if scan else None
  out['identities']=[dict(r) for r in c.execute('select id,state,verification_state,operation_id,shopify_product_id,verified_at from shopify_product_identity where product_id=?',(pid,))]
  out['runs']=[]
  for r in c.execute("select id,state,data_json from scheduled_task_run where task_key='shopify_publisher' order by started_at desc limit 10"):
   data=obj(r['data_json']); items=[{k:i.get(k) for k in ('id','state','operation_id','selection')} for i in data.get('items',[]) if str(i.get('id'))==str(pid)]
   if items: out['runs'].append({'id':r['id'],'state':r['state'],'items':items})
 result.append(out)
print(json.dumps({'database_writes':0,'shopify_requests':0,'operations':result},indent=2))
c.close()
PY
```

## Safe acceptance and rollback

1. Keep automatic publishing paused. Back up the complete `/app/instance` mount
   and catalogue; retain the current immutable image tag. No volume relocation.
2. Capture the probe above BEFORE resolving the original condition. Inspect the
   source/shared JSON and scan receipt; do not infer file history from hashes alone.
3. After explicitly deploying the candidate develop image, use the existing
   containing-collection update scan for affected old revisions. Do not clear
   `.scanned`, SKU indexes, identity mappings or recovery receipts.
4. Run the existing read-only reconciliation. Verify the intended product is
   matching, its queue revision is settled and the source-guard failure remains
   available historically. No manual publish is needed to settle matching work.
5. On isolated staging only: verify a pending already-matching product settles
   without source inspection or any mutation/Discord publication event. For an
   eligible Update Available/Create Available target, a subsequent unscanned
   JSON/image edit remains blocked, shows Scan Required and records no remote write. A targeted
   scan makes the real change eligible. Check Simple/Variable, Draft/Live, release
   timing, 2/4 workers and restart. Do not enable live auto mode without approval.
6. To roll back, explicitly select the previous immutable image and keep the same
   volume mapping. No migration is introduced; retain all evidence and backups.
