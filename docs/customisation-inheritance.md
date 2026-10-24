# Canonical customer-field inheritance

Only customer field definitions changed. Other customisation properties keep their
existing scalar precedence, including `proof_policy` and `storefront_enabled`.
No migration or catalogue rewrite is performed.

## Canonical source

Shared `product_info.json`:

```json
{"customisation":{"storefront_enabled":true,"fields":{
  "recipient":{"type":"text","label":"Recipient Name","required":true,"order":10},
  "age":{"type":"number","label":"Age","required":true,"order":20}
}}}
```

Product Override `product_info.json`:

```json
{"customisation":{"fields":{
  "personal_message":{"type":"text","label":"Personal Message","required":false,"order":0}
}}}
```

Effective order is Recipient Name, Age, Personal Message. Existing numeric order
and stable-ID tie-breaking apply *within* each authored group, not across groups.
The resolver adjusts only effective addition order where needed by the existing
publisher. Source JSON and inherited settings are never modified.

A pinned reusable `customisation.template` on Shared Collection/owning JSON supplies protected fields first,
followed by distinct shared fields and then distinct product fields. The pinned
library revision remains authoritative and must be retained in database backups.
Fresh reconstruction from standalone JSON is supported; template references still
require their existing library revisions, as before.

Absent or empty `fields` inherits; it does not clear fields. An explicitly disabled
storefront remains disabled. Locally owned disabled fields retain their meaning.

## Why this correction was needed

The prior resolver combined matching IDs as property patches and applied removal
tombstones. The editor exposed those inherited patch/exclusion controls. Global
ordering could also place a local order-0 field ahead of inherited order-10 fields.
This could silently change a required field. Now matching inherited IDs are a
validation conflict, even if the duplicate definitions happen to match.

The editor shows the effective list, labels inherited fields read-only and local
fields Product addition, and permits editing/removal/reordering only of local
fields. Shared/template edits remain in their existing owning editor.

## Invalid source: validation only, no legacy compatibility

Inherited patching, removal tombstones, template exclusion and Product Override
template selection have no implementation or editor controls. The field schema
requires complete `type` and `label` definitions. Duplicate effective IDs are
rejected, even when the definitions match. Product Overrides may contain only
distinct additions under `customisation.fields` (plus unchanged scalar settings).

Invalid source produces a precise validation error, without changing JSON:

- `customisation.fields.<id>.remove is obsolete` (including `remove:false`).
- `customisation.template is obsolete in Product Overrides` (any value).
- `customisation.template:null is obsolete` on an owner document.
- Missing `type`/`label` identifies the incomplete field, not an inherited patch.

The merchant must manually correct such files. No migrations, fallback merge,
alternate storage or automatic conversion is provided. The publisher cannot form
an approved configuration until the definition is valid. Unrelated products
remain usable. Image source checks remain independent; the conflict cannot break
a sibling editor or Products list. A conflicted row explicitly labels its price
as last indexed rather than claiming a fresh metadata-derived price.

Do not automatically delete instructions, rename IDs, copy inherited fields or
make required fields optional. Confirm their intended meaning with the merchant.
A genuinely new field needs a distinct identity. A change to a common field must
be reviewed at its collection/template owner. No real Unraid catalogue conflict
inventory was available: the configured Mac catalogue is empty.

The existing publisher consumes the resolved canonical configuration, using the
same reviewed mutation and readback. No extension, theme, customer-answer parser,
Production Time, scopes or Shopify API payload architecture changed.

## Unraid acceptance (initially read-only)

1. Before updating, back up appdata/database (including template revisions) and
   authoritative catalogue JSON. Keep the previous immutable image tag.
2. Inspect representative shared and child JSON without saving or rescanning.
   Check `customisation.fields` IDs and pinned `customisation.template` references.
   Flag duplicate inherited IDs, `remove` instructions and any child template key
   for merchant review; do not remove them automatically.
3. After the merchant deploys the immutable image, open a representative Simple
   child, Variable Collection child and Single Variable shared editor. Opening is
   read-only. Confirm inherited IDs/settings are unchanged, list order is correct,
   inherited controls are read-only, and existing local fields follow them.
4. Open any flagged product: expect a review conflict, not silent replacement.
   Confirm other products still open. Do not approve Shopify changes during this
   initial check. Review & Sync must show the complete supported effective fields.
5. Separately, when ready to validate authoring, add one disposable optional local
   field, Save, Reopen and run the normal bounded rescan. Verify only that local
   field was authored in child JSON and parent bytes stayed unchanged. Remove the
   test local field through its local control. This step intentionally writes
   local JSON and is not part of the read-only inspection.
6. Confirm Orders, Production Time and package IDs remain intact. Any real Shopify
   publication still requires the normal explicit signed approval.

Automated checks use synthetic JSON, mocked Shopify responses and offline Docker
mounts. Mobile browser coverage uses a 390px Chromium viewport, not native Safari.
One pre-existing test expects migration head 0015 and only the template table;
that obsolete assertion also fails on untouched a3c4e0d37877. Current migration
head remains 0018; the offline upgrade/restart smoke checks actual compatibility.

## Release validation

- 413 bounded Python tests passed for canonical validation, JSON/editor/scanner
  round-trips, inheritance, reviewed Shopify publication, Orders and reporting.
- The baseline migration assertion above remains excluded; it was reproduced on
  the untouched starting commit and is unrelated to canonical inheritance.
- 125 focused JavaScript tests passed, including existing storefront/Production
  Time and same-variant personalisation identity checks.
- Isolated Chrome: desktop Simple, mobile-width Variable Collection and desktop
  Single Variable, including Add/Edit/Remove, signed Save, Reopen and Rescan.
- Network-disabled Docker: two workers, 0017-to-0018 upgrade and automatic backup,
  template and sparse JSON persistence across restart, six stable linked packages,
  retained operation/recovery evidence and separate same-SKU order answers.
- Syntax checks and `git diff --check` passed. No production Shopify calls.
