"""Unit tests for `src/ui/main_menu.py`'s curses render loop (`_render`,
`run_main_menu`) — P3-F2-T9, raising this module's coverage to the >=90%
Phase 3 per-module floor (`docs/registry/phase3/README.md` §3), the
moment this feature wires the Reports entry point (P3-F2-T4).

`test_main_menu.py` already covers `MainMenuController`'s pure
navigation/dispatch state; this module covers the thin curses loop that
drives it — `_render`'s output and `run_main_menu`'s per-route dispatch
(sale entry / catalog / inventory / customers / sales history / reports /
unrecognized key / Esc / q), using `_FakeWindow` (a fake `getch()` key
queue, no real terminal) and monkeypatching each `run_*_screen` import so
this test module stays within `main_menu.py`'s own dispatch boundary
without re-exercising the screens it delegates to.
"""

import curses
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from src.db.schema import connect, init_schema
from src.ui.main_menu import MainMenuController, _render, run_main_menu


class _FakeWindow:
    """Minimal curses.window stand-in — records `addstr` text, no raw I/O.

    `run_main_menu` only calls `curses.curs_set`/`clear`/`addstr`/
    `refresh`/`getch` — mirrors `tests/unit/ui/test_catalog.py`'s
    `_FakeWindow`.
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


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "data.db")
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def _fake_curs_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """`run_main_menu` calls `curses.curs_set(0)`, which requires a real
    initialized terminal (`initscr()`) outside a real curses session.
    Patched to a no-op so the render loop is testable via `_FakeWindow`
    with no real terminal, mirroring `test_customers.py`/`test_catalog.py`'s
    approach of never touching real curses I/O."""
    monkeypatch.setattr(curses, "curs_set", lambda visibility: None)


# --- _render ---------------------------------------------------------------------


def test_render_lists_every_option_with_selected_prefix() -> None:
    stdscr = _FakeWindow()
    controller = MainMenuController()
    controller.move_down()

    _render(cast("curses.window", stdscr), controller)

    assert any(line.startswith("  Record a sale") for line in stdscr.lines)
    assert any(line.startswith("> Inventory") for line in stdscr.lines)


# --- run_main_menu: immediate back/quit -------------------------------------------


def test_run_main_menu_when_esc_returns_immediately(
    conn: sqlite3.Connection,
) -> None:
    stdscr = _FakeWindow(keys=[27])

    run_main_menu(cast("curses.window", stdscr), conn)


def test_run_main_menu_when_q_returns_immediately(conn: sqlite3.Connection) -> None:
    stdscr = _FakeWindow(keys=[ord("q")])

    run_main_menu(cast("curses.window", stdscr), conn)


def test_run_main_menu_when_unrecognized_key_then_quit_loops_then_exits(
    conn: sqlite3.Connection,
) -> None:
    stdscr = _FakeWindow(keys=[ord("z"), ord("q")])

    run_main_menu(cast("curses.window", stdscr), conn)


# --- run_main_menu: per-route dispatch ---------------------------------------------


def test_run_main_menu_sale_entry_route_invokes_sales_screen_then_quits(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "src.ui.main_menu.run_sales_screen",
        lambda stdscr, c: calls.append("sales"),
    )
    stdscr = _FakeWindow(keys=[ord("s"), ord("q")])

    run_main_menu(cast("curses.window", stdscr), conn)

    assert calls == ["sales"]


def test_run_main_menu_catalog_route_invokes_catalog_screen_then_quits(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "src.ui.main_menu.run_catalog_screen",
        lambda stdscr, c: calls.append("catalog"),
    )
    stdscr = _FakeWindow(keys=[ord("c"), ord("q")])

    run_main_menu(cast("curses.window", stdscr), conn)

    assert calls == ["catalog"]


def test_run_main_menu_inventory_route_invokes_inventory_screen_then_quits(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "src.ui.main_menu.run_inventory_screen",
        lambda stdscr, c: calls.append("inventory"),
    )
    stdscr = _FakeWindow(keys=[ord("i"), ord("q")])

    run_main_menu(cast("curses.window", stdscr), conn)

    assert calls == ["inventory"]


def test_run_main_menu_customers_route_invokes_customers_screen_then_quits(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "src.ui.main_menu.run_customers_screen",
        lambda stdscr, c: calls.append("customers"),
    )
    stdscr = _FakeWindow(keys=[ord("u"), ord("q")])

    run_main_menu(cast("curses.window", stdscr), conn)

    assert calls == ["customers"]


def test_run_main_menu_sales_history_route_invokes_sales_history_screen_then_quits(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "src.ui.main_menu.run_sales_history_screen",
        lambda stdscr, c: calls.append("history"),
    )
    stdscr = _FakeWindow(keys=[ord("h"), ord("q")])

    run_main_menu(cast("curses.window", stdscr), conn)

    assert calls == ["history"]


def test_run_main_menu_reports_route_invokes_reports_screen_then_quits(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "src.ui.main_menu.run_reports_screen",
        lambda stdscr, c: calls.append("reports"),
    )
    stdscr = _FakeWindow(keys=[ord("r"), ord("q")])

    run_main_menu(cast("curses.window", stdscr), conn)

    assert calls == ["reports"]


def test_run_main_menu_enter_on_default_selection_invokes_sale_entry_route(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "src.ui.main_menu.run_sales_screen",
        lambda stdscr, c: calls.append("sales"),
    )
    stdscr = _FakeWindow(keys=[ord("\n"), ord("q")])

    run_main_menu(cast("curses.window", stdscr), conn)

    assert calls == ["sales"]
