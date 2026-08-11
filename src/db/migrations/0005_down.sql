-- Migration 0005 — down
-- Reverses 0005_up.sql. Columns are dropped in reverse order of addition.
--
-- Caveat (ADR-009 Consequences, "Rollback path"): dropping
-- sale_line_items.unit_cost destroys every COGS snapshot captured since
-- the migration, and those values are not recoverable from anywhere else.
-- Safe only before real post-migration sales exist (development/testing).
-- Take a backup via src/db/backup.py before running this against a real
-- database.

ALTER TABLE sale_line_items DROP COLUMN unit_cost;
ALTER TABLE products DROP COLUMN cost;
