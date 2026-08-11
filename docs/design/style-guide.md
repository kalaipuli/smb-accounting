---
framework-file-class: project-owned
---

# Global Style Guide — SMB Account Software (curses CUI)

**Status:** Authored 2026-08-10 by ux-designer, `framework/spaces/phase3-structure.md` action 1 — the
project's first global style guide. It is not a new invention: every rule below is a conservative
transcription of conventions already shipping and repeated across `src/ui/main_menu.py`,
`src/ui/catalog.py`, `src/ui/inventory.py`, `src/ui/customers.py`, `src/ui/sales.py`, and
`src/ui/keybindings.py` as of Phase 1–2. No new token, color, or pattern is introduced here that does
not already exist in that code. Every subsequent UX proposal in this project MUST cite this document
by section (`style-guide.md#<section>`) rather than re-deriving any of the conventions below —
`framework/guidelines/ui-ux-rules.md` § Global Theme & Style System.

This document is a style guide for a **stdlib `curses` terminal UI**, not a web/GUI design system.
Sections below map onto the web-design categories (`ui-ux-rules.md` requires "color palette,
typography scale, spacing scale, and shared component style conventions") in the closest form a
`curses` CUI actually has — flagged explicitly at each section rather than silently invented, per
`ARCH-U03` ("no shared component library needed — `curses` stdlib widgets used directly, consistent
key bindings enforced by convention across screens").

---

## §1. Color Palette

**No color is used anywhere in `src/ui/` today.** No screen calls `curses.init_pair`,
`curses.color_pair`, `curses.start_color`, or `A_COLOR`. Every screen renders in the terminal's
default foreground/background with `curses.curs_set(0)` (cursor hidden except during text entry,
where the form/prompt positions it explicitly via `stdscr.move`).

- **Light/dark:** not applicable — the terminal's own color scheme (light or dark) is inherited
  unchanged; the application applies no palette on top of it.
- **Emphasis without color:** the one existing emphasis convention is the selection marker
  `"> "` / `"  "` prefix (§4), not a color or attribute change. `curses.A_REVERSE`/`A_BOLD` are not
  used anywhere in the codebase today.
- **Rule for new screens:** MUST NOT introduce `curses.init_pair`/color usage as a first user of
  color in this project without an ADR — `ARCH-U03`'s zero-extra-dependency, convention-driven
  design assumes a monochrome baseline, and `curses.start_color()` support varies across terminals
  the shop owner might run this in (`architecture.md` §8 names no terminal-capability floor).
  A Phase 3 reporting screen that needs to flag an abnormal condition (e.g. the cost-incomplete
  banner) MUST do so with a textual marker (§5), consistent with this section's baseline — not with
  color, which would be this project's first use and needs its own governance record.

## §2. Typography Scale

Not applicable in the web-design sense — `curses` renders whatever monospace font the user's
terminal emulator is configured with; the application has no font/weight/size control. The closest
equivalent convention already established is **line-role, not font**:

| Role | Convention (observed across every screen) |
|---|---|
| Screen title | Row 0, plain text, no decoration (`"Customers"`, `"Customer: {name}"`, `"SMB Account Software — Main Menu"`) |
| Key-binding legend | Row 1 (list/menu screens) or the line immediately after the title (detail screens) — always plain text describing every active key for that screen, e.g. `"Arrows/Tab move, Enter select, Esc/q back, shortcuts: ..."` |
| Body / list content | Content rows, one row per item/field |
| Instruction line inside a detail view | A single row naming the screen's action keys, e.g. `"'p' record payment, 'v' sales history, 't' transaction history, any other key back"` |

## §3. Spacing Scale

Row-index based, not pixel-based — the only "spacing" unit `curses` gives is the text row.

| Convention | Value | Source |
|---|---|---|
| List-screen layout | Row 0 = title, Row 1 = key-binding legend, Row 2 = blank, Row 3+ = one item per row | `main_menu.py::_render`, `customers.py::_render` |
| Detail-screen layout | Row 0 = title/primary identity, subsequent rows = one field per row, one blank row before the trailing instruction line | `customers.py::_render_detail` (rows 0–2 fields, row 4 balance, row 6 instructions) |
| Form layout (`run_form`) | Fixed one row per field starting at `start_row` (0 in the current caller); title/instructions placed on a row **below** the fixed field block so they never collide with it | `keybindings.py::run_form`, `customers.py::_prompt_customer_fields` (title on row 4, fields render at rows 0–2) |
| Message/prompt row | One dedicated row below the screen's content, never reusing a content row | `customers.py::_show_message`, `_prompt_payment_amount` |

**Rule for new screens:** reuse this fixed-row-per-item/field allocation. Do not introduce
variable/dynamic vertical spacing (blank-row padding between arbitrary elements) — every existing
screen uses contiguous rows with at most one blank separator row between logical blocks (legend →
content).

## §4. Shared Component Style Conventions

These are the actual "component library" this project has — plain-text patterns reused verbatim by
every screen, matching `ARCH-U03`.

### §4.1 List/menu screen
- Title row, then a key-binding legend row, then a blank row, then one row per list item.
- The highlighted item is prefixed `"> "`; every other item `"  "` (two spaces) — never a color or
  bold change (§1).
- Fast-access single-key shortcuts are named in the legend row itself (e.g. `"s=sale i=inventory
  c=catalog r=reports u=customers h=history"`), not discovered only by trial.
- **Empty-list state:** rendered inline at the position the first item would occupy, as a
  parenthetical hint naming the key that resolves the emptiness — e.g.
  `"(no customers yet — press 'n' to add one)"`. Never a blank screen and never an exception.

### §4.2 Detail screen
- Title row names the entity. Field rows follow, one per row, only rendering optional fields when
  present (`customers.py` only writes the `Address:` row `if customer.address`).
  Never a raw `None`/`null` printed for a missing optional field.
- A trailing instruction row spells out every action key plus the catch-all `"any other key back"`.

### §4.3 Form (multi-field entry)
- Built on `FormController`/`run_form` (`src/ui/keybindings.py`, `ARCH-U04`) — field-to-field
  navigation via Up/Down/Tab/Shift-Tab, `"> "`/`"  "` focus marker per field row (same marker as
  §4.1, applied per-field instead of per-item), cursor placed at the focused field's buffer end via
  `stdscr.move`.
- Edit-in-place default: each field may be pre-seeded with the entity's current value
  (`FormField.default`); pressing Enter without typing keeps that value unchanged.
- Enter on a non-last field advances focus; Enter on the last field submits.
- Esc at any field cancels the whole form (raises `EntryCancelled`) — no partial mutation, no error
  message, silently returns to the prior screen.
- **Single-field prompts** (e.g. payment amount) intentionally do NOT use `FormController` — they
  remain a direct `read_line` call. Only screens with more than one field use the form pattern
  (`ARCH-U04`; `docs/registry/phase2/.../tasks.md#P2-F2-T5`'s Cross-Cutting Consumption Obligation).

### §4.4 Message / error convention
- A message (success confirmation, validation error, or exception text) is written to one dedicated
  row below the screen's content, always followed by `"press any key to continue."`, and blocks on
  `stdscr.getch()` until acknowledged (`_show_message`).
- **Error text is always the caught domain exception's message** (`CustomerError`, `CatalogError`,
  `DiscountError`, ...) — never a raw `sqlite3.Error` or Python traceback. Domain exceptions are the
  only exception types a screen is allowed to catch and display (`architecture-rules.md` § Schema
  Ownership; `development-process.md` §3 error-path discipline).
- Cancellation (`EntryCancelled`) is a **distinct, silent path** — it is never routed through the
  message convention above; it returns to the prior screen with no message shown.

### §4.5 Key-binding vocabulary (`src/ui/keybindings.py`)
Every screen resolves raw key codes through the shared `NavAction` table before falling back to its
own screen-specific shortcuts:

| Action | Keys |
|---|---|
| Move | Arrow Up/Down, Tab (Tab = move-down/next-field) |
| Select / submit | Enter |
| Back | Esc |
| Quit | `q` / `Q` |

Screen-specific single-key shortcuts (e.g. `n` new, `e` edit, `p` pay, `v`/`t` drill-downs) are
resolved by the screen **before** falling back to `resolve_action()`, and are always spelled out in
that screen's legend/instruction row (§4.1, §4.2) — never a hidden keystroke.

**Rule for new screens (binding, not advisory):** every new screen in this project — including every
Phase 3 reporting page — MUST route generic navigation through `resolve_action()`/`FormController`
exactly as above rather than re-implementing key comparisons, and MUST document its own bindings in
its legend/instruction row. This is the DLVR-1-01 main-menu-shell consistency obligation
(`docs/registry/phase3/README.md` Planning Gate 1) restated as a style rule.

### §4.6 Loading / long-running operation convention
**No existing screen has a loading state** — every current operation (list fetch, single-record
read, single-transaction write) completes fast enough that no screen renders an intermediate
"loading" frame; the render loop's normal `_render` → `getch()` cycle is the only state. This is
recorded as the current baseline, not silently assumed: Phase 3's annual P&L aggregation is the
**first** operation in this project with a stated latency budget high enough (`docs/registry/phase3/
README.md` §6, proposed ≤1.0s) to be user-perceptible, so it is the first screen that needs an
explicit loading convention. See `docs/design/style-guide.md#48-loading-convention-new` below.

### §4.7 Loading convention (new — introduced by this proposal, not retrofitted)
Because §4.6 establishes there is no prior loading pattern to reuse, this proposal defines one,
built from the same primitives every other convention above uses (no new dependency, no color):
render a single content row reading `"Generating report — please wait..."` immediately after the
period is confirmed and **before** the blocking aggregation call, then call the reporting service,
then replace the screen with the result render. This is a single extra `stdscr.addstr` +
`stdscr.refresh()` pair around the existing call-then-render sequence every screen already uses
(e.g. `customers.py::_run_detail_screen`'s `balance = get_outstanding_balance(...)` immediately
followed by `_render_detail`) — no spinner widget, no timer, no new abstraction (`anti-patterns.md`
— no abstraction for fewer than three use cases; this is the first).
