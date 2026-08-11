-- Migration 0001 — up
-- Creates the core Phase 1 tables: products, discounts, inventory_stock.
-- DDL must be kept in sync with src/db/schema.py SCHEMA_STATEMENTS.
-- See src/db/schema.py module docstring for data classification and
-- retention notes.

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    price NUMERIC NOT NULL CHECK (price >= 0),
    gst_rate NUMERIC NOT NULL CHECK (gst_rate >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS discounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products (id),
    discount_type TEXT NOT NULL CHECK (discount_type IN ('percentage', 'flat')),
    value NUMERIC NOT NULL CHECK (value >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS inventory_stock (
    product_id INTEGER PRIMARY KEY REFERENCES products (id),
    quantity INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
