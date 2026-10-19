import threading
import time

from PyQt6.QtCore import QThread, pyqtSignal

from core.runtime.presence import return_settle_remaining_ms, window_shown
from winws_runtime.runtime.process_probe import WinwsProcessScan, scan_winws_processes

# Пока окно скрыто (трей, свёрнуто), опрос реже: снимок всех процессов Windows
# и сигналы окну каждые 2 с будят программу и тянут с диска страницы, которые
# система уже отобрала, а смотреть на результат некому. Падение winws в трее
# всё равно замечается за несколько секунд, значок трея обновится.
HIDDEN_INTERVAL_MS = 8000
# Как часто пауза проверяет, не вернули ли окно: на виду опрос снова частый.
_PAUSE_SLICE_S = 1.0


class ProcessMonitorThread(QThread):
    """
    Следит за каноническими процессами winws.exe/winws2.exe через WinAPI.
    Шлёт сигнал когда состояние (запущен/остановлен) изменилось.
    Параллельно замечает посторонние winws-процессы (то же имя, чужой путь).
    """
    processStatusChanged = pyqtSignal(bool)          # True / False
    processDetailsChanged = pyqtSignal(dict)         # имя exe -> список PID
    foreignProcessesChanged = pyqtSignal(dict)       # pid -> путь чужого winws
    checkingStarted = pyqtSignal()                   # Начало проверки
    checkingFinished = pyqtSignal()                  # Конец проверки

    def __init__(self, interval_ms: int = 5000, hidden_interval_ms: int = HIDDEN_INTERVAL_MS):
        """
        Args:
            interval_ms: Интервал проверки в миллисекундах (по умолчанию 5 сек)
            hidden_interval_ms: Интервал, пока окно скрыто (не меньше основного)
        """
        super().__init__()
        self.interval_ms   = interval_ms
        self.hidden_interval_ms = max(int(hidden_interval_ms), int(interval_ms))
        self._running      = True
        # Пауза между проверками ждёт это событие, а не просто спит: остановка
        # будит поток сразу, и его можно дождаться за миллисекунды.
        self._stop_requested = threading.Event()
        self._cur_state: bool | None = None
        self._cur_details: dict[str, list[int]] | None = None
        self._cur_foreign: dict[int, str] | None = None

    def _scan_processes_fast(self) -> WinwsProcessScan:
        """
        Канонические winws.exe/winws2.exe (PID) и посторонние (PID -> путь)
        за один снимок процессов.

        Канонический здесь означает:
        - имя процесса совпадает;
        - полный путь процесса совпадает с ожидаемым `exe/winws*.exe` проекта.
        """
        try:
            return scan_winws_processes()
        except Exception:
            return WinwsProcessScan(canonical_pids={}, foreign_paths={})

    # ------------------------- ОСНОВНОЙ ЦИКЛ --------------------------
    def run(self):
        from log.log import log            # импорт здесь, чтобы не было циклических импортов
        log("Process-monitor thread started (WinAPI canonical mode)", level="INFO")

        while self._running:
            # Мигание «идёт проверка» нужно только на виду; решаем один раз на
            # проход, чтобы пара сигналов не разошлась.
            show_progress = window_shown() is not False
            try:
                # 🔄 Сигнализируем о начале проверки
                if show_progress:
                    self.checkingStarted.emit()

                scan = self._scan_processes_fast()
                details = scan.canonical_pids
                is_running = bool(details)

                # 🔄 Сигнализируем об окончании проверки
                if show_progress:
                    self.checkingFinished.emit()

                # Если детали изменились — отдаём сигнал в GUI (важно: PID может поменяться без смены bool)
                if details != self._cur_details:
                    self._cur_details = details
                    self.processDetailsChanged.emit(details)

                foreign = scan.foreign_paths
                if foreign != self._cur_foreign:
                    self._cur_foreign = foreign
                    self.foreignProcessesChanged.emit(foreign)

                # Если состояние изменилось — отдаём сигнал в GUI
                if is_running != self._cur_state:
                    self._cur_state = is_running
                    log(f"canonical winws state → {is_running}", level="DEBUG")
                    self.processStatusChanged.emit(is_running)

            except Exception as e:
                from log.log import log

                log(f"Ошибка в потоке мониторинга: {e}", level="❌ ERROR")
                if show_progress:
                    self.checkingFinished.emit()  # На случай ошибки тоже завершаем

            if self._pause_between_scans():
                break

    def _pause_between_scans(self) -> bool:
        """Ждёт до следующей проверки. True — просили остановиться.

        На виду пауза ``interval_ms``, при скрытом окне ``hidden_interval_ms``;
        окно вернули — пауза укорачивается, и проверка идёт сразу по её сроку.
        """
        started = time.monotonic()
        while True:
            # Первые секунды после возврата из долгого фона окно само тянет память
            # с диска: частый опрос процессов начинается после них.
            shown = window_shown() is not False and return_settle_remaining_ms() == 0
            interval_s = (self.interval_ms if shown else self.hidden_interval_ms) / 1000.0
            remaining = interval_s - (time.monotonic() - started)
            if remaining <= 0:
                return False
            if self._stop_requested.wait(min(remaining, _PAUSE_SLICE_S)):
                return True

    # ------------------------ СТАНДАРТНЫЙ STOP ------------------------
    def stop(self):
        """Просит поток остановиться и не ждёт его (окно не подвисает)."""
        self._running = False
        self._stop_requested.set()
        self.quit()
