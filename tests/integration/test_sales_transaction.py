"""SD-005 integration test — real end-to-end sale transaction.

Per `framework/harness/sensor-debt-registry.md` SD-005 ("Tier 2 — integration
tests; ci.yml's 'Integration tests' step is an echo stub") and this space's
action 9 (`close-sd005-integration-test-stub`): now that `record_sale()`
(`src/services/sales.py`, landed by this space's Turn 6/action 5) exists,
this file replaces the CI stub with a real integration test.

Distinct from `tests/unit/services/test_sales.py`'s existing `-k
transaction` unit tests in one deliberate respect: this test builds its
database via the *actual migration runner* (`src/db/migrate.py`'s
`apply_pending_migrations()`, the same code path `src/main.py` calls at
real application startup) rather than `init_schema()`'s direct
`SCHEMA_STATEMENTS` execution. Both produce an equivalent schema today, but
only the migration-runner path exercises the production schema-bootstrap
code and would catch a migration/schema drift the unit-level `init_schema`
shortcut cannot. No database or service call is mocked anywhere in this
file — a real `sqlite3.Connection` against a real temp-file database is
used throughout, per `development-process.md` §4's `tests/integration/`
definition ("requires real running services; tests actual end-to-end
behaviour").

Covers `record_sale()`'s single-transaction commit end-to-end for a credit
sale: the `sales` insert, the `sale_line_items` insert, the inventory
`decrement_stock` write, and `record_credit_sale()`'s `credit_transactions`
insert all land in one `conn.commit()` — plus the rollback-on-failure case
(insufficient stock), proving no partial write survives across all four
tables when the transaction fails partway through.
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
from src.services.sales import CartLine, record_sale


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "data.db")
    applied = apply_pending_migrations(connection)
    assert applied, (
        "expected at least one migration to apply against a fresh database "
        "— an empty result means the migration runner silently did nothing "
        "and this test would be exercising no real schema-bootstrap path"
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


def test_record_sale_transaction_credit_sale_commits_all_four_tables_atomically(
    conn: sqlite3.Connection,
) -> None:
    """A credit sale against a real, migration-built database writes the
    `sales` row, the `sale_line_items` row, the inventory decrement, and
    the `credit_transactions` ledger row — all under the one `conn.commit()`
    `record_sale()` issues (`ADR-005` Decision (c) composed with `ADR-006`
    Decision (c))."""
    product = create_product(conn, name="Cooking Oil 1L", price=200, gst_rate=12)
    set_stock(conn, product.id, 10)
    customer = create_customer(conn, name="Ravi Kumar", phone="9990001111")

    sale = record_sale(
        conn,
        [CartLine(product_id=product.id, quantity=2)],
        payment_status="credit",
        customer_id=customer.id,
    )

    # sales
    sale_row = conn.execute(
        "SELECT customer_id, payment_status, bill_total FROM sales WHERE id = ?",
        (sale.id,),
    ).fetchone()
    assert sale_row == (customer.id, "credit", pytest.approx(sale.bill_total))

    # sale_line_items
    line_item_row = conn.execute(
        "SELECT sale_id, product_id, quantity FROM sale_line_items WHERE sale_id = ?",
        (sale.id,),
    ).fetchone()
    assert line_item_row == (sale.id, product.id, 2)

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


def test_record_sale_transaction_when_insufficient_stock_leaves_no_partial_writes(
    conn: sqlite3.Connection,
) -> None:
    """Error-path coverage (`development-process.md` §3): a mid-sequence
    stock failure on a credit sale must roll back every write issued so
    far — the `sales` row, the `sale_line_items` row, and the
    already-inserted (uncommitted) `credit_transactions` row — across all
    four tables, and leave `inventory_stock` untouched. Proves
    `record_sale()`'s single-commit/single-rollback boundary holds against
    a real migration-built database, not just the unit-level `init_schema`
    shortcut."""
    product = create_product(conn, name="Soap Bar", price=50, gst_rate=18)
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
    # inventory_stock's row count is unaffected either way (UPDATE, not
    # INSERT) — assert the quantity value itself is unchanged instead.
    assert get_stock(conn, product.id).quantity == stock_before
    assert get_outstanding_balance(conn, customer.id) == pytest.approx(0)
