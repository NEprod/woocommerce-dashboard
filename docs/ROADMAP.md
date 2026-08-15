# Roadmap

- **Phase 0 — Secure baseline, documentation, tests and containerisation:** complete baseline scope for version `0.1.0`.
- **Phase 1 — Database ingestion parity and catalogue integrity:** complete in `0.2.0`. Scanner characterization, migration/recovery foundations, operation control/history, catalogue projection/provenance, complete-parent transactions, recoverable marker coordination, lifecycle reconciliation, identity-preserving reconstruction, the metadata contract, and final release verification are complete.
- **Phase 2 — Catalogue management UI:** planned.
- **Phase 3 — Read-only WooCommerce mapping and comparison:** planned.
- **Phase 4 — Controlled WooCommerce publishing and updates:** planned.
- **Later — Website/header automation, scheduling, notifications, and business dashboard modules:** planned.

Phase 1 is the current catalogue-integrity release. Future work must continue to treat its scanner contract and database/recovery behavior as protected baselines.

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
