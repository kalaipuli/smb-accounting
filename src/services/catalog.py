"""Product catalog CRUD service (P1-F3-T2).

Implements add/edit/get/list for products against the `products` table
(created in P1-F1-T1), applying the GST rate/slab design finalized by
ADR-002 (Accepted — `docs/adr/ADR-002-gst-rate-slabs-design.md`).

No UI code here — `src/ui/catalog.py` (P1-F3-T4) is the only consumer of
this module's public functions and exceptions. No billing/GST-amount
calculation here either — that is `DLVR-2-01`, Phase 2, per ADR-002 §(c).
"""

import sqlite3
from dataclasses import dataclass

from src.exceptions import (
    GstRateInvalidError,
    ProductNotFoundError,
    ProductValidationError,
)

# Five statutory GST slabs per ADR-002 §(a). No other value is a valid
# `products.gst_rate` in this system's scope.
VALID_GST_RATES: frozenset[float] = frozenset({0, 5, 12, 18, 28})


@dataclass(frozen=True)
class Product:
    id: int
    name: str
    price: float
    gst_rate: float
    cost: float | None = None


def _validate_name(name: str) -> None:
    if not name or not name.strip():
        raise ProductValidationError("product name must not be empty")


def _validate_price(price: float) -> None:
    if price < 0:
        raise ProductValidationError(f"price {price!r} must not be negative")


def _validate_cost(cost: float | None) -> None:
    # `None` means "cost unknown" (ADR-009 Decision (a)) — never validated
    # against the non-negative check, which applies only to a known value.
    if cost is not None and cost < 0:
        raise ProductValidationError(f"cost {cost!r} must not be negative")


def _validate_gst_rate(gst_rate: float) -> None:
    if gst_rate not in VALID_GST_RATES:
        raise GstRateInvalidError(
            f"gst_rate {gst_rate!r} is not one of the valid GST slabs "
            f"{sorted(VALID_GST_RATES)}"
        )


def create_product(
    conn: sqlite3.Connection,
    name: str,
    price: float,
    gst_rate: float,
    cost: float | None = None,
) -> Product:
    """Add a new product to the catalog.

    Raises `ProductValidationError` for an empty name, negative price, or
    negative cost, `GstRateInvalidError` for an out-of-slab GST rate
    (ADR-002 §(a)). `cost` is keyword-defaulted to `None` ("cost unknown",
    ADR-009 Decision (a)) so every existing call site keeps working
    unchanged.
    """
    _validate_name(name)
    _validate_price(price)
    _validate_gst_rate(gst_rate)
    _validate_cost(cost)
    try:
        cursor = conn.execute(
            "INSERT INTO products (name, price, gst_rate, cost) VALUES (?, ?, ?, ?)",
            (name, price, gst_rate, cost),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise ProductValidationError(
            f"product {name!r} failed database constraints"
        ) from exc
    product_id = cursor.lastrowid
    assert product_id is not None
    return Product(id=product_id, name=name, price=price, gst_rate=gst_rate, cost=cost)


def update_product(
    conn: sqlite3.Connection,
    product_id: int,
    name: str,
    price: float,
    gst_rate: float,
    cost: float | None = None,
) -> Product:
    """Edit an existing product's name/price/gst_rate/cost.

    Raises `ProductNotFoundError` when `product_id` does not exist,
    `ProductValidationError`/`GstRateInvalidError` on invalid field data.
    `cost` is keyword-defaulted to `None` so every existing call site keeps
    working unchanged (ADR-009 Decision (a)/Consequences "Backward
    compatibility").
    """
    _validate_name(name)
    _validate_price(price)
    _validate_gst_rate(gst_rate)
    _validate_cost(cost)
    try:
        cursor = conn.execute(
            "UPDATE products SET name = ?, price = ?, gst_rate = ?, cost = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (name, price, gst_rate, cost, product_id),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise ProductValidationError(
            f"product {product_id} failed database constraints"
        ) from exc
    if cursor.rowcount == 0:
        raise ProductNotFoundError(f"product {product_id} does not exist")
    return Product(id=product_id, name=name, price=price, gst_rate=gst_rate, cost=cost)


def get_product(conn: sqlite3.Connection, product_id: int) -> Product:
    """Fetch a single product by id. Raises `ProductNotFoundError` if absent."""
    row = conn.execute(
        "SELECT id, name, price, gst_rate, cost FROM products WHERE id = ?",
        (product_id,),
    ).fetchone()
    if row is None:
        raise ProductNotFoundError(f"product {product_id} does not exist")
    return Product(id=row[0], name=row[1], price=row[2], gst_rate=row[3], cost=row[4])


def list_products(conn: sqlite3.Connection) -> list[Product]:
    """Return every product in the catalog, ordered by name."""
    rows = conn.execute(
        "SELECT id, name, price, gst_rate, cost FROM products ORDER BY name"
    ).fetchall()
    return [
        Product(id=row[0], name=row[1], price=row[2], gst_rate=row[3], cost=row[4])
        for row in rows
    ]
