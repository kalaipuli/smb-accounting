"""Curses P&L reporting screens — Period-Type Menu -> Period Selector ->
Report Summary (P3-F2-T4..T7).

Reachable from the main menu's `"reports"` route (`src/ui/main_menu.py`),
following the shared key-binding convention (arrows/Tab move, Enter
select, Esc/q back, `src/ui/keybindings.py`) — the DLVR-1-01 main-menu
navigation-shell consistency obligation every screen in this project
follows.

Three pages, built in strict sequence (`framework/spaces/
phase3-feature2-pl-reporting.md` Expectations):

1. **Period-Type Menu** (`_PeriodTypeMenuController` + `_render_menu`) —
   a list screen (`style-guide.md#41-listmenu-screen`) naming the four
   grains with their calendar meaning inline, plus fast-access shortcuts
   d/m/q/a.
2. **Period Selector** (`_prompt_*_period`) — a form screen
   (`style-guide.md#43-form-multi-field-entry`). Daily/Annual are
   single-field prompts via direct `read_line` (the documented exception,
   §4.3); Monthly/Quarterly use `FormController`/`run_form` for
   field-to-field navigation, per this feature's Cross-Cutting Consumption
   Obligation. Every field pre-fills to the current trading day
   (`src.config.get_reporting_tz()`). Invalid input re-prompts with every
   previously entered value retained. Esc at any field raises
   `EntryCancelled`, silently returning to Page 1.
3. **Report Summary** (`_render_report`) — a detail screen
   (`style-guide.md#42-detail-screen`) rendering the eleven-field
   `PLReport`. Loading state (`style-guide.md#47-loading-convention-new`)
   before the blocking aggregation call; empty state renders all-zero
   figures plus `"No sales recorded in this period."`; error state
   renders any `ReportingError` via the message convention
   (`style-guide.md#44-message--error-convention`) and returns to Page 1;
   cost-incomplete state (`ADR-009` Decision (c)) renders the banner and
   marks `net` `"(upper bound)"`.

No curses I/O lives in `src.services.reporting` — this module is the only
consumer of its public functions and `ReportingError`/
`ReportingValidationError`.
"""

import curses
import datetime as dt
import sqlite3

from src.config import get_reporting_tz
from src.exceptions import ReportingError
from src.services.reporting import (
    PLReport,
    generate_annual_report,
    generate_daily_report,
    generate_monthly_report,
    generate_quarterly_report,
)
from src.ui.keybindings import (
    EntryCancelled,
    FormField,
    NavAction,
    read_line,
    resolve_action,
    run_form,
)

BACK_ROUTE = "back"
QUIT_ROUTE = "quit"

GRAIN_DAILY = "daily"
GRAIN_MONTHLY = "monthly"
GRAIN_QUARTERLY = "quarterly"
GRAIN_ANNUAL = "annual"


class _GrainOption:
    """One Page-1 row: fast-access key, display label, and the grain id
    `_dispatch_grain` routes on."""

    def __init__(self, key: str, label: str, grain: str) -> None:
        self.key = key
        self.label = label
        self.grain = grain


GRAIN_OPTIONS: list[_GrainOption] = [
    _GrainOption(
        key="d",
        label="Daily (shortcut: d) — single trading day",
        grain=GRAIN_DAILY,
    ),
    _GrainOption(
        key="m",
        label="Monthly (shortcut: m) — calendar month",
        grain=GRAIN_MONTHLY,
    ),
    _GrainOption(
        key="q",
        label=(
            "Quarterly (shortcut: q) — Jan–Mar / Apr–Jun / Jul–Sep / "
            "Oct–Dec, calendar year"
        ),
        grain=GRAIN_QUARTERLY,
    ),
    _GrainOption(
        key="a",
        label="Annual (shortcut: a) — calendar year, 1 Jan – 31 Dec",
        grain=GRAIN_ANNUAL,
    ),
]


class PeriodTypeMenuController:
    """Pure navigation/dispatch state for Page 1 — no curses I/O. Mirrors
    `src.ui.main_menu.MainMenuController`'s pattern."""

    def __init__(self, options: list[_GrainOption] | None = None) -> None:
        self._options: list[_GrainOption] = (
            list(options) if options is not None else list(GRAIN_OPTIONS)
        )
        self._index = 0
        self._shortcut_grains: dict[int, str] = {}
        for option in self._options:
            self._shortcut_grains[ord(option.key.lower())] = option.grain
            self._shortcut_grains[ord(option.key.upper())] = option.grain

    @property
    def options(self) -> list[_GrainOption]:
        return self._options

    @property
    def selected_index(self) -> int:
        return self._index

    def selected_option(self) -> _GrainOption:
        return self._options[self._index]

    def move_up(self) -> None:
        self._index = (self._index - 1) % len(self._options)

    def move_down(self) -> None:
        self._index = (self._index + 1) % len(self._options)

    def dispatch(self, key: int) -> str | None:
        """Resolve one raw key code into a routing outcome — a grain id,
        `BACK_ROUTE`/`QUIT_ROUTE`, or `None` when the key only moved the
        highlighted item (or was unrecognized)."""
        if key in self._shortcut_grains:
            return self._shortcut_grains[key]

        action = resolve_action(key)
        if action is None:
            return None
        if action is NavAction.MOVE_UP:
            self.move_up()
            return None
        if action in (NavAction.MOVE_DOWN, NavAction.NEXT_FIELD):
            self.move_down()
            return None
        if action is NavAction.SELECT:
            return self.selected_option().grain
        if action is NavAction.BACK:
            return BACK_ROUTE
        if action is NavAction.QUIT:
            return QUIT_ROUTE
        return None


def _current_trading_day() -> dt.date:
    return dt.datetime.now(get_reporting_tz()).date()


def _render_menu(stdscr: "curses.window", controller: PeriodTypeMenuController) -> None:
    stdscr.clear()
    stdscr.addstr(0, 0, "Reports — Select Period Type")
    stdscr.addstr(
        1,
        0,
        "Arrows/Tab move, Enter select, Esc/q back, shortcuts: "
        "d=daily m=monthly q=quarterly a=annual",
    )
    for row, option in enumerate(controller.options, start=3):
        prefix = "> " if row - 3 == controller.selected_index else "  "
        stdscr.addstr(row, 0, f"{prefix}{option.label}")
    stdscr.refresh()


def _show_message(stdscr: "curses.window", row: int, message: str) -> None:
    stdscr.addstr(row, 0, message)
    stdscr.refresh()
    stdscr.getch()


def _prompt_daily_period(stdscr: "curses.window") -> dt.date:
    """Single-field Daily prompt (`style-guide.md#43` single-field
    exception) — pre-filled to the current trading day. Raises
    `EntryCancelled` (propagated from `read_line`) on Esc."""
    default = _current_trading_day().isoformat()
    while True:
        stdscr.clear()
        stdscr.addstr(0, 0, "Reports — Daily Period")
        stdscr.addstr(1, 0, "Enter submits, Esc cancels back to Page 1")
        label = f"Date (YYYY-MM-DD) [{default}]: "
        stdscr.addstr(2, 0, label)
        raw = read_line(stdscr, 2, len(label), default=default)
        try:
            return dt.date.fromisoformat(raw)
        except ValueError:
            _show_message(
                stdscr, 4, f"Invalid date {raw!r} — press any key to continue."
            )
            default = raw


def _prompt_annual_period(stdscr: "curses.window") -> int:
    """Single-field Annual prompt — pre-filled to the current trading
    year. Raises `EntryCancelled` (propagated from `read_line`) on Esc."""
    default = str(_current_trading_day().year)
    while True:
        stdscr.clear()
        stdscr.addstr(0, 0, "Reports — Annual Period")
        stdscr.addstr(1, 0, "Enter submits, Esc cancels back to Page 1")
        label = f"Year [{default}]: "
        stdscr.addstr(2, 0, label)
        raw = read_line(stdscr, 2, len(label), default=default)
        try:
            return int(raw)
        except ValueError:
            _show_message(
                stdscr, 4, f"Invalid year {raw!r} — press any key to continue."
            )
            default = raw


def _prompt_monthly_period(stdscr: "curses.window") -> tuple[int, int]:
    """Two-field Monthly prompt via `FormController`/`run_form`
    (`style-guide.md#43`) — pre-filled to the current trading month/year.
    Raises `EntryCancelled` (propagated from `run_form`) on Esc."""
    today = _current_trading_day()
    month_default = str(today.month)
    year_default = str(today.year)
    while True:
        stdscr.clear()
        stdscr.addstr(3, 0, "Reports — Monthly Period — Enter submits, Esc cancels")
        fields = [
            FormField(name="month", label="Month (1-12)", default=month_default),
            FormField(name="year", label="Year", default=year_default),
        ]
        values = run_form(stdscr, fields)
        try:
            month = int(values["month"])
            year = int(values["year"])
            if not 1 <= month <= 12:
                raise ValueError
        except ValueError:
            month_default = values["month"]
            year_default = values["year"]
            _show_message(stdscr, 5, "Invalid month/year — press any key to continue.")
            continue
        return year, month


def _prompt_quarterly_period(stdscr: "curses.window") -> tuple[int, int]:
    """Two-field Quarterly prompt via `FormController`/`run_form` —
    pre-filled to the current trading quarter/year. Raises
    `EntryCancelled` (propagated from `run_form`) on Esc."""
    today = _current_trading_day()
    quarter_default = str((today.month - 1) // 3 + 1)
    year_default = str(today.year)
    while True:
        stdscr.clear()
        stdscr.addstr(3, 0, "Reports — Quarterly Period — Enter submits, Esc cancels")
        fields = [
            FormField(name="quarter", label="Quarter (1-4)", default=quarter_default),
            FormField(name="year", label="Year", default=year_default),
        ]
        values = run_form(stdscr, fields)
        try:
            quarter = int(values["quarter"])
            year = int(values["year"])
            if not 1 <= quarter <= 4:
                raise ValueError
        except ValueError:
            quarter_default = values["quarter"]
            year_default = values["year"]
            _show_message(
                stdscr, 5, "Invalid quarter/year — press any key to continue."
            )
            continue
        return year, quarter


def _render_loading(stdscr: "curses.window") -> None:
    stdscr.clear()
    stdscr.addstr(0, 0, "Generating report — please wait...")
    stdscr.refresh()


def _money_row(stdscr: "curses.window", row: int, label: str, value: float) -> None:
    stdscr.addstr(row, 0, f"{label:<14}{value:>14.2f}")


def _render_report(stdscr: "curses.window", report: PLReport) -> None:
    stdscr.clear()
    stdscr.addstr(0, 0, f"P&L Report — {report.period_label}")
    row = 1
    if not report.cost_complete:
        stdscr.addstr(
            row,
            0,
            f"COGS incomplete: {report.lines_missing_cost} of {report.line_count} "
            "sale lines in this period have no recorded cost. "
            "Net profit is an upper bound.",
        )
        row += 1
    if report.line_count == 0:
        stdscr.addstr(row, 0, "No sales recorded in this period.")
        row += 1
    row += 1
    _money_row(stdscr, row, "Revenue:", report.revenue)
    row += 1
    _money_row(stdscr, row, "Discounts:", report.discounts)
    row += 1
    _money_row(stdscr, row, "COGS:", report.cogs)
    row += 1
    net_suffix = " (upper bound)" if not report.cost_complete else ""
    stdscr.addstr(row, 0, f"{'Net:':<14}{report.net:>14.2f}{net_suffix}")
    row += 2
    stdscr.addstr(row, 0, "Memo:")
    row += 1
    stdscr.addstr(
        row,
        0,
        "Credit sales recognised this period (accrual disclosure, not cash collected):",
    )
    row += 1
    _money_row(stdscr, row, "Credit sales:", report.credit_sales)
    row += 2
    stdscr.addstr(row, 0, "Any key returns to Page 1.")
    stdscr.refresh()


def _collect_and_generate(
    stdscr: "curses.window", conn: sqlite3.Connection, grain: str
) -> PLReport | None:
    """Drive Page 2 for the chosen grain, then Page 3's loading state and
    the blocking aggregation call. Returns `None` (message already shown)
    on a `ReportingError`. `EntryCancelled` from any Page-2 prompt
    propagates to the caller, which returns to Page 1 silently."""
    try:
        if grain == GRAIN_DAILY:
            period_date = _prompt_daily_period(stdscr)
            _render_loading(stdscr)
            return generate_daily_report(conn, period_date)
        if grain == GRAIN_MONTHLY:
            year, month = _prompt_monthly_period(stdscr)
            _render_loading(stdscr)
            return generate_monthly_report(conn, year, month)
        if grain == GRAIN_QUARTERLY:
            year, quarter = _prompt_quarterly_period(stdscr)
            _render_loading(stdscr)
            return generate_quarterly_report(conn, year, quarter)
        year = _prompt_annual_period(stdscr)
        _render_loading(stdscr)
        return generate_annual_report(conn, year)
    except ReportingError as exc:
        _show_message(
            stdscr,
            2,
            f"Could not generate report: {exc} — press any key to return.",
        )
        return None


def run_reports_screen(stdscr: "curses.window", conn: sqlite3.Connection) -> None:
    """Drive the reporting curses loop until the user backs out or quits
    Page 1. Page 2/Page 3 always return to Page 1 (cancel, error, or any
    key after a rendered report)."""
    curses.curs_set(0)
    controller = PeriodTypeMenuController()
    while True:
        _render_menu(stdscr, controller)
        key = stdscr.getch()
        grain = controller.dispatch(key)
        if grain is None:
            continue
        if grain in (BACK_ROUTE, QUIT_ROUTE):
            return
        try:
            report = _collect_and_generate(stdscr, conn, grain)
        except EntryCancelled:
            continue
        if report is None:
            continue
        _render_report(stdscr, report)
        stdscr.getch()
