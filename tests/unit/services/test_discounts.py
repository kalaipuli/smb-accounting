"""Unit tests for src/services/discounts.py (P1-F3-T3, P1-F3-T5).

Covers discount create/edit/apply against a throwaway file-backed SQLite
database, discount-type/value validation, and the discount-exceeds-price
edge case per this feature's Expectations.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.db.schema import connect, init_schema
from src.exceptions import (
    DiscountExceedsPriceError,
    DiscountNotFoundError,
    DiscountValidationError,
)
from src.services.catalog import create_product
from src.services.discounts import (
    Discount,
    apply_discount,
    create_discount,
    list_discounts_for_product,
    update_discount,
)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "data.db")
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def product_id(conn: sqlite3.Connection) -> int:
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)
    return product.id


# --- create_discount -------------------------------------------------------


def test_create_discount_when_valid_percentage_returns_persisted_discount(
    conn: sqlite3.Connection, product_id: int
) -> None:
    discount = create_discount(conn, product_id, discount_type="percentage", value=10)

    assert discount == Discount(
        id=discount.id, product_id=product_id, discount_type="percentage", value=10
    )
    row = conn.execute(
        "SELECT discount_type, value FROM discounts WHERE id = ?", (discount.id,)
    ).fetchone()
    assert row == ("percentage", 10)


def test_create_discount_when_valid_flat_returns_persisted_discount(
    conn: sqlite3.Connection, product_id: int
) -> None:
    discount = create_discount(conn, product_id, discount_type="flat", value=15)

    assert discount.discount_type == "flat"
    assert discount.value == 15


def test_create_discount_when_type_invalid_raises_discount_validation_error(
    conn: sqlite3.Connection, product_id: int
) -> None:
    with pytest.raises(DiscountValidationError):
        create_discount(conn, product_id, discount_type="buy-one-get-one", value=10)


def test_create_discount_when_value_negative_raises_discount_validation_error(
    conn: sqlite3.Connection, product_id: int
) -> None:
    with pytest.raises(DiscountValidationError):
        create_discount(conn, product_id, discount_type="flat", value=-5)


def test_create_discount_when_percentage_exceeds_100_raises_discount_validation_error(
    conn: sqlite3.Connection, product_id: int
) -> None:
    with pytest.raises(DiscountValidationError):
        create_discount(conn, product_id, discount_type="percentage", value=101)


def test_create_discount_when_product_missing_raises_discount_validation_error(
    conn: sqlite3.Connection,
) -> None:
    """Error-path coverage: the sqlite3 FK IntegrityError raised by the
    `discounts.product_id` foreign key must be surfaced as a domain
    exception, not propagate as a raw `sqlite3.IntegrityError`."""
    with pytest.raises(DiscountValidationError):
        create_discount(conn, 999, discount_type="flat", value=5)


def test_create_discount_when_flat_value_exceeds_price_raises_validation_error(
    conn: sqlite3.Connection, product_id: int
) -> None:
    """Write-time guard (Tier 2 gate Finding 1 / SEV-001): a flat discount
    larger than the product's current price (100) must be rejected at
    creation, not only later when `apply_discount` is called."""
    with pytest.raises(DiscountValidationError):
        create_discount(conn, product_id, discount_type="flat", value=150)


def test_create_discount_when_flat_value_equals_product_price_is_allowed(
    conn: sqlite3.Connection, product_id: int
) -> None:
    discount = create_discount(conn, product_id, discount_type="flat", value=100)

    assert discount.value == 100


# --- update_discount ---------------------------------------------------------


def test_update_discount_when_discount_exists_updates_type_and_value(
    conn: sqlite3.Connection, product_id: int
) -> None:
    discount = create_discount(conn, product_id, discount_type="flat", value=15)

    updated = update_discount(conn, discount.id, discount_type="percentage", value=20)

    assert updated == Discount(
        id=discount.id, product_id=product_id, discount_type="percentage", value=20
    )


def test_update_discount_when_discount_missing_raises_discount_not_found_error(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(DiscountNotFoundError):
        update_discount(conn, 999, discount_type="flat", value=5)


def test_update_discount_when_value_invalid_raises_discount_validation_error(
    conn: sqlite3.Connection, product_id: int
) -> None:
    discount = create_discount(conn, product_id, discount_type="flat", value=15)

    with pytest.raises(DiscountValidationError):
        update_discount(conn, discount.id, discount_type="percentage", value=150)


def test_update_discount_when_flat_value_exceeds_price_raises_validation_error(
    conn: sqlite3.Connection, product_id: int
) -> None:
    """Write-time guard (Tier 2 gate Finding 1 / SEV-001): editing a
    discount to a flat value larger than the product's current price (100)
    must be rejected, not only later when `apply_discount` is called."""
    discount = create_discount(conn, product_id, discount_type="percentage", value=10)

    with pytest.raises(DiscountValidationError):
        update_discount(conn, discount.id, discount_type="flat", value=150)


# --- list_discounts_for_product -----------------------------------------------


def test_list_discounts_for_product_when_none_exist_returns_empty_list(
    conn: sqlite3.Connection, product_id: int
) -> None:
    assert list_discounts_for_product(conn, product_id) == []


def test_list_discounts_for_product_when_multiple_exist_returns_all(
    conn: sqlite3.Connection, product_id: int
) -> None:
    create_discount(conn, product_id, discount_type="flat", value=5)
    create_discount(conn, product_id, discount_type="percentage", value=10)

    discounts = list_discounts_for_product(conn, product_id)

    assert len(discounts) == 2


# --- apply_discount (pure math) -----------------------------------------------


def test_apply_discount_when_percentage_reduces_price_proportionally() -> None:
    discount = Discount(id=1, product_id=1, discount_type="percentage", value=10)

    assert apply_discount(100, discount) == 90


def test_apply_discount_when_flat_subtracts_value() -> None:
    discount = Discount(id=1, product_id=1, discount_type="flat", value=15)

    assert apply_discount(100, discount) == 85


def test_apply_discount_when_flat_exceeds_price_raises_exceeds_price_error() -> None:
    discount = Discount(id=1, product_id=1, discount_type="flat", value=150)

    with pytest.raises(DiscountExceedsPriceError):
        apply_discount(100, discount)


def test_apply_discount_when_flat_discount_equals_price_returns_zero() -> None:
    discount = Discount(id=1, product_id=1, discount_type="flat", value=100)

    assert apply_discount(100, discount) == 0
