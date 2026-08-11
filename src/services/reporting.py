"""P&L aggregation service — daily/monthly/quarterly/annual (P3-F2-T1).

Implements `ADR-008` (P&L reporting period semantics) end to end:

- **Accrual basis** (Decision (a)) — revenue/discounts/COGS are recognised
  in the period containing `sales.created_at`; `credit_transactions` never
  moves a figure between periods. `credit_sales` is an informational,
  non-P&L accrual disclosure, not a cash-collected figure.
- **Trading day / timezone** (Decision (b)) — every bound is computed via
  `src.config.get_reporting_tz()`, never a bare constant, converted to UTC
  and rendered in `sales.created_at`'s exact `%Y-%m-%dT%H:%M:%S.fffZ`
  (millisecond) text form, then compared as text. Every period is
  half-open: inclusive lower bound, exclusive upper bound — there is no
  `<=` upper-bound comparison anywhere in this module.
- **Calendar year** (Decision (c)) — quarterly/annual grains use the
  calendar year (Q1 = Jan-Mar), not the Indian fiscal year.
- **Report output contract** (Decision (d)) — every generator function
  returns the frozen `PLReport` dataclass defined below; no field is
  redeclared elsewhere.

COGS sums only lines with a non-NULL `unit_cost` (`ADR-009` Decision (c));
`lines_missing_cost`/`cost_complete` make the gap observable in the
returned data rather than leaving the UI to infer it.

Read-only: every query issued here is a `SELECT`. No `INSERT`, `UPDATE`,
`DELETE`, `commit()`, or DDL statement appears in this module. Any
`sqlite3.Error` raised during a query is wrapped in `ReportingError`
(`src/exceptions.py`) — a raw `sqlite3.Error` never crosses this module's
boundary, per `framework/technology/backend/python.md` §Error Handling.
"""

import datetime as dt
import sqlite3
from dataclasses import dataclass

from src.config import get_reporting_tz
from src.exceptions import ReportingError, ReportingValidationError

_QUARTER_MONTHS: dict[int, tuple[int, int, int]] = {
    # quarter -> (start_month, end_month_exclusive_start, label_month_range)
    1: (1, 4, 1),
    2: (4, 7, 2),
    3: (7, 10, 3),
    4: (10, 13, 4),
}
_QUARTER_LABEL_RANGE: dict[int, str] = {
    1: "Jan–Mar",
    2: "Apr–Jun",
    3: "Jul–Sep",
    4: "Oct–Dec",
}
_MONTH_ABBR: list[str] = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


@dataclass(frozen=True)
class PLReport:
    """One P&L report for one period — the frozen eleven-field dataclass
    fixed by `ADR-008` Decision (d). Field names, types and meanings are
    binding; see the ADR table for the full definition of each."""

    period_label: str
    period_start_utc: str
    period_end_utc: str
    revenue: float
    discounts: float
    cogs: float
    net: float
    credit_sales: float
    line_count: int
    lines_missing_cost: int
    cost_complete: bool


def _format_bound(moment: dt.datetime) -> str:
    """Render a UTC `datetime` in `sales.created_at`'s exact
    `%Y-%m-%dT%H:%M:%S.fffZ` millisecond-precision text form, so the
    lexicographic text comparison in `_aggregate` agrees with how SQLite's
    `strftime('%Y-%m-%dT%H:%M:%fZ', 'now')` default writes `created_at`
    (`ADR-008` Decision (b))."""
    milliseconds = moment.microsecond // 1000
    return f"{moment.strftime('%Y-%m-%dT%H:%M:%S')}.{milliseconds:03d}Z"


def _bounds(local_start: dt.datetime, local_end: dt.datetime) -> tuple[str, str]:
    """Convert a half-open `[local_start, local_end)` window (in the
    reporting timezone) to its UTC text-bound pair."""
    start_utc = local_start.astimezone(dt.timezone.utc)
    end_utc = local_end.astimezone(dt.timezone.utc)
    return _format_bound(start_utc), _format_bound(end_utc)


def _aggregate(
    conn: sqlite3.Connection, period_label: str, start_utc: str, end_utc: str
) -> PLReport:
    """Issue the two read-only aggregation queries and assemble the
    `PLReport`. Any `sqlite3.Error` is wrapped in `ReportingError`."""
    try:
        line_row = conn.execute(
            "SELECT "
            "COALESCE(SUM(li.line_total - li.line_gst_amount), 0), "
            "COALESCE(SUM(li.discount_amount), 0), "
            "COALESCE(SUM(CASE WHEN li.unit_cost IS NOT NULL "
            "THEN li.unit_cost * li.quantity ELSE 0 END), 0), "
            "COUNT(*), "
            "COALESCE(SUM(CASE WHEN li.unit_cost IS NULL THEN 1 ELSE 0 END), 0) "
            "FROM sale_line_items li "
            "JOIN sales s ON s.id = li.sale_id "
            "WHERE s.created_at >= ? AND s.created_at < ?",
            (start_utc, end_utc),
        ).fetchone()
        credit_row = conn.execute(
            "SELECT COALESCE(SUM(bill_total), 0) FROM sales "
            "WHERE payment_status = 'credit' "
            "AND created_at >= ? AND created_at < ?",
            (start_utc, end_utc),
        ).fetchone()
    except sqlite3.Error as exc:
        raise ReportingError(
            f"failed to generate P&L report for {period_label!r}: {exc}"
        ) from exc

    revenue = float(line_row[0])
    discounts = float(line_row[1])
    cogs = float(line_row[2])
    line_count = int(line_row[3])
    lines_missing_cost = int(line_row[4])
    credit_sales = float(credit_row[0])

    return PLReport(
        period_label=period_label,
        period_start_utc=start_utc,
        period_end_utc=end_utc,
        revenue=revenue,
        discounts=discounts,
        cogs=cogs,
        net=revenue - cogs,
        credit_sales=credit_sales,
        line_count=line_count,
        lines_missing_cost=lines_missing_cost,
        cost_complete=lines_missing_cost == 0,
    )


def generate_daily_report(conn: sqlite3.Connection, report_date: dt.date) -> PLReport:
    """One trading day `[D 00:00:00.000, D+1 00:00:00.000)` in the
    reporting timezone (`ADR-008` Decision (b))."""
    tz = get_reporting_tz()
    local_start = dt.datetime(
        report_date.year, report_date.month, report_date.day, tzinfo=tz
    )
    local_end = local_start + dt.timedelta(days=1)
    start_utc, end_utc = _bounds(local_start, local_end)
    label = report_date.strftime("%d %b %Y")
    return _aggregate(conn, label, start_utc, end_utc)


def generate_monthly_report(
    conn: sqlite3.Connection, year: int, month: int
) -> PLReport:
    """One calendar month, `[1st 00:00:00.000, next-1st 00:00:00.000)`."""
    if not 1 <= month <= 12:
        raise ReportingValidationError(f"month {month!r} must be in 1..12")
    tz = get_reporting_tz()
    local_start = dt.datetime(year, month, 1, tzinfo=tz)
    if month == 12:
        local_end = dt.datetime(year + 1, 1, 1, tzinfo=tz)
    else:
        local_end = dt.datetime(year, month + 1, 1, tzinfo=tz)
    start_utc, end_utc = _bounds(local_start, local_end)
    label = f"{_MONTH_ABBR[month - 1]} {year}"
    return _aggregate(conn, label, start_utc, end_utc)


def generate_quarterly_report(
    conn: sqlite3.Connection, year: int, quarter: int
) -> PLReport:
    """One calendar quarter (`ADR-008` Decision (c)): Q1 Jan-Mar, Q2
    Apr-Jun, Q3 Jul-Sep, Q4 Oct-Dec."""
    if quarter not in _QUARTER_MONTHS:
        raise ReportingValidationError(f"quarter {quarter!r} must be in 1..4")
    start_month, end_month_exclusive, _ = _QUARTER_MONTHS[quarter]
    tz = get_reporting_tz()
    local_start = dt.datetime(year, start_month, 1, tzinfo=tz)
    if end_month_exclusive > 12:
        local_end = dt.datetime(year + 1, end_month_exclusive - 12, 1, tzinfo=tz)
    else:
        local_end = dt.datetime(year, end_month_exclusive, 1, tzinfo=tz)
    start_utc, end_utc = _bounds(local_start, local_end)
    label = f"CY {year} Q{quarter} ({_QUARTER_LABEL_RANGE[quarter]} {year})"
    return _aggregate(conn, label, start_utc, end_utc)


def generate_annual_report(conn: sqlite3.Connection, year: int) -> PLReport:
    """One calendar year, `[1 Jan 00:00:00.000, next 1 Jan 00:00:00.000)`
    (`ADR-008` Decision (c))."""
    tz = get_reporting_tz()
    local_start = dt.datetime(year, 1, 1, tzinfo=tz)
    local_end = dt.datetime(year + 1, 1, 1, tzinfo=tz)
    start_utc, end_utc = _bounds(local_start, local_end)
    label = f"CY {year}"
    return _aggregate(conn, label, start_utc, end_utc)
