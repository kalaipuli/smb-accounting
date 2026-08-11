-- Migration 0002 — down
-- Reverses 0002_up.sql. Columns are dropped in reverse order of addition.

ALTER TABLE discounts DROP COLUMN updated_at;
ALTER TABLE products DROP COLUMN updated_at;
