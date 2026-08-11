# Phase 1 — Feature 5 — Architect Review Fixes

Created by the architect review of Phase 1 deliverables (`P1-F5-T3`, `PD-1-004`), run 2026-08-02 against `guidelines/architect-review-checklist.md`.

Per `development-process.md` §8: "When architect review produces fixes: create `fixes.md` inside the feature directory; all Critical and High findings MUST clear before the next phase starts." This finding is deferred (permitted by `P1-F5-T3`'s own DoD — "resolve or explicitly defer every Critical/High finding") and does not block `PD-1-004`, but MUST clear before the Phase 1 → Phase 2 boundary.

---

## Finding 1 — Audit trail incomplete on catalog/discount update paths

**Severity:** High

**Location:**
- `src/db/schema.py` — `products` table (line 26-33), `discounts` table (line 35-42): neither declares an `updated_at` column (compare `inventory_stock`, line 44-49, which does).
- `src/services/catalog.py::update_product` (lines 78-105): `UPDATE products SET name = ?, price = ?, gst_rate = ? WHERE id = ?` — no timestamp column to set.
- `src/services/discounts.py::update_discount`: same pattern for `discounts`.

**Issue:** `architecture-rules.md`'s Audit Trail rule requires: "All state-mutating operations on business entities MUST emit an audit event containing: actor, timestamp, resource ID, and change summary." `update_product` and `update_discount` are state-mutating operations on business entities (product pricing/GST rate, discount rules) that record no timestamp anywhere — the only timestamp column present (`created_at`) is explicitly *preserved unchanged* by these update paths (confirmed by `tests/integration/test_audit_trail.py::test_update_product_preserves_created_at_audit_field` / `test_update_discount_preserves_created_at_audit_field`, which assert equality with the pre-update value). There is no record of when a product's price/GST rate or a discount's terms last changed.

This gap was not surfaced by the SD-002 sensor-debt closure (`P1-F5-T2`) because that task's test file asserts audit-field *presence*, and for the update paths the only field that exists (`created_at`) is present and unchanged by design — the test is internally consistent with the schema as built, but the schema itself does not carry an update-timestamp field for these two tables, so "audit trail" coverage is real for creation events and inventory mutations, and absent for catalog/discount update events.

**Impact:** A shop owner disputing a billed price or discount cannot be shown when the catalog entry was last changed, or what it looked like before. No downstream system depends on this today (no reporting/reconciliation module reads catalog history yet — Phase 2/3 do not exist), so the blast radius of leaving this open through Phase 1 sign-off is contained; but Phase 2's billing (`DLVR-2-01`) and Phase 3's P&L reporting (`DLVR-3-01`) may reasonably want to know when a price was last edited, and every day this stays open widens the population of un-audited historical edits that can never be recovered retroactively.

**Fix:**
1. Add `updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))` to both `products` and `discounts` in `src/db/schema.py`, as a new migration pair (`src/db/migrations/NNNN_up.sql` / `NNNN_down.sql`) — mirrors the existing `inventory_stock.updated_at` column exactly.
2. Update `update_product` / `update_discount` to explicitly set `updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')` on every `UPDATE` statement (SQLite does not auto-refresh a column's `DEFAULT` on `UPDATE` — the `inventory_stock` service functions already do this explicitly; the same pattern applies here).
3. Extend `tests/integration/test_audit_trail.py`'s `test_update_product_preserves_created_at_audit_field` / `test_update_discount_preserves_created_at_audit_field` (or add sibling tests) to additionally assert `updated_at` changes across the update call — not just that `created_at` is preserved.

**Rule violated:** `architecture-rules.md` — Audit Trail ("All state-mutating operations on business entities MUST emit an audit event containing: actor, timestamp, resource ID, and change summary").

**Impact (development-process.md §"Before Recording Any Finding" fields):**
- **Files that change:** `src/db/schema.py`, one new migration pair under `src/db/migrations/`, `src/services/catalog.py`, `src/services/discounts.py`, `tests/integration/test_audit_trail.py`, `tests/unit/services/test_catalog.py` / `tests/unit/services/test_discounts.py` (any tests asserting the exact `UPDATE` SQL text or full row shape).
- **Call sites affected:** `update_product` and `update_discount` callers (`src/ui/catalog.py`) are unaffected — return type (`Product`/`Discount`) does not need a new field for this fix to close the gap (the DB row is enough; adding a field to the returned dataclass is optional, at the owning persona's discretion).
- **Tests that must change:** see Fix step 3 above; also confirm `tests/unit/db/test_schema.py` covers the new column.
- **Blast radius if the fix is wrong:** worst case, a malformed migration breaks schema creation for all tables (caught immediately by `tests/unit/db/test_schema.py` and the SD-003 stop.sh reversibility gate); this is an additive, non-breaking column with a `DEFAULT`, so existing rows and existing readers of `products`/`discounts` are unaffected either way.

**Multi-system impact analysis (schema change, cross-cutting):**
- **Availability impact:** none — single local process, no downstream consumers of `products`/`discounts` exist yet beyond the catalog UI itself.
- **Blast radius:** contained to the two tables' `UPDATE` statements and the migration; additive column with a `DEFAULT`, no data loss risk for existing rows.
- **Rollback path:** the migration's paired `NNNN_down.sql` drops the column (or recreates the table without it, per SQLite's `ALTER TABLE` column-drop support in 3.35+ / the project's existing migration convention); reverting the two service-function edits is a one-line removal per function.

**Owner:** data-engineer (schema/migration), backend-developer (service-layer update + test extension).

**Must resolve before:** Phase 1 → Phase 2 boundary (per `development-process.md` §8 — Critical/High findings must clear before the next phase starts). Does not block `PD-1-004` for Phase 1 sign-off itself, per `P1-F5-T3`'s DoD allowance for deferral with a named owner.

**Status:** Done — Fix step 1 (schema/migration) done (data-engineer, 2026-08-02): `products`/`discounts` now carry `updated_at`; migration pair `src/db/migrations/0002_up.sql`/`0002_down.sql` added. Fix steps 2-3 done (backend-developer, 2026-08-02, space action 6): `update_product`/`update_discount` now set `updated_at` explicitly on every `UPDATE`; `tests/integration/test_audit_trail.py` extended with `test_update_product_refreshes_updated_at_audit_field` / `test_update_discount_refreshes_updated_at_audit_field` asserting the field changes (deterministic fixed-sentinel technique, not a real-time comparison). All three fix steps complete — this finding is fully resolved.
