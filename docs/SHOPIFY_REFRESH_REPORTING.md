# Shopify refresh completion and actionable notifications

## Demonstrated defect and correction

`shopify_publication.annotate` completed its channel/product readback and then
rejected Draft/Archived intent combined with a positive channel selection. It
caught that authored conflict as a generic Shopify error. Comparison correctly
withheld product freshness, but `shopify_status.refresh` also counted its
incomplete product evidence as an execution error. The scheduler therefore
completed its queue yet reported Partial.

Only that explicitly typed authored conflict is now a readiness finding. Mixed
or genuinely missing API evidence still fails execution. No string matching
decides execution success. Product comparison remains incomplete, with no new
full/equality checkpoint, until configuration is corrected and revalidated.
The 20-image publication guard and all signed mutation/recovery rules remain.

Normal create/update/current states are informational inspection results. The
15-target synthetic regression processes three durable slices and reproduces
15 checked, 2 current, 3 updates, 9 attention, 1 unavailable, no pending/deferred,
and a simulated 268-request counter. It is not a live Shopify test.

## Notifications

Terminal refresh receipts retain small condition fingerprints under
`action_observations`; they are not an independent product/issue database.
New, materially changed or reopened actionable conditions enqueue one summary
using the existing Discord event delivery claim. An unchanged condition is
quiet. A conclusive comparison supplies a resolution observation; failed or
skipped inspections do not. Real inspection failures alert independently of
product readiness. Automatic routine refresh/scanner success, accepted package
assignments and ordinary successful publishing are quiet, with receipt evidence
retained. Accepted asynchronous media processing is not itself a merchant alert.
Failed, blocked and uncertain publishing still retains its actionable event.

Claims are committed before sending, use compare-and-swap across workers, and
do not automatically retry uncertain delivery. The latest observation for each
condition is protected from ordinary log retention. Resolution/recovery of
mutation receipts still requires the original complete-evidence protocol.

Optional `DASHBOARD_BASE_URL` supplies the merchant's existing private dashboard
URL for clickable Discord correction links; it does not expose a public endpoint.
Without it, the exact local product/receipt path is included, not a guessed URL.
URLs containing embedded credentials/query/fragment are rejected. Dashboard
counter and correction links themselves are unchanged.

## Production evidence: supplied versus independently checked

Merchant-supplied probe: 839 retained operations, 373 stored incomplete records,
323 stored Partial status refreshes, 14 conservatively folded ledger incidents,
8 derived-completed historical Partial records, and 8 protected recovery cases.
These aggregates do **not** prove all 323 had the same cause or all 14 are
currently actionable. No production breakdown is fabricated and no historical
status/receipt is rewritten. The eight recoveries must not be cleared.

The expanded `app/operations_probe.py` can be streamed into the current container
without replacing its application files:

```sh
docker exec -i YOUR_TLCDASHBOARD_CONTAINER python - < /path/to/operations_probe.py
```

It uses SQLite read-only/query-only connections, no application factory,
migrations, workers, notifications or Shopify requests. It prints allowlisted
reason codes, evidence groups and retained incident IDs/types, plus the actual
dashboard query's counts, counting units, local availability and Recent Changes.
Arbitrary error bodies, credentials, customer values and source paths are not
exported. Dashboard projection failure is explicit, never an invented zero.
The reason groups can overlap; continuation slices are separate from proven
complete execution. `recorded_execution_error` is historical evidence, not proof
that its condition remains present today. Unassessed receipts need review.

An expanded production probe was supplied on 2026-10-20: 842 ledger rows, 374 raw
incomplete records and 324 Partial refreshes. Of these refreshes, 294 contain
recorded errors and 30 lack sufficient retained evidence. Overlapping reason
groups: 251 image-related, 59 incomplete managed evidence and 28 publication-intent
conflicts. These are not 294 proven API/database failures. Older prerequisite
errors were not typed separately; the generic evidence rule also incorrectly
elevated authored configuration to execution failure. The probe cannot retrospectively
prove every read completed, so original statuses remain unchanged.

Fourteen folded ledger incidents comprise 12 with insufficient retained evidence
(one append, two image renames, two metadata saves, two Intake handoffs, one old
Woo connection test, one Woo preview, one Shopify taxonomy batch and two Shopify
publishes) and two with recorded Shopify refresh errors. The fifteenth dashboard
failure is the latest Partial scheduled product run. Unassessed Partials without
explicit failed stages now have a separate clickable **Historical receipts needing
evidence review** category, not a proven current-failure badge or invented success.
Explicit failed stages/pending work still count as unresolved execution incidents.
All original records and eight recoveries (one Woo publish, five Shopify publishes,
two Shopify refreshes) remain intact. Local health is independently confirmed as
Healthy, 54/54/100%; current metadata count is 2, images 0, Shopify findings 5.

After deployment, refresh the affected scope
and rerun it: matching products should gain valid freshness, the Draft/channel
conflict must remain actionable/unverified, and the overbound gallery remains a
publishing prerequisite. No changes to authored configuration or live Shopify
are performed by this repair. 54/54 available local items must show Healthy,
independently of Shopify findings or operation incidents. Actual before/after
production post-deployment verification remains acceptance work; no live state
was changed during development. The two recorded read incidents still need
same-scope successful evidence or receipt review. Old discovery without retained identity still needs
merchant review; no title-only linking or new reconciliation engine is added.

No migration or data reset is required. Existing JSON, SKU/variant identity,
Orders, package IDs/acknowledgements and history remain authoritative. Unraid is
not automatically deployed. The planned immutable release tag is
`shopify-refresh-reporting-<commit>`; publication must not be claimed until remote
digest, architecture and full embedded revision have been verified.

## Validation and deployment acceptance

206 focused/bounded reporting, publishing, scheduler and notification tests plus
142 Orders/webhook/package/customisation/operation-control regressions passed
(348 unique Python tests). 34 focused JavaScript tests passed. Desktop and 400px
mobile browser checks confirmed the 12-case historical-review counter and list
agree, with working receipt links and separated healthy local availability.
Offline Docker checks used two non-root workers and network isolation, upgraded
the old database to existing head0018, and preserved Orders with two independent
personalised lines, six stable package IDs/manual links, authored shipping JSON,
the recovery receipt and alert observations across restart. No new migration.

Back up existing Unraid appdata normally, select the final immutable
`shopify-refresh-reporting-<commit>` image, and preserve all existing mounts and
environment. Do not start a second container against the same database. After
updating, run one manual product Status Refresh and `python -m app.operations_probe`
in its Console. Compare stored/derived statuses and counter/list totals. A
completed inspection with authored findings should succeed while those products
remain actionable; read failures must remain incomplete. Check one corrected
metadata/image condition resolves after validation. Do not retry or clear any of
the eight protected recoveries merely to test. Existing legacy errors still need
their exact scope checked; no unconditional zero-failure count is promised.
