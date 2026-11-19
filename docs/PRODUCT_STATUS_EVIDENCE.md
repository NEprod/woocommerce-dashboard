# Product-bound Shopify status evidence

This correction changes local status/comparison presentation only. It does not
change Shopify requests, approvals, queue ownership, execution budgets, catalogue
JSON, BOMs, inventory or database schema.

## Mapping dependencies

Saved catalogue views retain a product-specific context. Its remote collection
and attribute keys come from that product's persisted comparison, including
missing resources. The global context now covers policy, registry and connection
configuration, not every remote mapping in the catalogue.

Creating an unrelated collection or Recipient term no longer invalidates other
products. Changing, deleting or creating a mapping used by a product still
invalidates that product. Both page reads and the existing Saved Catalogue Views
task use the same batched local dependency calculation; neither contacts Shopify
or walks product folders to do this. Existing product-scoped comparison
invalidation remains unchanged.

An unmatched queue revision is not a field difference. Retained products show
**Status refresh pending** until current evidence establishes equality or an
actionable difference. A current matching comparison can supersede old queue
revision metadata for presentation, without changing the queue's write authority.
Genuine current differences still show **Update Available**. Recovery, processing
and prerequisite failures retain their existing precedence. Ten-minute freshness
still expires without fabricating a new comparison timestamp or field difference.

## Packed dimensions

The shared simple-product final readback can run after packed dimensions were
already acknowledged and independently verified. Its derived comparison now uses
that exact verified native receipt instead of recopying its pre-creation
`create_stage` plan. The original signed plan and historical receipt are unchanged.

The substitution requires the same product, source, SKU, store, configuration,
inventory item, exact original native plan and matching observed target, plus a
verified identity and receipt. It cannot replace a newer comparison, a changed
target, or uncertain/failed proof. Legacy pre-create summaries can use the same
strict evidence selection without writing to the database on page reads. The
native receipt's own verification timestamp remains authoritative for freshness.

## Upgrade and acceptance

No Shopify resync, migration or data reset is required. Old saved views use the
previous context format and conservatively show refresh-pending until the existing
Saved Catalogue Views task projects them locally. A successful manual verification
continues to refresh only its affected product immediately, even if that task is
disabled. This upgrade does not enable or reconfigure any scheduled task.

After manually updating the development image, inspect the seven reported
products: 3DCO-0001, ADOC-0001, HERO-0001, 16bit-0001, 16bit-0002, CCCC-0001 and
CCCC-0002. A fresh complete match should show Up to Date; an expired matching
check should say Last check matched, not Update Available. A genuine pending
field change must remain visible. Do not confirm a product sync merely to refresh
a stale badge. Production and hourly publishing remain untouched by this release.

Local regression coverage uses synthetic simple/variable products, isolated
databases, both publishers, unchanged/partial updates, shared dependencies,
creation with packed measurements, rejected/uncertain writes and legacy evidence.
It is not a substitute for the user's subsequent real development-store acceptance.

## Release validation

- Final focused status, native, manual/hourly, checkpoint and continuation run:
  **168 passed**, including 26 new product-status cases.
- Additional navigation, reconciliation-reporting, checkpoint and sale-projection
  batch: **81 passed** (overlaps the focused run).
- Broader Shopify/catalogue/scheduler regression run: **1,051 passed, 14 failed**.
  Every failed node was rerun against unchanged `e54780e` in a separate temporary
  baseline checkout and failed there too. The suite is not wholly green; these
  pre-existing failures were not hidden or changed as part of this correction.

The 14 baseline failures cover two historical recovery assertions; two disabled
variable-native completion assertions; a mocked transport clock exhaustion;
a detached cached ORM job in the legacy-recovery restart test; two migration
downgrade expectations; three old Draft/corrupt-plan workspace expectations;
and three taxonomy assignment/clear/scope expectations. They are recorded by:

```
test_shopify_acceptance_corrections::test_failed_field_verification_retains_media_and_id_then_recovers_without_write
test_shopify_acceptance_corrections::test_missing_media_receipt_recovery_reviews_fresh_local_digest_not_old_formatter
test_shopify_dimension_sync::test_disabled_variable_native_never_claims_complete_operation [both layouts]
test_shopify_discovery::test_response_byte_and_time_limits
test_shopify_legacy_availability_recovery::test_pending_description_survives_availability_restart_and_then_syncs_separately
test_shopify_orders::test_nonempty_orders_downgrade_requires_approval
test_shopify_product_ux::test_live_intent_remains_blocked_without_mutating_source
test_shopify_products_workspace::test_unknown_is_not_proof_of_remote_absence
test_shopify_products_workspace::test_bad_retained_plan_never_breaks_product_list
test_shopify_publication_sales::test_migration_fresh_upgrade_uniqueness_and_restart
test_shopify_taxonomy_batch::test_storefront_membership_spans_folders_and_missing_assignment_stays_none
test_shopify_taxonomy_lifecycle::test_informational_first_use_multiple_terms_and_explicit_clear
test_shopify_taxonomy_products::test_scope_blockers_keep_read_only_comparison_available
```
