# Phase 1 — Feature 4 — Inventory Management

**Cites:** DLVR-1-03 — "Inventory management — stock level tracking, decremented by sales. Reached through DLVR-1-01." (cites SCOPE-02, Cross-Cutting Consumption Obligation)

| ID | Status | Task | Description | Agent | Depends On | DoD / Sensor |
|----|--------|------|-------------|-------|------------|--------------|
| P1-F4-T1 | Done | Implement stock-level tracking model | `src/services/inventory.py` — stock level read/adjust against `inventory_stock` schema from P1-F1-T1, tied to catalog product IDs from Feature 3 | backend-developer | P1-F1-T1 | [Computational] `poetry run pytest tests/unit/services/test_inventory.py -q --tb=short && poetry run mypy src/services/inventory.py --strict` |
| P1-F4-T2 | Done | Implement inventory decrement hook | Expose a `decrement_stock(product_id, qty)` function/interface that Phase 2's sale-entry transaction (DLVR-2-01) will call — no sale logic itself, just the inventory-side contract and its own mutation path | backend-developer | P1-F4-T1 | [Computational] `poetry run pytest tests/unit/services/test_inventory.py -q --tb=short -k decrement` |
| P1-F4-T3 | Done | Wire inventory curses screens into main menu | `src/ui/inventory.py` — stock-level view/adjust screens reachable from DLVR-1-01's main menu, following its key-binding convention | backend-developer | P1-F2-T1, P1-F4-T2 | [Computational] `poetry run pytest tests/unit/ui -q --tb=short -k inventory` |
| P1-F4-T4 | Done | Unit tests for inventory module | Full pytest coverage of stock read/adjust/decrement paths, including negative-stock guard | backend-developer | P1-F4-T2 | [Computational] `poetry run pytest tests/unit/services/test_inventory.py -q --tb=short` |

### Gate Evidence

#### P1-F4-T1

Gate 1: `grep -rn "class StockLevel" src/ --include="*.py"` → zero matches before creation; `src/services/inventory.py` is the sole definition after.
Gate 2: Governing: ADR-002 (GST slabs, not directly applicable) — no new architectural choice; schema already fixed by P1-F1-T1's migration (`src/db/migrations/0001_up.sql`). Scope gap: none — `inventory_stock` is this task's own table. Silence gap: none — column names read directly from the migration file before writing any query. Conflict: none. Consumer gap: `src/ui/inventory.py` (P1-F4-T3) is the only consumer, addressed in this same turn.
Gate 3: No shared client/resource beyond the `sqlite3.Connection` passed in by the caller (same pattern as `src/services/catalog.py`) — no per-call construction.
Gate 4: `grep -rn "deprecated" src/services/inventory.py` → zero matches.
Gate 5: n/a — not a fix task.

#### P1-F4-T2

Gate 1: `grep -rn "def decrement_stock" src/ --include="*.py"` → single definition in `src/services/inventory.py`.
Gate 2: Governing: this feature's Expectations ("`decrement_stock` must be a pure inventory-side mutation — explicitly out of scope: any sale/transaction commit logic"). No scope/silence/conflict/consumer gaps — Phase 2's DLVR-2-01 is the future caller, not implemented here.
Gate 3: n/a — reuses the same connection-passing pattern as P1-F4-T1.
Gate 4: `grep -rn "deprecated" src/services/inventory.py` → zero matches.
Gate 5: n/a — not a fix task.

#### P1-F4-T3

Gate 1: `grep -rn "class InventoryController" src/ --include="*.py"` → single definition in `src/ui/inventory.py`.
Gate 2: Governing: this feature's Expectations ("follow `src/ui/catalog.py`'s established screen/menu-wiring pattern") — followed directly (controller/dispatch/render split, key-binding reuse from `src/ui/keybindings.py`). No gaps found.
Gate 3: `src/ui/main_menu.py` wires `run_inventory_screen` into the existing route dispatch — no new shared resource.
Gate 4: `grep -rn "deprecated" src/ui/inventory.py` → zero matches.
Gate 5: n/a — not a fix task.

#### P1-F4-T4

Gate 1: n/a — test file, not a schema/type addition.
Gate 2: n/a — no new architectural decision.
Gate 3: n/a.
Gate 4: n/a.
Gate 5: n/a — not a fix task.

## Notes

- P1-F4-T2 (decrement hook) intentionally stops short of the transactional sale+decrement commit — that belongs to DLVR-2-01 (Phase 2, out of scope for this plan). Phase 2 planning must wire DLVR-2-01's transaction to call this hook rather than re-implement stock mutation.
- P1-F4-T1 and P1-F4-T2 are the mutation-code tasks that Feature 5's SD-002 audit-trail integration test (P1-F5-T2) depends on, alongside Feature 3's catalog mutations.
