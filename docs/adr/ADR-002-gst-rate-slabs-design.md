# ADR-002: GST Rate/Slabs Design for Product Catalog

**Status:** Accepted
**Date:** 2026-08-01

## Context

`architecture.md` §10 flagged an open question: the product pricing catalog needs a **configurable per-product GST rate** rather than a single hardcoded rate, since Indian GST applies different rates to different product categories. `plan-protocol.md` P2 drained this into Phase 1 DoD condition `PD-1-GST` (owner: backend-developer), to be resolved before `src/services/catalog.py` (P1-F3-T2) implements product CRUD.

This ADR is scoped to the **catalog/pricing** concern only: where the rate lives and how it is validated/applied at catalog time. GST-inclusive **billing calculation** (computing invoice totals from the rate) is `DLVR-2-01`, Phase 2 — out of scope here, per `SCOPE-03`/`DLVR-1-02` and `project-context.md`'s MVP scope boundary.

## Decision

**(a) Slab set.** GST rates in India are applied in five statutory slabs. This system supports exactly these five values for `products.gst_rate`:

```
0%, 5%, 12%, 18%, 28%
```

No other rate value is valid for a product's GST field in this system's scope (no cess, no compensation-cess add-ons — out of scope for a single-shop kirana tool per `CTX-COMPLIANCE`).

**(b) Storage.** The rate is stored on the existing `products.gst_rate NUMERIC NOT NULL CHECK (gst_rate >= 0)` column, introduced in Phase 1 Feature 1 (`src/db/schema.py`). This column is **sufficient as-is** — no new table, no new column, and no schema migration is required by this ADR:

- One rate per product is the correct cardinality: a kirana shop's product catalog maps each SKU to a single statutory slab; no product needs multiple simultaneous rates.
- A separate `gst_slabs` lookup table was considered and rejected (see Alternatives) — it would add a join for no behavioural benefit at this data volume (architecture.md §8: single-shop, hundreds of products).
- The existing `CHECK (gst_rate >= 0)` constraint is deliberately **not tightened** to `CHECK (gst_rate IN (0,5,12,18,28))` in the DB schema by this ADR. Tightening the schema's CHECK constraint is a schema modification requiring Architect approval under `backend-developer.md`'s "MUST NOT modify core data schemas without Architect approval" constraint, and is not necessary to satisfy PD-1-GST — slab validation is enforced at the **service layer** instead (see below), which is where all business-rule validation for this catalog already lives per `architecture.md` §2 ("Service layer... enforces data consistency"). If the Architect reviewing this ADR judges the DB-level CHECK is required, that is a follow-up schema-change action, not part of this ADR's closure.

**(c) Where/how the rate is applied (this feature's scope).** In Phase 1 Feature 3:

- `src/services/catalog.py` (P1-F3-T2) validates `gst_rate` against the five-slab set on product create/edit, raising a domain-specific validation exception (not a bare `ValueError`/raw `sqlite3.IntegrityError`) when an out-of-slab value is submitted.
- `src/ui/catalog.py` (P1-F3-T4) surfaces that validation error to the user; it does not perform its own rate validation (no business logic in UI/route-equivalent layers, per `architecture.md` §2 and `anti-patterns.md`).
- No GST amount is computed or added to any price in this feature — `gst_rate` is stored and validated only. Computing GST-inclusive line/bill totals from this rate is `DLVR-2-01` (Phase 2 sales/billing), which will read `products.gst_rate` as a consumer of this column (see Consumer gap check below) but is not implemented here.

**(d) ADR Adherence Protocol — Gate 2 checks (`agent-conduct.md`):**

- **Scope gap:** No existing ADR makes this decision. `ADR-001` (Technology Stack Selection) fixes the stack (SQLite/Python/curses) but does not address GST slab modeling — this is a genuinely new decision, not an extension of an existing one by assumption.
- **Silence gap:** `architecture.md` §10 recommended "configurable per-product GST rate" but was explicitly silent on the slab set, storage location, and enforcement layer. This ADR resolves all three (slab set in (a), storage in (b), enforcement layer in (c)) rather than leaving them to be invented ad hoc during P1-F3-T2 implementation.
- **Conflict:** `docs/adr/` contains one other ADR (`ADR-001`). Reviewed in full — no contradiction: ADR-001 does not mention GST, rates, or the `products` table's columns.
- **Consumer gap:** Components that read `products.gst_rate` were enumerated: (1) `src/services/catalog.py` — write path, this feature; (2) `src/ui/catalog.py` — display only, this feature; (3) a future `src/services/billing` module (`DLVR-2-01`, Phase 2) — read path for GST-inclusive total calculation, not yet implemented. All three are named here so Phase 2's billing implementation has a decided contract to build against rather than re-deriving the slab set.

## Alternatives Considered

- **Separate `gst_slabs` reference table** (id, rate) with `products.gst_slab_id` FK — rejected. Adds a join and a schema migration for no behavioural gain at this scale (single shop, low hundreds of products); the fixed five-value statutory slab set does not change often enough to justify normalizing it into its own table, and `products.gst_rate` already exists and is populated by Feature 1's schema.
- **DB-level `CHECK (gst_rate IN (0,5,12,18,28))` constraint** — considered, not adopted by this ADR (see (b)) because it modifies the core schema, which requires Architect approval beyond this ADR's own review step; service-layer validation achieves the same enforcement without a schema change and can be escalated to a schema-change follow-up if the Architect prefers DB-level enforcement.
- **Hardcoded single global rate constant** — rejected outright; this is exactly what `architecture.md` §10 and `PD-1-GST` require replacing with a configurable per-product value.

## Consequences

- `src/services/catalog.py` owns GST slab validation; any future service that writes `products.gst_rate` (there should be none besides catalog CRUD) must reuse the same validation, not re-implement it, to avoid divergence.
- Phase 2 billing (`DLVR-2-01`) has a decided, documented contract for reading `gst_rate` — no re-derivation needed when that phase starts.
- If a future statutory slab is added/removed by the GST Council, only the service-layer validation set changes — no migration is needed, since the DB column already accepts any non-negative numeric value.

## Review

Per `agent-conduct.md` ADR Adherence Protocol and this feature's task DoD (`P1-F3-T1`), this ADR requires independent Architect review before its status may move to `Accepted`. No self-certification by the authoring persona (backend-developer). See `framework/spaces/phase1-feature3-catalog-pricing.md` action 2.

**Architect review verdict (2026-08-01): Accepted.**

Independently opened `framework/project/architecture.md` §10-adjacent context (line 141, the `plan-protocol.md` P2 boundary note) and `src/db/schema.py` — not accepted on the authoring persona's summary alone. Gate 2 ADR Adherence Protocol checks re-run independently:

- **Scope gap:** Confirmed — `ls docs/adr/` shows only `ADR-001` (Technology Stack Selection) besides this one. Opened `ADR-001` directly: no mention of GST, rates, or `products` columns. This ADR makes a genuinely new decision, not an unauthorized extension of an existing one.
- **Silence gap:** Confirmed — `architecture.md` line 141 (`plan-protocol.md` P2 boundary revalidation note) shows the slab set, storage location, and enforcement layer were not decided prior to this ADR; all three are resolved here, not left to be improvised during P1-F3-T2 implementation.
- **Conflict:** Confirmed none — `ADR-001` fixes stack only (Python/curses/SQLite/Poetry/pytest/ruff/mypy); no overlapping subject matter with GST slab modeling.
- **Consumer gap:** Confirmed — three consumers of `products.gst_rate` named: `src/services/catalog.py` (write, this feature), `src/ui/catalog.py` (display, this feature), future `src/services/billing` (`DLVR-2-01`, Phase 2 read path). This gives Phase 2 billing a decided contract; no unaddressed reader identified.
- **Schema impact:** No schema change is proposed by this ADR — `products.gst_rate NUMERIC NOT NULL CHECK (gst_rate >= 0)` is reused as-is, confirmed present in `src/db/schema.py` line 30. Cross-system impact analysis for a schema change is therefore not applicable; the DB-level `CHECK (gst_rate IN (...))` alternative is correctly deferred as a documented follow-up rather than bundled into this decision.
- **Rollback path:** Since no schema or cross-cutting change is made, rollback is confined to the service-layer validation function this ADR authorizes for P1-F3-T2 — reverting that function (or the ADR's Accepted status) fully reverses this decision with no data migration involved.

Verdict: **Accepted.** Resolves the `architecture.md` §10 GST slab open question and `PD-1-GST` in full — slab set, storage, and application-layer scope are all decided with no remaining silence gap in this feature's scope. `PD-1-GST` may move to `Resolved`.
