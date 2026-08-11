-- Migration 0003 — down
-- Reverses 0003_up.sql. sale_line_items references sales, so it is dropped
-- first (reverse dependency order, mirroring 0001_down.sql's convention).

DROP TABLE IF EXISTS sale_line_items;
DROP TABLE IF EXISTS sales;
