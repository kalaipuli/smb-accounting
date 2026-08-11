"""Unit tests for the customer/khata navigation controller and routing
(P2-F2-T5, partial — see `src/ui/customers.py`'s module-level scope note).

Covers key-binding dispatch (arrows/Tab/Enter/Esc/q, reused from
`src/ui/keybindings.py`) against `CustomerController`, which holds no
curses I/O — mirroring `test_inventory.py`'s coverage of
`InventoryController`. Also covers the payment-amount prompt, the pay
handler's degrade paths (invalid input, Esc-cancel, domain-exception
error message), and route dispatch — mirroring
`test_inventory.py`'s `_prompt_adjust_amount`/`_handle_adjust` coverage.
"""

import curses
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from src.db.schema import connect, init_schema
from src.services.customers import (
    CreditTransaction,
    Customer,
    create_customer,
    get_outstanding_balance,
    list_customers,
    record_credit_sale,
    record_payment,
)
from src.ui.customers import (
    BACK_ROUTE,
    DETAIL_ROUTE,
    EDIT_CUSTOMER_ROUTE,
    NEW_CUSTOMER_ROUTE,
    QUIT_ROUTE,
    CustomerController,
    _dispatch_route,
    _handle_edit_customer,
    _handle_new_customer,
    _handle_pay,
    _prompt_customer_fields,
    _prompt_payment_amount,
    _render,
    _render_detail,
    _render_transaction_history,
    _run_detail_screen,
    _run_transaction_history_screen,
    _show_message,
    run_customers_screen,
)
from src.ui.keybindings import EntryCancelled

RAJ = Customer(id=1, name="Raj Kumar", phone="9876543210", address="MG Road")
PRIYA = Customer(id=2, name="Priya Singh", phone="9123456780")


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Real (throwaway file-backed) SQLite connection — mirrors
    `tests/unit/ui/test_inventory.py`'s fixture. Handler functions call
    real `src.services.customers` functions, so a real schema-backed
    connection exercises the same write paths those services' own tests
    already cover, without re-mocking the service layer here.
    """
    connection = connect(tmp_path / "data.db")
    init_schema(connection)
    yield connection
    connection.close()


def _insert_sale(conn: sqlite3.Connection, customer_id: int, amount: float) -> int:
    """Insert a minimal `sales` row so `record_credit_sale`'s `sale_id`
    foreign key resolves — mirrors `tests/unit/services/test_customers.py`'s
    fixture-setup pattern (a real sale row, not a service-layer mock, since
    `sales` FK-references it per `ADR-005`)."""
    cursor = conn.execute(
        "INSERT INTO sales "
        "(customer_id, payment_status, subtotal, gst_total, bill_total) "
        "VALUES (?, 'credit', ?, 0, ?)",
        (customer_id, amount, amount),
    )
    conn.commit()
    sale_id = cursor.lastrowid
    assert sale_id is not None
    return sale_id


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
    one `ord()` per character — mirrors `test_keybindings.py`'s own
    `_keys` helper for `FormController`/`run_form` key-queue tests."""
    return [ord(c) for c in text]


def _backspaces(count: int) -> list[int]:
    """`count` backspace keycodes — clears a pre-filled `FormField.default`
    buffer (which starts populated, not empty) before typing a replacement
    value, mirroring `FormController`'s edit-in-place semantics (typed
    keys append to the existing buffer rather than replacing it)."""
    return [127] * count


# --- construction / empty state ------------------------------------------------


def test_controller_when_constructed_with_no_customers_selected_customer_is_none() -> (
    None
):
    controller = CustomerController()

    assert controller.selected_index == 0
    assert controller.selected_customer() is None


def test_controller_when_constructed_with_customers_defaults_to_first_selected() -> (
    None
):
    controller = CustomerController([RAJ, PRIYA])

    assert controller.selected_customer() == RAJ


# --- refresh --------------------------------------------------------------------


def test_refresh_when_selection_still_in_range_preserves_index() -> None:
    controller = CustomerController([RAJ, PRIYA])
    controller.move_down()

    controller.refresh([RAJ, PRIYA])

    assert controller.selected_index == 1


def test_refresh_when_list_shrinks_below_selection_clamps_index() -> None:
    controller = CustomerController([RAJ, PRIYA])
    controller.move_down()

    controller.refresh([RAJ])

    assert controller.selected_index == 0
    assert controller.selected_customer() == RAJ


def test_refresh_when_list_becomes_empty_selected_customer_is_none() -> None:
    controller = CustomerController([RAJ])

    controller.refresh([])

    assert controller.selected_customer() is None


# --- arrow/tab navigation dispatch ----------------------------------------------


def test_dispatch_when_down_arrow_moves_selection_forward() -> None:
    controller = CustomerController([RAJ, PRIYA])

    route = controller.dispatch(curses.KEY_DOWN)

    assert route is None
    assert controller.selected_index == 1


def test_dispatch_when_down_arrow_wraps_past_last_option() -> None:
    controller = CustomerController([RAJ, PRIYA])
    controller.dispatch(curses.KEY_DOWN)

    route = controller.dispatch(curses.KEY_DOWN)

    assert route is None
    assert controller.selected_index == 0


def test_dispatch_when_up_arrow_on_empty_list_does_not_raise() -> None:
    controller = CustomerController()

    route = controller.dispatch(curses.KEY_UP)

    assert route is None
    assert controller.selected_index == 0


def test_dispatch_when_tab_moves_selection_forward_like_down_arrow() -> None:
    controller = CustomerController([RAJ, PRIYA])

    controller.dispatch(ord("\t"))

    assert controller.selected_index == 1


# --- detail / back / quit dispatch ----------------------------------------------


def test_dispatch_when_enter_returns_detail_route() -> None:
    controller = CustomerController([RAJ])

    assert controller.dispatch(ord("\n")) == DETAIL_ROUTE


def test_dispatch_when_esc_returns_back_route() -> None:
    controller = CustomerController([RAJ])

    assert controller.dispatch(27) == BACK_ROUTE


def test_dispatch_when_q_returns_quit_route() -> None:
    controller = CustomerController([RAJ])

    assert controller.dispatch(ord("q")) == QUIT_ROUTE


def test_dispatch_when_unrecognized_key_returns_none_and_does_not_move_selection() -> (
    None
):
    controller = CustomerController([RAJ, PRIYA])

    route = controller.dispatch(ord("z"))

    assert route is None
    assert controller.selected_index == 0


def test_dispatch_when_n_pressed_returns_new_customer_route() -> None:
    controller = CustomerController()

    assert controller.dispatch(ord("n")) == NEW_CUSTOMER_ROUTE
    assert controller.dispatch(ord("N")) == NEW_CUSTOMER_ROUTE


def test_dispatch_when_e_pressed_returns_edit_customer_route() -> None:
    """Top-level `'e'`/`'E'` key on the customer list, mirroring
    `CatalogController.dispatch`'s `EDIT_PRODUCT_ROUTE` wiring exactly
    (architect finding 3, phase2-feature6-sales-customer-gaps Action 4)."""
    controller = CustomerController([RAJ])

    assert controller.dispatch(ord("e")) == EDIT_CUSTOMER_ROUTE
    assert controller.dispatch(ord("E")) == EDIT_CUSTOMER_ROUTE


# --- _render (pure render, no raw curses input) ----------------------------------


def test_render_when_customers_present_lists_each_with_selected_prefix() -> None:
    stdscr = _FakeWindow()
    controller = CustomerController([RAJ, PRIYA])
    controller.move_down()

    _render(cast("curses.window", stdscr), controller)

    assert any(
        line.startswith("  Raj Kumar") and "9876543210" in line for line in stdscr.lines
    )
    assert any(
        line.startswith("> Priya Singh") and "9123456780" in line
        for line in stdscr.lines
    )


def test_render_when_no_customers_shows_placeholder_message() -> None:
    stdscr = _FakeWindow()
    controller = CustomerController()

    _render(cast("curses.window", stdscr), controller)

    assert any("no customers yet" in line for line in stdscr.lines)


def test_render_detail_shows_name_phone_address_and_balance() -> None:
    stdscr = _FakeWindow()

    _render_detail(cast("curses.window", stdscr), RAJ, 150.0)

    assert any("Raj Kumar" in line for line in stdscr.lines)
    assert any("9876543210" in line for line in stdscr.lines)
    assert any("MG Road" in line for line in stdscr.lines)
    assert any("150.00" in line for line in stdscr.lines)


def test_render_detail_when_no_address_omits_address_line() -> None:
    stdscr = _FakeWindow()

    _render_detail(cast("curses.window", stdscr), PRIYA, 0.0)

    assert not any(line.startswith("Address:") for line in stdscr.lines)


# --- _show_message (single getch, no raw text capture) ---------------------------


def test_show_message_writes_message_and_waits_for_a_keypress() -> None:
    stdscr = _FakeWindow(keys=[ord("x")])

    _show_message(cast("curses.window", stdscr), 5, "Error: bad input")

    assert stdscr.lines == ["Error: bad input"]
    assert stdscr._keys == []


# --- _prompt_payment_amount (real raw-keycode capture path) ----------------------


def test_prompt_payment_amount_when_digits_typed_then_enter_returns_float() -> None:
    stdscr = _FakeWindow(keys=[*[ord(c) for c in "50.5"], ord("\n")])

    result = _prompt_payment_amount(cast("curses.window", stdscr), 8)

    assert result == 50.5


def test_prompt_payment_amount_when_non_numeric_returns_none() -> None:
    stdscr = _FakeWindow(keys=[*[ord(c) for c in "abc"], ord("\n")])

    result = _prompt_payment_amount(cast("curses.window", stdscr), 8)

    assert result is None


def test_prompt_payment_amount_when_esc_pressed_raises_entry_cancelled() -> None:
    stdscr = _FakeWindow(keys=[ord("5"), 27])

    with pytest.raises(EntryCancelled):
        _prompt_payment_amount(cast("curses.window", stdscr), 8)


# --- _handle_pay (service composition; _prompt_payment_amount monkeypatched) -----


def test_handle_pay_when_amount_valid_records_payment_and_reduces_balance(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer = create_customer(conn, name="Raj Kumar", phone="9876543210")
    sale_id = _insert_sale(conn, customer.id, 100.0)
    record_credit_sale(conn, customer.id, sale_id=sale_id, amount=100.0)
    monkeypatch.setattr(
        "src.ui.customers._prompt_payment_amount", lambda stdscr, row: 40.0
    )
    stdscr = _FakeWindow()

    _handle_pay(cast("curses.window", stdscr), conn, customer)

    assert get_outstanding_balance(conn, customer.id) == 60.0


def test_handle_pay_when_input_invalid_shows_message_and_does_not_write(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer = create_customer(conn, name="Raj Kumar", phone="9876543210")
    sale_id = _insert_sale(conn, customer.id, 100.0)
    record_credit_sale(conn, customer.id, sale_id=sale_id, amount=100.0)
    monkeypatch.setattr(
        "src.ui.customers._prompt_payment_amount", lambda stdscr, row: None
    )
    stdscr = _FakeWindow(keys=[ord("x")])

    _handle_pay(cast("curses.window", stdscr), conn, customer)

    assert any("Invalid input" in line for line in stdscr.lines)
    assert get_outstanding_balance(conn, customer.id) == 100.0


def test_handle_pay_when_amount_exceeds_balance_shows_error_message(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Error-path coverage: patch -> domain exception -> assert surfaced,
    no mutation. Mirrors `test_inventory.py`'s negative-stock-guard test."""
    customer = create_customer(conn, name="Raj Kumar", phone="9876543210")
    sale_id = _insert_sale(conn, customer.id, 100.0)
    record_credit_sale(conn, customer.id, sale_id=sale_id, amount=100.0)
    monkeypatch.setattr(
        "src.ui.customers._prompt_payment_amount", lambda stdscr, row: 500.0
    )
    stdscr = _FakeWindow(keys=[ord("x")])

    _handle_pay(cast("curses.window", stdscr), conn, customer)

    assert any(line.startswith("Error:") for line in stdscr.lines)
    assert get_outstanding_balance(conn, customer.id) == 100.0


def test_handle_pay_when_esc_cancels_no_mutation_and_no_invalid_message(
    conn: sqlite3.Connection,
) -> None:
    customer = create_customer(conn, name="Raj Kumar", phone="9876543210")
    sale_id = _insert_sale(conn, customer.id, 100.0)
    record_credit_sale(conn, customer.id, sale_id=sale_id, amount=100.0)
    stdscr = _FakeWindow(keys=[ord("5"), 27])

    _handle_pay(cast("curses.window", stdscr), conn, customer)

    assert get_outstanding_balance(conn, customer.id) == 100.0
    assert not any("Invalid input" in line for line in stdscr.lines)


# --- _prompt_customer_fields / _handle_new_customer (FormController creation form) -


def test_prompt_customer_fields_when_all_fields_typed_returns_typed_values() -> None:
    keys = [*_keys("Raj Kumar"), ord("\n"), *_keys("9876543210"), ord("\n")]
    keys += [*_keys("MG Road"), ord("\n")]
    stdscr = _FakeWindow(keys=keys)

    result = _prompt_customer_fields(cast("curses.window", stdscr))

    assert result == {
        "name": "Raj Kumar",
        "phone": "9876543210",
        "address": "MG Road",
    }


def test_prompt_customer_fields_when_esc_pressed_raises_entry_cancelled() -> None:
    stdscr = _FakeWindow(keys=[*_keys("Raj"), 27])

    with pytest.raises(EntryCancelled):
        _prompt_customer_fields(cast("curses.window", stdscr))


def test_handle_new_customer_when_valid_creates_customer(
    conn: sqlite3.Connection,
) -> None:
    keys = [*_keys("Raj Kumar"), ord("\n"), *_keys("9876543210"), ord("\n")]
    keys += [*_keys("MG Road"), ord("\n")]
    stdscr = _FakeWindow(keys=keys)

    _handle_new_customer(cast("curses.window", stdscr), conn)

    customers = list_customers(conn)
    assert len(customers) == 1
    assert customers[0].name == "Raj Kumar"
    assert customers[0].phone == "9876543210"
    assert customers[0].address == "MG Road"


def test_handle_new_customer_when_address_left_blank_stores_none(
    conn: sqlite3.Connection,
) -> None:
    keys = [
        *_keys("Priya Singh"),
        ord("\n"),
        *_keys("9123456780"),
        ord("\n"),
        ord("\n"),
    ]
    stdscr = _FakeWindow(keys=keys)

    _handle_new_customer(cast("curses.window", stdscr), conn)

    customers = list_customers(conn)
    assert len(customers) == 1
    assert customers[0].address is None


def test_handle_new_customer_when_duplicate_phone_shows_error_and_does_not_create(
    conn: sqlite3.Connection,
) -> None:
    """Error-path coverage: patch -> domain exception -> assert surfaced,
    no mutation. `create_customer` raises `CustomerValidationError` (a
    `CustomerError`) on a phone-uniqueness collision — exercised here via
    a real duplicate insert, not a monkeypatch, since the exception is a
    genuine DB-constraint path already covered at the service layer; this
    test proves the UI handler surfaces it rather than crashing."""
    create_customer(conn, name="Raj Kumar", phone="9876543210")
    keys = [*_keys("Someone Else"), ord("\n"), *_keys("9876543210"), ord("\n")]
    keys += [ord("\n"), ord("x")]
    stdscr = _FakeWindow(keys=keys)

    _handle_new_customer(cast("curses.window", stdscr), conn)

    assert any(line.startswith("Error:") for line in stdscr.lines)
    assert len(list_customers(conn)) == 1


def test_handle_new_customer_when_name_empty_shows_validation_error(
    conn: sqlite3.Connection,
) -> None:
    """Validation-error path: empty name is rejected by
    `create_customer`'s `_validate_name` before any insert is attempted."""
    keys = [ord("\n"), *_keys("9876543210"), ord("\n"), ord("\n"), ord("x")]
    stdscr = _FakeWindow(keys=keys)

    _handle_new_customer(cast("curses.window", stdscr), conn)

    assert any(line.startswith("Error:") for line in stdscr.lines)
    assert list_customers(conn) == []


def test_handle_new_customer_when_esc_cancels_no_mutation_and_no_error_message(
    conn: sqlite3.Connection,
) -> None:
    stdscr = _FakeWindow(keys=[*_keys("Raj"), 27])

    _handle_new_customer(cast("curses.window", stdscr), conn)

    assert list_customers(conn) == []
    assert not any(line.startswith("Error:") for line in stdscr.lines)


def test_prompt_customer_fields_when_existing_given_prefills_and_enter_keeps() -> None:
    """Pressing Enter on every field with no typed input keeps the
    pre-filled `existing` values — mirrors
    `catalog.py::_prompt_product_fields(stdscr, existing=product)`'s
    edit-in-place pre-fill pattern via `FormField.default`."""
    keys = [ord("\n"), ord("\n"), ord("\n")]
    stdscr = _FakeWindow(keys=keys)

    result = _prompt_customer_fields(cast("curses.window", stdscr), existing=RAJ)

    assert result == {
        "name": "Raj Kumar",
        "phone": "9876543210",
        "address": "MG Road",
    }


def test_prompt_customer_fields_when_existing_given_shows_edit_title() -> None:
    stdscr = _FakeWindow(keys=[ord("\n"), ord("\n"), ord("\n")])

    _prompt_customer_fields(cast("curses.window", stdscr), existing=RAJ)

    assert any("Edit Customer" in line for line in stdscr.lines)


def test_handle_edit_customer_when_valid_updates_customer(
    conn: sqlite3.Connection,
) -> None:
    create_customer(conn, name="Raj Kumar", phone="9876543210", address="MG Road")
    customer = list_customers(conn)[0]
    keys = [*_backspaces(len("Raj Kumar")), *_keys("Raj K"), ord("\n")]
    keys += [ord("\n"), ord("\n")]
    stdscr = _FakeWindow(keys=keys)

    _handle_edit_customer(cast("curses.window", stdscr), conn, customer)

    updated = list_customers(conn)[0]
    assert updated.name == "Raj K"
    assert updated.phone == "9876543210"
    assert updated.address == "MG Road"


def test_handle_edit_customer_when_duplicate_phone_shows_error_and_does_not_update(
    conn: sqlite3.Connection,
) -> None:
    """Error-path coverage: patch -> domain exception -> assert surfaced,
    no mutation. `update_customer` raises `CustomerValidationError` (a
    `CustomerError`) on a phone-uniqueness collision with a different
    customer — exercised via a real duplicate, not a monkeypatch, mirroring
    `test_handle_new_customer_when_duplicate_phone_shows_error...`."""
    create_customer(conn, name="Raj Kumar", phone="9876543210")
    create_customer(conn, name="Priya Singh", phone="9123456780")
    customer = next(c for c in list_customers(conn) if c.name == "Priya Singh")
    keys = [ord("\n")]
    keys += [*_backspaces(len("9123456780")), *_keys("9876543210"), ord("\n")]
    keys += [ord("\n"), ord("x")]
    stdscr = _FakeWindow(keys=keys)

    _handle_edit_customer(cast("curses.window", stdscr), conn, customer)

    assert any(line.startswith("Error:") for line in stdscr.lines)
    updated = next(c for c in list_customers(conn) if c.name == "Priya Singh")
    assert updated.phone == "9123456780"


def test_handle_edit_customer_when_esc_cancels_no_mutation_and_no_error_message(
    conn: sqlite3.Connection,
) -> None:
    create_customer(conn, name="Raj Kumar", phone="9876543210")
    customer = list_customers(conn)[0]
    stdscr = _FakeWindow(keys=[27])

    _handle_edit_customer(cast("curses.window", stdscr), conn, customer)

    unchanged = list_customers(conn)[0]
    assert unchanged.name == "Raj Kumar"
    assert not any(line.startswith("Error:") for line in stdscr.lines)


def test_dispatch_route_edit_customer_route_invokes_edit_handler(
    conn: sqlite3.Connection,
) -> None:
    create_customer(conn, name="Raj Kumar", phone="9876543210")
    customer = list_customers(conn)[0]
    controller = CustomerController([customer])
    keys = [*_backspaces(len("Raj Kumar")), *_keys("Raj K"), ord("\n")]
    keys += [ord("\n"), ord("\n")]
    stdscr = _FakeWindow(keys=keys)

    _dispatch_route(
        cast("curses.window", stdscr), conn, controller, EDIT_CUSTOMER_ROUTE
    )

    assert list_customers(conn)[0].name == "Raj K"


def test_dispatch_route_new_customer_route_invoked_even_with_empty_list(
    conn: sqlite3.Connection,
) -> None:
    """`NEW_CUSTOMER_ROUTE` must be reachable when no customer is yet
    selected/selectable — mirrors `CatalogController`'s `NEW_PRODUCT_ROUTE`
    handling, checked before any `selected_customer()` lookup."""
    controller = CustomerController()
    keys = [*_keys("Raj Kumar"), ord("\n"), *_keys("9876543210"), ord("\n"), ord("\n")]
    stdscr = _FakeWindow(keys=keys)

    _dispatch_route(cast("curses.window", stdscr), conn, controller, NEW_CUSTOMER_ROUTE)

    assert len(list_customers(conn)) == 1


# --- no raw PII reaches a logger.*() call ------------------------------------------


def test_module_has_no_logging_calls_pii_cannot_reach_a_logger() -> None:
    """`ADR-006` Decision (d)/`ARCH-D01`/SD-004/stop.sh gate 10 — this
    module must not log at all, matching the service layer's convention;
    the strongest guarantee that no PII field reaches a `logger.*()` call
    is that no such call exists in the module."""
    import inspect
    import re

    import src.ui.customers as customers_module

    source = inspect.getsource(customers_module)

    assert re.search(r"\blogger\s*\.\s*\w+\s*\(", source) is None
    assert "import logging" not in source
    assert "logging." not in source


# --- _run_detail_screen / _dispatch_route / run_customers_screen -----------------


def test_run_detail_screen_when_p_pressed_invokes_pay_then_any_other_key_returns(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer = create_customer(conn, name="Raj Kumar", phone="9876543210")
    sale_id = _insert_sale(conn, customer.id, 100.0)
    record_credit_sale(conn, customer.id, sale_id=sale_id, amount=100.0)
    monkeypatch.setattr(
        "src.ui.customers._prompt_payment_amount", lambda stdscr, row: 25.0
    )
    stdscr = _FakeWindow(keys=[ord("p"), ord("x")])

    _run_detail_screen(cast("curses.window", stdscr), conn, customer)

    assert get_outstanding_balance(conn, customer.id) == 75.0


def test_run_detail_screen_when_v_pressed_opens_sales_history_then_any_key_returns(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`'v'`/`'V'` on the customer-detail screen opens the sales-history
    screen filtered to `customer_id=customer.id`
    (phase2-feature6-sales-customer-gaps, Action 3) — verified here by
    monkeypatching `run_sales_history_screen` and asserting it was called
    with the right `customer_id`, mirroring the `p`/`_handle_pay` coverage
    above rather than re-driving the real sales-history screen's own keys."""
    customer = create_customer(conn, name="Raj Kumar", phone="9876543210")
    calls: list[int | None] = []

    def fake_history(
        stdscr: object, conn: sqlite3.Connection, customer_id: int | None = None
    ) -> None:
        calls.append(customer_id)

    monkeypatch.setattr("src.ui.customers.run_sales_history_screen", fake_history)
    stdscr = _FakeWindow(keys=[ord("v"), ord("x")])

    _run_detail_screen(cast("curses.window", stdscr), conn, customer)

    assert calls == [customer.id]


def test_render_transaction_history_lists_date_type_and_amount() -> None:
    stdscr = _FakeWindow()
    transactions = [
        CreditTransaction(
            id=1,
            customer_id=1,
            sale_id=10,
            transaction_type="credit_sale",
            amount=250.0,
            created_at="2026-08-09T10:00:00.000Z",
        ),
        CreditTransaction(
            id=2,
            customer_id=1,
            sale_id=None,
            transaction_type="payment",
            amount=100.0,
            created_at="2026-08-09T11:00:00.000Z",
        ),
    ]

    _render_transaction_history(cast("curses.window", stdscr), transactions)

    assert any(
        "2026-08-09T10:00:00.000Z" in line
        and "credit_sale" in line
        and "250.00" in line
        for line in stdscr.lines
    )
    assert any(
        "2026-08-09T11:00:00.000Z" in line and "payment" in line and "100.00" in line
        for line in stdscr.lines
    )


def test_render_transaction_history_when_empty_shows_placeholder_message() -> None:
    stdscr = _FakeWindow()

    _render_transaction_history(cast("curses.window", stdscr), [])

    assert any("no transactions yet" in line for line in stdscr.lines)


def test_run_transaction_history_screen_renders_customer_ledger_rows(
    conn: sqlite3.Connection,
) -> None:
    customer = create_customer(conn, name="Raj Kumar", phone="9876543210")
    sale_id = _insert_sale(conn, customer.id, 100.0)
    record_credit_sale(conn, customer.id, sale_id=sale_id, amount=100.0)
    record_payment(conn, customer.id, 40.0)
    stdscr = _FakeWindow(keys=[ord("x")])

    _run_transaction_history_screen(cast("curses.window", stdscr), conn, customer)

    assert any("credit_sale" in line for line in stdscr.lines)
    assert any("payment" in line for line in stdscr.lines)
    assert stdscr._keys == []


def test_run_detail_screen_when_t_pressed_opens_transaction_history_then_returns(
    conn: sqlite3.Connection,
) -> None:
    customer = create_customer(conn, name="Raj Kumar", phone="9876543210")
    sale_id = _insert_sale(conn, customer.id, 100.0)
    record_credit_sale(conn, customer.id, sale_id=sale_id, amount=100.0)
    stdscr = _FakeWindow(keys=[ord("t"), ord("x"), ord("x")])

    _run_detail_screen(cast("curses.window", stdscr), conn, customer)

    assert stdscr._keys == []


def test_run_detail_screen_when_capital_t_pressed_also_opens_transaction_history(
    conn: sqlite3.Connection,
) -> None:
    customer = create_customer(conn, name="Raj Kumar", phone="9876543210")
    stdscr = _FakeWindow(keys=[ord("T"), ord("x"), ord("x")])

    _run_detail_screen(cast("curses.window", stdscr), conn, customer)

    assert stdscr._keys == []


def test_run_detail_screen_when_capital_v_pressed_also_opens_sales_history(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer = create_customer(conn, name="Raj Kumar", phone="9876543210")
    calls: list[int | None] = []

    def fake_history(
        stdscr: object, conn: sqlite3.Connection, customer_id: int | None = None
    ) -> None:
        calls.append(customer_id)

    monkeypatch.setattr("src.ui.customers.run_sales_history_screen", fake_history)
    stdscr = _FakeWindow(keys=[ord("V"), ord("x")])

    _run_detail_screen(cast("curses.window", stdscr), conn, customer)

    assert calls == [customer.id]


def test_dispatch_route_when_no_customer_selected_is_a_no_op(
    conn: sqlite3.Connection,
) -> None:
    controller = CustomerController()
    stdscr = _FakeWindow()

    _dispatch_route(cast("curses.window", stdscr), conn, controller, DETAIL_ROUTE)

    assert stdscr.lines == []


def test_dispatch_route_detail_route_invokes_detail_screen(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer = create_customer(conn, name="Raj Kumar", phone="9876543210")
    controller = CustomerController([customer])
    stdscr = _FakeWindow(keys=[ord("x")])

    _dispatch_route(cast("curses.window", stdscr), conn, controller, DETAIL_ROUTE)

    assert any("Outstanding balance" in line for line in stdscr.lines)


def test_run_customers_screen_when_back_key_returns_immediately(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(curses, "curs_set", lambda visibility: None)
    stdscr = _FakeWindow(keys=[27])  # Esc -> BACK_ROUTE

    run_customers_screen(cast("curses.window", stdscr), conn)

    assert any(line == "Customers" for line in stdscr.lines)


def test_run_customers_screen_when_unrecognized_key_then_quit_loops_then_exits(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(curses, "curs_set", lambda visibility: None)
    stdscr = _FakeWindow(keys=[ord("z"), ord("q")])

    run_customers_screen(cast("curses.window", stdscr), conn)

    assert stdscr._keys == []


def test_run_customers_screen_when_enter_then_back_navigates_detail_and_returns(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(curses, "curs_set", lambda visibility: None)
    create_customer(conn, name="Raj Kumar", phone="9876543210")
    stdscr = _FakeWindow(keys=[ord("\n"), ord("x"), 27])

    run_customers_screen(cast("curses.window", stdscr), conn)

    # `_FakeWindow.clear()` empties `lines` on every screen transition (list
    # -> detail -> back to list), so only the final render's content
    # survives to inspect here — confirms the full round trip completed
    # (all three queued keys consumed) and landed back on the list screen.
    assert stdscr._keys == []
    assert any(line == "Customers" for line in stdscr.lines)
