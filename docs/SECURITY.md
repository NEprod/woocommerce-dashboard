# Security

This is a personal project baseline. Report suspected vulnerabilities privately to the repository owner rather than publishing usable secrets or exploit details.

## Rules

- Provide secrets only through the runtime environment.
- Never commit `.env` or any variant containing real values.
- Never commit `instance/site.db` or another database; it contains users and password hashes.
- Never commit a real catalogue, `.scanned`, `.scanned.pending`, `.update`, `sku_index.json`, generated output, backups, or logs.
- Protect Discord webhooks as credentials. Rotate any accidentally exposed webhook or token immediately.
- Future WooCommerce consumer keys and WordPress credentials must remain runtime secrets.
- Do not expose Flask debug mode publicly.
- Use a long random `SECRET_KEY` outside isolated development.
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
- Pending marker envelopes may contain only the established `.scanned` payload and bounded coordination fields; never add authored JSON, credentials, webhooks, or resolved database rows.

If a credential may have entered Git history or an image layer, rotate it first; removing the text afterward is not sufficient.
