# Phase 3 — Feature 1 — Product Cost Capture & COGS Snapshot

Adds a nullable purchase-price column to `products` and a per-line COGS snapshot on
`sale_line_items`, written at sale time, so a later purchase-price correction cannot restate
historical P&L. No user-facing report is produced by this feature — it is the data prerequisite
`PHFEAT-02` (P&L Reporting) consumes.

**Cites:** PHFEAT-01 — "Product cost capture and per-line COGS snapshot — a cost/purchase-price
field on the product catalog, and a per-line cost snapshot written onto each sale line at sale time
so historical P&L cannot restate when a purchase price is later corrected." (parent DLVR-3-02)

Governing ADR: `docs/adr/ADR-009-product-cost-capture-cogs-snapshot.md` — `Status: Accepted`
(2026-08-10, authored and accepted before this task list, `framework/spaces/phase3-start.md` action 3).
No ADR-authoring task appears in this feature's list — the ADR predates this planning run.

| ID | Status | Task | Description | Agent | Depends On | Cites |
|----|--------|------|-------------|-------|------------|-------|
| P3-F1-T1 | ✅ Done | Add `products.cost` and `sale_line_items.unit_cost` columns — schema + migration `0005` | `src/db/schema.py` `SCHEMA_STATEMENTS` plus `src/db/migrations/0005_up.sql` / `0005_down.sql`, per ADR-009 Decision (a) (`products.cost NUMERIC CHECK (cost IS NULL OR cost >= 0)`, nullable, no default) and Decision (b) (`sale_line_items.unit_cost NUMERIC CHECK (unit_cost IS NULL OR unit_cost >= 0)`) and Decision (d) (migration discipline per ADR-004: products column added before sale_line_items column in `0005_up.sql`; `0005_down.sql` drops them in reverse order) | data-engineer | none | PHFEAT-01; ADR-009 Decision (a) — "`ALTER TABLE products ADD COLUMN cost NUMERIC CHECK (cost IS NULL OR cost >= 0);`"; ADR-009 Decision (b) — "`ALTER TABLE sale_line_items ADD COLUMN unit_cost NUMERIC CHECK (unit_cost IS NULL OR unit_cost >= 0);`" |
| P3-F1-T2 | ✅ Done | Extend catalog service and product form to accept and persist cost | `src/services/catalog.py::Product` gains `cost: float \| None`; `create_product()`/`update_product()` gain a keyword-defaulted `cost: float \| None = None` (existing call sites unchanged); a negative cost raises `ProductValidationError` (`src/exceptions.py`, no new exception type); `src/ui/catalog.py` product form gains an optional cost field, blank input maps to `None` — per ADR-009 Decision (a) interface contract table | backend-developer | P3-F1-T1 | PHFEAT-01; ADR-009 Decision (a) — "a negative cost raises `ProductValidationError` (already declared in `src/exceptions.py` — no new exception type is introduced), and a blank UI input maps to `None`, not `0`." |
| P3-F1-T3 | ✅ Done | Write per-line COGS snapshot onto `sale_line_items.unit_cost` at sale time | `src/services/sales.py::_compute_line` reads the product's `cost` in the same call that computes the line, per ADR-009 Decision (b) — the identical mechanism ADR-005 Decision (b) already uses for `unit_price`/`gst_rate`/`discount_amount`. `SaleLineItem` gains `unit_cost: float \| None`. Discounts do not reduce cost. `NULL` propagates when the product's cost is unknown | backend-developer | P3-F1-T2 | PHFEAT-01; ADR-009 Decision (b) — "Written at sale time in `_compute_line` from the product's `cost` as read in that same call — the identical mechanism ADR-005 Decision (b) already uses for `unit_price`, `gst_rate` and `discount_amount`." |
| P3-F1-T4 | ✅ Done | Unit tests — schema/migration round-trip, catalog cost persistence and validation, snapshot immutability regression | `tests/unit/db/test_schema.py` (T-1), `tests/unit/db/test_migrate.py` (T-2, `0005` up+down against a Phase-2-shaped database, both exercised in the same test per ADR-004), `tests/unit/services/test_catalog.py` (T-3, round-trip cost read/write, negative cost raises `ProductValidationError` not a raw `sqlite3` error), `tests/unit/services/test_sales.py` (T-4 — the regression test asserting a sale line's recorded `unit_cost` survives a later product cost edit unchanged, not merely that the column is populated) | tester | P3-F1-T3 | PHFEAT-01; ADR-009 Decision (b) — "a stored derived value can disagree with its inputs" (grounds T-4's assert-old-value-survives framing) |
| P3-F1-T5 | ✅ Done | Integration test — snapshot participates in the existing single-transaction commit | `tests/integration/` — built through `apply_pending_migrations()`, nothing mocked, mirroring `tests/integration/test_sales_transaction.py`'s existing pattern; a failure partway through leaves no partial write across `sales`, `sale_line_items` (including the new `unit_cost` column), `inventory_stock`, `credit_transactions` | tester | P3-F1-T3 | PHFEAT-01; DLVR-3-02 — "a per-line cost snapshot written onto each sale line at sale time" |
| P3-F1-T6 | ✅ Done | Unit test — bill/receipt view never renders `unit_cost` | `tests/unit/ui/test_sales.py` — added at `phase3-closure` action 4 (test-manager review). Assert the rendered receipt output (`_render_receipt`/`_render_line_items`) for a sale whose line items carry a non-NULL `unit_cost` contains no representation of that value — negative assertion, not merely that the receipt renders. Regression test for ADR-009's Consequences blast-radius item (2): "`unit_cost` rendered on a customer-facing bill or receipt would expose the shop's purchase margins to the customer" | tester | P3-F1-T3 | PHFEAT-01; ADR-009 Decision (b) interface contract table — "`src/ui/sales.py` bill/receipt view \| **MUST NOT render `unit_cost`**" |

## Notes

- Test-strategy mapping (`docs/registry/phase3/README.md` § Phase 3 Test Strategy §1): P3-F1-T1 →
  T-1/T-2; P3-F1-T2 → T-3; P3-F1-T3 → T-4 (implementation); P3-F1-T4 → T-1/T-2/T-3/T-4 (unit
  assertions); P3-F1-T5 → T-5; P3-F1-T6 → not a numbered T-N item, but a required negative-test
  assertion under `development-process.md` §3 ("Negative test cases MUST always be included") and
  ADR-009's own Consequences blast-radius item (2) — added at `phase3-closure` action 4.
- Error-path discipline (`docs/registry/phase3/README.md` § Test Strategy §4): no external service
  boundary is introduced by this feature beyond the existing `sqlite3` boundary already covered by
  `ProductValidationError`/domain-exception conventions in P3-F1-T2/T4 — no new external-integration
  error-path test is owed.
- Coverage floor (`docs/registry/phase3/README.md` § Test Strategy §3): this feature's modules are
  in the ≥90% per-module Phase 3 floor — `src/db/schema.py`, the migration pair, `src/services/
  catalog.py`, `src/services/sales.py`.

## DoD Gate

Tier 1 — run before any task in this feature is marked Done:

```bash
poetry run ruff format --check src/ tests/
poetry run ruff check src/ tests/
poetry run mypy src/ --strict
poetry run pytest tests/unit/ -q --tb=short
```

Tier 2 gate: **Passed** (project-manager, 2026-08-10, bundled with Feature 2 per the 2026-08-10 user deferral decision, `phase3-feature1-cost-capture` space Stage 3). The bundled review (architect action 12, security-reviewer action 13, `framework/spaces/phase3-feature2-pl-reporting.md`) found Feature 1's own surface clean — cost-data classification (`products.cost`/`sale_line_items.unit_cost`) confirmed Internal/non-PII against `ARCH-D02`/`ARCH-D03`; `unit_cost` non-render on `src/ui/sales.py`/`src/ui/customers.py` independently re-verified by both reviewers (empty grep, direct test read). The joint blocking criterion (Feature-2-sourced: "All new environment variables documented in `project-context.md`" — `SMB_REPORTING_TZ_OFFSET_MINUTES`, ADR-008 Decision (b)) is now closed by action 15 (confirmed present in `.env.example:4` and `framework/project/project-context.md:75` by direct grep this run) — the bundled gate is a single joint verdict per the deferral decision, so this file carries the same `Passed` result. No remaining blocker.
