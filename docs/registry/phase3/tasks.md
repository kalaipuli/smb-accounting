# Phase 3 — Reporting — Feature Index

Written by project-manager, `framework/spaces/phase3-structure.md` action 3 (`write-phase3-task-structure`),
against the confirmed Phase 3 feature breakdown (`docs/registry/phase3/README.md` § Phase Planning
Decisions, Group D1) and the two `Accepted` ADRs (`ADR-008`, `ADR-009`).

| Tag | Feature | Folder | Status | Tasks | Cites |
|-----|---------|--------|--------|-------|-------|
| PHFEAT-01 | Product Cost Capture & COGS Snapshot | `feature1-product-cost-capture-cogs-snapshot/` | ✅ Done | `feature1-product-cost-capture-cogs-snapshot/tasks.md` | DLVR-3-02 — "Product cost capture and per-line COGS snapshot — a cost/purchase-price field on the product catalog, and a per-line cost snapshot written onto each sale line at sale time so historical P&L cannot restate when a purchase price is later corrected." |
| PHFEAT-02 | P&L Reporting (Period-Type Menu → Period Selector → Report Summary) | `feature2-pl-reporting/` | ✅ Done | `feature2-pl-reporting/tasks.md` | DLVR-3-01 — "P&L reporting — daily/monthly/quarterly/annual profit & loss aggregation and display, sourced from sales (DLVR-2-01) and the per-line cost snapshot delivered by DLVR-3-02. Reached through DLVR-1-01." |

Registered in `framework/project/scope-registry.md`: `PHFEAT-01` (parent tag `DLVR-3-02`), `PHFEAT-02`
(parent tag `DLVR-3-01`).

## Notes

- PHFEAT-02 depends on PHFEAT-01 (consumes the COGS snapshot for `cogs`/`lines_missing_cost`/
  `cost_complete`) per Group D1's confirmed sequencing — mirrors `project-plan.md`'s own DLVR-3-02 →
  DLVR-3-01 ordering (plan revision 5).
- Neither feature carries a `STORY-*` row — Group D3 (route (a), accepted 2026-08-10): both feature
  blocks in `docs/registry/phase3/README.md` § Phase Planning Decisions carry the marker
  `Stories: N/A — user-stories.md has not been populated for this project`. The backfill item is
  tracked in that README's `## Plan Learning` section, drained at the next `plan-protocol.md` P2 run.
