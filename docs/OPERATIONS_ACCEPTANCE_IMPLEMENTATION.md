# Operations acceptance refinement — implementation and evidence

Starting develop: `4d2348e6256ec2d325cd1c0ec17103246c3764bb`.
No live Shopify writes, theme/extension changes, main promotion or Unraid deployment.
The three unrelated Unraid/template files are preserved and excluded.

## Requirement-to-implementation gap matrix

| Requirement | Starting gap | Focused correction |
|---|---|---|
| Compact task overview | One large configuration card per selected task | Compact desktop overview; expandable mobile rows; separate details/settings and paginated history |
| Distinct release task | Releases processed under sale task | Separate five-minute persisted release task; same due engine, source-save path and cross-task lease |
| Queue | Long cards combined with settings/history | Dedicated saved-data queue, ten rows/page, search/state/trigger filters, explicit reasons/actions |
| Event History | Event receipts existed but embedded in settings | Dedicated sidebar/nav history and details; includes older original operations without inventing changes |
| Reconciliation | Repeated definitions/terms/schema and local registry parses | Read-only slice-scoped snapshots; per-phase request/cost/latency/retry/pagination counters |
| Large catalogue coordination | 60×8 full checks and 15-second healthy pauses | 150 bounded slices for existing 1,000-product selection, one-second healthy continuation; unchanged 20-minute/throttle/priority limits |
| Media | TLC guard 20; read queries/validators assumed 20/21 | Up to 250 images subject to existing bytes/evidence bounds; acknowledged 20-image upload slices and complete cursor-based media readback |
| Bootstrap | Old marker/date lacked new queue source fingerprint | No invented evidence or automatic scan; precise collection guidance and confirmed existing scoped update action |
| Schedules | Retained merchant intervals confused with defaults | New initialisation daily; preserve existing selected intervals, including 60; default-off authority unchanged |
| Acceptance | Original staging checklist did not cover new operations surfaces | Separate 25-test practical checklist plus existing thirty-scenario plan |

## What changed

`automatic_publishing_routes`, `operations_views` and templates separate settings,
queue, event history and event details. Reads use persisted jobs, product identities,
operation scopes and immutable event receipts. Queue/history GETs do not load source
JSON, inspect image folders or call Shopify. Original operation links remain.
Ten-row server-side pagination includes filtered totals. Historical receipts without
previous field values explicitly say details are unavailable. Reviewed intent from
an incomplete/failed operation is not presented as verified applied changes.
Discord delivery state comes from the existing durable delivery receipt.

Task overview uses existing branding/components/status classes. Dates are explicitly
Europe/London GMT/BST, with UTC under technical detail. Publishing settings retain
administrator/CSRF, exact domain confirmation and durable authority. Run Now alone
does not grant automatic publishing authority. Manual signed approval is unchanged.

`publishing_due.process_due` accepts an event-type/id scope. The new release task
uses exactly the existing local-save/backup/confined collection-scan path. Sales
retain their current calculation and signed authorisation engine. Both share the
same owner/fence and catalogue-operation exclusions with publisher/reconciliation.
No periodic local folder scan or configured-restart full scan is added.

## Reconciliation diagnosis and measured comparison

The actual planner repeatedly reads shared schema, field/metaobject definitions,
terms, dimension definitions and country/publication reference data. For example,
five informational attributes generate repeated definition/field/term lookups for
each product, even when the exact query/variables are identical within one slice.
Profiling the 100-product fixture also found **11,780 local registry loads**, taking
59 seconds of 80 profiled reconciliation seconds (profiling adds overhead).

`shopify_read_scope` caches only allowlisted reference reads during one bounded,
read-only inspection slice. Full product values, media and mutations are not cached.
Responses are copied for callers; failed transport responses are not reused.
Every new slice/request discards its cache. Publishing/readback executors do not
use this cache. A ContextVar-backed extension of the existing registry snapshot
mechanism similarly shares one validated local registry per read-only slice.
Subsequent slices see registry edits; no persistent stale cache was introduced.

Before and after use the same fictional catalogue, five informational attributes,
two images/product, complete fixed GraphQL responses and eight-product slices.
Both execute the real reconciliation/planner/comparison paths; fixture transport
rejects mutations. Classifications match at every size. This is **mock GraphQL
request accounting and CPU/SQLite timing**, not actual Shopify HTTP latency.

| Products | Simulated requests before → after | Reference hits | Runtime seconds before → after | SQL statements before → after |
|---:|---:|---:|---:|---:|
| 15 | 302 → 68 | 234 | 1.619 → 0.673 | 2,152 → 1,554 |
| 100 | 2,013 → 447 | 1,566 | 13.019 → 5.953 | 16,538 → 11,451 |
| 500 | 10,063 → 2,197 | 7,866 | 126.717 → 44.823 | 132,764 → 82,327 |
| 1,000 | 20,125 → 4,375 | 15,750 | 416.086 → 141.934 | 389,750 → 226,875 |

Peak process RSS before → after: 112.6→109.8, 119.1→117.6, 137.5→136.2,
149.2→152.5 MiB respectively, including fixture preparation, not per-request memory.
Benchmarks were run separately under local host load; no statistical network-latency
or throughput guarantee is inferred. Existing cross-worker ownership tests and
network-disabled 2/3/4-worker restart tests cover concurrency; no live multi-worker
Shopify throughput experiment was performed.

The before run is retained in its original JUnit evidence. The final after run
reuses that measured baseline rather than repeating a twenty-minute baseline run.
The default focused suite tests 15; larger benchmarks are opt-in:

```sh
TLC_PROFILE_SCALES=1 python3 -m pytest tests/test_reconciliation_request_profile.py --junitxml=/tmp/reconciliation-profile.xml
```

**Production 120/274 requests and 166.5 seconds have not been independently
reproduced.** The fixture demonstrates their repeated-reference pattern, not an
assertion that those exact live executions had identical attributes/read phases.
Actual requested/actual GraphQL costs and network latencies remain unmeasured here:
mock responses deliberately provide no invented cost. `cost_observations=0` means
unknown, not zero Shopify cost.

New real run profiles count HTTP GraphQL attempts separately from response-reported
requested/actual cost, shared reference hits, identical repeat requests, retries,
pagination and throttles. Product/variant/media/inventory fields grouped into one
query are not counted as separate HTTP requests. OAuth requests remain separate.
Profiles contain no variables, credentials, source content or customer information.

The 15-product phase breakdown is identity/connection 2→2, shared definitions
195→26, grouped product/verification 15→15, and taxonomy/metafields 90→25.
All 234 avoided requests were identical reference lookups; retries, pagination
and throttles were zero in this particular fixture. Individual media, variant,
inventory and customer configuration fields included in grouped queries do not
constitute additional HTTP requests. Larger galleries use additional bounded
media pages; those are measured separately by the adapter pagination tests.
Failed reads still fail/defer correctly and never renew incomplete freshness.

Remaining costs are product-specific values, ownership/term assignments, required
remote identity/readback, media pages and supported inventory checks. Not all reads
can be replaced by `updatedAt` equality. SQLite/source validation still costs work
at 1,000 products; this milestone does not replace those correctness checks or
claim constant-time reconciliation. The existing time bound can still defer work
truthfully when the store is slow/throttled; complete checks are not replayed.

## Media/variant capability and safety

The old count guard was TLC implementation, mirrored in first-21-media queries and
validators, not a universal Shopify 20-image limit. Shopify documents 250 combined
media/product and input-array bounds of 250. Current product-model APIs permit up
to 2,048 variants; TLC's existing 32-variant review/readback contract is retained.

Sources checked for API 2026-10 on 27 October 2026:

* https://help.shopify.com/en/manual/products/product-media/add-media
* https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/productCreate
* https://shopify.dev/docs/api/usage/limits
* https://shopify.dev/docs/api/admin-graphql/2026-10/objects/Product

New galleries stage/upload in at most 20-image slices. Acknowledged original-source
hash/resource receipts persist before yielding. Only completed uploads are reused;
signed upload parameters are not persisted. Source/configuration are revalidated
before continuation and Create. Duplicate/incomplete staged resource IDs block.
Three no-progress slices stop with actionable evidence. Ambiguous binary/Create
outcomes are not automatically replayed. All originals must be acknowledged before
one ProductCreate; async processing then uses the existing read-only finaliser.
Upload attempts do not consume the separate twelve-poll readback budget.

Normal small-product reads still request only 20 media edges, not 250 everywhere.
Larger media readback follows up to thirteen bounded pages and rejects repeated IDs,
stalled cursors, missing revision timestamps or changed product revision/correlation.
Partial evidence cannot become Up to Date. No truncation or silent deletion occurs.

Mock tests cover 1, 20, 21, 53 and 250 images, 251 rejected, acknowledged continuation,
restart, async processing, uncertain uploads, stable IDs/order and both variable
layouts with 53 images. No real `3DCO-0001` product was changed or inspected.

**Existing-product media add/remove/replacement remains unsupported.** Its review
blocks instead of re-uploading/deleting a gallery. Alt/order changes retain media
IDs and unchanged images are not uploaded. Existing 10 MiB/file, 30 MiB total,
response/evidence/time budgets and native/package capability safeguards remain.
Product details/editor disclose application versus Shopify capabilities explicitly.

## Scan evidence, persistence and rollback

Migration 0019 introduced a publisher source revision that old scan dates/markers
did not contain. Neither old ingestion success nor mtime alone proves exactly
which current JSON, images and variants were scanned. This is why pre-upgrade
jobs bootstrap as `unscanned`; no safe equivalent proof was found to manufacture.
The reported approximately fifteen live rows were not individually inspected here.

Guidance resolves the actual shared JSON and containing collection inside the
configured root, validates the product belongs there, rejects missing/unsafe paths
and requires explicit confirmation. It invokes existing `shared_collection` scan
dispatch. Active catalogue ownership returns 409; no duplicate/full-scan fallback.
It does not rewrite JSON or run anything merely on opening the queue/guidance.

Forward migration **0020_operations_acceptance** adds the independent five-minute
release task and two history indexes. Published 0019 is untouched. Existing
intervals/authority are preserved; new reconciliation initialisation is daily.
An installation with a retained explicit authority must be paused before any
live upgrade/inspection. Appdata stays at `/app/instance`.

Back up the complete real appdata, matching catalogue/taxonomy/output and prior
immutable image before upgrade. Keep `automatic-publishing-4d2348e6256e` available.
Downgrade is deliberately refused. Rollback requires the paired pre-0020 appdata
and source backup plus old image, first tested on separate mounts. Image-only
rollback is not supported; a local restore cannot undo remote Shopify changes.

## Read-only production evidence collection (no deployment or Shopify calls)

Run inside the existing container only when the merchant is ready. The default
database path is used below; stop if the installation uses a different configured
SQLite location. The probe refuses to create a database and never imports the app.

```sh
python - <<'PY'
import json,sqlite3
from pathlib import Path
p=Path('/app/instance/site.db')
if not p.is_file():raise SystemExit('Configured database not found; do not guess or create it.')
c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)
c.execute('pragma query_only=on')
tasks=[dict(zip(('task','enabled','minutes','state'),r)) for r in c.execute('select key,enabled,interval_minutes,state from scheduled_task order by key')]
runs=[]
for id,key,state,raw in c.execute("select id,task_key,state,data_json from scheduled_task_run where task_key='shopify_products' order by created_at desc limit 3"):
 d=json.loads(raw);runs.append({'id':id,'task':key,'state':state,'counts':d.get('counts'),'request_profile':d.get('request_profile','Not recorded by this version/run')})
queue=[dict(zip(('sku','collection','state','reason'),r)) for r in c.execute("select p.sku,c.source_relpath,j.state,j.error from shopify_publish_job j join product p on p.id=j.product_id left join collection c on c.id=p.collection_id where j.requested_revision='unscanned' order by p.id limit 25")]
print(json.dumps({'database_writes':0,'shopify_requests':0,'tasks':tasks,'recent_reconciliation':runs,'unscanned_queue':queue},indent=2))
PY
```

## Acceptance gate

See `OPERATIONS_ACCEPTANCE_CHECKLIST.md` (25 practical cases) and the existing
`AUTOMATIC_PUBLISHING_ACCEPTANCE.md` (30 foundational cases). Real costs, network
latency, large-image processing, staging Discord and store behaviour remain
merchant staging acceptance, not assertions made from mocks. Default-off authority,
signed approvals, source/identity/freshness checks and uncertain recovery remain.

## Final offline validation

509 distinct Python cases passed across the bounded runs (overlapping reruns
deduplicated), including all four scaling fixtures. The last focused run was
41 passed with the three expensive scaling sizes skipped there only; those three
had already passed in the explicit scaling run. The adapter regression rerun was
126 passed, with two separately investigated known failures excluded. JavaScript:
310 passed. Syntax compilation and `git diff --check` passed.

Two residual assertions are not represented as passes:

* `test_missing_media_receipt_recovery_reviews_fresh_local_digest_not_old_formatter`
  expects `verified` but gets protected `uncertain`; reproduced unchanged on the
  archived starting revision 4d2348e. This milestone does not weaken recovery.
* `test_docker_examples_expose_optional_shopify_configuration` fails against the
  merchant's unrelated modified Unraid template (optional client fields absent).
  It passes against the committed starting template. That unrelated file is not
  changed or included in the milestone commit.

This is bounded evidence, not a claim that every historical full-suite assertion
passes. The initial new gallery failure tests had an incorrectly shaped mock
response; the fixture was corrected to return Shopify's `product` envelope, and
all five failed/stalled/changed-page safety cases now pass.

Headless isolated Chromium at 1440×1000 and 390×844 passed navigation, filtered
totals, pagination, event detail, GMT/BST context, default-off authority, no browser
errors, no horizontal overflow and no Save/button bottom-navigation overlap.
Before/after screenshots use fictional data only. The mobile task overview height
fell from 1,715 px (one old task card) to 1,013 px (six compact task summaries).
The former combined publishing page was 7,127 desktop / 9,027 mobile px; separate
settings are 1,000 / 1,132 px. Queue/history remain ten records/page, not an
unbounded catalogue. Five local fixture requests/page had median response times:
tasks 4.83 ms, queue 5.85 ms, events 3.79 ms, settings 2.79 ms; these are not Unraid
or real network performance guarantees.

Final-source network-disabled Docker upgrade/restart passed with two, three and
four workers. Checks covered 0019→0020 with a migration backup, retained selected
60-minute reconciliation, new five-minute release task, both indexes, persisted
jobs/due rows/events/logs, unchanged disabled authority/startup grace, six stable
package IDs/GIDs, original operation receipts, dimensions/customisations, one
order with two distinct same-SKU personalisations and private page responses.
No real catalogue, appdata, Shopify product, theme or extension was touched.

Before publishing, the committed image is additionally checked with its exact
revision label. The delivery report records its immutable tag and verified remote
digest. Staging checklist boxes intentionally remain unchecked: offline mocks and
restart tests do not authorize live automation or establish actual API costs.
