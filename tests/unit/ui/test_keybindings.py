"""Unit tests for the shared key-binding dispatch table and the shared
raw-text-entry primitives (`read_line`/`EntryCancelled`,
`FormField`/`FormController`/`run_form`, ADR-007).

The `FormController`/`run_form` tests here are a minimal smoke check
proving the primitive itself works — full navigation-matrix coverage
(forward/backward, edit-in-place, Esc-cancels-whole-form,
Enter-submits-on-last-field) is `P2-F4-T3`'s job (tester, next turn)."""

import curses
from typing import cast

import pytest

from src.ui.keybindings import (
    EntryCancelled,
    FormController,
    FormField,
    NavAction,
    read_line,
    resolve_action,
    run_form,
)


def test_resolve_action_when_key_up_returns_move_up() -> None:
    assert resolve_action(curses.KEY_UP) is NavAction.MOVE_UP


def test_resolve_action_when_key_down_returns_move_down() -> None:
    assert resolve_action(curses.KEY_DOWN) is NavAction.MOVE_DOWN


def test_resolve_action_when_tab_returns_next_field() -> None:
    assert resolve_action(ord("\t")) is NavAction.NEXT_FIELD


def test_resolve_action_when_enter_newline_returns_select() -> None:
    assert resolve_action(ord("\n")) is NavAction.SELECT


def test_resolve_action_when_key_enter_constant_returns_select() -> None:
    assert resolve_action(curses.KEY_ENTER) is NavAction.SELECT


def test_resolve_action_when_esc_returns_back() -> None:
    assert resolve_action(27) is NavAction.BACK


def test_resolve_action_when_lowercase_q_returns_quit() -> None:
    assert resolve_action(ord("q")) is NavAction.QUIT


def test_resolve_action_when_uppercase_q_returns_quit() -> None:
    assert resolve_action(ord("Q")) is NavAction.QUIT


def test_resolve_action_when_unrecognized_key_returns_none() -> None:
    assert resolve_action(ord("z")) is None


# --- read_line / EntryCancelled --------------------------------------------------


class _FakeWindow:
    """Minimal curses.window stand-in — records `addstr` text, no raw I/O."""

    def __init__(self, keys: list[int] | None = None) -> None:
        self.lines: list[str] = []
        self._keys: list[int] = list(keys) if keys is not None else []

    def addstr(self, row: int, col: int, text: str) -> None:
        self.lines.append(text)

    def refresh(self) -> None:
        pass

    def getch(self) -> int:
        return self._keys.pop(0)


def _keys(text: str) -> list[int]:
    return [ord(c) for c in text]


def test_read_line_when_text_typed_then_enter_returns_typed_text() -> None:
    stdscr = _FakeWindow(keys=[*_keys("Rice"), ord("\n")])

    result = read_line(cast("curses.window", stdscr), 2, 6)

    assert result == "Rice"


def test_read_line_when_enter_pressed_with_empty_buffer_returns_default() -> None:
    stdscr = _FakeWindow(keys=[ord("\n")])

    result = read_line(cast("curses.window", stdscr), 2, 6, default="Rice 1kg")

    assert result == "Rice 1kg"


def test_read_line_when_backspace_removes_last_typed_character() -> None:
    stdscr = _FakeWindow(
        keys=[*_keys("Ricd"), curses.KEY_BACKSPACE, ord("e"), ord("\n")]
    )

    result = read_line(cast("curses.window", stdscr), 2, 6)

    assert result == "Rice"


def test_read_line_when_esc_pressed_raises_entry_cancelled() -> None:
    stdscr = _FakeWindow(keys=[*_keys("Ri"), 27])

    with pytest.raises(EntryCancelled):
        read_line(cast("curses.window", stdscr), 2, 6)


# --- FormField / FormController / run_form (smoke tests, ADR-007) ----------------


def _fields() -> list[FormField]:
    return [
        FormField(name="name", label="Name"),
        FormField(name="phone", label="Phone", default="9999999999"),
    ]


def test_form_controller_starts_on_first_field_with_defaults_as_buffers() -> None:
    controller = FormController(_fields())

    assert controller.index == 0
    assert controller.current_field().name == "name"
    assert controller.buffer("phone") == "9999999999"


def test_form_controller_move_next_field_advances_index() -> None:
    controller = FormController(_fields())

    controller.move_next_field()

    assert controller.index == 1
    assert controller.is_last_field()


def test_form_controller_move_next_field_is_noop_past_last_field() -> None:
    controller = FormController(_fields())
    controller.move_next_field()

    controller.move_next_field()

    assert controller.index == 1


def test_form_controller_move_prev_field_is_noop_before_first_field() -> None:
    controller = FormController(_fields())

    controller.move_prev_field()

    assert controller.index == 0


def test_form_controller_handle_key_up_moves_focus_backward() -> None:
    controller = FormController(_fields())
    controller.move_next_field()

    result = controller.handle_key(curses.KEY_UP)

    assert result is None
    assert controller.index == 0


def test_form_controller_handle_key_down_moves_focus_forward() -> None:
    controller = FormController(_fields())

    result = controller.handle_key(curses.KEY_DOWN)

    assert result is None
    assert controller.index == 1


def test_form_controller_handle_key_literal_char_appends_to_current_buffer() -> None:
    controller = FormController(_fields())

    controller.handle_key(ord("q"))

    assert controller.buffer("name") == "q"


def test_form_controller_handle_key_backspace_removes_last_char() -> None:
    controller = FormController(_fields())
    controller.handle_key(ord("A"))
    controller.handle_key(ord("B"))

    controller.handle_key(curses.KEY_BACKSPACE)

    assert controller.buffer("name") == "A"


def test_form_controller_handle_key_enter_on_non_last_field_advances_focus() -> None:
    controller = FormController(_fields())

    result = controller.handle_key(ord("\n"))

    assert result is None
    assert controller.index == 1


def test_form_controller_handle_key_enter_on_last_field_submits() -> None:
    controller = FormController(_fields())
    controller.move_next_field()

    result = controller.handle_key(ord("\n"))

    assert result == "submit"


def test_form_controller_handle_key_esc_cancels_from_any_field() -> None:
    controller = FormController(_fields())

    result = controller.handle_key(27)

    assert result == "cancel"


def test_form_controller_values_returns_all_field_buffers() -> None:
    controller = FormController(_fields())
    controller.handle_key(ord("x"))

    assert controller.values() == {"name": "x", "phone": "9999999999"}


class _FakeFormWindow:
    """Minimal curses.window stand-in for `run_form` — records `addstr`
    calls, no raw I/O."""

    def __init__(self, keys: list[int]) -> None:
        self._keys: list[int] = list(keys)
        self.moved: list[tuple[int, int]] = []

    def addstr(self, row: int, col: int, text: str) -> None:
        pass

    def move(self, row: int, col: int) -> None:
        self.moved.append((row, col))

    def refresh(self) -> None:
        pass

    def getch(self) -> int:
        return self._keys.pop(0)


def test_run_form_when_enter_on_last_field_returns_typed_values() -> None:
    stdscr = _FakeFormWindow(keys=[*_keys("Rita"), ord("\n"), *_keys("Q"), ord("\n")])

    result = run_form(cast("curses.window", stdscr), _fields())

    assert result == {"name": "Rita", "phone": "9999999999Q"}


def test_run_form_when_esc_pressed_raises_entry_cancelled() -> None:
    stdscr = _FakeFormWindow(keys=[*_keys("Ri"), 27])

    with pytest.raises(EntryCancelled):
        run_form(cast("curses.window", stdscr), _fields())


# --- FormController full navigation-matrix coverage (P2-F4-T3) -------------------
#
# Everything above this line is Turn 2's smoke coverage (kept, not duplicated).
# Everything below closes the exhaustive matrix the task DoD requires:
# forward/backward navigation through every field, edit-in-place persistence,
# Esc-cancels-the-whole-form (not just the current field), and
# Enter-submits-only-on-the-true-last-field — plus the Tab / Shift-Tab
# (KEY_BTAB-unavailable degrade) paths the smoke tests did not exercise.


def _three_fields() -> list[FormField]:
    return [
        FormField(name="a", label="A"),
        FormField(name="b", label="B"),
        FormField(name="c", label="C"),
    ]


# --- forward navigation ------------------------------------------------------


def test_form_controller_handle_key_tab_advances_focus() -> None:
    controller = FormController(_fields())

    result = controller.handle_key(ord("\t"))

    assert result is None
    assert controller.index == 1


def test_form_controller_down_key_traverses_every_field_forward() -> None:
    controller = FormController(_three_fields())

    controller.handle_key(curses.KEY_DOWN)
    assert controller.index == 1
    controller.handle_key(curses.KEY_DOWN)
    assert controller.index == 2
    controller.handle_key(curses.KEY_DOWN)  # past last field: no-op

    assert controller.index == 2


def test_form_controller_tab_key_traverses_every_field_forward() -> None:
    controller = FormController(_three_fields())

    for _ in range(3):
        controller.handle_key(ord("\t"))

    assert controller.index == 2


# --- backward navigation ------------------------------------------------------


def test_form_controller_up_key_traverses_every_field_backward() -> None:
    controller = FormController(_three_fields())
    controller.move_next_field()
    controller.move_next_field()

    controller.handle_key(curses.KEY_UP)
    assert controller.index == 1
    controller.handle_key(curses.KEY_UP)
    assert controller.index == 0
    controller.handle_key(curses.KEY_UP)  # before first field: no-op

    assert controller.index == 0


def test_form_controller_shift_tab_moves_focus_backward_when_key_btab_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(curses, "KEY_BTAB", 353, raising=False)
    controller = FormController(_three_fields())
    controller.move_next_field()
    controller.move_next_field()

    result = controller.handle_key(353)

    assert result is None
    assert controller.index == 1


def test_form_controller_shift_tab_key_degrades_to_noop_when_curses_lacks_key_btab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(curses, "KEY_BTAB", raising=False)
    controller = FormController(_three_fields())
    controller.move_next_field()

    # 353 is KEY_BTAB's usual raw code on platforms that define it; with the
    # attribute absent this must degrade to a harmless no-op, not a crash and
    # not a stray literal-character append (353 is outside 32-126 anyway).
    result = controller.handle_key(353)

    assert result is None
    assert controller.index == 1


# --- edit-in-place -------------------------------------------------------------


def test_form_controller_edit_in_place_persists_after_navigate_away_and_back() -> None:
    controller = FormController(_three_fields())
    controller.move_next_field()  # field "b"

    controller.handle_key(ord("X"))
    controller.handle_key(ord("Y"))
    controller.move_next_field()  # field "c"
    controller.move_prev_field()  # back to field "b"

    assert controller.buffer("b") == "XY"
    assert controller.index == 1


# --- Esc cancels the whole form, not just the current field --------------------


def test_form_controller_esc_cancels_from_a_non_first_field() -> None:
    controller = FormController(_three_fields())
    controller.move_next_field()
    controller.handle_key(ord("p"))

    result = controller.handle_key(27)

    assert result == "cancel"


def test_run_form_esc_after_navigating_and_editing_cancels_whole_form() -> None:
    """Esc must abort the entire form, including buffered edits already made
    on earlier fields — not just discard the currently-focused field."""
    stdscr = _FakeFormWindow(keys=[*_keys("Ri"), curses.KEY_DOWN, *_keys("12"), 27])

    with pytest.raises(EntryCancelled):
        run_form(cast("curses.window", stdscr), _fields())


# --- Enter: advance vs submit ---------------------------------------------------


def test_form_controller_enter_only_submits_on_the_true_last_field_of_three() -> None:
    controller = FormController(_three_fields())

    assert controller.handle_key(ord("\n")) is None
    assert controller.index == 1
    assert controller.handle_key(ord("\n")) is None
    assert controller.index == 2
    assert controller.handle_key(ord("\n")) == "submit"


def test_run_form_enter_advances_through_every_field_and_submits_on_the_last() -> None:
    stdscr = _FakeFormWindow(
        keys=[*_keys("A"), ord("\n"), *_keys("B"), ord("\n"), *_keys("C"), ord("\n")]
    )

    result = run_form(cast("curses.window", stdscr), _three_fields())

    assert result == {"a": "A", "b": "B", "c": "C"}
