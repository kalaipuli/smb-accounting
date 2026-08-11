"""Curses customer/khata (credit) view, creation, balance, and payment
screen (P2-F2-T5).

Reachable from the main menu's "customers" route (`src/ui/main_menu.py`)
per `ARCH-F04`'s narrative flow: "Main menu -> Customers -> select customer
-> view outstanding balance / record payment against balance." Follows the
shared key-binding convention (arrows/Tab/Enter/Esc-q) from
`src/ui/keybindings.py`, mirroring `src/ui/inventory.py`'s
list -> detail -> single-field-prompt structure.

`CustomerController` holds pure navigation/dispatch state (the customer
list and the highlighted index) with no curses I/O, so it is
unit-testable without a real terminal — mirroring
`InventoryController`/`CatalogController`. `run_customers_screen` is the
thin curses render loop that drives the controller and calls
`src.services.customers` for balance reads, payment recording, and
customer creation.

Customer creation (multi-field name/phone/address entry, `'n'` shortcut
from the list screen, mirroring `src/ui/catalog.py`'s `'n'` new-product
convention) uses `FormController`/`run_form`
(`src/ui/keybindings.py`, Feature 4/`P2-F4-T2`) for field-to-field
navigation, per
`docs/registry/phase2/feature2-customer-credit-khata/tasks.md#P2-F2-T5`'s
Cross-Cutting Consumption Obligation (`framework/project/project-plan.md`
DLVR-2-03) — not the linear `read_line`-per-field anti-pattern
`src/ui/catalog.py`'s own forms still use (retrofitting Catalog is
explicitly out of `ADR-007`'s scope). The payment-amount prompt remains a
*single*-field prompt (not a multi-field form) and correctly continues to
use `read_line` directly — it never fell under this obligation.

No raw `customer.name`/`.phone`/`.address` field reaches a `logger.*()`
call anywhere in this module (`ADR-006` Decision (d), `ARCH-D01`, SD-004,
`.claude/hooks/stop.sh` gate 10) — this module does not log at all,
matching `src/services/customers.py`'s existing no-logging convention;
on-screen display of a selected customer's own PII (the reason the
feature exists) is not a logging call and is not in scope for that gate.
"""

import curses
import sqlite3

from src.exceptions import CustomerError
from src.services.customers import (
    CreditTransaction,
    Customer,
    create_customer,
    get_outstanding_balance,
    list_credit_transactions,
    list_customers,
    record_payment,
    update_customer,
)
from src.ui.keybindings import (
    EntryCancelled,
    FormField,
    NavAction,
    read_line,
    resolve_action,
    run_form,
)
from src.ui.sales import run_sales_history_screen

BACK_ROUTE = "back"
QUIT_ROUTE = "quit"
DETAIL_ROUTE = "detail"
NEW_CUSTOMER_ROUTE = "new_customer"
EDIT_CUSTOMER_ROUTE = "edit_customer"


class CustomerController:
    """Pure navigation state over the customer list — no curses I/O."""

    def __init__(self, customers: list[Customer] | None = None) -> None:
        self._customers: list[Customer] = (
            list(customers) if customers is not None else []
        )
        self._index = 0

    @property
    def customers(self) -> list[Customer]:
        return self._customers

    @property
    def selected_index(self) -> int:
        return self._index

    def selected_customer(self) -> Customer | None:
        if not self._customers:
            return None
        return self._customers[self._index]

    def refresh(self, customers: list[Customer]) -> None:
        """Replace the customer list, clamping the selection into range."""
        self._customers = list(customers)
        if self._index >= len(self._customers):
            self._index = max(0, len(self._customers) - 1)

    def move_up(self) -> None:
        if self._customers:
            self._index = (self._index - 1) % len(self._customers)

    def move_down(self) -> None:
        if self._customers:
            self._index = (self._index + 1) % len(self._customers)

    def dispatch(self, key: int) -> str | None:
        """Resolve one raw key code into a routing outcome.

        Returns a route string when the key routes away from plain
        navigation (view detail, new-customer shortcut, back, quit);
        returns None when the key only moved the highlighted item (or was
        unrecognized).
        """
        if key in (ord("n"), ord("N")):
            return NEW_CUSTOMER_ROUTE
        if key in (ord("e"), ord("E")):
            return EDIT_CUSTOMER_ROUTE

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
            return DETAIL_ROUTE
        if action is NavAction.BACK:
            return BACK_ROUTE
        if action is NavAction.QUIT:
            return QUIT_ROUTE
        return None


def _render(stdscr: "curses.window", controller: CustomerController) -> None:
    stdscr.clear()
    stdscr.addstr(0, 0, "Customers")
    stdscr.addstr(
        1,
        0,
        "Arrows/Tab move, Enter view balance/pay, n new customer, e edit, Esc/q back",
    )
    for row, customer in enumerate(controller.customers, start=3):
        prefix = "> " if row - 3 == controller.selected_index else "  "
        stdscr.addstr(row, 0, f"{prefix}{customer.name}  {customer.phone}")
    if not controller.customers:
        stdscr.addstr(3, 0, "(no customers yet — press 'n' to add one)")
    stdscr.refresh()


def _render_detail(stdscr: "curses.window", customer: Customer, balance: float) -> None:
    stdscr.clear()
    stdscr.addstr(0, 0, f"Customer: {customer.name}")
    stdscr.addstr(1, 0, f"Phone: {customer.phone}")
    if customer.address:
        stdscr.addstr(2, 0, f"Address: {customer.address}")
    stdscr.addstr(4, 0, f"Outstanding balance: {balance:.2f}")
    stdscr.addstr(
        6,
        0,
        "'p' record payment, 'v' sales history, 't' transaction history, "
        "any other key back",
    )
    stdscr.refresh()


def _render_transaction_history(
    stdscr: "curses.window", transactions: list[CreditTransaction]
) -> None:
    """Render one line per `CreditTransaction` (date, type, amount) — the
    transaction-level khata history `get_outstanding_balance()`'s aggregate
    does not expose (phase2-feature3-cross-cutting-closure, Action 8)."""
    stdscr.clear()
    stdscr.addstr(0, 0, "Credit Transaction History")
    stdscr.addstr(1, 0, "Press any key to go back.")
    row = 3
    for txn in transactions:
        stdscr.addstr(
            row, 0, f"{txn.created_at}  {txn.transaction_type}  {txn.amount:.2f}"
        )
        row += 1
    if not transactions:
        stdscr.addstr(row, 0, "(no transactions yet)")
    stdscr.refresh()


def _prompt_payment_amount(stdscr: "curses.window", row: int) -> float | None:
    """Collect a payment amount from the user. Returns None on bad input.
    Raises `EntryCancelled` (propagated from `read_line`) if the user
    presses Esc — callers MUST treat that as a distinct path from the
    `None`/bad-input return.
    """
    label = "Payment amount: "
    stdscr.addstr(row, 0, label)
    raw = read_line(stdscr, row, len(label))
    try:
        return float(raw.strip())
    except ValueError:
        return None


def _show_message(stdscr: "curses.window", row: int, message: str) -> None:
    stdscr.addstr(row, 0, message)
    stdscr.refresh()
    stdscr.getch()


def _prompt_customer_fields(
    stdscr: "curses.window", existing: Customer | None = None
) -> dict[str, str]:
    """Collect name/phone/address from the user via `FormController`'s
    `run_form` (`src/ui/keybindings.py`, `ADR-007`) — field-to-field
    navigation (Up/Down/Tab/Shift-Tab, edit-in-place, Enter
    advances-then-submits-on-last-field) instead of the linear
    cancel-on-Esc-only `read_line`-per-field sequence.

    When `existing` is given, each field is pre-filled with its current
    value via `FormField.default` (edit-in-place; Enter keeps it
    unchanged) — mirrors `catalog.py::_prompt_product_fields(stdscr,
    existing=product)`'s pre-fill pattern.

    `run_form` renders its fields starting at row 0 (`ADR-007` Decision
    (c), fixed one-row-per-field layout) — the title/instructions line is
    written to a row below the fixed 3-field block so the two do not
    collide.

    Raises `EntryCancelled` (propagated from `run_form`) if the user
    presses Esc on any field — callers MUST treat that as a distinct path
    from a submitted-but-invalid value.
    """
    stdscr.clear()
    title = "Edit Customer" if existing else "New Customer"
    stdscr.addstr(4, 0, f"{title} — arrows/Tab move, Enter submits, Esc cancels")
    fields = [
        FormField(name="name", label="Name", default=existing.name if existing else ""),
        FormField(
            name="phone", label="Phone", default=existing.phone if existing else ""
        ),
        FormField(
            name="address",
            label="Address (optional)",
            default=(existing.address or "") if existing else "",
        ),
    ]
    return run_form(stdscr, fields)


def _handle_new_customer(stdscr: "curses.window", conn: sqlite3.Connection) -> None:
    try:
        values = _prompt_customer_fields(stdscr)
    except EntryCancelled:
        return
    name = values["name"]
    phone = values["phone"]
    address = values["address"].strip() or None
    try:
        create_customer(conn, name=name, phone=phone, address=address)
    except CustomerError as exc:
        _show_message(stdscr, 6, f"Error: {exc} — press any key to continue.")


def _handle_edit_customer(
    stdscr: "curses.window", conn: sqlite3.Connection, customer: Customer
) -> None:
    try:
        values = _prompt_customer_fields(stdscr, existing=customer)
    except EntryCancelled:
        return
    name = values["name"]
    phone = values["phone"]
    address = values["address"].strip() or None
    try:
        update_customer(conn, customer.id, name=name, phone=phone, address=address)
    except CustomerError as exc:
        _show_message(stdscr, 6, f"Error: {exc} — press any key to continue.")


def _handle_pay(
    stdscr: "curses.window", conn: sqlite3.Connection, customer: Customer
) -> None:
    try:
        amount = _prompt_payment_amount(stdscr, 8)
    except EntryCancelled:
        return
    if amount is None:
        _show_message(stdscr, 10, "Invalid input — press any key to continue.")
        return
    try:
        record_payment(conn, customer.id, amount)
    except CustomerError as exc:
        _show_message(stdscr, 10, f"Error: {exc} — press any key to continue.")


def _run_transaction_history_screen(
    stdscr: "curses.window", conn: sqlite3.Connection, customer: Customer
) -> None:
    """Fetch and render this customer's individual credit-sale/payment
    ledger rows (phase2-feature3-cross-cutting-closure, Action 8) — the
    transaction-level detail that only the aggregate outstanding balance
    was previously shown for."""
    transactions = list_credit_transactions(conn, customer.id)
    _render_transaction_history(stdscr, transactions)
    stdscr.getch()


def _run_detail_screen(
    stdscr: "curses.window", conn: sqlite3.Connection, customer: Customer
) -> None:
    while True:
        balance = get_outstanding_balance(conn, customer.id)
        _render_detail(stdscr, customer, balance)
        key = stdscr.getch()
        if key in (ord("p"), ord("P")):
            _handle_pay(stdscr, conn, customer)
            continue
        if key in (ord("v"), ord("V")):
            # phase2-feature6-sales-customer-gaps, Action 3: per-customer
            # sales/invoice history drill-down (architect finding 2),
            # filtered to this customer via `list_sales(customer_id=...)`.
            run_sales_history_screen(stdscr, conn, customer_id=customer.id)
            continue
        if key in (ord("t"), ord("T")):
            # phase2-feature3-cross-cutting-closure, Action 8: individual
            # credit_transactions ledger rows (date, type, amount) — not
            # exposed by the aggregate `get_outstanding_balance()` display
            # above.
            _run_transaction_history_screen(stdscr, conn, customer)
            continue
        return


def _dispatch_route(
    stdscr: "curses.window",
    conn: sqlite3.Connection,
    controller: CustomerController,
    route: str,
) -> None:
    """Handle one resolved customer route (all routes except back/quit)."""
    if route == NEW_CUSTOMER_ROUTE:
        _handle_new_customer(stdscr, conn)
        return
    customer = controller.selected_customer()
    if customer is None:
        return
    if route == EDIT_CUSTOMER_ROUTE:
        _handle_edit_customer(stdscr, conn, customer)
    elif route == DETAIL_ROUTE:
        _run_detail_screen(stdscr, conn, customer)


def run_customers_screen(stdscr: "curses.window", conn: sqlite3.Connection) -> None:
    """Drive the customers curses loop until the user backs out or quits."""
    controller = CustomerController(list_customers(conn))
    curses.curs_set(0)
    while True:
        controller.refresh(list_customers(conn))
        _render(stdscr, controller)
        key = stdscr.getch()
        route = controller.dispatch(key)
        if route is None:
            continue
        if route in (BACK_ROUTE, QUIT_ROUTE):
            return
        _dispatch_route(stdscr, conn, controller, route)
