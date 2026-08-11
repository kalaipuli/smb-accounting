"""Unit tests for `src/services/reporting.py` — the P&L aggregation
service (P3-F2-T2), implementing the test-strategy rows the tasks.md/ADR
bind to this action:

- T-6 — revenue/discounts/cogs/net each asserted independently against a
  hand-computed mixed-GST-slab-with-discount fixture.
- T-7 — one test per grain (daily/monthly/quarterly/annual) proving a sale
  just outside the period window is excluded.
- T-8 — an empty period returns all-zero figures, never `None`/an
  exception.
- T-9 — a sale line whose product cost is unknown (the ADR-009
  pre-migration/unrecoverable-cost case) makes the report cost-incomplete.
- ADR-008 Decision (e)'s six resolved boundary cases (the sixth —
  refund/correction — is explicitly out of scope per the ADR; no test is
  owed for it, recorded here as a comment rather than silently omitted).

All fixtures use a real (throwaway, file-backed) SQLite database via
`src.db.schema.connect`/`init_schema` — no mock of the database anywhere
in this file. `sales.created_at` is set directly via `UPDATE` after
`record_sale()` commits, because `record_sale()` always stamps "now" and
these tests need deterministic historical timestamps to exercise period
boundaries; this is test-fixture setup, not a substitute for the reporting
service's own read path, which is exercised unmodified.
"""

import datetime as dt
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.config import DEFAULT_REPORTING_TZ_OFFSET_MINUTES
from src.db.schema import connect, init_schema
from src.exceptions import ReportingError, ReportingValidationError
from src.services.catalog import create_product
from src.services.customers import create_customer, record_payment
from src.services.discounts import create_discount
from src.services.inventory import set_stock
from src.services.reporting import (
    generate_annual_report,
    generate_daily_report,
    generate_monthly_report,
    generate_quarterly_report,
)
from src.services.sales import CartLine, record_sale

IST = dt.timezone(dt.timedelta(minutes=DEFAULT_REPORTING_TZ_OFFSET_MINUTES))


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "data.db")
    init_schema(connection)
    yield connection
    connection.close()


def _set_created_at(
    conn: sqlite3.Connection, table: str, row_id: int, moment_ist: dt.datetime
) -> str:
    """Overwrite `<table>.created_at` with `moment_ist` (a `datetime` in
    the reporting timezone), rendered in the exact millisecond-precision
    UTC text form `sales.created_at`'s column default produces
    (`ADR-008` Decision (b)) — so the reporting service's text-comparison
    bounds agree with this deterministic fixture timestamp."""
    utc = moment_ist.astimezone(dt.timezone.utc)
    ms = utc.microsecond // 1000
    text = f"{utc.strftime('%Y-%m-%dT%H:%M:%S')}.{ms:03d}Z"
    conn.execute(f"UPDATE {table} SET created_at = ? WHERE id = ?", (text, row_id))  # noqa: S608
    conn.commit()
    return text


# --- T-6: hand-computed mixed-GST-slab-with-discount fixture ----------------


def test_generate_daily_report_when_mixed_slab_cart_with_discount_matches_hand_computed_figures(  # noqa: E501
    conn: sqlite3.Connection,
) -> None:
    """rice: price 100, gst 5%, cost 70, qty 2, flat discount 10/unit ->
    discount_amount 20, line_net 180, line_gst 9, cogs 140.
    oil: price 200, gst 12%, cost 150, qty 1, no discount ->
    line_net 200, line_gst 24, cogs 150.
    revenue = 180 + 200 = 380; discounts = 20; cogs = 140 + 150 = 290;
    net = revenue - cogs = 90. GST (9 + 24 = 33) never enters `revenue` or
    `net` (ADR-008 Decision (d) — GST is not a P&L figure)."""
    rice = create_product(conn, name="Rice 1kg", price=100, gst_rate=5, cost=70)
    oil = create_product(conn, name="Cooking Oil 1L", price=200, gst_rate=12, cost=150)
    set_stock(conn, rice.id, 10)
    set_stock(conn, oil.id, 10)
    discount = create_discount(conn, rice.id, discount_type="flat", value=10)

    sale = record_sale(
        conn,
        [
            CartLine(product_id=rice.id, quantity=2, discount=discount),
            CartLine(product_id=oil.id, quantity=1),
        ],
        payment_status="paid",
    )
    _set_created_at(conn, "sales", sale.id, dt.datetime(2026, 6, 15, 12, 0, tzinfo=IST))

    report = generate_daily_report(conn, dt.date(2026, 6, 15))

    assert report.revenue == pytest.approx(380)
    assert report.discounts == pytest.approx(20)
    assert report.cogs == pytest.approx(290)
    assert report.net == pytest.approx(90)
    assert report.line_count == 2
    assert report.cost_complete is True


# --- T-7: one test per grain, a sale just outside the window is excluded ---


def test_generate_daily_report_excludes_sale_at_next_trading_day_start(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)
    set_stock(conn, product.id, 10)
    inside = record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )
    outside = record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )
    _set_created_at(
        conn, "sales", inside.id, dt.datetime(2026, 6, 15, 23, 0, tzinfo=IST)
    )
    _set_created_at(
        conn, "sales", outside.id, dt.datetime(2026, 6, 16, 0, 0, tzinfo=IST)
    )

    report = generate_daily_report(conn, dt.date(2026, 6, 15))

    assert report.line_count == 1


def test_generate_monthly_report_excludes_sale_at_next_month_start(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)
    set_stock(conn, product.id, 10)
    inside = record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )
    outside = record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )
    _set_created_at(
        conn, "sales", inside.id, dt.datetime(2026, 6, 30, 23, 0, tzinfo=IST)
    )
    _set_created_at(
        conn, "sales", outside.id, dt.datetime(2026, 7, 1, 0, 0, tzinfo=IST)
    )

    report = generate_monthly_report(conn, 2026, 6)

    assert report.line_count == 1


def test_generate_quarterly_report_excludes_sale_at_next_quarter_start(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)
    set_stock(conn, product.id, 10)
    inside = record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )
    outside = record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )
    _set_created_at(
        conn, "sales", inside.id, dt.datetime(2026, 6, 30, 23, 0, tzinfo=IST)
    )
    _set_created_at(
        conn, "sales", outside.id, dt.datetime(2026, 7, 1, 0, 0, tzinfo=IST)
    )

    report = generate_quarterly_report(conn, 2026, 2)  # Q2 = Apr-Jun

    assert report.line_count == 1


def test_generate_annual_report_excludes_sale_at_next_year_start(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)
    set_stock(conn, product.id, 10)
    inside = record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )
    outside = record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )
    _set_created_at(
        conn, "sales", inside.id, dt.datetime(2026, 12, 31, 23, 0, tzinfo=IST)
    )
    _set_created_at(
        conn, "sales", outside.id, dt.datetime(2027, 1, 1, 0, 0, tzinfo=IST)
    )

    report = generate_annual_report(conn, 2026)

    assert report.line_count == 1


# --- T-8: empty period returns zeroes, never None/an exception -------------


def test_generate_daily_report_when_empty_returns_all_zero_figures_not_none(
    conn: sqlite3.Connection,
) -> None:
    report = generate_daily_report(conn, dt.date(2026, 6, 15))

    assert report.revenue == 0.0
    assert report.discounts == 0.0
    assert report.cogs == 0.0
    assert report.net == 0.0
    assert report.credit_sales == 0.0
    assert report.line_count == 0
    assert report.lines_missing_cost == 0
    assert report.cost_complete is True
    assert report.period_label == "15 Jun 2026"


# --- T-9: pre-migration / unrecoverable cost -> cost-incomplete ------------


def test_generate_daily_report_when_line_cost_unknown_marks_cost_incomplete(
    conn: sqlite3.Connection,
) -> None:
    """A sale line whose product cost was never recorded (the ADR-009
    pre-migration/unrecoverable-cost case) must make the report
    cost-incomplete and exclude that line's cost from `cogs`, not
    silently treat it as zero cost while claiming completeness."""
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)  # no cost
    set_stock(conn, product.id, 10)
    sale = record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )
    _set_created_at(conn, "sales", sale.id, dt.datetime(2026, 6, 15, 12, 0, tzinfo=IST))

    report = generate_daily_report(conn, dt.date(2026, 6, 15))

    assert report.lines_missing_cost == 1
    assert report.cost_complete is False
    assert report.cogs == 0.0


# --- ADR-008 Decision (e): the six resolved boundary cases ------------------


def test_boundary_sale_at_0200_ist_belongs_to_that_trading_day_not_the_previous_one(
    conn: sqlite3.Connection,
) -> None:
    """Case 1: a sale at 02:00 IST (= 20:30 UTC the previous day) belongs
    to trading day D, not D-1."""
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)
    set_stock(conn, product.id, 10)
    sale = record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )
    _set_created_at(conn, "sales", sale.id, dt.datetime(2026, 6, 15, 2, 0, tzinfo=IST))

    same_day = generate_daily_report(conn, dt.date(2026, 6, 15))
    previous_day = generate_daily_report(conn, dt.date(2026, 6, 14))

    assert same_day.line_count == 1
    assert previous_day.line_count == 0


def test_boundary_sale_at_last_millisecond_of_trading_day_is_included(
    conn: sqlite3.Connection,
) -> None:
    """Case 2: a sale at 23:59:59.999 IST on the last day of a period is
    included — the exclusive upper bound sits at the next period's
    00:00:00.000 IST, so no millisecond of the last trading day is
    dropped."""
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)
    set_stock(conn, product.id, 10)
    sale = record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )
    _set_created_at(
        conn,
        "sales",
        sale.id,
        dt.datetime(2026, 6, 15, 23, 59, 59, 999000, tzinfo=IST),
    )

    report = generate_daily_report(conn, dt.date(2026, 6, 15))

    assert report.line_count == 1


def test_boundary_credit_sale_repaid_next_period_stays_recognised_in_the_sale_period(
    conn: sqlite3.Connection,
) -> None:
    """Case 3: a credit sale in period P repaid in period P+1 — revenue,
    discounts and COGS all land in P; P+1 is unchanged by the repayment;
    P's `credit_sales` disclosure includes the sale."""
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5, cost=70)
    set_stock(conn, product.id, 10)
    customer = create_customer(conn, name="Ravi Kumar", phone="9990001111")
    sale = record_sale(
        conn,
        [CartLine(product_id=product.id, quantity=1)],
        payment_status="credit",
        customer_id=customer.id,
    )
    _set_created_at(conn, "sales", sale.id, dt.datetime(2026, 6, 15, 12, 0, tzinfo=IST))
    payment = record_payment(conn, customer.id, sale.bill_total)
    _set_created_at(
        conn,
        "credit_transactions",
        payment.id,
        dt.datetime(2026, 7, 5, 12, 0, tzinfo=IST),
    )

    june_report = generate_monthly_report(conn, 2026, 6)
    july_report = generate_monthly_report(conn, 2026, 7)

    assert june_report.line_count == 1
    assert june_report.revenue == pytest.approx(100)
    assert june_report.credit_sales == pytest.approx(sale.bill_total)
    assert july_report.line_count == 0
    assert july_report.revenue == 0.0
    assert july_report.credit_sales == 0.0


def test_boundary_quarter_and_year_calendar_boundary_31_dec_vs_1_jan(
    conn: sqlite3.Connection,
) -> None:
    """Case 4: a sale on 31 Dec 2027 IST is `CY 2027 Q4`; a sale on
    1 Jan 2028 IST is `CY 2028 Q1`."""
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)
    set_stock(conn, product.id, 10)
    sale_dec = record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )
    sale_jan = record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )
    _set_created_at(
        conn, "sales", sale_dec.id, dt.datetime(2027, 12, 31, 23, 0, tzinfo=IST)
    )
    _set_created_at(
        conn, "sales", sale_jan.id, dt.datetime(2028, 1, 1, 1, 0, tzinfo=IST)
    )

    q4_2027 = generate_quarterly_report(conn, 2027, 4)
    q1_2028 = generate_quarterly_report(conn, 2028, 1)

    assert q4_2027.line_count == 1
    assert q4_2027.period_label == "CY 2027 Q4 (Oct–Dec 2027)"
    assert q1_2028.line_count == 1
    assert q1_2028.period_label == "CY 2028 Q1 (Jan–Mar 2028)"


def test_boundary_same_period_requested_twice_returns_identical_result(
    conn: sqlite3.Connection,
) -> None:
    """Case 5: the same period requested twice returns a byte-identical
    result — no database mutation, no drift between calls (the
    integration test proves the database itself is byte-identical;
    at the unit level this proves the returned value is idempotent)."""
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5, cost=70)
    set_stock(conn, product.id, 10)
    sale = record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )
    _set_created_at(conn, "sales", sale.id, dt.datetime(2026, 6, 15, 12, 0, tzinfo=IST))

    first = generate_daily_report(conn, dt.date(2026, 6, 15))
    second = generate_daily_report(conn, dt.date(2026, 6, 15))

    assert first == second


# Case 6 (a partial refund or a corrected sale) is out of scope for Phase 3
# by ADR-008 Decision (e) itself — no schema exists for a reversal/refund,
# and `sale_line_items` forbids a negative quantity. No test is owed for
# this case; recorded here so a future reader does not mistake its absence
# for an oversight.


# --- error paths -------------------------------------------------------------


def test_generate_monthly_report_when_month_out_of_range_raises_reporting_validation_error(  # noqa: E501
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(ReportingValidationError):
        generate_monthly_report(conn, 2026, 13)


def test_generate_quarterly_report_when_quarter_out_of_range_raises_reporting_validation_error(  # noqa: E501
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(ReportingValidationError):
        generate_quarterly_report(conn, 2026, 5)


def test_generate_daily_report_when_connection_closed_wraps_sqlite_error_in_reporting_error(  # noqa: E501
    conn: sqlite3.Connection,
) -> None:
    """Error-path coverage for the module's one external call (the SQLite
    query): a real closed connection — not a mock — surfaces
    `sqlite3.ProgrammingError` (a `sqlite3.Error` subclass), which must be
    wrapped in `ReportingError`, never crossing the service boundary raw."""
    conn.close()

    with pytest.raises(ReportingError):
        generate_daily_report(conn, dt.date(2026, 6, 15))
