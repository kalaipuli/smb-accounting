"""Unit tests for the main menu navigation controller and routing.

Covers key-binding dispatch (arrows/Tab/Enter/Esc/q) and menu-item routing
(P1-F2-T3), plus the fast-access "record a sale" shortcut (P1-F2-T2).
"""

import curses

from src.ui.main_menu import (
    BACK_ROUTE,
    CATALOG_ROUTE,
    INVENTORY_ROUTE,
    MAIN_MENU_OPTIONS,
    QUIT_ROUTE,
    SALE_ENTRY_ROUTE,
    MainMenuController,
)

# --- construction ------------------------------------------------------------


def test_controller_when_constructed_defaults_to_first_option_selected() -> None:
    controller = MainMenuController()

    assert controller.selected_index == 0
    assert controller.selected_option() == MAIN_MENU_OPTIONS[0]


def test_controller_when_constructed_with_no_options_raises_value_error() -> None:
    try:
        MainMenuController(options=[])
    except ValueError:
        return
    raise AssertionError("expected ValueError for empty options")


# --- arrow/tab navigation dispatch -------------------------------------------


def test_dispatch_when_down_arrow_moves_selection_forward() -> None:
    controller = MainMenuController()

    route = controller.dispatch(curses.KEY_DOWN)

    assert route is None
    assert controller.selected_index == 1


def test_dispatch_when_down_arrow_wraps_past_last_option() -> None:
    controller = MainMenuController()
    for _ in range(len(controller.options) - 1):
        controller.dispatch(curses.KEY_DOWN)

    route = controller.dispatch(curses.KEY_DOWN)

    assert route is None
    assert controller.selected_index == 0


def test_dispatch_when_up_arrow_wraps_before_first_option() -> None:
    controller = MainMenuController()

    route = controller.dispatch(curses.KEY_UP)

    assert route is None
    assert controller.selected_index == len(controller.options) - 1


def test_dispatch_when_tab_moves_selection_forward_like_down_arrow() -> None:
    controller = MainMenuController()

    controller.dispatch(ord("\t"))

    assert controller.selected_index == 1


# --- Enter / select dispatch --------------------------------------------------


def test_dispatch_when_enter_on_first_option_returns_its_route() -> None:
    controller = MainMenuController()

    route = controller.dispatch(ord("\n"))

    assert route == MAIN_MENU_OPTIONS[0].route


def test_dispatch_when_enter_after_moving_down_returns_second_option_route() -> None:
    controller = MainMenuController()
    controller.dispatch(curses.KEY_DOWN)

    route = controller.dispatch(ord("\n"))

    assert route == MAIN_MENU_OPTIONS[1].route


# --- Esc / q dispatch ----------------------------------------------------------


def test_dispatch_when_esc_returns_back_route() -> None:
    controller = MainMenuController()

    assert controller.dispatch(27) == BACK_ROUTE


def test_dispatch_when_q_returns_quit_route() -> None:
    controller = MainMenuController()

    assert controller.dispatch(ord("q")) == QUIT_ROUTE


# --- unrecognized key ----------------------------------------------------------


def test_dispatch_when_unrecognized_key_returns_none_and_does_not_move_selection() -> (
    None
):
    controller = MainMenuController()

    route = controller.dispatch(ord("z"))

    assert route is None
    assert controller.selected_index == 0


# --- fast-access "record a sale" shortcut (P1-F2-T2) --------------------------


def test_dispatch_shortcut_key_jumps_to_sale_entry_route_regardless_of_selection() -> (
    None
):
    controller = MainMenuController()
    controller.dispatch(curses.KEY_DOWN)
    controller.dispatch(curses.KEY_DOWN)  # now on a non-sale item

    route = controller.dispatch(ord("s"))

    assert route == SALE_ENTRY_ROUTE


def test_dispatch_shortcut_key_uppercase_also_jumps_to_sale_entry_route() -> None:
    controller = MainMenuController()

    route = controller.dispatch(ord("S"))

    assert route == SALE_ENTRY_ROUTE


def test_dispatch_shortcut_does_not_change_selected_index() -> None:
    controller = MainMenuController()
    controller.dispatch(curses.KEY_DOWN)

    controller.dispatch(ord("s"))

    assert controller.selected_index == 1


# --- shortcut label consistency (demo-review fix) ------------------------------


def test_every_menu_option_label_displays_its_own_shortcut_key() -> None:
    for option in MAIN_MENU_OPTIONS:
        assert f"(shortcut: {option.key})" in option.label, (
            f"option {option.route!r} label {option.label!r} does not "
            f"display its shortcut key {option.key!r}"
        )


# --- fast-access keys for every option (Action 10) -----------------------------


def test_dispatch_every_menu_option_shortcut_jumps_directly_to_its_route() -> None:
    for option in MAIN_MENU_OPTIONS:
        controller = MainMenuController()
        controller.dispatch(curses.KEY_UP)  # move off the default selection

        route = controller.dispatch(ord(option.key))

        assert route == option.route, (
            f"pressing {option.key!r} should jump directly to "
            f"{option.route!r}, got {route!r}"
        )


def test_dispatch_every_menu_option_shortcut_uppercase_also_jumps_directly() -> None:
    for option in MAIN_MENU_OPTIONS:
        controller = MainMenuController()

        route = controller.dispatch(ord(option.key.upper()))

        assert route == option.route


def test_dispatch_inventory_shortcut_jumps_to_route_regardless_of_selection() -> None:
    controller = MainMenuController()
    controller.dispatch(curses.KEY_DOWN)
    controller.dispatch(curses.KEY_DOWN)

    route = controller.dispatch(ord("i"))

    assert route == INVENTORY_ROUTE


def test_dispatch_catalog_shortcut_does_not_change_selected_index() -> None:
    controller = MainMenuController()
    controller.dispatch(curses.KEY_DOWN)

    controller.dispatch(ord("c"))

    assert controller.selected_index == 1


def test_dispatch_catalog_shortcut_returns_catalog_route() -> None:
    controller = MainMenuController()

    route = controller.dispatch(ord("c"))

    assert route == CATALOG_ROUTE
