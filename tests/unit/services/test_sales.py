"""Unit tests for src/services/sales.py (P2-F1-T3, P2-F1-T4/P2-F2-T4).

Covers GST-inclusive bill computation against a throwaway file-backed
SQLite database: single-slab lines, mixed-GST-slab carts (per ADR-005
Decision (a) — no blended rate across differing slabs), discount
interaction (percentage and flat), and validation/error-path coverage.

Also covers `record_sale()`'s single-transaction commit (`ADR-005`
Decision (c), composed with `ADR-006` Decision (c)) — the success path
for both `paid` and `credit` sales, and rollback-on-failure across the
full sequence (insufficient stock, missing customer), tagged so
`pytest ... -k transaction` selects them, per this feature's DoD sensor.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.db.schema import connect, init_schema
from src.exceptions import (
    CustomerNotFoundError,
    DiscountExceedsPriceError,
    InsufficientStockError,
    ProductNotFoundError,
    SaleNotFoundError,
    SaleTransactionError,
    SaleValidationError,
)
from src.services.catalog import create_product, get_product, update_product
from src.services.customers import create_customer, get_outstanding_balance
from src.services.discounts import Discount, create_discount
from src.services.inventory import get_stock, set_stock
from src.services.sales import (
    CartLine,
    Sale,
    SaleLineItem,
    compute_bill,
    get_sale,
    list_sales,
    record_sale,
)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "data.db")
    init_schema(connection)
    yield connection
    connection.close()


# --- compute_bill: single line, no discount ---------------------------------


def test_compute_bill_when_single_line_no_discount_returns_gst_inclusive_total(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)

    bill = compute_bill(conn, [CartLine(product_id=product.id, quantity=2)])

    assert bill.subtotal == 200
    assert bill.gst_total == 10
    assert bill.bill_total == 210
    assert bill.line_items == [
        SaleLineItem(
            product_id=product.id,
            quantity=2,
            unit_price=100,
            gst_rate=5,
            discount_amount=0.0,
            line_net_price=200,
            line_gst_amount=10.0,
            line_total=210.0,
        )
    ]


def test_compute_bill_when_gst_rate_zero_adds_no_gst(conn: sqlite3.Connection) -> None:
    product = create_product(conn, name="Fresh Milk 1L", price=50, gst_rate=0)

    bill = compute_bill(conn, [CartLine(product_id=product.id, quantity=3)])

    assert bill.subtotal == 150
    assert bill.gst_total == 0
    assert bill.bill_total == 150


# --- compute_bill: discount interaction -------------------------------------


def test_compute_bill_when_percentage_discount_reduces_net_before_gst(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Cooking Oil 1L", price=200, gst_rate=12)
    discount = create_discount(conn, product.id, discount_type="percentage", value=10)

    bill = compute_bill(
        conn, [CartLine(product_id=product.id, quantity=1, discount=discount)]
    )

    # unit price 200 -> 10% off -> 180 net; GST 12% of 180 = 21.6
    assert bill.subtotal == pytest.approx(180)
    assert bill.gst_total == pytest.approx(21.6)
    assert bill.bill_total == pytest.approx(201.6)
    assert bill.line_items[0].discount_amount == pytest.approx(20)


def test_compute_bill_when_flat_discount_scales_with_quantity(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Soap Bar", price=50, gst_rate=18)
    discount = create_discount(conn, product.id, discount_type="flat", value=10)

    bill = compute_bill(
        conn, [CartLine(product_id=product.id, quantity=4, discount=discount)]
    )

    # per-unit discounted price 40, quantity 4 -> net 160; discount_amount
    # is the line's total discount (10 * 4), not the per-unit value.
    assert bill.line_items[0].discount_amount == pytest.approx(40)
    assert bill.subtotal == pytest.approx(160)
    assert bill.gst_total == pytest.approx(28.8)
    assert bill.bill_total == pytest.approx(188.8)


# --- compute_bill: mixed-GST-slab carts (ADR-005 Decision (a)) -------------


def test_compute_bill_when_cart_spans_multiple_gst_slabs_sums_each_lines_own_slab(
    conn: sqlite3.Connection,
) -> None:
    exempt = create_product(conn, name="Fresh Vegetables", price=40, gst_rate=0)
    low = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)
    mid = create_product(conn, name="Cooking Oil 1L", price=200, gst_rate=12)
    high = create_product(conn, name="Soft Drink 1L", price=60, gst_rate=18)
    top = create_product(conn, name="Luxury Item", price=1000, gst_rate=28)

    bill = compute_bill(
        conn,
        [
            CartLine(product_id=exempt.id, quantity=2),  # 80, +0
            CartLine(product_id=low.id, quantity=1),  # 100, +5
            CartLine(product_id=mid.id, quantity=1),  # 200, +24
            CartLine(product_id=high.id, quantity=2),  # 120, +21.6
            CartLine(product_id=top.id, quantity=1),  # 1000, +280
        ],
    )

    expected_subtotal = 80 + 100 + 200 + 120 + 1000
    expected_gst = 0 + 5 + 24 + 21.6 + 280
    assert bill.subtotal == pytest.approx(expected_subtotal)
    assert bill.gst_total == pytest.approx(expected_gst)
    assert bill.bill_total == pytest.approx(expected_subtotal + expected_gst)
    # Each line's GST reflects only its own product's slab — no blended rate.
    assert [line.gst_rate for line in bill.line_items] == [0, 5, 12, 18, 28]


def test_compute_bill_when_mixed_slabs_and_discount_combine_correctly(
    conn: sqlite3.Connection,
) -> None:
    low = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)
    high = create_product(conn, name="Soft Drink 1L", price=60, gst_rate=18)
    discount = create_discount(conn, low.id, discount_type="flat", value=20)

    bill = compute_bill(
        conn,
        [
            CartLine(product_id=low.id, quantity=1, discount=discount),  # net 80
            CartLine(product_id=high.id, quantity=1),  # net 60
        ],
    )

    # low: net 80, gst 5% = 4; high: net 60, gst 18% = 10.8
    assert bill.subtotal == pytest.approx(140)
    assert bill.gst_total == pytest.approx(14.8)
    assert bill.bill_total == pytest.approx(154.8)


# --- compute_bill: validation / error paths ---------------------------------


def test_compute_bill_when_cart_empty_raises_sale_validation_error(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(SaleValidationError):
        compute_bill(conn, [])


def test_compute_bill_when_quantity_not_positive_raises_sale_validation_error(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)

    with pytest.raises(SaleValidationError):
        compute_bill(conn, [CartLine(product_id=product.id, quantity=0)])


def test_compute_bill_when_product_missing_raises_product_not_found_error(
    conn: sqlite3.Connection,
) -> None:
    """Error-path coverage: a cart line referencing a non-existent product
    must surface `src/services/catalog.py`'s own domain exception, not a
    raw lookup failure."""
    with pytest.raises(ProductNotFoundError):
        compute_bill(conn, [CartLine(product_id=999, quantity=1)])


def test_compute_bill_when_discount_belongs_to_other_product_raises_validation_error(
    conn: sqlite3.Connection,
) -> None:
    product_a = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)
    product_b = create_product(conn, name="Soap Bar", price=50, gst_rate=18)
    discount_for_b = create_discount(conn, product_b.id, discount_type="flat", value=5)

    with pytest.raises(SaleValidationError):
        compute_bill(
            conn,
            [CartLine(product_id=product_a.id, quantity=1, discount=discount_for_b)],
        )


def test_compute_bill_when_discount_exceeds_price_propagates_discount_error(
    conn: sqlite3.Connection,
) -> None:
    """Error-path coverage: a flat discount larger than the unit price
    must surface `src/services/discounts.py`'s own domain exception."""
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)
    discount = Discount(id=1, product_id=product.id, discount_type="flat", value=150)

    with pytest.raises(DiscountExceedsPriceError):
        compute_bill(
            conn, [CartLine(product_id=product.id, quantity=1, discount=discount)]
        )


def test_compute_bill_when_quantity_negative_raises_sale_validation_error(
    conn: sqlite3.Connection,
) -> None:
    """Invalid-input coverage distinct from the zero-quantity case above —
    a negative quantity must not be silently treated as a no-op line."""
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)

    with pytest.raises(SaleValidationError):
        compute_bill(conn, [CartLine(product_id=product.id, quantity=-1)])


# --- compute_bill: boundary values -------------------------------------------


def test_compute_bill_when_flat_discount_equals_price_results_in_zero_net_and_zero_gst(
    conn: sqlite3.Connection,
) -> None:
    """Boundary: a flat discount exactly equal to the unit price is valid
    (only a result *below* zero raises `DiscountExceedsPriceError` per
    `apply_discount`'s `< 0` check) — the line nets to zero, and GST on a
    zero net price is zero."""
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)
    discount = create_discount(conn, product.id, discount_type="flat", value=100)

    bill = compute_bill(
        conn, [CartLine(product_id=product.id, quantity=1, discount=discount)]
    )

    assert bill.line_items[0].discount_amount == pytest.approx(100)
    assert bill.subtotal == pytest.approx(0)
    assert bill.gst_total == pytest.approx(0)
    assert bill.bill_total == pytest.approx(0)


def test_compute_bill_when_percentage_discount_is_full_results_in_zero_net_and_zero_gst(
    conn: sqlite3.Connection,
) -> None:
    """Boundary: a 100% percentage discount is the maximum valid value
    (`_validate_discount` rejects anything above 100) — the line nets to
    zero and no GST is added."""
    product = create_product(conn, name="Cooking Oil 1L", price=200, gst_rate=12)
    discount = create_discount(conn, product.id, discount_type="percentage", value=100)

    bill = compute_bill(
        conn, [CartLine(product_id=product.id, quantity=2, discount=discount)]
    )

    assert bill.subtotal == pytest.approx(0)
    assert bill.gst_total == pytest.approx(0)
    assert bill.bill_total == pytest.approx(0)


# --- compute_bill: cart aggregation correctness ------------------------------


def test_compute_bill_when_cart_has_duplicate_product_lines_computed_independently(
    conn: sqlite3.Connection,
) -> None:
    """Two cart lines referencing the same product are not merged or
    deduplicated — each is priced and GST-computed independently, and the
    bill totals reflect the sum of both lines."""
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)

    bill = compute_bill(
        conn,
        [
            CartLine(product_id=product.id, quantity=1),
            CartLine(product_id=product.id, quantity=2),
        ],
    )

    assert len(bill.line_items) == 2
    assert [line.quantity for line in bill.line_items] == [1, 2]
    assert bill.subtotal == pytest.approx(300)
    assert bill.gst_total == pytest.approx(15)
    assert bill.bill_total == pytest.approx(315)


# --- _compute_line / compute_bill: unit_cost COGS snapshot (ADR-009 (b)) ---
# P3-F1-T4


def test_compute_bill_when_product_cost_known_snapshots_it_onto_the_line(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5, cost=70)

    bill = compute_bill(conn, [CartLine(product_id=product.id, quantity=3)])

    assert bill.line_items[0].unit_cost == 70


def test_compute_bill_when_product_cost_unknown_propagates_null_not_zero(
    conn: sqlite3.Connection,
) -> None:
    """ADR-009 Decision (b) — `NULL` propagates when the product's cost is
    unknown; it must never be silently treated as `0`."""
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)

    bill = compute_bill(conn, [CartLine(product_id=product.id, quantity=1)])

    assert bill.line_items[0].unit_cost is None


def test_compute_bill_when_discount_applied_unit_cost_is_unaffected_by_discount(
    conn: sqlite3.Connection,
) -> None:
    """ADR-009 Decision (b) — discounts do not reduce cost: a discount
    changes what the customer paid, not what the shop paid. `unit_cost`
    must equal the product's raw `cost`, independent of `discount_amount`."""
    product = create_product(
        conn, name="Cooking Oil 1L", price=200, gst_rate=12, cost=150
    )
    discount = create_discount(conn, product.id, discount_type="percentage", value=50)

    bill = compute_bill(
        conn, [CartLine(product_id=product.id, quantity=1, discount=discount)]
    )

    assert bill.line_items[0].discount_amount == pytest.approx(100)
    assert bill.line_items[0].unit_cost == 150


def test_record_sale_transaction_when_committed_snapshots_unit_cost_onto_line_items(
    conn: sqlite3.Connection,
) -> None:
    """The COGS snapshot must actually reach persisted storage, not just
    the in-memory `SaleLineItem` `compute_bill` returns."""
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5, cost=70)
    set_stock(conn, product.id, 10)

    sale = record_sale(
        conn, [CartLine(product_id=product.id, quantity=2)], payment_status="paid"
    )

    (unit_cost,) = conn.execute(
        "SELECT unit_cost FROM sale_line_items WHERE sale_id = ?", (sale.id,)
    ).fetchone()
    assert unit_cost == 70


def test_get_sale_after_later_product_cost_edit_returns_original_unit_cost_snapshot(
    conn: sqlite3.Connection,
) -> None:
    """STORY: P3-F1-T4 snapshot-immutability regression (ADR-009
    Consequences, blast radius item (3)) — a sale line's `unit_cost` must
    survive a later edit to `products.cost` unchanged. A snapshot written
    from the wrong source (e.g. re-reading cost after a catalog edit) would
    produce wrong-but-plausible COGS; this asserts the *old* value persists,
    not merely that the column is populated."""
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5, cost=70)
    set_stock(conn, product.id, 10)
    sale = record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )

    # The shop owner later corrects the purchase price in the catalog.
    update_product(conn, product.id, name="Rice 1kg", price=100, gst_rate=5, cost=90)

    reloaded = get_sale(conn, sale.id)
    assert reloaded.line_items[0].unit_cost == 70  # unchanged by the later edit
    assert get_product(conn, product.id).cost == 90  # the catalog itself did change


def test_record_sale_transaction_when_product_cost_unknown_persists_null_unit_cost(
    conn: sqlite3.Connection,
) -> None:
    """Error/edge-path coverage: a sale of a product whose cost was never
    entered must persist `unit_cost = NULL`, per ADR-009 Decision (c)'s
    unknown-cost mechanism — not `0`, and not a crash."""
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)
    set_stock(conn, product.id, 10)

    sale = record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )

    reloaded = get_sale(conn, sale.id)
    assert reloaded.line_items[0].unit_cost is None


# --- record_sale: single-transaction commit (P2-F1-T4 / P2-F2-T4) -----------


def test_record_sale_transaction_when_paid_commits_sale_and_decrements_stock(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)
    set_stock(conn, product.id, 10)

    sale = record_sale(
        conn, [CartLine(product_id=product.id, quantity=3)], payment_status="paid"
    )

    assert isinstance(sale, Sale)
    assert sale.customer_id is None
    assert sale.payment_status == "paid"
    assert sale.bill_total == pytest.approx(315)
    assert get_stock(conn, product.id).quantity == 7

    row = conn.execute(
        "SELECT customer_id, payment_status, bill_total FROM sales WHERE id = ?",
        (sale.id,),
    ).fetchone()
    assert row == (None, "paid", pytest.approx(315))

    line_item_count = conn.execute(
        "SELECT COUNT(*) FROM sale_line_items WHERE sale_id = ?", (sale.id,)
    ).fetchone()[0]
    assert line_item_count == 1


def test_record_sale_transaction_when_credit_commits_sale_and_credit_ledger_row(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Cooking Oil 1L", price=200, gst_rate=12)
    set_stock(conn, product.id, 10)
    customer = create_customer(conn, name="Ravi Kumar", phone="9990001111")

    sale = record_sale(
        conn,
        [CartLine(product_id=product.id, quantity=1)],
        payment_status="credit",
        customer_id=customer.id,
    )

    assert sale.customer_id == customer.id
    assert sale.payment_status == "credit"
    assert get_stock(conn, product.id).quantity == 9
    assert get_outstanding_balance(conn, customer.id) == pytest.approx(sale.bill_total)

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


def test_record_sale_transaction_when_insufficient_stock_rolls_back_all_steps(
    conn: sqlite3.Connection,
) -> None:
    """Error-path coverage: a mid-sequence stock failure on a credit sale
    must roll back the sale row, the sale_line_items row, and the
    already-inserted (uncommitted) credit_transactions row — no orphaned
    rows survive, and stock is left untouched."""
    product = create_product(conn, name="Soap Bar", price=50, gst_rate=18)
    set_stock(conn, product.id, 1)
    customer = create_customer(conn, name="Ravi Kumar", phone="9990001111")

    with pytest.raises(SaleTransactionError):
        record_sale(
            conn,
            [CartLine(product_id=product.id, quantity=5)],
            payment_status="credit",
            customer_id=customer.id,
        )

    assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM sale_line_items").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM credit_transactions").fetchone()[0] == 0
    assert get_stock(conn, product.id).quantity == 1
    assert get_outstanding_balance(conn, customer.id) == pytest.approx(0)


def test_record_sale_transaction_when_insufficient_stock_wraps_original_error(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Soap Bar", price=50, gst_rate=18)
    set_stock(conn, product.id, 1)

    with pytest.raises(SaleTransactionError) as exc_info:
        record_sale(
            conn, [CartLine(product_id=product.id, quantity=5)], payment_status="paid"
        )

    assert isinstance(exc_info.value.__cause__, InsufficientStockError)


def test_record_sale_transaction_when_credit_customer_missing_raises_and_rolls_back(
    conn: sqlite3.Connection,
) -> None:
    """A non-existent customer_id on a credit sale is caught by the
    pre-transaction validation, before any write is issued — nothing to
    roll back, and no sales row is created."""
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)
    set_stock(conn, product.id, 10)

    with pytest.raises(CustomerNotFoundError):
        record_sale(
            conn,
            [CartLine(product_id=product.id, quantity=1)],
            payment_status="credit",
            customer_id=999,
        )

    assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 0
    assert get_stock(conn, product.id).quantity == 10


def test_record_sale_transaction_when_payment_status_invalid_raises_sale_validation_error(  # noqa: E501
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)

    with pytest.raises(SaleValidationError):
        record_sale(
            conn, [CartLine(product_id=product.id, quantity=1)], payment_status="void"
        )


def test_record_sale_transaction_when_credit_without_customer_id_raises_sale_validation_error(  # noqa: E501
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)

    with pytest.raises(SaleValidationError):
        record_sale(
            conn, [CartLine(product_id=product.id, quantity=1)], payment_status="credit"
        )


# --- list_sales / get_sale (phase2-feature6-sales-customer-gaps, Action 3) --


def test_list_sales_when_no_filter_returns_all_sales_most_recent_first(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)
    set_stock(conn, product.id, 10)
    first = record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )
    second = record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )

    sales = list_sales(conn)

    assert [sale.id for sale in sales] == [second.id, first.id]


def test_list_sales_when_customer_id_given_filters_to_that_customer(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)
    set_stock(conn, product.id, 10)
    customer_a = create_customer(conn, name="Ravi Kumar", phone="9990001111")
    customer_b = create_customer(conn, name="Priya Singh", phone="9123456780")
    sale_a = record_sale(
        conn,
        [CartLine(product_id=product.id, quantity=1)],
        payment_status="credit",
        customer_id=customer_a.id,
    )
    record_sale(
        conn,
        [CartLine(product_id=product.id, quantity=1)],
        payment_status="credit",
        customer_id=customer_b.id,
    )

    sales = list_sales(conn, customer_id=customer_a.id)

    assert [sale.id for sale in sales] == [sale_a.id]


def test_list_sales_when_more_rows_than_limit_caps_at_limit(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)
    set_stock(conn, product.id, 10)
    for _ in range(3):
        record_sale(
            conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
        )

    sales = list_sales(conn, limit=2)

    assert len(sales) == 2


def test_list_sales_when_no_sales_returns_empty_list(conn: sqlite3.Connection) -> None:
    assert list_sales(conn) == []


def test_list_sales_returns_header_rows_with_no_line_items(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)
    set_stock(conn, product.id, 10)
    record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )

    sales = list_sales(conn)

    assert sales[0].line_items == []
    assert sales[0].created_at != ""


def test_get_sale_when_found_returns_full_line_item_detail(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)
    set_stock(conn, product.id, 10)
    committed = record_sale(
        conn, [CartLine(product_id=product.id, quantity=2)], payment_status="paid"
    )

    sale = get_sale(conn, committed.id)

    assert sale.id == committed.id
    assert sale.bill_total == pytest.approx(committed.bill_total)
    assert len(sale.line_items) == 1
    assert sale.line_items[0].product_id == product.id
    assert sale.line_items[0].quantity == 2
    assert sale.created_at != ""


def test_get_sale_when_not_found_raises_sale_not_found_error(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(SaleNotFoundError):
        get_sale(conn, 999)
