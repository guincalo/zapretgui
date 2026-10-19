from __future__ import annotations

import sys
from ctypes import wintypes

from log.log import log


WM_SYSCOMMAND = 0x0112
SC_MINIMIZE = 0xF020
SC_RESTORE = 0xF120
_SC_COMMAND_MASK = 0xFFF0


def handle_native_minimize_command(window, message, *, minimize_to_tray_enabled=None) -> bool:
    """Перехватывает команду Windows «свернуть» до обычного сворачивания."""
    if sys.platform != "win32":
        return False

    msg = _read_msg(message)
    if msg is None:
        return False
    if int(msg.message) != WM_SYSCOMMAND:
        return False
    if (int(msg.wParam) & _SC_COMMAND_MASK) != SC_MINIMIZE:
        return False

    return handle_minimize_request(window, minimize_to_tray_enabled=minimize_to_tray_enabled)


def is_native_minimize_command(message) -> bool:
    """Команда Windows «свернуть» (кнопка в заголовке, клик по значку в панели задач).

    Приходит до того, как Windows сожмёт рабочный набор свёрнутого окна: самый
    ранний сигнал ухода, нужный ``ui.window_presence``. Сообщение не «съедается».
    """
    if sys.platform != "win32":
        return False
    msg = _read_msg(message)
    if msg is None or int(msg.message) != WM_SYSCOMMAND:
        return False
    return (int(msg.wParam) & _SC_COMMAND_MASK) == SC_MINIMIZE


def is_native_restore_command(message) -> bool:
    """Команда Windows «развернуть» (клик по окну в панели задач, Alt+Tab).

    Приходит раньше, чем Qt покажет и нарисует окно: это самый ранний сигнал
    возврата, нужный ``ui.window_presence``. Сообщение не «съедается».
    """
    if sys.platform != "win32":
        return False
    msg = _read_msg(message)
    if msg is None or int(msg.message) != WM_SYSCOMMAND:
        return False
    return (int(msg.wParam) & _SC_COMMAND_MASK) == SC_RESTORE


def handle_minimize_request(window, *, minimize_to_tray_enabled=None) -> bool:
    """Общий обработчик команды «свернуть окно»."""
    try:
        if not _is_minimize_to_tray_enabled(minimize_to_tray_enabled):
            return False
        return bool(window.close_to_tray())
    except Exception as exc:
        log(f"Не удалось обработать команду сворачивания в трей: {exc}", "DEBUG")
        return False


def _is_minimize_to_tray_enabled(provider) -> bool:
    if not callable(provider):
        return False
    return bool(provider())


def _read_msg(message):
    try:
        address = int(message)
    except Exception:
        return None
    if address <= 0:
        return None

    try:
        return wintypes.MSG.from_address(address)
    except Exception:
        return None


__all__ = [
    "SC_MINIMIZE",
    "WM_SYSCOMMAND",
    "handle_minimize_request",
    "handle_native_minimize_command",
    "is_native_minimize_command",
]
