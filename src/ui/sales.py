"""Curses sale-entry screen (P2-F1-T5).

Reachable from the main menu's "Record a sale" route and its `s`
fast-access shortcut (`ARCH-U02`, already wired in
`src/ui/main_menu.py`'s `MainMenuController._shortcut_routes`) — this
module replaces `src/ui/sale_entry.py`'s `render_sale_entry_stub`
placeholder with the real screen. Follows the shared key-binding
convention (arrows/Tab/Enter/Esc-q) from `src/ui/keybindings.py`,
mirroring `src/ui/catalog.py`/`src/ui/inventory.py`/`src/ui/customers.py`'s
list -> action -> pure-navigation-controller structure.

Narrative flow per `ARCH-F01`: browse/select product(s) and quantity →
apply discount if any → add as a cart line item → checkout (choose
paid/credit; a credit sale requires selecting an existing customer) → GST
calculated and the sale committed atomically via
`src/services/sales.py`'s `record_sale()` (`ADR-005`) → receipt summary
shown (line items, discounts, GST breakdown, bill total, payment status).

`SalesController` holds pure navigation/cart state (the product list, the
highlighted index, and the pending cart) with no curses I/O, so it is
unit-testable without a real terminal — mirroring
`CatalogController`/`InventoryController`/`CustomerController`.
`run_sales_screen` is the thin curses render loop that drives the
controller and calls `src.services.sales`/`src.services.customers`/
`src.services.discounts` for cart pricing, customer lookup, and the
final atomic commit.

Line-item entry is a two-step flow (Finding 2 fix, phase2-feature5-sale-
entry-fixes): quantity is collected via `FormController`/`run_form`
(`src/ui/keybindings.py`, `ADR-007`, Feature 4/`P2-F4-T2`) — a single-field
form today, kept on `run_form` rather than dropped to `read_line` per the
architect's decision in that space — then, after a read-only stock
pre-check against `src.services.inventory.get_stock` (advisory only;
`record_sale`'s atomic `decrement_stock` remains the sole enforcement
point per `ADR-005`), discount is chosen via a separate list-navigation
step (`_select_discount`/`_render_discount_select`, mirroring
`_select_customer`/`_render_customer_select`) rather than typed as a raw
discount ID — selecting one `Discount` out of
`list_discounts_for_product`'s results is a list-navigation concern, not
a free-text concern (ADR-007's own Alternatives Considered reasoning).
The payment-status choice (paid/credit) is a single keypress, not a text
field, so it reads raw keys directly (mirroring `customers.py`'s
single-field `_prompt_payment_amount` judgment call for a genuinely
single-value prompt) rather than routing through a one-field form.

Domain exceptions from `record_sale()` (`SalesError` family — validation
failures, insufficient stock surfaced as `SaleTransactionError`) and from
customer lookup (`CustomerError` family) are caught and shown as an
on-screen message; this module never lets a raw exception crash the
curses loop.

No raw `customer.name`/`.phone`/`.address` field reaches a `logger.*()`
call anywhere in this module (`ADR-006` Decision (d), `ARCH-D01`, SD-004,
`.claude/hooks/stop.sh` gate 10) — this module does not log at all,
matching `src/ui/customers.py`'s existing no-logging convention.
"""

import curses
import sqlite3
from dataclasses import dataclass

from src.exceptions import CustomerError, SalesError
from src.services.catalog import Product, get_product, list_products
from src.services.customers import Customer, get_customer, list_customers
from src.services.discounts import Discount, list_discounts_for_product
from src.services.inventory import get_stock
from src.services.sales import (
    CartLine,
    Sale,
    SaleLineItem,
    get_sale,
    list_sales,
    record_sale,
)
from src.ui.keybindings import (
    EntryCancelled,
    FormField,
    NavAction,
    resolve_action,
    run_form,
)

BACK_ROUTE = "back"
QUIT_ROUTE = "quit"
ADD_ROUTE = "add"
CHECKOUT_ROUTE = "checkout"
HISTORY_DETAIL_ROUTE = "history_detail"


@dataclass(frozen=True)
class CartItemDisplay:
    """One pending cart line, before checkout — carries the full `Product`
    (not just its id) so the on-screen cart list can render the product's
    name without a repeated lookup, and the resolved `Discount` (if any)
    for the same reason."""

    product: Product
    quantity: int
    discount: Discount | None = None


class SalesController:
    """Pure navigation + pending-cart state over the product list — no
    curses I/O. Mirrors `CatalogController`'s product-list navigation,
    extended with a cart the checkout step consumes."""

    def __init__(self, products: list[Product] | None = None) -> None:
        self._products: list[Product] = list(products) if products is not None else []
        self._index = 0
        self._cart: list[CartItemDisplay] = []

    @property
    def products(self) -> list[Product]:
        return self._products

    @property
    def selected_index(self) -> int:
        return self._index

    @property
    def cart(self) -> list[CartItemDisplay]:
        return self._cart

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

    def add_item(self, item: CartItemDisplay) -> None:
        self._cart.append(item)

    def clear_cart(self) -> None:
        self._cart = []

    def cart_lines(self) -> list[CartLine]:
        """Convert the pending display cart into `src.services.sales`'
        `CartLine` list, the shape `compute_bill`/`record_sale` expect."""
        return [
            CartLine(
                product_id=item.product.id,
                quantity=item.quantity,
                discount=item.discount,
            )
            for item in self._cart
        ]

    def dispatch(self, key: int) -> str | None:
        """Resolve one raw key code into a routing outcome.

        Returns a route string when the key routes away from plain
        navigation (add line item, checkout, back, quit); returns None
        when the key only moved the highlighted item (or was
        unrecognized).
        """
        if key in (ord("a"), ord("A")):
            return ADD_ROUTE
        if key in (ord("c"), ord("C")):
            return CHECKOUT_ROUTE

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
            return ADD_ROUTE
        if action is NavAction.BACK:
            return BACK_ROUTE
        if action is NavAction.QUIT:
            return QUIT_ROUTE
        return None


def _show_message(stdscr: "curses.window", row: int, message: str) -> None:
    stdscr.addstr(row, 0, message)
    stdscr.refresh()
    stdscr.getch()


def _render(stdscr: "curses.window", controller: SalesController) -> None:
    stdscr.clear()
    stdscr.addstr(0, 0, "Record a Sale")
    stdscr.addstr(
        1, 0, "Arrows/Tab move, Enter/a add line item, c checkout, Esc/q back"
    )
    row = 3
    for i, product in enumerate(controller.products):
        prefix = "> " if i == controller.selected_index else "  "
        stdscr.addstr(
            row,
            0,
            f"{prefix}{product.name}  price={product.price}  gst={product.gst_rate}%",
        )
        row += 1
    if not controller.products:
        stdscr.addstr(row, 0, "(no products yet — add one via Catalog)")
        row += 1
    row += 1
    stdscr.addstr(row, 0, "Cart:")
    row += 1
    if not controller.cart:
        stdscr.addstr(row, 0, "  (empty)")
        row += 1
    for item in controller.cart:
        discount_label = (
            f" discount(id={item.discount.id})={item.discount.discount_type}:"
            f"{item.discount.value}"
            if item.discount
            else ""
        )
        stdscr.addstr(row, 0, f"  {item.product.name} x{item.quantity}{discount_label}")
        row += 1
    stdscr.refresh()


def _prompt_line_item_fields(
    stdscr: "curses.window", product: Product
) -> dict[str, str]:
    """Collect quantity via `FormController`'s `run_form`
    (`src/ui/keybindings.py`, `ADR-007`) — a single-field form today
    (phase2-feature5-sale-entry-fixes, action 3: the free-text discount ID
    field was removed; discount is now chosen via the separate
    `_select_discount` list-navigation step called from `_handle_add_item`
    after quantity is validated). `run_form` is kept rather than dropped to
    `read_line` for this one remaining field per the architect's decision
    in that space — an implementation-convenience choice, not an
    architectural requirement.

    `run_form` renders its fields starting at row 0 (`ADR-007` Decision
    (c), fixed one-row-per-field layout) — the title/instructions lines
    are written below the fixed field block so the two do not collide.

    Raises `EntryCancelled` (propagated from `run_form`) if the user
    presses Esc.
    """
    stdscr.clear()
    stdscr.addstr(3, 0, f"Add line item — {product.name} (price={product.price})")
    stdscr.addstr(4, 0, "Arrows/Tab move, Enter submits, Esc cancels")
    fields = [FormField(name="quantity", label="Quantity")]
    return run_form(stdscr, fields)


def _render_discount_select(
    stdscr: "curses.window", discounts: list[Discount], index: int
) -> None:
    stdscr.clear()
    stdscr.addstr(0, 0, "Select Discount")
    stdscr.addstr(1, 0, "Arrows/Tab move, Enter select, Esc cancels")
    labels = ["(no discount)"] + [
        f"{d.discount_type} {d.value} (id={d.id})" for d in discounts
    ]
    for row, label in enumerate(labels, start=3):
        prefix = "> " if row - 3 == index else "  "
        stdscr.addstr(row, 0, f"{prefix}{label}")
    stdscr.refresh()


def _select_discount(
    stdscr: "curses.window", conn: sqlite3.Connection, product_id: int
) -> Discount | None:
    """Browse/select a discount for the product being added to the cart
    (phase2-feature5-sale-entry-fixes, action 3), mirroring
    `_select_customer`'s control flow. A synthetic "(no discount)" entry
    is always prepended to `list_discounts_for_product`'s results, so
    unlike `_select_customer` this never needs a None-for-empty-list early
    return — the list is never actually empty. Raises `EntryCancelled` if
    the user cancels via Esc/q."""
    discounts = list_discounts_for_product(conn, product_id)
    options: list[Discount | None] = [None, *discounts]
    index = 0
    while True:
        _render_discount_select(stdscr, discounts, index)
        key = stdscr.getch()
        if key == 27:  # Esc — no dedicated curses.KEY_* constant
            raise EntryCancelled
        action = resolve_action(key)
        if action is NavAction.MOVE_UP:
            index = (index - 1) % len(options)
            continue
        if action in (NavAction.MOVE_DOWN, NavAction.NEXT_FIELD):
            index = (index + 1) % len(options)
            continue
        if action is NavAction.SELECT:
            return options[index]
        if action is NavAction.QUIT:
            raise EntryCancelled
        continue


def _handle_add_item(
    stdscr: "curses.window",
    conn: sqlite3.Connection,
    controller: SalesController,
    product: Product,
) -> None:
    try:
        values = _prompt_line_item_fields(stdscr, product)
    except EntryCancelled:
        return

    try:
        quantity = int(values["quantity"].strip())
    except ValueError:
        _show_message(stdscr, 6, "Invalid quantity — press any key to continue.")
        return
    if quantity <= 0:
        _show_message(
            stdscr, 6, "Quantity must be positive — press any key to continue."
        )
        return

    # Read-only stock pre-check (phase2-feature5-sale-entry-fixes, action 2):
    # advisory only — `record_sale`'s atomic `decrement_stock` remains the
    # sole enforcement point (ADR-005); this narrows how often that path is
    # hit, it does not replace it.
    already_in_cart = sum(
        item.quantity for item in controller.cart if item.product.id == product.id
    )
    stock = get_stock(conn, product.id)
    if already_in_cart + quantity > stock.quantity:
        _show_message(
            stdscr,
            6,
            "Insufficient stock for this quantity — press any key to continue.",
        )
        return

    try:
        discount = _select_discount(stdscr, conn, product.id)
    except EntryCancelled:
        return

    controller.add_item(
        CartItemDisplay(product=product, quantity=quantity, discount=discount)
    )


def _prompt_payment_status(stdscr: "curses.window") -> str:
    """Collect the payment status via a single keypress — not a form, since
    it is a single choice between two literal options, not free text.
    Raises `EntryCancelled` if the user presses Esc."""
    stdscr.clear()
    stdscr.addstr(0, 0, "Payment status: p = paid, c = credit, Esc cancels")
    stdscr.refresh()
    while True:
        key = stdscr.getch()
        if key == 27:  # Esc — no dedicated curses.KEY_* constant
            raise EntryCancelled
        if key in (ord("p"), ord("P")):
            return "paid"
        if key in (ord("c"), ord("C")):
            return "credit"


def _render_customer_select(
    stdscr: "curses.window", customers: list[Customer], index: int
) -> None:
    stdscr.clear()
    stdscr.addstr(0, 0, "Select Customer (credit sale)")
    stdscr.addstr(1, 0, "Arrows/Tab move, Enter select, Esc cancels")
    for row, customer in enumerate(customers, start=3):
        prefix = "> " if row - 3 == index else "  "
        stdscr.addstr(row, 0, f"{prefix}{customer.name}  {customer.phone}")
    stdscr.refresh()


def _select_customer(
    stdscr: "curses.window", conn: sqlite3.Connection
) -> Customer | None:
    """Browse/select an existing customer for a credit sale. Returns
    `None` when no customer exists yet (caller must surface this — the
    checkout cannot proceed). Raises `EntryCancelled` if the user cancels
    via Esc/q."""
    customers = list_customers(conn)
    if not customers:
        return None
    index = 0
    while True:
        _render_customer_select(stdscr, customers, index)
        key = stdscr.getch()
        if key == 27:  # Esc — no dedicated curses.KEY_* constant
            raise EntryCancelled
        action = resolve_action(key)
        if action is NavAction.MOVE_UP:
            index = (index - 1) % len(customers)
            continue
        if action in (NavAction.MOVE_DOWN, NavAction.NEXT_FIELD):
            index = (index + 1) % len(customers)
            continue
        if action is NavAction.SELECT:
            return customers[index]
        if action is NavAction.QUIT:
            raise EntryCancelled
        continue


def _render_line_items(
    stdscr: "curses.window",
    conn: sqlite3.Connection,
    line_items: list[SaleLineItem],
    start_row: int,
) -> int:
    """Render one line per `SaleLineItem`, starting at `start_row`.
    Factored out of `_render_receipt` (phase2-feature6-sales-customer-gaps,
    Action 3 / architect finding 2) so both the post-checkout receipt and
    the new sales-history detail view (`_render_history_detail`) share this
    rendering logic rather than duplicating it. Returns the next unused
    row, so callers can continue writing below the rendered block."""
    row = start_row
    for line in line_items:
        product = get_product(conn, line.product_id)
        stdscr.addstr(
            row,
            0,
            f"{product.name} x{line.quantity} @ {line.unit_price:.2f} "
            f"discount={line.discount_amount:.2f} gst={line.line_gst_amount:.2f} "
            f"= {line.line_total:.2f}",
        )
        row += 1
    return row


def _render_receipt(
    stdscr: "curses.window", conn: sqlite3.Connection, sale: Sale
) -> None:
    stdscr.clear()
    stdscr.addstr(0, 0, "Receipt")
    row = _render_line_items(stdscr, conn, sale.line_items, 2)
    row += 1
    stdscr.addstr(row, 0, f"Subtotal: {sale.subtotal:.2f}")
    row += 1
    stdscr.addstr(row, 0, f"GST total: {sale.gst_total:.2f}")
    row += 1
    stdscr.addstr(row, 0, f"Bill total: {sale.bill_total:.2f}")
    row += 1
    stdscr.addstr(row, 0, f"Payment status: {sale.payment_status}")
    row += 1
    stdscr.addstr(row, 0, "Press any key to return to the main menu.")
    stdscr.refresh()
    stdscr.getch()


def _handle_checkout(
    stdscr: "curses.window", conn: sqlite3.Connection, controller: SalesController
) -> bool:
    """Run the checkout flow. Returns `True` only when a sale was actually
    committed via `record_sale` — `False` for every other outcome (empty
    cart, Esc/cancel at any step, missing customer, or a domain-exception
    validation error). The caller (`_dispatch_route`/`run_sales_screen`)
    uses this signal to decide whether to exit the sale-entry screen back
    to the main menu — only a committed sale should trigger that exit
    (phase2-feature6-sales-customer-gaps, Action 2 / architect finding 1)."""
    if not controller.cart:
        _show_message(
            stdscr, 0, "Cart is empty — add a line item first. Press any key."
        )
        return False

    try:
        payment_status = _prompt_payment_status(stdscr)
    except EntryCancelled:
        return False

    customer_id: int | None = None
    if payment_status == "credit":
        try:
            customer = _select_customer(stdscr, conn)
        except EntryCancelled:
            return False
        if customer is None:
            _show_message(
                stdscr,
                0,
                "No customers exist yet — add one via Customers. Press any key.",
            )
            return False
        customer_id = customer.id

    cart = controller.cart_lines()
    try:
        sale = record_sale(conn, cart, payment_status, customer_id)
    except (SalesError, CustomerError) as exc:
        _show_message(stdscr, 0, f"Error: {exc} — press any key to continue.")
        return False

    controller.clear_cart()
    _render_receipt(stdscr, conn, sale)
    return True


def _dispatch_route(
    stdscr: "curses.window",
    conn: sqlite3.Connection,
    controller: SalesController,
    route: str,
) -> bool:
    """Handle one resolved sales route (all routes except back/quit).

    Returns `True` only when the route was a checkout that actually
    committed a sale — propagated from `_handle_checkout` so
    `run_sales_screen` knows whether to exit the screen. Every other route
    (add-item, or a non-committing checkout outcome) returns `False`."""
    if route == CHECKOUT_ROUTE:
        return _handle_checkout(stdscr, conn, controller)
    product = controller.selected_product()
    if product is None:
        return False
    if route == ADD_ROUTE:
        _handle_add_item(stdscr, conn, controller, product)
    return False


def run_sales_screen(stdscr: "curses.window", conn: sqlite3.Connection) -> None:
    """Drive the sale-entry curses loop until the user backs out, quits, or
    commits a sale. A committed checkout exits this loop (returning control
    to `main_menu.py`'s loop, which re-renders the main menu on its next
    iteration) — Esc/cancel, validation errors, empty-cart, and
    missing-customer outcomes all keep the user on this screen, exactly as
    before this fix (phase2-feature6-sales-customer-gaps, Action 2)."""
    controller = SalesController(list_products(conn))
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
        committed = _dispatch_route(stdscr, conn, controller, route)
        if committed:
            return


# --- Sales history (general list + per-customer drill-down) -----------------
# phase2-feature6-sales-customer-gaps, Action 3 / architect finding 2.


class SalesHistoryController:
    """Pure nav state over a list of committed `Sale`s — no curses I/O.
    Mirrors `CatalogController`'s list -> selected-index pattern, over
    `list_sales()`'s results rather than the product catalog."""

    def __init__(self, sales: list[Sale] | None = None) -> None:
        self._sales: list[Sale] = list(sales) if sales is not None else []
        self._index = 0

    @property
    def sales(self) -> list[Sale]:
        return self._sales

    @property
    def selected_index(self) -> int:
        return self._index

    def selected_sale(self) -> Sale | None:
        if not self._sales:
            return None
        return self._sales[self._index]

    def refresh(self, sales: list[Sale]) -> None:
        """Replace the sale list, clamping the selection into range."""
        self._sales = list(sales)
        if self._index >= len(self._sales):
            self._index = max(0, len(self._sales) - 1)

    def move_up(self) -> None:
        if self._sales:
            self._index = (self._index - 1) % len(self._sales)

    def move_down(self) -> None:
        if self._sales:
            self._index = (self._index + 1) % len(self._sales)

    def dispatch(self, key: int) -> str | None:
        """Resolve one raw key code into a routing outcome — view detail,
        back, quit, or `None` for plain navigation/unrecognized keys."""
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
            return HISTORY_DETAIL_ROUTE
        if action is NavAction.BACK:
            return BACK_ROUTE
        if action is NavAction.QUIT:
            return QUIT_ROUTE
        return None


def _customer_label(conn: sqlite3.Connection, customer_id: int | None) -> str:
    """ "walk-in" for a `None` `customer_id` (no customer attached to the
    sale), the customer's name otherwise."""
    if customer_id is None:
        return "walk-in"
    return get_customer(conn, customer_id).name


def _render_history_list(
    stdscr: "curses.window",
    conn: sqlite3.Connection,
    controller: SalesHistoryController,
) -> None:
    stdscr.clear()
    stdscr.addstr(0, 0, "Sales History")
    stdscr.addstr(1, 0, "Arrows/Tab move, Enter view detail, Esc/q back")
    row = 3
    for i, sale in enumerate(controller.sales):
        prefix = "> " if i == controller.selected_index else "  "
        customer_label = _customer_label(conn, sale.customer_id)
        stdscr.addstr(
            row,
            0,
            f"{prefix}{sale.created_at}  {customer_label}  "
            f"bill_total={sale.bill_total:.2f}  {sale.payment_status}",
        )
        row += 1
    if not controller.sales:
        stdscr.addstr(row, 0, "(no sales yet)")
    stdscr.refresh()


def _render_history_detail(
    stdscr: "curses.window", conn: sqlite3.Connection, sale: Sale
) -> None:
    stdscr.clear()
    stdscr.addstr(0, 0, "Sale Detail")
    customer_label = _customer_label(conn, sale.customer_id)
    stdscr.addstr(1, 0, f"Date: {sale.created_at}  Customer: {customer_label}")
    row = _render_line_items(stdscr, conn, sale.line_items, 3)
    row += 1
    stdscr.addstr(row, 0, f"Subtotal: {sale.subtotal:.2f}")
    row += 1
    stdscr.addstr(row, 0, f"GST total: {sale.gst_total:.2f}")
    row += 1
    stdscr.addstr(row, 0, f"Bill total: {sale.bill_total:.2f}")
    row += 1
    stdscr.addstr(row, 0, f"Payment status: {sale.payment_status}")
    row += 1
    stdscr.addstr(row, 0, "Press any key to go back.")
    stdscr.refresh()
    stdscr.getch()


def _run_history_detail_screen(
    stdscr: "curses.window", conn: sqlite3.Connection, sale_id: int
) -> None:
    """Fetch full line-item detail for one sale (`get_sale`, since
    `list_sales()`'s rows carry no `line_items`) and render it."""
    sale = get_sale(conn, sale_id)
    _render_history_detail(stdscr, conn, sale)


def _dispatch_history_route(
    stdscr: "curses.window",
    conn: sqlite3.Connection,
    controller: SalesHistoryController,
    route: str,
) -> None:
    """Handle one resolved sales-history route (all routes except
    back/quit)."""
    sale = controller.selected_sale()
    if sale is None:
        return
    if route == HISTORY_DETAIL_ROUTE:
        _run_history_detail_screen(stdscr, conn, sale.id)


def run_sales_history_screen(
    stdscr: "curses.window",
    conn: sqlite3.Connection,
    customer_id: int | None = None,
) -> None:
    """Drive the sales-history curses loop until the user backs out or
    quits. When `customer_id` is given, the list is filtered to that
    customer's sales only (the drill-down from
    `src/ui/customers.py::_run_detail_screen`'s `'v'`/`'V'` key);
    otherwise every sale is shown (the general main-menu entry point) —
    both capped/most-recent-first via `list_sales()`."""
    controller = SalesHistoryController(list_sales(conn, customer_id=customer_id))
    curses.curs_set(0)
    while True:
        controller.refresh(list_sales(conn, customer_id=customer_id))
        _render_history_list(stdscr, conn, controller)
        key = stdscr.getch()
        route = controller.dispatch(key)
        if route is None:
            continue
        if route in (BACK_ROUTE, QUIT_ROUTE):
            return
        _dispatch_history_route(stdscr, conn, controller, route)
