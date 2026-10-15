from __future__ import annotations

from settings.mode import DEFAULT_LAUNCH_METHOD, ZAPRET1_MODE, ZAPRET2_MODE, normalize_launch_method
from ui.navigation.sidebar_builder import sync_nav_visibility
from ui.navigation_pages import (
    resolve_control_page_for_method,
    resolve_preset_setup_page_for_method,
    resolve_zapret1_navigation_pages,
    resolve_zapret2_navigation_pages,
)
from app.page_names import PageName
from ui.window_adapter import get_current_page, get_loaded_page, show_page
from ui.workflows.common import get_current_launch_method


def get_mode_context_pages(window) -> set:
    mode_context_pages = set()
    winws2_pages = resolve_zapret2_navigation_pages()
    winws1_pages = resolve_zapret1_navigation_pages()
    for page_name in (
        PageName.DPI_SETTINGS,
        winws2_pages.control_page,
        winws2_pages.user_presets_page,
        winws2_pages.preset_setup_page,
        winws2_pages.preset_raw_editor_page,
        winws2_pages.profile_setup_page,
        winws1_pages.control_page,
        winws1_pages.user_presets_page,
        winws1_pages.preset_setup_page,
        winws1_pages.preset_raw_editor_page,
        winws1_pages.profile_setup_page,
    ):
        page = get_loaded_page(window, page_name)
        if page is not None:
            mode_context_pages.add(page)
    return mode_context_pages


def resolve_navigation_page_for_preset_setup(method: str | None) -> PageName:
    normalized = normalize_launch_method(method, default="")
    page_name = resolve_preset_setup_page_for_method(normalized)
    if page_name is None:
        return resolve_control_page_for_method(normalized)
    return page_name


def resolve_mode_context_page_for_method(current_page_name: PageName | None, method: str | None) -> PageName:
    normalized = normalize_launch_method(method, default="")
    control_page = resolve_control_page_for_method(normalized)
    pages = (
        resolve_zapret2_navigation_pages()
        if normalized == ZAPRET2_MODE
        else resolve_zapret1_navigation_pages()
        if normalized == ZAPRET1_MODE
        else None
    )
    if pages is None:
        return control_page

    current = current_page_name
    if current in {
        PageName.ZAPRET1_USER_PRESETS,
        PageName.ZAPRET2_USER_PRESETS,
        PageName.ZAPRET1_PRESET_RAW_EDITOR,
        PageName.ZAPRET2_PRESET_RAW_EDITOR,
    }:
        return pages.user_presets_page
    if current in {
        PageName.ZAPRET1_PRESET_SETUP,
        PageName.ZAPRET2_PRESET_SETUP,
        PageName.ZAPRET1_PROFILE_SETUP,
        PageName.ZAPRET2_PROFILE_SETUP,
    }:
        return pages.preset_setup_page
    return control_page


def redirect_to_preset_setup_page_for_method(window, method: str) -> None:
    current = get_current_page(window)

    mode_context_pages = get_mode_context_pages(window)

    if current is not None and current not in mode_context_pages:
        return
    current_page_name = getattr(current, "_page_registry_name", None)
    if not isinstance(current_page_name, PageName):
        current_page_name = None

    show_page(
        window,
        resolve_mode_context_page_for_method(current_page_name, method),
        allow_internal=True,
    )


def apply_launch_method_changed_ui(window, method: str) -> None:
    try:
        from log.log import log

        log(f"Метод '{method}' сохранён, UI переключается на страницы выбранного режима", "INFO")
    except Exception:
        pass

    try:
        from ui.window_ui_session import get_window_ui_session

        session = get_window_ui_session(window)
        preset_runtime = session.preset_runtime_coordinator if session is not None else None
        if preset_runtime is not None:
            preset_runtime.setup_active_preset_file_watcher()
    except Exception:
        pass

    try:
        sync_nav_visibility(window, method)
    except Exception:
        pass

    try:
        # Страницы прежнего режима больше не открыть: журнал забывает их сразу,
        # иначе «назад» горела бы, а нажатие ничего не делало.
        from ui.window_ui_session import get_window_ui_session

        session = get_window_ui_session(window)
        if session is not None:
            session.page_host.navigation_history.prune_unavailable()
    except Exception:
        pass

    try:
        redirect_to_preset_setup_page_for_method(window, method)
    except Exception:
        pass


def show_active_mode_control_page(window, *, allow_internal: bool) -> None:
    method = get_current_launch_method(default=DEFAULT_LAUNCH_METHOD)
    show_page(
        window,
        resolve_control_page_for_method(method),
        allow_internal=allow_internal,
    )


__all__ = [
    "apply_launch_method_changed_ui",
    "get_mode_context_pages",
    "redirect_to_preset_setup_page_for_method",
    "resolve_mode_context_page_for_method",
    "resolve_navigation_page_for_preset_setup",
    "show_active_mode_control_page",
]
