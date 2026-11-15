# Production & Operations — Inventory M1

Open **Operations → Production & Operations** as an administrator. On a phone, use **More** to find the same entry. The workshop data is TLC-local; there is no Shopify or order-picking integration. Catalogue JSON is unchanged.

## Set up the workshop

1. Create locations, such as **Rack A**, **Rack A / Bin 01**, and **Finished parts**. Stock is counted in the exact chosen location, not also in its parent.
2. Add a material (g/kg, ml/l, mm/m, sheets or pieces), a purchased component, or a manufactured component. Enter one permanent internal barcode per record; letters, digits and hyphens are allowed (1–24 characters; case is normalised). An archived record keeps its code. The stock unit cannot be changed once a record exists.
3. Convert purchase packs by specifying their quantity and measuring unit. For example, a 1 kg pack for a material held in grams allows a receipt of one pack to add 1,000 g. For sheets, record length, width and thickness in mm. Pieces must be whole; sheet fractions are permitted, e.g. 0.5 sheet.
4. A manufactured component must have one to 30 distinct raw materials and expected good pieces per batch. Include intended purge, supports and waste in the recipe. Set up a cost basis for every source material before completing production; use a purchase pack price, priced receipt or priced opening stock.
5. **Opening Stock** is for the first recorded stock at an item/location pair. Saving or editing a record never adds stock. To correct an existing quantity use **Stock Check**.

## Record and review

- **Receive Stock** accepts stock units or packs for materials and purchased components. Enter the total receipt price in GBP if known; otherwise the configured pack price is used as an estimate, if available. Select a current or previously used shelf, then scan its barcode, or scan any active physical location directly. A location selection alone never enables confirmation. **Keep stock unassigned** records the receipt without a physical destination only when you explicitly tick it.
- **Complete Production** scales the recipe for the actual good output as a guide, but requires actual consumption of each listed material. Choose the source location for each material; choose one destination for the finished components. Check the review for before/after counts and actual cost. All changes post together; shortages, missing source inputs or stale previews do not post any part of the batch.
- **Move Stock** posts equal and opposite movements in one transaction. To allocate Unassigned stock, choose **Unassigned — built-in** as the source and scan the physical destination. **Stock Check** displays the expected balance, physical count and difference. A mismatch needs a reason; a matching check does not move stock.
- **Stock History** shows each receipt and movement ID, with references, operator, costs, and links between original and reversing transactions. **Reverse this transaction** creates a new compensating receipt; it cannot remove stock that is no longer available. Never repair counts by editing SQLite. A completed transaction's original receipt remains immutable.

Storage uses an inventory-only additive SQLite migration and an append-only stock ledger. Amounts and GBP totals are recorded to six decimal places without binary floating-point balance arithmetic. Current raw/purchased unit costs use a weighted average of costed receipts/opening stock that have not been reversed, falling back to the configured pack price when needed. It is not FIFO or a per-location valuation. Production uses the current costing basis and stores the resulting actual batch cost and cost per good component; later price changes do not reprice a batch. A production ingredient can have one source location per batch in M1; move stock first if it is split across locations. Nested component recipes, shop-floor integrations, reservations and picking are not included.

## Legacy locations named Unassigned

The upgrade only adds a reserved, non-scannable ledger destination. Existing locations, barcodes, balances and history are not adopted, merged or renamed based on their display name. Check the existing location's code and **Stock held here** before deciding what to do. If those balances are genuinely unlocated, use **Move Stock** once per item: choose the exact legacy location as source, choose **Unassigned — built-in** as destination, enter the applicable quantity and a reference, then review and confirm. This records an ordinary auditable transfer and leaves the old history in place. If the location is physical, leave it alone or rename it to reflect where it is. New items, staff accounts and storage mounts do not need to be recreated.

## Labels and phones

Choose **Print barcode label** from an item or location, pick a width, then print at actual size. The genuine Code 128 and QR symbols carry the same code. Print at 100% scaling and try a test label before printing a set.

Open **Scan / Look up** or use a stock form's **Scan or enter a barcode** control. Camera scanning requires a trusted HTTPS dashboard origin, camera permission and native browser `BarcodeDetector` support. QR is preferred; Code 128 is accepted when the browser can decode it. Not all phone browsers provide that API. Without it, choose by name, type the label code, or use a keyboard-style scanner. The camera reads frames locally, stops after decoding or timing out, and never submits a stock transaction. If you use the unencrypted Unraid address, expect manual lookup rather than the camera.

## Deploy and accept

1. Before a manual update, take a consistent backup of the mounted TLCDashboard appdata (stop the container for a filesystem copy or use the dashboard's existing SQLite backup method). Leave mounts, configuration and commerce settings unchanged. Install the verified immutable Docker tag for this revision; startup applies the additive inventory migration. There is no manual SQL step. To roll back the app and database together, restore the paired pre-upgrade backup with the old image; that loses workshop changes made after the backup. Do not downgrade the live inventory tables.
2. Prefer a disposable dashboard/AppData, or use clearly named workshop-only test records. Set up a shelf and a finished bin. Create two materials with prices (e.g. 1 kg of filament at £19.99 and 5 sheets at £12.50), one purchased item with a 50-piece pack, and one manufactured component with a two-material recipe. Receive one pack each and verify quantities and prices. Print one label; scan it in a supported mobile browser and check manual entry too.
3. Complete a batch of three components with 32.75 g filament and 0.5 sheet actually used, even if the recipe estimate differs. Verify actual cost £1.904673, three good pieces in the destination, and both source deductions on the receipt. Refresh or repeat the same confirmation; it must not create another receipt.
4. Move one component to the shelf. Count it as zero with a test reason, then reverse the count to restore it. Try an oversized transfer and confirm it is blocked. Reopen Stock History and check the original and reversal both remain. On a disposable instance, restart and verify balances and history remain. Do not test stock actions in Shopify.
