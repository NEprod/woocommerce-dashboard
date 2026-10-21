# Security

This is a personal project baseline. Report suspected vulnerabilities privately to the repository owner rather than publishing usable secrets or exploit details.

## Rules

- Shopify Orders adds customer contact/address and historical personalisation data
  to the existing private instance database. Orders pages/status/Sync Now require
  an authenticated administrator; writes to the local queue also require CSRF.
  Responses are no-store/private. The importer registers no Shopify order mutation,
  requests only read_orders in addition to existing scopes, and never sends customer
  data to Discord or request/error logs. Scope release/install approval is separate.
- Orders source properties are untrusted. Jinja escapes readable answers and technical
  JSON; missing/malformed private snapshots retain public answers with warnings. Do not
  treat a browser-generated snapshot as tamper-proof. Protect order-containing backups
  and review retention/deletion obligations; local redaction has a durable re-import
  tombstone, but does not erase historical backups automatically. See SHOPIFY_ORDERS.md.

- Provide secrets only through the runtime environment.
- Never commit `.env` or any variant containing real values.
- Never commit `instance/site.db` or another database; it contains users and password hashes.
- Never commit a real catalogue, `.scanned`, `.scanned.pending`, `.update`, `sku_index.json`, generated output, backups, or logs.
- Protect Discord webhooks as credentials. Rotate any accidentally exposed webhook or token immediately.
- WooCommerce Consumer Keys and Consumer Secrets are runtime-only secrets. They
  must not be rendered, persisted, logged, included in operation payloads, or
  sent to Discord. Authentication is sent only to same-origin HTTPS routes and
  never through query-string credentials.
- WooCommerce discovery/preview remains GET-only. Controlled publishing uses a
  distinct publisher transport limited to reviewed POST/PUT/PATCH requests;
  DELETE is forbidden, mutating redirects are never replayed automatically,
  and cross-origin redirects, oversized bodies/responses, malformed URLs and
  unbounded redirects remain rejected. TLS verification stays enabled.
- A publish request is accepted only from a current digest-bound preview for the
  same configured store and at most ten eligible selected parents. Persisted
  operation state contains bounded summaries/digests and verified IDs, never
  complete payloads, raw Woo responses, headers, cookies or credentials.
- Do not expose Flask debug mode publicly.
- Production refuses a missing or recognized placeholder `SECRET_KEY`. Supply a
  stable long random value only through the runtime environment; never generate
  a different value on each container start.
- Review staged files and Docker build context before every push.
- Keep persistent mounts and backups access-controlled.
- Treat `/app/instance` as sensitive application data: `site.db` contains user
  records and password hashes, and `backups/` contains historical database copies.
- Mount `/app/instance`, `/catalogue`, and `/output` read/write from explicit host
  directories. Keep catalogue/output outside appdata and back up the instance and
  catalogue separately at an understood consistency point.
- The production container begins as root only inside the entrypoint so it can
  validate `PUID`/`PGID`, prepare `/app/instance`, and correct its application-owned
  state. `gosu` then replaces it with the non-root Gunicorn process before application
  import or migrations. UID/GID zero and malformed identity values are rejected.
- Recursive ownership correction is limited to `/app/instance`. Catalogue and
  output contents are never recursively chowned automatically; grant the configured
  identity explicit host-side access and inspect exact paths before changing ownership.
- Unraid templates must contain only placeholders and harmless defaults. Never
  save a generated `SECRET_KEY` or Discord webhook into tracked/shared XML.
- Keep catalogue operation scopes concise. Operation history must redact credential-like keys and errors and must not store full metadata payloads.
- Central diagnostic redaction covers authorization and cookie headers, bearer
  tokens, API/WooCommerce credentials, passwords, Discord webhooks, session
  secrets, and sensitive configured/home path prefixes before persistence or
  browser presentation. Never log a complete environment mapping.
- Pending marker envelopes may contain only the established `.scanned` payload and bounded coordination fields; never add authored JSON, credentials, webhooks, or resolved database rows.
- Filesystem folder browsing is an authenticated administrator setup function.
  `/folder-picker` must never be exposed anonymously, and its responses must not
  be repurposed as a public filesystem API.
- Catalogue thumbnails are served only through the authenticated opaque
  `/catalogue-images/products/<id>` route. The resolver is confined to the
  configured catalogue root and projected product folder, rejects traversal and
  symlink escape, validates supported image content, and never exposes authored
  host/container paths or provides a general-purpose file-download endpoint.

If a credential may have entered Git history or an image layer, rotate it first; removing the text afterward is not sufficient.

## Administrator-controlled password recovery

Self-service password reset is disabled for the first stable release. The help
page sends no email, generates no token, and does not look up the submitted
account. Old reset links cannot change a password. Do not paste old links into
support messages or request logs; URL paths can still appear in access logs,
but those old links no longer authorise recovery in this release.

Recovery requires trusted administrator access to the host/container. Verify the
requester's identity out of band and take the normal access-controlled SQLite
backup before proceeding. Identify the actual container with `docker ps`; do not
guess its name. In a private, unrecorded interactive terminal run:

```bash
docker exec -it <confirmed-container-name> python -m app.admin_recovery
```

Use the configured non-root application UID/GID with Docker's `--user UID:GID`
option where required by local policy. Enter the existing administrator username,
then the new password twice at the hidden prompts (at least 12 characters).
No password, hash or reset token is printed, passed as a command argument, or
written to shell history. Never add a password argument, pipe one in, put it in
an environment variable, or run this from a terminal recording session. If a
hidden interactive prompt is unavailable, the command refuses recovery.

The command opens the existing configured SQLite database in read/write-only
mode (never creates one), updates only one existing administrator's password
hash, and performs no migrations, scheduler startup, Shopify requests or account
creation. Reopen a browser and verify login with the new password; the previous
password must fail. Existing sessions are not revoked by this local lost-password
procedure. Suspected account/session compromise requires a separate incident
response; do not treat this command as session revocation or change `SECRET_KEY`
casually, because that also affects signed sessions and review approvals.
