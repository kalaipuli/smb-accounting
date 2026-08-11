# Phase 2 — Feature 4 — Field-Navigable Form Entry (FormController)

In-house multi-field form-navigation primitive: extends `src/ui/keybindings.py`
(`read_line`/`NavAction`) with a pure-navigation-state `FormController` (mirrors
`CatalogController`/`InventoryController`) so a form can hold focus per field and
move between fields (Up/Down; Tab where terminfo supports it) without submitting
or cancelling the whole form. No third-party forms/widget library added — stays
inside the stdlib-`curses`, zero-extra-dependency decision (ADR-001/ARCH-U03).

**Cites:** DLVR-2-03 — "Field-navigable form entry primitive (`FormController`) — extends `src/ui/keybindings.py` (`read_line`/`NavAction`) with a pure-navigation-state form controller (mirrors `CatalogController`) so multi-field forms support field-to-field movement (Up/Down; Tab where terminfo supports it) without submitting or cancelling the whole form. No third-party forms/widget library added — stays inside the stdlib-`curses`, zero-extra-dependency decision." (cites ARCH-U04)

| ID | Status | Task | Description | Agent | Depends On | DoD / Sensor |
|----|--------|------|-------------|-------|------------|--------------|
| P2-F4-T1 | Done | Author ADR for FormController design | Decide (a) the focus-navigation model (field index + Up/Down as the portable binding, Tab/Shift-Tab as a best-effort addition given `curses.KEY_BTAB`'s terminfo dependency), (b) how `read_line` changes from a blocking per-field call into a stateful multi-field loop, (c) per-field cursor placement and redraw. Mirrors ADR-005/ADR-006 structure. Cites ARCH-U04 | backend-developer | none | [Inferential] ADR exists in `docs/adr/`, `Status: Accepted`, reviewed and accepted by architect |

### P2-F4-T1 — Notes

`docs/adr/ADR-007-formcontroller-field-navigation.md` drafted 2026-08-03
(`framework/spaces/phase2-feature2-customer-credit-khata.md` action 10,
`draft-formcontroller-adr`) — decides (a) field-index + Up/Down as the
portable focus-navigation binding, Tab/`curses.KEY_BTAB`-guarded
Shift-Tab as best-effort; (b) a new additive `FormField`/`FormController`/
`run_form` API in `src/ui/keybindings.py` (existing `read_line` untouched,
zero change to its call sites' behavior); (c) full-form redraw every
keystroke, `"> "` focus marker mirroring `CatalogController`'s convention,
cursor placed at the focused field's buffer end via `stdscr.move`. Filed
`Status: Proposed` — **not** self-certified to `Accepted` by the
authoring persona (backend-developer), per the DoD-gate no-self-certification
rule. Independent architect review completed 2026-08-03 in
`framework/spaces/phase2-feature4-formcontroller.md` action 1
(`review-accept-formcontroller-adr`) — ADR-007's design claims verified
against `src/ui/keybindings.py` and `src/ui/catalog.py` directly (not
accepted on the ADR's self-reported checks alone), Gate 2 ADR Adherence
Protocol checks re-run independently, no defect found. ADR-007 `Status`
set to `Accepted`. This task's `Status` is now `Done`.
| P2-F4-T2 | Done | Implement `FormController` and extend `read_line`/`NavAction` | `src/ui/keybindings.py` — add the `FormController` (or equivalent) navigation-state class and rework the field-entry loop per P2-F4-T1's design; no change to the existing single-field `read_line` call sites' external behavior beyond enabling field-to-field navigation | backend-developer | P2-F4-T1 | [Computational] `poetry run pytest tests/unit/ui -q --tb=short -k keybindings && poetry run mypy src/ui/keybindings.py --strict` |
| P2-F4-T3 | Done | Unit tests for `FormController` navigation | Full pytest coverage of forward/backward field navigation, edit-in-place per field, Esc-cancels-whole-form, and Enter-submits-on-last-field, driven via a fake key queue (no real terminal), mirroring `CatalogController`'s existing test pattern | tester | P2-F4-T2 | [Computational] `poetry run pytest tests/unit/ui -q --tb=short -k form` |

### P2-F4-T2 — Gate Evidence

Gate 1: `grep -rn "class FormController\|class FormField" src/ --include="*.py"` — zero matches (confirmed before writing). No duplicate schema exists.
Gate 2: Governing ADR — `docs/adr/ADR-007-formcontroller-field-navigation.md`, `Status: Accepted` (reviewed by architect, action 1 of this space). Scope gap: none — ADR-007 makes the concrete API/key-binding/cursor decisions this task implements; no sibling component shares this concern. Silence gap: none — Decision (a)/(b)/(c) fully specify `FormField`/`FormController`/`run_form` down to key codes and cursor math; nothing left for this turn to invent. Conflict: none — no other ADR touches `src/ui/keybindings.py`. Consumer gap: none — named consumers (`src/ui/customers.py`, `src/ui/sales.py`) are Pending, addressed by actions 4/5 of this space, not this turn.
Gate 3: n/a — no shared resource (database client, cache, external client) is constructed by this task; `FormController` is pure in-memory navigation state, no I/O.
Gate 4: `grep -rn "npyscreen\|py_cui" src/ --include="*.py"` — zero matches. No deprecated/rejected symbols in the plan.
Gate 5: n/a — not a fix task.

### P2-F4-T2 — Notes

`FormField`/`FormController`/`run_form` implemented in `src/ui/keybindings.py`
per ADR-007 Decision (a)/(b)/(c): field-index + Up/Down as the portable
binding, Tab forward and `getattr(curses, "KEY_BTAB", None)`-guarded
Shift-Tab backward as best-effort; `handle_key` does not route through
`resolve_action()` (own explicit key checks, printable range 32-126,
matching `read_line` byte-for-byte); Enter advances on a non-last field,
submits on the last; Esc cancels the whole form via the existing
`EntryCancelled` (no new exception type). `run_form` mirrors `read_line`'s
structural loop, full-form redraw every keystroke, `"> "`/`"  "` focus
marker matching `CatalogController._render`'s convention,
`stdscr.move`-based cursor placement at the focused field's buffer end,
one fixed row per field. `read_line` itself: zero lines changed (diff
confirms only new code was appended after it). Smoke-level unit tests
added to `tests/unit/ui/test_keybindings.py` (selectable via
`-k keybindings`) proving `FormController`'s navigation/buffer/submit/cancel
mechanics and `run_form`'s render-loop wiring — full navigation-matrix
coverage is `P2-F4-T3`'s job (tester, next turn). Verification: `pytest
tests/unit/ui -k keybindings` (27 passed), full `pytest tests/unit` (341
passed, zero regressions across `catalog.py`/`inventory.py`/`sales.py`
consumers), `mypy src/ui/keybindings.py --strict` (clean), `ruff format
--check`/`ruff check` (clean, one line-length fix applied before final
run).

### P2-F4-T3 — Gate Evidence

Gate 1: n/a — this task adds tests only, no new type/class defined; nothing for the duplicate-schema grep to check.
Gate 2: Governing ADR — `docs/adr/ADR-007-formcontroller-field-navigation.md`, `Status: Accepted`. Scope gap: none — the ADR's Decision (a)/(b)/(c) fully describe the navigation/edit/cancel/submit semantics this task tests against; no sibling component involved. Silence gap: none — the ADR's Decision (a) explicitly calls out the `curses.KEY_BTAB` terminfo dependency as "best-effort," which is exactly the case this task adds a test for (degrade to no-op when the attribute is absent, not a crash). Conflict: none. Consumer gap: none — this task is itself the test-coverage consumer of P2-F4-T2's implementation; no other reader of `FormController` exists yet (actions 4/5 land after this turn).
Gate 3: n/a — no shared resource constructed; tests exercise `FormController`/`run_form` against `_FakeFormWindow`, an in-memory stand-in with no real terminal or I/O.
Gate 4: `grep -rn "npyscreen\|py_cui" tests/unit/ui/test_keybindings.py` — zero matches. No deprecated/rejected symbols introduced.
Gate 5: n/a — not a fix task.

### P2-F4-T3 — Notes

Reviewed Turn 2's smoke-level `FormController`/`run_form` tests in
`tests/unit/ui/test_keybindings.py` against this task's DoD and identified
genuine gaps, all closed without duplicating existing coverage:

- **Forward navigation** — smoke coverage exercised a single `move_next_field()`
  call and one Down/Tab key each on a 2-field form; added full-traversal tests
  across a 3-field form for both `KEY_DOWN` and Tab (`ord("\t")`), including the
  past-last-field no-op boundary.
- **Backward navigation** — smoke coverage exercised a single `KEY_UP` press;
  added full-traversal-backward coverage across 3 fields, the
  before-first-field no-op boundary, and the `KEY_BTAB` path in both directions:
  moves focus backward when `curses.KEY_BTAB` is available (`monkeypatch.setattr`),
  and degrades to a harmless no-op — not a crash, not a stray literal-character
  append — when the attribute is absent (`monkeypatch.delattr`), per ADR-007
  Decision (a)'s "best-effort" terminfo-dependent framing. Neither path existed
  in Turn 2's tests.
- **Edit-in-place** — no prior test verified a field's buffer survives
  navigating away and back; added a test that edits field "b", moves to "c",
  moves back to "b", and asserts the buffer is unchanged.
- **Esc cancels the whole form** — Turn 2's `run_form` Esc test only cancelled
  from the first field with no prior navigation. Added a controller-level test
  cancelling from a non-first field, and a `run_form`-level test that types
  into field 1, navigates to field 2, types more, then presses Esc — asserting
  `EntryCancelled` still raises (no partial submit path exists; cancellation is
  form-wide by construction, since `handle_key` returning `"cancel"` short-circuits
  the loop before any field's value could be returned).
- **Enter submits only on the true last field** — Turn 2 tested this on a
  2-field form (one non-last, one last). Added a 3-field controller-level test
  walking all three Enter presses (advance, advance, submit) and a
  `run_form`-level test typing and pressing Enter through all three fields,
  asserting the final `values()` dict.

No duplicate assertions added — every new test targets a case the existing 14
smoke tests did not already cover. All new tests are named
`test_form_controller_*`/`test_run_form_*`, matching the DoD's literal sensor
`-k form`; confirmed via `pytest tests/unit/ui --collect-only -k form` before
and after that no unrelated test in `tests/unit/ui` matches the substring
`form` (only this file's `FormController`/`run_form` tests do — no collision).

Verification: `pytest tests/unit/ui -k form` — 25 passed (14 Turn-2 smoke +
11 new). Full `pytest tests/unit` — 352 passed (341 baseline + 11 new, zero
regressions). `mypy tests/unit/ui/test_keybindings.py --strict` — clean.
`ruff format --check`/`ruff check` — clean (one line-length fix applied
before final run).

## Notes

- This feature has no `SCOPE-*` deliverable of its own — it is UI implementation infrastructure (`ARCH-U04`), not a new business capability. It exists to be consumed by Feature 1 and Feature 2's screen-wiring tasks (`feature1/P2-F1-T5`, `feature2/P2-F2-T5`) per `project-plan.md`'s Cross-Cutting Consumption Obligation "In-house field-navigable form entry (`FormController`)..." added at plan revision 4.
- P2-F4-T1/T2 do not depend on Feature 1 or Feature 2 — the `FormController` is generic over any field list and can be built and tested standalone against a fake key queue, the same way `src/ui/keybindings.py`'s existing `read_line`/`resolve_action` are.
- Sequencing: `feature1/P2-F1-T5` and `feature2/P2-F2-T5` (the screens that must actually use field navigation) now depend on this feature's `P2-F4-T2` landing first — see the updated `Depends On` in each.
