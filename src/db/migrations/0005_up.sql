-- Migration 0005 — up
-- Adds products.cost and sale_line_items.unit_cost (ADR-009, P3-F1-T1).
-- DDL must be kept in sync with src/db/schema.py SCHEMA_STATEMENTS.
-- products is altered first, then sale_line_items — 0005_down.sql reverses
-- that order, mirroring 0002_down.sql's convention.
--
-- Both columns are nullable with no default: NULL means "cost unknown",
-- never "cost is zero" (ADR-009 Decision (a)/(b)). Each column's CHECK
-- constraint tolerates NULL — SQLite allows ALTER TABLE ADD COLUMN with a
-- CHECK constraint only when the constraint evaluates non-false for the
-- column's default (NULL) value on existing rows, which
-- `x IS NULL OR x >= 0` satisfies.
--
-- products.cost is the most recently known per-unit purchase (landed)
-- price, exclusive of input GST, in the same units as products.price.
--
-- sale_line_items.unit_cost is written at sale time from the product's
-- cost as read in that same call (ADR-009 Decision (b)) — the identical
-- mechanism ADR-005 Decision (b) already uses for unit_price/gst_rate/
-- discount_amount, so a later catalog edit cannot silently alter a
-- historical bill's recorded cost. Discounts do not reduce cost.

ALTER TABLE products ADD COLUMN cost NUMERIC
    CHECK (cost IS NULL OR cost >= 0);

ALTER TABLE sale_line_items ADD COLUMN unit_cost NUMERIC
    CHECK (unit_cost IS NULL OR unit_cost >= 0);
