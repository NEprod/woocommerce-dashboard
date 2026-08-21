# Scanner, Operations, and Discord

Phase 2 Milestone 7 provides authenticated `/scanner`, `/operations`, and
`/operations/<operation-id>` workspaces over the protected Phase 1 scanner and
operation model. These pages are a presentation layer. They do not change
scanner selection, resolved rows, SKU allocation/reuse, JSON inheritance,
marker coordination, transactional ingestion, reconciliation, or output copying.

## Supported Scanner modes

- **Append** uses the ordinary scanner selection contract. It discovers items
  without a completed `.scanned` marker, preserves existing projections, stages
  and finalises markers through the existing recovery workflow, and may copy
  required images to `/output`.
- **Update** uses the established `.update`-selected workflow. It refreshes the
  complete affected parent and variations while preserving unrelated catalogue
  records and existing parent/variation identities. `.update` is removed only
  after database and marker finalisation succeeds.
- **Full** is the existing intentional catalogue-wide workflow. It is
  exhaustive, can take longer, reconciles missing state, and retains the
  protected intentional full-scan SKU-index behaviour. It requires a second explicit
  catalogue-wide confirmation.

Reconstruction is a separate identity-preserving database recovery workflow;
it is not a Scanner mode. Shared collection refresh continues to be invoked
only by collection metadata saves. All modes continue to require the catalogue,
output, and instance mounts because image processing/output copying remain part
of the established scanner workflow.

The Scanner page performs safe preflight checks for a readable catalogue mount,
writable output mount, available instance/database, SQLite quick-check result,
migration head, active operation, and a secret-free Discord summary. Absolute
host paths and webhook values are never rendered. The same storage and lock
checks run again server-side when the confirmed POST is made.

## Confirmation, locking, retry, and cancellation

Every canonical Scanner start requires explicit confirmation. Full also
requires a separate catalogue-wide acknowledgement. Client-side duplicate
click protection is supplementary: the non-blocking process-local lock remains
authoritative and returns a controlled `409`. Refreshing a GET never starts work.

The Phase 1 runtime remains single-worker and single-replica. Safe cancellation
is not implemented; no Cancel button, process termination, destructive rollback,
or partial-marker shortcut is exposed. An active operation must reach its
existing terminal or recovery state.

`/operations` uses server-side filtering, deterministic sorting, and pages of
25, 50, or 100 records. It supports type, persisted state, warning/error
attention, recovery state, and bounded ID/scope search. Collection labels use
the folder basename; product labels use resolved titles. No command line or
unrestricted process information is shown.

Persisted state labels are Running (`running`), Completed (`succeeded`),
Completed with warnings (`partial`), Failed (`failed`), Interrupted
(`interrupted`), and Queued (`pending`, where present). Recovery-required marker
state is separate from the scanner result.

Retry links exist only for failed/partial/interrupted Append, Update, and Full.
They return to Scanner with the supported mode preselected and still require
normal confirmation. Starting a retry creates a new operation; original history
is preserved. Unsupported recovery/cancellation is stated explicitly.

Operation Detail combines persisted history with process-local progress where
still available. It shows structured start/terminal events, actual counts,
recovery/marker state, affected product/collection links, and Discord summary.
Fields not stored by the schema—such as initiating user, historical warning
lines, or durable Discord attempts—are not invented.

## Progress and logs

Running operations are observed with non-overlapping four-second polling. The
client stops at a terminal state, backs off after network failure, and never
starts work or sends notifications. Percentage is shown only when the runner
knows the collection total; otherwise stage/current item is authoritative.

The existing SSE compatibility endpoints remain unchanged. The runner keeps a
parallel read-only snapshot of the same bounded, already-redacted process log:

- maximum 2,000 lines and approximately 2 MiB;
- chronological order;
- server pages of at most 100 lines (UI default 50);
- bounded search and severity filters;
- text-only browser rendering.

This is not a new log file and is not durable. After restart, persisted summaries
and items remain while the page reports that process-local lines are no longer
retained. Redaction covers webhooks, authorization/cookie headers, bearer tokens,
passwords/secrets, and configured/personal absolute path prefixes while retaining
useful portable catalogue references.

## Discord audit and policy

Discord is optional and disabled by default. Existing embed structure, colour
meanings, footer/timestamp, channel separation, and product-ingest event remain.
The client validates Discord HTTPS webhook hosts, uses short connect/read
timeouts, and performs at most two attempts for timeout, connection, rate-limit,
or transient service failure. Rate-limit waits are capped at one second.
Response bodies, webhook fragments, credentials, stack traces, and host paths
are never reported.

| Event | Channel variable | Default |
|---|---|---|
| Scanner started | `DISCORD_WEBHOOK_SCANS_INFO` | Disabled globally / skipped if empty |
| Scanner completed cleanly | `DISCORD_WEBHOOK_SCANS_INFO` | Disabled globally / skipped if empty |
| Scanner completed with warnings | `DISCORD_WEBHOOK_SCANS_INFO` | Distinct warning embed |
| Scanner failed | `DISCORD_WEBHOOK_SCANS_ERRORS` | Redacted failure embed |
| Collection metadata updated | `DISCORD_WEBHOOK_EDITS` | Skipped if empty |
| Product override created/updated/removed | `DISCORD_WEBHOOK_OVERRIDES` and `DISCORD_WEBHOOK_EDITS` | Skipped per empty channel |
| Product ingested | `DISCORD_WEBHOOK_INGEST` | Existing optional event; empty by default |

There is no separate recovery/interruption message because startup recovery has
no durable delivery identity. Adding durable delivery history or restart retry
would require a reviewed migration. Scanner events are guarded once per
process/run to prevent completion-handler re-entry; page views, polling,
filters, searches, and refreshes never send messages.

Delivery state is shown live while the bounded run remains in memory. After
restart the page says delivery was not retained rather than claiming Sent or
Failed. Notification failure remains a warning and never changes scanner or
database success.

Future operational features must consider Discord when work is asynchronous,
long-running, warning/failure-prone, or requires user action. Low-value
navigation, searches, filters, reads, and minor synchronous successes must not
notify. Preserve the embed style. Any future environment variable must also be
added to the Unraid template and documented.

Tests keep Discord disabled or use mocked HTTP transport. Real webhook values
are never committed. The real-webhook regression remains gated to Milestone 9.
