from log.log import log
from settings.mode import ALL_LAUNCH_METHODS

# Пока поток проверки жив, его приёмник должен жить тоже: ссылка не даёт сборщику мусора его убрать.
_PENDING_PROBES: set = set()


def _adopt_leftover_engine(runtime_feature, runtime_snapshot, launch_method: str | None) -> None:
    """Работает движок, которого эта программа не запускала: передаём его работу своему без разрыва."""
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


def _probe_off_the_ui_thread(decide) -> None:
    """Смотрит, жив ли движок из папки программы, в фоновом потоке; ``decide(bool)`` вызывается в потоке окна.

    Перечень процессов Windows — не работа для потока окна: при выключенном автозапуске он занимал бы
    его на каждом старте программы.
    """
    import threading

    from PyQt6.QtCore import QObject, pyqtSignal

    class _Relay(QObject):
        done = pyqtSignal(bool)

        def __init__(self) -> None:
            super().__init__()
            self.done.connect(self._on_done)

        def _on_done(self, left_running: bool) -> None:
            _PENDING_PROBES.discard(self)
            try:
                decide(bool(left_running))
            finally:
                self.deleteLater()

    relay = _Relay()
    _PENDING_PROBES.add(relay)

    def _work() -> None:
        relay.done.emit(_own_engine_left_running())

    threading.Thread(target=_work, name="autostart-leftover-probe", daemon=True).start()


def start_dpi_autostart(
    startup_state,
    *,
    runtime_feature,
    ui_state,
    launch_method: str | None = None,
    probe=None,
) -> None:
    """Запускает DPI autostart через общий runtime-путь.

    ``probe(decide)`` — как проверить, не остался ли движок от прежней версии, когда автозапуск выключен;
    по умолчанию это фоновый поток (``_probe_off_the_ui_thread``).
    """
    if bool(startup_state.dpi_autostart_initiated):
        log("Автозапуск DPI уже выполнен", "DEBUG")
        return

    startup_state.dpi_autostart_initiated = True

    runtime_snapshot = runtime_feature.objects.runtime_service.snapshot()
    initial_phase = str(getattr(runtime_snapshot, "phase", "") or "").strip().lower()
    published = bool(getattr(runtime_snapshot, "running", False)) and initial_phase == "running"
    if published:
        # Монитор уже видит работающий движок, которого эта программа не запускала (остался от прежней версии
        # после самообновления или после выхода без остановки обхода): со старыми lua и запуском —
        # передаём его работу своему движку из пресета без разрыва.
        _adopt_leftover_engine(runtime_feature, runtime_snapshot, launch_method)
        return
    if initial_phase != "autostart_pending":
        # Автозапуск выключен, но монитор мог ещё не заметить движок от прежней версии: смотрим сами, иначе
        # он так и работал бы со старыми файлами.
        def _decide(left_running: bool) -> None:
            if left_running:
                _adopt_leftover_engine(runtime_feature, runtime_snapshot, launch_method)
                return
            log("Автозапуск DPI отключён", "INFO")
            _mark_runtime_stopped(runtime_feature)

        (probe or _probe_off_the_ui_thread)(_decide)
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
    """Работает ли движок (winws2/winws) из папки этой программы. Вызывается только из фонового потока."""
    try:
        from winws_runtime.runtime.process_probe import is_any_canonical_winws_running

        return bool(is_any_canonical_winws_running())
    except Exception:
        return False


def _mark_runtime_stopped(runtime_feature) -> None:
    runtime_feature.objects.runtime_service.mark_stopped(clear_error=True)


def _mark_runtime_failed(runtime_feature, message: str) -> None:
    runtime_feature.objects.runtime_service.mark_start_failed(str(message or "").strip())
