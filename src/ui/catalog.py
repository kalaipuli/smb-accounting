"""Curses catalog & discount management screens (P1-F3-T4).

Reachable from the main menu's "catalog" route (`src/ui/main_menu.py`,
P1-F2-T1, already Done). Follows the shared key-binding convention
(arrows/Tab/Enter/Esc-q) from `src/ui/keybindings.py` — no new key-binding
scheme is introduced here, per this feature's Expectations.

`CatalogController` holds pure navigation/dispatch state (the product list
and the highlighted index) with no curses I/O, so it is unit-testable
without a real terminal — mirroring `src/ui/main_menu.py`'s
`MainMenuController` pattern. `run_catalog_screen` is the thin curses
render loop that drives the controller and calls
`src.services.catalog`/`src.services.discounts` for create/edit/apply.

Raw curses text-entry (`_prompt_product_fields`/`_prompt_discount_fields`)
reads one keycode at a time via `src.ui.keybindings.read_line`, which
supports Esc-to-cancel and pre-filled (editable) default values — like the
rest of this module's dispatch logic, it is driven entirely through
`stdscr.getch()`/`addstr`, so it is unit-testable with a fake window (a
predetermined key queue) without a real terminal.
"""

import curses
import sqlite3

from src.exceptions import CatalogError, DiscountError, DiscountExceedsPriceError
from src.services.catalog import Product, create_product, list_products, update_product
from src.services.discounts import (
    Discount,
    apply_discount,
    create_discount,
    list_discounts_for_product,
)
from src.ui.keybindings import EntryCancelled, NavAction, read_line, resolve_action

BACK_ROUTE = "back"
QUIT_ROUTE = "quit"
SELECT_ROUTE = "select"
NEW_PRODUCT_ROUTE = "new_product"
NEW_DISCOUNT_ROUTE = "new_discount"
EDIT_PRODUCT_ROUTE = "edit_product"


class CatalogController:
    """Pure navigation state over the product list — no curses I/O."""

    def __init__(self, products: list[Product] | None = None) -> None:
        self._products: list[Product] = list(products) if products is not None else []
        self._index = 0

    @property
    def products(self) -> list[Product]:
        return self._products

    @property
    def selected_index(self) -> int:
        return self._index

    def selected_product(self) -> Product | None:
        if not self._products:
            return None
        return self._products[self._index]

    def refresh(self, products: list[Product]) -> None:
        """Replace the product list, clamping the selection into range."""
        self._products = list(products)
        if self._index >= len(self._products):
            self._index = max(0, len(self._products) - 1)

    def move_up(self) -> None:
        if self._products:
            self._index = (self._index - 1) % len(self._products)

    def move_down(self) -> None:
        if self._products:
            self._index = (self._index + 1) % len(self._products)

    def dispatch(self, key: int) -> str | None:
        """Resolve one raw key code into a routing outcome.

        Returns a route string when the key routes away from plain
        navigation (select, new product/discount shortcuts, back, quit);
        returns None when the key only moved the highlighted item (or was
        unrecognized).
        """
        if key in (ord("n"), ord("N")):
            return NEW_PRODUCT_ROUTE
        if key in (ord("d"), ord("D")):
            return NEW_DISCOUNT_ROUTE
        if key in (ord("e"), ord("E")):
            return EDIT_PRODUCT_ROUTE

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
            return SELECT_ROUTE
        if action is NavAction.BACK:
            return BACK_ROUTE
        if action is NavAction.QUIT:
            return QUIT_ROUTE
        return None


def _prompt_field(
    stdscr: "curses.window", row: int, label: str, default: str = ""
) -> str:
    """Prompt for one field, showing `default` as its editable starting
    value (bracketed hint; pressing Enter with no typed input keeps it).
    Raises `EntryCancelled` (propagated from `read_line`) if the user
    presses Esc.
    """
    prompt = f"{label} [{default}]: " if default else f"{label}: "
    stdscr.addstr(row, 0, prompt)
    return read_line(stdscr, row, len(prompt), default=default)


def _prompt_product_fields(
    stdscr: "curses.window", existing: Product | None = None
) -> tuple[str, float, float, float | None] | None:
    """Collect name/price/gst_rate/cost from the user. Returns None on bad
    input. When `existing` is given, each field is pre-filled with its
    current value as the editable starting value (Enter keeps it
    unchanged). Cost is optional — a blank input maps to `None` ("cost
    unknown", ADR-009 Decision (a)), not `0`. Raises `EntryCancelled`
    (propagated) if the user cancels via Esc on any field — callers MUST
    treat that as a distinct path from the `None`/bad-input return.
    """
    stdscr.clear()
    stdscr.addstr(0, 0, "Edit Product" if existing else "New Product")
    try:
        name = _prompt_field(
            stdscr, 2, "Name", default=existing.name if existing else ""
        )
        price = float(
            _prompt_field(
                stdscr, 3, "Price", default=str(existing.price) if existing else ""
            )
        )
        gst_rate = float(
            _prompt_field(
                stdscr,
                4,
                "GST rate",
                default=str(existing.gst_rate) if existing else "",
            )
        )
        cost_default = (
            str(existing.cost) if existing and existing.cost is not None else ""
        )
        cost_input = _prompt_field(stdscr, 5, "Cost (blank = unknown)", cost_default)
        cost = float(cost_input) if cost_input.strip() else None
    except ValueError:
        return None
    return name, price, gst_rate, cost


def _prompt_discount_fields(stdscr: "curses.window") -> tuple[str, float] | None:
    """Collect discount_type/value from the user. Returns None on bad
    input. Raises `EntryCancelled` (propagated) if the user cancels via
    Esc on any field.
    """
    stdscr.clear()
    stdscr.addstr(0, 0, "New Discount (type: percentage or flat)")
    try:
        discount_type = _prompt_field(stdscr, 2, "Type")
        value = float(_prompt_field(stdscr, 3, "Value"))
    except ValueError:
        return None
    return discount_type, value


def _show_message(stdscr: "curses.window", row: int, message: str) -> None:
    stdscr.addstr(row, 0, message)
    stdscr.refresh()
    stdscr.getch()


def _render(stdscr: "curses.window", controller: CatalogController) -> None:
    stdscr.clear()
    stdscr.addstr(0, 0, "Catalog")
    stdscr.addstr(
        1,
        0,
        "Arrows/Tab move, Enter discounts, n new, e edit, d add discount, Esc/q back",
    )
    for row, product in enumerate(controller.products, start=3):
        prefix = "> " if row - 3 == controller.selected_index else "  "
        cost_display = "unknown" if product.cost is None else str(product.cost)
        stdscr.addstr(
            row,
            0,
            f"{prefix}{product.name}  price={product.price}  gst={product.gst_rate}%"
            f"  cost={cost_display}",
        )
    if not controller.products:
        stdscr.addstr(3, 0, "(no products yet — press 'n' to add one)")
    stdscr.refresh()


def _render_discounts(
    stdscr: "curses.window", product: Product, discounts: list[Discount]
) -> None:
    stdscr.clear()
    stdscr.addstr(0, 0, f"Discounts for {product.name} (price={product.price})")
    stdscr.addstr(1, 0, "'d' add discount, any other key to go back")
    for row, discount in enumerate(discounts, start=3):
        try:
            discounted = apply_discount(product.price, discount)
        except DiscountExceedsPriceError:
            stdscr.addstr(
                row,
                0,
                f"  {discount.discount_type} {discount.value} -> "
                "(no longer valid at current price)",
            )
            continue
        stdscr.addstr(
            row,
            0,
            f"  {discount.discount_type} {discount.value} -> {discounted:.2f}",
        )
    if not discounts:
        stdscr.addstr(3, 0, "(no discounts yet)")
    stdscr.refresh()


def _handle_new_product(stdscr: "curses.window", conn: sqlite3.Connection) -> None:
    try:
        fields = _prompt_product_fields(stdscr)
    except EntryCancelled:
        return
    if fields is None:
        _show_message(stdscr, 6, "Invalid input — press any key to continue.")
        return
    name, price, gst_rate, cost = fields
    try:
        create_product(conn, name=name, price=price, gst_rate=gst_rate, cost=cost)
    except CatalogError as exc:
        _show_message(stdscr, 6, f"Error: {exc} — press any key to continue.")


def _handle_edit_product(
    stdscr: "curses.window", conn: sqlite3.Connection, product: Product
) -> None:
    try:
        fields = _prompt_product_fields(stdscr, existing=product)
    except EntryCancelled:
        return
    if fields is None:
        _show_message(stdscr, 6, "Invalid input — press any key to continue.")
        return
    name, price, gst_rate, cost = fields
    try:
        update_product(
            conn, product.id, name=name, price=price, gst_rate=gst_rate, cost=cost
        )
    except CatalogError as exc:
        _show_message(stdscr, 6, f"Error: {exc} — press any key to continue.")


def _handle_new_discount(
    stdscr: "curses.window", conn: sqlite3.Connection, product: Product
) -> None:
    try:
        fields = _prompt_discount_fields(stdscr)
    except EntryCancelled:
        return
    if fields is None:
        _show_message(stdscr, 6, "Invalid input — press any key to continue.")
        return
    discount_type, value = fields
    try:
        create_discount(conn, product.id, discount_type=discount_type, value=value)
    except DiscountError as exc:
        _show_message(stdscr, 6, f"Error: {exc} — press any key to continue.")


def _run_discount_screen(
    stdscr: "curses.window", conn: sqlite3.Connection, product: Product
) -> None:
    while True:
        discounts = list_discounts_for_product(conn, product.id)
        _render_discounts(stdscr, product, discounts)
        key = stdscr.getch()
        if key in (ord("d"), ord("D")):
            _handle_new_discount(stdscr, conn, product)
            continue
        return


def _dispatch_route(
    stdscr: "curses.window",
    conn: sqlite3.Connection,
    controller: CatalogController,
    route: str,
) -> None:
    """Handle one resolved catalog route (all routes except back/quit)."""
    if route == NEW_PRODUCT_ROUTE:
        _handle_new_product(stdscr, conn)
        return
    product = controller.selected_product()
    if product is None:
        return
    if route == EDIT_PRODUCT_ROUTE:
        _handle_edit_product(stdscr, conn, product)
    elif route == SELECT_ROUTE:
        _run_discount_screen(stdscr, conn, product)
    elif route == NEW_DISCOUNT_ROUTE:
        _handle_new_discount(stdscr, conn, product)


def run_catalog_screen(stdscr: "curses.window", conn: sqlite3.Connection) -> None:
    """Drive the catalog curses loop until the user backs out or quits."""
    controller = CatalogController(list_products(conn))
    curses.curs_set(0)
    while True:
        controller.refresh(list_products(conn))
        _render(stdscr, controller)
        key = stdscr.getch()
        route = controller.dispatch(key)
        if route is None:
            continue
        if route in (BACK_ROUTE, QUIT_ROUTE):
            return
        _dispatch_route(stdscr, conn, controller, route)
