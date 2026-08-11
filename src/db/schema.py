"""SQLite schema for SMB Account Software.

Data classification (per framework/personas/data-engineer.md and
framework/project/scope-registry.md ARCH-D02/ARCH-D03/ARCH-D04):
- products        — internal (ARCH-D03: "Product pricing catalog — internal").
                     `cost` (added migration 0005, ADR-009 Decision (a)) is
                     also internal under ARCH-D03 — the most recently known
                     per-unit purchase price, exclusive of input GST; NULL
                     means "cost unknown", never zero. Not PII. Commercially
                     sensitive to the shop — no service module logs it.
- discounts       — internal (scoped to a product's pricing catalog entry)
- inventory_stock — internal (ARCH-D04: "Inventory stock levels — Internal —
                     current state only; history via transaction log")
- sales           — internal (ARCH-D02: "Sales/transaction records —
                     Internal — Indefinite — required for historical P&L
                     reporting"). `customer_id` is a nullable foreign key
                     to a future `customers` table (Feature 2, ADR-006) —
                     the PII itself (name/phone/address) lives on that
                     table, not here; this table carries no PII fields
                     directly.
- sale_line_items — internal, same classification/retention as `sales`
                     (ARCH-D02) — a `sales` row's line-item detail.
                     `unit_cost` (added migration 0005, ADR-009 Decision (b))
                     is also internal under ARCH-D02 — a per-line COGS
                     snapshot of the product's `cost` as read at sale time,
                     independent of `discount_amount`; NULL propagates when
                     the product's cost is unknown at sale time and is never
                     backfilled. Not PII. Commercially sensitive; MUST NOT be
                     rendered on a customer-facing bill or receipt
                     (`src/ui/sales.py`).
- customers       — PII (ARCH-D01: "Customer name, phone, address" —
                     "Log masking: Yes — never logged in full"). `phone`
                     is the uniqueness key (ADR-006 Decision (a)). No raw
                     `customer.name`/`.phone`/`.address` field may reach a
                     `logger.*()` call anywhere in `src/` — enforced by
                     `.claude/hooks/stop.sh` gate 10 (SD-004).
- credit_transactions — internal ledger (ARCH-D02-equivalent transaction
                     record), linked to PII via `customer_id` but carrying
                     no PII field directly. `amount` is a derived-balance
                     input classified per ARCH-D05 ("Log masking: Yes —
                     never logged in full" for the derived khata balance) —
                     no raw balance/amount value may reach a `logger.*()`
                     call either, though this is not yet covered by stop.sh
                     gate 10's field-name grep (tracked as a residual,
                     non-blocking gap in `ADR-006`'s Review section). The
                     outstanding balance is never stored — it is always
                     derived by aggregating this table (ADR-006 Decision
                     (b)); `get_outstanding_balance()` in
                     `src/services/customers.py` is the single owner of
                     that derivation.

None of `products`/`discounts`/`inventory_stock`/`sales`/`sale_line_items`
carry PII; `customers` does. Retention: CTX-COMPLIANCE confirms no formal
regulatory regime applies to this project; these are the shop owner's own
business records (pricing, discounts, stock levels, sales, customer/credit
records) and are retained indefinitely with no automated deletion
pipeline — there is no mandated retention ceiling to enforce. `sales`/
`sale_line_items` are additionally required to be retained indefinitely
per ARCH-D02 for historical P&L reporting — `unit_price`/`gst_rate`/
`discount_amount` are snapshotted onto `sale_line_items` at sale time (not
looked up via join) specifically so a later edit to `products` cannot
silently alter a historical bill (ADR-005 Decision (b)).

`inventory_stock` holds current stock levels only (one row per product) —
it is not a movement ledger. A full stock-movement history belongs to the
sales/transaction log introduced in a later phase (DLVR-2-01), per ARCH-D04
— `sales`/`sale_line_items` below are that log. `credit_transactions` is the
equivalent ledger for customer credit/khata events (ADR-006 Decision (b)).

Schema/table ordering in SCHEMA_STATEMENTS below is dependency order:
`customers` is declared before `sales` (which references it) and before
`credit_transactions` (which references both `customers` and `sales`), so
the full sequence is readable in execution order (SQLite itself does not
require this — `REFERENCES` targets are not validated at CREATE TABLE
time — but keeping declaration order matching dependency order avoids
relying on that leniency).

Resolved cross-feature gap (previously open, closed by this migration,
ADR-006/P2-F2-T2): `sales.customer_id REFERENCES customers (id)` per
ADR-005 Decision (b) previously blocked *every* INSERT into `sales` —
including a `customer_id = NULL` paid-only sale — while the `customers`
table did not yet exist, because `connect()` below always sets `PRAGMA
foreign_keys = ON` and SQLite resolves a column's `REFERENCES` target
table at statement-execution time, not only when a non-null FK value is
actually checked. Now that `customers` exists, that blocking point is
closed; `tests/unit/db/test_schema.py`'s `_connect_without_fk_enforcement`
helper (used for sales/sale_line_items CHECK-constraint tests predating
this migration) remains valid as a CHECK-constraint isolation tool but is
no longer required to work around the missing `customers` table.
"""

import sqlite3
from pathlib import Path

SCHEMA_STATEMENTS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        price NUMERIC NOT NULL CHECK (price >= 0),
        gst_rate NUMERIC NOT NULL CHECK (gst_rate >= 0),
        cost NUMERIC CHECK (cost IS NULL OR cost >= 0),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS discounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL REFERENCES products (id),
        discount_type TEXT NOT NULL CHECK (discount_type IN ('percentage', 'flat')),
        value NUMERIC NOT NULL CHECK (value >= 0),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS inventory_stock (
        product_id INTEGER PRIMARY KEY REFERENCES products (id),
        quantity INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        phone TEXT NOT NULL UNIQUE,
        address TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER REFERENCES customers (id),
        payment_status TEXT NOT NULL CHECK (payment_status IN ('paid', 'credit')),
        subtotal NUMERIC NOT NULL CHECK (subtotal >= 0),
        gst_total NUMERIC NOT NULL CHECK (gst_total >= 0),
        bill_total NUMERIC NOT NULL CHECK (bill_total >= 0),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sale_line_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sale_id INTEGER NOT NULL REFERENCES sales (id),
        product_id INTEGER NOT NULL REFERENCES products (id),
        quantity INTEGER NOT NULL CHECK (quantity > 0),
        unit_price NUMERIC NOT NULL CHECK (unit_price >= 0),
        gst_rate NUMERIC NOT NULL CHECK (gst_rate >= 0),
        unit_cost NUMERIC CHECK (unit_cost IS NULL OR unit_cost >= 0),
        discount_amount NUMERIC NOT NULL DEFAULT 0 CHECK (discount_amount >= 0),
        line_gst_amount NUMERIC NOT NULL CHECK (line_gst_amount >= 0),
        line_total NUMERIC NOT NULL CHECK (line_total >= 0),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS credit_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL REFERENCES customers (id),
        sale_id INTEGER REFERENCES sales (id),
        transaction_type TEXT NOT NULL
            CHECK (transaction_type IN ('credit_sale', 'payment')),
        amount NUMERIC NOT NULL CHECK (amount > 0),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
]


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)
    conn.commit()
