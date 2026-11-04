# Royal Mail Click & Drop product catalogue export

## Scope and baseline

Starting local and read-only verified Unraid revision:
`0df66f051d3b16871bff59ea614f7f92c077ad1e` on `develop`.
The deployment check used Safari's existing Unraid session and `docker inspect`
only. Existing `/app/instance`, `/catalogue`, `/output`, `/intake` and `/taxonomy`
mounts were confirmed in Unraid. No live source or appdata changes were made.

The release adds one local customs field and one download button. No migrations,
new dependencies, export database, remote APIs, publisher/scanner modifications,
Intake changes, package changes or deployment actions are involved.

## Customs Description

Collection Editor → existing shipping/customs section, alongside Country of
Origin and HS Code. Short factual description, maximum 50 characters, one line.
Examples: `Greeting card`, `Wooden ornament`, `Christmas decoration`.

```json
{
  "customs_description": "Greeting card"
}
```

This is an optional top-level scalar in the existing `product_info.json` schema.
Normal inheritance applies: shared collection → product override. No inherited
definitions are copied into a child. A child may use the existing scalar override
control; disabling it restores inheritance. An explicitly blank value clears the
resolved description, including when the collection has a description. It is
not an Extended Customs Description or the product listing description.

The existing Save Metadata handler performs its existing containing-collection
scan, ingestion and saved-view capture. SQLite persistence uses the existing
`shopify_publish_job.view_json` **authored** resolved catalogue projection; there
is no new column/table. Completed scan evidence remains in the existing
`scan_operation_id` and catalogue operation records. The export reads that saved
projection with existing resolved Product/Variation rows, not source JSON.

The commerce snapshot explicitly excludes `customs_description`. Simple and
Variable signed local plans and canonical Shopify source fingerprints are
unchanged by this field. It is never a Shopify metafield, description, SEO value
or change reason. Existing scanner triggers and read-only refresh handoff are
unchanged. A customs-only edit of a verified linked Live fixture retained the
same verified queue revision/generation, Up to Date status and remote identity;
the next explicit hourly scheduler cycle made no API calls.

## Download

Shipping Packages → **Export Royal Mail CSV**, next to Add Package.
Admin-only GET `/packaging/royal-mail.csv`, private/no-store response.

Filename uses the current Europe/London date:
`tlc-royal-mail-products-YYYY-MM-DD.csv`.

The existing dashboard notification conventions are inline `role="status"`
feedback with existing `alert` success/error styles (there is no active shared
toast service in the current dashboard). No notification subsystem was added.
After a successful response, the browser initiates a download and shows the
filename, row count and compact warning counts. A failed HTTP/network/invalid
response shows an error and cannot show a success notification or CSV download.
Browser download initiation is tested; final device save permissions remain
under the browser/user's control.

| CSV heading | Click & Drop Products mapping | Local source |
|---|---|---|
| SKU | SKU | Existing Simple or individual sellable Variant SKU |
| Product Name | Name | Existing resolved product/variant title |
| Price (GBP) | Price | Existing effective selling price, exact decimals |
| Weight (KG) | Weight in KG | Existing persisted product/variant **Weight (g)** ÷ 1000 |
| Customs Description | Customs Description | Only the inherited local field above |
| HS Code | HS Code | Existing inherited `harmonized_system_code`, text |
| Non-standardised Manufacturer ID | **Leave unmapped** | Same existing SKU, reference for separate entry |
| Country of Origin | Country of Origin | Existing inherited ISO country, formatted as English country name |

No VAT is added, removed or calculated. Existing sale-date/modifier calculations
are reused read-only; variant prices and weights are already scanner-resolved.
No package tare, parcel measurements or shipping charge is included. Gram units
are explicit in the current scanner/SQLite contract, not inferred from size.

Active local Drafts are included. Variable parents are not included: each active,
visible/sellable variant gets its own row and actual SKU. Missing/hidden catalogue
records are not exported. Unsupported product types are reported, not guessed.

Missing optional prices, weights, descriptions, HS codes and countries stay blank
with warnings. Unknown country codes stay blank with a warning, never default GB.
Country names come from the bundled public-domain IANA ISO-3166 table with common
English display names, including GB → United Kingdom. No country or HS code is
invented. No deployment/environment country fallback is silently substituted for
an absent locally authored country.

Duplicate (case-insensitive), missing, whitespace-padded or formula-leading SKUs
block the whole download with a precise identity error; rows are not silently
merged, dropped or renamed. Invalid known weights/codes also block. Names over
Royal Mail's documented 100-character editor limit are warned about, not silently
truncated. These names should be checked in the import preview.

UTF-8 CSV is generated with Python's standard `csv.writer`, proper CRLF/quoting,
commas/apostrophes/Unicode preserved. Dangerous formula-leading/control-containing
text cells are prefixed with an apostrophe **in the download only**, with a warning.
SKU identities are never changed for this purpose. Leading-zero HS strings are
preserved literally in the file; do not open/resave them as numeric cells in Excel.
Import the original download, or explicitly select Text in a spreadsheet importer.

The published Products importer limits (20,000 rows / 20 MB) are enforced without
truncation. Empty catalogues produce a header-only CSV and zero-row notification.

## Source freshness and read-only boundary

Export requires a complete saved authored projection, matching saved/requested
revision and succeeded retained scan operation. Pending/unavailable projections
or an active source refresh block export with the product/action explained.
It does not attempt to repair evidence or run a scan. Review the product's
existing source/scan status and use its ordinary scoped action if genuinely needed.

This is an export of **saved** local evidence, not a fresh filesystem verification.
Unobserved external edits cannot be discovered without reading files; this route
deliberately does not do so. External file changes must go through the existing
normal refresh workflow before relying on the export. Normal editor saves already
perform that refresh. Shopify comparison health, package recovery and Draft/Live
publishing approval do not grant or deny local export authority.

Tests deny source-document reads, gallery inspection and external requests, and
observe SQL to ensure the download performs no INSERT/UPDATE/DELETE/schema writes.
The file is returned directly; there is no server-side export file/history store.

## Royal Mail compatibility findings — checked 4 November 2026

The official [Products import guide](https://help.parcel.royalmail.com/hc/en-gb/articles/115001530293-How-to-import-your-product-data)
lists the supported price, weight and customs mappings. SKU is the only mandatory
mapping. It does **not** list Non-standardised Manufacturer ID. This is separate
from the Orders CSV specification; support there is not claimed for Products.

The intended SKU value is retained in the clearly labelled seventh CSV column,
which should be left unmapped. Mapping the
supported columns is the ordinary import workflow; account-specific mapping UI
has not been tested because no real Royal Mail import was authorised. If the
live mapping list has changed, inspect it before importing; do not map this column
to a barcode or an unrelated field.

The official [product editor guide](https://help.parcel.royalmail.com/hc/en-gb/articles/115001413614-How-to-create-a-product)
does recognise Non-standardised Manufacturer ID (EU). Simplest separate entry:
Products → expand the imported product → enter its SKU as the EU non-standardised
manufacturer ID → Update. Do not invent an EAN/GTIN. This export does not claim
the identifier was populated if it was not mapped or entered separately.

Royal Mail's Products guide does not document an exhaustive country-name/code
acceptance matrix. Export uses readable full names (not Shopify's alpha-2 code)
and the live import preview must confirm them. No order-import schema was used
as authority for Products mapping.

## Fictional example

```csv
SKU,Product Name,Price (GBP),Weight (KG),Customs Description,HS Code,Non-standardised Manufacturer ID,Country of Origin
FIC-CARD-001,"Élodie's card, floral",3.50,0.05,Greeting card,001234,FIC-CARD-001,United Kingdom
FIC-ORN-001-S,Woodland ornament Small,4.50,0.1,Wooden ornament,442019,FIC-ORN-001-S,United Kingdom
FIC-ORN-001-L,Woodland ornament Large,6.50,0.25,Wooden ornament,442019,FIC-ORN-001-L,United Kingdom
```

These are fictional values, not tariff classification advice or real catalogue
records. Only the two ornament variants appear, not a parent row.

## Executed validation

- Focused real editor/collection scan tests for Simple, Variable Collection and
  Single Variable; override save/blank/inheritance; reload and new DB session.
- Complete Simple/Variable Shopify plan, managed fingerprints and no-change
  comparisons unchanged; linked Live customs-only save remains verified and next
  hourly cycle is a no-op. Shopify responses are controlled test doubles only.
- CSV rows, SKU conflicts/missing/unsafe identities, sales without VAT, exact
  grams-to-kg conversion, UTF-8/quotes, leading zeros, missing values, errors,
  current-date filename, authentication, stale evidence, no filesystem/API writes.
- Desktop 1440×1000 and mobile 390×844 isolated Chrome: save → actual scoped scan
  → reload → real browser download; success/error messages, no horizontal overflow,
  no JavaScript errors. Screenshots under `docs/evidence/royal-mail/`.
- Relevant existing HS, editor/scope, scanner parity, Simple/Variable/manual and
  automatic publishing, Intake and package suites. See final delivery for totals.
- Existing offline Docker upgrade/restart harness extended with shared customs
  save and CSV checks, 2 and 4 workers, disposable `/app/instance`/catalogue/output,
  `--network none`; automatic publishing remains disabled in those fixtures.

Results: 349 bounded Python tests passed. The final focused export/schema run
passed 47 tests (overlaps that regression run; do not add the counts together).
31 JavaScript checks and both browser layouts passed. Two- and four-worker
Docker checks passed. No remaining failures in these selected suites; the entire
repository test suite was not run. Initial new test defects were fixed without
production workarounds: a detached ORM test object, an unrelated fixture product
being restored by a shared scan, and an old identity-only package-history record
incorrectly labelled as a sellable source product. The latter correctly blocked
export until the fixture was marked archival; no scan evidence was fabricated.

No claim is made of a real Click & Drop import, iPhone Safari download, live
customs save or production Shopify acceptance during development.

## Manual Unraid deployment / rollback

1. Back up existing appdata at `/app/instance` and the authoritative catalogue JSON.
2. Install the delivery's immutable `royal-mail-<commit>` image manually. Leave all
   current mounts, scheduler controls, credentials and mappings unchanged.
3. Confirm embedded revision and existing startup behaviour. No full scan is needed
   merely for upgrading this code; no database migration was introduced.
4. Open one existing Collection Editor, enter an accurate Customs Description and
   Save Metadata. Wait for its normal affected-collection refresh; reopen and check.
5. Confirm Shopify descriptions, pending changes and Draft/Live intent are unchanged
   by that field alone. Other genuinely outstanding changes stay outstanding.
6. Shipping Packages → Export Royal Mail CSV. Check filename, row count and warnings.
7. Compare a Simple row and two differently priced/weighted variants against TLC.
   Check Draft inclusion, no variable parent row, leading-zero code, country name.
8. If rollback is necessary, reinstall
   `neprod/woocommerce-dashboard:hs-code-0df66f051d3b` with the same mounts.
   Preserve all new authored JSON and receipts. Do not reset the database. The
   previous version preserves unknown local JSON keys; the export UI is absent.

Deployment is merchant-controlled. No Unraid restart/update, automation setting
change or production catalogue write is part of this development release.

## Manual Click & Drop acceptance (separate approval to import)

- [ ] Open **Products → Import**, not Orders → Import; upload the original CSV.
- [ ] Select first row is a header. Choose Create/Update/Create and update deliberately;
      Update can overwrite existing mapped product values. First inspect a small
      disposable fictional subset, only if authorised in the chosen account.
- [ ] Map the seven supported columns using the table above. Leave manufacturer ID
      unmapped unless the current Products mapping explicitly supports that exact field.
      Do not map package sizes, services, postage, inventory or barcodes.
- [ ] Check price, weight, country and leading-zero HS code in preview; inspect long
      name and missing-value warnings. Capture the mapping and preview as evidence.
- [ ] Import only after merchant approval. Inspect imported rows and any Import History
      errors. Confirm no variable parent row or unintended package/service change.
- [ ] Enter the EU non-standardised manufacturer ID separately where required, using
      the actual SKU for appropriate TLC-manufactured goods. Do not claim CSV mapped it.
- [ ] Remove only explicitly disposable acceptance records using Royal Mail's normal
      UI, or retain the agreed real products. No live acceptance import was executed here.
