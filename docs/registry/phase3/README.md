# Phase 3 — Reporting

**Goal:** Deliver P&L reporting across daily, monthly, quarterly, and annual periods, aggregating sales, cost, and discount data from Phases 1–2.

**Deliverables:**
- P&L reporting — daily/monthly/quarterly/annual profit & loss aggregation and display

**DoD Conditions:** See [project-plan.md Phase 3 DoD Conditions](../../../framework/project/project-plan.md)
Run `bash framework/harness/phase-check.sh` to verify.

**Features:** <!-- list feature dirs when created -->

---

## Phase 3 Planning Gates

Written at the Phase 2→3 boundary (2026-08-09, plan revision 5) by project-manager. These are conditions on the Phase 3 planning run itself — check them while writing task files, not after.

### Gate 1 — the Curses navigation-shell obligation needs a dedicated task row

`project-plan.md`'s Cross-Cutting Consumption Obligations table marks this obligation `satisfied` for `DLVR-3-01` as of plan revision 5:

> Curses main-menu navigation shell — every screen must be reachable through it and follow its consistent key bindings (arrows/Tab move, Enter select, Esc/q back)

`phase-start-protocol.md` Step 2 Coverage Check 5 therefore binds on this phase: the obligation's identifying phrase must appear **verbatim in at least one task row's `Cites` cell** inside the consuming feature's `tasks.md` — not only in the `DLVR-3-01` row of `project-plan.md`. Step 2's per-feature rules require it to be a **dedicated row** ("Consumed-obligation tasks are dedicated rows, never folded into a citation"), whose `Task` states the integration point directly — e.g. reaching the reports screen from the main menu's existing Reports entry with the standard key bindings — and whose `Cites` names both the feature's `PHFEAT-NN` parent and the phrase above, quoted verbatim.

**Why this gate exists, recorded rather than assumed.** `grep -rln "Curses main-menu navigation shell" docs/registry/` returns **nothing**. The phrase appears in no task row anywhere in Phases 1 or 2, although `project-plan.md` marks the obligation consumed by `DLVR-1-02`, `DLVR-1-03`, `DLVR-2-01` and `DLVR-2-02` at plan level. Check 5 would have failed for both phases had it been run. Neither phase is retro-fixable — both are signed off and tagged (`phase1-*`, `phase2-stable`) — so the correction available is to not repeat it here. Citation at plan grain is not consumption; a task row is.

### Gate 2 — conditional: the `FormController` obligation

`project-plan.md`'s second obligation — "In-house field-navigable form entry (`FormController`) — every multi-field data-entry screen must use it for field-to-field navigation instead of a linear cancel-on-Esc-only prompt sequence" — names only `DLVR-2-01` and `DLVR-2-02` as consumers, and both carry verbatim task rows (`P2-F1-T5`, `P2-F2-T5`). It does not bind on Phase 3 as the table stands. It becomes relevant only if a Phase 3 screen introduces multi-field data entry (a period-selection form with several fields would qualify); in that case the obligation table gains a consumer and the same dedicated-row rule applies.

---

## Carried Forward to Phase 3 Planning

Recorded at the Phase 2→3 boundary (2026-08-09, plan revision 5) by project-manager. These are inputs the Phase 3 planning run must consume before it writes task files. Neither was produced here — the boundary run deliberately decided nothing about Phase 3's content.

### 1. The architect authors a P&L aggregation ADR up front

Owner: **architect**. It must be `Accepted` — not drafted — **before the first Phase 3 task line is written**, per `framework/guidelines/development-process.md` §8: "if any ADR governing this feature is not yet Accepted, resolve it before writing the first task line."

Three decisions, each with a wrong-by-default answer if an implementer picks silently:

- **Accounting basis — accrual or cash?** `sales.payment_status` separates `paid` from `credit`, and `credit_transactions` records later repayments. Accrual (revenue at sale) and cash (revenue at payment) produce different figures for the same period. Nothing in `scope.md`, `architecture.md` or ADR-001…007 states which the report means. A kirana owner's intuition is usually cash; the schema's shape suggests accrual.
- **Day and period boundary, including timezone.** Every `created_at` is written by `strftime('%Y-%m-%dT%H:%M:%fZ','now')` — UTC — and `src/services/sales.py` relies on that column default rather than supplying a local timestamp. A shop's trading day is IST (UTC+5:30), so a naive `DATE(created_at)` grouping attributes every sale between 00:00 and 05:30 IST to the previous trading day.
- **Calendar year or Indian fiscal year (April–March)?** "Quarterly" and "annual" in SCOPE-04 are ambiguous between the two. The fiscal year is the one a shop's accountant uses.

### 2. ux-designer participates before any Phase 3 task file is written

Phase 3 delivers a user-facing report screen, so `phase-start-protocol.md` Step 1's standing UX open decision applies: page/flow decomposition, state coverage (loading/empty/error), named interaction-richness detail per page, and — if no global style guide exists yet — the style guide itself. Step 2 then requires UI tasks to be decomposed **per page/flow, never a single aggregate row**: "A feature whose only UI plan is a single 'implement frontend UI' task has not resolved this decision."

Route this through the Step 1 delegation rule — project-manager identifies the decision and invokes ux-designer; ux-designer proposes; the user confirms; the resolution is recorded in this README under a `## Phase Planning Decisions` section citing the proposing persona. Project-manager does not author the UX content.

### 3. The same up-front ADR decides cost capture — three further decisions

Opened by the user's Option A ruling at the Phase 2→3 boundary gate (2026-08-09), which added `DLVR-3-02` (product cost capture and per-line COGS snapshot) to Phase 3. Owner: **architect**. Same condition as item 1 — `Accepted` before the first Phase 3 task line. The architect may split this into a second ADR; what matters is that these decisions exist before decomposition, not how many files hold them.

- **Where the cost/purchase-price field lives on the product catalog.** `products` today carries `name`, `price` (selling), `gst_rate`, `created_at`, `updated_at` — no cost column, and migrations 0001–0004 add none. The ADR names the column, its type and its nullability, and must comply with the migration discipline ADR-004 already establishes (`NNNN_up.sql` / `NNNN_down.sql` authored and exercised together, mirrored into `SCHEMA_STATEMENTS`).
- **A per-line COGS snapshot is written onto `sale_line_items` at sale time.** This is the recommendation carried into Phase 3, with its reasoning, for the ADR to rule on: product cost is mutable (catalog edit exists; `updated_at` exists because rows are updated), so a P&L that joins current cost at report time silently restates historical profit every time the owner corrects a purchase price — invisibly, in a tool whose entire purpose is period reporting. Snapshotting follows the pattern `sale_line_items` already uses for `unit_price`, `gst_rate` and `discount_amount` under ADR-005, rather than introducing a new one.
- **How sales recorded before the migration report.** Any already-installed database has sale rows whose cost is unrecoverable. The ADR must state the disposition — report those periods as cost-incomplete, or treat null cost as zero and label it — because the silent default (treating null as zero) under-counts COGS and overstates profit without any signal to the reader.

---

## Phase 3 Test Strategy

Written at the Phase 2→3 boundary (2026-08-09) by **test-manager**, space `phase3-boundary-revalidation` action 9. This is the strategy Phase 3 decomposition must satisfy — it names layers, cases, and numeric floors. It writes **no task rows and no feature breakdown**; those belong to the separate Phase 3 planning run.

**Status:** proposed; pending the action-10 user confirmation. Nothing here is a `project-plan.md` DoD condition unless the user rules that it should be.

### 1. Test layers and pass criteria

| # | What must be tested | Layer | Owner deliverable | Pass criteria |
|---|---|---|---|---|
| T-1 | Cost/purchase-price column added to `products` — type, nullability, default | Unit (`tests/unit/db/test_schema.py`) | DLVR-3-02 | Column present in `SCHEMA_STATEMENTS` with the ADR's declared type and nullability; a product row inserted without a cost behaves as the ADR states (rejected, or null-and-labelled) |
| T-2 | Migration `0005` up **and** down | Unit (`tests/unit/db/test_migrate.py`) | DLVR-3-02 | `up` applied to a Phase-2-shaped database yields the new column and preserves every existing row; `down` restores the Phase-2 shape without data loss on untouched tables; both exercised in the same test per ADR-004 |
| T-3 | Catalog create/edit accepts and persists cost | Unit (`tests/unit/services/test_catalog.py`) | DLVR-3-02 | Round-trip read returns the written cost; an invalid cost (negative, non-numeric) raises the project's domain exception from `src/exceptions.py`, not a raw `sqlite3` error |
| T-4 | Per-line COGS snapshot written onto `sale_line_items` at sale time | Unit (`tests/unit/services/test_sales.py`) | DLVR-3-02 | A sale line records the product's cost **as at sale time**; editing the product's cost afterwards leaves the recorded line unchanged. This is the regression test for the failure the snapshot exists to prevent — assert the *old* value survives, not merely that a column is populated |
| T-5 | Snapshot participates in the existing single-transaction commit | Integration (`tests/integration/`) | DLVR-3-02 | Built through `apply_pending_migrations()`, nothing mocked (the pattern `tests/integration/test_sales_transaction.py` establishes). A failure partway through leaves no partial write across `sales`, `sale_line_items`, `inventory_stock`, `credit_transactions` — including the new cost column |
| T-6 | P&L aggregation math — revenue, cost, discounts, net | Unit (new reporting service test module) | DLVR-3-01 | Each of the four figures asserted independently against a hand-computed fixture; a cart spanning multiple GST slabs with a discount asserted end-to-end, mirroring the Phase 2 `test_sales.py` mixed-rate convention |
| T-7 | All four period grains — daily, monthly, quarterly, annual | Unit (new reporting service test module) | DLVR-3-01 | One test per grain; each proves a sale **just outside** the window is excluded, not only that in-window sales are included. Exclusion is the assertion that catches an off-by-one range; inclusion alone does not |
| T-8 | Empty period | Unit | DLVR-3-01 | A period with zero sales returns zeroes and renders, rather than raising or returning `None` |
| T-9 | Pre-migration sales with unrecoverable cost | Unit **and** Integration | DLVR-3-01 | Behaviour matches the ADR's ruling (cost-incomplete, or zero-and-labelled) and the label is observable in the returned data — not left to the UI to infer. Integration builds the fixture by migrating a database that already holds Phase-2 sales |
| T-10 | End-to-end report generation over a migrated database | Integration | DLVR-3-01 | Real SQLite file, migration-runner bootstrap, no mocks; sales seeded through the real `record_sale()` path; the report figures reconcile to the seeded data |
| T-11 | Reports screen reachable from the main menu with the standard key bindings | Unit (`tests/unit/ui/`) | DLVR-3-01 | Driven by a fake key queue, no real terminal (the `tests/unit/ui/test_main_menu.py` pattern). Arrows/Tab move, Enter selects, Esc/q returns to the menu. This is the test-side counterpart of Planning Gate 1 above |
| T-12 | Report screen loading / empty / error states | Unit (`tests/unit/ui/`) | DLVR-3-01 | Each state renders without raising; the empty state is distinguishable from a zero-value state. Bind the exact states to the ux-designer output required by Carried Forward item 2 |
| T-13 | Owner demonstration of the report screen | Manual (PD-3-DEMO) | Both | `PD-3-DEMO` is `[Inferential]`, owner PM/PO. No automated test discharges it; it is listed here so the plan does not read as if the suite closes the phase |

### 2. Period-boundary cases — assertions are bound to the ADR, not chosen here

The three decisions in **Carried Forward item 1** (accounting basis, day/period boundary and timezone, calendar vs Indian fiscal year) are **not yet decided**, and no ADR under `docs/adr/` governs reporting. Each case below is a **required test**; its expected value is whatever the ADR rules.

**Writing these assertions before that ADR is `Accepted` would let the test author decide the ADR by default.** Per `development-process.md` §8 the ADR must be `Accepted` before the first Phase 3 task line, which makes it a precondition of this section, not a parallel activity.

| Case | Layer that owns it | Why it is wrong by default |
|---|---|---|
| A sale at 02:00 IST (= 20:30 UTC the previous day) | Unit — aggregation service | Every `created_at` is UTC (`strftime('%Y-%m-%dT%H:%M:%fZ','now')`, `src/db/schema.py`). A naive `DATE(created_at)` grouping attributes every sale between 00:00 and 05:30 IST to the previous trading day |
| A sale at 23:59:59.999 on the last day of a period | Unit — aggregation service | Inclusive vs exclusive upper bound; a `<` where `<=` is meant silently drops the last day's trading |
| A credit sale in period P repaid in period P+1 | Unit — aggregation service | Accrual counts revenue in P, cash counts it in P+1. `sales.payment_status` and `credit_transactions` make both computable, so the query shape alone will not reveal which was intended |
| A quarter and a year boundary | Unit — aggregation service | Calendar (Jan–Dec, Q1 = Jan–Mar) vs Indian fiscal (Apr–Mar, Q1 = Apr–Jun). Both are defensible readings of SCOPE-04; only one matches the shop's accountant |
| The same period requested twice | Integration | Report generation must be read-only and repeatable — re-running leaves the database identical. Every integration test in this phase must be idempotent |
| A partial refund or a corrected sale, if either exists in Phase 3 | Unit | Not currently in the schema. Listed so that its absence is a recorded decision rather than an oversight; if Phase 3 does not introduce it, no test is owed |

### 3. Coverage target

**Coverage target — per module, not global:**

- **≥ 90% line coverage for every module Phase 3 creates or modifies.** This includes the new reporting service module, the new reporting UI module, `src/db/schema.py`, the migration pair, `src/services/catalog.py` and `src/services/sales.py` — and `src/ui/main_menu.py` the moment the Reports entry point is wired into it.
- **≥ 92% line coverage for `src/` overall — no regression against the 2026-08-09 baseline.** The overall floor is set to the measured baseline, not to a round number, so a large under-tested new module cannot be absorbed by the existing average.

**Command that measures it:**

```bash
poetry run pytest tests/unit/ -q --tb=short --cov=src --cov-report=term-missing
```

`pytest-cov 7.1.0` is already in `pyproject.toml`.

**Enforcement status (updated 2026-08-09, PM/PO ruling, `phase3-boundary-revalidation` space action 28):**

- The **overall ≥92% floor is enforced** as `project-plan.md` Phase 3 DoD condition `PD-3-COV`, running `poetry run pytest tests/unit/ -q --cov=src --cov-fail-under=92`, owner backend-developer, added at plan revision 6. `phase-check.sh` executes it at every boundary run.
- The **per-module ≥90% floor is not enforced by any sensor.** `pytest-cov` has no per-module fail-under and `--cov-fail-under` asserts the whole-project total only. It remains a stated target, verified by reading the `--cov-report=term-missing` table above. This is a known gap, recorded rather than left implicit: a large under-tested new module can still pass `PD-3-COV` by riding the existing average, which is the precise failure the two-floor design was meant to prevent.
- Nothing is wired into `pyproject.toml`; the gate lives in the phase DoD table, where `phase-check.sh` reads it.

**Baseline measured 2026-08-09 (463 unit tests passing, TOTAL 92%, 1604 statements, 133 missed):**

| Module | Coverage | In Phase 3 floor? |
|---|---|---|
| `src/services/sales.py` | 100% | Yes — modified by the COGS snapshot |
| `src/services/inventory.py` | 100% | Only if modified |
| `src/ui/keybindings.py` | 100% | Only if modified |
| `src/db/schema.py` | 100% | Yes — cost column |
| `src/services/customers.py` | 98% | Only if modified |
| `src/db/migrate.py` | 98% | Yes — migration 0005 |
| `src/ui/customers.py` | 99% | Only if modified |
| `src/services/discounts.py` | 97% | Only if modified |
| `src/ui/inventory.py` | 96% | Only if modified |
| `src/services/catalog.py` | 94% | Yes — cost field |
| `src/ui/catalog.py` | 93% | Yes — cost field on the product form |
| `src/ui/sales.py` | 93% | Only if modified |
| `src/config.py` | 75% | Carved out unless modified |
| `src/db/backup.py` | 68% | Carved out unless modified |
| `src/ui/main_menu.py` | 65% | **In the floor — ruled 2026-08-09, not a carve-out.** See "Ruling" below |
| `src/main.py` | 52% | Carved out unless modified |

Three carve-outs remain — `src/config.py` (75%), `src/db/backup.py` (68%), `src/main.py` (52%). They are pre-existing Phase 1 debt. A Phase 3 gate that failed on them would either block this phase for work outside it or be waived wholesale, and a waived gate is worse than no gate. The `unless modified` clause is the load-bearing part.

**Ruling — `src/ui/main_menu.py` is raised to the floor as named Phase 3 work (2026-08-09, PM/PO, `phase3-boundary-revalidation` space action 29).**

The module measures **65%** — 34 of 97 statements uncovered (lines 111, 115–126, 131–158). Planning Gate 1 above requires the Reports screen to be reached through this exact main-menu shell, so Phase 3 modifies it and it enters the ≥90% per-module floor.

Two dispositions were put to the PM/PO: raise it to ≥90% as named Phase 3 work (visible cost, correct coverage), or carve it out by name with the 65% figure and the reason on the record (visible exception, honest gate). **The carve-out was offered and declined.** The ruling was: raise it.

What this obligates, before Phase 3 decomposition is considered complete:

- Phase 3 carries a **dedicated task row** to bring `src/ui/main_menu.py` from 65% to ≥ 90%, owned by the persona that wires the Reports entry point. It is a task in its own right, not an implicit rider on the wiring task, and not a gate-time judgement call.
- The obligation is **unconditional**, not contingent on when the Reports wiring lands. Making it conditional would reintroduce exactly the "carved out **until** …" formulation this ruling replaced.
- Phase 3 therefore absorbs roughly 25 points of pre-existing Phase 1 coverage debt in a module it otherwise touches only to add one menu entry. That cost was stated when the ruling was made and is recorded here so it is not rediscovered as a surprise during decomposition.

The option deliberately rejected was the third one: letting the floor apply silently and waiving it at the gate. That is the Phase 1 failure this section exists to prevent — `docs/registry/phase1/feature3-catalog-pricing/tasks.md:66` records `src/ui/catalog.py` shipping at 45% against a stated ≥80% target, waived because no sensor read the target.

### 4. External-integration error paths

`architecture.md` §4 records exactly one boundary — `ARCH-B01 | Service layer | SQLite DB`. There is no network call, no third-party service, and no other external system in this stack.

`definition-of-done.md` Tier 1 requires an error-path test for every function that calls an external service. For Phase 3 that resolves to: **every new reporting service function that issues a query must have a test that makes the `sqlite3` call raise, and asserts the project's own domain exception from `src/exceptions.py` propagates — not a bare `sqlite3.Error`.** The `IntegrityError`-to-domain-exception convention in `tests/unit/services/test_catalog.py` is the pattern to follow. No service module in this codebase logs, so the "asserts an error log event is emitted" clause of `development-process.md` §3 has no log event to assert at this layer — the same disposition recorded for `P2-F2-T6`.

### 5. Contract tests

**None required — count is zero, and that is a finding, not an omission.** `architecture.md` §4 states verbatim: "No inter-service or external-system boundaries exist — this is a single local process with one in-process service layer talking to a local SQLite file. The only internal boundary is layering (UI → services → DB), not a network or process boundary, so no versioned contract/ADR is required." If Phase 3 later introduces an export, print, or sync integration, that adds a boundary and this line stops being true.

### 6. Performance

**No public endpoints exist**, so the standard Endpoint / P95 / throughput / load-test table does not apply to this architecture. The substitute concern is real: annual P&L aggregates a full year of `sale_line_items` in a single-user CUI where a slow screen reads as a hang.

**Proposed budget — awaiting user confirmation at action 10, not a gate today:**

| Operation | Budget | Data volume | Test type | Pass/fail |
|---|---|---|---|---|
| Annual P&L aggregation | ≤ 1.0 s from period selection to rendered summary | 50,000 `sale_line_items` rows across 12 months | Seeded local timing test in `tests/integration/` | Median of 5 runs under budget; a run over budget fails |
| Daily P&L aggregation | ≤ 200 ms | Same seeded database | Same | Same |

**No numeric latency budget exists anywhere in this project** — not in `architecture.md`, `project-context.md`, `scope.md`, or any ADR. These figures are proposed by test-manager from the interaction class (a keypress-driven local screen), not derived from a recorded requirement. They are marked proposed for exactly that reason.

### 7. Deliberately not covered

Stated so that absence is a decision on the record rather than a gap:

- **`curses` terminal rendering itself.** UI tests drive a fake key queue and assert controller state; no real terminal is instantiated. Established by `tests/unit/ui/` across Phases 1–2 and not revisited here.
- **The four carved-out modules above**, unless Phase 3 modifies them.
- **Cross-year and cross-timezone historical restatement**, beyond the boundary cases in §2 — the report reads a single period at a time and no restatement feature is in scope.
- **Concurrent access.** Single-user local process (`architecture.md` §4); no multi-writer or locking test is owed.
- **Backup/restore interaction with the new column beyond a single case.** `src/db/backup.py` must survive the migration — one integration case covers it; raising `backup.py`'s own 68% coverage is Phase 1 debt, not Phase 3 work.
- **Any performance figure as a gate**, until §6 is confirmed.

---

## Phase Planning Decisions

Resolved 2026-08-10 at `framework/spaces/phase3-start.md` action 3, across three presentation rounds. Proposer for every decision below is **architect** (`docs/adr/ADR-008-pl-reporting-period-semantics.md`, `docs/adr/ADR-009-product-cost-capture-cogs-snapshot.md`, both authored at action 2). Confirmation is the owner's response, relayed by the coordinator into this space each round. Both ADRs now read `Status: Accepted`.

### Group A — reporting period semantics (`ADR-008`)

| # | Decision | Proposed | Resolution | Owner response |
|---|---|---|---|---|
| A1 | Accounting basis | Accrual — revenue recognised at the sale | **Accepted as proposed.** No ADR change. | `"A1 - sale"` |
| A2 | Reporting timezone mechanism | Fixed `REPORTING_TZ` constant, UTC+05:30 | **Redirected.** Owner chose a single shop-level configurable setting (not a per-user profile — none exists in this codebase). Architect drafted the revision at action 17 (`get_reporting_tz()` / `SMB_REPORTING_TZ_OFFSET_MINUTES` override on `DEFAULT_REPORTING_TZ_OFFSET_MINUTES = 330`, reusing `src/config.py`'s `get_db_path()` idiom); default value (IST, UTC+05:30) and the half-open trading-day bounds are unchanged. | `"A2 - store as UTC. add timezone to the user profile."` → clarified via action 16 to option (i); final wording approved with `"ok"` |
| A3 | Calendar year vs Indian fiscal year | Indian fiscal year, Apr–Mar | **Redirected.** Owner chose the calendar year (Jan–Dec, Q1 = Jan–Mar), the more common software default. Architect drafted the revision at action 15; the Indian fiscal year is the recorded rejected alternative. | `"A3 - Calendar year"` → drafted wording confirmed in a later round |

### Group B — product cost capture (`ADR-009`)

| # | Decision | Proposed | Resolution | Owner response |
|---|---|---|---|---|
| B1 | Cost column location/type/nullability | `products.cost NUMERIC`, nullable, no default, GST-exclusive | **Accepted as proposed.** | `"ok"` |
| B2 | Snapshot vs join-at-report | Per-line snapshot on `sale_line_items.unit_cost`, written at sale time, line COGS derived not stored | **Accepted as proposed.** | `"ok"` |
| B3 | Unknown-cost disposition | Cost-incomplete (COGS sums non-NULL lines only; `lines_missing_cost`/`cost_complete` returned as data; no backfill) | **Accepted as proposed.** | `"ok"` |

### Group C — report output contract (`ADR-008` Decision (d))

Architect-derived — no existing document defined this shape prior to this ADR. The eleven-field frozen dataclass (`period_label`, `period_start_utc`, `period_end_utc`, `revenue`, `discounts`, `cogs`, `net`, `credit_sales`, `line_count`, `lines_missing_cost`, `cost_complete`) is **accepted as proposed**, confirmed with `"ok"`.

### Group D — Phase 3 feature breakdown, UX resolution, and story treatment

Resolved 2026-08-10 at `framework/spaces/phase3-structure.md` action 2. Proposer for the feature breakdown is **project-manager**; proposer for the UX decomposition is **ux-designer** (action 1, `docs/design/style-guide.md` and the three-page reporting proposal reproduced in that space's Reference notes). User confirmed both as proposed, and ruled on story treatment, in a single response.

| # | Decision | Proposed | Resolution | Owner response |
|---|---|---|---|---|
| D1 | Feature breakdown | Two features: **Feature 1 — Product Cost Capture & COGS Snapshot** (cites `DLVR-3-02`, governing `ADR-009`, depends on: none) and **Feature 2 — P&L Reporting** (cites `DLVR-3-01`, governing `ADR-008`, depends on Feature 1 — consumes the COGS snapshot) | **Accepted as proposed.** No redirect. | Confirmed. |
| D2 | UX decomposition | Three pages under Feature 2 — Period-Type Menu, Period Selector, Report Summary — per action 1's proposal (loading/empty/error/cost-incomplete state coverage, named interaction-richness detail per page, key bindings consistent with the DLVR-1-01 main-menu shell) | **Accepted as proposed.** No redirect. | Confirmed. |
| D3 | Story treatment | Neither feature can cite a `STORY-*` row — `framework/project/user-stories.md` has not been populated for this project (Phases 1–3 alike carry the same absence). Two routes offered: (a) accept an `N/A` marker per feature and carry a backfill item forward to the next plan boundary, or (b) draft `STORY-*` rows for both features now. | **Route (a) accepted.** Both features carry the marker below; no `STORY-*` rows drafted in this command. Backfill carried forward — see `## Plan Learning` below. | "accept the N/A marker ... carry a backfill item forward to the next plan boundary" |

**Feature 1 — Product Cost Capture & COGS Snapshot**
Cites: `DLVR-3-02` — "Product cost capture and per-line COGS snapshot ... Sequenced before DLVR-3-01, which consumes the snapshot." Governing ADR: `ADR-009` (Accepted 2026-08-10).
Stories: N/A — user-stories.md has not been populated for this project.

**Feature 2 — P&L Reporting (Period-Type Menu → Period Selector → Report Summary)**
Cites: `DLVR-3-01` — "P&L reporting — daily/monthly/quarterly/annual profit & loss aggregation and display, sourced from sales (DLVR-2-01) and the per-line cost snapshot delivered by DLVR-3-02. Reached through DLVR-1-01." Governing ADR: `ADR-008` (Accepted 2026-08-10). Depends on Feature 1.
Stories: N/A — user-stories.md has not been populated for this project.

### Ceiling note

This command's 10-turn call-log ceiling was explicitly raised by the owner (2026-08-10) once it became clear that closing this gate required all three groups presented, not only Group A — see `framework/spaces/phase3-start.md` § Open items, "ceiling raised."

## Open Documentation Items

Routed here from `framework/spaces/phase3-start.md` so they receive attention outside that relay. Neither carries an action row in the phase3-start command — the first is deferred by the 2026-08-10 command-split ruling, the second is Low severity and out of that command's trimmed scope.

| Item | Severity | Owner | Done when |
|---|---|---|---|
| `docs/adr/README.md`'s index table lists only ADR-001 — ADR-002…ADR-009 are absent — and its stated naming convention (`NNN-kebab-title.md`) does not match the `ADR-NNN-kebab-title.md` form every actual file uses. Documentation drift, no functional impact. | Low | technical-writer | `docs/adr/README.md` lists every file in `docs/adr/` with title, status and date, and its naming line matches the actual convention. Do not action before the phase3-start action-3 gate closes — ADR-008 and ADR-009 statuses change at that gate. |

## Plan Learning

Routed here from `framework/spaces/phase3-structure.md` action 2 (2026-08-10) per the owner's explicit ruling on story treatment (route (a): accept the `N/A` marker, carry a backfill item forward). This is a plan-completeness gap, not a Phase 3 task — it must not be actioned inside this command; `plan-protocol.md` P2 drains this section at the next phase boundary.

| Item | Scope | Owner | Drain point |
|---|---|---|---|
| `framework/project/user-stories.md` has never been populated for this project — only the unfilled template exists. Every `DLVR-*` across Phases 1, 2, and 3 (`DLVR-1-01`, `DLVR-1-02`, `DLVR-1-03`, `DLVR-2-01`, `DLVR-2-02`, `DLVR-3-01`, `DLVR-3-02`) carries a `Stories: N/A — <reason>` marker instead of a resolved `STORY-*` row. This is a pre-existing, project-wide gap, not something introduced by Phase 3. | Backfill `user-stories.md` with `STORY-*` rows for all confirmed `DLVR-*` tags to date | project-manager | Next `plan-protocol.md` P2 run (Phase 3→4 boundary revalidation), step 0 — "backfill a missing file" per the project-manager persona's standing responsibility |
