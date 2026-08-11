-- Migration 0004 — down
-- Reverses 0004_up.sql. credit_transactions references both customers and
-- sales, so it is dropped first (reverse dependency order, mirroring
-- 0001_down.sql/0003_down.sql's convention).

DROP TABLE IF EXISTS credit_transactions;
DROP TABLE IF EXISTS customers;
