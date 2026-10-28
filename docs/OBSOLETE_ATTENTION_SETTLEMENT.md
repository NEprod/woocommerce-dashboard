# Obsolete Attention settlement — no publishing authority

Confirmed Unraid baseline: `0a8b56f8307b`. The read-only investigation found
3DCO-0001's current canonical source matched complete identity-bound Shopify
evidence (14 variants, 53 images). Its old numeric-format queue hash did not.
HERO-0001 similarly matched an older complete comparison but retained a bootstrap
`unscanned` marker. Old completed scans predated publisher checkpoints; their
existence alone does not prove a current publisher revision.

## Exact settlement contract

The existing saved-view worker and subsequent complete comparisons may consume
an **unowned Attention** entry only when:

* It is a bootstrap `unscanned` entry with no attempted operation, or the exact
  source-freshness prerequisite with a retained failed automatic-preflight receipt
  explicitly recording no write attempted, and no recovery requirement.
* No catalogue operation is currently running/pending, and exactly one retained
  product/source identity is fully verified, with no uncertain/pending stages.
* The current configured-store comparison is full, complete, non-invalidated,
  identity/remote-ID/configuration/mapping-bound, has no changes, blockers,
  package changes, failed reads or conflicts, and retains explicit equality.
* The **current** canonical revision, including current gallery references/stats,
  matches that comparison. The old requested hash is never declared equivalent.
* An atomic conditional update still sees the same unowned Attention generation,
  requested revision, source, error, operation, view, identity and comparison.

The existing queue `verified` terminal state is displayed as **No Changes** for
this case. A small evidence index in existing `view_json.queue_no_changes`
records the old requested revision and current comparison binding. It is a
derived no-op receipt, **not a scan checkpoint or mutation approval**.

Requested/verified revisions, scan-operation ID, original error, failed receipt,
Shopify IDs and all remote evidence/timestamps are preserved. No Shopify request,
scan, publication event or Discord success is generated. Old evidence remains
“Last check matched,” not newly fresh. A superseded explicit no-write prerequisite
is excluded from current execution incidents; the failed operation remains in
History. Failed/uncertain writes are never resolved by this mechanism.

The initial pass considers at most 25 matching Attention projections per existing
saved-view worker slice. It does not reconsider genuine Create/Update projections
or add image walks to GETs or idle publishing. New complete comparisons use the
same checks; a later actual difference reopens Attention rather than borrowing
the historical no-op as scan authority. Existing worker leases/fences and source
freshness at the mutation boundary remain in place. No migration or JSON change.

## Cases still requiring merchant action

* 16bit-0001/0002 and ADOC-0001: genuine updates plus missing publisher checkpoint;
  run their existing targeted collection update **only when approved**, then review.
* LCBC-0002: Live Create Available; targeted Lego collection scan required before
  any authorised creation. No automatic scan or creation is introduced.
* Draft-only products remain local. Linked Draft metadata stays paused.
* 16bit-0003's Draft/channel-publication conflict requires metadata review.
* Recovery Required, ambiguous mutation evidence, multiple identities, incomplete
  readback, failed reads and actual unscanned changes cannot settle.

## Safe acceptance / rollback

1. Keep automatic publishing paused. The read-only check on 28 October 2026 found
   it already **Paused**; the agent did not change that setting. Confirm again
   before deployment/acceptance. Back up `/app/instance` and catalogue first.
2. Explicitly deploy the immutable candidate only after merchant approval. Keep
   all existing mounts. No schema migration is introduced by this correction.
3. Allow the existing saved-view worker to finish; do not run a scan or publishing
   job just to clear matching Attention. Inspect 3DCO/HERO queue **No Changes**,
   Products' matching/last-check state and retained Identity/History evidence.
4. Confirm original source-failure receipt/error still exists, no new publishing
   success/Discord message appeared, and no scan checkpoint was invented. Healthy
   54/54 remains local catalogue availability, not Shopify synchronisation health.
5. Confirm genuine updates/create prerequisites and all protected recovery cases
   remain actionable. Do not run their scans until separately approved.
6. In isolated staging, test a later source edit, incomplete readback, identity
   conflict and owned/recovery job: none may be consumed by this path. Keep live
   automatic publishing paused until explicit acceptance/authorisation.
7. Roll back by selecting `publisher-eligibility-0a8b56f8307b`, keeping the same
   `/app/instance` mount and paused authority. Preserve backups and all receipts.
