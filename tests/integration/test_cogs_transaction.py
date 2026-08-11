"""ADR-009 (P3-F1-T5) integration test — real end-to-end COGS snapshot
transaction.

Mirrors `tests/integration/test_sales_transaction.py`'s pattern exactly:
built through the actual migration runner (`src/db/migrate.py`'s
`apply_pending_migrations()`, the same code path `src/main.py` calls at real
application startup), not `init_schema()`'s direct `SCHEMA_STATEMENTS`
execution, so migration `0005` (products.cost / sale_line_items.unit_cost,
ADR-009) is exercised through its real bootstrap path rather than a unit-
level shortcut. No database or service call is mocked anywhere in this file
— a real `sqlite3.Connection` against a real temp-file database is used
throughout, per `development-process.md` §4's `tests/integration/`
definition ("requires real running services; tests actual end-to-end
behaviour").

Covers two properties this feature's DoD requires beyond
`test_sales_transaction.py`'s existing coverage:

1. `sale_line_items.unit_cost` is written inside `record_sale()`'s single
   commit alongside the other three tables (`sales`, `inventory_stock`,
   `credit_transactions`) — proving the COGS snapshot is not a separate,
   out-of-band write that could disagree with the rest of the transaction.
2. A failure partway through the transaction (insufficient stock) leaves no
   partial write across any of the four tables, `unit_cost` included — the
   same atomicity guarantee `test_sales_transaction.py` proves for the other
   columns, now covering the new one.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.db.migrate import apply_pending_migrations
from src.db.schema import connect
from src.exceptions import SaleTransactionError
from src.services.catalog import create_product
from src.services.customers import create_customer, get_outstanding_balance
from src.services.inventory import get_stock, set_stock
from src.services.sales import CartLine, get_sale, record_sale


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "data.db")
    applied = apply_pending_migrations(connection)
    assert applied, (
        "expected at least one migration to apply against a fresh database "
        "— an empty result means the migration runner silently did nothing "
        "and this test would be exercising no real schema-bootstrap path"
    )
    assert 5 in applied, (
        "migration 0005 (ADR-009 products.cost/sale_line_items.unit_cost) "
        "must be part of the applied set for this test to exercise the real "
        "bootstrap path this feature depends on"
    )
    yield connection
    connection.close()


def _table_row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
        for table in (
            "sales",
            "sale_line_items",
            "inventory_stock",
            "credit_transactions",
        )
    }


def test_record_sale_transaction_commits_unit_cost_snapshot_atomically_with_the_rest(
    conn: sqlite3.Connection,
) -> None:
    """A credit sale of a costed product, against a real migration-built
    database, writes `sale_line_items.unit_cost` under the same single
    `conn.commit()` as the `sales` row, the inventory decrement, and the
    `credit_transactions` ledger row (`ADR-005` Decision (c) composed with
    `ADR-006` Decision (c) and `ADR-009` Decision (b))."""
    product = create_product(
        conn, name="Cooking Oil 1L", price=200, gst_rate=12, cost=150
    )
    set_stock(conn, product.id, 10)
    customer = create_customer(conn, name="Ravi Kumar", phone="9990001111")

    sale = record_sale(
        conn,
        [CartLine(product_id=product.id, quantity=2)],
        payment_status="credit",
        customer_id=customer.id,
    )

    # sale_line_items — unit_cost persisted alongside the rest of the line
    line_item_row = conn.execute(
        "SELECT sale_id, product_id, quantity, unit_cost "
        "FROM sale_line_items WHERE sale_id = ?",
        (sale.id,),
    ).fetchone()
    assert line_item_row == (sale.id, product.id, 2, 150)

    # sales
    sale_row = conn.execute(
        "SELECT customer_id, payment_status, bill_total FROM sales WHERE id = ?",
        (sale.id,),
    ).fetchone()
    assert sale_row == (customer.id, "credit", pytest.approx(sale.bill_total))

    # inventory_stock — decremented
    assert get_stock(conn, product.id).quantity == 8

    # credit_transactions — ledger row backing the outstanding balance
    credit_row = conn.execute(
        "SELECT customer_id, sale_id, transaction_type, amount "
        "FROM credit_transactions WHERE sale_id = ?",
        (sale.id,),
    ).fetchone()
    assert credit_row == (
        customer.id,
        sale.id,
        "credit_sale",
        pytest.approx(sale.bill_total),
    )
    assert get_outstanding_balance(conn, customer.id) == pytest.approx(sale.bill_total)

    # get_sale() reads the same snapshot back
    reloaded = get_sale(conn, sale.id)
    assert reloaded.line_items[0].unit_cost == 150


def test_record_sale_transaction_when_insufficient_stock_leaves_no_partial_unit_cost_write(  # noqa: E501
    conn: sqlite3.Connection,
) -> None:
    """Error-path coverage (`development-process.md` §3): a mid-sequence
    stock failure on a credit sale of a costed product must roll back every
    write issued so far — the `sales` row, the `sale_line_items` row
    (`unit_cost` included), and the already-inserted (uncommitted)
    `credit_transactions` row — across all four tables, leaving
    `inventory_stock` untouched. Proves `record_sale()`'s single-commit/
    single-rollback boundary holds for the new `unit_cost` write against a
    real migration-built database."""
    product = create_product(conn, name="Soap Bar", price=50, gst_rate=18, cost=30)
    set_stock(conn, product.id, 1)
    customer = create_customer(conn, name="Priya Singh", phone="9990002222")

    before = _table_row_counts(conn)
    stock_before = get_stock(conn, product.id).quantity

    with pytest.raises(SaleTransactionError):
        record_sale(
            conn,
            [CartLine(product_id=product.id, quantity=5)],
            payment_status="credit",
            customer_id=customer.id,
        )

    after = _table_row_counts(conn)
    assert after["sales"] == before["sales"]
    assert after["sale_line_items"] == before["sale_line_items"]
    assert after["credit_transactions"] == before["credit_transactions"]
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM sale_line_items WHERE unit_cost IS NOT NULL"
        ).fetchone()[0]
        == 0
    )
    # inventory_stock's row count is unaffected either way (UPDATE, not
    # INSERT) — assert the quantity value itself is unchanged instead.
    assert get_stock(conn, product.id).quantity == stock_before
    assert get_outstanding_balance(conn, customer.id) == pytest.approx(0)
