from __future__ import annotations

import threading
import unittest
from unittest.mock import Mock, patch


class _Signal:
    def __init__(self) -> None:
        self._callbacks = []

    def connect(self, callback) -> None:
        self._callbacks.append(callback)

    def emit(self) -> None:
        for callback in list(self._callbacks):
            callback()


class _FakeProcessMonitor:
    instances = []

    def __init__(self, *, interval_ms: int) -> None:
        self.interval_ms = interval_ms
        self.processDetailsChanged = _Signal()
        self.finished = _Signal()
        self.stop = Mock()
        self.start = Mock()
        _FakeProcessMonitor.instances.append(self)


class ProcessMonitorNonblockingTests(unittest.TestCase):
    def test_process_monitor_stop_does_not_wait_for_sleeping_thread(self) -> None:
        from winws_runtime.monitoring.process_monitor import ProcessMonitorThread

        monitor = ProcessMonitorThread(interval_ms=2000)
        monitor.wait = Mock(return_value=True)
        monitor.quit = Mock()

        monitor.stop()

        self.assertFalse(monitor._running)
        monitor.quit.assert_called_once()
        monitor.wait.assert_not_called()

    def test_stopped_process_monitor_does_not_sleep_out_its_pause(self) -> None:
        from PyQt6.QtCore import QCoreApplication

        from winws_runtime.monitoring import process_monitor

        _app = QCoreApplication.instance() or QCoreApplication([])
        scanned = threading.Event()
        empty_scan = process_monitor.WinwsProcessScan(canonical_pids={}, foreign_paths={})

        def _scan():
            scanned.set()
            return empty_scan

        with patch.object(process_monitor, "scan_winws_processes", _scan):
            monitor = process_monitor.ProcessMonitorThread(interval_ms=60_000)
            monitor.start()
            try:
                self.assertTrue(scanned.wait(5))
                monitor.stop()
                # Поток уже в паузе на минуту: остановка обязана её прервать.
                self.assertTrue(monitor.wait(3000))
            finally:
                if monitor.isRunning():
                    monitor.terminate()
                    monitor.wait(3000)

    def _paused_for(self, shown_by_second) -> float:
        """Сколько секунд пауза между проверками, если окно видно/скрыто по секундам."""
        from winws_runtime.monitoring import process_monitor

        monitor = process_monitor.ProcessMonitorThread(interval_ms=2000, hidden_interval_ms=8000)
        now = [0.0]

        def wait(timeout=None) -> bool:
            now[0] += timeout
            return False

        monitor._stop_requested.wait = wait
        with (
            patch.object(process_monitor, "window_shown", lambda: shown_by_second(now[0])),
            patch.object(process_monitor.time, "monotonic", lambda: now[0]),
        ):
            self.assertFalse(monitor._pause_between_scans())
        return now[0]

    def test_hidden_window_slows_the_scan(self) -> None:
        self.assertAlmostEqual(self._paused_for(lambda _t: True), 2.0, delta=0.01)
        self.assertAlmostEqual(self._paused_for(lambda _t: False), 8.0, delta=0.01)
        # None — окно ещё не показывалось: опрашиваем как на виду.
        self.assertAlmostEqual(self._paused_for(lambda _t: None), 2.0, delta=0.01)

    def test_returned_window_cuts_the_long_pause_short(self) -> None:
        # Окно вернули на третьей секунде скрытой паузы: проверка идёт по короткому сроку.
        self.assertAlmostEqual(self._paused_for(lambda t: t >= 3.0), 3.0, delta=0.01)

    def test_progress_signals_are_silent_while_hidden(self) -> None:
        from PyQt6.QtCore import QCoreApplication

        from winws_runtime.monitoring import process_monitor

        _app = QCoreApplication.instance() or QCoreApplication([])
        empty_scan = process_monitor.WinwsProcessScan(canonical_pids={}, foreign_paths={})
        for shown, expected in ((False, 0), (True, 1)):
            started: list[int] = []
            monitor = process_monitor.ProcessMonitorThread(interval_ms=60_000)
            monitor.checkingStarted.connect(lambda: started.append(1))
            monitor._pause_between_scans = lambda: True  # один проход и выход
            with (
                patch.object(process_monitor, "scan_winws_processes", lambda: empty_scan),
                patch.object(process_monitor, "window_shown", return_value=shown),
            ):
                monitor.run()
            self.assertEqual(len(started), expected, shown)

    def test_hidden_interval_is_never_shorter_than_the_normal_one(self) -> None:
        from winws_runtime.monitoring.process_monitor import ProcessMonitorThread

        self.assertEqual(ProcessMonitorThread(interval_ms=9000, hidden_interval_ms=1000).hidden_interval_ms, 9000)

    def test_shutdown_waits_for_current_and_retired_monitors(self) -> None:
        from PyQt6.QtCore import QCoreApplication

        from winws_runtime.monitoring import process_monitor, process_monitor_manager

        _app = QCoreApplication.instance() or QCoreApplication([])
        empty_scan = process_monitor.WinwsProcessScan(canonical_pids={}, foreign_paths={})
        manager = process_monitor_manager.ProcessMonitorManager(observe_process_details=lambda _details: None)

        with patch.object(process_monitor, "scan_winws_processes", lambda: empty_scan):
            manager.initialize_process_monitor()
            first = manager.process_monitor
            manager.initialize_process_monitor()
            second = manager.process_monitor
            try:
                self.assertTrue(manager.shutdown(timeout_ms=3000))
                # После закрытия ни один поток слежения не выполняет Python-код.
                self.assertFalse(first.isRunning())
                self.assertFalse(second.isRunning())
                self.assertIsNone(manager.process_monitor)
                self.assertEqual(manager._retired_process_monitors, [])
            finally:
                for monitor in (first, second):
                    if monitor.isRunning():
                        monitor.terminate()
                        monitor.wait(3000)

    def test_shutdown_keeps_reference_to_monitor_that_did_not_stop(self) -> None:
        from winws_runtime.monitoring import process_monitor_manager

        manager = process_monitor_manager.ProcessMonitorManager(observe_process_details=lambda _details: None)
        stuck = Mock()
        stuck.wait.return_value = False
        manager.process_monitor = stuck

        self.assertFalse(manager.shutdown(timeout_ms=1))

        stuck.stop.assert_called_once()
        # Работающий поток нельзя отпускать: Qt уничтожит его объект.
        self.assertEqual(manager._retired_process_monitors, [stuck])

    def test_reinitializing_process_monitor_keeps_old_thread_until_finished(self) -> None:
        from winws_runtime.monitoring import process_monitor_manager

        _FakeProcessMonitor.instances = []
        manager = process_monitor_manager.ProcessMonitorManager(observe_process_details=lambda _details: None)

        with patch(
            "winws_runtime.monitoring.process_monitor.ProcessMonitorThread",
            _FakeProcessMonitor,
        ):
            manager.initialize_process_monitor()
            first = manager.process_monitor
            manager.initialize_process_monitor()

        self.assertIsNot(first, manager.process_monitor)
        first.stop.assert_called_once()
        self.assertIn(first, manager._retired_process_monitors)

        first.finished.emit()

        self.assertNotIn(first, manager._retired_process_monitors)


if __name__ == "__main__":
    unittest.main()
