-- Migration 0003 — up
-- Adds the sales/sale_line_items tables (ADR-005, P2-F1-T2). DDL must be
-- kept in sync with src/db/schema.py SCHEMA_STATEMENTS.
-- See src/db/schema.py module docstring for data classification, retention,
-- and the known cross-feature gap (sales.customer_id REFERENCES customers
-- (id) — no customers table exists yet; see that docstring for detail).

CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER REFERENCES customers (id),
    payment_status TEXT NOT NULL CHECK (payment_status IN ('paid', 'credit')),
    subtotal NUMERIC NOT NULL CHECK (subtotal >= 0),
    gst_total NUMERIC NOT NULL CHECK (gst_total >= 0),
    bill_total NUMERIC NOT NULL CHECK (bill_total >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS sale_line_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id INTEGER NOT NULL REFERENCES sales (id),
    product_id INTEGER NOT NULL REFERENCES products (id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price NUMERIC NOT NULL CHECK (unit_price >= 0),
    gst_rate NUMERIC NOT NULL CHECK (gst_rate >= 0),
    discount_amount NUMERIC NOT NULL DEFAULT 0 CHECK (discount_amount >= 0),
    line_gst_amount NUMERIC NOT NULL CHECK (line_gst_amount >= 0),
    line_total NUMERIC NOT NULL CHECK (line_total >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
