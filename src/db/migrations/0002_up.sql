-- Migration 0002 — up
-- Adds updated_at to products and discounts, mirroring inventory_stock's
-- existing column. Closes architect-review Finding 1
-- (docs/registry/phase1/feature5-cross-cutting-closure/fixes.md) — audit
-- trail was incomplete on catalog/discount update paths (no timestamp
-- recorded for update events). DDL must be kept in sync with
-- src/db/schema.py SCHEMA_STATEMENTS.
--
-- Note: SQLite's ALTER TABLE ADD COLUMN rejects a non-constant DEFAULT
-- (e.g. strftime(...)) whenever the target table already has rows to
-- backfill (verified: "Cannot add a column with non-constant default").
-- The column is therefore added nullable, then immediately backfilled via
-- UPDATE. SQLite also has no ALTER TABLE ... ALTER COLUMN to retroactively
-- attach a NOT NULL constraint after the fact — a database migrated via
-- this path relies on application code (src/services/catalog.py,
-- src/services/discounts.py) to always populate updated_at on every write
-- going forward. A fresh database created via src/db/schema.py's
-- CREATE TABLE (SCHEMA_STATEMENTS) declares the column NOT NULL DEFAULT
-- (strftime(...)) directly, since CREATE TABLE has no existing-row
-- backfill constraint to satisfy.

ALTER TABLE products ADD COLUMN updated_at TEXT;
UPDATE products SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE updated_at IS NULL;

ALTER TABLE discounts ADD COLUMN updated_at TEXT;
UPDATE discounts SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE updated_at IS NULL;
