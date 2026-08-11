# Phase 1 — Core Data & Application Shell

**Goal:** Deliver the curses navigation shell and the two foundation data modules — product pricing catalog/discounts and inventory — that everything else builds on.

**Deliverables:**
- Application shell — curses main menu with keyboard navigation
- Product pricing catalog and discount management
- Inventory management — stock level tracking

**DoD Conditions:** See [project-plan.md Phase 1 DoD Conditions](../../../framework/project/project-plan.md)
Run `bash framework/harness/phase-check.sh` to verify.

**Features:**
- [feature1-database-foundation](feature1-database-foundation/tasks.md) — shared schema/migrations; closes SD-003
- [feature2-application-shell](feature2-application-shell/tasks.md) — DLVR-1-01
- [feature3-catalog-pricing](feature3-catalog-pricing/tasks.md) — DLVR-1-02; closes PD-1-GST
- [feature4-inventory-management](feature4-inventory-management/tasks.md) — DLVR-1-03
- [feature5-cross-cutting-closure](feature5-cross-cutting-closure/tasks.md) — closes PD-1-BACKUP, SD-002 (Phase 1 scope), architect review gate (PD-1-004), phase demo (PD-1-DEMO)
