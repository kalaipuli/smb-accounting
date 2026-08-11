# Phase 1 — Feature 3 — Product Catalog & Pricing

**Cites:** DLVR-1-02 — "Product pricing catalog and discount management — add/edit product (name, price, GST rate) and discount rules. Reached through DLVR-1-01." (cites SCOPE-03, Cross-Cutting Consumption Obligation) · PD-1-GST DoD condition

| ID | Status | Task | Description | Agent | Depends On | DoD / Sensor |
|----|--------|------|-------------|-------|------------|--------------|
| P1-F3-T1 | Done | Design GST rate/slabs model (PD-1-GST closure) | Resolve the architecture.md §10 open question: configurable per-product GST rate field (not a hardcoded single rate) — document as an ADR in `docs/adr/` covering slab support (e.g. 0%/5%/12%/18%/28%) and where the rate is stored/applied; update `framework/project/project-plan.md` PD-1-GST status to `Resolved` once the ADR is Accepted | backend-developer | none | [Inferential] ADR exists in `docs/adr/`, status `Accepted`, reviewed by architect — no automated command substitutes for the design-decision judgment; `test -f docs/adr/<adr-file>.md` only confirms existence, not adequacy |
| P1-F3-T2 | Done | Implement product catalog CRUD | `src/services/catalog.py` — add/edit product (name, price, gst_rate) against the schema from P1-F1-T1, applying the GST field design from P1-F3-T1 | backend-developer | P1-F1-T1, P1-F3-T1 | [Computational] `poetry run pytest tests/unit/services/test_catalog.py -q --tb=short && poetry run mypy src/services/catalog.py --strict` |
| P1-F3-T3 | Done | Implement discount rules management | `src/services/discounts.py` — create/edit/apply discount rules against catalog items | backend-developer | P1-F3-T2 | [Computational] `poetry run pytest tests/unit/services/test_discounts.py -q --tb=short` |
| P1-F3-T4 | Done | Wire catalog/discount curses screens into main menu | `src/ui/catalog.py` — screens reachable from DLVR-1-01's main menu, following its key-binding convention (arrows/Tab/Enter/Esc-q) | backend-developer | P1-F2-T1, P1-F3-T3 | [Computational] `poetry run pytest tests/unit/ui -q --tb=short -k catalog` |
| P1-F3-T5 | Done | Unit tests for catalog/pricing/discount logic | Full pytest coverage of catalog CRUD, GST field validation, discount application math | backend-developer | P1-F3-T3 | [Computational] `poetry run pytest tests/unit/services/test_catalog.py tests/unit/services/test_discounts.py -q --tb=short` |

### P1-F3-T2 — Gate Evidence

Gate 1: `grep -rn "class Product" src/ --include="*.py"` → only `src/services/catalog.py` defines it. No duplicate.
Gate 2: Governing ADR: `docs/adr/ADR-002-gst-rate-slabs-design.md` (Accepted). Scope/silence/conflict/consumer checks performed by action 2 (architect) prior to this task starting; catalog.py implements exactly the slab set/storage/enforcement-layer decision in ADR-002 §(a)-(c).
Gate 3: `products` table accessed via `sqlite3.Connection` passed in by the caller (dependency injection from `src/main.py`/tests' fixtures) — not constructed per call inside the service.
Gate 4: `grep -rn "sqlite3.connect(" src/services/catalog.py` → no output; uses `src.db.schema.connect` only via caller injection, no deprecated symbols.
Gate 5: n/a — not a fix task.

### P1-F3-T3 — Gate Evidence

Gate 1: `grep -rn "class Discount" src/ --include="*.py"` → only `src/services/discounts.py` defines it. No duplicate.
Gate 2: Governing ADR: none beyond the `discounts` schema itself (`discount_type IN ('percentage','flat')`, `value >= 0`, created P1-F1-T1) — no new architectural decision required; discount math (percentage/flat) is a direct implementation of that existing schema contract, not a new design choice needing its own ADR.
Gate 3: `discounts` table accessed via the same injected `sqlite3.Connection` as P1-F3-T2 — no new client construction.
Gate 4: `grep -rn "sqlite3.connect(" src/services/discounts.py` → no output.
Gate 5: n/a — not a fix task.

### P1-F3-T4 — Gate Evidence

Gate 1: `grep -rn "class CatalogController" src/ --include="*.py"` → only `src/ui/catalog.py` defines it. No duplicate.
Gate 2: No new architectural decision — reuses `src/ui/keybindings.py`'s existing `NavAction`/`resolve_action` table (per this feature's Expectations: "no new key-binding scheme invented") and the `MainMenuController` pattern already established in `src/ui/main_menu.py` (P1-F2-T3).
Gate 3: `run_catalog_screen(stdscr, conn)` receives its `sqlite3.Connection` from `src/ui/main_menu.py`, which receives it from `src/main.py`'s single connection created at startup — no per-call construction.
Gate 4: `grep -rn "sqlite3.connect(" src/ui/catalog.py` → no output.
Gate 5: n/a — not a fix task.

### P1-F3-T5 — Gate Evidence

Gate 1: n/a — this task adds test coverage only, no new production type.
Gate 2: n/a — no new architectural decision; verifies P1-F3-T1-T4's decisions.
Gate 3: n/a — tests construct their own throwaway `sqlite3.Connection` per `tmp_path` fixture, not a shared resource.
Gate 4: n/a — no deprecated symbols used in test files (`poetry run ruff check tests/` passed).
Gate 5: n/a — not a fix task.

## Notes

- P1-F3-T1 is the task that closes PD-1-GST (Phase 1 DoD condition, owner backend-developer per `framework/project/project-plan.md`). It must complete (ADR Accepted) before P1-F3-T2 can implement the GST field correctly.
- P1-F3-T2 and P1-F3-T3 are the mutation-code tasks that Feature 5's SD-002 audit-trail integration test (P1-F5-T2) depends on.

## Tier 2 Gate

**Result: PASS** — 2026-08-02. Verified by `framework/spaces/phase1-feature3-tier2-gate.md` (actions 1-4, 6-9), aggregated by project-manager (action 5).

| Tier 2 — Feature Done DoD item | Verdict | Evidence |
|---|---|---|
| Integration tests green | **Deferred** — SD-005 | `framework/harness/sensor-debt-registry.md` SD-005: `Status: Open`, resolution condition "Before Phase 2 sign-off — once DLVR-2-01 (sales/billing transaction) exists, replace the stub." Named as a known-deferred, non-blocking item in this gate's command. |
| Cross-layer schema consistency verified (no type declared in more than one canonical location) | **Pass** | Architect (action 1): `Product` defined only in `src/services/catalog.py`, `Discount` only in `src/services/discounts.py`; `src/ui/catalog.py` imports both, redeclares neither. API-schema-vs-frontend-types sub-bullet: n/a (no HTTP API in this stack). |
| All new environment variables documented in `project-context.md` | **Pass — n/a** | Technical-writer (action 3): no new environment variables introduced by Feature 3; `.env.example` unchanged. |
| ADR written for every architectural decision made during this feature | **Pass** | Technical-writer (action 3) + architect (action 1): `docs/adr/ADR-002-gst-rate-slabs-design.md` `Status: Accepted`, matches shipped GST slab implementation (`VALID_GST_RATES` frozenset, write-time enforcement) exactly. |
| API reference entry created or updated for every new or changed endpoint | **Pass — n/a** | Technical-writer (action 3): no HTTP framework import/decorator anywhere in scope; catalog/discount screens are internal curses UI, not an external API surface — determined explicitly, not silently skipped. |
| Architect review checklist run — all findings resolved or explicitly deferred with justification | **Pass** | Architect (action 1): 6/7 checklist priorities clean; one High finding (unhandled `DiscountExceedsPriceError` crash) raised, fixed by backend-developer (action 6, two-layer fix + tests), independently re-verified resolved by security-reviewer (action 8). No open findings. |
| Security review passed — no Critical findings open; any High findings explicitly accepted with justification and tracked for Tier 3 resolution | **Pass** | Security-reviewer (actions 2, 8): `docs/reviews/security-2026-08-01.md` closing verdict `APPROVED`, 0 open Critical/High/Medium. SEV-001 (High, crash path) and SEV-002 (Medium, dependency pinning) both fixed — not merely accepted — and independently re-verified in committed code this gate. |

**Known-deferred, non-blocking:** SD-005 (integration tests, resolves before Phase 2 sign-off), SD-002 (audit trail on mutations, resolves before Phase 1 sign-off — tracked separately, not a Feature 3 Tier 2 blocker).

**Follow-up tracked, non-blocking:** action 10 (`raise-catalog-ui-coverage-to-target`, owner backend-developer, pending) — `src/ui/catalog.py` unit coverage measured at 45% against the test-manager persona's ≥80% target; a numeric coverage floor was never a named Tier 1/Tier 2 DoD item for Feature 3, so it does not block this gate.
