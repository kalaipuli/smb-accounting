"""Unit tests for src/services/inventory.py (P1-F4-T1, P1-F4-T2, P1-F4-T4).

Covers stock read/set/adjust/decrement against a throwaway file-backed
SQLite database, including the negative-stock guard and the
`decrement_stock` hook Phase 2's sale-entry transaction will call.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.db.schema import connect, init_schema
from src.exceptions import (
    InsufficientStockError,
    InventoryValidationError,
    ProductNotFoundError,
)
from src.services.catalog import create_product
from src.services.inventory import (
    StockLevel,
    adjust_stock,
    decrement_stock,
    get_stock,
    set_stock,
)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "data.db")
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def product_id(conn: sqlite3.Connection) -> int:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    return product.id


# --- get_stock ----------------------------------------------------------------


def test_get_stock_when_no_stock_row_exists_returns_zero_quantity(
    conn: sqlite3.Connection, product_id: int
) -> None:
    assert get_stock(conn, product_id) == StockLevel(product_id=product_id, quantity=0)


def test_get_stock_when_product_missing_raises_product_not_found_error(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(ProductNotFoundError):
        get_stock(conn, 999)


# --- set_stock ------------------------------------------------------------------


def test_set_stock_when_no_existing_row_creates_it(
    conn: sqlite3.Connection, product_id: int
) -> None:
    result = set_stock(conn, product_id, 50)

    assert result == StockLevel(product_id=product_id, quantity=50)
    assert get_stock(conn, product_id).quantity == 50


def test_set_stock_when_existing_row_overwrites_quantity(
    conn: sqlite3.Connection, product_id: int
) -> None:
    set_stock(conn, product_id, 50)

    result = set_stock(conn, product_id, 20)

    assert result.quantity == 20
    assert get_stock(conn, product_id).quantity == 20


def test_set_stock_when_quantity_negative_raises_inventory_validation_error(
    conn: sqlite3.Connection, product_id: int
) -> None:
    with pytest.raises(InventoryValidationError):
        set_stock(conn, product_id, -1)


def test_set_stock_when_product_missing_raises_product_not_found_error(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(ProductNotFoundError):
        set_stock(conn, 999, 10)


# --- adjust_stock -----------------------------------------------------------------


def test_adjust_stock_when_positive_delta_increases_quantity(
    conn: sqlite3.Connection, product_id: int
) -> None:
    set_stock(conn, product_id, 10)

    result = adjust_stock(conn, product_id, 5)

    assert result.quantity == 15


def test_adjust_stock_when_negative_delta_decreases_quantity(
    conn: sqlite3.Connection, product_id: int
) -> None:
    set_stock(conn, product_id, 10)

    result = adjust_stock(conn, product_id, -4)

    assert result.quantity == 6


def test_adjust_stock_when_delta_would_take_stock_negative_raises_insufficient_stock_error(  # noqa: E501
    conn: sqlite3.Connection, product_id: int
) -> None:
    set_stock(conn, product_id, 3)

    with pytest.raises(InsufficientStockError):
        adjust_stock(conn, product_id, -4)

    # Negative-stock guard: the failed adjustment must not have partially
    # applied — quantity is unchanged.
    assert get_stock(conn, product_id).quantity == 3


def test_adjust_stock_when_product_missing_raises_product_not_found_error(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(ProductNotFoundError):
        adjust_stock(conn, 999, 5)


# --- decrement_stock (P1-F4-T2 hook) -----------------------------------------------


def test_decrement_stock_when_sufficient_stock_reduces_quantity(
    conn: sqlite3.Connection, product_id: int
) -> None:
    set_stock(conn, product_id, 10)

    result = decrement_stock(conn, product_id, 3)

    assert result.quantity == 7
    assert get_stock(conn, product_id).quantity == 7


def test_decrement_stock_when_qty_exceeds_stock_raises_insufficient_stock_error(
    conn: sqlite3.Connection, product_id: int
) -> None:
    set_stock(conn, product_id, 2)

    with pytest.raises(InsufficientStockError):
        decrement_stock(conn, product_id, 3)

    assert get_stock(conn, product_id).quantity == 2


@pytest.mark.parametrize("qty", [0, -1])
def test_decrement_stock_when_qty_not_positive_raises_inventory_validation_error(
    conn: sqlite3.Connection, product_id: int, qty: int
) -> None:
    set_stock(conn, product_id, 10)

    with pytest.raises(InventoryValidationError):
        decrement_stock(conn, product_id, qty)


def test_decrement_stock_when_product_missing_raises_product_not_found_error(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(ProductNotFoundError):
        decrement_stock(conn, 999, 1)
