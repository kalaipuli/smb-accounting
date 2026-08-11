# ADR-005: GST-Inclusive Billing Aggregation, Sales/Sale-Line-Item Schema, and Sale Transaction Atomicity

**Status:** Accepted
**Date:** 2026-08-02

## Context

`ADR-002` (GST Rate/Slabs Design) deliberately scoped itself to the product
catalog only and explicitly disclaimed this decision: *"GST-inclusive
billing calculation... is `DLVR-2-01`, Phase 2 — out of scope here."*
`project-context.md`'s `CTX-MVP` requires "GST-inclusive billing on
invoices (bill-level calculation only)." `framework/project/architecture.md`
§3 Flow 1 describes the sale flow narratively ("select product(s)/quantity
→ apply discount if any → GST calculated → attach to customer... →
transaction committed") but does not decide the aggregation math, the
schema, or the atomicity mechanism. No other ADR in `docs/adr/` addresses
any of this (confirmed: `ADR-001` is stack selection only, `ADR-003`/`ADR-004`
address backup/recovery and migration tooling respectively — neither
touches sales data).

This ADR closes that silence gap, identified by the architect's cross-phase
integrity check (`framework/spaces/phase2-scope-planning.md`, Open items,
action 1) and corresponds to that space's action 4
(`author-adr-sales-billing-gst`) and task registry entry `P2-F1-T1`
(`docs/registry/phase2/feature1-sales-billing-gst/tasks.md`).

## Decision

### (a) Bill-level GST aggregation for mixed-slab carts

`products.price` is treated as the **GST-exclusive base price** — GST is
computed and added at sale time, not backed out of an already-tax-inclusive
catalog price. This is consistent with `ADR-002`(c)'s own wording ("no GST
amount is computed or added to any price" in the catalog feature — implying
addition happens later, at billing) and keeps the catalog stable across GST
slab changes: a slab change does not require re-deriving every product's
stored price.

For each sale line item, in the order `architecture.md` Flow 1 already
specifies (apply discount, then compute GST):

```
line_net_price   = (unit_price * quantity) - discount_amount   # GST-exclusive
line_gst_amount  = line_net_price * (gst_rate / 100)            # this line's own slab
line_total       = line_net_price + line_gst_amount             # GST-inclusive
```

The bill total aggregates **per line**, not via a single blended rate:

```
subtotal   = SUM(line_net_price)   across all lines   # GST-exclusive
gst_total  = SUM(line_gst_amount)  across all lines    # sum of each line's own-slab GST
bill_total = subtotal + gst_total                       # GST-inclusive — what CTX-MVP requires
```

A cart spanning multiple statutory slabs (0/5/12/18/28%, per `ADR-002`(a))
is therefore always computed correctly, because each line's GST is computed
against its own product's slab before summing — there is no point at which
a single "blended" rate is applied across differing slabs.

### (b) Sales / sale-line-item schema

```sql
CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER REFERENCES customers (id),
    payment_status TEXT NOT NULL CHECK (payment_status IN ('paid', 'credit')),
    subtotal NUMERIC NOT NULL CHECK (subtotal >= 0),
    gst_total NUMERIC NOT NULL CHECK (gst_total >= 0),
    bill_total NUMERIC NOT NULL CHECK (bill_total >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS sale_line_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id INTEGER NOT NULL REFERENCES sales (id),
    product_id INTEGER NOT NULL REFERENCES products (id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price NUMERIC NOT NULL CHECK (unit_price >= 0),
    gst_rate NUMERIC NOT NULL CHECK (gst_rate >= 0),
    discount_amount NUMERIC NOT NULL DEFAULT 0 CHECK (discount_amount >= 0),
    line_gst_amount NUMERIC NOT NULL CHECK (line_gst_amount >= 0),
    line_total NUMERIC NOT NULL CHECK (line_total >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
```

- `customer_id` is nullable — a `paid`-in-full sale has no customer
  attachment; a `credit` sale MUST have a non-null `customer_id` (enforced
  at the service layer, not by a `CHECK` constraint that would need to
  reference another column's value, which SQLite `CHECK` cannot express
  across-column conditionally without a trigger — out of scope for this
  ADR; service-layer validation follows the same precedent `ADR-002`(b)
  set for GST slab validation).
- `unit_price` and `gst_rate` are **snapshotted** onto each
  `sale_line_items` row from `products` at the moment of sale, not looked
  up via join at read time. `ARCH-D02` requires sales/transaction records
  be retained indefinitely "for historical P&L reporting" — if the catalog
  price or GST rate is edited after a sale, a historical bill must
  continue to show what was actually charged that day, not today's catalog
  value. This mirrors the existing `products`/`discounts`/`inventory_stock`
  table conventions already in `src/db/schema.py` (`AUTOINCREMENT` PK,
  `strftime` timestamp defaults, `NOT NULL CHECK` guards).
- `discount_amount` on `sale_line_items` is the line's total discount
  (already resolved via `src/services/discounts.py` at sale time), not a
  per-unit value — this avoids re-deriving discount math from `discounts`
  records for historical bills, for the same snapshot reason as above.

### (c) Sale transaction atomicity pattern

`src/services/inventory.py`'s `set_stock`/`adjust_stock`/`decrement_stock`
each call `conn.commit()` internally after their own statement (confirmed
by reading the file directly). Reused as-is inside a multi-step sale
transaction, an intermediate commit inside `decrement_stock` would make the
inventory decrement durable before the credit-balance step runs — if that
later step then fails, the transaction cannot be rolled back atomically.

This ADR decides:

- A single `sqlite3.Connection` is shared across the entire sale-commit
  sequence (sale insert, `sale_line_items` inserts, inventory decrement,
  and — when `payment_status = 'credit'` — the credit-balance update).
- `set_stock`, `adjust_stock`, and `decrement_stock` in
  `src/services/inventory.py` gain a new optional parameter
  `commit: bool = True`. The default preserves today's per-call-commit
  behaviour for every existing Phase 1 caller (`src/ui/inventory.py`'s
  manual-adjust screen, and Phase 1 unit tests) — this is a
  backward-compatible signature addition, not a breaking change.
- `src/services/customers.py`'s credit-balance-update function (decided by
  `ADR-006`) follows the same `commit: bool = True` pattern for the same
  reason.
- The sale orchestrator, `record_sale()` in a new `src/services/sales.py`,
  calls each mutation with `commit=False`, then issues exactly one
  `conn.commit()` after every step succeeds. On any exception during the
  sequence, it calls `conn.rollback()` and raises `SaleTransactionError`
  (new domain exception, wrapping the original error) — no partial commit
  can occur, because no sub-step commits independently once composed this
  way.
- New exceptions added to `src/exceptions.py`: `SalesError` (base),
  `SaleValidationError` (bad line-item/payment-status data),
  `SaleNotFoundError`, `SaleTransactionError` (wraps a failure during the
  atomic commit sequence).

### Gate 2 — ADR Adherence Protocol checks (`agent-conduct.md`)

- **Scope gap:** `ADR-002` explicitly disclaims this decision for its own
  scope ("out of scope here"); no other existing ADR (`ADR-001`, `ADR-003`,
  `ADR-004`) addresses sales data, GST bill aggregation, or transaction
  atomicity. This is a genuinely new decision, not an unauthorized
  extension of an existing one.
- **Silence gap:** `architecture.md` §3 Flow 1 states the sequence
  narratively (discount → GST → commit) but not the aggregation math,
  schema, or atomicity mechanism — all three are resolved here.
- **Conflict:** `docs/adr/` (`ADR-001`–`ADR-004`) reviewed in full; no
  contradiction found — none of them makes a competing decision about
  sales schema, GST bill computation, or transaction handling.
- **Consumer gap:** components that will read this schema/decision are
  named: `src/services/sales.py` (write path, `P2-F1-T3`/`T4`),
  `src/ui/sales.py` (display, `P2-F1-T5`), a future P&L reporting module
  (`ARCH-F02`, reads `sales`/`sale_line_items` for period aggregation, not
  yet implemented), and `src/services/customers.py` (khata balance
  read/write on credit sales, `ADR-006`).

## Multi-System Impact Analysis

- **Availability impact:** none beyond this single local process — no
  downstream systems exist in this stack (`project-context.md` §2: no
  message queue, no cache, no server). The only effect is within the local
  sale-entry code path.
- **Blast radius:** the `commit: bool = True` signature addition to
  `src/services/inventory.py`'s three mutation functions affects every
  existing caller. Backward-compatible by default — `src/ui/inventory.py`'s
  manual-adjust screen and existing Phase 1 unit tests
  (`tests/unit/services/test_inventory.py`) keep their current
  commit-per-call behaviour unchanged and require no edits. Only new
  Phase 2 tests exercising the composed transaction need to pass
  `commit=False` and assert the caller commits/rolls back.
- **Rollback path:** this decision is additive — new tables (`sales`,
  `sale_line_items`), new module (`src/services/sales.py`), new exceptions,
  and one backward-compatible parameter addition to `inventory.py`.
  Reverting is: drop the new tables/migration pair, remove
  `src/services/sales.py`, remove the new exception classes, and remove the
  `commit` parameter from `inventory.py` (a no-op for existing callers
  since the default already preserves their behaviour). No data migration
  of existing `products`/`discounts`/`inventory_stock` rows is required in
  either direction.

## Alternatives Considered

- **Single blended/weighted-average GST rate for the whole cart** —
  rejected. Mathematically ill-defined once a cart spans multiple slabs
  and quantities vary; risks silently misstating GST on any multi-rate
  cart, which directly contradicts `CTX-MVP`'s requirement that
  GST-inclusive billing be correct. Real multi-item GST invoices tax each
  item at its own slab and sum — this ADR's per-line method matches that.
- **Treating `products.price` as already GST-inclusive (MRP-style) and
  backing out the tax portion at billing time** — rejected. Requires a
  division/back-calculation on every sale, is more error-prone with
  rounding, and has no textual support in `ADR-002`; the
  "exclusive base + add GST" model is simpler and matches `ADR-002`(c)'s
  own wording directly.
- **No snapshot — always join `products` for historical bill
  reconstruction** — rejected. Breaks the moment catalog data changes,
  silently corrupting historical P&L figures with no error raised, in
  direct conflict with `ARCH-D02`'s indefinite-retention-for-reporting
  requirement.
- **Duplicating inventory-decrement SQL directly inside `sales.py`** instead
  of reusing `inventory.py`'s existing functions — rejected. Violates
  `anti-patterns.md`'s "re-declare a shared type/logic in multiple modules"
  and the pre-implementation Gate 1 no-duplicate-schema rule.
- **SAVEPOINT-based nested transactions** — rejected as unneeded
  complexity. A single `sqlite3.Connection` with deferred `commit()` in one
  orchestrating call already provides the required atomicity guarantee for
  this project's single-connection, single-process design (confirmed:
  Python's `sqlite3` module defaults to non-autocommit / deferred
  transaction mode, batching all statements since the last commit/rollback
  into one transaction).

## Consequences

- `src/services/sales.py` becomes the single owner of the GST bill
  aggregation formula and the sale-commit sequence — any future code that
  needs a GST-inclusive total must call into this module rather than
  re-implementing the per-line formula, to avoid divergence.
- `src/services/inventory.py`'s mutation functions become reusable inside
  composed, multi-step transactions (via `commit=False`) without breaking
  any existing single-call usage — this pattern should be followed by
  `ADR-006`'s customer credit-balance-update function for the same reason.
- Phase 2 P&L reporting (`ARCH-F02`) has a decided, documented schema
  (`sales`/`sale_line_items`) to read from — no re-derivation needed when
  that work starts.
- A future statutory GST slab change requires no schema or migration —
  only `ADR-002`'s service-layer slab-validation set changes, since
  `sale_line_items.gst_rate` is a snapshot, not a live lookup.

## Review

Per `agent-conduct.md`'s ADR Adherence Protocol and this feature's task DoD
(`P2-F1-T1`), this ADR requires independent Architect review before its
status may move to `Accepted`. No self-certification by the authoring
persona (backend-developer) — this ADR was filed as `Status: Proposed`
pending that review. See `framework/spaces/phase2-scope-planning.md`
action 4.

**Architect review verdict (2026-08-02): Accepted.**

Independently opened and cross-checked, not accepted on the authoring
persona's summary alone: `docs/adr/ADR-002-gst-rate-slabs-design.md` (its own
explicit out-of-scope disclaimer for bill-level GST), `framework/project/
architecture.md` §3 Flow 1 (line 51-57) and ARCH-F01 (line 191) — confirmed
the flow narratively states "apply discount if any → GST calculated" in that
exact order and "commits a single SQLite transaction: inserts the sale
record, decrements inventory, and (if on credit) updates the customer's
outstanding balance" — matching this ADR's Decision (a) ordering and
Decision (c) transaction scope exactly, not an invented sequence.
`src/services/inventory.py` (read directly) — confirmed `set_stock`,
`adjust_stock`, `decrement_stock` each call `conn.commit()` internally,
substantiating this ADR's stated atomicity gap and justifying the
`commit: bool = True` parameter design. `src/exceptions.py` — confirmed the
existing `CatalogError`/`DiscountError`/`InventoryError` base+specific
exception convention this ADR's new `SalesError` family follows.
`grep -rn "^class " src/` — no duplicate/conflicting `Sale`/`Sales`-named
type exists; `git status --short docs/adr/` — file is new, no drift.

Gate 2 ADR Adherence Protocol checks re-run independently:

- **Scope gap:** Confirmed — `ADR-002` explicitly disclaims bill-level GST
  computation for its own scope; `ADR-001`/`ADR-003`/`ADR-004` opened
  directly, none addresses sales data, GST aggregation, or transaction
  atomicity. Genuinely new decision, not an unauthorized extension.
- **Silence gap:** Confirmed — `architecture.md` §3 Flow 1/ARCH-F01 state
  the sequence narratively only; aggregation math, schema, and atomicity
  mechanism were undecided prior to this ADR and are resolved here.
- **Conflict:** Confirmed none — `docs/adr/` (ADR-001–004) reviewed; no
  competing decision on sales schema, GST bill computation, or transaction
  handling exists.
- **Consumer gap:** Confirmed — `src/services/sales.py` (write, P2-F1-T3/T4),
  `src/ui/sales.py` (display, P2-F1-T5), future P&L reporting (ARCH-F02,
  not yet implemented), and `src/services/customers.py` (`ADR-006`, khata
  balance on credit sales) are all named; no unaddressed reader identified.
- **Schema impact / cross-system analysis:** This is a schema change (two
  new tables). Multi-System Impact Analysis is present and adequate —
  availability impact none (single local process, no downstream systems,
  confirmed against `project-context.md` §2), blast radius correctly scoped
  to the backward-compatible `commit` parameter addition on
  `inventory.py`'s three mutation functions (default preserves every
  existing Phase 1 caller's behaviour, confirmed by reading
  `src/ui/inventory.py`'s call sites use the default), rollback path is
  additive-only (drop new tables, remove new module/exceptions, remove the
  `commit` parameter — a no-op for existing callers).
- **Migration mechanism consistency (Architect lens, beyond the base four
  checks):** `ADR-004` (Accepted) established that schema changes reach a
  live install only through a versioned `NNNN_up.sql`/`NNNN_down.sql`
  migration pair applied via `apply_pending_migrations()` — a bare
  `SCHEMA_STATEMENTS` addition alone would silently fail to reach any
  pre-existing installed database, the exact bug class `ADR-004` closed.
  This ADR's own text does not restate that mechanism, but the downstream
  task registry (`docs/registry/phase2/feature1-sales-billing-gst/
  tasks.md`, `P2-F1-T2`) already assigns "`src/db/schema.py` + migration
  pair in `src/db/migrations/`" to the implementing task, correctly
  deferring the mechanical detail the same way `ADR-002` deferred its own
  implementation specifics — not a silence gap requiring this ADR to
  re-decide a mechanism `ADR-004` already fixed project-wide.

Verdict: **Accepted.** Resolves DLVR-2-01's silence gap in full — GST
bill-level aggregation, sales/sale-line-item schema, and transaction
atomicity are all decided with no remaining silence gap in this
deliverable's scope. `P2-F1-T1` may move to `Done`; `P2-F1-T2` onward may
proceed against this decided schema/aggregation/transaction pattern.
