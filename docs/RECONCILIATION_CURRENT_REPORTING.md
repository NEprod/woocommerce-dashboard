# Current reconciliation reporting and 19 MB product originals

Reconciliation banners are a read-only projection of saved product findings and
retained task history. A later complete product comparison can supersede an old
read failure, including after its ten-minute freshness expires. Expiry still
changes the product badge to “Last check matched”; it is not a new product
difference. Uncertain identities and writes are never cleared by this reporting.

Findings from products outside a newer narrow reconciliation scope remain
visible. Unattributed execution/connection failures remain separately labelled
until a completed same-scope (or full-scope) inspection supersedes them. Original
task reports, errors and timestamps are never rewritten or deleted.

The Publishing Queue labels the original trigger as “Queued by scan” and shows
the successful manual Review & Sync receipt separately from the queue receipt.
A matching saved product comparison does not adopt an old scan revision or grant
new publication authority. Existing pending jobs and their receipts are unchanged.

## Product-image limit

`shopify_drafts.MAX_IMAGE = 19_000_000` bytes is the single bound shared by product
gallery preflight, staged-batch validation and publishing revalidation. Existing
PNG/JPEG/WebP, 20 megapixel, 100:1 aspect-ratio, path confinement and original-byte
identity checks remain. No original is resized, compressed or replaced. Scanner
image discovery does not upload images; Shopify preparation validates the originals.
Known size/dimension findings are product-readiness results, not failed Shopify
requests. They still block that product's publication.

The independent batch limits remain twenty originals and 30 MiB per acknowledged
slice. Files are read, verified and uploaded one at a time; the gallery retains
metadata/hashes, not its combined binary data. The Requests multipart encoder may
temporarily copy one original, but never encodes a whole gallery together.

Product binaries go directly from the container to the validated Shopify staged
HTTPS target. The Flask inbound body limit, browser reverse proxy, Tailscale HTTPS
and Cloudflare customer-photo Worker/R2 are not on that path. The staged target's
own policy/expiry, network conditions and Shopify processing still apply; no live
upload is claimed by offline tests. Existing upload timeouts are unchanged.

Shopify API requirements: https://shopify.dev/docs/apps/build/product-merchandising/products-and-collections/manage-media
(20 MB, 20 MP, aspect ratio 100:1; checked 18 November 2026).

Customer-photo limits, upload signing, private storage and retrieval are untouched.
No scheduler/worker budgets, ownership, signed approvals, checkpoint continuation,
Shopify mutation selection or automatic-publishing settings are changed.

## Acceptance after the merchant's manual development update

Review CCCC-0007 normally. Its unchanged `cccc_snowman_01.png` (11,620,469 bytes)
must pass local image preflight. Review the existing remote identity/absence before
approving any create or update. This release preparation does not upload Snowman,
change products or run reconciliation on the deployment.
