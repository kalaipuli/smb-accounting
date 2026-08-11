-- Migration 0004 — up
-- Adds the customers/credit_transactions tables (ADR-006, P2-F2-T2). DDL
-- must be kept in sync with src/db/schema.py SCHEMA_STATEMENTS. `customers`
-- is created first — `sales.customer_id REFERENCES customers (id)`
-- (migration 0003) and `credit_transactions.customer_id REFERENCES
-- customers (id)` both depend on it; `credit_transactions` is created last
-- since it also references `sales (id)`. See src/db/schema.py module
-- docstring for data classification (customers carries PII per ARCH-D01;
-- credit_transactions.amount is a derived-balance input per ARCH-D05).

CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT NOT NULL UNIQUE,
    address TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS credit_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers (id),
    sale_id INTEGER REFERENCES sales (id),
    transaction_type TEXT NOT NULL
        CHECK (transaction_type IN ('credit_sale', 'payment')),
    amount NUMERIC NOT NULL CHECK (amount > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
