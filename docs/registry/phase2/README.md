# Phase 2 — Sales & Billing

**Goal:** Deliver sale entry with GST-inclusive billing (built on the Phase 1 catalog and inventory) and customer credit (khata) tracking.

**Deliverables:**
- Sales entry & GST-inclusive billing
- Customer credit (khata) tracking
- Field-navigable form entry primitive (`FormController`) — added at plan revision 4 (`project-update-protocol.md` Area 4, 2026-08-03)

**DoD Conditions:** See [project-plan.md Phase 2 DoD Conditions](../../../framework/project/project-plan.md)
Run `bash framework/harness/phase-check.sh` to verify.

**Features:**
- [feature1-sales-billing-gst](feature1-sales-billing-gst/tasks.md) — DLVR-2-01; closes PD-2-SD005 groundwork (transaction to test); its screen-wiring task (P2-F1-T5) consumes feature4's `FormController`
- [feature2-customer-credit-khata](feature2-customer-credit-khata/tasks.md) — DLVR-2-02; its screen-wiring task (P2-F2-T5) consumes feature4's `FormController`
- [feature3-cross-cutting-closure](feature3-cross-cutting-closure/tasks.md) — closes PD-2-SD004, PD-2-SD005, architect review gate (PD-2-004), phase demo (PD-2-DEMO)
- [feature4-form-navigation](feature4-form-navigation/tasks.md) — DLVR-2-03; in-house `FormController`, no new dependency (ARCH-U04)
