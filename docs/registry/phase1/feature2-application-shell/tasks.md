# Phase 1 — Feature 2 — Application Shell

**Cites:** DLVR-1-01 — "Application Shell — curses main menu (Sales / Inventory / Catalog / Reports / Customers) with keyboard navigation (arrows/Tab move, Enter select, Esc/q back) and a fast-access 'record a sale' shortcut" (cites ARCH-U01)

| ID | Status | Task | Description | Agent | Depends On | DoD / Sensor |
|----|--------|------|-------------|-------|------------|--------------|
| P1-F2-T1 | Done | Implement curses main menu shell | Build `src/ui/main_menu.py`: main menu screen listing Sales / Inventory / Catalog / Reports / Customers; arrow/Tab navigation, Enter to select, Esc/q to go back; consistent key-binding module shared by all future screens | backend-developer | none | [Computational] `poetry run ruff check src/ui/ && poetry run mypy src/ui/ --strict && poetry run pytest tests/unit/ui -q --tb=short` |
| P1-F2-T2 | Done | Fast-access "record a sale" shortcut | Add a single-key shortcut from the main menu that jumps directly into the (stubbed, Phase-2-pending) sale-entry flow — stub screen only, no sale logic (Phase 2 scope) | backend-developer | P1-F2-T1 | [Computational] `poetry run pytest tests/unit/ui -q --tb=short -k shortcut` |
| P1-F2-T3 | Done | Unit tests for navigation shell | pytest covering key-binding dispatch (arrows/Tab/Enter/Esc/q) and menu-item routing | backend-developer | P1-F2-T1 | [Computational] `poetry run pytest tests/unit/ui -q --tb=short` |

### P1-F2-T1 — Gate Evidence

Gate 1: `grep -rn "class MenuOption\|class NavAction\|class MainMenuController" src/ --include="*.py"` → each class defined exactly once (`src/ui/main_menu.py`, `src/ui/keybindings.py`) — no duplicates.
Gate 2: Governing ADR: ADR-001 (curses stdlib, Python 3.12, pytest/ruff/mypy toolchain). Scope gap: none — ADR already selects curses as the UI stack this task implements against. Silence gap: none — this task is pure navigation scaffolding, no new architectural decision required. Conflict: none. Consumer gap: none — Feature 3/4 screens are the future consumers and were the reason the key-binding module was factored out as shared (per space Expectations).
Gate 3: n/a — no shared resource (DB client, cache) is constructed by this task; the curses `stdscr` is passed in by `curses.wrapper`, not constructed per call.
Gate 4: `grep -rn "curses\.\(is_term_resized\|nocbreak\)\b" src/ui/` → zero matches; no deprecated curses/Python symbols used.
Gate 5: n/a — not a fix task.

### P1-F2-T2 — Gate Evidence

Gate 1: n/a — no new schema/class introduced by this task beyond `MainMenuController.dispatch`'s shortcut branch and `src/ui/sale_entry.py::render_sale_entry_stub`, neither of which duplicates an existing type.
Gate 2: Governing ADR: ADR-001. No scope/silence/conflict/consumer gaps — task note in this file explicitly scopes the shortcut to UI routing only, deferring transaction logic to DLVR-2-01 (Phase 2).
Gate 3: n/a — no shared resource involved.
Gate 4: n/a — no deprecated symbols introduced.
Gate 5: n/a — not a fix task.

### P1-F2-T3 — Gate Evidence

Gate 1: n/a — test files only, no new production schema/class.
Gate 2: Governing ADR: ADR-001 (pytest as test framework). No gaps.
Gate 3: n/a.
Gate 4: n/a.
Gate 5: n/a — not a fix task.

## Notes

- This feature is the shared navigation shell that Feature 3 (Catalog & Pricing) and Feature 4 (Inventory) screens plug into — see the Cross-Cutting Consumption Obligation row in `framework/project/project-plan.md`. Their screen-wiring tasks (P1-F3-T4, P1-F4-T3) depend on P1-F2-T1.
- "Record a sale" shortcut (P1-F2-T2) is UI scaffolding only; the sale-entry transaction logic it links to belongs to DLVR-2-01 (Phase 2, out of scope for this plan).
