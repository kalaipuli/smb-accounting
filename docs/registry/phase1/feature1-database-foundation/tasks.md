# Phase 1 — Feature 1 — Database Foundation

Shared SQLite schema and migration tooling that Feature 3 (Catalog & Pricing) and
Feature 4 (Inventory) both build on. Also the landing point for SD-003 closure
(migration reversibility), since the check requires a first real migration file
to exist before it can be configured and verified.

**Cites:** DLVR-1-02, DLVR-1-03 (both depend on a schema existing) · SD-003 (sensor debt)

| ID | Status | Task | Description | Agent | Depends On | DoD / Sensor |
|----|--------|------|-------------|-------|------------|--------------|
| P1-F1-T1 | Done | Design SQLite schema for products, discounts, inventory | Define `products` (name, price, gst_rate), `discounts`, `inventory_stock` tables in `src/db/schema.py`; write first real migration file(s) in `src/db/migrations/` following an `NNNN_up.sql` / `NNNN_down.sql` naming convention (up/down pair per migration) | data-engineer | none | [Computational] `test -f src/db/schema.py && ls src/db/migrations/*_up.sql` — schema file exists and at least one up/down migration pair exists |
| P1-F1-T2 | Done | Configure migration reversibility check in stop.sh (SD-003 closure) | Add a check to `.claude/hooks/stop.sh` enforcing that every `NNNN_up.sql` has a matching `NNNN_down.sql` in `src/db/migrations/`; update `framework/harness/sensor-debt-registry.md` SD-003 row to `Resolved` with date, and `framework/harness/sensor-registry.md` entry from `Debt` to `Active` | data-engineer | P1-F1-T1 | [Computational] `grep -q "migration.*revers\|_up.sql\|_down.sql" .claude/hooks/stop.sh` (SD-003's own `check_command`) passes, plus a manual verification that stop.sh actually fails when an up-migration is added without its down-migration counterpart |
| P1-F1-T3 | Done | Unit tests for schema/migration loading | pytest covering schema creation against a throwaway SQLite file and migration apply/rollback round-trip | tester | P1-F1-T1 | [Computational] `poetry run pytest tests/unit/db -q --tb=short` |

### P1-F1-T2 — Gate Evidence

Not applicable yet — task is `Pending`, not `In Progress`. Gate Evidence Block is filled by database-administrator immediately before moving this task to `In Progress`, per `development-process.md` §8.

## Notes

- SD-003's resolution condition ("once the first real migration file is added ... a reversibility naming convention and check must be configured in stop.sh") is satisfied by the T1 → T2 sequence in this feature: T1 lands the first real migration, T2 configures and verifies the check.
- This feature has no direct `DLVR-*` deliverable of its own — it is infrastructure consumed by Feature 3 and Feature 4.
