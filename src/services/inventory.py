"""Inventory stock-level tracking service (P1-F4-T1, P1-F4-T2).

Implements stock level read/adjust against the `inventory_stock` table
(created in P1-F1-T1 — `src/db/migrations/0001_up.sql` /
`src/db/schema.py`), keyed by catalog product IDs from Feature 3's
`products` table (`src/services/catalog.py`).

`decrement_stock` is the inventory-side mutation hook Phase 2's sale-entry
transaction (DLVR-1-03 / DLVR-2-01) will call when a sale reduces stock.
It is deliberately a pure inventory-side contract — no sale/transaction
commit logic lives here; that belongs to Phase 2's DLVR-2-01, per this
feature's Expectations.

No UI code here — `src/ui/inventory.py` (P1-F4-T3) is the only consumer of
this module's public functions and exceptions.
"""

import sqlite3
from dataclasses import dataclass

from src.exceptions import (
    InsufficientStockError,
    InventoryValidationError,
    ProductNotFoundError,
)


@dataclass(frozen=True)
class StockLevel:
    product_id: int
    quantity: int


def _validate_product_exists(conn: sqlite3.Connection, product_id: int) -> None:
    row = conn.execute("SELECT 1 FROM products WHERE id = ?", (product_id,)).fetchone()
    if row is None:
        raise ProductNotFoundError(f"product {product_id} does not exist")


def get_stock(conn: sqlite3.Connection, product_id: int) -> StockLevel:
    """Fetch the current stock level for a product.

    A product with no `inventory_stock` row yet (never adjusted) reads as
    quantity 0 rather than raising — `inventory_stock` holds current state
    only (ARCH-D04), and a catalog product legitimately starts with no
    stock recorded. Raises `ProductNotFoundError` if the product itself
    does not exist in the catalog.
    """
    _validate_product_exists(conn, product_id)
    row = conn.execute(
        "SELECT quantity FROM inventory_stock WHERE product_id = ?",
        (product_id,),
    ).fetchone()
    if row is None:
        return StockLevel(product_id=product_id, quantity=0)
    return StockLevel(product_id=product_id, quantity=row[0])


def set_stock(
    conn: sqlite3.Connection, product_id: int, quantity: int, commit: bool = True
) -> StockLevel:
    """Set the absolute stock level for a product (manual adjust screen).

    Raises `InventoryValidationError` for a negative `quantity`,
    `ProductNotFoundError` if the product does not exist.

    `commit` (`ADR-005` Decision (c)) defaults to `True`, preserving the
    existing per-call-commit behaviour for every Phase 1 caller
    (`src/ui/inventory.py`'s manual-adjust screen). Pass `commit=False` to
    compose this call into a larger caller-owned transaction (e.g. Phase
    2's `src/services/sales.py` sale-commit orchestrator) — the caller is
    then responsible for issuing `conn.commit()`/`conn.rollback()`.
    """
    if quantity < 0:
        raise InventoryValidationError(f"quantity {quantity!r} must not be negative")
    _validate_product_exists(conn, product_id)
    conn.execute(
        "INSERT INTO inventory_stock (product_id, quantity) VALUES (?, ?) "
        "ON CONFLICT(product_id) DO UPDATE SET "
        "quantity = excluded.quantity, "
        "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
        (product_id, quantity),
    )
    if commit:
        conn.commit()
    return StockLevel(product_id=product_id, quantity=quantity)


def adjust_stock(
    conn: sqlite3.Connection, product_id: int, delta: int, commit: bool = True
) -> StockLevel:
    """Adjust stock by a relative delta (positive restocks, negative sells).

    Raises `InsufficientStockError` if the adjustment would take stock
    below zero (the negative-stock guard), `ProductNotFoundError` if the
    product does not exist.

    `commit` (`ADR-005` Decision (c)) — see `set_stock`'s docstring; passed
    through unchanged.
    """
    current = get_stock(conn, product_id)
    new_quantity = current.quantity + delta
    if new_quantity < 0:
        raise InsufficientStockError(
            f"adjusting product {product_id} by {delta!r} would take stock "
            f"below zero (current={current.quantity!r})"
        )
    return set_stock(conn, product_id, new_quantity, commit=commit)


def decrement_stock(
    conn: sqlite3.Connection, product_id: int, qty: int, commit: bool = True
) -> StockLevel:
    """Inventory-side mutation hook for Phase 2's sale-entry transaction
    (DLVR-1-03, reached through DLVR-2-01) to call when a sale reduces
    stock.

    Pure inventory-side mutation contract only — deliberately stops short
    of any sale/transaction commit logic, which belongs to Phase 2's
    DLVR-2-01. Raises `InventoryValidationError` for a non-positive `qty`,
    `InsufficientStockError` if stock would go negative (the negative-stock
    guard), `ProductNotFoundError` if the product does not exist.

    `commit` (`ADR-005` Decision (c)) — defaults to `True` for any
    standalone caller; `src/services/sales.py`'s `record_sale()` passes
    `commit=False` to compose this call into its single atomic
    sale-commit transaction.
    """
    if qty <= 0:
        raise InventoryValidationError(f"decrement qty {qty!r} must be positive")
    return adjust_stock(conn, product_id, -qty, commit=commit)
