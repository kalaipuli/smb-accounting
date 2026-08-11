"""Discount rules management service (P1-F3-T3).

Implements create/edit/apply for discount rules against the catalog items
produced by `src/services/catalog.py` (P1-F3-T2), backed by the `discounts`
table (`discount_type IN ('percentage', 'flat')`, created in P1-F1-T1).

No UI code here — `src/ui/catalog.py` (P1-F3-T4) is the only consumer.
"""

import sqlite3
from dataclasses import dataclass

from src.exceptions import (
    DiscountExceedsPriceError,
    DiscountNotFoundError,
    DiscountValidationError,
)

VALID_DISCOUNT_TYPES: frozenset[str] = frozenset({"percentage", "flat"})


@dataclass(frozen=True)
class Discount:
    id: int
    product_id: int
    discount_type: str
    value: float


def _validate_discount(discount_type: str, value: float) -> None:
    if discount_type not in VALID_DISCOUNT_TYPES:
        raise DiscountValidationError(
            f"discount_type {discount_type!r} must be one of "
            f"{sorted(VALID_DISCOUNT_TYPES)}"
        )
    if value < 0:
        raise DiscountValidationError(f"discount value {value!r} must not be negative")
    if discount_type == "percentage" and value > 100:
        raise DiscountValidationError(
            f"percentage discount value {value!r} must not exceed 100"
        )


def _get_product_price(conn: sqlite3.Connection, product_id: int) -> float | None:
    """Look up a product's current price. Returns `None` if it does not exist.

    A missing product is not raised here — the caller's own INSERT/UPDATE
    statement already surfaces that case as `DiscountValidationError` via
    its `sqlite3.IntegrityError` handler; duplicating that check here would
    diverge the two error messages for the same condition.
    """
    row = conn.execute(
        "SELECT price FROM products WHERE id = ?", (product_id,)
    ).fetchone()
    return row[0] if row is not None else None


def _validate_flat_value_against_price(
    conn: sqlite3.Connection, product_id: int, discount_type: str, value: float
) -> None:
    """Write-time guard: a flat discount must not exceed the product's
    *current* price (`Finding 1`, Tier 2 gate — `apply_discount` performs
    this same check at read/apply time, but a price edit made after a
    discount is created cannot be caught by this write-time check alone;
    the UI layer's `_render_discounts` degrades gracefully for that case)."""
    if discount_type != "flat":
        return
    price = _get_product_price(conn, product_id)
    if price is not None and value > price:
        raise DiscountValidationError(
            f"flat discount value {value!r} exceeds product {product_id}'s "
            f"current price {price!r}"
        )


def create_discount(
    conn: sqlite3.Connection, product_id: int, discount_type: str, value: float
) -> Discount:
    """Create a discount rule for a product.

    Raises `DiscountValidationError` for invalid `discount_type`/`value`
    (including a percentage above 100, or a flat value exceeding the
    product's current price), or when `product_id` does not reference an
    existing product (FK violation surfaced as a domain error).
    """
    _validate_discount(discount_type, value)
    _validate_flat_value_against_price(conn, product_id, discount_type, value)
    try:
        cursor = conn.execute(
            "INSERT INTO discounts (product_id, discount_type, value) VALUES (?, ?, ?)",
            (product_id, discount_type, value),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise DiscountValidationError(
            f"discount for product {product_id} failed database constraints "
            "(product may not exist)"
        ) from exc
    discount_id = cursor.lastrowid
    assert discount_id is not None
    return Discount(
        id=discount_id, product_id=product_id, discount_type=discount_type, value=value
    )


def update_discount(
    conn: sqlite3.Connection, discount_id: int, discount_type: str, value: float
) -> Discount:
    """Edit an existing discount rule's type/value.

    Raises `DiscountNotFoundError` when `discount_id` does not exist,
    `DiscountValidationError` on invalid field data (including a flat value
    exceeding the product's current price).
    """
    _validate_discount(discount_type, value)
    row = conn.execute(
        "SELECT product_id FROM discounts WHERE id = ?", (discount_id,)
    ).fetchone()
    if row is None:
        raise DiscountNotFoundError(f"discount {discount_id} does not exist")
    product_id = row[0]
    _validate_flat_value_against_price(conn, product_id, discount_type, value)
    try:
        conn.execute(
            "UPDATE discounts SET discount_type = ?, value = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (discount_type, value, discount_id),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise DiscountValidationError(
            f"discount {discount_id} failed database constraints"
        ) from exc
    return Discount(
        id=discount_id, product_id=product_id, discount_type=discount_type, value=value
    )


def list_discounts_for_product(
    conn: sqlite3.Connection, product_id: int
) -> list[Discount]:
    """Return every discount rule attached to a product."""
    rows = conn.execute(
        "SELECT id, product_id, discount_type, value FROM discounts "
        "WHERE product_id = ? ORDER BY id",
        (product_id,),
    ).fetchall()
    return [
        Discount(id=row[0], product_id=row[1], discount_type=row[2], value=row[3])
        for row in rows
    ]


def apply_discount(price: float, discount: Discount) -> float:
    """Apply a discount rule's math to a price and return the discounted price.

    Percentage discounts reduce `price` by `value` percent; flat discounts
    subtract `value` directly. Raises `DiscountExceedsPriceError` when the
    result would be negative (e.g. a flat discount larger than the price).
    """
    if discount.discount_type == "percentage":
        discounted_price = price * (1 - discount.value / 100)
    else:
        discounted_price = price - discount.value

    if discounted_price < 0:
        raise DiscountExceedsPriceError(
            f"discount {discount.value!r} ({discount.discount_type}) "
            f"exceeds price {price!r}"
        )
    return discounted_price
