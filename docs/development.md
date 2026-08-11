# Development Guide

Developer-facing notes for working on SMB Account Software. For what the
app does and how to run it as an end user, see `README.md` at the repo root.

---

## Local Development Setup

```bash
# Install dependencies
poetry install

# Run the app
poetry run python -m src.main

# Format check
poetry run ruff format --check src/ tests/

# Lint
poetry run ruff check src/ tests/

# Type check
poetry run mypy src/ --strict

# Unit tests
poetry run pytest tests/unit/ -q --tb=short

# Integration tests
poetry run pytest tests/integration/ -q --tb=short
```

Full Tier-1 Definition of Done gate: `framework/project/project-context.md`
§6.

---

## Audit Trail Test

`tests/integration/test_audit_trail.py` is an **internal correctness
check**, not a user-facing feature. It exists to satisfy SD-002
(`framework/harness/sensor-debt-registry.md`) — the requirement that every
state-mutating operation on a business entity leaves an audit trail
(`created_at`/`updated_at` columns populated and, on update, refreshed).

Run it directly:

```bash
poetry run pytest tests/integration -k audit -q --tb=short
```

**What it currently covers (Phase 1):**
- Catalog product mutations — `create_product`, `update_product`
  (`src/services/catalog.py`)
- Discount rule mutations — `create_discount`, `update_discount`
  (`src/services/discounts.py`)
- Inventory stock mutations — `set_stock` (manual absolute-set),
  `adjust_stock` (relative delta / restock path), `decrement_stock`
  (sale-entry decrement hook) (`src/services/inventory.py`)

Each covered mutation is asserted to populate `created_at` on insert,
preserve `created_at` unchanged on update, and set/refresh `updated_at` on
every write.

**What it does not cover:** Phase 2 sales and credit/khata mutation paths
(DLVR-2-01/DLVR-2-02) — that code does not exist yet. This is tracked as a
follow-up in the SD-002 registry row's `Notes` and must be closed by
extending this same test file once that code is written, before the Phase
2 sign-off boundary.

If you add a new state-mutating function to `src/services/`, add a
corresponding audit-field test to this file — that is what keeps SD-002
closed as the codebase grows.
