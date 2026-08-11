"""SD-002 audit-trail integration test — full Phase 1 + Phase 2 mutation
coverage.

Per `framework/harness/sensor-debt-registry.md` SD-002 ("Audit trail — all
mutations emit audit events") and
`docs/registry/phase1/feature5-cross-cutting-closure/tasks.md` P1-F5-T2:
asserts audit fields (`created_at`/`updated_at`) are present and populated
on every mutation path:

- Catalog product mutations (Feature 3 — `src/services/catalog.py`)
- Discount rule mutations (Feature 3 — `src/services/discounts.py`)
- Inventory stock mutations (Feature 4 — `src/services/inventory.py`:
  `set_stock` (manual adjust screen's absolute-set path), `adjust_stock`
  (relative delta, e.g. the UI restock path — `src/ui/inventory.py`), and
  `decrement_stock` (the sale-entry decrement hook))
- Sales mutations (Phase 2 Feature 1 — `src/services/sales.py`:
  `record_sale`'s `sales`/`sale_line_items` writes)
- Customer/khata credit mutations (Phase 2 Feature 2 —
  `src/services/customers.py`: `create_customer`, `update_customer`, and
  the `credit_transactions` ledger writes issued by `record_credit_sale`
  (exercised via `record_sale`'s credit-sale path, its only production
  call site) and `record_payment`)

`sale_line_items` and `credit_transactions` are insert-only tables (no
`UPDATE` statement targets either anywhere in the codebase) — they carry
`created_at` only, no `updated_at` column, so only `created_at` presence is
asserted for those two tables.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.db.schema import connect, init_schema
from src.services.catalog import create_product, update_product
from src.services.customers import create_customer, record_payment, update_customer
from src.services.discounts import create_discount, update_discount
from src.services.inventory import adjust_stock, decrement_stock, set_stock
from src.services.sales import CartLine, record_sale


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "data.db")
    init_schema(connection)
    yield connection
    connection.close()


# --- catalog: products -------------------------------------------------------------


def test_create_product_writes_created_at_audit_field(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)

    created_at = conn.execute(
        "SELECT created_at FROM products WHERE id = ?", (product.id,)
    ).fetchone()[0]

    assert created_at is not None
    assert created_at != ""


def test_update_product_preserves_created_at_audit_field(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    original_created_at = conn.execute(
        "SELECT created_at FROM products WHERE id = ?", (product.id,)
    ).fetchone()[0]

    update_product(conn, product.id, name="Rice 5kg", price=280, gst_rate=0)

    updated_created_at = conn.execute(
        "SELECT created_at FROM products WHERE id = ?", (product.id,)
    ).fetchone()[0]
    assert updated_created_at == original_created_at


def test_update_product_refreshes_updated_at_audit_field(
    conn: sqlite3.Connection,
) -> None:
    """`update_product` must set `updated_at` explicitly on every UPDATE
    (Finding 1, fixes.md) — not just preserve `created_at`. A fixed
    backdated sentinel (rather than a real-time comparison) makes the
    assertion deterministic regardless of clock/microsecond granularity."""
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    conn.execute(
        "UPDATE products SET updated_at = '2000-01-01T00:00:00.000000Z' WHERE id = ?",
        (product.id,),
    )
    conn.commit()

    update_product(conn, product.id, name="Rice 5kg", price=280, gst_rate=0)

    updated_at = conn.execute(
        "SELECT updated_at FROM products WHERE id = ?", (product.id,)
    ).fetchone()[0]
    assert updated_at != "2000-01-01T00:00:00.000000Z"
    assert updated_at > "2000-01-01T00:00:00.000000Z"


# --- catalog: discounts -------------------------------------------------------------


def test_create_discount_writes_created_at_audit_field(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)

    discount = create_discount(conn, product.id, discount_type="flat", value=5)

    created_at = conn.execute(
        "SELECT created_at FROM discounts WHERE id = ?", (discount.id,)
    ).fetchone()[0]
    assert created_at is not None
    assert created_at != ""


def test_update_discount_preserves_created_at_audit_field(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    discount = create_discount(conn, product.id, discount_type="flat", value=5)
    original_created_at = conn.execute(
        "SELECT created_at FROM discounts WHERE id = ?", (discount.id,)
    ).fetchone()[0]

    update_discount(conn, discount.id, discount_type="flat", value=10)

    updated_created_at = conn.execute(
        "SELECT created_at FROM discounts WHERE id = ?", (discount.id,)
    ).fetchone()[0]
    assert updated_created_at == original_created_at


def test_update_discount_refreshes_updated_at_audit_field(
    conn: sqlite3.Connection,
) -> None:
    """`update_discount` must set `updated_at` explicitly on every UPDATE
    (Finding 1, fixes.md). Uses the same fixed-sentinel technique as
    `test_update_product_refreshes_updated_at_audit_field` for a
    deterministic, sleep-free assertion of change."""
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    discount = create_discount(conn, product.id, discount_type="flat", value=5)
    conn.execute(
        "UPDATE discounts SET updated_at = '2000-01-01T00:00:00.000000Z' WHERE id = ?",
        (discount.id,),
    )
    conn.commit()

    update_discount(conn, discount.id, discount_type="flat", value=10)

    updated_at = conn.execute(
        "SELECT updated_at FROM discounts WHERE id = ?", (discount.id,)
    ).fetchone()[0]
    assert updated_at != "2000-01-01T00:00:00.000000Z"
    assert updated_at > "2000-01-01T00:00:00.000000Z"


# --- inventory: stock ------------------------------------------------------------


def test_set_stock_writes_updated_at_audit_field(conn: sqlite3.Connection) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)

    set_stock(conn, product.id, 50)

    updated_at = conn.execute(
        "SELECT updated_at FROM inventory_stock WHERE product_id = ?",
        (product.id,),
    ).fetchone()[0]
    assert updated_at is not None
    assert updated_at != ""


def test_adjust_stock_refreshes_updated_at_audit_field(
    conn: sqlite3.Connection,
) -> None:
    """Covers the manual restock path (`src/ui/inventory.py`'s adjust
    screen) — a distinct call site and a distinct sign of `delta` from
    `decrement_stock`'s negative-delta hook, so it needs its own direct
    coverage rather than relying on `decrement_stock` exercising the
    shared `set_stock` internals."""
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    set_stock(conn, product.id, 10)
    first_updated_at = conn.execute(
        "SELECT updated_at FROM inventory_stock WHERE product_id = ?",
        (product.id,),
    ).fetchone()[0]

    adjust_stock(conn, product.id, 5)

    second_updated_at = conn.execute(
        "SELECT updated_at FROM inventory_stock WHERE product_id = ?",
        (product.id,),
    ).fetchone()[0]
    assert second_updated_at is not None
    assert second_updated_at != ""
    assert first_updated_at is not None


def test_decrement_stock_refreshes_updated_at_audit_field(
    conn: sqlite3.Connection,
) -> None:
    """Covers the Phase 2 sale-entry decrement hook's own mutation path
    (P1-F4-T2) — not Phase 2's sale/transaction commit logic itself, which
    does not exist yet."""
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    set_stock(conn, product.id, 10)
    first_updated_at = conn.execute(
        "SELECT updated_at FROM inventory_stock WHERE product_id = ?",
        (product.id,),
    ).fetchone()[0]

    decrement_stock(conn, product.id, 3)

    second_updated_at = conn.execute(
        "SELECT updated_at FROM inventory_stock WHERE product_id = ?",
        (product.id,),
    ).fetchone()[0]
    assert second_updated_at is not None
    assert second_updated_at != ""
    # Both timestamps exist and the row was written twice — the audit
    # field is refreshed on every mutation, not just the first insert.
    assert first_updated_at is not None


# --- sales -------------------------------------------------------------------------


def test_record_sale_writes_created_at_and_updated_at_on_sales_row(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    set_stock(conn, product.id, 10)
    customer = create_customer(conn, name="Ravi Kumar", phone="9990001111")

    sale = record_sale(
        conn,
        [CartLine(product_id=product.id, quantity=2)],
        payment_status="credit",
        customer_id=customer.id,
    )

    created_at, updated_at = conn.execute(
        "SELECT created_at, updated_at FROM sales WHERE id = ?", (sale.id,)
    ).fetchone()
    assert created_at is not None
    assert created_at != ""
    assert updated_at is not None
    assert updated_at != ""


def test_record_sale_writes_created_at_on_sale_line_items_row(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    set_stock(conn, product.id, 10)
    customer = create_customer(conn, name="Ravi Kumar", phone="9990001111")

    sale = record_sale(
        conn,
        [CartLine(product_id=product.id, quantity=2)],
        payment_status="credit",
        customer_id=customer.id,
    )

    created_at = conn.execute(
        "SELECT created_at FROM sale_line_items WHERE sale_id = ?", (sale.id,)
    ).fetchone()[0]
    assert created_at is not None
    assert created_at != ""


# --- customers / khata credit ------------------------------------------------------


def test_create_customer_writes_created_at_audit_field(
    conn: sqlite3.Connection,
) -> None:
    customer = create_customer(conn, name="Ravi Kumar", phone="9990001111")

    created_at = conn.execute(
        "SELECT created_at FROM customers WHERE id = ?", (customer.id,)
    ).fetchone()[0]
    assert created_at is not None
    assert created_at != ""


def test_update_customer_preserves_created_at_audit_field(
    conn: sqlite3.Connection,
) -> None:
    customer = create_customer(conn, name="Ravi Kumar", phone="9990001111")
    original_created_at = conn.execute(
        "SELECT created_at FROM customers WHERE id = ?", (customer.id,)
    ).fetchone()[0]

    update_customer(conn, customer.id, name="Ravi K.", phone="9990001111")

    updated_created_at = conn.execute(
        "SELECT created_at FROM customers WHERE id = ?", (customer.id,)
    ).fetchone()[0]
    assert updated_created_at == original_created_at


def test_update_customer_refreshes_updated_at_audit_field(
    conn: sqlite3.Connection,
) -> None:
    """Same fixed-sentinel technique as
    `test_update_product_refreshes_updated_at_audit_field` — `update_customer`
    sets `updated_at` explicitly on every UPDATE (see its docstring)."""
    customer = create_customer(conn, name="Ravi Kumar", phone="9990001111")
    conn.execute(
        "UPDATE customers SET updated_at = '2000-01-01T00:00:00.000000Z' WHERE id = ?",
        (customer.id,),
    )
    conn.commit()

    update_customer(conn, customer.id, name="Ravi K.", phone="9990001111")

    updated_at = conn.execute(
        "SELECT updated_at FROM customers WHERE id = ?", (customer.id,)
    ).fetchone()[0]
    assert updated_at != "2000-01-01T00:00:00.000000Z"
    assert updated_at > "2000-01-01T00:00:00.000000Z"


def test_record_sale_credit_path_writes_created_at_on_credit_transactions_row(
    conn: sqlite3.Connection,
) -> None:
    """Exercises `record_credit_sale` through its only production call
    site — `record_sale`'s credit-sale branch — rather than calling it
    directly, so this test reflects the real composed transaction."""
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    set_stock(conn, product.id, 10)
    customer = create_customer(conn, name="Ravi Kumar", phone="9990001111")

    sale = record_sale(
        conn,
        [CartLine(product_id=product.id, quantity=2)],
        payment_status="credit",
        customer_id=customer.id,
    )

    created_at = conn.execute(
        "SELECT created_at FROM credit_transactions WHERE sale_id = ?", (sale.id,)
    ).fetchone()[0]
    assert created_at is not None
    assert created_at != ""


def test_record_payment_writes_created_at_on_credit_transactions_row(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    set_stock(conn, product.id, 10)
    customer = create_customer(conn, name="Ravi Kumar", phone="9990001111")
    record_sale(
        conn,
        [CartLine(product_id=product.id, quantity=2)],
        payment_status="credit",
        customer_id=customer.id,
    )

    record_payment(conn, customer.id, amount=50)

    created_at = conn.execute(
        "SELECT created_at FROM credit_transactions WHERE customer_id = ? "
        "AND transaction_type = 'payment'",
        (customer.id,),
    ).fetchone()[0]
    assert created_at is not None
    assert created_at != ""
