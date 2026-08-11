"""Curses main menu shell.

Lists the five top-level destinations (Sales / Inventory / Catalog / Reports
/ Customers), supports arrow/Tab navigation with wraparound, Enter to
select, Esc/q to go back or quit, and a fast-access single-key shortcut per
option (`s`/`i`/`c`/`r`/`u`, case-insensitive) that jumps straight to that
option's route regardless of which item is currently highlighted.

Navigation/dispatch logic lives in `MainMenuController`, kept free of any
curses I/O so it is unit-testable without a real terminal. `run_main_menu`
is the thin curses render loop that drives the controller.
"""

import curses
import sqlite3
from dataclasses import dataclass

from src.ui.catalog import run_catalog_screen
from src.ui.customers import run_customers_screen
from src.ui.inventory import run_inventory_screen
from src.ui.keybindings import NavAction, resolve_action
from src.ui.reports import run_reports_screen
from src.ui.sales import run_sales_history_screen, run_sales_screen

SALE_ENTRY_ROUTE = "sale_entry"
CATALOG_ROUTE = "catalog"
INVENTORY_ROUTE = "inventory"
CUSTOMERS_ROUTE = "customers"
SALES_HISTORY_ROUTE = "sales_history"
REPORTS_ROUTE = "reports"
BACK_ROUTE = "back"
QUIT_ROUTE = "quit"


@dataclass(frozen=True)
class MenuOption:
    key: str
    label: str
    route: str


MAIN_MENU_OPTIONS: list[MenuOption] = [
    MenuOption(key="s", label="Record a sale (shortcut: s)", route=SALE_ENTRY_ROUTE),
    MenuOption(key="i", label="Inventory (shortcut: i)", route=INVENTORY_ROUTE),
    MenuOption(key="c", label="Catalog (shortcut: c)", route=CATALOG_ROUTE),
    MenuOption(key="r", label="Reports (shortcut: r)", route="reports"),
    MenuOption(key="u", label="Customers (shortcut: u)", route=CUSTOMERS_ROUTE),
    MenuOption(key="h", label="Sales History (shortcut: h)", route=SALES_HISTORY_ROUTE),
]


class MainMenuController:
    """Pure navigation/dispatch state for the main menu — no curses I/O."""

    def __init__(self, options: list[MenuOption] | None = None) -> None:
        self._options: list[MenuOption] = (
            list(options) if options is not None else list(MAIN_MENU_OPTIONS)
        )
        if not self._options:
            raise ValueError("MainMenuController requires at least one option")
        self._index = 0
        # Fast-access single-key jump table, derived from each option's own
        # `key` field — every option's shortcut (both cases) jumps straight
        # to its route regardless of the currently highlighted item, the
        # same behaviour the sale-entry shortcut ("s") already had.
        self._shortcut_routes: dict[int, str] = {}
        for option in self._options:
            self._shortcut_routes[ord(option.key.lower())] = option.route
            self._shortcut_routes[ord(option.key.upper())] = option.route

    @property
    def options(self) -> list[MenuOption]:
        return self._options

    @property
    def selected_index(self) -> int:
        return self._index

    def selected_option(self) -> MenuOption:
        return self._options[self._index]

    def move_up(self) -> None:
        self._index = (self._index - 1) % len(self._options)

    def move_down(self) -> None:
        self._index = (self._index + 1) % len(self._options)

    def dispatch(self, key: int) -> str | None:
        """Resolve one raw key code into a routing outcome.

        Returns a route string when the key routes away from the menu
        (select, shortcut, back, quit); returns None when the key only
        moved the highlighted item (or was unrecognized).
        """
        if key in self._shortcut_routes:
            return self._shortcut_routes[key]

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
            return self.selected_option().route
        if action is NavAction.BACK:
            return BACK_ROUTE
        if action is NavAction.QUIT:
            return QUIT_ROUTE
        return None


def _render(stdscr: "curses.window", controller: MainMenuController) -> None:
    stdscr.clear()
    stdscr.addstr(0, 0, "SMB Account Software — Main Menu")
    stdscr.addstr(
        1,
        0,
        "Arrows/Tab move, Enter select, Esc/q back, "
        "shortcuts: s=sale i=inventory c=catalog r=reports u=customers h=history",
    )
    for row, option in enumerate(controller.options, start=3):
        prefix = "> " if row - 3 == controller.selected_index else "  "
        stdscr.addstr(row, 0, f"{prefix}{option.label}")
    stdscr.refresh()


def run_main_menu(stdscr: "curses.window", conn: sqlite3.Connection) -> None:
    """Drive the main menu curses loop until the user backs out or quits."""
    controller = MainMenuController()
    curses.curs_set(0)
    while True:
        _render(stdscr, controller)
        key = stdscr.getch()
        route = controller.dispatch(key)
        if route is None:
            continue
        if route in (BACK_ROUTE, QUIT_ROUTE):
            return
        if route == SALE_ENTRY_ROUTE:
            run_sales_screen(stdscr, conn)
            continue
        if route == CATALOG_ROUTE:
            run_catalog_screen(stdscr, conn)
            continue
        if route == INVENTORY_ROUTE:
            run_inventory_screen(stdscr, conn)
            continue
        if route == CUSTOMERS_ROUTE:
            run_customers_screen(stdscr, conn)
            continue
        if route == SALES_HISTORY_ROUTE:
            run_sales_history_screen(stdscr, conn)
            continue
        if route == REPORTS_ROUTE:
            run_reports_screen(stdscr, conn)
            continue
        continue
