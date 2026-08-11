-- Migration 0001 — down
-- Reverses 0001_up.sql. Tables are dropped in reverse dependency order:
-- inventory_stock and discounts both reference products.id, so they are
-- dropped first.

DROP TABLE IF EXISTS inventory_stock;
DROP TABLE IF EXISTS discounts;
DROP TABLE IF EXISTS products;
