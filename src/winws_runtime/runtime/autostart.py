from log.log import log
from settings.mode import ALL_LAUNCH_METHODS


def start_dpi_autostart(
    startup_state,
    *,
    runtime_feature,
    ui_state,
    launch_method: str | None = None,
) -> None:
    """Запускает DPI autostart через общий runtime-путь."""
    if bool(startup_state.dpi_autostart_initiated):
        log("Автозапуск DPI уже выполнен", "DEBUG")
        return

    startup_state.dpi_autostart_initiated = True

    runtime_snapshot = runtime_feature.objects.runtime_service.snapshot()
    initial_phase = str(getattr(runtime_snapshot, "phase", "") or "").strip().lower()
    published = bool(getattr(runtime_snapshot, "running", False)) and initial_phase == "running"
    if published or _own_engine_left_running():
        # Работает движок, которого эта программа не запускала: остался от
        # прежней версии после самообновления (обход не останавливался) или
        # после выхода без остановки обхода. Он со старыми lua и запуском —
        # передаём его работу своему движку из пресета без разрыва. Смотрим
        # сами, не дожидаясь монитора: при выключенном автозапуске иначе
        # прежний движок так и работал бы со старыми файлами.
        adopted_method = str(launch_method or getattr(runtime_snapshot, "launch_method", "") or "").strip().lower()
        if adopted_method not in ALL_LAUNCH_METHODS:
            log("Автозапуск DPI: активный процесс уже опубликован монитором", "INFO")
            return
        log("Автозапуск DPI: работает прежний движок — передаём работу своему без разрыва", "INFO")
        runtime_feature.objects.launch_runtime.start_dpi_async(
            selected_mode=None,
            launch_method=adopted_method,
            _startup_autostart=True,
        )
        return
    if initial_phase != "autostart_pending":
        log("Автозапуск DPI отключён", "INFO")
        _mark_runtime_stopped(runtime_feature)
        return

    resolved_method = str(
        launch_method or getattr(runtime_snapshot, "launch_method", "") or ""
    ).strip().lower()
    if resolved_method not in ALL_LAUNCH_METHODS:
        log(f"Автозапуск не поддерживается для метода: {resolved_method or 'unknown'}", "WARNING")
        _mark_runtime_stopped(runtime_feature)
        return

    log(f"Автозапуск передан в единый DPI runtime-путь: {resolved_method}", "INFO")
    runtime_feature.objects.launch_runtime.start_dpi_async(
        selected_mode=None,
        launch_method=resolved_method,
        _startup_autostart=True,
    )


def _own_engine_left_running() -> bool:
    """Работает ли движок (winws2/winws) из папки этой программы."""
    try:
        from winws_runtime.runtime.process_probe import get_canonical_winws_process_pids

        return any(get_canonical_winws_process_pids().values())
    except Exception:
        return False


def _mark_runtime_stopped(runtime_feature) -> None:
    runtime_feature.objects.runtime_service.mark_stopped(clear_error=True)


def _mark_runtime_failed(runtime_feature, message: str) -> None:
    runtime_feature.objects.runtime_service.mark_start_failed(str(message or "").strip())
