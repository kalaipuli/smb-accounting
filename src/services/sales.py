"""Sale-entry service — GST-inclusive bill computation (P2-F1-T3) and the
atomic sale-commit transaction (P2-F1-T4/P2-F2-T4).

Computes a GST-inclusive bill total for a cart of line items against the
Phase 1 catalog (`src/services/catalog.py`) and any discount attached via
`src/services/discounts.py`, per `ADR-005` Decision (a)'s per-line
aggregation method: each line's own GST slab is computed and summed
independently — no single blended rate is ever applied across a cart that
spans multiple statutory slabs (0/5/12/18/28%, `ADR-002`(a)).

`record_sale()` is the sale-commit orchestrator (`ADR-005` Decision (c)):
one shared `sqlite3.Connection` is used across the `sales` insert, the
`sale_line_items` inserts, `src/services/inventory.py`'s `decrement_stock`
(`commit=False`), and — when `payment_status = 'credit'` —
`src/services/customers.py`'s `record_credit_sale` (`commit=False`). Exactly
one `conn.commit()` is issued after every step succeeds; any exception
during that sequence triggers `conn.rollback()` and raises
`SaleTransactionError` — no sub-step commits independently once composed.

No UI code here — `src/ui/sales.py` (`P2-F1-T5`) is the only consumer of
this module's public functions and exceptions.
"""

import sqlite3
from dataclasses import dataclass

from src.exceptions import SaleNotFoundError, SaleTransactionError, SaleValidationError
from src.services.catalog import get_product
from src.services.customers import get_customer, record_credit_sale
from src.services.discounts import Discount, apply_discount
from src.services.inventory import decrement_stock

_VALID_PAYMENT_STATUSES = ("paid", "credit")


@dataclass(frozen=True)
class CartLine:
    """One requested line item before bill computation.

    `discount`, when provided, must belong to `product_id` and is applied
    to the product's current unit price via `apply_discount`
    (`src/services/discounts.py`) — the same per-unit application the
    catalog UI already uses (`src/ui/catalog.py`'s `_render_discounts`),
    not re-derived here.
    """

    product_id: int
    quantity: int
    discount: Discount | None = None


@dataclass(frozen=True)
class SaleLineItem:
    """A computed, GST-inclusive line item — snapshotted per `ADR-005`
    Decision (b) for later persistence as one `sale_line_items` row by
    `P2-F1-T4`. `unit_price`/`gst_rate`/`discount_amount` are captured at
    computation time, not re-derived from a join at read time, so a later
    catalog edit cannot silently alter what this line actually charged.

    `unit_cost` is the per-line COGS snapshot (ADR-009 Decision (b)),
    captured by the identical mechanism: the product's `cost` as read in
    the same `_compute_line` call, independent of `discount_amount`
    (a discount changes what the customer paid, not what the shop paid).
    `None` when the product's cost is unknown — never `0`."""

    product_id: int
    quantity: int
    unit_price: float
    gst_rate: float
    discount_amount: float
    line_net_price: float
    line_gst_amount: float
    line_total: float
    unit_cost: float | None = None


@dataclass(frozen=True)
class Bill:
    """The GST-inclusive bill total for a cart, aggregated per `ADR-005`
    Decision (a) — `subtotal`/`gst_total` are each the sum of every line's
    own-slab figures; no blended rate is applied across differing slabs."""

    line_items: list[SaleLineItem]
    subtotal: float
    gst_total: float
    bill_total: float


def _compute_line(conn: sqlite3.Connection, cart_line: CartLine) -> SaleLineItem:
    if cart_line.quantity <= 0:
        raise SaleValidationError(
            f"cart line quantity {cart_line.quantity!r} must be positive"
        )
    product = get_product(conn, cart_line.product_id)

    if cart_line.discount is not None:
        if cart_line.discount.product_id != product.id:
            raise SaleValidationError(
                f"discount {cart_line.discount.id!r} belongs to product "
                f"{cart_line.discount.product_id!r}, not {product.id!r}"
            )
        discounted_unit_price = apply_discount(product.price, cart_line.discount)
        discount_amount = (product.price - discounted_unit_price) * cart_line.quantity
    else:
        discount_amount = 0.0

    line_net_price = (product.price * cart_line.quantity) - discount_amount
    line_gst_amount = line_net_price * (product.gst_rate / 100)
    line_total = line_net_price + line_gst_amount

    return SaleLineItem(
        product_id=product.id,
        quantity=cart_line.quantity,
        unit_price=product.price,
        gst_rate=product.gst_rate,
        discount_amount=discount_amount,
        line_net_price=line_net_price,
        line_gst_amount=line_gst_amount,
        line_total=line_total,
        unit_cost=product.cost,
    )


def compute_bill(conn: sqlite3.Connection, cart: list[CartLine]) -> Bill:
    """Compute the GST-inclusive bill total for a cart of line items.

    Raises `SaleValidationError` for an empty cart, a non-positive
    quantity on any line, or a discount that does not belong to its
    line's product. Propagates `ProductNotFoundError`
    (`src/services/catalog.py`) if a line references a non-existent
    product, and `DiscountExceedsPriceError`
    (`src/services/discounts.py`) if a line's discount would take its
    unit price below zero.
    """
    if not cart:
        raise SaleValidationError("cart must contain at least one line item")

    line_items = [_compute_line(conn, cart_line) for cart_line in cart]
    subtotal = sum(line.line_net_price for line in line_items)
    gst_total = sum(line.line_gst_amount for line in line_items)
    bill_total = subtotal + gst_total

    return Bill(
        line_items=line_items,
        subtotal=subtotal,
        gst_total=gst_total,
        bill_total=bill_total,
    )


@dataclass(frozen=True)
class Sale:
    """A committed sale — the persisted result of `record_sale()`, or a row
    read back by `list_sales()`/`get_sale()` (phase2-feature6-sales-
    customer-gaps, Action 3). `created_at` defaults to `""` for a
    freshly-`record_sale()`-committed `Sale` (the caller already has the
    commit-time context; this module does not re-query it) and is always
    populated for `Sale`s returned by `list_sales()`/`get_sale()`."""

    id: int
    customer_id: int | None
    payment_status: str
    subtotal: float
    gst_total: float
    bill_total: float
    line_items: list[SaleLineItem]
    created_at: str = ""


def _insert_sale_line_item(
    conn: sqlite3.Connection, sale_id: int, line: SaleLineItem
) -> None:
    conn.execute(
        "INSERT INTO sale_line_items "
        "(sale_id, product_id, quantity, unit_price, gst_rate, "
        "discount_amount, line_gst_amount, line_total, unit_cost) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            sale_id,
            line.product_id,
            line.quantity,
            line.unit_price,
            line.gst_rate,
            line.discount_amount,
            line.line_gst_amount,
            line.line_total,
            line.unit_cost,
        ),
    )


def record_sale(
    conn: sqlite3.Connection,
    cart: list[CartLine],
    payment_status: str,
    customer_id: int | None = None,
) -> Sale:
    """Commit a sale atomically: `sales` insert, `sale_line_items` inserts,
    inventory decrement, and — when `payment_status = 'credit'` — the
    customer credit-balance update, all in a single transaction
    (`ADR-005` Decision (c), composed with `ADR-006` Decision (c)).

    Validation and bill computation (`compute_bill`) happen before any
    write is issued — `SaleValidationError` (bad `payment_status`, a
    `credit` sale with no `customer_id`, an empty cart, a non-positive
    quantity, or a mismatched discount), `ProductNotFoundError`, and
    `DiscountExceedsPriceError` all propagate unwrapped from this
    pre-check phase, mirroring `src/services/inventory.py`'s
    validate-before-mutate convention. A `credit` sale's `customer_id` is
    also validated to exist before the transaction begins — `SaleTransactionError`
    is reserved for failures once the atomic write sequence has started (a
    changed customer mid-sequence, insufficient stock, or any other write
    failure); at that point `conn.rollback()` is issued and no partial
    commit can occur.
    """
    if payment_status not in _VALID_PAYMENT_STATUSES:
        raise SaleValidationError(
            f"payment_status {payment_status!r} must be one of "
            f"{_VALID_PAYMENT_STATUSES!r}"
        )
    if payment_status == "credit" and customer_id is None:
        raise SaleValidationError("a credit sale requires a customer_id")

    bill = compute_bill(conn, cart)

    if payment_status == "credit":
        assert customer_id is not None
        get_customer(conn, customer_id)  # raises CustomerNotFoundError if absent

    try:
        cursor = conn.execute(
            "INSERT INTO sales "
            "(customer_id, payment_status, subtotal, gst_total, bill_total) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                customer_id,
                payment_status,
                bill.subtotal,
                bill.gst_total,
                bill.bill_total,
            ),
        )
        sale_id = cursor.lastrowid
        assert sale_id is not None

        for line in bill.line_items:
            _insert_sale_line_item(conn, sale_id, line)

        if payment_status == "credit":
            assert customer_id is not None
            record_credit_sale(
                conn, customer_id, sale_id, bill.bill_total, commit=False
            )

        for line in bill.line_items:
            decrement_stock(conn, line.product_id, line.quantity, commit=False)

        conn.commit()
    except Exception as exc:
        conn.rollback()
        raise SaleTransactionError(
            f"sale transaction failed and was rolled back: {exc}"
        ) from exc

    return Sale(
        id=sale_id,
        customer_id=customer_id,
        payment_status=payment_status,
        subtotal=bill.subtotal,
        gst_total=bill.gst_total,
        bill_total=bill.bill_total,
        line_items=bill.line_items,
    )


def list_sales(
    conn: sqlite3.Connection, customer_id: int | None = None, limit: int = 100
) -> list[Sale]:
    """Return committed sales, most-recent-first, capped at `limit` rows
    (phase2-feature6-sales-customer-gaps, Action 3 — the "general sales
    list"/"per-customer history" query, addressing architect finding 2's
    "no query function exists" gap). Header-level rows only —
    `line_items` is always `[]` here; call `get_sale()` for full
    line-item detail on one sale.

    When `customer_id` is given, only that customer's sales are returned
    (the per-customer drill-down from `src/ui/customers.py`); otherwise
    every sale is returned. Always ordered `created_at DESC`.
    """
    query = (
        "SELECT id, customer_id, payment_status, subtotal, gst_total, "
        "bill_total, created_at FROM sales"
    )
    params: tuple[object, ...]
    if customer_id is not None:
        query += " WHERE customer_id = ?"
        params = (customer_id, limit)
    else:
        params = (limit,)
    # `created_at`'s millisecond-precision timestamp (`src/db/schema.py`)
    # can tie between two sales committed in the same millisecond; `id
    # DESC` is a deterministic secondary key so "most-recent-first" still
    # holds for ties, rather than leaving SQLite's tie-break order
    # unspecified.
    query += " ORDER BY created_at DESC, id DESC LIMIT ?"
    rows = conn.execute(query, params).fetchall()
    return [
        Sale(
            id=row[0],
            customer_id=row[1],
            payment_status=row[2],
            subtotal=row[3],
            gst_total=row[4],
            bill_total=row[5],
            line_items=[],
            created_at=row[6],
        )
        for row in rows
    ]


def get_sale(conn: sqlite3.Connection, sale_id: int) -> Sale:
    """Fetch a single sale with full line-item detail, for a "view
    invoice" detail screen (phase2-feature6-sales-customer-gaps, Action
    3). Raises `SaleNotFoundError` if absent, mirroring
    `src/services/customers.py::get_customer`'s not-found convention.

    `sale_line_items` does not persist `line_net_price` (only
    `discount_amount`/`line_gst_amount`/`line_total`) — it is derived here
    as `line_total - line_gst_amount`, the same relationship
    `_compute_line` establishes at write time (`line_total =
    line_net_price + line_gst_amount`), so no value is re-derived
    incorrectly.
    """
    row = conn.execute(
        "SELECT id, customer_id, payment_status, subtotal, gst_total, "
        "bill_total, created_at FROM sales WHERE id = ?",
        (sale_id,),
    ).fetchone()
    if row is None:
        raise SaleNotFoundError(f"sale {sale_id} does not exist")

    line_rows = conn.execute(
        "SELECT product_id, quantity, unit_price, gst_rate, discount_amount, "
        "line_gst_amount, line_total, unit_cost FROM sale_line_items "
        "WHERE sale_id = ? ORDER BY id",
        (sale_id,),
    ).fetchall()
    line_items = [
        SaleLineItem(
            product_id=line_row[0],
            quantity=line_row[1],
            unit_price=line_row[2],
            gst_rate=line_row[3],
            discount_amount=line_row[4],
            line_net_price=line_row[6] - line_row[5],
            line_gst_amount=line_row[5],
            line_total=line_row[6],
            unit_cost=line_row[7],
        )
        for line_row in line_rows
    ]

    return Sale(
        id=row[0],
        customer_id=row[1],
        payment_status=row[2],
        subtotal=row[3],
        gst_total=row[4],
        bill_total=row[5],
        line_items=line_items,
        created_at=row[6],
    )
