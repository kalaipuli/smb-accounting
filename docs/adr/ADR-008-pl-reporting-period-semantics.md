---
report_type: adr
adr_number: "ADR-008"
status: Accepted
date: 2026-08-10
---

# ADR-008: P&L Reporting Period Semantics — Accounting Basis, Trading-Day/Timezone Boundary, and Year Convention

**Date:** 2026-08-10
**Status:** Accepted

> **Status note.** Accepted 2026-08-10 at `framework/spaces/phase3-start.md`
> action 3, across three rounds of owner review: Group A (accounting basis,
> trading-day/timezone boundary, year convention) approved with two
> redirects — A3 from the Indian fiscal year to the calendar year (drafted at
> action 15, confirmed by the owner), and A2's timezone mechanism from a
> hardcoded constant to a single shop-level configurable setting (drafted at
> action 17); Groups B/C (companion `ADR-009` cost-capture decisions and this
> ADR's Decision (d) output contract) approved together with A2's revised
> wording in the same final round. See `docs/registry/phase3/README.md`
> § Phase Planning Decisions for the full resolution record. Per
> `framework/guidelines/development-process.md` §8, the first Phase 3 task
> line may now be written, and per `docs/registry/phase3/README.md` § Phase
> 3 Test Strategy §2 the six period-boundary test assertions take their
> expected values from the Decision section below.

## Context

Phase 3 delivers `DLVR-3-01` — "P&L reporting — daily/monthly/quarterly/annual
profit & loss aggregation and display" — against `SCOPE-04`, whose full text
is "P&L reporting — daily, monthly, quarterly, annual". Three questions must
be answered before any aggregation query can be written, and each has a
different-but-plausible wrong answer that an implementer would otherwise pick
silently.

**1. Accounting basis.** `sales.payment_status` separates `paid` from
`credit`, and `credit_transactions` records later repayments, so accrual
(revenue at sale) and cash (revenue at payment) are both computable in
principle and produce different figures for the same period. Nothing in
`scope.md`, `architecture.md`, `interview-notes.md` or ADR-001…007 states
which the report means. One piece of schema evidence is decisive and is
recorded here rather than assumed: `src/services/customers.py::record_payment`
writes a khata payment with `sale_id = NULL` per ADR-006 Decision (b) — "not
tied to any specific sale ... a standalone payment against the customer's
overall balance". A repayment therefore cannot be attributed to the sale it
settles.

**2. Trading-day boundary and timezone.** Every `created_at` in
`src/db/schema.py` defaults to `strftime('%Y-%m-%dT%H:%M:%fZ', 'now')` — UTC,
millisecond precision, ISO-8601, lexicographically sortable — and
`src/services/sales.py::record_sale` supplies no timestamp of its own, so
that default is the only sale timestamp the system has. The shop trades in
IST (UTC+05:30). A naive `DATE(created_at)` grouping attributes every sale
between 00:00 and 05:30 IST to the previous trading day. `src/config.py`
holds no timezone constant today.

**3. Calendar year or Indian fiscal year.** "Quarterly" and "annual" in
SCOPE-04 are ambiguous between the calendar year (Jan–Dec, Q1 = Jan–Mar) and
the Indian fiscal year (Apr–Mar, Q1 = Apr–Jun). Both are defensible readings;
only one matches the year the shop's accountant files on.

A fourth question is unavoidable once the first three are answered: **what
shape does the reporting service return?** No reporting module exists under
`src/services/`, and no ADR defines a P&L return contract. Leaving it
unspecified would hand a cross-cutting interface decision to the first
implementation task, and `docs/registry/phase3/README.md` T-9 already
constrains it ("the label is observable in the returned data — not left to
the UI to infer"). It is decided here as architect-owned interface design.

## Decision

### (a) Accrual basis — revenue is recognised at sale time

We will recognise revenue, discounts and COGS in the period containing the
sale's `sales.created_at`, regardless of when (or whether) a credit sale is
repaid. `credit_transactions` rows do **not** move any P&L figure between
periods.

To keep the cash question visible rather than answered wrongly, every report
additionally carries an informational, non-P&L figure: **credit sales
recognised in this period** — `SUM(sales.bill_total) WHERE payment_status =
'credit'` within the period. It is labelled as an accrual disclosure, not as
a cash-collected figure, because no cash-collected-per-sale figure is
computable (see Context, item 1).

### (b) Trading day is IST (UTC+05:30) by default, as a single configurable shop-level setting

- The reporting timezone is **one configurable value for the whole shop
  install** — not a per-user setting (there is no user/profile/login concept
  anywhere in this codebase; see Alternatives) and not a hardcoded constant
  either, per the owner's 2026-08-10 ruling on action 16
  (`framework/spaces/phase3-start.md`): option (i), a single shop-level
  setting, not option (ii), a per-user profile system.
- **Storage mechanism: an environment-variable override on a config
  constant, not a new settings table.** `src/config.py` already establishes
  this exact shape for `DEFAULT_DB_PATH` / `get_db_path()` — a module-level
  default overridable by an environment variable, read once at call time, no
  schema or migration involved:

  ```python
  # src/config.py
  DEFAULT_REPORTING_TZ_OFFSET_MINUTES: int = 330  # IST, UTC+05:30

  def get_reporting_tz() -> datetime.timezone:
      raw = os.environ.get("SMB_REPORTING_TZ_OFFSET_MINUTES")
      minutes = int(raw) if raw else DEFAULT_REPORTING_TZ_OFFSET_MINUTES
      return datetime.timezone(datetime.timedelta(minutes=minutes))
  ```

  The reporting service calls `get_reporting_tz()` — never a bare
  `REPORTING_TZ` constant — everywhere a trading-day or period bound is
  computed, mirroring how every DB-path caller goes through `get_db_path()`
  rather than reading `DEFAULT_DB_PATH` directly
  (`architecture-rules.md` § No Undocumented Configuration; § Config
  Isolation).
- Default value is unchanged from the original proposal: UTC+05:30 (IST).
  `zoneinfo` is not used — see Alternatives.
- A **trading day** `D` is `[D 00:00:00.000 IST, D+1 00:00:00.000 IST)`.
- Every period is **half-open**: inclusive lower bound, exclusive upper
  bound. There is no `<=` upper comparison anywhere in the reporting service.
- Bounds are computed in IST, converted to UTC, and rendered in the exact
  `%Y-%m-%dT%H:%M:%f` + `Z` form `created_at` uses, then compared as text:
  `WHERE s.created_at >= :start_utc AND s.created_at < :end_utc`. This is
  correct because that format is fixed-width and lexicographically ordered;
  it is also index-friendly (see Scaling Limit).
- `sales.created_at` is the single period key. `sale_line_items.created_at`
  is never used for period selection — a line belongs to the period its sale
  belongs to.

### (c) Calendar year (1 January – 31 December) for annual and quarterly

- **Annual** = 1 Jan YYYY (IST) to 1 Jan YYYY+1 (IST), exclusive upper,
  labelled `CY 2027`.
- **Quarterly** = Q1 Jan–Mar, Q2 Apr–Jun, Q3 Jul–Sep, Q4 Oct–Dec, labelled
  `CY 2027 Q1 (Jan–Mar 2027)`.
- **Daily** and **monthly** grains are unaffected by this choice.
- Every rendered period carries an explicit label naming the calendar year and
  the covered calendar months, so the reader never has to infer which year
  convention was used.

### (d) Report output contract

The reporting service returns one frozen dataclass per report. Field names,
types and producers are fixed here; consumers are the reporting UI and the
tests in `docs/registry/phase3/README.md` §1 T-6…T-10.

| Field | Type | Meaning |
|---|---|---|
| `period_label` | `str` | Human-readable label per (c), e.g. `CY 2027 Q1 (Jan–Mar 2027)` |
| `period_start_utc` | `str` | Inclusive lower bound, in `created_at`'s format |
| `period_end_utc` | `str` | **Exclusive** upper bound, same format |
| `revenue` | `float` | Sum of line net (post-discount, GST-exclusive) amounts |
| `discounts` | `float` | Sum of `sale_line_items.discount_amount` |
| `cogs` | `float` | Sum of `unit_cost * quantity` over lines with a non-NULL `unit_cost` (ADR-009) |
| `net` | `float` | `revenue - cogs` |
| `credit_sales` | `float` | Accrual disclosure per (a) — not a cash figure |
| `line_count` | `int` | Sale lines in the period |
| `lines_missing_cost` | `int` | Lines with NULL `unit_cost` (ADR-009 Decision (c)) |
| `cost_complete` | `bool` | `lines_missing_cost == 0` |

Rules that bind on the implementation:

- **GST is not a P&L figure.** GST collected is a liability, not revenue, and
  GST filing is out of scope (`CTX-MVP`). `revenue` is GST-exclusive —
  consistent with `products.price`, which `_compute_line` proves is
  GST-exclusive (`line_gst_amount` is added on top of `line_net_price`).
  `gst_total` is not reported as income and does not enter `net`.
- **`discounts` is disclosure, not a subtraction.** `revenue` is already net
  of discounts (`line_net_price = price*qty - discount_amount`); `discounts`
  is reported so the owner can see what was given away. Subtracting it again
  would double-count, which is the arithmetic error this note exists to
  prevent.
- **Report generation is read-only.** The reporting service issues `SELECT`
  only — no `INSERT`, `UPDATE`, `DELETE`, `commit()` or DDL. Re-running the
  same period leaves the database byte-identical.
- **Empty period is a value, not an error.** Zero sales returns all-zero
  figures with `line_count = 0`, `lines_missing_cost = 0`,
  `cost_complete = True` — never `None` and never an exception.
- **Errors are domain errors.** Any `sqlite3` failure inside the reporting
  service is wrapped in a domain exception declared in `src/exceptions.py`
  (canonical location, `architecture-rules.md` § Schema Ownership); a raw
  `sqlite3.Error` must not cross the service boundary. The dataclasses live
  with the reporting service module, and no field of them is redeclared
  elsewhere.

### (e) Resolved expectations for the six period-boundary cases

These are the values `docs/registry/phase3/README.md` § Test Strategy §2
defers to this ADR. They become writable the moment this ADR is `Accepted`.

| Case (README §2) | Expected behaviour under this ADR |
|---|---|
| A sale at 02:00 IST (= 20:30 UTC the previous day) | Belongs to trading day **D** (the IST date), not D-1. Per (b). |
| A sale at 23:59:59.999 IST on the last day of a period | **Included.** The exclusive upper bound sits at the next period's 00:00:00.000 IST, so no millisecond of the last trading day is dropped. Per (b). |
| A credit sale in period P repaid in period P+1 | Revenue, discounts and COGS all land in **P**. P+1 is unchanged by the repayment. P's `credit_sales` disclosure includes the sale. Per (a). |
| A quarter and a year boundary | Calendar. A sale on 31 Dec 2027 IST is `CY 2027 Q4`; a sale on 1 Jan 2028 IST is `CY 2028 Q1`. Per (c). |
| The same period requested twice | Byte-identical result, no database mutation. Per (d). |
| A partial refund or a corrected sale | **Out of scope for Phase 3 by decision, not by omission.** No refund, reversal or sale-correction structure exists in the schema (no negative quantity is permitted — `sale_line_items` carries `CHECK (quantity > 0)`), and adding one changes the sale-commit transaction (ADR-005) and the khata ledger (ADR-006), not just reporting. No test is owed in Phase 3. Recorded as a known limitation: a mis-entered sale cannot currently be corrected in-app, and any future correction feature must revisit this ADR because a reversal dated after the period it corrects would restate a closed period. |

## Alternatives Considered

- **Cash basis (revenue at payment).** Rejected. It is the likelier intuition
  for a kirana owner and is named as such in `docs/registry/phase3/README.md`,
  so it is rejected on a mechanical ground rather than a preference: khata
  payments carry `sale_id = NULL` (ADR-006 Decision (b)), so a repayment
  cannot be attributed to a sale, and therefore cannot be matched to the line
  items whose `unit_cost` supplies COGS. A cash-basis P&L would show COGS in
  the period of sale and revenue in the period of payment, producing periods
  with cost and no revenue. Making it computable requires a payment-to-sale
  allocation model (FIFO allocation across a customer's open sales, or a
  per-sale settlement ledger) — a larger change than SCOPE-04 covers. The
  `credit_sales` disclosure in (a) is the mitigation.
- **Hybrid: accrual P&L with a separate cash-collected summary.** Rejected
  for Phase 3 for the same reason — the cash figure per period is computable
  in total (`SUM(credit_transactions.amount) WHERE transaction_type =
  'payment'`) but cannot be split by product, cost or discount, so it cannot
  be a P&L column. If the owner asks for a collections view later it belongs
  in a separate khata report, not in the P&L.
- **Naive `DATE(created_at)` / UTC grouping.** Rejected: misattributes every
  sale between 00:00 and 05:30 IST to the previous trading day — up to a
  quarter of a late-trading shop's day, silently.
- **`zoneinfo.ZoneInfo("Asia/Kolkata")` instead of a fixed offset.**
  Rejected: it requires the system tz database, which on Windows means adding
  the `tzdata` package — a new dependency (`architecture-rules.md`
  § Dependency Governance) for an offline single-install tool, against
  ADR-001's zero-extra-dependency stance. India has observed no DST since
  1945, so a fixed +05:30 offset is lossless today. See Scaling Limit for
  when this stops being true.
- **Storing local (IST) timestamps in `created_at`.** Rejected: would require
  restating every historical row, and loses the single unambiguous instant
  representation that makes ordering and comparison correct.
- **Per-user timezone profiles (a login/profile system carrying an
  individually configurable timezone per user).** Rejected — not for being
  premature (the shop-level setting itself is no longer rejected; see
  Decision (b), revised 2026-08-10 per the owner's ruling on action 16), but
  because there is no user, login, session, or profile concept anywhere in
  this codebase to hang it on. `architecture.md` states three times that this
  is a single-user, single-install tool (line 13: "a local, single-user
  accounting tool"; line 121: "SQLite single-writer model — Single concurrent
  user (by design)... a multi-user redesign (server DB) would be required if
  scope changes"; ARCH-P01: "1 (per installation)"), and no settings, profile,
  or session table exists in `src/db/schema.py`. Building a profile system to
  carry one shop-wide value would be a materially larger and architecturally
  contradictory change relative to the value it stores. A single shop-level
  configurable setting (Decision (b)) achieves the actual requirement — one
  timezone, settable without a code change — without inventing a user
  concept the rest of the application does not have.
- **A new `settings` database table for this one value.** Rejected: no
  settings/config table exists anywhere in `src/db/schema.py` today, and
  introducing one for a single value means a new migration pair
  (`0005_up.sql`/`0005_down.sql`, competing with ADR-009's own migration
  0005 — see that ADR), a new read/write service path, and a new place a
  "which value wins" question can arise (DB row vs. code default) where none
  exists today. The environment-variable-override shape in Decision (b)
  reuses `src/config.py`'s own established idiom (`DEFAULT_DB_PATH` /
  `get_db_path()` / `SMB_DB_PATH`) exactly, needs no schema change, and is
  editable by the shop owner (setting an environment variable, or a future
  wrapper script) without a database write path. **Cost to reverse later:**
  low — if a settings table is introduced for other reasons in a later
  phase, `get_reporting_tz()`'s body is the only place that changes; its
  call sites do not.
- **Indian fiscal year (Apr–Mar, Q1 = Apr–Jun).** Rejected: it is the year the
  shop's accountant files on and was the architect's original proposal (this
  ADR's initial `Proposed` draft), but the owner overturned it at the action-3
  gate in favour of the calendar year — the more common software default and
  a defensible reading of SCOPE-04's plain wording. **Cost to reverse later:**
  low — reversing back to the fiscal year touches one constant (the
  year-start month) and the period-label strings only; no schema, query
  shape, or stored value depends on which convention is active.
- **Inclusive upper bound (`<=` with a last-millisecond value).** Rejected:
  requires constructing `23:59:59.999`, which silently drops any sale in the
  final sub-millisecond and breaks if `created_at`'s precision ever changes.
  Half-open bounds have neither failure mode.
- **Reporting GST collected as revenue.** Rejected: GST is collected on
  behalf of the government, not earned; including it would overstate revenue
  by the GST rate and make `net` meaningless.

## Consequences

- **Easier:** the six period-boundary assertions in
  `docs/registry/phase3/README.md` §2 become writable with a stated expected
  value each, closing the "test author decides the ADR by default" risk that
  section names.
- **Easier:** half-open, converted-bound text comparison means one query
  shape serves all four grains — only the two bound values differ — so
  daily/monthly/quarterly/annual are one code path with four bound
  calculators, not four aggregation implementations.
- **Harder:** the trading-day conversion must be applied consistently. Any
  future query that filters sales by date and skips `get_reporting_tz()` will
  disagree with the P&L by up to 5.5 hours of trading. The existing
  `list_sales()` ordering is unaffected (it orders, it does not bucket), but
  any future date filter on it inherits this obligation.
- **Harder:** accrual means a period can report healthy profit while no cash
  has been collected. The `credit_sales` disclosure makes this visible, but
  it is a real interpretation burden on the owner and was accepted knowingly.
- **Compliance posture.** *Data classification:* the report aggregates
  `sales` / `sale_line_items` — Internal per `architecture.md` ARCH-D02 — and
  contains **no PII**; `customers.name`/`phone`/`address` (ARCH-D01) are not
  read by the reporting path, and no customer identity appears in any report
  field in (d). The `credit_sales` figure is an aggregate, not a per-customer
  balance, so ARCH-D05's log-masking obligation is not engaged. *Tenant
  isolation:* not applicable — single shop, single user, single local file
  (ARCH-P01). *Audit trail:* the reporting path is read-only and mutates no
  business entity, so `architecture-rules.md` § Audit Trail imposes no event
  here; the read itself is not audited, which is acceptable for a
  single-owner local tool with no confidentiality boundary between users.
- **Multi-system impact analysis** (cross-cutting: this decision governs
  every period-scoped query the project will ever write):
  - **Availability impact:** none beyond this single local process — there
    are no downstream systems (`architecture.md` §4 records exactly one
    boundary, service layer → SQLite). The only user-visible effect is the
    time to render a report screen; see Scaling Limit.
  - **Blast radius:** worst case, a wrong bound calculation reports incorrect
    profit figures that look plausible — the failure is silent and the owner
    may act on it (pricing, stock, tax). This is why the boundary cases are
    mandatory tests rather than advisory. No data corruption is possible: the
    reporting path performs no writes, so a defect here cannot damage the
    ledger, only misreport it.
  - **Rollback path:** the decisions are code-level and data-free — nothing
    here changes the schema or writes a stored value, so reversal is editing
    `DEFAULT_REPORTING_TZ_OFFSET_MINUTES` (or the `SMB_REPORTING_TZ_OFFSET_MINUTES`
    override), the year-convention constant, or the basis query and
    re-running the report. No migration, no backfill, no restatement of
    stored data. If the owner overturns the basis or the year convention
    (e.g. back to the Indian fiscal year) after Phase 3 ships, previously
    rendered reports were never persisted, so there is nothing to reconcile.

## Scaling Limit

- **Fixed IST offset:** breaks if India adopts DST, if the tool is used
  outside IST, or if a multi-shop/multi-timezone scope ever lands (currently
  an explicit `scope.md` exclusion). Signal: any install whose owner is not
  in IST. Response: replace the fixed-offset value `get_reporting_tz()`
  returns with a configured IANA zone and accept the `tzdata` dependency.
- **Text-comparison bounds:** valid only while every `created_at` is written
  in the exact `%Y-%m-%dT%H:%M:%fZ` UTC form. If any future writer inserts a
  differently formatted or non-UTC timestamp, lexicographic comparison
  silently returns wrong rows. Any new insert path into `sales` must keep the
  column default.
- **Aggregation volume:** an annual report scans a year of `sale_line_items`
  joined to `sales`. At the low-hundreds-of-transactions-per-day scale in
  `architecture.md` §1 this is tens of thousands of rows and is comfortable.
  `architecture.md` §8 already names the mitigation — "Add indexes on hot
  query columns (date, product, customer) if reports slow down". Concretely:
  beyond roughly 100,000 `sale_line_items` rows, or if the proposed
  ≤1.0 s annual-report budget in `docs/registry/phase3/README.md` §6 is
  missed, add an index on `sales(created_at)`. That budget is itself marked
  proposed-not-derived and is not a gate today.
- **Accrual-only reporting:** breaks down as a management tool if credit
  sales grow to dominate turnover, at which point the owner needs a
  collections view. Signal: `credit_sales` approaching total revenue.
  Response: a separate khata/collections report, plus the payment-to-sale
  allocation model this ADR rejected.
