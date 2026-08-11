---
report_type: adr
adr_number: "ADR-009"
status: Accepted
date: 2026-08-10
---

# ADR-009: Product Cost Capture, Per-Line COGS Snapshot, and Pre-Migration Cost Disposition

**Date:** 2026-08-10
**Status:** Accepted

> **Status note.** Accepted 2026-08-10 at `framework/spaces/phase3-start.md`
> action 3 — presented as Group B (cost column location/type/nullability,
> per-line COGS snapshot, unknown-cost disposition) alongside companion
> `ADR-008`'s Group C (report output contract) and approved together by the
> owner. See `docs/registry/phase3/README.md` § Phase Planning Decisions for
> the full resolution record. Per `framework/guidelines/development-process.md`
> §8, the first Phase 3 task line may now be written — `DLVR-3-02`'s own plan
> row states the same requirement. Companion to `ADR-008`, which decides the
> period semantics that consume the values captured here.

## Context

`DLVR-3-02` — "Product cost capture and per-line COGS snapshot" — was added
to Phase 3 at plan revision 5 (Phase 2→3 boundary) because **no cost column
exists anywhere in the schema**, although `DLVR-3-01` and `architecture.md`
§3 Flow 2 ("aggregates sales, cost of goods ... and discounts given for the
selected period") both require cost of goods. `products` carries `name`,
`price`, `gst_rate`, `created_at`, `updated_at`; migrations `0001`–`0004` add
no cost column. Without one, "P&L" reduces to a revenue report.

Three decisions follow from that, and each has a silent wrong default:

1. **Where the cost lives, and its type and nullability.** A `NOT NULL
   DEFAULT 0` column would make every existing product assert a zero purchase
   price, indistinguishable from a genuinely free item.
2. **Whether cost is snapshotted per sale line or joined at report time.**
   Product cost is mutable — the catalog has an edit path and `updated_at`
   exists because rows are updated. A P&L that joins current cost restates
   historical profit every time the owner corrects a purchase price,
   invisibly, in a tool whose entire purpose is period reporting.
3. **How sales recorded before the migration report.** Their cost is
   unrecoverable. Treating NULL as zero under-counts COGS and overstates
   profit with no signal to the reader.

Two existing decisions bind on the answers. **ADR-005 Decision (b)** already
snapshots `unit_price`, `gst_rate` and `discount_amount` onto
`sale_line_items` at sale time — `src/db/schema.py`'s docstring records the
reason verbatim: "specifically so a later edit to `products` cannot silently
alter a historical bill". **ADR-004** fixes the migration discipline:
`NNNN_up.sql` / `NNNN_down.sql` authored and exercised together, discovered
and applied by `apply_pending_migrations()` at startup, mirrored into
`SCHEMA_STATEMENTS`.

One further point is decided here because leaving it silent would be a real
defect: `src/services/sales.py::_compute_line` computes `line_gst_amount` on
top of `line_net_price`, which proves `products.price` is **GST-exclusive**.
A cost recorded GST-inclusive would not be comparable to it, and the
resulting margin would be wrong by the GST rate.

## Decision

### (a) The cost column — `products.cost`

```sql
ALTER TABLE products ADD COLUMN cost NUMERIC
    CHECK (cost IS NULL OR cost >= 0);
```

- **Name:** `cost`. **Type:** `NUMERIC` — the form `price` and `gst_rate`
  already use in this table. **Nullable, with no default.**
- **Meaning:** the most recently known per-unit purchase (landed) price of
  the product, **exclusive of input GST**, in the same currency and units as
  `price`. Input tax credit is out of scope (`CTX-MVP` defers GST portal
  filing), so no input-GST figure is stored.
- **`NULL` means "cost unknown"** — never "cost is zero". This is the value
  Decision (c) keys on, and it is why the column is nullable.
- **Validation** lives in `src/services/catalog.py` alongside the existing
  `_validate_price` / `_validate_gst_rate`: a negative cost raises
  `ProductValidationError` (already declared in `src/exceptions.py` — no new
  exception type is introduced), and a blank UI input maps to `None`, not
  `0`. The `CHECK` constraint is a backstop, not the first line of defence,
  matching the convention `src/services/customers.py::record_payment`
  documents.

### (b) Per-line COGS snapshot — `sale_line_items.unit_cost`

```sql
ALTER TABLE sale_line_items ADD COLUMN unit_cost NUMERIC
    CHECK (unit_cost IS NULL OR unit_cost >= 0);
```

- Written at sale time in `_compute_line` from the product's `cost` as read
  in that same call — the identical mechanism ADR-005 Decision (b) already
  uses for `unit_price`, `gst_rate` and `discount_amount`. No new pattern is
  introduced.
- **Line COGS is derived, not stored:** `unit_cost * quantity`. This mirrors
  `get_sale`, which already derives `line_net_price` rather than persisting
  it; a stored derived value can disagree with its inputs.
- **Discounts do not reduce cost.** A discount changes what the customer
  paid, not what the shop paid. `unit_cost` is independent of
  `discount_amount`.
- `NULL` propagates: a sale of a product whose `cost` is `NULL` records
  `unit_cost = NULL`. Decision (c) covers that case with the same mechanism
  it covers pre-migration rows with.

**Interface contract — every field, producer and consumer this changes.** No
implementation task may alter this set without architect review
(`architect.md` § Interface Design).

| Symbol | Change | Consumers affected |
|---|---|---|
| `src/services/catalog.py::Product` | gains `cost: float \| None` | `src/ui/catalog.py`, `src/services/sales.py::_compute_line`, catalog tests |
| `create_product()` / `update_product()` | gain `cost: float \| None = None` — **keyword-defaulted, so every existing call site keeps working unchanged** | `src/ui/catalog.py`, all Phase 1 catalog tests |
| `src/services/sales.py::SaleLineItem` | gains `unit_cost: float \| None` | `_insert_sale_line_item`, `get_sale`, `src/ui/sales.py`, sales tests |
| `_insert_sale_line_item()` | writes `unit_cost` | — |
| `get_sale()` | reads `unit_cost` back | `src/ui/customers.py` invoice view |
| `src/ui/catalog.py` product form | gains an optional cost field; blank ⇒ `None` | — |
| `src/ui/sales.py` bill/receipt view | **MUST NOT render `unit_cost`** | see Consequences, blast radius |

`Bill`, `Sale`, `compute_bill()` and `record_sale()` signatures are unchanged.
No shared type is redeclared: `SaleLineItem` and `Product` keep their single
canonical definitions (`architecture-rules.md` § Schema Ownership).

### (c) Unknown cost is reported as cost-incomplete — never as zero

- COGS sums **only lines with a non-NULL `unit_cost`**.
- The reporting service returns `lines_missing_cost` (count) and
  `cost_complete` (bool) alongside the figures — the fields fixed in ADR-008
  Decision (d) — so the incompleteness is **observable in the returned data**,
  not inferred by the UI (`docs/registry/phase3/README.md` T-9).
- When `cost_complete` is false, the UI renders an explicit banner naming the
  counts — e.g. "COGS incomplete: 12 of 340 sale lines in this period have no
  recorded cost. Net profit is an upper bound." — and `net` is labelled as an
  upper bound in that state.
- **No backfill.** Historical `unit_cost` is never populated from today's
  `products.cost`.
- This mechanism is not pre-migration-specific: it covers every unknown-cost
  line for the life of the product, including sales made after the migration
  of a product whose cost was never entered. One mechanism, whole class.

### (d) Migration `0005`, per ADR-004

- `src/db/migrations/0005_up.sql` adds both columns (products first, then
  `sale_line_items`); `0005_down.sql` drops them in reverse order of
  addition, following `0002_down.sql`'s `ALTER TABLE ... DROP COLUMN` idiom.
- Both files are authored and exercised together in one test
  (`docs/registry/phase3/README.md` T-2), and the same DDL is mirrored into
  `SCHEMA_STATEMENTS` in `src/db/schema.py` so a freshly created database and
  a migrated one have identical shape.
- `src/db/schema.py`'s module docstring gains the classification note for
  both columns (see Consequences).
- The tolerant-idempotent "duplicate column name" path ADR-004 defines
  applies unchanged; both statements are of exactly the idempotent-or-
  tolerated kind ADR-004's `executescript` caveat depends on.

## Alternatives Considered

- **`cost NUMERIC NOT NULL DEFAULT 0`.** Rejected: every product that
  predates the migration would assert a zero purchase price, which is
  indistinguishable from a genuinely free item and inflates reported profit
  by 100% of revenue on those lines — silently, and permanently, because
  nothing would ever mark those values as fabricated. It also removes the
  only signal Decision (c) can key on.
- **Joining `products.cost` at report time instead of snapshotting.**
  Rejected: this is the failure the deliverable exists to prevent. Correcting
  a purchase price would restate every historical period that ever sold that
  product, invisibly. It also contradicts ADR-005 Decision (b), which already
  established the opposite for `unit_price` on the same table — accepting it
  would put two contradictory conventions on one row.
- **Backfilling `sale_line_items.unit_cost` from current `products.cost` at
  migration time.** Rejected: asserts a price that was not true at sale time,
  producing plausible-looking but fabricated history — worse than a labelled
  gap, because it is undetectable afterwards.
- **A separate `product_costs` history table (cost with effective dates).**
  Rejected: YAGNI at one use case (`anti-patterns.md` — no abstraction for
  fewer than three), and it solves a problem the snapshot already solves —
  the only cost history reporting depends on is the cost as at each sale,
  which `unit_cost` records exactly. It would also add a temporal join to
  every report query.
- **FIFO or weighted-average lot costing.** Rejected as **not computable**:
  no purchases, receipts or lot table exists, and `inventory_stock` is
  explicitly "current stock levels only ... not a movement ledger"
  (`src/db/schema.py`). Real lot costing needs a purchases ledger, which is a
  larger scope than SCOPE-04 and is not in `scope.md`. **Stated gap:** what
  this ADR delivers is *latest-known-cost* costing — `unit_cost` is the
  shop's purchase price at the moment of sale, not the actual acquisition
  cost of the specific unit sold. When purchase prices move within a stock
  holding period, reported COGS is approximate. That is a real limitation,
  recorded here rather than left for someone to discover from a discrepancy.
- **Storing `line_cost_total` as a column too.** Rejected: derivable from two
  persisted columns, and a stored derived value can drift out of agreement
  with its inputs. Consistent with `line_net_price`, which `get_sale` derives.
- **Recording cost GST-inclusive.** Rejected: `products.price` is
  GST-exclusive (proven by `_compute_line`), so a GST-inclusive cost would
  overstate COGS by the input GST rate and understate margin, with no error
  anywhere to catch it.
- **Making cost mandatory on product creation going forward.** Rejected for
  Phase 3: it would block the catalog screen for a shop owner who does not
  know a purchase price at entry time, and Decision (c) already reports the
  consequence honestly. Revisit if `lines_missing_cost` stays high in
  practice — that metric is exactly the signal for it.

## Consequences

- **Easier:** P&L gains a real cost figure, and it cannot be retroactively
  changed by a catalog edit — the property `architecture.md` §3 Flow 2
  assumes and the schema did not yet support.
- **Easier:** the unknown-cost path is uniform. Pre-migration rows, products
  never costed, and future gaps all surface through one pair of fields, so
  the UI has one state to render and the tests have one behaviour to assert.
- **Harder:** the shop owner must enter purchase prices for cost figures to
  mean anything. Until they do, every report is cost-incomplete and says so.
  That visible nagging is the intended behaviour, not a defect.
- **Harder:** two more columns to keep synchronised across
  `SCHEMA_STATEMENTS`, the migration pair, and the dataclasses. This is the
  cost ADR-004's mirroring discipline already imposes on every schema change.
- **Backward compatibility:** `cost` is keyword-defaulted to `None` on
  `create_product` / `update_product`, so no existing caller or test breaks
  (`architecture-rules.md` § Backward Compatibility). `SaleLineItem` gains a
  field with a `None` default for the same reason; positional construction in
  existing code stays valid.
- **Compliance posture.** *Data classification:* `products.cost` is
  **Internal** under `architecture.md` ARCH-D03 ("Product pricing catalog —
  Internal"); `sale_line_items.unit_cost` is **Internal** under ARCH-D02
  ("Sales/transaction records — Internal — Indefinite"). Neither is PII, so
  no new ARCH-D row is created — these are columns on already-classified
  entities, and `src/db/schema.py`'s module docstring is updated to say so.
  Cost is nonetheless commercially sensitive to the shop; no service module
  in this codebase logs today, and none should begin logging cost.
  *Tenant isolation:* not applicable — single shop, single user, single local
  file (ARCH-P01). *Audit trail:* `products.updated_at` records that a cost
  edit occurred but not the prior value; there is no cost-history table by
  the decision above. This is accepted because the per-line snapshot
  preserves the only prior values reporting depends on — an edit can no
  longer damage a historical figure, so the audit need is materially reduced.
  The sale-commit path already writes `sale_line_items` inside ADR-005
  Decision (c)'s single transaction, and `unit_cost` joins that same
  transaction with no new commit point.
- **Multi-system impact analysis** (cross-cutting: a schema change reaching
  every installed database at next startup):
  - **Availability impact:** none beyond this single local process — no
    downstream systems exist (`architecture.md` §4). Migration `0005` runs at
    startup via `apply_pending_migrations()`; both statements are
    `ALTER TABLE ADD COLUMN`, which SQLite performs as a metadata-only
    operation without rewriting the table, so the startup delay is
    negligible even on a multi-year database. `src/db/backup.py` must survive
    the new column — one integration case covers it
    (`docs/registry/phase3/README.md` §7).
  - **Blast radius:** three worst cases, in descending severity. (1) A defect
    in `0005_up.sql` raises an untolerated `OperationalError` and the app
    fails to start — loud, per ADR-004's deliberate design, and non-
    destructive because `ADD COLUMN` does not rewrite data. (2) `unit_cost`
    rendered on a customer-facing bill or receipt would expose the shop's
    purchase margins to the customer; this is why the interface table above
    prohibits it in `src/ui/sales.py` explicitly rather than leaving it to
    taste. (3) A snapshot written from the wrong source (e.g. re-reading cost
    after a catalog edit) would produce wrong-but-plausible COGS — covered by
    the T-4 regression test, which must assert the *old* value survives a
    subsequent cost edit, not merely that the column is populated.
  - **Rollback path:** `0005_down.sql` drops both columns and the version row
    is deleted from `schema_migrations` so the runner treats it as pending —
    the procedure ADR-004 already documents. **Caveat, stated because it is
    asymmetric:** dropping `sale_line_items.unit_cost` destroys every
    snapshot captured since the migration, and those values are not
    recoverable from anywhere else. The down migration is therefore safe only
    before real post-migration sales exist (i.e. during development and
    testing, which is what T-2 exercises). To reverse the *feature* on a live
    install after sales have been recorded, revert the reporting and capture
    code and leave the columns in place — an unused nullable column is inert,
    whereas a dropped one is data loss. Take a backup via `src/db/backup.py`
    before any down migration on a real database.

## Scaling Limit

- **Latest-known-cost costing** holds while purchase prices are stable
  relative to stock turnover — true for a kirana shop buying weekly at
  broadly steady prices. It breaks when the owner needs true lot-level COGS,
  when purchase prices move sharply within a holding period, or when
  multi-supplier price variance becomes material. Signal: reported margin
  disagreeing with the owner's own sense of it on specific products.
  Response: a purchases/receipts ledger plus FIFO or weighted-average
  costing — the alternative rejected above, which would supersede this ADR
  rather than extend it.
- **Cost completeness** improves monotonically as products are costed, but
  pre-migration periods remain permanently cost-incomplete. There is no scale
  at which that self-corrects, by design; the label is permanent for those
  periods.
- **Aggregation volume:** COGS is summed over the same `sale_line_items`
  scan ADR-008's revenue figures use, so it adds no separate scaling
  concern — the index trigger point in ADR-008's Scaling Limit
  (roughly 100,000 `sale_line_items` rows, or a missed annual-report budget)
  governs both.
- **Migration model:** `ADD COLUMN` is metadata-only and stays cheap at any
  row count this project will reach. ADR-004's own scaling limit — a future
  migration performing a large data backfill on the blocking startup path —
  is deliberately not approached here: Decision (c) rules out a backfill.
