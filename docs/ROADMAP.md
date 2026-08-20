# Roadmap

- **Phase 0 — Secure baseline, documentation, tests and containerisation:** complete baseline scope for version `0.1.0`.
- **Phase 1 — Database ingestion parity and catalogue integrity:** complete in `0.2.0`. Scanner characterization, migration/recovery foundations, operation control/history, catalogue projection/provenance, complete-parent transactions, recoverable marker coordination, lifecycle reconciliation, identity-preserving reconstruction, the metadata contract, and final release verification are complete.
- **Phase 2 — Catalogue management UI:** in progress on `develop`. Milestone 1
  establishes the semantic design system, neutral branding, responsive
  navigation shell, local UI assets, authenticated folder browser, and safe
  placeholder/compatibility routes without changing the scanner or schema.
  Milestone 1.1 established the interim contrast baseline. The approved design
  reconciliation replaces its dark-first styling with the canonical light-first
  system, permanent sidebar/mobile shell, accessible tables, and dedicated dark
  hierarchy/code states without changing application behavior.
  Milestone 2 completes setup-result and shared operation-progress presentation.
  Milestone 3 adds the genuine read-only catalogue-health Dashboard over the
  existing projection and operation records. Milestone 4 replaces the legacy
  flat catalogue table with the genuine collection-grouped Products browser,
  URL-backed supported filters, server-side parent pagination, and lazy
  variation previews.
- **Phase 3 — Read-only WooCommerce mapping and comparison:** planned.
- **Phase 4 — Controlled WooCommerce publishing and updates:** planned.
- **Later — Website/header automation, scheduling, notifications, and business dashboard modules:** planned.

Phase 1 is the current catalogue-integrity release. Future work must continue to treat its scanner contract and database/recovery behavior as protected baselines.

## Phase 2 execution plan

Phase 2 uses sequential approval gates on the long-lived `develop` branch:

1. Design system, neutral branding, navigation shell, local assets, safe routes.
   Milestone 1.1 is a visual correction within this completed scope and is not
   the start of Milestone 2.
2. Setup completion and unified operation-progress presentation foundation.
   **Complete on `develop`.** Setup retains its completion summary and next
   actions; existing scan/update surfaces share a normalized, accessible live
   presentation over the protected process-local runner state.
3. Catalogue health dashboard. **Complete on `develop`.** Genuine projection,
   metadata-completeness, operation, attention, and recent-product data replace
   the placeholder without adding analytics or persistence.
4. Products browser with collection grouping and lazy variation previews.
   **Complete on `develop`.** The browser derives counts, lifecycle, pricing,
   images, provenance, and timestamps from the existing projection. Variable
   detail is fetched only on expansion; no scanner or persistence contract is
   changed.
5. Product detail and metadata provenance experience.
6. Collections browser and collection workflows.
7. Scanner workspace and operation history UI.
8. Settings, accessibility, responsive refinement, and future placeholders.
9. Complete documentation, contract verification, Docker/Unraid testing, and
   the explicitly gated `v0.3.0` release.

Milestones 1–8 publish immutable multi-platform development images plus the
moving `develop` image. They do not update stable or historical image tags.

## Phase 1 execution plan

The approved Phase 1 work remains divided into Milestones 0–10:

0. Verify the Phase 0 baseline, branch, tests, image, and source boundaries.
1. Characterize and protect scanner contracts using isolated fictional fixtures.
2. Add migrations, conservative Phase 0 adoption, persistent backups, and restoration.
3. Add single-process catalogue-operation locking and persistent operation history.
4. Activate Collection → Product → Variation projection, field parity, and portable provenance.
5. Make ordinary ingestion atomic per complete parent product. **Complete.**
6. Add recoverable marker/database orchestration without claiming cross-store atomicity. **Complete.**
7. Reconcile variations and missing/restored products, including shared collection updates. **Complete.**
8. Add identity-preserving reconstruction distinct from intentional full regeneration. **Complete.**
9. Formalize the complete metadata contract, schemas, examples, and in-app guidance. **Complete.**
10. Complete all acceptance, migration, reconstruction, source-boundary, Docker, publication, and documentation checks. **Complete.**

Milestones 4–9 must not publish, replace, or modify any Docker Hub tag. Final publication belongs exclusively to Milestone 10 after all preceding acceptance criteria pass.

Phase 1 production publication must use one multi-platform build result for `neprod/woocommerce-dashboard:phase-1`, `neprod/woocommerce-dashboard:0.2.0`, and `neprod/woocommerce-dashboard:latest`. The shared manifest must include `linux/amd64` and `linux/arm64`. Existing `phase-0` and `0.1.0` tags are immutable and must not be republished. See [Phase 1 Acceptance](PHASE_1_ACCEPTANCE.md).
