"""Unit tests for the inventory navigation controller and routing (P1-F4-T3).

Covers key-binding dispatch (arrows/Tab/Enter/Esc/q, reused from
`src/ui/keybindings.py`) and the inventory-specific shortcut ('a' adjust)
against `InventoryController`, which holds no curses I/O — mirroring
`test_catalog.py`'s coverage of `CatalogController`.
"""

import curses
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from src.db.schema import connect, init_schema
from src.services.catalog import Product, create_product
from src.services.inventory import StockLevel, get_stock, set_stock
from src.ui.inventory import (
    ADJUST_ROUTE,
    BACK_ROUTE,
    QUIT_ROUTE,
    InventoryController,
    InventoryRow,
    _dispatch_route,
    _handle_adjust,
    _load_rows,
    _prompt_adjust_amount,
    _render,
    _show_message,
    run_inventory_screen,
)
from src.ui.keybindings import EntryCancelled

RICE = InventoryRow(
    product=Product(id=1, name="Rice 1kg", price=60, gst_rate=5),
    stock=StockLevel(product_id=1, quantity=10),
)
SUGAR = InventoryRow(
    product=Product(id=2, name="Sugar 1kg", price=45, gst_rate=5),
    stock=StockLevel(product_id=2, quantity=0),
)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Real (throwaway file-backed) SQLite connection — mirrors
    `tests/unit/ui/test_catalog.py`'s fixture."""
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

    def refresh(self) -> None:
        pass

    def getch(self) -> int:
        return self._keys.pop(0)


# --- construction / empty state ------------------------------------------------


def test_controller_when_constructed_with_no_rows_selected_row_is_none() -> None:
    controller = InventoryController()

    assert controller.selected_index == 0
    assert controller.selected_row() is None


def test_controller_when_constructed_with_rows_defaults_to_first_selected() -> None:
    controller = InventoryController([RICE, SUGAR])

    assert controller.selected_row() == RICE


# --- refresh --------------------------------------------------------------------


def test_refresh_when_selection_still_in_range_preserves_index() -> None:
    controller = InventoryController([RICE, SUGAR])
    controller.move_down()

    controller.refresh([RICE, SUGAR])

    assert controller.selected_index == 1


def test_refresh_when_list_shrinks_below_selection_clamps_index() -> None:
    controller = InventoryController([RICE, SUGAR])
    controller.move_down()

    controller.refresh([RICE])

    assert controller.selected_index == 0
    assert controller.selected_row() == RICE


def test_refresh_when_list_becomes_empty_selected_row_is_none() -> None:
    controller = InventoryController([RICE])

    controller.refresh([])

    assert controller.selected_row() is None


# --- arrow/tab navigation dispatch ----------------------------------------------


def test_dispatch_when_down_arrow_moves_selection_forward() -> None:
    controller = InventoryController([RICE, SUGAR])

    route = controller.dispatch(curses.KEY_DOWN)

    assert route is None
    assert controller.selected_index == 1


def test_dispatch_when_down_arrow_wraps_past_last_option() -> None:
    controller = InventoryController([RICE, SUGAR])
    controller.dispatch(curses.KEY_DOWN)

    route = controller.dispatch(curses.KEY_DOWN)

    assert route is None
    assert controller.selected_index == 0


def test_dispatch_when_up_arrow_on_empty_list_does_not_raise() -> None:
    controller = InventoryController()

    route = controller.dispatch(curses.KEY_UP)

    assert route is None
    assert controller.selected_index == 0


def test_dispatch_when_tab_moves_selection_forward_like_down_arrow() -> None:
    controller = InventoryController([RICE, SUGAR])

    controller.dispatch(ord("\t"))

    assert controller.selected_index == 1


# --- adjust / back / quit dispatch ----------------------------------------------


def test_dispatch_when_enter_returns_adjust_route() -> None:
    controller = InventoryController([RICE])

    assert controller.dispatch(ord("\n")) == ADJUST_ROUTE


def test_dispatch_when_a_returns_adjust_route() -> None:
    controller = InventoryController([RICE])

    assert controller.dispatch(ord("a")) == ADJUST_ROUTE


def test_dispatch_when_esc_returns_back_route() -> None:
    controller = InventoryController([RICE])

    assert controller.dispatch(27) == BACK_ROUTE


def test_dispatch_when_q_returns_quit_route() -> None:
    controller = InventoryController([RICE])

    assert controller.dispatch(ord("q")) == QUIT_ROUTE


def test_dispatch_when_unrecognized_key_returns_none_and_does_not_move_selection() -> (
    None
):
    controller = InventoryController([RICE, SUGAR])

    route = controller.dispatch(ord("z"))

    assert route is None
    assert controller.selected_index == 0


# --- _render (pure render, no raw curses input) ----------------------------------


def test_render_when_rows_present_lists_each_with_selected_prefix_and_quantity() -> (
    None
):
    stdscr = _FakeWindow()
    controller = InventoryController([RICE, SUGAR])
    controller.move_down()

    _render(cast("curses.window", stdscr), controller)

    assert any(
        line.startswith("  Rice 1kg") and "qty=10" in line for line in stdscr.lines
    )
    assert any(
        line.startswith("> Sugar 1kg") and "qty=0" in line for line in stdscr.lines
    )


def test_render_when_no_rows_shows_placeholder_message() -> None:
    stdscr = _FakeWindow()
    controller = InventoryController()

    _render(cast("curses.window", stdscr), controller)

    assert any("no products yet" in line for line in stdscr.lines)


# --- _show_message (single getch, no raw text capture) ---------------------------


def test_show_message_writes_message_and_waits_for_a_keypress() -> None:
    stdscr = _FakeWindow(keys=[ord("x")])

    _show_message(cast("curses.window", stdscr), 5, "Error: bad input")

    assert stdscr.lines == ["Error: bad input"]
    assert stdscr._keys == []


# --- _load_rows -------------------------------------------------------------------


def test_load_rows_when_products_exist_pairs_each_with_its_stock_level(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    set_stock(conn, product.id, 25)

    rows = _load_rows(conn)

    assert rows == [InventoryRow(product=product, stock=StockLevel(product.id, 25))]


def test_load_rows_when_no_stock_row_yet_defaults_to_zero_quantity(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)

    rows = _load_rows(conn)

    assert rows == [InventoryRow(product=product, stock=StockLevel(product.id, 0))]


# --- _prompt_adjust_amount (action 12: cancel) ------------------------------------
# Exercises the real raw-keycode capture path (`read_line`) via `_FakeWindow`'s
# predetermined key queue — no `_prompt_adjust_amount` monkeypatch here, unlike
# the `_handle_adjust` tests below.


def test_prompt_adjust_amount_when_digits_typed_then_enter_returns_int() -> None:
    stdscr = _FakeWindow(keys=[*[ord(c) for c in "5"], ord("\n")])

    result = _prompt_adjust_amount(cast("curses.window", stdscr), 3)

    assert result == 5


def test_prompt_adjust_amount_when_esc_pressed_raises_entry_cancelled() -> None:
    stdscr = _FakeWindow(keys=[ord("5"), 27])

    with pytest.raises(EntryCancelled):
        _prompt_adjust_amount(cast("curses.window", stdscr), 3)


# --- _handle_adjust ---------------------------------------------------------------
# `_prompt_adjust_amount` is monkeypatched at the module boundary — raw
# curses text capture is out of this module's testing boundary, but the
# handler's own control flow (dispatch to the service layer, degrade on
# `None`/domain-exception) is exercised here against a real schema-backed
# connection.


def test_handle_adjust_when_delta_valid_adjusts_stock(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    set_stock(conn, product.id, 10)
    monkeypatch.setattr("src.ui.inventory._prompt_adjust_amount", lambda stdscr, row: 5)
    stdscr = _FakeWindow()
    row = InventoryRow(product=product, stock=get_stock(conn, product.id))

    _handle_adjust(cast("curses.window", stdscr), conn, row)

    assert get_stock(conn, product.id).quantity == 15


def test_handle_adjust_when_input_invalid_shows_message_and_does_not_write(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    set_stock(conn, product.id, 10)
    monkeypatch.setattr(
        "src.ui.inventory._prompt_adjust_amount", lambda stdscr, row: None
    )
    stdscr = _FakeWindow(keys=[ord("x")])
    row = InventoryRow(product=product, stock=get_stock(conn, product.id))

    _handle_adjust(cast("curses.window", stdscr), conn, row)

    assert any("Invalid input" in line for line in stdscr.lines)
    assert get_stock(conn, product.id).quantity == 10


def test_handle_adjust_when_delta_would_go_negative_shows_error_message(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative-stock guard surfaced through the UI layer."""
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    set_stock(conn, product.id, 2)
    monkeypatch.setattr(
        "src.ui.inventory._prompt_adjust_amount", lambda stdscr, row: -5
    )
    stdscr = _FakeWindow(keys=[ord("x")])
    row = InventoryRow(product=product, stock=get_stock(conn, product.id))

    _handle_adjust(cast("curses.window", stdscr), conn, row)

    assert any(line.startswith("Error:") for line in stdscr.lines)
    assert get_stock(conn, product.id).quantity == 2


def test_handle_adjust_when_esc_cancels_no_mutation_and_no_invalid_message(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    set_stock(conn, product.id, 10)

    def _raise_cancelled(stdscr: object, row: int) -> None:
        raise EntryCancelled

    monkeypatch.setattr("src.ui.inventory._prompt_adjust_amount", _raise_cancelled)
    stdscr = _FakeWindow()
    row = InventoryRow(product=product, stock=get_stock(conn, product.id))

    _handle_adjust(cast("curses.window", stdscr), conn, row)

    assert get_stock(conn, product.id).quantity == 10
    assert not any("Invalid input" in line for line in stdscr.lines)


# --- _dispatch_route / run_inventory_screen ---------------------------------------


def test_dispatch_route_when_no_row_selected_is_a_no_op(
    conn: sqlite3.Connection,
) -> None:
    controller = InventoryController()
    stdscr = _FakeWindow()

    _dispatch_route(cast("curses.window", stdscr), conn, controller, ADJUST_ROUTE)

    assert stdscr.lines == []


def test_dispatch_route_adjust_route_invokes_handler(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    set_stock(conn, product.id, 10)
    monkeypatch.setattr("src.ui.inventory._prompt_adjust_amount", lambda stdscr, row: 3)
    controller = InventoryController(
        [InventoryRow(product, get_stock(conn, product.id))]
    )
    stdscr = _FakeWindow()

    _dispatch_route(cast("curses.window", stdscr), conn, controller, ADJUST_ROUTE)

    assert get_stock(conn, product.id).quantity == 13


def test_run_inventory_screen_when_back_key_returns_immediately(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(curses, "curs_set", lambda visibility: None)
    stdscr = _FakeWindow(keys=[27])  # Esc -> BACK_ROUTE

    run_inventory_screen(cast("curses.window", stdscr), conn)

    assert any(line == "Inventory" for line in stdscr.lines)


def test_run_inventory_screen_when_unrecognized_key_then_quit_loops_then_exits(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(curses, "curs_set", lambda visibility: None)
    stdscr = _FakeWindow(keys=[ord("z"), ord("q")])

    run_inventory_screen(cast("curses.window", stdscr), conn)

    assert stdscr._keys == []
