# Storage and Retention

The application has three deliberately separate persistent storage domains:

| Container path | Owner | Purpose |
| --- | --- | --- |
| `/app/instance` | application | SQLite and controlled database backups |
| `/catalogue` | catalogue/scanner | authored metadata, source images, markers, pending identity, and SKU indexes |
| `/output` | user | copied/generated WooCommerce upload output |

All three paths require explicit read/write mounts. Catalogue or output content
must not be moved beneath `/app/instance`, and persistent state must not depend
on the writable container layer.

## Runtime configuration

Production startup requires a non-empty, non-placeholder `SECRET_KEY` supplied
through the runtime environment. The application never writes this value to the
database, a generated `.env`, logs, backups, or operation payloads. Reusing the
same strong value preserves sessions across container restarts.

Generate a value on a trusted host without placing it in documentation or source:

```bash
openssl rand -hex 32
```

Diagnostic redaction masks authentication/cookie headers, bearer tokens,
passwords, API and WooCommerce credential assignments, Discord webhooks, and
sensitive home/configured path prefixes before browser presentation or database
persistence. Catalogue-relative context remains visible.

## Operation retention

Live scan state is process-local and remains subject to the documented
single-worker/single-replica boundary. Active and unresolved-recovery runs are
never removed. The newest 20 ordinary completed runs remain in memory. Each
unconsumed log queue retains at most 2,000 lines and approximately 2 MiB of text;
oldest lines are discarded with an explicit truncation marker while completion
summaries remain available.

Persistent routine successes are removed only when both older than 180 days and
outside the newest 1,000. Failed or otherwise non-routine resolved operations
are retained for at least 365 days. Running, pending, unresolved recovery, active
process references, and the newest operation of each operation type are
protected. Related item rows are deleted through the operation relationship in
the same database transaction. Cleanup failure is redacted and does not change
the completed operation result.

## Backup retention

`/app/instance/backups` is mode `0700`; verified SQLite backup files and their
recovery markers are mode `0600`.

- Reconstruction backups retain the newest 10 plus every backup newer than 30
  days. A backup associated with recovery-required or failure is protected for
  at least 90 days.
- Metadata editor backups are stored beside each authored `product_info.json`.
  For each source file, the newest 10 plus every backup newer than 90 days are
  retained. UTC microseconds and a random suffix prevent same-second collision.
- Migration backups retain the newest verified backup for each migration
  transition and the newest overall recovery point. Additional unprotected
  migration backups are capped at a total of 20.

Pruning runs only after the new backup is validated. Cleanup failure never
invalidates the new write. The newest verified recovery point is never removed.
Back up `/app/instance` and `/catalogue` together at an understood consistency
point; application retention is not a substitute for an external backup policy.

## Temporary files

Atomic marker and SKU-index replacement retains its protected recovery
semantics. Cleanup only recognizes application-created database backup,
database restore, and metadata-editor temporary names. A temporary must be more
than 24 hours old and have a valid final destination. Metadata cleanup is skipped
while a catalogue operation is active.

Cleanup never targets `.scanned`, `.scanned.pending`, `.update`,
`sku_index.json`, authored metadata, source images, or generated output.

## Logging

The application does not create `/app/instance/logs`. Gunicorn and application
messages remain on stdout/stderr. Compose uses Docker's `local` logging driver
with 10 MiB files, five retained files, and compression, for an approximate
50 MiB pre-compression ceiling per container. Docker or Unraid—not the Python
application—enforces this host log policy.

## Deferred release hardening

Base-image digest pinning, a full transitive dependency lock, determination of
the standalone Tkinter utilities' support status, and user-configurable persisted
retention settings remain deferred. No database migration was added for
retention.
