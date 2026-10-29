# Edit / save / publish lifecycle audit — 29 October 2026

## Scope and evidence boundary

Baseline: `5420a3394b646c74c2e9569a553f4f31ddd448eb` on develop. The running
Unraid container was confirmed at that revision, image digest
`sha256:883bfba77787dc758c28dddad5d52f7394fe3a8613f8e75f77dfbfa0ce1ce047`.
Automatic publishing was paused. No live scan, reconciliation, publication,
package correction, settings change or database repair was performed.
Read-only SQL used a read-only database URI and `PRAGMA query_only=ON`.
The source-resolution probe blocked network requests and did not start the app's
runtime/scheduler. A temporary unsaved browser field was discarded.

Confirmed mounts remain `/catalogue`, `/intake`, `/output`, `/taxonomy` and
`/app/instance`; no mount or storage relocation is required.

## Real installation findings

### ADOC-0001 through ADOC-0004

The source folders and shared JSON exist. The shared save at 23:01:16 UTC retained
Live, publication preferences, informational attributes, Product Type and shipping
settings. Its mandatory pre-save backup establishes that these were multiple
real changes, not just a status change. Scoped scan
`33d64e6ffd944b5eae230a79efd2acc7` succeeded, updating four products with no
scanner errors. No full scan is necessary to explain this incident.

The shared customisation is `{"storefront_enabled":true,"proof_policy":"none"}`:
there are **no fields and no template**. Every affected product inherits it.
Fingerprinting calls `public_configuration`, which correctly refuses an enabled
configuration without supported enabled fields, but raised an untyped ValueError.
The read-only comparison path converted that into the false generic explanation
“Local catalogue evidence is unavailable”. Queue Attention already retained the
more useful underlying error.

Manual discovery/reconciliation run `f51c49a8d7f04dc0bf42dcc8b786cc9b` and child
status operation `4eaa76c4192748f1965e6971776f7283` therefore checked four products,
reported four unavailable results and made one shared Shopify connection request.
No Shopify mutation occurred. Earlier noon comparisons are retained historical
evidence, not proof that the new configuration matches Shopify. ADOC-0001 retains
its existing Shopify identity; ADOC-0002/3/4 have no retained linked identity.

Correction: use the existing typed customisation-definition error and existing
readiness-finding mechanism. Product status reports the specific actionable
customisation blocker; inspection can complete with findings rather than claiming
an API/source execution failure. No new successful fingerprint, scan checkpoint,
remote equality or timestamp is manufactured. Publication remains blocked until
the merchant adds the intended supported fields or deliberately disables the
empty configuration at its owning source, then saves normally.

### Stable field ID

The rejected ID was `dogs-name`. The existing canonical rule is unchanged:
`^[a-z][a-z0-9_]{0,63}$`. `dogs_name` is valid; hyphens, spaces, uppercase and a
leading digit are not. The old message explained only “lowercase”, concealing
the actual problem. UI help, UI error and backend error now explain the full
rule. Mobile errors scroll into view. No identifiers are renamed, and inherited
or existing IDs remain protected. The live rejected field never became authored
JSON; a local fixture verifies successful canonical creation/edit/save/reopen.

### LCBC-0001

Current product override is `{}`. The 22:54 pre-save backup contained
`{"live":false}`. The saved removal of that key means it now inherits **Live** from
its collection. Its retained Shopify identity remains Draft. Current source
revision matches its queue revision; its scoped scan and following read-only
comparison completed. Update Available is consequently accurate for the current
authored intent. The evidence does not establish whether removing that override
was intended. No automatic restoration to Draft or activation was performed.
If it should remain Draft, the merchant must explicitly choose and save Draft.

### 16bit-0002

Correction `95021c10083b4acb9ea04f012966c3d6` retains an explicit Shopify Accepted
package acknowledgement at 09:19:39 UTC, bound to the existing product, variant
58770588369279, InventoryItem 57207133438335 and mapped package. It correctly says
`independently_verified:false`. Current source and queue revisions match; queue
is verified. The original uncertain operation
`5b0c73fae2464db4933cbcf43b967040` is preserved as immutable recovery history and
is superseded by the accepted correction. It must not be replayed.

### Catalogue Healthy

54/54 Healthy is local catalogue availability (15 parents + 39 variations), not
Shopify equality or permission to publish. Separate operational/Shopify blockers
remain meaningful.

## Existing lifecycle contract — unchanged

| Path | Source / refresh | Eligibility and remote boundary |
| --- | --- | --- |
| Product Override save | Sparse authoritative child JSON; containing-collection scan | Record revision/queue/status only. No Shopify call or publication. |
| Shared Collection save | Authoritative parent JSON; affected-collection scan | Resolve inherited values, respecting explicit child intent. No publication. |
| Collection Draft → Live | Same shared-save path | Eligible children become available; explicitly Draft children stay Draft. Wait for hourly publisher or signed approval. |
| Product Draft → Live | Same override-save path | Accumulated eligible changes target retained identity. Wait for publishing trigger. |
| Live metadata edit | Local save and scoped scan | Create/Update Available, subject to valid current source and other safeguards. |
| Customisation add/edit | Same canonical `customisation.fields`, protected inherited fields | Only local additions saved in child; same scoped scan and public configuration contract. |
| Review & Sync | Current effective source + read-only remote comparison | Explicit signed confirmation durably accepts browser-independent work. |
| Hourly publisher | Persisted eligible work, no local scan | Existing policy/lease/source/identity checks; changed-only writes; acknowledged/readback evidence; queue settlement. |
| Daily/manual reconciliation | Read-only Shopify comparison against local authority | No local scan or remote write. Valid drift may queue normal future publishing; missing authority never fabricated. |
| Saved Products status | Persisted derived projection | Publisher records verified status. A separate comparison invalidates projection for the existing saved-view worker; GET never publishes/scans. |

No scheduler, scanner, publication trigger, identity, Orders, package, Production
Time or Shopify extension production code changed in this release.

## Executed isolated workflow

`tests/test_edit_publish_lifecycle.py` uses actual signed save routes, original
scanner/ingestion, real scheduler entry points and a controlled Shopify adapter.
It runs both Product Override and Shared Collection activation scenarios:

1. Save shared Draft configuration with a required inherited customer field.
2. Save local description and distinct optional `dogs_name`; assert parent unchanged.
3. Wait for each real containing-collection scan; reopen editor and verify sparse JSON.
4. Explicitly execute an hourly cycle: Draft remains local, no API calls/create.
5. Save Live intent; assert save/scan still made **zero Shopify calls**.
6. Explicitly execute the existing hourly entry: one verified create + activation.
7. Save another description; assert no Shopify call until the next explicit hourly cycle.
8. Update the same identity; no ProductCreate, image upload or unchanged customisation write.
9. Assert saved Up to Date immediately after publication, without manual Reconcile.
10. Run a read-only comparison and existing projection worker; assert no mutations.
11. Open a new application/database session; verify identity, queue and status persist.
12. Execute another unchanged hourly cycle; zero API calls or duplicate writes.

The separate real-browser fixture covers desktop 1440×1000 and mobile 390×844:
hyphen rejection, exact visible guidance, underscore assignment, signed local save,
actual scoped scan, reload, editable local field with stable ID, read-only inherited
fields and no horizontal overflow. Browser sessions are isolated from live Safari.

Existing network-isolated Docker scenarios additionally exercise upgrade/restart
with two/four workers, original `/app/instance`, accepted package finalisation and
14 variants/53 acknowledged images, preserving receipts without duplicate writes.
These are mocked Shopify results, **not** production-store publishing acceptance.

Three assertions failed identically on untouched baseline 5420 and were corrected
as test fixtures/contracts: missing checkpoint in drift fixture; requiring a policy
change to reconsider an unchanged verified product; expecting synchronous HTTP
completion instead of durable background acceptance. No production safety was
relaxed to satisfy them.

### Validation record and production-file justification

- 186 affected checks: scanner contracts/scope, additive customisations, status
  reporting, saved views, Draft settlement, package correction, finalisation and
  variable verification.
- 67 scheduler/manual/automatic/checkpoint/lifecycle execution checks, including
  both activation owners and no mutation on save.
- 114 designer/metadata/onboarding checks.
- 48 publishing-safety/storefront/lifecycle checks (overlaps other groups), plus
  four final readiness regressions after the specific error-message refinement.
- 68 JavaScript checks and the desktop/mobile browser scenario passed.
- Existing deprecation warnings remain; no new test failures were waived.
- Preliminary unlabelled Docker images passed functional/restart assertions but
  their final reporting rejected the missing revision label. The exact labelled
  committed image must pass the same checks before release; final image/digest
  results belong in the delivery report.

| Production file | Why modified |
| --- | --- |
| `app/storefront_customisations.py` | Preserve specific authored-configuration errors as the existing typed exception. No payload/schema change for valid products. |
| `app/shopify_drafts.py` | Translate that local configuration error at the common review boundary into an existing readiness finding; retain failed-check evidence without claiming equality. |
| `app/shopify_customisations.py` | Preserve the same specific finding in customisation-only review rather than replace it with a generic error. |
| `app/customisations.py` | Explain the already-enforced stable-ID rule on backend validation. No expanded or renamed identities. |
| `app/static/assets/js/customisation-designer.js` | Explain the same rule before/on Apply and scroll its error into view on mobile. No new editor mode. |

## Merchant acceptance after deliberate deployment

Keep automatic publishing paused. Back up `/app/instance` and authoritative source
files before deployment. Keep the previous immutable image for rollback; there is
no database migration in this correction. Do not run a full scan to hide findings.

- [ ] Confirm running revision/mounts and paused publishing.
- [ ] Open ADOC Shared Collection: inspect the enabled-but-empty customisations.
      Confirm the dashboard explains that specific blocker, not missing source.
- [ ] Deliberately add the intended supported fields (e.g. ID `dogs_name`, label
      “Dog's name”) or disable the empty configuration. Save once; only the normal
      affected-collection scan should run. Capture saved JSON, scan ID and eligibility.
- [ ] Confirm no product creation/update occurred on Save or Draft → Live alone.
- [ ] Confirm inherited fields remain read-only; child JSON holds only local additions.
- [ ] Decide LCBC-0001 intent explicitly: retaining `{}` means inherited Live; choose
      an explicit Draft override if it must remain linked Draft. Do not activate it
      as a test on the production store.
- [ ] Confirm 16bit-0002's Accepted package correction remains current and original
      uncertainty remains historical. Do not assign the package again.
- [ ] In a separately authorised staging store, run one signed Review & Sync or
      deliberately allow one hourly cycle. Capture operation/event IDs, changed-field
      plan, acknowledged writes, complete readback, retained identity and Up to Date.
- [ ] Edit one field, save, observe Update Available without mutation; then explicitly
      authorise the existing publishing workflow. Verify same identity and no package replay.
- [ ] Verify unchanged hourly cycle does no work; linked Draft edits remain paused;
      reconciliation stays read-only; restart starts no full local scan.

Production publishing remains merchant-controlled. No live acceptance action was
automatically executed by this development release.
