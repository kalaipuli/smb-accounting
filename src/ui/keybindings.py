"""Shared key-binding dispatch for curses screens.

This module centralizes the generic navigation key vocabulary (arrows, Tab,
Enter, Esc, q) so every curses screen in the application — main menu now,
Catalog and Inventory screens later (Phase 1 Features 3/4) — resolves raw
`stdscr.getch()` key codes through the same table instead of re-implementing
key comparisons per screen.

Screen-specific fast-access shortcuts (e.g. the main menu's "record a sale"
single-key jump) are NOT part of this table — they are resolved by the
consuming screen before falling back to `resolve_action`, since they are not
generic navigation semantics shared across screens.

Also centralizes `read_line`/`EntryCancelled` — the shared raw-keycode
field-entry primitive (Catalog and Inventory screens both need
backspace-editing and Esc-to-cancel, which `stdscr.getstr()`'s buffered
line editing cannot express) — for the same reason: one implementation,
not a re-implementation per screen.
"""

import curses
from dataclasses import dataclass
from enum import Enum, auto


class NavAction(Enum):
    """Generic navigation intents shared by every curses screen."""

    MOVE_UP = auto()
    MOVE_DOWN = auto()
    NEXT_FIELD = auto()
    SELECT = auto()
    BACK = auto()
    QUIT = auto()


_KEY_DISPATCH: dict[int, NavAction] = {
    curses.KEY_UP: NavAction.MOVE_UP,
    curses.KEY_DOWN: NavAction.MOVE_DOWN,
    ord("\t"): NavAction.NEXT_FIELD,
    ord("\n"): NavAction.SELECT,
    ord("\r"): NavAction.SELECT,
    curses.KEY_ENTER: NavAction.SELECT,
    27: NavAction.BACK,  # ESC has no dedicated curses.KEY_* constant
    ord("q"): NavAction.QUIT,
    ord("Q"): NavAction.QUIT,
}


def resolve_action(key: int) -> NavAction | None:
    """Resolve a raw `stdscr.getch()` key code into a generic nav action.

    Returns None when the key is not part of the shared navigation
    vocabulary — callers should fall back to their own screen-specific
    handling (e.g. fast-access shortcuts) before discarding the key.
    """
    return _KEY_DISPATCH.get(key)


class EntryCancelled(Exception):
    """Raised by `read_line` when the user presses Esc during field entry.

    Callers MUST treat this as a distinct path from invalid input: no
    mutation, no error message — abort the entry/edit and return to the
    prior screen.
    """


def read_line(stdscr: "curses.window", row: int, col: int, default: str = "") -> str:
    """Character-by-character raw text capture shared by every curses
    screen's field-entry prompts (Catalog's new/edit product and new
    discount forms, Inventory's adjust-stock delta prompt).

    Supports backspace and Esc-to-cancel — neither is expressible through
    `stdscr.getstr()`'s buffered line-editing, which is why every prompt
    reads one raw keycode at a time via this helper instead.

    Returns the typed text. If the user presses Enter without typing
    anything, returns `default` — this lets an edit prompt seeded with the
    entity's current value be accepted unchanged by pressing Enter.

    Raises `EntryCancelled` if the user presses Esc before Enter.
    """
    buf = ""
    while True:
        stdscr.addstr(row, col, buf + " ")
        stdscr.refresh()
        key = stdscr.getch()
        if key == 27:  # Esc — no dedicated curses.KEY_* constant
            raise EntryCancelled
        if key in (curses.KEY_ENTER, ord("\n"), ord("\r")):
            return buf if buf else default
        if key in (curses.KEY_BACKSPACE, 127, 8):
            buf = buf[:-1]
            continue
        if 32 <= key <= 126:
            buf += chr(key)


@dataclass(frozen=True)
class FormField:
    """One field's static definition: internal key, display label, and
    optional pre-filled starting value (edit-in-place default, same
    semantics as `read_line`'s own `default` parameter)."""

    name: str
    label: str
    default: str = ""


class FormController:
    """Pure navigation + per-field text-buffer state over a fixed field
    list — no curses I/O. Mirrors `CatalogController`/`InventoryController`'s
    existing pure-state pattern so it is unit-testable via a fake key queue,
    with no real terminal, exactly like those two classes already are.

    Holds what a single `read_line()` call's local `buf` variable held for
    one field, now keyed per field and retained across repeated
    `handle_key` calls — the per-field buffer no longer dies when the next
    field's prompt begins, because there is no "next field's prompt" as a
    separate blocking call anymore; it is the same loop, same object,
    advancing its field index.

    Per ADR-007 Decision (a): `handle_key` deliberately does NOT route
    through `resolve_action()` — `resolve_action`'s `'q'`/`'Q'` → `QUIT`
    mapping is correct for list screens (no free-text entry there) but
    would be wrong here, since a form field must accept the literal
    character 'q' as typed text.
    """

    def __init__(self, fields: list[FormField]) -> None:
        self._fields: list[FormField] = list(fields)
        self._index = 0
        self._buffers: dict[str, str] = {
            field.name: field.default for field in self._fields
        }

    @property
    def fields(self) -> list[FormField]:
        return self._fields

    @property
    def index(self) -> int:
        return self._index

    def current_field(self) -> FormField:
        return self._fields[self._index]

    def buffer(self, name: str) -> str:
        return self._buffers[name]

    def values(self) -> dict[str, str]:
        return dict(self._buffers)

    def is_last_field(self) -> bool:
        return self._index == len(self._fields) - 1

    def move_next_field(self) -> None:
        if self._index < len(self._fields) - 1:
            self._index += 1

    def move_prev_field(self) -> None:
        if self._index > 0:
            self._index -= 1

    def handle_key(self, key: int) -> str | None:
        """One raw `stdscr.getch()` code in, one of `"submit"`/`"cancel"`/
        `None` out.

        Navigation keys (Up/Down/Tab/Shift-Tab/Enter/Esc) are checked
        first, exactly as `read_line` already checks Esc/Enter/backspace
        before falling through to literal-character handling. Every
        printable character 32-126 is treated as literal text for the
        current field's buffer, matching `read_line`'s own approach
        byte-for-byte.
        """
        if key == 27:  # Esc — no dedicated curses.KEY_* constant
            return "cancel"
        if key in (curses.KEY_ENTER, ord("\n"), ord("\r")):
            if self.is_last_field():
                return "submit"
            self.move_next_field()
            return None
        if key == curses.KEY_UP:
            self.move_prev_field()
            return None
        if key == curses.KEY_DOWN:
            self.move_next_field()
            return None
        if key == ord("\t"):
            self.move_next_field()
            return None
        btab = getattr(curses, "KEY_BTAB", None)
        if btab is not None and key == btab:
            self.move_prev_field()
            return None
        if key in (curses.KEY_BACKSPACE, 127, 8):
            name = self.current_field().name
            self._buffers[name] = self._buffers[name][:-1]
            return None
        if 32 <= key <= 126:
            name = self.current_field().name
            self._buffers[name] += chr(key)
            return None
        return None


def run_form(stdscr: "curses.window", fields: list[FormField]) -> dict[str, str]:
    """`read_line`'s multi-field counterpart — the thin render loop driving
    a `FormController`: `while True: render → stdscr.getch() → handle_key
    → act on result`, rendering every field each pass instead of one.

    Loops until `handle_key` returns `"submit"` (returns
    `controller.values()`) or `"cancel"` (raises `EntryCancelled`, the
    *same* exception `read_line` already raises).

    Per ADR-007 Decision (c): full-form redraw every keystroke, `"> "` /
    `"  "` focus marker matching `CatalogController._render`'s convention,
    `stdscr.move`-based cursor placement at the focused field's buffer end,
    one fixed row per field (`start_row + index`).
    """
    controller = FormController(fields)
    start_row = 0
    while True:
        for row_offset, field in enumerate(controller.fields):
            row = start_row + row_offset
            prefix = "> " if row_offset == controller.index else "  "
            value = controller.buffer(field.name)
            label = f"{prefix}{field.label}: "
            stdscr.addstr(row, 0, label + value + " ")
            if row_offset == controller.index:
                stdscr.move(row, len(label) + len(value))
        stdscr.refresh()
        key = stdscr.getch()
        result = controller.handle_key(key)
        if result == "submit":
            return controller.values()
        if result == "cancel":
            raise EntryCancelled
