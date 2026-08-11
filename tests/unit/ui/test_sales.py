"""Unit tests for the sale-entry navigation controller and routing
(P2-F1-T5).

Covers key-binding dispatch (arrows/Tab/Enter/Esc/q, reused from
`src/ui/keybindings.py`) against `SalesController`, which holds no curses
I/O — mirroring `test_catalog.py`/`test_customers.py`'s coverage of their
own controllers. Also covers line-item entry via `FormController`
(`_prompt_line_item_fields`/`_handle_add_item`), discount application,
checkout for both a paid and a credit sale (with customer lookup), the
insufficient-stock error path, and Esc-cancel behaviour at every
cancel-capable step.
"""

import curses
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from src.db.schema import connect, init_schema
from src.services.catalog import Product, create_product
from src.services.customers import create_customer, get_outstanding_balance
from src.services.discounts import create_discount
from src.services.inventory import set_stock
from src.services.sales import CartLine, Sale, get_sale, record_sale
from src.ui.keybindings import EntryCancelled
from src.ui.sales import (
    ADD_ROUTE,
    BACK_ROUTE,
    CHECKOUT_ROUTE,
    HISTORY_DETAIL_ROUTE,
    QUIT_ROUTE,
    CartItemDisplay,
    SalesController,
    SalesHistoryController,
    _customer_label,
    _dispatch_history_route,
    _dispatch_route,
    _handle_add_item,
    _handle_checkout,
    _prompt_line_item_fields,
    _prompt_payment_status,
    _render,
    _render_history_detail,
    _render_history_list,
    _render_receipt,
    _run_history_detail_screen,
    _select_customer,
    _select_discount,
    _show_message,
    run_sales_history_screen,
    run_sales_screen,
)

RICE = Product(id=1, name="Rice 1kg", price=100.0, gst_rate=5.0)
SUGAR = Product(id=2, name="Sugar 1kg", price=45.0, gst_rate=5.0)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Real (throwaway file-backed) SQLite connection — mirrors
    `tests/unit/ui/test_customers.py`'s fixture. Handler functions call
    real `src.services.*` functions, so a real schema-backed connection
    exercises the same write paths those services' own tests already
    cover, without re-mocking the service layer here."""
    connection = connect(tmp_path / "data.db")
    init_schema(connection)
    yield connection
    connection.close()


class _FakeWindow:
    """Minimal curses.window stand-in — records `addstr` text, no raw I/O."""

    def __init__(self, keys: list[int] | None = None) -> None:
        self.lines: list[str] = []
        self._keys: list[int] = list(keys) if keys is not None else []

    def clear(self) -> None:
        self.lines = []

    def addstr(self, row: int, col: int, text: str) -> None:
        self.lines.append(text)

    def move(self, row: int, col: int) -> None:
        """No-op — `run_form` (`FormController`) places the cursor here;
        this fake records no cursor state, only rendered text."""

    def refresh(self) -> None:
        pass

    def getch(self) -> int:
        return self._keys.pop(0)


def _keys(text: str) -> list[int]:
    """Turn a string into the raw keycode sequence `run_form` consumes,
    one `ord()` per character — mirrors `test_customers.py`'s own `_keys`
    helper for `FormController`/`run_form` key-queue tests."""
    return [ord(c) for c in text]


# --- construction / empty state --------------------------------------------------


def test_controller_when_constructed_with_no_products_selected_product_is_none() -> (
    None
):
    controller = SalesController()

    assert controller.selected_index == 0
    assert controller.selected_product() is None
    assert controller.cart == []


def test_controller_when_constructed_with_products_defaults_to_first_selected() -> None:
    controller = SalesController([RICE, SUGAR])

    assert controller.selected_product() == RICE


# --- refresh -----------------------------------------------------------------------


def test_refresh_when_list_shrinks_below_selection_clamps_index() -> None:
    controller = SalesController([RICE, SUGAR])
    controller.move_down()

    controller.refresh([RICE])

    assert controller.selected_index == 0
    assert controller.selected_product() == RICE


# --- arrow/tab navigation dispatch ---------------------------------------------------


def test_dispatch_when_down_arrow_moves_selection_forward() -> None:
    controller = SalesController([RICE, SUGAR])

    route = controller.dispatch(curses.KEY_DOWN)

    assert route is None
    assert controller.selected_index == 1


def test_dispatch_when_down_arrow_wraps_past_last_option() -> None:
    controller = SalesController([RICE, SUGAR])
    controller.dispatch(curses.KEY_DOWN)

    route = controller.dispatch(curses.KEY_DOWN)

    assert route is None
    assert controller.selected_index == 0


def test_dispatch_when_tab_moves_selection_forward_like_down_arrow() -> None:
    controller = SalesController([RICE, SUGAR])

    controller.dispatch(ord("\t"))

    assert controller.selected_index == 1


# --- add/checkout/back/quit dispatch --------------------------------------------------


def test_dispatch_when_enter_returns_add_route() -> None:
    controller = SalesController([RICE])

    assert controller.dispatch(ord("\n")) == ADD_ROUTE


def test_dispatch_when_a_returns_add_route() -> None:
    controller = SalesController([RICE])

    assert controller.dispatch(ord("a")) == ADD_ROUTE
    assert controller.dispatch(ord("A")) == ADD_ROUTE


def test_dispatch_when_c_returns_checkout_route() -> None:
    controller = SalesController([RICE])

    assert controller.dispatch(ord("c")) == CHECKOUT_ROUTE
    assert controller.dispatch(ord("C")) == CHECKOUT_ROUTE


def test_dispatch_when_esc_returns_back_route() -> None:
    controller = SalesController([RICE])

    assert controller.dispatch(27) == BACK_ROUTE


def test_dispatch_when_q_returns_quit_route() -> None:
    controller = SalesController([RICE])

    assert controller.dispatch(ord("q")) == QUIT_ROUTE


def test_dispatch_when_unrecognized_key_returns_none_and_does_not_move_selection() -> (
    None
):
    controller = SalesController([RICE, SUGAR])

    route = controller.dispatch(ord("z"))

    assert route is None
    assert controller.selected_index == 0


# --- cart_lines conversion -----------------------------------------------------------


def test_cart_lines_converts_display_cart_to_service_cart_lines() -> None:
    controller = SalesController([RICE])
    controller.add_item(CartItemDisplay(product=RICE, quantity=2, discount=None))

    lines = controller.cart_lines()

    assert len(lines) == 1
    assert lines[0].product_id == RICE.id
    assert lines[0].quantity == 2
    assert lines[0].discount is None


def test_clear_cart_empties_the_cart() -> None:
    controller = SalesController([RICE])
    controller.add_item(CartItemDisplay(product=RICE, quantity=1, discount=None))

    controller.clear_cart()

    assert controller.cart == []


# --- _render (pure render, no raw curses input) ---------------------------------------


def test_render_when_products_present_lists_each_with_selected_prefix() -> None:
    stdscr = _FakeWindow()
    controller = SalesController([RICE, SUGAR])
    controller.move_down()

    _render(cast("curses.window", stdscr), controller)

    assert any(line.startswith("  Rice 1kg") for line in stdscr.lines)
    assert any(line.startswith("> Sugar 1kg") for line in stdscr.lines)


def test_render_when_no_products_shows_placeholder_message() -> None:
    stdscr = _FakeWindow()
    controller = SalesController()

    _render(cast("curses.window", stdscr), controller)

    assert any("no products yet" in line for line in stdscr.lines)


def test_render_when_cart_empty_shows_empty_placeholder() -> None:
    stdscr = _FakeWindow()
    controller = SalesController([RICE])

    _render(cast("curses.window", stdscr), controller)

    assert any("(empty)" in line for line in stdscr.lines)


def test_render_when_cart_has_items_lists_them() -> None:
    stdscr = _FakeWindow()
    controller = SalesController([RICE])
    controller.add_item(CartItemDisplay(product=RICE, quantity=3, discount=None))

    _render(cast("curses.window", stdscr), controller)

    assert any("Rice 1kg x3" in line for line in stdscr.lines)


# --- _prompt_line_item_fields / _handle_add_item (line-item entry, FormController) ----


def test_prompt_line_item_fields_when_typed_returns_quantity() -> None:
    keys = [*_keys("3"), ord("\n")]
    stdscr = _FakeWindow(keys=keys)

    result = _prompt_line_item_fields(cast("curses.window", stdscr), RICE)

    assert result == {"quantity": "3"}


def test_prompt_line_item_fields_when_esc_pressed_raises_entry_cancelled() -> None:
    stdscr = _FakeWindow(keys=[*_keys("3"), 27])

    with pytest.raises(EntryCancelled):
        _prompt_line_item_fields(cast("curses.window", stdscr), RICE)


def test_handle_add_item_when_quantity_only_adds_line_without_discount(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    set_stock(conn, product.id, 10)
    controller = SalesController([product])
    # "2" + Enter submits the single-field quantity form; Enter again
    # selects the synthetic "(no discount)" entry (index 0) in the
    # discount list-selection step.
    keys = [*_keys("2"), ord("\n"), ord("\n")]
    stdscr = _FakeWindow(keys=keys)

    _handle_add_item(cast("curses.window", stdscr), conn, controller, product)

    assert len(controller.cart) == 1
    assert controller.cart[0].quantity == 2
    assert controller.cart[0].discount is None


def test_handle_add_item_when_discount_selected_applies_discount(
    conn: sqlite3.Connection,
) -> None:
    """Discount list-selection (phase2-feature5-sale-entry-fixes, action 3)
    — navigating past the synthetic "(no discount)" entry to the product's
    one discount and pressing Enter attaches that `Discount` to the cart
    line."""
    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    set_stock(conn, product.id, 10)
    discount = create_discount(conn, product.id, discount_type="flat", value=10.0)
    controller = SalesController([product])
    keys = [*_keys("2"), ord("\n"), ord("\t"), ord("\n")]
    stdscr = _FakeWindow(keys=keys)

    _handle_add_item(cast("curses.window", stdscr), conn, controller, product)

    assert len(controller.cart) == 1
    assert controller.cart[0].discount == discount


def test_handle_add_item_when_quantity_invalid_shows_message_and_does_not_add(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    controller = SalesController([product])
    keys = [*_keys("abc"), ord("\n"), ord("x")]
    stdscr = _FakeWindow(keys=keys)

    _handle_add_item(cast("curses.window", stdscr), conn, controller, product)

    assert controller.cart == []
    assert any("Invalid quantity" in line for line in stdscr.lines)


def test_handle_add_item_when_esc_cancels_no_mutation(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    controller = SalesController([product])
    stdscr = _FakeWindow(keys=[*_keys("2"), 27])

    _handle_add_item(cast("curses.window", stdscr), conn, controller, product)

    assert controller.cart == []


def test_handle_add_item_when_esc_during_discount_select_cancels_whole_flow(
    conn: sqlite3.Connection,
) -> None:
    """Esc on the discount list-selection step (after quantity has already
    been submitted) still cancels the whole add-item flow — no cart
    mutation."""
    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    set_stock(conn, product.id, 10)
    controller = SalesController([product])
    keys = [*_keys("2"), ord("\n"), 27]
    stdscr = _FakeWindow(keys=keys)

    _handle_add_item(cast("curses.window", stdscr), conn, controller, product)

    assert controller.cart == []


# --- _handle_add_item: stock pre-check (phase2-feature5-sale-entry-fixes, action 2) ---


def test_handle_add_item_when_quantity_within_stock_is_accepted(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    set_stock(conn, product.id, 5)
    controller = SalesController([product])
    keys = [*_keys("5"), ord("\n"), ord("\n")]
    stdscr = _FakeWindow(keys=keys)

    _handle_add_item(cast("curses.window", stdscr), conn, controller, product)

    assert len(controller.cart) == 1
    assert controller.cart[0].quantity == 5


def test_handle_add_item_when_quantity_exceeds_stock_is_rejected(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    set_stock(conn, product.id, 3)
    controller = SalesController([product])
    keys = [*_keys("5"), ord("\n"), ord("x")]
    stdscr = _FakeWindow(keys=keys)

    _handle_add_item(cast("curses.window", stdscr), conn, controller, product)

    assert controller.cart == []
    assert any("Insufficient stock" in line for line in stdscr.lines)


def test_handle_add_item_when_summed_with_existing_cart_line_exceeds_stock_is_rejected(
    conn: sqlite3.Connection,
) -> None:
    """Quantity 3 is individually within stock (5), but the cart already
    holds 3 of the same product, so 3 + 3 = 6 exceeds the available 5 —
    rejected."""
    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    set_stock(conn, product.id, 5)
    controller = SalesController([product])
    controller.add_item(CartItemDisplay(product=product, quantity=3, discount=None))
    keys = [*_keys("3"), ord("\n"), ord("x")]
    stdscr = _FakeWindow(keys=keys)

    _handle_add_item(cast("curses.window", stdscr), conn, controller, product)

    assert len(controller.cart) == 1
    assert any("Insufficient stock" in line for line in stdscr.lines)


# --- _select_discount / _render_discount_select (mirrors _select_customer) -----------


def test_select_discount_when_no_discounts_lists_only_no_discount_option(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    stdscr = _FakeWindow(keys=[ord("\n")])

    result = _select_discount(cast("curses.window", stdscr), conn, product.id)

    assert result is None
    assert any("(no discount)" in line for line in stdscr.lines)
    assert not any("percentage" in line or "flat" in line for line in stdscr.lines)


def test_select_discount_when_discounts_exist_is_navigable_and_returns_selected(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    discount = create_discount(conn, product.id, discount_type="flat", value=10.0)
    stdscr = _FakeWindow(keys=[ord("\t"), ord("\n")])

    result = _select_discount(cast("curses.window", stdscr), conn, product.id)

    assert result == discount


def test_select_discount_when_esc_pressed_raises_entry_cancelled(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    create_discount(conn, product.id, discount_type="flat", value=10.0)
    stdscr = _FakeWindow(keys=[27])

    with pytest.raises(EntryCancelled):
        _select_discount(cast("curses.window", stdscr), conn, product.id)


# --- _prompt_payment_status ----------------------------------------------------------


def test_prompt_payment_status_when_p_pressed_returns_paid() -> None:
    stdscr = _FakeWindow(keys=[ord("p")])

    assert _prompt_payment_status(cast("curses.window", stdscr)) == "paid"


def test_prompt_payment_status_when_c_pressed_returns_credit() -> None:
    stdscr = _FakeWindow(keys=[ord("c")])

    assert _prompt_payment_status(cast("curses.window", stdscr)) == "credit"


def test_prompt_payment_status_when_esc_pressed_raises_entry_cancelled() -> None:
    stdscr = _FakeWindow(keys=[27])

    with pytest.raises(EntryCancelled):
        _prompt_payment_status(cast("curses.window", stdscr))


# --- _select_customer -----------------------------------------------------------------


def test_select_customer_when_no_customers_returns_none(
    conn: sqlite3.Connection,
) -> None:
    result = _select_customer(cast("curses.window", _FakeWindow()), conn)

    assert result is None


def test_select_customer_when_enter_pressed_returns_selected_customer(
    conn: sqlite3.Connection,
) -> None:
    customer = create_customer(conn, name="Raj Kumar", phone="9876543210")
    stdscr = _FakeWindow(keys=[ord("\n")])

    result = _select_customer(cast("curses.window", stdscr), conn)

    assert result == customer


def test_select_customer_when_esc_pressed_raises_entry_cancelled(
    conn: sqlite3.Connection,
) -> None:
    create_customer(conn, name="Raj Kumar", phone="9876543210")
    stdscr = _FakeWindow(keys=[27])

    with pytest.raises(EntryCancelled):
        _select_customer(cast("curses.window", stdscr), conn)


# --- _handle_checkout: paid sale end-to-end -------------------------------------------


def test_handle_checkout_paid_sale_end_to_end_commits_and_shows_receipt(
    conn: sqlite3.Connection,
) -> None:
    from src.services.inventory import set_stock

    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    set_stock(conn, product.id, 10)
    controller = SalesController([product])
    controller.add_item(CartItemDisplay(product=product, quantity=2, discount=None))
    stdscr = _FakeWindow(keys=[ord("p"), ord("x")])

    _handle_checkout(cast("curses.window", stdscr), conn, controller)

    assert controller.cart == []
    assert any("Bill total" in line for line in stdscr.lines)
    assert any("Payment status: paid" in line for line in stdscr.lines)


def test_handle_checkout_when_cart_empty_shows_message_and_does_not_prompt(
    conn: sqlite3.Connection,
) -> None:
    controller = SalesController()
    stdscr = _FakeWindow(keys=[ord("x")])

    _handle_checkout(cast("curses.window", stdscr), conn, controller)

    assert any("Cart is empty" in line for line in stdscr.lines)


def test_handle_checkout_when_esc_on_payment_status_cancels_no_mutation(
    conn: sqlite3.Connection,
) -> None:
    from src.services.inventory import set_stock

    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    set_stock(conn, product.id, 10)
    controller = SalesController([product])
    controller.add_item(CartItemDisplay(product=product, quantity=1, discount=None))
    stdscr = _FakeWindow(keys=[27])

    _handle_checkout(cast("curses.window", stdscr), conn, controller)

    assert len(controller.cart) == 1


# --- _handle_checkout: credit sale end-to-end, with customer lookup ------------------


def test_handle_checkout_credit_sale_end_to_end_looks_up_customer_and_commits(
    conn: sqlite3.Connection,
) -> None:
    from src.services.inventory import set_stock

    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    set_stock(conn, product.id, 10)
    customer = create_customer(conn, name="Raj Kumar", phone="9876543210")
    controller = SalesController([product])
    controller.add_item(CartItemDisplay(product=product, quantity=2, discount=None))
    stdscr = _FakeWindow(keys=[ord("c"), ord("\n"), ord("x")])

    _handle_checkout(cast("curses.window", stdscr), conn, controller)

    assert controller.cart == []
    assert any("Payment status: credit" in line for line in stdscr.lines)
    assert get_outstanding_balance(conn, customer.id) > 0


def test_handle_checkout_credit_sale_when_no_customers_shows_message(
    conn: sqlite3.Connection,
) -> None:
    from src.services.inventory import set_stock

    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    set_stock(conn, product.id, 10)
    controller = SalesController([product])
    controller.add_item(CartItemDisplay(product=product, quantity=2, discount=None))
    stdscr = _FakeWindow(keys=[ord("c"), ord("x")])

    _handle_checkout(cast("curses.window", stdscr), conn, controller)

    assert any("No customers exist yet" in line for line in stdscr.lines)
    assert len(controller.cart) == 1


# --- _handle_checkout: insufficient-stock error path (error-path coverage) -----------


def test_handle_checkout_when_insufficient_stock_shows_error_and_does_not_crash(
    conn: sqlite3.Connection,
) -> None:
    """Error-path coverage: no stock set (defaults to 0) -> `record_sale`
    raises `SaleTransactionError` (wrapping `InsufficientStockError` from
    `src/services/inventory.py`'s negative-stock guard) -> surfaced as an
    on-screen message, no crash, cart is not cleared on failure."""
    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    controller = SalesController([product])
    controller.add_item(CartItemDisplay(product=product, quantity=5, discount=None))
    stdscr = _FakeWindow(keys=[ord("p"), ord("x")])

    _handle_checkout(cast("curses.window", stdscr), conn, controller)

    assert any(line.startswith("Error:") for line in stdscr.lines)
    assert len(controller.cart) == 1


# --- _handle_checkout / run_sales_screen: return-to-main-menu signal (Action 2) ------


def test_handle_checkout_paid_sale_end_to_end_returns_true(
    conn: sqlite3.Connection,
) -> None:
    """A committed sale signals success back to the caller — the caller
    uses this to decide whether to exit the sale-entry screen."""
    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    set_stock(conn, product.id, 10)
    controller = SalesController([product])
    controller.add_item(CartItemDisplay(product=product, quantity=2, discount=None))
    stdscr = _FakeWindow(keys=[ord("p"), ord("x")])

    result = _handle_checkout(cast("curses.window", stdscr), conn, controller)

    assert result is True


def test_handle_checkout_when_cart_empty_returns_false(
    conn: sqlite3.Connection,
) -> None:
    controller = SalesController()
    stdscr = _FakeWindow(keys=[ord("x")])

    result = _handle_checkout(cast("curses.window", stdscr), conn, controller)

    assert result is False


def test_handle_checkout_when_esc_on_payment_status_returns_false(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    set_stock(conn, product.id, 10)
    controller = SalesController([product])
    controller.add_item(CartItemDisplay(product=product, quantity=1, discount=None))
    stdscr = _FakeWindow(keys=[27])

    result = _handle_checkout(cast("curses.window", stdscr), conn, controller)

    assert result is False


def test_handle_checkout_credit_sale_when_no_customers_returns_false(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    set_stock(conn, product.id, 10)
    controller = SalesController([product])
    controller.add_item(CartItemDisplay(product=product, quantity=2, discount=None))
    stdscr = _FakeWindow(keys=[ord("c"), ord("x")])

    result = _handle_checkout(cast("curses.window", stdscr), conn, controller)

    assert result is False


def test_handle_checkout_when_insufficient_stock_returns_false(
    conn: sqlite3.Connection,
) -> None:
    """Error-path coverage of the return signal: `record_sale` raising
    `SaleTransactionError` must not be mistaken for a committed sale."""
    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    controller = SalesController([product])
    controller.add_item(CartItemDisplay(product=product, quantity=5, discount=None))
    stdscr = _FakeWindow(keys=[ord("p"), ord("x")])

    result = _handle_checkout(cast("curses.window", stdscr), conn, controller)

    assert result is False


def test_run_sales_screen_when_checkout_commits_a_sale_exits_the_screen(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A committed checkout must exit `run_sales_screen`'s loop so
    `main_menu.py`'s loop regains control and re-renders the main menu —
    the core fix for the return-to-main-menu gap."""
    monkeypatch.setattr(curses, "curs_set", lambda visibility: None)
    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    set_stock(conn, product.id, 10)
    # Enter (SELECT) adds the highlighted product with quantity "2", Enter
    # submits quantity, Enter selects "(no discount)", "c" checks out, "p"
    # pays in full, "x" dismisses the receipt — all keys are consumed with
    # none left over, proving the loop exited right after the receipt.
    keys = [
        ord("\n"),
        *_keys("2"),
        ord("\n"),
        ord("\n"),
        ord("c"),
        ord("p"),
        ord("x"),
    ]
    stdscr = _FakeWindow(keys=keys)

    run_sales_screen(cast("curses.window", stdscr), conn)

    assert stdscr._keys == []
    assert any("Bill total" in line for line in stdscr.lines)


def test_run_sales_screen_when_checkout_is_cancelled_stays_on_screen(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Esc on the payment-status prompt (cancelled checkout) must not exit
    the screen — the loop keeps re-rendering the sale-entry screen until a
    subsequent explicit Esc/q is pressed."""
    monkeypatch.setattr(curses, "curs_set", lambda visibility: None)
    create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    # "c" dispatches CHECKOUT_ROUTE with an empty cart, which shows the
    # "Cart is empty" message and returns without prompting further — the
    # screen does not exit, so a subsequent Esc is needed to back out.
    keys = [ord("c"), ord("x"), 27]
    stdscr = _FakeWindow(keys=keys)

    run_sales_screen(cast("curses.window", stdscr), conn)

    # All keys were consumed (proving the loop re-rendered the sale-entry
    # screen after the cancelled/non-committing checkout instead of exiting
    # early) and the loop's last render is the sale-entry screen itself —
    # not a receipt — before the final Esc backs out.
    assert stdscr._keys == []
    assert any(line == "Record a Sale" for line in stdscr.lines)


def test_run_sales_screen_when_checkout_has_validation_error_stays_on_screen(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checkout that raises a domain validation error (insufficient
    stock — no stock set, defaults to 0) must not exit the screen; the
    user stays on the sale-entry screen and can retry or back out with a
    subsequent explicit Esc/q."""
    monkeypatch.setattr(curses, "curs_set", lambda visibility: None)
    create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    # Add a line item with no stock set (defaults to 0 -> insufficient
    # stock at commit time), checkout as paid -> record_sale raises ->
    # error shown, screen does not exit -> subsequent Esc backs out.
    keys = [
        ord("\n"),
        *_keys("1"),
        ord("\n"),
        ord("\n"),
        ord("c"),
        ord("p"),
        ord("x"),
        27,
    ]
    stdscr = _FakeWindow(keys=keys)

    run_sales_screen(cast("curses.window", stdscr), conn)

    # All keys were consumed (the loop re-rendered the sale-entry screen
    # after the validation-error checkout instead of exiting) and the
    # final render before the last Esc is the sale-entry screen itself.
    assert stdscr._keys == []
    assert any(line == "Record a Sale" for line in stdscr.lines)


# --- _dispatch_route / run_sales_screen --------------------------------------------


def test_dispatch_route_when_no_product_selected_add_route_is_a_no_op(
    conn: sqlite3.Connection,
) -> None:
    controller = SalesController()
    stdscr = _FakeWindow()

    _dispatch_route(cast("curses.window", stdscr), conn, controller, ADD_ROUTE)

    assert stdscr.lines == []


def test_dispatch_route_checkout_route_invoked_even_with_no_products(
    conn: sqlite3.Connection,
) -> None:
    controller = SalesController()
    stdscr = _FakeWindow(keys=[ord("x")])

    _dispatch_route(cast("curses.window", stdscr), conn, controller, CHECKOUT_ROUTE)

    assert any("Cart is empty" in line for line in stdscr.lines)


def test_run_sales_screen_when_back_key_returns_immediately(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(curses, "curs_set", lambda visibility: None)
    stdscr = _FakeWindow(keys=[27])  # Esc -> BACK_ROUTE

    run_sales_screen(cast("curses.window", stdscr), conn)

    assert any(line == "Record a Sale" for line in stdscr.lines)


def test_run_sales_screen_when_unrecognized_key_then_quit_loops_then_exits(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(curses, "curs_set", lambda visibility: None)
    stdscr = _FakeWindow(keys=[ord("z"), ord("q")])

    run_sales_screen(cast("curses.window", stdscr), conn)

    assert stdscr._keys == []


# --- no raw PII reaches a logger.*() call ------------------------------------------


def test_module_has_no_logging_calls_pii_cannot_reach_a_logger() -> None:
    """`ADR-006` Decision (d)/`ARCH-D01`/SD-004/stop.sh gate 10 — this
    module must not log at all, matching `src/ui/customers.py`'s
    convention; the strongest guarantee that no PII field reaches a
    `logger.*()` call is that no such call exists in the module."""
    import inspect
    import re

    import src.ui.sales as sales_module

    source = inspect.getsource(sales_module)

    assert re.search(r"\blogger\s*\.\s*\w+\s*\(", source) is None
    assert "import logging" not in source
    assert "logging." not in source


def test_show_message_writes_message_and_waits_for_a_keypress() -> None:
    stdscr = _FakeWindow(keys=[ord("x")])

    _show_message(cast("curses.window", stdscr), 5, "Error: bad input")

    assert stdscr.lines == ["Error: bad input"]
    assert stdscr._keys == []


# --- bill/receipt unit_cost non-render regression (ADR-009, P3-F1-T6) ---------------
# ADR-009 Consequences, blast radius item (2): unit_cost rendered on a
# customer-facing bill or receipt would expose the shop's purchase margins
# to the customer — the interface contract table prohibits src/ui/sales.py
# from rendering it. This is a negative assertion: the rendered output for a
# sale whose line items carry a non-NULL unit_cost must contain no
# representation of that value anywhere in the receipt or the sales-history
# detail view (both go through _render_line_items).


def test_render_receipt_when_line_items_have_known_unit_cost_does_not_render_it(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(
        conn, name="Rice 1kg", price=100.0, gst_rate=5.0, cost=70.0
    )
    set_stock(conn, product.id, 10)
    sale = record_sale(
        conn, [CartLine(product_id=product.id, quantity=2)], payment_status="paid"
    )
    # get_sale() reloads with the persisted unit_cost snapshot attached —
    # confirms the value is actually present on the object being rendered,
    # not merely absent because it was never populated.
    reloaded = get_sale(conn, sale.id)
    assert reloaded.line_items[0].unit_cost == 70.0
    stdscr = _FakeWindow(keys=[ord("x")])

    _render_receipt(cast("curses.window", stdscr), conn, reloaded)

    rendered_text = "\n".join(stdscr.lines)
    assert "70" not in rendered_text
    assert "unit_cost" not in rendered_text.lower()
    assert "cost" not in rendered_text.lower()


def test_render_history_detail_when_line_items_have_known_unit_cost_does_not_render_it(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(
        conn, name="Rice 1kg", price=100.0, gst_rate=5.0, cost=70.0
    )
    set_stock(conn, product.id, 10)
    sale = record_sale(
        conn, [CartLine(product_id=product.id, quantity=2)], payment_status="paid"
    )
    reloaded = get_sale(conn, sale.id)
    assert reloaded.line_items[0].unit_cost == 70.0
    stdscr = _FakeWindow(keys=[ord("x")])

    _render_history_detail(cast("curses.window", stdscr), conn, reloaded)

    rendered_text = "\n".join(stdscr.lines)
    assert "70" not in rendered_text
    assert "unit_cost" not in rendered_text.lower()
    assert "cost" not in rendered_text.lower()


# --- SalesHistoryController navigation (Action 3) ------------------------------------

SALE_A = Sale(
    id=1,
    customer_id=None,
    payment_status="paid",
    subtotal=100.0,
    gst_total=5.0,
    bill_total=105.0,
    line_items=[],
    created_at="2026-08-01 10:00:00",
)
SALE_B = Sale(
    id=2,
    customer_id=1,
    payment_status="credit",
    subtotal=200.0,
    gst_total=24.0,
    bill_total=224.0,
    line_items=[],
    created_at="2026-08-02 10:00:00",
)


def test_history_controller_when_constructed_with_no_sales_selected_sale_is_none() -> (
    None
):
    controller = SalesHistoryController()

    assert controller.selected_index == 0
    assert controller.selected_sale() is None


def test_history_controller_when_constructed_defaults_to_first_selected() -> None:
    controller = SalesHistoryController([SALE_A, SALE_B])

    assert controller.selected_sale() == SALE_A


def test_history_controller_refresh_when_list_shrinks_clamps_index() -> None:
    controller = SalesHistoryController([SALE_A, SALE_B])
    controller.move_down()

    controller.refresh([SALE_A])

    assert controller.selected_index == 0
    assert controller.selected_sale() == SALE_A


def test_history_controller_dispatch_when_down_arrow_moves_selection_forward() -> None:
    controller = SalesHistoryController([SALE_A, SALE_B])

    route = controller.dispatch(curses.KEY_DOWN)

    assert route is None
    assert controller.selected_index == 1


def test_history_controller_dispatch_when_down_arrow_wraps_past_last_option() -> None:
    controller = SalesHistoryController([SALE_A, SALE_B])
    controller.dispatch(curses.KEY_DOWN)

    route = controller.dispatch(curses.KEY_DOWN)

    assert route is None
    assert controller.selected_index == 0


def test_history_controller_dispatch_when_enter_returns_history_detail_route() -> None:
    controller = SalesHistoryController([SALE_A])

    assert controller.dispatch(ord("\n")) == HISTORY_DETAIL_ROUTE


def test_history_controller_dispatch_when_esc_returns_back_route() -> None:
    controller = SalesHistoryController([SALE_A])

    assert controller.dispatch(27) == BACK_ROUTE


def test_history_controller_dispatch_when_q_returns_quit_route() -> None:
    controller = SalesHistoryController([SALE_A])

    assert controller.dispatch(ord("q")) == QUIT_ROUTE


def test_history_controller_dispatch_when_unrecognized_key_returns_none() -> None:
    controller = SalesHistoryController([SALE_A, SALE_B])

    route = controller.dispatch(ord("z"))

    assert route is None
    assert controller.selected_index == 0


# --- _customer_label / _render_history_list / _render_history_detail -----------------


def test_customer_label_when_customer_id_none_returns_walk_in(
    conn: sqlite3.Connection,
) -> None:
    assert _customer_label(conn, None) == "walk-in"


def test_customer_label_when_customer_id_given_returns_customer_name(
    conn: sqlite3.Connection,
) -> None:
    customer = create_customer(conn, name="Raj Kumar", phone="9876543210")

    assert _customer_label(conn, customer.id) == "Raj Kumar"


def test_render_history_list_when_sales_present_lists_each_with_selected_prefix(
    conn: sqlite3.Connection,
) -> None:
    stdscr = _FakeWindow()
    controller = SalesHistoryController([SALE_A, SALE_B])
    customer = create_customer(conn, name="Raj Kumar", phone="9876543210")
    sale_with_customer = Sale(
        id=SALE_B.id,
        customer_id=customer.id,
        payment_status=SALE_B.payment_status,
        subtotal=SALE_B.subtotal,
        gst_total=SALE_B.gst_total,
        bill_total=SALE_B.bill_total,
        line_items=[],
        created_at=SALE_B.created_at,
    )
    controller.refresh([SALE_A, sale_with_customer])
    controller.move_down()

    _render_history_list(cast("curses.window", stdscr), conn, controller)

    assert any(line.startswith("  ") and "walk-in" in line for line in stdscr.lines)
    assert any(line.startswith("> ") and "Raj Kumar" in line for line in stdscr.lines)


def test_render_history_list_when_no_sales_shows_placeholder_message(
    conn: sqlite3.Connection,
) -> None:
    stdscr = _FakeWindow()
    controller = SalesHistoryController()

    _render_history_list(cast("curses.window", stdscr), conn, controller)

    assert any("no sales yet" in line for line in stdscr.lines)


def test_render_history_detail_shows_line_items_and_totals(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    set_stock(conn, product.id, 10)
    sale = record_sale(
        conn, [CartLine(product_id=product.id, quantity=2)], payment_status="paid"
    )
    stdscr = _FakeWindow(keys=[ord("x")])

    _render_history_detail(cast("curses.window", stdscr), conn, sale)

    assert any("Rice 1kg x2" in line for line in stdscr.lines)
    assert any("Bill total" in line for line in stdscr.lines)


# --- _run_history_detail_screen / _dispatch_history_route -----------------------------


def test_run_history_detail_screen_fetches_and_renders_sale(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    set_stock(conn, product.id, 10)
    sale = record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )
    stdscr = _FakeWindow(keys=[ord("x")])

    _run_history_detail_screen(cast("curses.window", stdscr), conn, sale.id)

    assert any("Sale Detail" in line for line in stdscr.lines)


def test_dispatch_history_route_when_no_sale_selected_is_a_no_op(
    conn: sqlite3.Connection,
) -> None:
    controller = SalesHistoryController()
    stdscr = _FakeWindow()

    _dispatch_history_route(
        cast("curses.window", stdscr), conn, controller, HISTORY_DETAIL_ROUTE
    )

    assert stdscr.lines == []


def test_dispatch_history_route_history_detail_route_invokes_detail_screen(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    set_stock(conn, product.id, 10)
    sale = record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )
    controller = SalesHistoryController([sale])
    stdscr = _FakeWindow(keys=[ord("x")])

    _dispatch_history_route(
        cast("curses.window", stdscr), conn, controller, HISTORY_DETAIL_ROUTE
    )

    assert any("Sale Detail" in line for line in stdscr.lines)


# --- run_sales_history_screen ---------------------------------------------------------


def test_run_sales_history_screen_when_back_key_returns_immediately(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(curses, "curs_set", lambda visibility: None)
    stdscr = _FakeWindow(keys=[27])  # Esc -> BACK_ROUTE

    run_sales_history_screen(cast("curses.window", stdscr), conn)

    assert any(line == "Sales History" for line in stdscr.lines)


def test_run_sales_history_screen_when_enter_then_back_views_detail_and_returns(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(curses, "curs_set", lambda visibility: None)
    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    set_stock(conn, product.id, 10)
    record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )
    stdscr = _FakeWindow(keys=[ord("\n"), ord("x"), 27])

    run_sales_history_screen(cast("curses.window", stdscr), conn)

    assert stdscr._keys == []


def test_run_sales_history_screen_when_customer_id_given_filters_to_that_customer(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(curses, "curs_set", lambda visibility: None)
    product = create_product(conn, name="Rice 1kg", price=100.0, gst_rate=5.0)
    set_stock(conn, product.id, 10)
    customer = create_customer(conn, name="Raj Kumar", phone="9876543210")
    record_sale(
        conn,
        [CartLine(product_id=product.id, quantity=1)],
        payment_status="credit",
        customer_id=customer.id,
    )
    stdscr = _FakeWindow(keys=[27])

    run_sales_history_screen(
        cast("curses.window", stdscr), conn, customer_id=customer.id
    )

    assert any("Raj Kumar" in line for line in stdscr.lines)
