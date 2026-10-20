# Operations and current catalogue health

## Reporting inventory and ownership

Execution, record readiness and finding severity are independent. A successful
inspection may discover an action-required product; it has not failed to inspect.
Partial means required execution remains incomplete, not merely that findings exist.

| Producer / persistence | Successful execution / incomplete execution | Findings, recovery and consumers |
| --- | --- | --- |
| Scanner, ingest, marker recovery → CatalogueOperation/items + bounded live state | Required projections and markers committed / invalid source, SKU conflict, I/O, database or marker failure | Optional image fallback and unchanged folders are information. Missing publishing artwork is product readiness. Marker/database uncertainty remains recovery. Scanner, History, dashboard and deduplicated scanner Discord consume the receipt. |
| Catalogue Intake grouping/import/metadata/handoff → same operation ledger | Requested local transformations saved / required transformation or validation failed | Existing bounded warning findings retain source context. Publishing prerequisites are not claims of publication. Intake result pages, History and per-action Discord consume results. |
| Product metadata/editor/scanner → authored JSON and projection | Validation/save/projection completed / invalid authored data or persistence failure | Current missing fields/artwork derive from the active projection. They disappear after validated correction, not after an unrelated successful operation. JSON remains authoritative. |
| Shopify connection/discovery → explicit read response | Requested page and pagination evidence validated / denied, malformed or incomplete page | Unlinked products are expected on first connection. Discovery does not store inferred mappings. Existing signed SKU/identity review remains required; titles never authorise linking. No whole-store completeness claim from one page. |
| Shopify status/comparison → ShopifyProductComparison + read-only operation | Requested bounded inspection completed / required reads, comparison or persistence failed | Missing local publishing prerequisites are typed readiness findings. Pending bounded work is continuation, not successful full verification. Full managed evidence still gates product freshness. Dashboard/Products/task history consume cached evidence; read-only refreshes do not send Discord alerts. |
| Scheduled product reconciliation → ScheduledTaskRun + correlated slice receipts | Queue processed or intentionally protected targets accounted for / required query failed or budget exhausted with work outstanding | Run is the primary execution; slice receipts remain evidence. Existing CAS lease/fences prevent two-worker duplicate execution. Attention/deferred counts explain readiness separately. |
| Review & Sync, create/update, metafields, native dimensions, galleries → signed plan, identity and operation receipts | Requested supported fields accepted and required readable fields verified / blocked target, API rejection or incomplete required stage | Preflight is not publication. Accepted saved-package assignment has no independent readback; accepted is successful, uncertain is recovery. Existing notification event keys/CAS deduplicate remote notifications. No automatic write retry. |
| Orders startup/manual import → Orders checkpoint + ScheduledTaskRun | Complete durable import/checkpoint / incomplete pages, access, network or persistence failure | Optional missing snapshots remain warnings without merging line identities. Webhook inbox is encrypted, deduplicated and never routinely fetches Orders. Incomplete events await authorised reconciliation. Orders overview/task history retain separate delivery/access state. |
| Scheduled sales → sale transition evidence + ScheduledTaskRun | Approved transition verified / failed or uncertain transition | Sale uncertainty is recovery; product status refresh cannot resolve it. Existing sales behaviour unchanged. |
| Taxonomy, publication, customisations, navigation, inventory → scoped signed receipts | Requested owned stage verified / rejected, incomplete or uncertain stage | Their existing recovery protocols remain authoritative. Generic later reads do not erase write failures. |
| Dashboard / Operations / Discord | Read-only presentation of the above | Current issues and historical executions have separate counts and labels. Discord delivery failure is not remote product execution failure. No history is deleted by this repair. |

## Demonstrated defects before this repair

* Dashboard `failed_operations` counted **all retained** raw partial, failed and
  interrupted rows. Thus a displayed 360 was a historical row total, not proof of
  360 active incidents. Production breakdown must be collected read-only; the
  development database is not the merchant database.
* Recovery counted every non-`none` recovery receipt. Eight cannot be declared
  safe or resolved from that number alone; inspect their original receipts.
* Catalogue Health combined local missing items with metadata gaps, all historical
  failed rows and recovery. Therefore 54/54 local availability could coexist with
  the unexplained Needs attention label.
* Scanner warnings alone changed execution to partial; Operations manufactured a
  warning from a partial status even when no warning evidence existed.
* Status refresh used publication preflight: missing artwork raised the same
  exception as failed Shopify reads. Pending bounded slices became partial rows;
  scheduled run completion also treated product attention as execution failure.

## Counting and resolution contract

Current issue lists and dashboard counters use one read-only projection. No second
issue database or manually incremented counters are introduced. Stable keys identify
conditions, not each observation. Metadata counts are product/field-group findings;
one product may contribute more than one. Missing images are a separate condition.
Optional SEO is information, not a required metadata error.

All nonzero Needs Attention rows link to `/operations/issues?category=…`:

| Category | Authoritative source | Counting unit / correction |
| --- | --- | --- |
| metadata | Active Product short/long descriptions both blank | One product/field-group; open product metadata |
| images | Existing safe original parent/variation image resolver | One product; inspect original artwork/source mount, then rescan |
| missing | Product/Variation catalogue_status=missing | One item; variation SKU and parent link retained |
| shopify | Existing locally computed Shopify product states/comparisons | One product requiring review/fresh evidence; open signed Review & Sync |
| failures | Exact operation type + scope + failure condition, and latest incomplete task per key | One unresolved incident; open original receipt/task detail |
| recovery | Retained non-none recovery receipts, plus product recovery without such a receipt | One recovery case; no automatic retry or clearing |
| information (separate, not Needs Attention) | Missing optional SEO | One optional product finding |

The same projection supplies count and paginated list (25 findings/page). Search
shows both the category total and matching total. Opening any link is read-only.
New scan/status receipts retain explicit local condition observations for committed
or actually checked products, bounded to1000 products/receipt. Booleans distinguish
present from resolved: omission never resolves a condition. These existing-receipt
observations support New/Existing/Reopened labels. Older lifecycle dates cannot be
reconstructed where no evidence was retained; current projection still resolves
corrected local conditions without inventing historical transitions.

Repeated exact-scope read failures form one active incident. A later complete read
of that same scope resolves the read incident. It cannot resolve a failed write.
Write failures without conclusive recovery evidence remain for review, including
legacy records where the target cannot be reconstructed. Recovery receipts remain
individual cases and are never silently cleared. Historical statuses remain raw in
storage; any derived interpretation is labelled and retains the original receipt.

Local availability, Shopify readiness and operations health are displayed separately.
Opening an issue or discovery page never writes Shopify or source JSON. Issue detail
links lead to existing product, review, connection or receipt workflows.

Historical Partial scans receive a corrected derived interpretation only where all
parent projections and markers completed, no errors/recovery/blocking stage remains,
and *every* retained warning is demonstrably informational (with no omitted samples).
Unknown old warnings remain unassessed/incomplete, not silently made successful.

## Discovery and deliberately deferred work

Existing discovery is explicit, paginated and read-only. Complete exact-SKU candidate
discovery now creates an action-required reconciliation finding, not an execution
failure; incomplete pagination still fails. No candidates are adopted automatically.
Existing Identity / History can recover retained mappings. The repository does **not**
yet implement complete first-time adoption with no retained identity or a durable
whole-store candidate inventory. That is a separate bounded onboarding/linking task,
not a hidden new reconciliation engine in this repair. No potential-match count is
invented. Existing write recovery protocols remain mandatory. Legacy failed writes
whose intended target state cannot be reconstructed cannot be auto-resolved by a
later unrelated success, even if this leaves a review-required historical incident.

## Merchant-only evidence and safe acceptance

The live Unraid database/logs are unavailable to this development environment.
The reported360/eight must not be presented as an inspected production breakdown.
After updating the development image, run `python -m app.operations_probe` inside
the existing container. It opens the database in SQLite read-only mode, does not
initialise/migrate the app and prints only counts, types and recovery IDs/states.
It performs zero Shopify requests and zero writes. Its ledger-only incident total
is deliberately labelled; dashboard also includes current product/task evidence.

1. Back up existing persistent appdata using the normal procedure; keep mounts and
   environment unchanged. Update to the published immutable operations-health tag.
2. Confirm migration head remains0018. Do not delete DB/history/markers or reset any
   freshness or recovery state. No special data conversion/recovery is required.
3. Compare the read-only probe's historical breakdown with current dashboard lists.
   Open every recovery case before deciding any action; nothing is automatically cleared.
4. A matching product refresh should complete and retain its normal freshness proof.
   An update-needed product should show a successful inspection plus product changes.
   Missing artwork is a prerequisite finding, with no false full freshness checkpoint.
5. Tap each nonzero counter; total, scope and list must agree. Correct one local
   metadata/image condition, run its existing validation/targeted scan and confirm it
   disappears. Its receipts remain in History; recurrence reopens the stable condition.
6. Inspect Scheduled Tasks: one primary run may contain multiple internal slices.
   History can include these explicitly; no duplicate worker execution is implied.
7. Check Orders (including test-order filter), separate same-SKU personalisations,
   package IDs, signed publishing and customisations. Do not publish live changes
   merely to test reporting. No Shopify scope/theme/rate/default-package change is needed.

Rollback uses the previous immutable image with the same unchanged0018 schema and
appdata; no history is removed. Original receipts remain valid. New reporting keys
are additive JSON and ignored by older application versions; older reporting defects
will of course reappear on rollback.

## Validation evidence

Bounded regression:556 Python tests passed across reporting/dashboard, scheduler and
leases, scanner operation control, Intake transformations, discovery, reconciliation,
publishing/Discord, dimension sync, sparse shipping overrides, Orders/webhooks and
customisations.19 focused JavaScript tests passed. Tests use synthetic Shopify and
local assets, never live product writes. Existing deprecation warnings are unrelated.
Five older discovery/dashboard assertions were reproduced against the untouched base
commit before correcting their stale assumptions about bounded throttling retries,
capacity-only database bookkeeping, GraphQL introspection and Orders wording.

Desktop/mobile browser fixture verifies separate healthy local availability, exact
counter-to-list totals, search, empty states, receipts and product actions. Its360
historical incomplete rows and eight recovery cases are **synthetic**, not production
evidence. Optional-image scans, source-image repair, repeated/reopened conditions,
incomplete reads and storage exceptions have focused tests. The Docker runner
`tests/docker_operations_health_smoke.py` extends the established network-isolated,
two-worker0017→0018 upgrade/restart chain with retained recovery/receipt checks and
the read-only probe, while preserving package IDs and separate order personalisations.
The release report records its actual result and remote image digest.
