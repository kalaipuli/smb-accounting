# Project Dashboard

> Phase gate status board. Updated after every phase transition.
> Full phase plan (deliverables, milestones, DoD conditions): [project-plan.md](../../framework/project/project-plan.md)
> To verify phase DoD conditions: `bash framework/harness/phase-check.sh`

## Phase Status

| Phase | Goal | Status | DoD verified | Sign-off | Tracker |
|-------|------|--------|-------------|---------|---------|
| Phase 0 — Foundation | Harness init, architecture, project skeleton | ✅ Complete | Pass | 2026-08-01 | [phase0/](phase0/) |
| Phase 1 — Core Data & Application Shell | Curses navigation shell, product catalog/pricing/discounts, inventory tracking | ✅ Complete | Pass | 2026-08-02 | [phase1/](phase1/) |
| Phase 2 — Sales & Billing | Sale entry with GST-inclusive billing and customer credit (khata) tracking | ✅ Complete | Pass | 2026-08-09 | [phase2/](phase2/) |
| Phase 3 — Reporting | P&L reporting across daily/monthly/quarterly/annual periods | ✅ Complete | Pass | 2026-08-11 | [phase3/tasks.md](phase3/tasks.md) |

Status values: 🔲 Not started · 🔄 In progress · ✅ Complete · 🚫 Blocked

## Current Phase

**Phase 3 — Reporting** — In progress. The full P2 boundary revalidation per `plan-protocol.md` §P2 is **complete: 2026-08-09, plan revision 5**, PM/PO-confirmed (phase3-boundary-revalidation space, actions 4–8). All seven drain steps walked with a per-step verdict; both Cross-Cutting Consumption Obligations drained to `satisfied`; Phase 3 stamped `Planned under: baseline v1 · plan revision 5`. Step-8 outcome: phase set confirmed, deliverable set restructured — one deliverable added (`DLVR-3-02`, product cost capture and per-line COGS snapshot, sequenced before `DLVR-3-01`, because no cost column exists anywhere in the schema although the plan and `architecture.md` §3 Flow 2 both require cost of goods).

**Phase 3 gate change — plan revision 6 (2026-08-09, Group 6 PM/PO ruling).** The Phase 3 DoD Conditions table gained one `[Computational]` row, `PD-3-COV`: unit-test line coverage for `src/` overall must be at or above the 2026-08-09 baseline of **92%** — `poetry run pytest tests/unit/ -q --cov=src --cov-fail-under=92`, owner **backend-developer**, status Pending. The number asserts no regression against the tree as measured that day (463 unit tests, 1604 statements, 133 missed), not a round aspiration. Ruling issued against test-manager's Phase 3 Test Strategy ([phase3/README.md](phase3/README.md) §3) and the Phase 1 precedent in which `src/ui/catalog.py` shipped at 45% under a stated ≥80% target because no sensor read it. **The part with no sensor:** the PM/PO accepted a *second* floor — ≥90% line coverage for every module Phase 3 creates or modifies — and `pytest-cov` has no per-module fail-under, so `PD-3-COV` asserts the whole-project total only. A large under-tested new module can pass it by riding the existing average. That floor is registered as sensor debt **`SD-006`** (Open, owner backend-developer, `framework/harness/sensor-debt-registry.md`), to be configured as a `.claude/hooks/stop.sh` gate when Phase 3's task structure is written and in all cases before Phase 3 sign-off. The plan is at **revision 7** (revision 7 being a two-cell `PD-0-003`/`PD-0-004` sensor-predicate repair, no gate change).

Still outstanding before Phase 3 decomposition: `phase-start-protocol.md` Steps 1–3 (feature breakdown and task structure — a separate Phase 3 planning command), the P&L/cost-capture ADR that must be `Accepted` before the first task line, and the harness leg of the boundary (`harness-protocol.md` Steps 2–5 have not been run at either the Phase 1→2 or Phase 2→3 boundary — 18 of 21 Document Currency rows in `framework/harness/version-manifest.md` read `Stale`). **Ruled by the user, 2026-08-09, verbatim: "Run harness-protocol Steps 2–5 first"** — it runs as its own command before Phase 3 decomposition begins. Planning gates and carried-forward decisions: [phase3/README.md](phase3/README.md).

**Phase 3 planning closed — 2026-08-10, `feature/phase3-planning`.** The full planning arc across `phase3-start` → `phase3-structure` → `phase3-closure` is done: ADR-008 (P&L reporting period semantics) and ADR-009 (product cost capture, per-line COGS snapshot) both `Accepted`; `docs/design/style-guide.md` and the three-page reporting UX proposal (Period-Type Menu, Period Selector, Report Summary, full loading/empty/error/cost-incomplete state coverage) authored by ux-designer; the task registry written — `docs/registry/phase3/tasks.md` (PHFEAT-01/PHFEAT-02) plus 6-task Feature 1 and 10-task Feature 2 files, 16 task rows total after this closure's two additions. This closure's own test-manager review cross-referenced every T-1..T-13 Test Strategy row and all six period-boundary cases against the task registry: all PASS except one real gap (T-9's Integration half — no task row for the pre-migration/unrecoverable-cost fixture, open item owner project-manager) and one deliberate absence (T-13, manual demo, on the record). Two coverage gaps found and closed with new task rows: `P3-F2-T10` (raise `src/config.py` to ≥90%, in-floor per README §3's "unless modified" clause, triggered by ADR-008 Decision (b)'s new `get_reporting_tz()`) and `P3-F1-T6` (regression test locking ADR-009's prohibition on rendering `unit_cost` in `src/ui/sales.py`'s bill/receipt view). Sensor debt `SD-006` (per-module ≥90% coverage floor) is `Resolved` — `.claude/hooks/stop.sh` gate 11 configured, verified against both the real tree (11/11 gates pass, all Phase-3 modules carved out under "unless modified") and a hypothetical modified-and-under-covered module (gate fails as designed).

Next gate: `DLVR-3-02` — product cost field and per-line COGS snapshot implemented, migrated and unit-tested (it precedes P&L aggregation; aggregation for all four periods is the gate after it).
Run `bash framework/harness/phase-check.sh` before declaring phase complete.

**Phase 3 complete — 2026-08-11.** Feature 1 (`phase3-feature1-cost-capture`, DLVR-3-02) and Feature 2 (`phase3-feature2-pl-reporting`, DLVR-3-01) both implemented and closed, all 16 task rows Done; an ad-hoc fix (`phase3-catalog-list-cost-display`) added cost display to the catalog list screen. Both remaining Inferential DoD conditions closed in the `phase3-signoff` space: `PD-3-004` (architect review of the full Phase 3 diff against ADR-008/ADR-009, no open Critical or High findings) and `PD-3-DEMO` (owner ran the app and confirmed acceptance verbatim "demo is fine"). `phase-check.sh` re-verified: all 18 Computational conditions pass across every phase; all 8 Inferential conditions across Phases 0–3 now `Resolved`/signed off. Phase 3 — and the full MVP scope (`scope.md` SCOPE-01..04) — is complete; `project-plan.md` defines no Phase 4.

## Inferential Sensor Coverage

Inferential sensors have no automated check — coverage depends on the named persona being invoked each phase. A stale `last-confirmed` date is the signal that coverage has lapsed.

Update this table after every gate review and at every phase sign-off.

| Persona | Concern covered | Required from phase | Last confirmed | Confirmed by |
|---------|----------------|--------------------|--------------:|-------------|
| architect | Architecture rules, ADR compliance | Phase 1 | 2026-08-10 | `phase3-start` actions 2, 15, 17 — ADR-008/ADR-009 authored and revised, both `Accepted` |
| security-reviewer | Security anti-patterns, threat model | Phase 1 | 2026-08-02 | Feature 3 Tier 2 gate |
| database-administrator | Schema safety, migration review | Phase 1 | — | |
| ux-designer | UX proposal completeness, style-guide adherence | Phase 3 | 2026-08-10 | `phase3-structure` action 1 — reporting UX proposal (3 pages, full state coverage) + `docs/design/style-guide.md` |
| test-manager | Test strategy / task-registry coverage review | Phase 3 | 2026-08-10 | `phase3-closure` actions 1, 3, 4 — T-N/period-boundary/module-set cross-reference; `src/config.py` and `unit_cost`-render gaps closed with new task rows |
| backend-developer | Coverage-gate / sensor configuration correctness | Phase 3 | 2026-08-10 | `phase3-closure` action 2 — SD-006 `stop.sh` gate 11 configured, verified against real and hypothetical trees |
| project-manager | Planning-gate confirmation with user (PM/PO) | Phase 3 | 2026-08-10 | `phase3-start`/`phase3-structure` ADR and feature-breakdown user-confirmation gates |
