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
- Keep catalogue operation scopes concise. Operation history must redact credential-like keys and errors and must not store full metadata payloads.
- Pending marker envelopes may contain only the established `.scanned` payload and bounded coordination fields; never add authored JSON, credentials, webhooks, or resolved database rows.

If a credential may have entered Git history or an image layer, rotate it first; removing the text afterward is not sufficient.
