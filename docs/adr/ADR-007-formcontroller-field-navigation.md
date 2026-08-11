# ADR-007: `FormController` — Field-Navigable Multi-Field Form Entry

**Status:** Accepted
**Date:** 2026-08-03

## Context

Every multi-field data-entry screen in this codebase today (`src/ui/catalog.py`'s
`_prompt_product_fields`/`_prompt_discount_fields`, and the equivalent
pattern any future customer-creation or sale-line-item form would need to
copy) is a **linear sequence of blocking `read_line()` calls** — one field
is fully entered and confirmed with Enter before the next field's prompt
is even shown, and pressing Esc on *any* field cancels the *entire* form
via `EntryCancelled` propagating straight out. There is no way to move
back to an earlier field to correct it without cancelling and restarting
the whole form. `src/ui/keybindings.py`'s `read_line` is deliberately a
single-field primitive (its own docstring: "shared raw-keycode field-entry
primitive") — it was never designed to hold state across fields.

`framework/project/architecture.md` `ARCH-U04` (confirmed 2026-08-03)
already names the target design at a high level: an in-house
`FormController` mirroring `CatalogController`/`InventoryController`'s
existing pure-navigation-state pattern, extending `read_line`/`NavAction`,
staying inside the stdlib-`curses`, zero-extra-dependency decision
(`ADR-001`, `ARCH-T03`, `ARCH-U03` — `npyscreen` explicitly evaluated and
rejected there). `ARCH-U04` states the *what* and the *why*; it does not
decide the *how* — the concrete `FormController` API, how `read_line`'s
blocking single-field call becomes a stateful multi-field loop, or
per-field cursor/redraw behaviour. No ADR resolves that silence gap. This
ADR closes it, per task registry `docs/registry/phase2/feature4-form-navigation/tasks.md`
`P2-F4-T1` and `framework/project/project-plan.md` `DLVR-2-03`'s Cross-Cutting
Consumption Obligation (both `DLVR-2-01` — sales entry — and `DLVR-2-02` —
customer/khata — depend on this landing before their own field-navigable
create/entry forms can close: `feature1/P2-F1-T5`, `feature2/P2-F2-T5`).

## Decision

### (a) Focus-navigation model — field index + Up/Down as the portable binding

`FormController` holds a **field index** into a fixed, ordered list of
fields (mirroring `CatalogController`'s `_index` into its product list).
Focus moves between fields via:

- **Up / Down arrows (`curses.KEY_UP`/`curses.KEY_DOWN`)** — the portable
  binding. Every terminal/terminfo combination this project has to support
  resolves these two keys reliably (already proven — `resolve_action`
  already maps them for every existing list screen). This is the binding
  documented to the user and the one guaranteed to work everywhere.
- **Tab (`\t`) — best-effort forward-only.** `\t` is already a stable,
  single fixed keycode (`ord("\t")`, no terminfo variance) and is already
  mapped in `resolve_action` today (`NavAction.NEXT_FIELD`) for exactly
  this "move forward" intent on list screens (where it currently behaves
  identically to Down). `FormController` reuses it as "move to next field."
- **Shift-Tab (`curses.KEY_BTAB`) — best-effort backward, guarded.**
  `curses.KEY_BTAB` exists as a named constant in Python's `curses` module
  but whether the terminal driver actually *emits* it for Shift-Tab is
  terminfo-dependent (some terminals send an escape sequence the current
  `TERM` entry may not map to `KEY_BTAB` at all) — this is exactly the
  caveat Feature 4's own task description names. `FormController` reads
  `curses.KEY_BTAB` via `getattr(curses, "KEY_BTAB", None)` and only wires
  it if present, so a `curses` build/terminfo combination lacking it does
  not raise `AttributeError` — it simply means Shift-Tab silently does
  nothing on that terminal, and the user falls back to the portable Up
  binding. This key is intentionally **not** added to `keybindings.py`'s
  shared `_KEY_DISPATCH`/`resolve_action` table — it is `FormController`-local,
  since no other existing screen (list-navigation only) has a "move
  backward through a sequence" concept distinct from `MOVE_UP`, and adding
  it to the shared table would risk misapplying a terminfo-fragile key
  across every screen for zero benefit.
- **Enter — advances on a non-last field, submits on the last field.**
  Overloading Enter this way (rather than requiring a separate explicit
  "submit" key) mirrors how a shop owner already uses Enter today in the
  existing linear `_prompt_field` sequence (confirm-and-move-on) — the
  behavioural difference introduced here is only that Enter on a
  non-terminal field now moves focus forward instead of unconditionally
  blocking on `read_line` for the *next* field call; the last field's
  Enter is where "submit" actually happens, per `P2-F4-T3`'s DoD wording
  ("Enter-submits-on-last-field").
- **Esc — cancels the whole form, unconditionally, from any field.**
  Preserves today's existing whole-form-cancel semantics (`EntryCancelled`)
  exactly — Esc's meaning does not change; only how many fields exist
  before it fires changes (previously exactly one, now potentially many).

### (b) `read_line`'s blocking per-field call becomes a stateful multi-field loop

`read_line(stdscr, row, col, default)` **is not modified or removed.**
Every existing single-field call site (`src/ui/catalog.py`'s
`_prompt_field`, `src/ui/inventory.py`'s stock-delta prompt,
`src/ui/customers.py`'s payment-amount prompt) keeps calling it exactly as
today — `P2-F4-T2`'s own DoD requires "no change to the existing
single-field `read_line` call sites' external behavior." `FormController`
is additive: a new class in `src/ui/keybindings.py` plus a new module-level
function, `run_form`, that is `read_line`'s multi-field analogue — not a
replacement for it.

```python
@dataclass(frozen=True)
class FormField:
    """One field's static definition: internal key, display label, and
    optional pre-filled starting value (edit-in-place default, same
    semantics as read_line's own `default` parameter)."""
    name: str
    label: str
    default: str = ""


class FormController:
    """Pure navigation + per-field text-buffer state over a fixed field
    list — no curses I/O. Mirrors CatalogController/InventoryController's
    existing pure-state pattern so it is unit-testable via a fake key
    queue, with no real terminal, exactly like those two classes already
    are.

    Holds what a single read_line() call's local `buf` variable held for
    one field, now keyed per field and retained across repeated
    dispatch() calls — this is the concrete mechanism by which "read_line
    becomes stateful": the per-field buffer no longer dies when the next
    field's prompt begins, because there is no "next field's prompt" as a
    separate blocking call anymore — it is the same loop, same object,
    advancing its field index.
    """

    def __init__(self, fields: list[FormField]) -> None: ...
    @property
    def fields(self) -> list[FormField]: ...
    @property
    def index(self) -> int: ...
    def current_field(self) -> FormField: ...
    def buffer(self, name: str) -> str: ...
    def values(self) -> dict[str, str]: ...  # all fields' current buffers
    def is_last_field(self) -> bool: ...
    def move_next_field(self) -> None: ...   # no-op past the last field
    def move_prev_field(self) -> None: ...   # no-op before the first field
    def handle_key(self, key: int) -> str | None:
        """One raw stdscr.getch() code in, one of "submit"/"cancel"/None
        out. Navigation keys (Up/Down/Tab/Shift-Tab/Enter/Esc) are checked
        first, exactly as read_line already checks Esc/Enter/backspace
        before falling through to literal-character handling — this is
        deliberate and is the one point where FormController must NOT
        reuse keybindings.py's generic resolve_action(): resolve_action
        maps 'q'/'Q' to NavAction.QUIT for list screens, which is correct
        there (no free-text entry happens on a list screen) but would be
        wrong here — a form field must accept the literal character 'q'
        as typed text (e.g. a product name containing the letter q).
        FormController's own explicit key checks, not resolve_action,
        govern every printable character 32-126, matching read_line's own
        approach byte-for-byte.
        """
```

`run_form(stdscr, fields: list[FormField]) -> dict[str, str]` is the thin
render loop — `read_line`'s multi-field counterpart, structurally the same
shape (`while True: render → stdscr.getch() → handle_key → act on result`)
but rendering every field each pass instead of one, and looping until
`handle_key` returns `"submit"` (returns `controller.values()`) or
`"cancel"` (raises `EntryCancelled`, the *same* exception `read_line`
already raises — every existing caller's `except EntryCancelled:` block
around a `_prompt_*_fields` call needs no new exception type to catch when
that call site is eventually migrated to `run_form`).

### (c) Per-field cursor placement and redraw

- **Redraw scope: full form, every keystroke** — mirrors `read_line`'s own
  behaviour (`stdscr.addstr` + `stdscr.refresh()` every iteration, not
  only on navigation). `run_form` clears and re-renders every field's
  label + current buffer value each pass, not only the field being edited
  — this keeps a stale value from a field the user already edited-and-moved-on-from
  from ever appearing, and is the same "redraw the whole visible state
  every iteration" convention `_render`/`_render_discounts` already use
  for list screens.
- **Focus indicator:** the field currently holding focus is prefixed with
  the same `"> "` / `"  "` marker convention `CatalogController`'s
  `_render` already uses for the selected list row — one visual
  vocabulary for "this is the focused thing" across both list screens and
  forms, not a second competing convention.
- **Cursor placement:** after each field's label+value is drawn, the
  cursor position is set via `stdscr.move(row, col)` to
  `row = <that field's assigned row>`, `col = len(label_prefix) +
  len(current_buffer_text)` — i.e. immediately after the last typed
  character of the *focused* field only, exactly where `read_line`
  already places the cursor for its one field (`addstr(row, col, buf +
  " ")` followed by `refresh()`, which leaves the terminal's own cursor
  tracking `addstr`'s write position). Non-focused fields show their
  buffer text but do not receive the terminal cursor.
- **Field rows are pre-assigned, not dynamically computed from content
  length** — each `FormField` occupies exactly one fixed row (`start_row +
  index`), matching `_prompt_product_fields`'s existing fixed-row-per-field
  layout (rows 2/3/4 for name/price/gst_rate today). No field wraps to a
  second row regardless of typed length; this is an accepted simplicity
  trade-off given kirana-shop field values (names, phone numbers, prices)
  are short.

### Gate 2 — ADR Adherence Protocol checks (`agent-conduct.md`)

- **Scope gap:** `ARCH-U04` names the `FormController` decision at the
  architecture level (what it is, why it's needed, that it stays
  stdlib-`curses`) but makes none of this ADR's concrete decisions (API
  shape, key bindings beyond the general "Up/Down; Tab where terminfo
  supports it" phrase already in `ARCH-U04`/`P2-F4-T1`'s own task
  description, cursor/redraw behaviour). No other ADR touches form/field
  navigation. Genuinely new decision at this level of detail.
- **Silence gap:** how `read_line`'s statefulness changes, the exact
  `FormController`/`FormField`/`run_form` API, and per-field cursor/redraw
  were all previously undecided — all resolved in (a)/(b)/(c) above.
- **Conflict:** `docs/adr/` (`ADR-001`–`ADR-006`) reviewed — no
  contradiction. `ADR-001`'s stdlib-`curses`-only decision is followed,
  not contradicted (no third-party forms/widget library introduced,
  matching `ARCH-U04`'s explicit rejection of `npyscreen`). No ADR
  currently governs `src/ui/keybindings.py`'s internals at all — this is
  the first.
- **Consumer gap:** components named: `src/ui/keybindings.py` (this
  module gains `FormField`/`FormController`/`run_form`, `P2-F4-T2`),
  `src/ui/customers.py` (customer-creation form, `P2-F2-T5`, currently
  deliberately left unbuilt pending this ADR — see
  `framework/spaces/phase2-feature2-customer-credit-khata.md` Turn 7's
  Handover log), `src/ui/sales.py` (sale-entry line-item/discount form,
  `feature1/P2-F1-T5`, not yet built at all). Both named consumers are
  Pending, not Done, at the time this ADR is authored — no existing
  shipped screen needs to be retrofitted or risks behavioural drift from
  this decision; `_prompt_product_fields`/`_prompt_discount_fields` in
  `src/ui/catalog.py` are **not** consumers of this ADR — retrofitting
  Catalog's already-`Done` (Phase 1) forms onto `FormController` is
  explicitly out of this ADR's scope (no task registry entry requests it;
  doing so unprompted would be scope creep against `development-process.md`
  §2's one-task-per-commit discipline).

## Multi-System Impact Analysis

- **Availability impact:** none — single local process, no downstream
  systems; `FormController`/`run_form` are pure-Python/`curses`-stdlib
  additions to an existing module.
- **Blast radius:** additive only. `read_line`, `EntryCancelled`,
  `NavAction`, `resolve_action`, and `_KEY_DISPATCH` in
  `src/ui/keybindings.py` are unchanged — every existing screen
  (`main_menu.py`, `catalog.py`, `inventory.py`, `customers.py`) that
  calls `read_line` or `resolve_action` today continues to compile and
  behave identically, since nothing in this ADR removes or renames an
  existing symbol or changes an existing function's signature. The new
  symbols (`FormField`, `FormController`, `run_form`) are consumed by no
  code yet — they will not be until `feature1/P2-F1-T5`/`feature2/P2-F2-T5`
  wire their forms to them, at which point *those* screens' behaviour
  changes from linear-blocking to field-navigable (the intended change,
  not a regression).
- **Rollback path:** purely additive — reverting is deleting
  `FormField`/`FormController`/`run_form` from `src/ui/keybindings.py` and
  their tests. No schema, no migration, no data involved. If a consumer
  screen has already been wired to `run_form` by the time of a rollback,
  that screen's form-entry call sites would need to revert to the
  pre-existing linear `_prompt_*_fields` pattern as a companion change —
  not a concern at the time this ADR is filed, since no consumer exists
  yet.

## Alternatives Considered

- **Third-party curses forms/widget library (e.g. `npyscreen`,
  `py_cui`)** — rejected; already evaluated and rejected at the
  `ARCH-U04` architecture-decision level (`npyscreen`: unmaintained, still
  a thin `curses` wrapper, adds a dependency for a single-shop offline
  install without a real capability gain). This ADR does not reopen that
  choice, only implements the in-house alternative `ARCH-U04` already
  selected.
- **Modify `read_line` itself to accept a list of fields (in-place
  expansion) instead of adding a new `FormController`/`run_form` pair** —
  rejected; would force every existing single-field call site
  (`_prompt_field`, the stock-delta prompt, the payment-amount prompt) to
  either pass a one-element field list or grow a parallel code path inside
  the same function, directly risking `P2-F4-T2`'s explicit constraint
  ("no change to the existing single-field `read_line` call sites'
  external behavior") for no benefit — a genuinely separate concern
  (one field vs. many, with different navigation semantics) is better
  served by a genuinely separate primitive, mirroring the existing
  precedent of `CatalogController` and `InventoryController` being two
  distinct classes rather than one parameterized "list controller."
- **Tab/Shift-Tab as the *only* (non-fallback) navigation binding** —
  rejected; `curses.KEY_BTAB`'s terminfo dependency (named explicitly in
  Feature 4's own task description) means a user on a terminal/`TERM`
  combination that does not emit it would have no way to move backward
  through a form at all. Up/Down as the always-present portable binding,
  with Tab/Shift-Tab as an enhancement layered on top, avoids a hard
  usability failure on any one terminal configuration.
- **Submit via a distinct dedicated key (e.g. `Ctrl-S`) instead of
  overloading Enter on the last field** — rejected; `curses` control-key
  handling is itself terminal-driver-dependent and adds a second binding
  the user must learn, when Enter-to-confirm is already the exact meaning
  Enter carries on every existing single-field prompt in this codebase.
  `P2-F4-T3`'s own DoD wording ("Enter-submits-on-last-field") confirms
  this is the intended design, not a workaround.
- **Field wrapping / dynamic row layout for long values** — rejected as
  unneeded complexity; kirana-shop field values in scope today (names,
  phone numbers, prices, GST rates) are short, single-line values; a
  fixed-row-per-field layout matching the existing `_prompt_product_fields`
  convention is simpler and sufficient.

## Consequences

- `src/ui/keybindings.py` becomes the single owner of both single-field
  (`read_line`) and multi-field (`FormController`/`run_form`) text-entry
  primitives — any future screen needing multi-field entry consumes
  `run_form` rather than re-implementing a linear `_prompt_*_fields`
  sequence, directly satisfying `project-plan.md`'s Cross-Cutting
  Consumption Obligation for `DLVR-2-03`.
- `feature1/P2-F1-T5` (sale-entry line-item form) and `feature2/P2-F2-T5`
  (customer-creation form) can now proceed against a decided API —
  neither was buildable against a defined contract before this ADR (see
  `framework/spaces/phase2-feature2-customer-credit-khata.md` Turn 7's
  Handover log, which explicitly deferred `P2-F2-T5` pending this
  decision rather than building against the pre-`FormController`
  anti-pattern).
- `_KEY_DISPATCH`/`resolve_action` remain exactly as they are today — this
  ADR does not touch the shared list-navigation vocabulary, only adds a
  parallel, `FormController`-local set of key checks for the
  free-text-entry use case, where `resolve_action`'s `'q'`-means-quit
  mapping would otherwise be actively wrong.
- Any future form (beyond the two named consumers) that needs
  field-to-field navigation has a decided pattern to follow instead of a
  third bespoke linear-prompt implementation appearing.

## Review

Per `agent-conduct.md`'s ADR Adherence Protocol and this feature's task
DoD (`P2-F4-T1`), this ADR requires independent Architect review before
its status may move to `Accepted`. No self-certification by the
authoring persona (backend-developer) — this ADR is filed as
`Status: Proposed` pending that review. See
`framework/spaces/phase2-feature2-customer-credit-khata.md` action 10
(`draft-formcontroller-adr`); action 11
(`review-accept-formcontroller-adr`, architect) is the independent
review turn, tracked in a new relay space per that space's closure at
action 10.

**Architect review verdict (2026-08-03): Accepted.**

Independently opened and cross-checked, not accepted on the authoring
persona's self-reported checks alone. `src/ui/keybindings.py` (read in
full) — confirmed `_KEY_DISPATCH` maps `ord("q")`/`ord("Q")` to
`NavAction.QUIT` and `resolve_action` returns that mapping unconditionally;
the ADR's claim that routing `FormController` through `resolve_action`
would swallow a literal `q`/`Q` typed into a form field is correct, and the
Decision's explicit choice to bypass `resolve_action` inside `handle_key`
in favour of `read_line`-style explicit checks (printable-range 32-126,
matching `read_line`'s own `if 32 <= key <= 126` line) is the right fix,
not a workaround. Confirmed `read_line` (lines 68-97) is genuinely
untouched by this ADR — no proposed edit to its body, signature, or the
`EntryCancelled`/`NavAction`/`_KEY_DISPATCH` symbols it shares the module
with; every existing call site (`catalog.py`'s `_prompt_field`,
`inventory.py`'s stock-delta prompt) is therefore unaffected, matching
`P2-F4-T2`'s DoD constraint. `src/ui/catalog.py` (`_render`, line 188) —
confirmed `prefix = "> " if row - 3 == controller.selected_index else "
"` is the actual existing focus-marker convention `CatalogController` uses
today; the ADR's proposed reuse of `"> "`/`"  "` for `FormController`'s
focused-field marker is consistent with this, not an invented precedent.
`stdscr.move`-based cursor placement is new (no existing screen calls
`stdscr.move` directly — `read_line`/`_render` rely on `addstr`'s
implicit cursor tracking, which is sufficient only when exactly one
field/row is drawn per pass); because `run_form` redraws every field
every keystroke, an explicit `stdscr.move(row, col)` after the full
redraw is necessary to land the terminal cursor back on the focused
field rather than wherever the last-drawn field's `addstr` left it — the
ADR does not misrepresent this as an existing pattern, and the mechanism
is sound.

Gate 2 ADR Adherence Protocol checks re-run independently:

- **Scope gap:** Confirmed — `ARCH-U04` (`framework/project/architecture.md`
  line 184) names the `FormController` decision at the architecture level
  (what it is, why, stdlib-`curses`-only, Up/Down as the portable binding
  with Tab where terminfo supports it) but resolves none of this ADR's
  concrete decisions (API shape, Shift-Tab's `getattr`-guarded fallback,
  Enter's advance-vs-submit split, per-field cursor/redraw mechanics). No
  sibling component with the same concern is left unaddressed — form
  navigation has exactly one owner module (`src/ui/keybindings.py`) and
  this ADR is the first to govern it.
- **Silence gap:** Confirmed — how `read_line`'s single-field statefulness
  becomes multi-field, the concrete `FormField`/`FormController`/`run_form`
  API, and per-field cursor/redraw behaviour were all genuinely undecided
  before this ADR (no prior ADR touches `keybindings.py`'s internals); all
  three are resolved in Decision (a)/(b)/(c) with enough concreteness for
  `backend-developer` to implement without further judgment calls on the
  navigation-key table itself.
- **Conflict:** Confirmed none. `docs/adr/` ADR-001 through ADR-006
  independently reread — `ADR-001`'s stdlib-`curses`-only,
  zero-extra-dependency decision is followed (no third-party widget
  library introduced); no other ADR references `src/ui/keybindings.py`,
  `FormController`, or field-navigation semantics. `ARCH-U03`
  ("no shared component library needed... consistent key bindings
  enforced by convention") is followed, not contradicted — the ADR
  explicitly reuses the `"> "` convention rather than introducing a
  second one.
- **Consumer gap:** Confirmed — `grep` across `src/ui/` for
  `keybindings` imports shows only `main_menu.py`, `catalog.py`, and
  `inventory.py` currently import from this module, and none of the three
  consume `FormField`/`FormController`/`run_form` (they use
  `read_line`/`resolve_action`/`NavAction` only, which are unchanged) —
  so none is a consumer of this ADR's new surface and none needs
  addressing here. The ADR's named consumers (`src/ui/customers.py`,
  `src/ui/sales.py`, both `Pending`) are correct and complete; no reader
  of the new symbols is left unaddressed.

**Multi-System Impact Analysis:** present and adequate — availability
impact correctly assessed as none (single local process, no downstream
system); blast radius correctly scoped as additive-only with the specific
mechanism named (no existing symbol renamed, removed, or signature-changed
in `keybindings.py`); rollback path is concrete (delete the three new
symbols and their tests; correctly flags that an already-wired consumer
screen would need a companion revert, and correctly notes this is not yet
a live concern since no consumer exists at filing time).

No defect found. `Status` set to `Accepted`.
