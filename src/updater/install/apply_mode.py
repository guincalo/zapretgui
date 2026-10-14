from __future__ import annotations

"""Режим ``Zapret.exe --apply-update <план>``: новая версия ставит себя сама.

Запускается из пробного дерева новой версии (``file_update``). Порядок
установки, замена файлов с журналом, проверка запуска и откат — общее ядро
``zapret_update.apply`` (то же, что у Zapret KVN). Здесь — только своё у
Zapret 2 GUI:

* до замены: остановить драйверы WinDivert и Monkey, запущенные из папки этой
  программы (как установщик Inno; имена служб общие у Dev и Stable, поэтому
  чужую копию не трогаем);
* после замены: номер версии в «Программах и компонентах»;
* ход установки пишется в ``handoff.json`` — по нему окно-продолжение меняет
  этапы, а при неудаче следующий запуск сообщает серверу причину.

Модуль без Qt: окно на экране держит окно-продолжение, запущенное старой
версией.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config.build_info import CHANNEL

from . import handoff as install_handoff
from . import paths

DRIVER_SERVICES = ("WinDivert", "WinDivert14", "WinDivert1.4", "WinDivert64", "Monkey")
_UNINSTALL_ROOT = r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
_UNINSTALL_KEYS = {
    # Так их называет установщик Inno — с двумя закрывающими скобками.
    "dev": "{5C71C1DC-7627-4E57-9B1A-6B5D1F3A57F1-DEV}}_is1",
    "stable": "{5C71C1DC-7627-4E57-9B1A-6B5D1F3A57F1}}_is1",
}
APPLY_LOG_NAME = "apply.log"

_log = logging.getLogger("zapret_update")


def _setup_logging() -> None:
    try:
        target = paths.update_state_dir() / APPLY_LOG_NAME
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(target, maxBytes=512 * 1024, backupCount=1, encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    _log.addHandler(handler)
    _log.setLevel(logging.INFO)
    _log.propagate = False


def _image_path_under(image: str, root: Path) -> bool:
    text = image.strip().strip('"')
    for prefix in ("\\??\\", "\\\\?\\"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    try:
        target = os.path.normcase(os.path.abspath(text))
    except (TypeError, ValueError):
        return False
    base = os.path.normcase(os.path.abspath(str(root))).rstrip("\\/") + os.sep
    return target.startswith(base)


def stop_driver_services(root: Path, names=DRIVER_SERVICES) -> list[str]:
    """Остановить и снять службы драйверов, запущенные из ``root``; вернуть остановленные."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.OpenSCManagerW.restype = wintypes.HANDLE
    advapi.OpenServiceW.restype = wintypes.HANDLE
    advapi.OpenServiceW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD]
    advapi.QueryServiceConfigW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ControlService.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p]
    advapi.DeleteService.argtypes = [wintypes.HANDLE]
    advapi.CloseServiceHandle.argtypes = [wintypes.HANDLE]

    class _Config(ctypes.Structure):
        _fields_ = [
            ("dwServiceType", wintypes.DWORD),
            ("dwStartType", wintypes.DWORD),
            ("dwErrorControl", wintypes.DWORD),
            ("lpBinaryPathName", wintypes.LPWSTR),
            ("lpLoadOrderGroup", wintypes.LPWSTR),
            ("dwTagId", wintypes.DWORD),
            ("lpDependencies", wintypes.LPWSTR),
            ("lpServiceStartName", wintypes.LPWSTR),
            ("lpDisplayName", wintypes.LPWSTR),
        ]

    stopped: list[str] = []
    manager = advapi.OpenSCManagerW(None, None, 0x0001)  # SC_MANAGER_CONNECT
    if not manager:
        return stopped
    try:
        for name in names:
            # SERVICE_QUERY_CONFIG | SERVICE_STOP | DELETE
            service = advapi.OpenServiceW(manager, name, 0x0001 | 0x0020 | 0x10000)
            if not service:
                continue
            try:
                needed = wintypes.DWORD(0)
                advapi.QueryServiceConfigW(service, None, 0, ctypes.byref(needed))
                buffer = ctypes.create_string_buffer(max(needed.value, ctypes.sizeof(_Config)))
                if not advapi.QueryServiceConfigW(service, buffer, needed, ctypes.byref(needed)):
                    continue
                config = ctypes.cast(buffer, ctypes.POINTER(_Config)).contents
                if not _image_path_under(config.lpBinaryPathName or "", root):
                    continue
                status = ctypes.create_string_buffer(64)
                advapi.ControlService(service, 1, status)  # SERVICE_CONTROL_STOP
                advapi.DeleteService(service)
                stopped.append(name)
            finally:
                advapi.CloseServiceHandle(service)
    finally:
        advapi.CloseServiceHandle(manager)
    return stopped


def set_display_version(version: str, channel: str = CHANNEL) -> bool:
    """Номер версии в «Программах и компонентах» (если программа ставилась установщиком)."""
    if sys.platform != "win32":
        return False
    import winreg

    key_name = _UNINSTALL_KEYS.get(str(channel or "").lower())
    if not key_name:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{_UNINSTALL_ROOT}\\{key_name}", 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, str(version))
        return True
    except OSError:
        return False


def _hooks_class():
    from zapret_update import apply

    class GuiHooks(apply.Hooks):
        def before_swap(self, plan) -> None:
            stopped = stop_driver_services(Path(plan.app_dir))
            if stopped:
                _log.info("Остановлены службы драйверов из папки программы: %s", ", ".join(stopped))

        def after_swap(self, plan) -> None:
            if set_display_version(plan.version):
                _log.info("Версия в «Программах и компонентах»: %s", plan.version)

        def show_fatal(self, plan, details) -> None:
            if sys.platform != "win32":
                return
            try:
                import ctypes

                ctypes.windll.user32.MessageBoxW(
                    None,
                    "Не удалось завершить обновление и запустить прежнюю версию.\n\n"
                    f"Подробности: {paths.update_state_dir() / APPLY_LOG_NAME}",
                    "Zapret 2",
                    0x10,
                )
            except Exception:
                _log.exception("Не удалось показать сообщение об ошибке")

    return GuiHooks


def _reporter(plan):
    """Ход установки → ``handoff.json`` для окна-продолжения и следующего запуска."""
    from zapret_update.apply import Stage

    states = {
        Stage.PREPARE: install_handoff.HandoffState.PREPARED,
        Stage.WAIT_APP: install_handoff.HandoffState.PREPARED,
        Stage.SWAP: install_handoff.HandoffState.LAUNCHED,
        Stage.START: install_handoff.HandoffState.SUCCEEDED,
        Stage.DONE: install_handoff.HandoffState.SUCCEEDED,
        Stage.FAILED: install_handoff.HandoffState.FAILED,
    }
    last: dict[str, object] = {}

    def report(stage, _fraction) -> None:
        state = states.get(stage)
        if state is None or last.get("state") is state:
            return
        last["state"] = state
        error, failure = "", ""
        if state is install_handoff.HandoffState.FAILED:
            failure = _read(plan.failure_file).strip()
            error = (_read(plan.error_log).strip().splitlines() or ["установка не удалась"])[0]
        install_handoff.write_record(
            install_handoff.UpdateHandoffRecord(
                state=state,
                version=plan.version,
                target_root=plan.app_dir,
                installer_path="",
                gui_pid=plan.app_pid,
                installed_version=plan.version if state is install_handoff.HandoffState.SUCCEEDED else "",
                error=error,
                failure=failure,
            )
        )

    return report


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8") if path else ""
    except OSError:
        return ""


def run_apply_mode(plan_path: str) -> int:
    """Поставить новую версию по плану; код выхода 0 — успех."""
    from zapret_update import apply, handoff

    _setup_logging()
    try:
        plan = apply.ApplyPlan.load(Path(plan_path))
    except Exception:
        _log.exception("План установки не читается: %s", plan_path)
        return 2
    # Старая версия ждёт этот файл и только после него закрывается.
    try:
        (Path(plan.work_dir) / "applier.ready").write_text("ready", encoding="utf-8")
    except OSError:
        _log.exception("Не удалось отметиться перед старой версией")
    try:
        ok = apply.run_apply(plan, _hooks_class()(), _reporter(plan))
    except Exception:
        _log.exception("Установка прервана")
        ok = False
    finally:
        handoff.release(plan.hold_marker)
    return 0 if ok else 1


__all__ = ["run_apply_mode", "set_display_version", "stop_driver_services"]
