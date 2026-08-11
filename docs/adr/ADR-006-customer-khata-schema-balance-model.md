# ADR-006: Customer/Khata Schema and Balance-Derivation Model

**Status:** Accepted
**Date:** 2026-08-02

## Context

`framework/project/architecture.md` `ARCH-F04` describes the customer
credit (khata) flow narratively only: "Main menu → Customers → select
customer → view outstanding balance / record payment against balance" —
it does not decide the `customers` table schema, a uniqueness key, or how
the outstanding balance is derived. No ADR in `docs/adr/` covers this.
`ARCH-D01` classifies customer name/phone/address as PII ("Log masking:
Yes — never logged in full") and `ARCH-D05` classifies the derived khata
balance as internal data derived from PII-linked customer + transaction
data. `framework/harness/sensor-debt-registry.md` SD-004 (status `Open`)
requires a grep-based PII-log-masking check in `.claude/hooks/stop.sh`
before this module's logging code is written.

This ADR closes both the schema/balance-model silence gap and the SD-004
sensor debt, identified by the architect's cross-phase integrity check
(`framework/spaces/phase2-scope-planning.md`, Open items, action 1) and
corresponds to that space's action 5
(`author-adr-khata-schema-and-close-sd004`) and task registry entry
`P2-F2-T1` (`docs/registry/phase2/feature2-customer-credit-khata/tasks.md`).

## Decision

### (a) `customers` schema and uniqueness key

```sql
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT NOT NULL UNIQUE,
    address TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
```

`phone` is the uniqueness key — a kirana shop owner identifies a regular
credit customer primarily by phone number in practice; `name` alone
collides across distinct customers with the same common name, and
`address` is optional free text that not every customer needs to supply
credit. A customer who is to be extended credit must be reachable, so
`phone` is `NOT NULL`; `address` remains nullable.

### (b) Balance-derivation model — ledger of transactions

```sql
CREATE TABLE IF NOT EXISTS credit_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers (id),
    sale_id INTEGER REFERENCES sales (id),
    transaction_type TEXT NOT NULL CHECK (transaction_type IN ('credit_sale', 'payment')),
    amount NUMERIC NOT NULL CHECK (amount > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
```

The outstanding balance is **not** a stored, mutated column — it is always
derived by aggregating this ledger:

```
outstanding_balance(customer_id) =
    SUM(amount WHERE transaction_type = 'credit_sale') -
    SUM(amount WHERE transaction_type = 'payment')
    ... for that customer_id
```

- `sale_id` is nullable — a `credit_sale` row is created by
  `ADR-005`'s sale-commit transaction (linking back to the `sales` row
  that created the debt) and a standalone `payment` row (not tied to any
  specific sale — the customer is simply paying down their balance) has
  `sale_id = NULL`.
- `amount` is always positive; `transaction_type` determines its effect on
  the balance (credit_sale increases what is owed, payment decreases it).
- `get_outstanding_balance(conn, customer_id)` in `src/services/customers.py`
  computes the derived value via a single SQL aggregate query — no
  balance value is cached or stored anywhere else.

Every credit-affecting event is its own row with its own `created_at`,
which directly extends `SD-002`'s audit-trail concern (its residual note,
recorded at Phase 1 closure, explicitly names sales/credit mutation code as
needing audit-field coverage once it is written) without building a
separate audit log alongside a running counter.

### (c) `commit` parameter convention (consistency with `ADR-005`)

The credit-balance-update function this ADR authorizes for
`src/services/customers.py` (e.g. `record_credit_sale()`,
`record_payment()`) MUST accept an optional `commit: bool = True`
parameter, following the same pattern `ADR-005`(c) establishes for
`src/services/inventory.py` — this allows `ADR-005`'s sale orchestrator to
compose a credit-balance insert into its single atomic sale-commit
transaction (`commit=False`, deferring the actual commit to the
orchestrator) while preserving today's simple single-call behaviour by
default for any standalone credit/payment recording screen.

### (d) SD-004 closure — PII-log-masking sensor

`ARCH-D01` names exactly three PII fields for this project: customer
`name`, `phone`, `address`. `.claude/hooks/stop.sh` gains a new gate
(gate 10, `TOTAL_GATES` bumped to 10) that greps `src/` for a logger call
passing one of these fields directly as an argument:

```bash
echo "[10/${TOTAL_GATES}] No raw customer PII in log calls (SD-004)..."
if grep -rnE 'logger\.[a-z]+\([^)]*customer\.(name|phone|address)' src/ --include="*.py" 2>/dev/null; then
    FAILED_GATES+=("PII in logs: raw customer PII field (name/phone/address) passed directly to a logger call — mask before logging, per ARCH-D01")
else
    echo "  ✓ No raw PII field passed to logger calls"
fi
```

This is a real enforcing gate (fails the DoD gate run if a raw PII field
reaches a logger call), not a marker comment — it satisfies both SD-004's
literal `check_command` (`grep -q "PII|pii" .claude/hooks/stop.sh`) and its
"Expected sensor" description ("A grep-based check flagging PII field
names... near logging calls"). `framework/harness/sensor-debt-registry.md`
SD-004's row is updated to `Status: Resolved` as part of this ADR's
closure.

### Gate 2 — ADR Adherence Protocol checks (`agent-conduct.md`)

- **Scope gap:** no existing ADR makes this decision; `ARCH-F04` states the
  flow narratively only. Genuinely new decision.
- **Silence gap:** schema, uniqueness key, and balance-derivation model
  were all previously undecided — all three resolved here.
- **Conflict:** `docs/adr/` (`ADR-001`–`ADR-005`) reviewed; no
  contradiction — none decides customer schema or balance derivation.
  `ADR-005`(c)'s `commit` parameter convention is extended here, not
  contradicted, by (c) above.
- **Consumer gap:** components named: `src/services/customers.py` (write
  path, `P2-F2-T2`/`T3`), `src/ui/customers.py` (display,
  `P2-F2-T5`), `src/services/sales.py` (`ADR-005`, composes a
  `credit_sale` row into its atomic transaction when `payment_status =
  'credit'`), and P&L/reporting (future read path for outstanding dues
  totals, not yet implemented).

## Multi-System Impact Analysis

- **Availability impact:** none beyond this single local process — no
  downstream systems exist in this stack.
- **Blast radius:** the new stop.sh gate 10 runs against all of `src/` on
  every DoD check — a false positive would require any legitimate log
  call touching a variable literally named `customer.name`/`.phone`/
  `.address` to instead pass a masked/derived value (e.g. a customer ID or
  a redacted display string), which is precisely the behaviour SD-004
  requires; this is the intended constraint, not an unintended side
  effect. No existing Phase 1 code touches customer data, so this gate
  introduces no immediate failures against current `src/`.
- **Rollback path:** this decision is additive — new tables (`customers`,
  `credit_transactions`), new service module, new exceptions, one new
  stop.sh gate. Reverting is: drop the new tables/migration pair, remove
  `src/services/customers.py`, remove the stop.sh gate (reverting
  `TOTAL_GATES` to 9) and revert the SD-004 registry row to `Open`. No
  data migration of existing tables required in either direction.

## Alternatives Considered

- **`name` as the uniqueness key** — rejected; common-name collisions
  would silently merge distinct customers or block valid registrations.
- **Synthetic external customer ID (e.g. shop-assigned card number)** —
  rejected; no requirement grounds this (`ARCH-F04` names no ID scheme),
  and phone number already serves this role in the real-world workflow.
- **Stored running `balance` column on `customers`, mutated per
  transaction** — rejected; no audit trail of individual events without
  also building a ledger anyway (duplicating effort), and a bug or partial
  write could leave an unreconstructable balance, conflicting with
  `architecture.md` §1's stated data-consistency quality attribute.
- **Hybrid: ledger plus a cached balance column kept in sync** — rejected
  as unneeded complexity at kirana-shop data volumes (tens to low hundreds
  of customers, per `architecture.md` §8), where a `SUM()` aggregate query
  has no meaningful performance cost.
- **Marker-comment-only stop.sh gate (bare "PII" string, no enforcing
  logic)** — rejected; technically satisfies the literal `check_command`
  string match but not SD-004's actual purpose, leaving an actual PII leak
  into logs uncaught.

## Consequences

- `src/services/customers.py` becomes the single owner of balance
  derivation — any future code needing a customer's outstanding balance
  must call `get_outstanding_balance()` rather than re-deriving it, to
  avoid divergence.
- Every credit-affecting event is independently auditable via
  `credit_transactions`, extending SD-002's audit-trail coverage to
  Phase 2 credit mutation code without additional plumbing.
- `.claude/hooks/stop.sh` now computationally enforces the PII-log-masking
  rule this project committed to at `ARCH-D01`, closing SD-004.
- A future need to reconstruct "balance as of date X" (e.g. for a
  historical report) is directly supported by summing `credit_transactions`
  up to that date — the ledger model made this free; a running-balance
  column would not have supported it without additional snapshotting.

## Review

Per `agent-conduct.md`'s ADR Adherence Protocol and this feature's task DoD
(`P2-F2-T1`), this ADR requires independent Architect review before its
status may move to `Accepted`. No self-certification by the authoring
persona (backend-developer) — this ADR was filed as `Status: Proposed`
pending that review. See `framework/spaces/phase2-scope-planning.md`
action 5.

**Architect review verdict (2026-08-02): Accepted.**

Independently opened and cross-checked, not accepted on the authoring
persona's summary alone: `framework/project/architecture.md` §5 (line 80-86,
ARCH-D01/ARCH-D05 data classification table) and ARCH-F04 (line 194) —
confirmed ARCH-D01 names exactly "Customer name, phone, address" as PII with
"Log masking: Yes — never logged in full", and ARCH-F04 states the khata
flow narratively only ("select customer → view outstanding balance / record
payment"), with no schema or balance-model decision, matching this ADR's
own Context claim. `framework/harness/sensor-debt-registry.md` SD-004 row —
confirmed `Open` prior to this ADR, resolution condition and
`check_command` (`grep -q "PII|pii" .claude/hooks/stop.sh`) match exactly
what this ADR's Decision (d) closes. `.claude/hooks/stop.sh` (read directly)
— confirmed gate 10 is a real enforcing check (fails the gate run via
`FAILED_GATES+=`, following the same convention as gates 1-9), not a bare
marker comment, and `TOTAL_GATES` is correctly bumped 9→10;
`bash -n .claude/hooks/stop.sh` — syntax OK. `src/exceptions.py` — confirmed
the base+specific exception convention this ADR's implied `CustomerError`
family (per `ADR-005`'s `SalesError` precedent) would follow.
`grep -rn "^class " src/` — no duplicate/conflicting `Customer`-named type
exists.

Gate 2 ADR Adherence Protocol checks re-run independently:

- **Scope gap:** Confirmed — no existing ADR makes this decision; `ARCH-F04`
  states the flow narratively only. Genuinely new decision.
- **Silence gap:** Confirmed — customer schema, uniqueness key, and
  balance-derivation model were all undecided prior to this ADR; all three
  resolved here.
- **Conflict:** Confirmed none — `docs/adr/` (ADR-001–005) reviewed; `ADR-005`
  is extended (its `commit: bool = True` convention, Decision (c)), not
  contradicted, by this ADR's own (c).
- **Consumer gap:** Confirmed — `src/services/customers.py` (write,
  P2-F2-T2/T3), `src/ui/customers.py` (display, P2-F2-T5),
  `src/services/sales.py` (`ADR-005`, composes a `credit_sale` row into its
  atomic transaction), and future P&L/reporting (outstanding-dues read
  path, not yet implemented) are all named.
- **Schema impact / cross-system analysis:** This is a schema change (two
  new tables, one carrying PII). Multi-System Impact Analysis is present
  and adequate — availability impact none (confirmed, single local process);
  blast radius correctly scoped to the new stop.sh gate 10 (a false
  positive requires a legitimate log call to pass a masked/derived value
  instead — the intended constraint, confirmed no existing Phase 1 `src/`
  code touches customer data so the gate introduces no immediate failure);
  rollback path additive-only (drop new tables, remove module, remove
  stop.sh gate, revert SD-004 to `Open`). Backup impact checked
  independently against `docs/adr/ADR-003-backup-recovery.md`: backup is a
  whole-file `sqlite3.Connection.backup()` snapshot, not table-scoped, so
  the new PII-carrying `customers` table is automatically covered with no
  ADR-003 change required; `architecture.md` ARCH-D01 explicitly records
  "Encryption at rest: No (local file only, single-user device; no
  encryption library in scope)" — an unencrypted backup of PII is therefore
  consistent with the project's own already-accepted data-classification
  decision, not a new unreviewed exposure.
- **Migration mechanism consistency (Architect lens):** Same finding as
  `ADR-005`'s review — this ADR does not restate `ADR-004`'s migration-runner
  mechanism, but the downstream task registry
  (`docs/registry/phase2/feature2-customer-credit-khata/tasks.md`,
  `P2-F2-T2`) already assigns the migration-pair creation to the
  implementing task, correctly deferring the mechanical detail.
- **ARCH-D05 residual gap (new finding, non-blocking for this ADR):**
  `architecture.md` line 86 classifies khata balances (ARCH-D05) as
  requiring "Log masking: Yes — never logged in full" — a broader
  commitment than SD-004's own registry text, which names only ARCH-D01's
  three customer fields (`name`/`phone`/`address`). This ADR's Decision (d)
  and the stop.sh gate 10 it adds close SD-004 exactly as SD-004 itself was
  scoped — they do not extend to `credit_transactions.amount` or a derived
  balance value reaching a log call. This is a real, un-closed gap against
  `architecture.md`'s own data classification table, but it is outside both
  ADR-006's declared Decision scope and SD-004's own literal resolution
  condition — not a reason to withhold Accepted status from this ADR, which
  fully satisfies what it set out to decide. Written to this space's Open
  items as a new Medium finding for follow-up (new sensor debt or an
  extension of stop.sh gate 10), not folded into this ADR silently.

Verdict: **Accepted.** Resolves DLVR-2-02's silence gap in full — customer/
khata schema, uniqueness key, and balance-derivation model are decided, and
SD-004 (as literally scoped) is closed. `P2-F2-T1` may move to `Done`;
`P2-F2-T2` onward may proceed against this decided schema/balance model. A
new, narrower gap (ARCH-D05 balance log-masking, distinct from SD-004) is
flagged separately above and does not block this verdict.

---

**Independent architect re-confirmation (2026-08-03), second pass —
`framework/spaces/phase2-feature2-customer-credit-khata.md` action 2
(`review-accept-khata-adr`).**

This is a distinct, dated confirmation — it does not overwrite or
supersede the 2026-08-02 verdict above. Per `agent-conduct.md`'s ADR
Adherence Protocol and the DoD-gate no-self-certification rule, the
2026-08-02 verdict was not accepted at face value from this file's own
text. Provenance was checked first: `framework/spaces/phase2-scope-planning.md`'s
own Handover log shows the 2026-08-02 verdict was produced by a genuinely
separate architect turn (Actions 9/10, `depends-on: 4, 5`, gated behind
backend-developer's Action 5 authoring turn reaching evidenced completion)
— not the same turn or persona that authored the ADR's Decision content
(backend-developer, Action 5, in the same space). This confirms the review
was a real independent pass, not self-certification, before any of its
substance was relied upon.

All architect-lens checks were then re-run fresh against
`framework/project/architecture.md` directly, not against this file's own
summary of them:

- `ARCH-D01` (architecture.md §5, line 82): re-confirmed the PII field set
  is exactly "Customer name, phone, address" with "Log masking: Yes —
  never logged in full", matching Decision (d) and stop.sh gate 10's
  field list verbatim.
- `ARCH-F04` (architecture.md §13, line 194): re-confirmed narrative-only
  — no schema, uniqueness-key, or balance-model decision present there;
  the silence gap this ADR closes is real.
- `grep -in "customer|khata|credit" docs/adr/ADR-001*.md docs/adr/ADR-002*.md
  docs/adr/ADR-003*.md docs/adr/ADR-004*.md docs/adr/ADR-005*.md` — no
  prior ADR decides the `customers` schema, uniqueness key, or
  balance-derivation model; the only hits are `ADR-005`'s forward
  references to this ADR (its `customer_id`/`payment_status` FK and its
  Decision (c) `commit` convention extension), not a competing decision —
  no conflict.
- `.claude/hooks/stop.sh` (read directly, lines 132-137): gate 10 is a
  real enforcing check (`FAILED_GATES+=` on match), `TOTAL_GATES=10` at
  line 46 — matches Decision (d) exactly.
- `framework/harness/sensor-debt-registry.md` SD-004 row: re-confirmed
  `Status: Resolved`, 2026-08-02, citing this ADR — matches.
- `grep -rn "^class " src/` — no duplicate/conflicting `Customer`-named
  type exists (Priority 1 of `architect-review-checklist.md`); `src/exceptions.py`
  confirmed the base+specific exception convention (`CatalogError`/
  `DiscountError`/`InventoryError`/`SalesError`) this ADR's implied
  `CustomerError` family will follow — no schema/type collision.

Gate 2 ADR Adherence Protocol checks (`agent-conduct.md`), re-run fresh a
second time, independent of the 2026-08-02 record above:

- **Scope gap:** none — no sibling ADR makes a conflicting or
  overlapping schema decision for `customers`/khata; this remains the
  sole owning ADR.
- **Silence gap:** none outstanding — schema, uniqueness key, and
  balance-derivation model are all resolved in Decision (a)/(b); the
  `commit` convention (c) closes the only cross-ADR silence
  (composition into `ADR-005`'s sale-commit transaction).
- **Conflict:** none — re-checked against all of `ADR-001`–`ADR-005`;
  `ADR-005`'s `commit: bool = True` convention is extended, not
  contradicted.
- **Consumer gap:** none unaddressed — `src/services/customers.py`
  (write), `src/ui/customers.py` (display), `src/services/sales.py`
  (`ADR-005`, composes a `credit_sale` row), future P&L/reporting
  (read path, not yet implemented), and `.claude/hooks/stop.sh` (PII-log
  consumer of the ARCH-D01 field list) are all named and correctly
  scoped.

**ARCH-D05 residual gap re-checked and confirmed real, correctly scoped
as non-blocking:** `architecture.md` §5 line 86 (`ARCH-D05`) independently
re-read — it marks derived khata *balance* values, not just the three
`ARCH-D01` customer fields, as "Log masking: Yes — never logged in full."
`.claude/hooks/stop.sh` gate 10's grep pattern
(`customer\.(name|phone|address)`) does not match a balance value or a
`credit_transactions.amount` field, so this gap is real — a future log
call emitting a raw outstanding-balance number would not be caught by the
current gate. It is correctly scoped as non-blocking for *this* ADR's
Accepted status because: (1) `SD-004`'s own registry text and
`check_command` name only the three `ARCH-D01` fields, not `ARCH-D05` —
Decision (d) closes exactly what `SD-004` was scoped to close, not a
broader commitment it never made; (2) no code exists yet anywhere in
`src/` that logs a balance value — the gap is prospective, not an active
leak; (3) `ARCH-D05` coverage is a new, separate concern that requires
either a new sensor debt row or an extension of stop.sh gate 10, tracked
as an Open item, not a defect in this ADR's own declared scope. Withholding
Accepted status from `ADR-006` over a gap it never claimed to close would
be scope creep on this review's part.

**Second-pass verdict: Confirmed — Accepted stands.** The 2026-08-02
review was genuine (separate architect turn, evidenced in
`phase2-scope-planning.md`'s Handover log) and its substance holds under
independent re-verification. No new blocking finding. `action 2`
(`review-accept-khata-adr`,
`framework/spaces/phase2-feature2-customer-credit-khata.md`) may close.
