"""Unit tests for src/services/catalog.py (P1-F3-T2, P1-F3-T5).

Covers product CRUD (create/update/get/list) and GST slab validation
(ADR-002 §(a)) against a throwaway file-backed SQLite database, plus
error-path coverage for the sqlite3 I/O boundary (negative price/FK
failures surfaced as domain exceptions, not raw `sqlite3.IntegrityError`).
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.db.schema import connect, init_schema
from src.exceptions import (
    GstRateInvalidError,
    ProductNotFoundError,
    ProductValidationError,
)
from src.services.catalog import (
    Product,
    create_product,
    get_product,
    list_products,
    update_product,
)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "data.db")
    init_schema(connection)
    yield connection
    connection.close()


# --- create_product ----------------------------------------------------------


def test_create_product_when_valid_returns_persisted_product(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)

    assert product == Product(id=product.id, name="Rice 1kg", price=60, gst_rate=5)
    row = conn.execute(
        "SELECT name, price, gst_rate FROM products WHERE id = ?", (product.id,)
    ).fetchone()
    assert row == ("Rice 1kg", 60, 5)


def test_create_product_when_gst_rate_not_in_slab_set_raises_gst_rate_invalid_error(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(GstRateInvalidError):
        create_product(conn, name="Odd Item", price=10, gst_rate=15)


@pytest.mark.parametrize("gst_rate", [0, 5, 12, 18, 28])
def test_create_product_when_gst_rate_is_a_valid_slab_succeeds(
    conn: sqlite3.Connection, gst_rate: int
) -> None:
    product = create_product(conn, name="Item", price=10, gst_rate=gst_rate)

    assert product.gst_rate == gst_rate


def test_create_product_when_price_negative_raises_product_validation_error(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(ProductValidationError):
        create_product(conn, name="Bad Item", price=-1, gst_rate=5)


def test_create_product_when_name_empty_raises_product_validation_error(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(ProductValidationError):
        create_product(conn, name="  ", price=10, gst_rate=5)


# --- create_product / update_product: cost (ADR-009 Decision (a)) ----------


def test_create_product_when_cost_omitted_defaults_to_none(
    conn: sqlite3.Connection,
) -> None:
    """ADR-009 Decision (a) — `cost` is keyword-defaulted to `None` so
    every existing call site (no `cost` argument) keeps working unchanged."""
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)

    assert product.cost is None
    assert get_product(conn, product.id).cost is None


def test_create_product_when_cost_given_persists_it(conn: sqlite3.Connection) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5, cost=45)

    assert product.cost == 45
    assert get_product(conn, product.id).cost == 45


def test_create_product_when_cost_zero_is_accepted_and_distinct_from_none(
    conn: sqlite3.Connection,
) -> None:
    """`cost=0` (a genuinely free purchase price) must not be conflated
    with `cost=None` ("cost unknown") — ADR-009 Decision (a)."""
    product = create_product(conn, name="Free Sample", price=0, gst_rate=0, cost=0)

    assert product.cost == 0
    assert product.cost is not None


def test_create_product_when_cost_negative_raises_product_validation_error(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(ProductValidationError):
        create_product(conn, name="Bad Item", price=10, gst_rate=5, cost=-1)


def test_update_product_when_cost_given_persists_it(conn: sqlite3.Connection) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)

    updated = update_product(
        conn, product.id, name="Rice 1kg", price=60, gst_rate=5, cost=42
    )

    assert updated.cost == 42
    assert get_product(conn, product.id).cost == 42


def test_update_product_when_cost_omitted_resets_to_none(
    conn: sqlite3.Connection,
) -> None:
    """`cost` is keyword-defaulted to `None` on `update_product` too — an
    edit call that does not pass `cost` writes `None`, mirroring how every
    other field on this call is a full replacement, not a partial patch."""
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5, cost=45)

    updated = update_product(conn, product.id, name="Rice 1kg", price=60, gst_rate=5)

    assert updated.cost is None
    assert get_product(conn, product.id).cost is None


def test_update_product_when_cost_negative_raises_product_validation_error(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)

    with pytest.raises(ProductValidationError):
        update_product(conn, product.id, name="Rice 1kg", price=60, gst_rate=5, cost=-5)


# --- update_product ------------------------------------------------------------


def test_update_product_when_product_exists_updates_all_fields(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)

    updated = update_product(conn, product.id, name="Rice 5kg", price=280, gst_rate=0)

    assert updated == Product(id=product.id, name="Rice 5kg", price=280, gst_rate=0)
    assert get_product(conn, product.id) == updated


def test_update_product_when_product_missing_raises_product_not_found_error(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(ProductNotFoundError):
        update_product(conn, 999, name="Ghost", price=10, gst_rate=5)


def test_update_product_when_gst_rate_not_in_slab_set_raises_gst_rate_invalid_error(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)

    with pytest.raises(GstRateInvalidError):
        update_product(conn, product.id, name="Rice 1kg", price=60, gst_rate=7)


# --- get_product ---------------------------------------------------------------


def test_get_product_when_product_exists_returns_it(conn: sqlite3.Connection) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)

    assert get_product(conn, product.id) == product


def test_get_product_when_product_missing_raises_product_not_found_error(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(ProductNotFoundError):
        get_product(conn, 999)


# --- list_products ---------------------------------------------------------------


def test_list_products_when_catalog_empty_returns_empty_list(
    conn: sqlite3.Connection,
) -> None:
    assert list_products(conn) == []


def test_list_products_when_multiple_products_returns_them_ordered_by_name(
    conn: sqlite3.Connection,
) -> None:
    create_product(conn, name="Sugar 1kg", price=45, gst_rate=5)
    create_product(conn, name="Atta 5kg", price=250, gst_rate=0)

    names = [product.name for product in list_products(conn)]

    assert names == ["Atta 5kg", "Sugar 1kg"]
