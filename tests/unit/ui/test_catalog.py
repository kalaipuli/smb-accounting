"""Unit tests for the catalog navigation controller and routing (P1-F3-T4).

Covers key-binding dispatch (arrows/Tab/Enter/Esc/q, reused from
`src/ui/keybindings.py`) and the catalog-specific shortcuts ('n' new
product, 'e' edit product, 'd' add discount) against `CatalogController`,
which holds no curses I/O — mirroring `test_main_menu.py`'s coverage of
`MainMenuController`.
"""

import curses
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from src.db.schema import connect, init_schema
from src.services.catalog import Product, create_product, get_product
from src.services.discounts import Discount, list_discounts_for_product
from src.ui.catalog import (
    BACK_ROUTE,
    EDIT_PRODUCT_ROUTE,
    NEW_DISCOUNT_ROUTE,
    NEW_PRODUCT_ROUTE,
    QUIT_ROUTE,
    SELECT_ROUTE,
    CatalogController,
    _dispatch_route,
    _handle_edit_product,
    _handle_new_discount,
    _handle_new_product,
    _prompt_product_fields,
    _render,
    _render_discounts,
    _run_discount_screen,
    _show_message,
    run_catalog_screen,
)
from src.ui.keybindings import EntryCancelled

RICE = Product(id=1, name="Rice 1kg", price=60, gst_rate=5)
SUGAR = Product(id=2, name="Sugar 1kg", price=45, gst_rate=5)
ATTA = Product(id=3, name="Atta 5kg", price=250, gst_rate=0)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Real (throwaway file-backed) SQLite connection — mirrors
    `tests/unit/services/test_catalog.py`'s fixture. Handler functions call
    real `src.services.catalog`/`src.services.discounts` functions, so a
    real schema-backed connection exercises the same write paths those
    services' own tests already cover, without re-mocking the service
    layer here.
    """
    connection = connect(tmp_path / "data.db")
    init_schema(connection)
    yield connection
    connection.close()


# --- construction / empty state ------------------------------------------------


def test_controller_when_constructed_with_no_products_selected_product_is_none() -> (
    None
):
    controller = CatalogController()

    assert controller.selected_index == 0
    assert controller.selected_product() is None


def test_controller_when_constructed_with_products_defaults_to_first_selected() -> None:
    controller = CatalogController([RICE, SUGAR])

    assert controller.selected_product() == RICE


# --- refresh --------------------------------------------------------------------


def test_refresh_when_selection_still_in_range_preserves_index() -> None:
    controller = CatalogController([RICE, SUGAR, ATTA])
    controller.move_down()

    controller.refresh([RICE, SUGAR, ATTA])

    assert controller.selected_index == 1


def test_refresh_when_list_shrinks_below_selection_clamps_index() -> None:
    controller = CatalogController([RICE, SUGAR, ATTA])
    controller.move_down()
    controller.move_down()

    controller.refresh([RICE])

    assert controller.selected_index == 0
    assert controller.selected_product() == RICE


def test_refresh_when_list_becomes_empty_selected_product_is_none() -> None:
    controller = CatalogController([RICE])

    controller.refresh([])

    assert controller.selected_product() is None


# --- arrow/tab navigation dispatch ----------------------------------------------


def test_dispatch_when_down_arrow_moves_selection_forward() -> None:
    controller = CatalogController([RICE, SUGAR])

    route = controller.dispatch(curses.KEY_DOWN)

    assert route is None
    assert controller.selected_index == 1


def test_dispatch_when_down_arrow_wraps_past_last_option() -> None:
    controller = CatalogController([RICE, SUGAR])
    controller.dispatch(curses.KEY_DOWN)

    route = controller.dispatch(curses.KEY_DOWN)

    assert route is None
    assert controller.selected_index == 0


def test_dispatch_when_up_arrow_on_empty_list_does_not_raise() -> None:
    controller = CatalogController()

    route = controller.dispatch(curses.KEY_UP)

    assert route is None
    assert controller.selected_index == 0


def test_dispatch_when_tab_moves_selection_forward_like_down_arrow() -> None:
    controller = CatalogController([RICE, SUGAR])

    controller.dispatch(ord("\t"))

    assert controller.selected_index == 1


# --- select / back / quit dispatch ----------------------------------------------


def test_dispatch_when_enter_returns_select_route() -> None:
    controller = CatalogController([RICE])

    assert controller.dispatch(ord("\n")) == SELECT_ROUTE


def test_dispatch_when_esc_returns_back_route() -> None:
    controller = CatalogController([RICE])

    assert controller.dispatch(27) == BACK_ROUTE


def test_dispatch_when_q_returns_quit_route() -> None:
    controller = CatalogController([RICE])

    assert controller.dispatch(ord("q")) == QUIT_ROUTE


# --- catalog-specific shortcuts --------------------------------------------------


def test_dispatch_when_n_returns_new_product_route() -> None:
    controller = CatalogController([RICE])

    assert controller.dispatch(ord("n")) == NEW_PRODUCT_ROUTE


def test_dispatch_when_e_returns_edit_product_route() -> None:
    controller = CatalogController([RICE])

    assert controller.dispatch(ord("e")) == EDIT_PRODUCT_ROUTE


def test_dispatch_when_d_returns_new_discount_route() -> None:
    controller = CatalogController([RICE])

    assert controller.dispatch(ord("d")) == NEW_DISCOUNT_ROUTE


def test_dispatch_when_unrecognized_key_returns_none_and_does_not_move_selection() -> (
    None
):
    controller = CatalogController([RICE, SUGAR])

    route = controller.dispatch(ord("z"))

    assert route is None
    assert controller.selected_index == 0


# --- _render_discounts (pure render, no raw curses input) -----------------------


class _FakeWindow:
    """Minimal curses.window stand-in — records `addstr` text, no raw I/O.

    `_render_discounts`/`_render`/`_show_message`/the route-dispatch loop
    functions only call `clear`/`addstr`/`refresh`/`getch` — single-keycode
    reads, not raw text capture — so this control-flow path is
    unit-testable without a real terminal by feeding `getch` a
    predetermined key sequence. Only `_prompt_field`'s `getstr` (and its
    `curses.echo()`/`curses.noecho()` pairing) requires actual terminal
    text-capture and remains out of this module's testing boundary, per
    the module docstring.
    """

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


def test_render_discounts_when_flat_discount_exceeds_current_price_shows_degraded_state_and_does_not_raise() -> (  # noqa: E501
    None
):
    """Tier 2 gate Finding 1 / SEV-001 regression: a flat discount larger
    than the product's current price must not crash the render loop."""
    stdscr = _FakeWindow()
    product = Product(id=1, name="Rice 1kg", price=50, gst_rate=5)
    discount = Discount(id=1, product_id=1, discount_type="flat", value=150)

    _render_discounts(cast("curses.window", stdscr), product, [discount])

    assert any("no longer valid at current price" in line for line in stdscr.lines)


def test_render_discounts_when_discount_within_price_shows_discounted_value() -> None:
    stdscr = _FakeWindow()
    product = Product(id=1, name="Rice 1kg", price=100, gst_rate=5)
    discount = Discount(id=1, product_id=1, discount_type="flat", value=15)

    _render_discounts(cast("curses.window", stdscr), product, [discount])

    assert any("85.00" in line for line in stdscr.lines)


# --- .products property / move_up (action 10 coverage) --------------------------


def test_products_property_returns_current_list() -> None:
    controller = CatalogController([RICE, SUGAR])

    assert controller.products == [RICE, SUGAR]


def test_move_up_when_products_present_moves_selection_backward_and_wraps() -> None:
    controller = CatalogController([RICE, SUGAR, ATTA])

    route = controller.dispatch(curses.KEY_UP)

    assert route is None
    assert controller.selected_index == 2


# --- _render (pure render, no raw curses input) ----------------------------------


def test_render_when_products_present_lists_each_with_selected_prefix() -> None:
    stdscr = _FakeWindow()
    controller = CatalogController([RICE, SUGAR])
    controller.move_down()

    _render(cast("curses.window", stdscr), controller)

    assert any(line.startswith("  Rice 1kg") for line in stdscr.lines)
    assert any(line.startswith("> Sugar 1kg") for line in stdscr.lines)


def test_render_when_product_has_known_cost_shows_it_in_the_row() -> None:
    stdscr = _FakeWindow()
    product = Product(id=1, name="Rice 1kg", price=60.0, gst_rate=5.0, cost=45.0)
    controller = CatalogController([product])

    _render(cast("curses.window", stdscr), controller)

    assert any("cost=45.0" in line for line in stdscr.lines)


def test_render_when_product_has_no_cost_shows_unknown_marker_not_none_or_zero() -> (
    None
):
    """ADR-009 Decision (a): `None` cost means "unknown", never zero — the
    list row must not render `None` or `0`, mirroring the form's own
    "blank = unknown" convention (`src/ui/catalog.py:157`)."""
    stdscr = _FakeWindow()
    controller = CatalogController([RICE])

    _render(cast("curses.window", stdscr), controller)

    assert any("cost=unknown" in line for line in stdscr.lines)
    assert not any("cost=None" in line for line in stdscr.lines)
    assert not any("cost=0" in line for line in stdscr.lines)


def test_render_when_no_products_shows_placeholder_message() -> None:
    stdscr = _FakeWindow()
    controller = CatalogController()

    _render(cast("curses.window", stdscr), controller)

    assert any("no products yet" in line for line in stdscr.lines)


# --- _show_message (single getch, no raw text capture) ---------------------------


def test_show_message_writes_message_and_waits_for_a_keypress() -> None:
    stdscr = _FakeWindow(keys=[ord("x")])

    _show_message(cast("curses.window", stdscr), 6, "Error: bad input")

    assert stdscr.lines == ["Error: bad input"]
    assert stdscr._keys == []


def _keys(text: str) -> list[int]:
    return [ord(c) for c in text]


# --- _prompt_product_fields (action 11: edit pre-fill / action 12: cancel) ------
# Exercises the real raw-keycode capture path (`read_line`) via `_FakeWindow`'s
# predetermined key queue — no `_prompt_product_fields` monkeypatch here,
# unlike the handler tests below.


def test_prompt_product_fields_when_editing_shows_current_values_as_defaults() -> None:
    product = Product(id=1, name="Rice 1kg", price=60.0, gst_rate=5.0)
    stdscr = _FakeWindow(keys=[ord("\n"), ord("\n"), ord("\n"), ord("\n")])

    fields = _prompt_product_fields(cast("curses.window", stdscr), existing=product)

    assert fields == ("Rice 1kg", 60.0, 5.0, None)
    assert any("Name [Rice 1kg]" in line for line in stdscr.lines)
    assert any("Price [60.0]" in line for line in stdscr.lines)
    assert any("GST rate [5.0]" in line for line in stdscr.lines)


def test_prompt_product_fields_when_editing_with_known_cost_shows_it_as_default() -> (
    None
):
    """ADR-009 Decision (a) — a non-`None` `existing.cost` pre-fills the
    cost field's editable default, mirroring name/price/gst_rate."""
    product = Product(id=1, name="Rice 1kg", price=60.0, gst_rate=5.0, cost=45.0)
    stdscr = _FakeWindow(keys=[ord("\n"), ord("\n"), ord("\n"), ord("\n")])

    fields = _prompt_product_fields(cast("curses.window", stdscr), existing=product)

    assert fields == ("Rice 1kg", 60.0, 5.0, 45.0)
    assert any("Cost (blank = unknown) [45.0]" in line for line in stdscr.lines)


def test_prompt_product_fields_when_editing_and_field_retyped_overrides_default() -> (
    None
):
    product = Product(id=1, name="Rice 1kg", price=60.0, gst_rate=5.0)
    stdscr = _FakeWindow(
        keys=[*_keys("Rice 2kg"), ord("\n"), ord("\n"), ord("\n"), ord("\n")],
    )

    fields = _prompt_product_fields(cast("curses.window", stdscr), existing=product)

    assert fields == ("Rice 2kg", 60.0, 5.0, None)


def test_prompt_product_fields_when_creating_shows_no_bracketed_default() -> None:
    keys = [
        *_keys("Rice"),
        ord("\n"),
        *_keys("60"),
        ord("\n"),
        *_keys("5"),
        ord("\n"),
        ord("\n"),
    ]
    stdscr = _FakeWindow(keys=keys)

    fields = _prompt_product_fields(cast("curses.window", stdscr))

    assert fields == ("Rice", 60.0, 5.0, None)
    assert any(line == "Name: " for line in stdscr.lines)
    assert not any(line.startswith("Name [") for line in stdscr.lines)


def test_prompt_product_fields_when_creating_and_cost_typed_returns_it() -> None:
    """ADR-009 Decision (a) — a typed cost value on the create path maps to
    the given float, not `None`."""
    keys = [
        *_keys("Rice"),
        ord("\n"),
        *_keys("60"),
        ord("\n"),
        *_keys("5"),
        ord("\n"),
        *_keys("40"),
        ord("\n"),
    ]
    stdscr = _FakeWindow(keys=keys)

    fields = _prompt_product_fields(cast("curses.window", stdscr))

    assert fields == ("Rice", 60.0, 5.0, 40.0)


def test_prompt_product_fields_when_esc_pressed_on_first_field_raises_entry_cancelled() -> (  # noqa: E501
    None
):
    stdscr = _FakeWindow(keys=[*_keys("Ri"), 27])

    with pytest.raises(EntryCancelled):
        _prompt_product_fields(cast("curses.window", stdscr))


# --- _handle_new_product / _handle_edit_product / _handle_new_discount ----------
# `_prompt_product_fields`/`_prompt_discount_fields` are monkeypatched at the
# module boundary — the raw-curses-input capture inside them is out of this
# module's testing boundary, but the handler's own control flow (dispatch to
# the service layer, degrade on `None`/domain-exception) is not and is
# exercised here against a real schema-backed connection.


def test_handle_new_product_when_fields_valid_creates_product(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "src.ui.catalog._prompt_product_fields",
        lambda stdscr, existing=None: ("Rice 1kg", 60.0, 5.0, None),
    )
    stdscr = _FakeWindow()

    _handle_new_product(cast("curses.window", stdscr), conn)

    row = conn.execute("SELECT name, price, gst_rate FROM products").fetchone()
    assert row == ("Rice 1kg", 60.0, 5.0)


def test_handle_new_product_when_cost_given_persists_cost(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-009 Decision (a) — a cost value returned by
    `_prompt_product_fields` is threaded through to `create_product` and
    persisted."""
    monkeypatch.setattr(
        "src.ui.catalog._prompt_product_fields",
        lambda stdscr, existing=None: ("Rice 1kg", 60.0, 5.0, 45.0),
    )
    stdscr = _FakeWindow()

    _handle_new_product(cast("curses.window", stdscr), conn)

    (cost,) = conn.execute("SELECT cost FROM products").fetchone()
    assert cost == 45.0


def test_handle_new_product_when_fields_invalid_shows_message_and_does_not_write(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "src.ui.catalog._prompt_product_fields", lambda stdscr, existing=None: None
    )
    stdscr = _FakeWindow(keys=[ord("x")])

    _handle_new_product(cast("curses.window", stdscr), conn)

    assert any("Invalid input" in line for line in stdscr.lines)
    assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0


def test_handle_new_product_when_catalog_error_raised_shows_error_message(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "src.ui.catalog._prompt_product_fields",
        lambda stdscr, existing=None: (
            "Odd Item",
            10.0,
            15.0,  # 15% not a valid GST slab
            None,
        ),
    )
    stdscr = _FakeWindow(keys=[ord("x")])

    _handle_new_product(cast("curses.window", stdscr), conn)

    assert any(line.startswith("Error:") for line in stdscr.lines)
    assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0


def test_handle_new_product_when_esc_cancels_no_mutation_and_no_invalid_message(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise_cancelled(stdscr: object, existing: Product | None = None) -> None:
        raise EntryCancelled

    monkeypatch.setattr("src.ui.catalog._prompt_product_fields", _raise_cancelled)
    stdscr = _FakeWindow()

    _handle_new_product(cast("curses.window", stdscr), conn)

    assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0
    assert not any("Invalid input" in line for line in stdscr.lines)


def test_handle_edit_product_when_fields_valid_updates_product(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    monkeypatch.setattr(
        "src.ui.catalog._prompt_product_fields",
        lambda stdscr, existing=None: ("Rice 1kg", 65.0, 5.0, None),
    )
    stdscr = _FakeWindow()

    _handle_edit_product(cast("curses.window", stdscr), conn, product)

    assert get_product(conn, product.id).price == 65.0


def test_handle_edit_product_when_cost_given_persists_cost(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-009 Decision (a) — a cost value returned by
    `_prompt_product_fields` is threaded through to `update_product` and
    persisted."""
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    monkeypatch.setattr(
        "src.ui.catalog._prompt_product_fields",
        lambda stdscr, existing=None: ("Rice 1kg", 60.0, 5.0, 38.0),
    )
    stdscr = _FakeWindow()

    _handle_edit_product(cast("curses.window", stdscr), conn, product)

    assert get_product(conn, product.id).cost == 38.0


def test_handle_edit_product_when_fields_invalid_shows_message(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    monkeypatch.setattr(
        "src.ui.catalog._prompt_product_fields", lambda stdscr, existing=None: None
    )
    stdscr = _FakeWindow(keys=[ord("x")])

    _handle_edit_product(cast("curses.window", stdscr), conn, product)

    assert any("Invalid input" in line for line in stdscr.lines)


def test_handle_edit_product_when_esc_cancels_no_mutation_and_no_invalid_message(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)

    def _raise_cancelled(stdscr: object, existing: Product | None = None) -> None:
        raise EntryCancelled

    monkeypatch.setattr("src.ui.catalog._prompt_product_fields", _raise_cancelled)
    stdscr = _FakeWindow()

    _handle_edit_product(cast("curses.window", stdscr), conn, product)

    assert get_product(conn, product.id).price == 60.0
    assert not any("Invalid input" in line for line in stdscr.lines)


def test_handle_new_discount_when_fields_valid_creates_discount(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    monkeypatch.setattr(
        "src.ui.catalog._prompt_discount_fields",
        lambda stdscr: ("flat", 10.0),
    )
    stdscr = _FakeWindow()

    _handle_new_discount(cast("curses.window", stdscr), conn, product)

    discounts = list_discounts_for_product(conn, product.id)
    assert len(discounts) == 1
    assert discounts[0].value == 10.0


def test_handle_new_discount_when_fields_invalid_shows_message(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    monkeypatch.setattr("src.ui.catalog._prompt_discount_fields", lambda stdscr: None)
    stdscr = _FakeWindow(keys=[ord("x")])

    _handle_new_discount(cast("curses.window", stdscr), conn, product)

    assert any("Invalid input" in line for line in stdscr.lines)
    assert list_discounts_for_product(conn, product.id) == []


def test_handle_new_discount_when_esc_cancels_no_mutation_and_no_invalid_message(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)

    def _raise_cancelled(stdscr: object) -> None:
        raise EntryCancelled

    monkeypatch.setattr("src.ui.catalog._prompt_discount_fields", _raise_cancelled)
    stdscr = _FakeWindow()

    _handle_new_discount(cast("curses.window", stdscr), conn, product)

    assert list_discounts_for_product(conn, product.id) == []
    assert not any("Invalid input" in line for line in stdscr.lines)


def test_handle_new_discount_when_discount_error_raised_shows_error_message(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    monkeypatch.setattr(
        "src.ui.catalog._prompt_discount_fields",
        lambda stdscr: ("invalid-type", 10.0),
    )
    stdscr = _FakeWindow(keys=[ord("x")])

    _handle_new_discount(cast("curses.window", stdscr), conn, product)

    assert any(line.startswith("Error:") for line in stdscr.lines)


# --- _run_discount_screen / _dispatch_route / run_catalog_screen ----------------
# All raw input in this section is a single `getch()` keycode (mockable via
# `_FakeWindow`'s key queue) — not raw text capture — so it is inside this
# module's testing boundary.


def test_run_discount_screen_when_d_then_other_key_adds_discount_then_returns(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    monkeypatch.setattr(
        "src.ui.catalog._prompt_discount_fields", lambda stdscr: ("flat", 5.0)
    )
    stdscr = _FakeWindow(keys=[ord("d"), ord("q")])

    _run_discount_screen(cast("curses.window", stdscr), conn, product)

    assert len(list_discounts_for_product(conn, product.id)) == 1


def test_dispatch_route_new_product_route_invokes_handler(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "src.ui.catalog._prompt_product_fields",
        lambda stdscr, existing=None: ("Rice 1kg", 60.0, 5.0, None),
    )
    controller = CatalogController()
    stdscr = _FakeWindow()

    _dispatch_route(cast("curses.window", stdscr), conn, controller, NEW_PRODUCT_ROUTE)

    assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 1


def test_dispatch_route_when_no_product_selected_is_a_no_op(
    conn: sqlite3.Connection,
) -> None:
    controller = CatalogController()
    stdscr = _FakeWindow()

    _dispatch_route(cast("curses.window", stdscr), conn, controller, EDIT_PRODUCT_ROUTE)

    assert stdscr.lines == []


def test_dispatch_route_edit_product_route_invokes_handler(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    monkeypatch.setattr(
        "src.ui.catalog._prompt_product_fields",
        lambda stdscr, existing=None: ("Rice 1kg", 75.0, 5.0, None),
    )
    controller = CatalogController([product])
    stdscr = _FakeWindow()

    _dispatch_route(cast("curses.window", stdscr), conn, controller, EDIT_PRODUCT_ROUTE)

    assert get_product(conn, product.id).price == 75.0


def test_dispatch_route_select_route_invokes_discount_screen(
    conn: sqlite3.Connection,
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    controller = CatalogController([product])
    stdscr = _FakeWindow(keys=[ord("q")])

    _dispatch_route(cast("curses.window", stdscr), conn, controller, SELECT_ROUTE)

    assert any("Discounts for Rice 1kg" in line for line in stdscr.lines)


def test_dispatch_route_new_discount_route_invokes_handler(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    product = create_product(conn, name="Rice 1kg", price=60, gst_rate=5)
    monkeypatch.setattr(
        "src.ui.catalog._prompt_discount_fields", lambda stdscr: ("flat", 5.0)
    )
    controller = CatalogController([product])
    stdscr = _FakeWindow()

    _dispatch_route(cast("curses.window", stdscr), conn, controller, NEW_DISCOUNT_ROUTE)

    assert len(list_discounts_for_product(conn, product.id)) == 1


def test_run_catalog_screen_when_back_key_returns_immediately(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(curses, "curs_set", lambda visibility: None)
    stdscr = _FakeWindow(keys=[27])  # Esc -> BACK_ROUTE

    run_catalog_screen(cast("curses.window", stdscr), conn)

    assert any(line == "Catalog" for line in stdscr.lines)


def test_run_catalog_screen_when_unrecognized_key_then_quit_loops_then_exits(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(curses, "curs_set", lambda visibility: None)
    stdscr = _FakeWindow(keys=[ord("z"), ord("q")])

    run_catalog_screen(cast("curses.window", stdscr), conn)

    assert stdscr._keys == []
