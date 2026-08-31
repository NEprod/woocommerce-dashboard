# Security

This is a personal project baseline. Report suspected vulnerabilities privately to the repository owner rather than publishing usable secrets or exploit details.

## Rules

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
