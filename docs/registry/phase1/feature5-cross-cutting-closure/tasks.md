# Phase 1 — Feature 5 — Cross-Cutting Phase 1 Closure

Tasks that close Phase 1 DoD conditions and sensor debts that are not tied to a
single deliverable — backup/recovery design, the audit-trail sensor debt, the
architect review gate, and the phase demo.

**Cites:** PD-1-BACKUP, PD-1-004, PD-1-DEMO (Phase 1 DoD conditions) · SD-002 (sensor debt)

| ID | Status | Task | Description | Agent | Depends On | DoD / Sensor |
|----|--------|------|-------------|-------|------------|--------------|
| P1-F5-T1 | Done | Backup/data-loss recovery mechanism (PD-1-BACKUP closure) | Resolve architecture.md §10 open question: design and implement a simple periodic local backup/export mechanism for the SQLite file (lightweight default per architecture.md §10 recommendation) — document as an ADR in `docs/adr/`, implement the backup routine, update PD-1-BACKUP status in `framework/project/project-plan.md` to `Resolved` once Accepted and implemented | architect | P1-F1-T1 | [Inferential] ADR exists in `docs/adr/`, status `Accepted`; backup routine implemented and manually exercised (backup file produced, restore verified) — no fully automated command substitutes for the design-decision judgment |
| P1-F5-T2 | Done | Audit-trail integration test (SD-002 closure — Phase 1 scope) | Add an integration test file asserting audit fields (e.g. `created_at`/`updated_at` or equivalent) are present on every Phase 1 mutation path: catalog CRUD (P1-F3-T2), discount rules (P1-F3-T3), inventory stock adjust/decrement (P1-F4-T1, P1-F4-T2). Update `framework/harness/sensor-debt-registry.md` SD-002 row: mark `Resolved` for the Phase 1 mutation paths it covers, with a note that DLVR-2-01/DLVR-2-02 (Phase 2 mutation code, not yet written) require a follow-up integration-test extension at the Phase 2 boundary — do not close the debt as fully resolved if it is re-opened by the registry's own resolution-condition wording (see Open item logged in the planning space) | backend-developer | P1-F3-T2, P1-F3-T3, P1-F4-T1, P1-F4-T2 | [Computational] `find tests -iname "*audit*" \| grep -q .` (SD-002's own `check_command`) passes, plus `poetry run pytest tests/integration -k audit -q --tb=short` green |
| P1-F5-T3 | Done | Architect review of Phase 1 deliverables (PD-1-004) | Run `guidelines/architect-review-checklist.md` against DLVR-1-01, DLVR-1-02, DLVR-1-03 and all ADRs produced in this phase (P1-F3-T1, P1-F5-T1); resolve or explicitly defer every Critical/High finding | architect | P1-F2-T3, P1-F3-T5, P1-F4-T4, P1-F5-T1, P1-F5-T2 | [Inferential] Architect review record with zero open Critical/High findings, or each deferred with justification and a named owner |
| P1-F5-T4 | Done | Phase 1 demo and owner sign-off (PD-1-DEMO) | Demonstrate the working curses shell, catalog/pricing/discounts, and inventory modules to the project owner; record explicit acceptance | project-manager | P1-F5-T3 | [Inferential] Owner's explicit confirmation recorded in the planning space / handover log |

### Gate Evidence

#### P1-F5-T1 — Gate Evidence

Gate 1: `grep -rn "class Backup" src/ --include="*.py"` → no matches (no duplicate type). No shared type introduced.
Gate 2: Governing ADR: `docs/adr/ADR-003-backup-recovery.md` (new, `Accepted`). Scope gap: none — ADR covers the only backup/recovery component in the system. Silence gap: architecture.md §10 did not specify trigger mechanism, storage location, or retention count — resolved and stated explicitly in ADR-003 (startup-interval + manual CLI trigger, `<db_path.parent>/backups/`, retention 7). Conflict: none — no other ADR addresses backup/recovery. Consumer gap: only consumer is `src/main.py` at startup (wired) and the shop owner/support via the CLI (`python -m src.db.backup`) — both addressed.
Gate 3: Shared resource check — n/a. `create_backup`/`restore_backup` open their own short-lived `sqlite3.Connection` objects scoped to the backup/restore operation itself (not the application's shared DB connection); this mirrors the existing pattern in `src/db/schema.py`'s `connect()` helper and does not construct the app's shared connection per-request.
Gate 4: `grep -rn "print(" src/db/backup.py` → zero matches after remediation (CLI output routed through structured logging, `_cli_output()`, per anti-patterns.md Code Quality rule) — see Manual Exercise Evidence below for full Tier-1 gate output.
Gate 5: n/a — not a fix task.

**Manual Exercise Evidence (architect, 2026-08-02):**
Against a real schema-backed SQLite file (`init_schema()`-created, same path shape as `SMB_DB_PATH`):
1. `python -m src.db.backup backup` → `Backup created: .../backups/data-20260802T065112542397Z.db`
2. Simulated data loss: `DELETE FROM products` against the live file → 0 rows.
3. `python -m src.db.backup restore <backup_file>` → live file restored; `SELECT` confirmed the original row (`Rice 1kg`, 60, 5) was recovered.
4. `maybe_backup_on_startup()` exercised twice: with a fresh backup present it correctly returned `None` (skip — not stale); after backdating the existing backup's mtime by 2 days it correctly took a new backup (returned a new `Path`, distinct from the stale one).
5. Full Tier-1 DoD gate re-run after remediation: `ruff format --check`, `ruff check`, `mypy --strict`, `pytest tests/unit/` (170 passed), duplicate-class grep (0), `print(` grep (0) — all pass.

#### P1-F5-T2 — Gate Evidence

Gate 1: `grep -rn "class Audit" src/ --include="*.py"` → no matches (no new shared type introduced — this task is test-only plus a registry status update).
Gate 2: Governing guideline: `guidelines/architecture-rules.md` (audit trail rule) via `framework/harness/sensor-debt-registry.md` SD-002. Scope gap: none — this task closes the debt for exactly the mutation paths that exist in Phase 1 (catalog, discounts, inventory), matching SD-002's own resolution-condition scope for those paths. Silence gap: none — the task's own dod already specifies the residual (Phase 2 wording) handling. Conflict: none. Consumer gap: none — no other component reads this test file as a contract.
Gate 3: Shared resource check — n/a. Test-only change using the existing `conn` fixture pattern already established in this file.
Gate 4: `grep -rn "print(" tests/integration/test_audit_trail.py` → zero matches.
Gate 5: n/a — not a fix task.

**Coverage verification evidence (backend-developer, 2026-08-02):**
Re-read current service code (`src/services/catalog.py`, `src/services/discounts.py`, `src/services/inventory.py`) against the existing test file rather than trusting its docstring claim. Found one gap: `src/services/inventory.py`'s `adjust_stock` is a distinct, directly-called mutation path (`src/ui/inventory.py:157`, the manual restock/adjust screen) that was previously only exercised indirectly through `decrement_stock`'s internal call (negative delta only). Added `test_adjust_stock_refreshes_updated_at_audit_field` (positive-delta/restock case) to close the gap. All other Phase 1 mutation paths (catalog create/update, discount create/update, inventory set_stock/decrement_stock) were already directly covered and re-verified against current signatures.
`find tests -iname "*audit*" | grep -q .` → pass. `poetry run pytest tests/integration -k audit -q --tb=short` → 7 passed. Full Tier-1 gate (`ruff format --check`, `ruff check`, `mypy --strict`, `pytest tests/unit/` (170 passed), duplicate-class grep (0 matches), `print(` grep (0 matches)) re-run after the fix — all pass.
`framework/harness/sensor-debt-registry.md` SD-002 row updated: `Status` → `Resolved`, `Date resolved` → 2026-08-02, `Notes` → scoped to Phase 1 mutation paths with the Phase 2 (DLVR-2-01/DLVR-2-02) residual named as a follow-up debt for the Phase 2 boundary, owner backend-developer.

#### P1-F5-T3 — Gate Evidence

Gate 1: `grep -rn "^class " src/ --include="*.py" | awk -F: '{print $NF}' | sort | uniq -d` → no duplicates (no new shared type introduced by this review action itself).
Gate 2: Governing guidelines: `guidelines/architect-review-checklist.md` (Priorities 1-7 run; Priority 8 N/A — no LLM in stack), `guidelines/architecture-rules.md`, `guidelines/anti-patterns.md`. Scope gap: none — checklist applies to the full `src/` tree, not a single component. Silence gap: none — checklist's grep patterns are non-exhaustive examples per its own framing; supplemented with a direct read of `src/db/schema.py` against the Audit Trail rule text (see Finding 1). Conflict: found — `project-plan.md` PD-1-004's literal condition text ("no open Critical or High findings") is narrower than this task's own DoD ("resolve or explicitly defer... with a named owner"); flagged in the PD-1-004 row rather than silently reconciled. Consumer gap: n/a — review action, not a resource-producing task.
Gate 3: Shared resource check — n/a (review action, no new resource introduced).
Gate 4: `grep -rn "noqa\|type: ignore" src/ --include="*.py"` → zero matches (Priority 7 suppressor audit).
Gate 5: n/a — not a fix task (the fix specification for Finding 1 lives in `fixes.md`, itself carrying its own `**Impact:**` field per `development-process.md`'s fix specification format).

**Review Evidence (architect, 2026-08-02):**
Full checklist Priorities 1-7 run against `src/` (Priority 8 skipped — no LLM component). Zero matches on duplicate schema, per-request client construction, unwrapped secrets, sync I/O in async context (N/A, no async in stack), and unjustified suppressors. Priority 4 (ADR coverage): all three Phase 1 ADRs (`ADR-001`, `ADR-002`, `ADR-003`) `Accepted`, each with Context/Decision/Alternatives/Consequences; ADR-002/ADR-003 additionally carry Multi-System Impact Analysis for their cross-cutting changes. DLVR-1-01/02/03 cross-checked directly against their cited tags (`ARCH-U01`, `SCOPE-02`, `SCOPE-03`) and the implementing code — all three verified, no gap; `project-plan.md` DLVR rows corrected from stale `Pending` to `Done` to match the underlying Feature 1-4 task registries. Full Tier-1 DoD gate re-run (`ruff format --check`, `ruff check`, `mypy --strict`, `pytest tests/unit/ tests/integration/`) → all pass, 177 tests green.

One High finding surfaced beyond the checklist's own grep patterns: `products`/`discounts` tables carry no `updated_at` column, so `update_product`/`update_discount` record no timestamp for the update event — a genuine gap against `architecture-rules.md`'s Audit Trail rule that the SD-002 closure (`P1-F5-T2`) did not surface, since its test only asserts `created_at` is preserved unchanged on update. Full finding, fix specification, and impact analysis recorded in `docs/registry/phase1/feature5-cross-cutting-closure/fixes.md` Finding 1 — deferred with named owners (data-engineer for the migration, backend-developer for the service-layer update), must clear before the Phase 1 → Phase 2 boundary. Zero other Critical/High findings.

Verdict: `PD-1-004` satisfied per this task's DoD (deferral with a named owner in lieu of zero open findings) → `framework/project/project-plan.md` PD-1-004 status → `Resolved`, with the deferred finding and the PD-1-004-condition-text/task-DoD wording gap both noted in the row.

#### P1-F5-T4 — Gate Evidence

Gate 1: `grep -rn "^class " src/ --include="*.py" | awk -F: '{print $NF}' | sort | uniq -d` → no duplicates (no new shared type introduced — this task is a demo/sign-off action, not a code change).
Gate 2: Governing guideline: `guidelines/definition-of-done.md` (owner sign-off requirement) via PD-1-DEMO. Scope gap: none. Silence gap: none — the DoD's "explicit confirmation" bar is satisfied per `agent-conduct.md`'s Confirmation Gate protocol (approval issued in a separate response, after the plan/demo was presented). Conflict: none. Consumer gap: none.
Gate 3: Shared resource check — n/a. No resource produced.
Gate 4: n/a — no source files touched.
Gate 5: n/a — not a fix task.

**Sign-off evidence (project-manager, 2026-08-02):** Phase 1 demo content (application shell, catalog/pricing/discounts, inventory, backup/recovery, audit trail, architect review, plus four demo-review redirects: main-menu shortcut labels + real key wiring, backup/audit-trail user and developer docs, edit-form pre-fill, entry-prompt cancel support, and the startup migration-runner crash fix) presented across four re-presentations in the planning space's `## Pending user input`. User responded "ok" to the final (turn-4) re-presentation — explicit acceptance issued in a separate response after the demo was shown, per the Confirmation Gates protocol. PD-1-DEMO → `Resolved`.

Not applicable yet for remaining tasks — `Pending`. Filled per-task immediately before `Pending` → `In Progress`, per `development-process.md` §8.

## Notes

- P1-F5-T1 (PD-1-BACKUP) depends only on the schema existing (P1-F1-T1) — it backs up the SQLite file as a whole, not any one module's data, so it does not need to wait on catalog/inventory implementation.
- P1-F5-T2 closes SD-002 only for the mutation paths that exist in Phase 1 (catalog, discounts, inventory). SD-002's resolution condition as written in `framework/harness/sensor-debt-registry.md` also names DLVR-2-01/DLVR-2-02 — those are Phase 2 deliverables that do not exist yet. This is a wording inconsistency in the debt registry (a "before Phase 1 sign-off" trigger referencing Phase-2-only deliverables), flagged as an Open item in the planning space rather than resolved here, since fixing the registry text is outside this action's scope. Phase 2 planning must add a follow-up SD-002-extension task once DLVR-2-01/DLVR-2-02 mutation code lands.
- P1-F5-T3 is the gate for `PD-1-004` (no open Critical/High architect findings) in `framework/project/project-plan.md`.
- `PD-1-SDR` (no expired sensor debts) and `PD-1-001`/`PD-1-002`/`PD-1-003` (unit test/type-check/lint gates) are verified automatically by `bash framework/harness/phase-check.sh` against the cumulative Phase 1 codebase — no standalone task is needed for them beyond the module-level unit-test tasks already listed in Features 1–4.
