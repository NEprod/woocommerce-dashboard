# Catalogue Operation Control

Phase 1 permits one catalogue-mutating operation at a time within the documented
single Gunicorn worker. Append scans, product updates, shared collection updates,
full scans, and reconstruction all use the same non-blocking process-local lock.
A second request is rejected with HTTP `409` and a safe summary of the active
operation; it is not queued.

## Persistent history

Migration `0002_operations` adds `catalogue_operation` and
`catalogue_operation_item`. The operation row records type, safe scope, UTC start
and end times, status, product counts, a concise sanitized error, and marker and
recovery states. Scope values and errors are bounded, and keys that indicate
secrets, passwords, tokens, API keys, or webhooks are redacted. Full metadata
payloads and credentials must never be stored.

Ordinary ingestion now writes one item per emitted parent. A successful item and
its complete parent projection share a transaction and use
`database_state=committed`. If any parent stage fails, that transaction is rolled
back and a separate item records `status=failed`,
`database_state=rolled_back`, the affected SKU and portable source path, and a
bounded sanitized error. Missing variation-parent rows are failed items rather
than silently skipped. Marker state remains `not_started`; Milestone 5 does not
stage or alter scanner markers.

Final states are `succeeded`, `partial`, `failed`, or `interrupted`. Scan history
is finalized from a `finally` path, so scanner or ingestion exceptions release the
process lock. Notification failures retain the existing best-effort behavior and
do not turn an otherwise successful scan into a failure. If final history writing
itself fails, the in-process lock is still released and the unfinished row is
recoverable at the next startup.

## Startup recovery

After migrations complete, startup changes any remaining `running` operation to
`interrupted`, records an end time, and sets `recovery_state` to
`review_required`. This is diagnostic state: it does not claim that SQLite and
catalogue files were rolled back together. Marker-specific recovery is introduced
in a later approved milestone.

## Deployment boundary

The lock and live active-operation summary are process-local. Persistent rows are
history, not a distributed mutex or queue. The supported Phase 1 deployment is
therefore one Gunicorn worker in one application replica. A multi-worker or
multi-replica deployment requires both a shared operation coordinator and a
separate, single-owner migration execution step before replicas start.
