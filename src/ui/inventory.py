"""Curses inventory stock-level view/adjust screen (P1-F4-T3).

Reachable from the main menu's "inventory" route (`src/ui/main_menu.py`,
P1-F2-T1, already Done). Follows the shared key-binding convention
(arrows/Tab/Enter/Esc-q) from `src/ui/keybindings.py`, mirroring
`src/ui/catalog.py`'s established screen/menu-wiring pattern, per this
feature's Expectations.

`InventoryController` holds pure navigation/dispatch state (the product
list paired with its current stock level, and the highlighted index) with
no curses I/O, so it is unit-testable without a real terminal — mirroring
`CatalogController`. `run_inventory_screen` is the thin curses render loop
that drives the controller and calls `src.services.inventory` for
read/adjust.

Raw curses text-entry (`_prompt_adjust_amount`) reads one keycode at a
time via `src.ui.keybindings.read_line` (shared with `catalog.py`'s field
prompts), which supports Esc-to-cancel — so it is unit-testable with a
fake window (a predetermined key queue) without a real terminal.
"""

import curses
import sqlite3
from dataclasses import dataclass

from src.exceptions import InventoryError
from src.services.catalog import Product, list_products
from src.services.inventory import StockLevel, adjust_stock, get_stock
from src.ui.keybindings import EntryCancelled, NavAction, read_line, resolve_action

BACK_ROUTE = "back"
QUIT_ROUTE = "quit"
ADJUST_ROUTE = "adjust"


@dataclass(frozen=True)
class InventoryRow:
    product: Product
    stock: StockLevel


class InventoryController:
    """Pure navigation state over the product/stock list — no curses I/O."""

    def __init__(self, rows: list[InventoryRow] | None = None) -> None:
        self._rows: list[InventoryRow] = list(rows) if rows is not None else []
        self._index = 0

    @property
    def rows(self) -> list[InventoryRow]:
        return self._rows

    @property
    def selected_index(self) -> int:
        return self._index

    def selected_row(self) -> InventoryRow | None:
        if not self._rows:
            return None
        return self._rows[self._index]

    def refresh(self, rows: list[InventoryRow]) -> None:
        """Replace the row list, clamping the selection into range."""
        self._rows = list(rows)
        if self._index >= len(self._rows):
            self._index = max(0, len(self._rows) - 1)

    def move_up(self) -> None:
        if self._rows:
            self._index = (self._index - 1) % len(self._rows)

    def move_down(self) -> None:
        if self._rows:
            self._index = (self._index + 1) % len(self._rows)

    def dispatch(self, key: int) -> str | None:
        """Resolve one raw key code into a routing outcome.

        Returns a route string when the key routes away from plain
        navigation (adjust, back, quit); returns None when the key only
        moved the highlighted item (or was unrecognized).
        """
        if key in (ord("a"), ord("A")):
            return ADJUST_ROUTE

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
            return ADJUST_ROUTE
        if action is NavAction.BACK:
            return BACK_ROUTE
        if action is NavAction.QUIT:
            return QUIT_ROUTE
        return None


def _load_rows(conn: sqlite3.Connection) -> list[InventoryRow]:
    return [
        InventoryRow(product=product, stock=get_stock(conn, product.id))
        for product in list_products(conn)
    ]


def _prompt_adjust_amount(stdscr: "curses.window", row: int) -> int | None:
    """Collect a signed delta from the user. Returns None on bad input.
    Raises `EntryCancelled` (propagated from `read_line`) if the user
    presses Esc — callers MUST treat that as a distinct path from the
    `None`/bad-input return.
    """
    label = "Delta (+/-): "
    stdscr.addstr(row, 0, label)
    raw = read_line(stdscr, row, len(label))
    try:
        return int(raw.strip())
    except ValueError:
        return None


def _show_message(stdscr: "curses.window", row: int, message: str) -> None:
    stdscr.addstr(row, 0, message)
    stdscr.refresh()
    stdscr.getch()


def _render(stdscr: "curses.window", controller: InventoryController) -> None:
    stdscr.clear()
    stdscr.addstr(0, 0, "Inventory")
    stdscr.addstr(1, 0, "Arrows/Tab move, Enter/a adjust stock, Esc/q back")
    for row, item in enumerate(controller.rows, start=3):
        prefix = "> " if row - 3 == controller.selected_index else "  "
        stdscr.addstr(
            row,
            0,
            f"{prefix}{item.product.name}  qty={item.stock.quantity}",
        )
    if not controller.rows:
        stdscr.addstr(3, 0, "(no products yet — add one via Catalog)")
    stdscr.refresh()


def _handle_adjust(
    stdscr: "curses.window", conn: sqlite3.Connection, row: InventoryRow
) -> None:
    stdscr.clear()
    stdscr.addstr(0, 0, f"Adjust stock for {row.product.name}")
    stdscr.addstr(1, 0, f"Current quantity: {row.stock.quantity}")
    try:
        delta = _prompt_adjust_amount(stdscr, 3)
    except EntryCancelled:
        return
    if delta is None:
        _show_message(stdscr, 5, "Invalid input — press any key to continue.")
        return
    try:
        adjust_stock(conn, row.product.id, delta)
    except InventoryError as exc:
        _show_message(stdscr, 5, f"Error: {exc} — press any key to continue.")


def _dispatch_route(
    stdscr: "curses.window",
    conn: sqlite3.Connection,
    controller: InventoryController,
    route: str,
) -> None:
    """Handle one resolved inventory route (all routes except back/quit)."""
    row = controller.selected_row()
    if row is None:
        return
    if route == ADJUST_ROUTE:
        _handle_adjust(stdscr, conn, row)


def run_inventory_screen(stdscr: "curses.window", conn: sqlite3.Connection) -> None:
    """Drive the inventory curses loop until the user backs out or quits."""
    controller = InventoryController(_load_rows(conn))
    curses.curs_set(0)
    while True:
        controller.refresh(_load_rows(conn))
        _render(stdscr, controller)
        key = stdscr.getch()
        route = controller.dispatch(key)
        if route is None:
            continue
        if route in (BACK_ROUTE, QUIT_ROUTE):
            return
        _dispatch_route(stdscr, conn, controller, route)
