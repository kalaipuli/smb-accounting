"""Unit tests for `src/ui/reports.py` — the P&L reporting curses screens
(P3-F2-T8), implementing the two test-strategy rows this action's `dod`
assigns:

- T-11 — the reports screen is reachable with the standard key bindings
  (arrows/Tab move, Enter selects, Esc/q returns), driven by a fake key
  queue, no real terminal — the `PeriodTypeMenuController` tests below and
  the `run_reports_screen` Esc-exit test are this test's own direct
  counterpart; `tests/unit/ui/test_main_menu_screen.py`'s
  `test_run_main_menu_reports_route_invokes_reports_screen_then_quits`
  additionally proves the main-menu-to-Page-1 leg.
- T-12 — each of loading/empty/error/cost-incomplete renders without
  raising, and the empty state is distinguishable from a zero-value state
  via the literal `"No sales recorded in this period."` text device, not
  merely by rendering without raising.

Follows `tests/unit/ui/test_sales.py`'s `_FakeWindow`/`_keys` fake-key-queue
pattern — no real curses I/O anywhere in this file.
"""

import curses
import datetime as dt
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from src.config import DEFAULT_REPORTING_TZ_OFFSET_MINUTES
from src.db.schema import connect, init_schema
from src.exceptions import ReportingError
from src.services.catalog import create_product
from src.services.inventory import set_stock
from src.services.reporting import PLReport
from src.services.sales import CartLine, record_sale
from src.ui.keybindings import EntryCancelled
from src.ui.reports import (
    BACK_ROUTE,
    GRAIN_ANNUAL,
    GRAIN_DAILY,
    GRAIN_MONTHLY,
    GRAIN_QUARTERLY,
    PeriodTypeMenuController,
    _collect_and_generate,
    _prompt_annual_period,
    _prompt_daily_period,
    _prompt_monthly_period,
    _prompt_quarterly_period,
    _render_loading,
    _render_menu,
    _render_report,
    run_reports_screen,
)

IST = dt.timezone(dt.timedelta(minutes=DEFAULT_REPORTING_TZ_OFFSET_MINUTES))


class _FakeWindow:
    """Minimal curses.window stand-in — records `addstr` text, no raw I/O.
    Mirrors `tests/unit/ui/test_sales.py`'s `_FakeWindow`, plus a
    `history` list that survives `clear()` — several of this screen's
    prompts loop (invalid input -> message -> `stdscr.clear()` -> retry),
    so a message shown mid-flow would otherwise be wiped by the next
    render before the test can assert on it. `lines` still reflects only
    the most recent render, for assertions about the current screen."""

    def __init__(self, keys: list[int] | None = None) -> None:
        self.lines: list[str] = []
        self.history: list[str] = []
        self._keys: list[int] = list(keys) if keys is not None else []

    def clear(self) -> None:
        self.lines = []

    def addstr(self, row: int, col: int, text: str) -> None:
        self.lines.append(text)
        self.history.append(text)

    def move(self, row: int, col: int) -> None:
        """No-op — `run_form` (`FormController`) places the cursor here."""

    def refresh(self) -> None:
        pass

    def getch(self) -> int:
        return self._keys.pop(0)


def _keys(text: str) -> list[int]:
    return [ord(c) for c in text]


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "data.db")
    init_schema(connection)
    yield connection
    connection.close()


def _fixed_trading_day(monkeypatch: pytest.MonkeyPatch) -> dt.date:
    """Pin `_current_trading_day()` to a deterministic date so every
    prompt's pre-fill default is known ahead of time — 2026-06-15 (a
    Monday), month 6, quarter 2 ((6-1)//3+1)."""
    fixed = dt.date(2026, 6, 15)
    monkeypatch.setattr("src.ui.reports._current_trading_day", lambda: fixed)
    return fixed


def _set_created_at(
    conn: sqlite3.Connection, sale_id: int, moment_ist: dt.datetime
) -> None:
    utc = moment_ist.astimezone(dt.timezone.utc)
    ms = utc.microsecond // 1000
    text = f"{utc.strftime('%Y-%m-%dT%H:%M:%S')}.{ms:03d}Z"
    conn.execute("UPDATE sales SET created_at = ? WHERE id = ?", (text, sale_id))
    conn.commit()


def _sample_report(**overrides: object) -> PLReport:
    base: dict[str, object] = {
        "period_label": "15 Jun 2026",
        "period_start_utc": "2026-06-14T18:30:00.000Z",
        "period_end_utc": "2026-06-15T18:30:00.000Z",
        "revenue": 100.0,
        "discounts": 10.0,
        "cogs": 50.0,
        "net": 50.0,
        "credit_sales": 20.0,
        "line_count": 3,
        "lines_missing_cost": 0,
        "cost_complete": True,
    }
    base.update(overrides)
    return PLReport(**base)  # type: ignore[arg-type]


# --- PeriodTypeMenuController: T-11 standard key bindings -------------------


def test_controller_default_selection_is_daily() -> None:
    controller = PeriodTypeMenuController()

    assert controller.selected_option().grain == GRAIN_DAILY


def test_controller_move_down_then_up_returns_to_start() -> None:
    controller = PeriodTypeMenuController()

    controller.move_down()
    assert controller.selected_option().grain == GRAIN_MONTHLY
    controller.move_up()
    assert controller.selected_option().grain == GRAIN_DAILY


def test_controller_move_up_from_first_wraps_to_last() -> None:
    controller = PeriodTypeMenuController()

    controller.move_up()

    assert controller.selected_option().grain == GRAIN_ANNUAL


def test_dispatch_down_arrow_moves_selection_forward() -> None:
    controller = PeriodTypeMenuController()

    route = controller.dispatch(curses.KEY_DOWN)

    assert route is None
    assert controller.selected_option().grain == GRAIN_MONTHLY


def test_dispatch_tab_moves_selection_forward_like_down_arrow() -> None:
    controller = PeriodTypeMenuController()

    controller.dispatch(ord("\t"))

    assert controller.selected_option().grain == GRAIN_MONTHLY


def test_dispatch_enter_selects_the_highlighted_grain() -> None:
    controller = PeriodTypeMenuController()
    controller.move_down()

    assert controller.dispatch(ord("\n")) == GRAIN_MONTHLY


def test_dispatch_esc_returns_back_route() -> None:
    controller = PeriodTypeMenuController()

    assert controller.dispatch(27) == BACK_ROUTE


def test_dispatch_shortcut_d_m_a_select_grain_directly_without_moving_selection() -> (
    None
):
    controller = PeriodTypeMenuController()

    assert controller.dispatch(ord("d")) == GRAIN_DAILY
    assert controller.dispatch(ord("m")) == GRAIN_MONTHLY
    assert controller.dispatch(ord("a")) == GRAIN_ANNUAL


def test_dispatch_shortcut_q_selects_quarterly_shadowing_global_quit_convention() -> (
    None
):
    """Observed behaviour, documented as a finding rather than asserted as
    correct: `dispatch()` checks `_shortcut_grains` before falling back to
    `resolve_action()`, so the Quarterly option's fast-access key 'q'
    (named in the Page 1 legend per the task's own `dod`) always resolves
    to `GRAIN_QUARTERLY` on this screen — the shared key-binding
    convention's 'q'/'Q' = quit (`src/ui/keybindings.py` `NavAction.QUIT`)
    is unreachable here; only Esc reaches `BACK_ROUTE`. Surfaced as an
    Open finding in the space rather than treated as a test failure, since
    this action does not own `PeriodTypeMenuController`'s implementation."""
    controller = PeriodTypeMenuController()

    assert controller.dispatch(ord("q")) == GRAIN_QUARTERLY
    assert controller.dispatch(ord("Q")) == GRAIN_QUARTERLY


def test_dispatch_unrecognized_key_returns_none_and_does_not_move_selection() -> None:
    controller = PeriodTypeMenuController()

    route = controller.dispatch(ord("z"))

    assert route is None
    assert controller.selected_option().grain == GRAIN_DAILY


# --- _render_menu ------------------------------------------------------------


def test_render_menu_lists_all_grains_with_selected_prefix() -> None:
    stdscr = _FakeWindow()
    controller = PeriodTypeMenuController()
    controller.move_down()

    _render_menu(cast("curses.window", stdscr), controller)

    assert any(line.startswith("  Daily") for line in stdscr.lines)
    assert any(line.startswith("> Monthly") for line in stdscr.lines)


def test_render_menu_shows_calendar_meaning_and_shortcuts_inline() -> None:
    stdscr = _FakeWindow()
    controller = PeriodTypeMenuController()

    _render_menu(cast("curses.window", stdscr), controller)

    rendered = "\n".join(stdscr.lines)
    assert "Jan–Mar" in rendered
    assert "shortcuts: d=daily m=monthly q=quarterly a=annual" in rendered


# --- _prompt_daily_period (single-field, read_line) --------------------------


def test_prompt_daily_period_when_enter_accepts_prefilled_trading_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed = _fixed_trading_day(monkeypatch)
    stdscr = _FakeWindow(keys=[ord("\n")])

    result = _prompt_daily_period(cast("curses.window", stdscr))

    assert result == fixed


def test_prompt_daily_period_when_typed_valid_date_returns_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_trading_day(monkeypatch)
    keys = [*_keys("2026-07-01"), ord("\n")]
    stdscr = _FakeWindow(keys=keys)

    result = _prompt_daily_period(cast("curses.window", stdscr))

    assert result == dt.date(2026, 7, 1)


def test_prompt_daily_period_when_invalid_input_retries_with_value_retained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_trading_day(monkeypatch)
    keys = [*_keys("bogus"), ord("\n"), ord("x"), *_keys("2026-07-01"), ord("\n")]
    stdscr = _FakeWindow(keys=keys)

    result = _prompt_daily_period(cast("curses.window", stdscr))

    assert result == dt.date(2026, 7, 1)
    assert any("Invalid date 'bogus'" in line for line in stdscr.history)


def test_prompt_daily_period_when_esc_raises_entry_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_trading_day(monkeypatch)
    stdscr = _FakeWindow(keys=[27])

    with pytest.raises(EntryCancelled):
        _prompt_daily_period(cast("curses.window", stdscr))


# --- _prompt_annual_period (single-field, read_line) --------------------------


def test_prompt_annual_period_when_enter_accepts_prefilled_trading_year(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_trading_day(monkeypatch)
    stdscr = _FakeWindow(keys=[ord("\n")])

    result = _prompt_annual_period(cast("curses.window", stdscr))

    assert result == 2026


def test_prompt_annual_period_when_invalid_input_retries_with_value_retained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_trading_day(monkeypatch)
    keys = [*_keys("abc"), ord("\n"), ord("x"), *_keys("2027"), ord("\n")]
    stdscr = _FakeWindow(keys=keys)

    result = _prompt_annual_period(cast("curses.window", stdscr))

    assert result == 2027
    assert any("Invalid year 'abc'" in line for line in stdscr.history)


def test_prompt_annual_period_when_esc_raises_entry_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_trading_day(monkeypatch)
    stdscr = _FakeWindow(keys=[27])

    with pytest.raises(EntryCancelled):
        _prompt_annual_period(cast("curses.window", stdscr))


# --- _prompt_monthly_period (FormController/run_form) -------------------------


def test_prompt_monthly_period_when_enter_twice_accepts_prefilled_month_and_year(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_trading_day(monkeypatch)
    # Enter on month (not last field) advances; Enter on year (last field) submits.
    stdscr = _FakeWindow(keys=[ord("\n"), ord("\n")])

    result = _prompt_monthly_period(cast("curses.window", stdscr))

    assert result == (2026, 6)


def test_prompt_monthly_period_when_invalid_month_retains_values_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_trading_day(monkeypatch)
    # month pre-fills "6"; typing "9" appends -> "69" (out of 1..12) -> retry,
    # values retained; backspace-clear "69" -> type "6" -> submit.
    keys = [
        *_keys("9"),
        ord("\t"),
        ord("\n"),
        ord("x"),
        127,
        127,
        *_keys("6"),
        ord("\t"),
        ord("\n"),
    ]
    stdscr = _FakeWindow(keys=keys)

    result = _prompt_monthly_period(cast("curses.window", stdscr))

    assert result == (2026, 6)
    assert any("Invalid month/year" in line for line in stdscr.history)


def test_prompt_monthly_period_when_esc_raises_entry_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_trading_day(monkeypatch)
    stdscr = _FakeWindow(keys=[27])

    with pytest.raises(EntryCancelled):
        _prompt_monthly_period(cast("curses.window", stdscr))


# --- _prompt_quarterly_period (FormController/run_form) -----------------------


def test_prompt_quarterly_period_when_enter_twice_accepts_prefilled_quarter_and_year(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_trading_day(monkeypatch)  # month 6 -> quarter (6-1)//3+1 = 2
    stdscr = _FakeWindow(keys=[ord("\n"), ord("\n")])

    result = _prompt_quarterly_period(cast("curses.window", stdscr))

    assert result == (2026, 2)


def test_prompt_quarterly_period_when_invalid_quarter_retains_values_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_trading_day(monkeypatch)
    # quarter pre-fills "2"; typing "9" appends -> "29" (out of 1..4) -> retry,
    # values retained; backspace-clear "29" -> type "3" -> submit.
    keys = [
        *_keys("9"),
        ord("\t"),
        ord("\n"),
        ord("x"),
        127,
        127,
        *_keys("3"),
        ord("\t"),
        ord("\n"),
    ]
    stdscr = _FakeWindow(keys=keys)

    result = _prompt_quarterly_period(cast("curses.window", stdscr))

    assert result == (2026, 3)
    assert any("Invalid quarter/year" in line for line in stdscr.history)


def test_prompt_quarterly_period_when_esc_raises_entry_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_trading_day(monkeypatch)
    stdscr = _FakeWindow(keys=[27])

    with pytest.raises(EntryCancelled):
        _prompt_quarterly_period(cast("curses.window", stdscr))


# --- _render_loading (T-12: loading state) ------------------------------------


def test_render_loading_shows_generating_message_without_raising() -> None:
    stdscr = _FakeWindow()

    _render_loading(cast("curses.window", stdscr))

    assert any("Generating report — please wait..." in line for line in stdscr.lines)


# --- _render_report (T-12: success / empty / cost-incomplete states) ---------


def test_render_report_success_state_renders_all_money_figures_without_raising() -> (
    None
):
    stdscr = _FakeWindow()
    report = _sample_report()

    _render_report(cast("curses.window", stdscr), report)

    rendered = "\n".join(stdscr.lines)
    assert "P&L Report — 15 Jun 2026" in rendered
    assert "100.00" in rendered  # revenue
    assert "10.00" in rendered  # discounts
    assert "50.00" in rendered  # cogs / net
    assert "20.00" in rendered  # credit_sales (Memo block)
    assert "(upper bound)" not in rendered


def test_render_report_empty_state_shows_the_no_sales_text_device() -> None:
    """T-12: the empty state (`line_count == 0`) is distinguishable from a
    zero-value state by the literal `"No sales recorded in this period."`
    text device."""
    stdscr = _FakeWindow()
    report = _sample_report(
        revenue=0.0,
        discounts=0.0,
        cogs=0.0,
        net=0.0,
        credit_sales=0.0,
        line_count=0,
        lines_missing_cost=0,
        cost_complete=True,
    )

    _render_report(cast("curses.window", stdscr), report)

    rendered = "\n".join(stdscr.lines)
    assert "No sales recorded in this period." in rendered
    assert "0.00" in rendered  # figures still render, not omitted


def test_render_report_zero_value_state_with_lines_present_omits_empty_text() -> None:
    """T-12's distinguishing case: a report with `line_count > 0` whose
    figures happen to all be zero (e.g. a fully-discounted cart) must NOT
    be confused with the true empty-period state — the text device is
    absent."""
    stdscr = _FakeWindow()
    report = _sample_report(
        revenue=0.0,
        discounts=0.0,
        cogs=0.0,
        net=0.0,
        credit_sales=0.0,
        line_count=2,
        lines_missing_cost=0,
        cost_complete=True,
    )

    _render_report(cast("curses.window", stdscr), report)

    rendered = "\n".join(stdscr.lines)
    assert "No sales recorded in this period." not in rendered


def test_render_report_cost_incomplete_state_renders_banner_and_upper_bound() -> None:
    stdscr = _FakeWindow()
    report = _sample_report(lines_missing_cost=2, line_count=5, cost_complete=False)

    _render_report(cast("curses.window", stdscr), report)

    rendered = "\n".join(stdscr.lines)
    assert "COGS incomplete: 2 of 5 sale lines" in rendered
    assert "Net profit is an upper bound." in rendered
    assert "(upper bound)" in rendered


# --- _collect_and_generate: per-grain dispatch + error path (T-12: error state) --


def test_collect_and_generate_daily_grain_calls_generate_daily_report(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixed_trading_day(monkeypatch)
    calls: list[object] = []

    def fake(conn: sqlite3.Connection, report_date: dt.date) -> PLReport:
        calls.append(report_date)
        return _sample_report()

    monkeypatch.setattr("src.ui.reports.generate_daily_report", fake)
    stdscr = _FakeWindow(keys=[ord("\n")])

    result = _collect_and_generate(cast("curses.window", stdscr), conn, GRAIN_DAILY)

    assert calls == [dt.date(2026, 6, 15)]
    assert result == _sample_report()


def test_collect_and_generate_monthly_grain_calls_generate_monthly_report(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixed_trading_day(monkeypatch)
    calls: list[tuple[int, int]] = []

    def fake(conn: sqlite3.Connection, year: int, month: int) -> PLReport:
        calls.append((year, month))
        return _sample_report()

    monkeypatch.setattr("src.ui.reports.generate_monthly_report", fake)
    stdscr = _FakeWindow(keys=[ord("\n"), ord("\n")])

    result = _collect_and_generate(cast("curses.window", stdscr), conn, GRAIN_MONTHLY)

    assert calls == [(2026, 6)]
    assert result == _sample_report()


def test_collect_and_generate_quarterly_grain_calls_generate_quarterly_report(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixed_trading_day(monkeypatch)
    calls: list[tuple[int, int]] = []

    def fake(conn: sqlite3.Connection, year: int, quarter: int) -> PLReport:
        calls.append((year, quarter))
        return _sample_report()

    monkeypatch.setattr("src.ui.reports.generate_quarterly_report", fake)
    stdscr = _FakeWindow(keys=[ord("\n"), ord("\n")])

    result = _collect_and_generate(cast("curses.window", stdscr), conn, GRAIN_QUARTERLY)

    assert calls == [(2026, 2)]
    assert result == _sample_report()


def test_collect_and_generate_annual_grain_calls_generate_annual_report(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixed_trading_day(monkeypatch)
    calls: list[int] = []

    def fake(conn: sqlite3.Connection, year: int) -> PLReport:
        calls.append(year)
        return _sample_report()

    monkeypatch.setattr("src.ui.reports.generate_annual_report", fake)
    stdscr = _FakeWindow(keys=[ord("\n")])

    result = _collect_and_generate(cast("curses.window", stdscr), conn, GRAIN_ANNUAL)

    assert calls == [2026]
    assert result == _sample_report()


def test_collect_and_generate_when_reporting_error_shows_message_and_returns_none(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Error-path coverage for the module's one external-service call
    (`generate_daily_report`, ultimately backed by SQLite): a
    `ReportingError` is surfaced via the message convention, and the
    caller (`run_reports_screen`) gets `None` back rather than a raised
    exception."""
    _fixed_trading_day(monkeypatch)

    def raise_error(conn: sqlite3.Connection, report_date: dt.date) -> PLReport:
        raise ReportingError("boom")

    monkeypatch.setattr("src.ui.reports.generate_daily_report", raise_error)
    stdscr = _FakeWindow(keys=[ord("\n"), ord("x")])

    result = _collect_and_generate(cast("curses.window", stdscr), conn, GRAIN_DAILY)

    assert result is None
    assert any("Could not generate report: boom" in line for line in stdscr.lines)


def test_collect_and_generate_when_entry_cancelled_propagates_to_caller(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixed_trading_day(monkeypatch)
    stdscr = _FakeWindow(keys=[27])

    with pytest.raises(EntryCancelled):
        _collect_and_generate(cast("curses.window", stdscr), conn, GRAIN_DAILY)


# --- run_reports_screen: end-to-end loop (T-11 reachability, T-12 states) ----


def test_run_reports_screen_when_esc_at_page1_returns_immediately(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(curses, "curs_set", lambda visibility: None)
    stdscr = _FakeWindow(keys=[27])

    run_reports_screen(cast("curses.window", stdscr), conn)

    assert any(line.startswith("Reports — Select Period Type") for line in stdscr.lines)


def test_run_reports_screen_when_unrecognized_key_then_esc_exits(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(curses, "curs_set", lambda visibility: None)
    stdscr = _FakeWindow(keys=[ord("z"), 27])

    run_reports_screen(cast("curses.window", stdscr), conn)

    assert stdscr._keys == []


def test_run_reports_screen_daily_grain_end_to_end_renders_report_and_returns_to_page1(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-11 end-to-end: standard key bindings (Enter selects the
    highlighted Daily option, Enter accepts the pre-filled date, any key
    dismisses the report, Esc backs out) drive the whole screen through a
    real `generate_daily_report()` call against a real seeded database —
    no service call is mocked in this test."""
    monkeypatch.setattr(curses, "curs_set", lambda visibility: None)
    _fixed_trading_day(monkeypatch)
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5, cost=70)
    set_stock(conn, product.id, 10)
    sale = record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )
    _set_created_at(conn, sale.id, dt.datetime(2026, 6, 15, 12, 0, tzinfo=IST))
    keys = [ord("\n"), ord("\n"), ord("x"), 27]
    stdscr = _FakeWindow(keys=keys)

    run_reports_screen(cast("curses.window", stdscr), conn)

    rendered = "\n".join(stdscr.history)
    assert "P&L Report — 15 Jun 2026" in rendered
    assert stdscr._keys == []


def test_run_reports_screen_daily_grain_when_no_sales_renders_empty_state(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(curses, "curs_set", lambda visibility: None)
    _fixed_trading_day(monkeypatch)
    keys = [ord("\n"), ord("\n"), ord("x"), 27]
    stdscr = _FakeWindow(keys=keys)

    run_reports_screen(cast("curses.window", stdscr), conn)

    assert any("No sales recorded in this period." in line for line in stdscr.history)


def test_run_reports_screen_daily_grain_when_cost_missing_renders_incomplete_banner(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(curses, "curs_set", lambda visibility: None)
    _fixed_trading_day(monkeypatch)
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5)  # no cost
    set_stock(conn, product.id, 10)
    sale = record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )
    _set_created_at(conn, sale.id, dt.datetime(2026, 6, 15, 12, 0, tzinfo=IST))
    keys = [ord("\n"), ord("\n"), ord("x"), 27]
    stdscr = _FakeWindow(keys=keys)

    run_reports_screen(cast("curses.window", stdscr), conn)

    rendered = "\n".join(stdscr.history)
    assert "COGS incomplete: 1 of 1 sale lines" in rendered
    assert "(upper bound)" in rendered


def test_run_reports_screen_when_entry_cancelled_returns_silently_to_page1(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(curses, "curs_set", lambda visibility: None)
    _fixed_trading_day(monkeypatch)
    # select Daily, Esc cancels the date prompt (EntryCancelled, silent —
    # no error message), back on Page 1, Esc backs out.
    keys = [ord("\n"), 27, 27]
    stdscr = _FakeWindow(keys=keys)

    run_reports_screen(cast("curses.window", stdscr), conn)

    assert stdscr._keys == []
    assert not any("Error" in line for line in stdscr.lines)


def test_run_reports_screen_when_reporting_error_shows_message_and_returns_to_page1(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(curses, "curs_set", lambda visibility: None)
    _fixed_trading_day(monkeypatch)

    def raise_error(conn: sqlite3.Connection, report_date: dt.date) -> PLReport:
        raise ReportingError("boom")

    monkeypatch.setattr("src.ui.reports.generate_daily_report", raise_error)
    keys = [ord("\n"), ord("\n"), ord("x"), 27]
    stdscr = _FakeWindow(keys=keys)

    run_reports_screen(cast("curses.window", stdscr), conn)

    assert any("Could not generate report: boom" in line for line in stdscr.history)
    assert stdscr._keys == []
