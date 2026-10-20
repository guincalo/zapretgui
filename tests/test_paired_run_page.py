"""Кнопка «С обходом и без» на странице BlockCheck: переключение Zapret между двумя проверками."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from blockcheck import paired_run
from blockcheck.ui.page import BlockcheckPage


class _Feature:
    pass


class _Bypass:
    def __init__(self, running: bool) -> None:
        self.running = running
        self.calls: list[str] = []

    def is_running(self) -> bool:
        return self.running

    def start(self) -> bool:
        self.calls.append("start")
        self.running = True
        return True

    def stop(self) -> bool:
        self.calls.append("stop")
        self.running = False
        return True


REPORT = {"scope": "main", "elapsed": 3.0, "problems": [], "services": [], "working": []}


class PairedPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _page(self, bypass: _Bypass | None = None):
        kwargs = {}
        if bypass is not None:
            kwargs = {"zapret_is_running": bypass.is_running, "start_zapret": bypass.start, "stop_zapret": bypass.stop}
        with patch.object(BlockcheckPage, "_request_page_initial_state_load", lambda self: None):
            page = BlockcheckPage(
                blockcheck_feature=_Feature(), dns_feature=_Feature(), create_strategy_scan_worker=lambda *_a, **_k: None, **kwargs
            )
        self.addCleanup(page.deleteLater)
        return page

    def test_button_is_there_but_off_without_zapret_control(self) -> None:
        page = self._page()

        self.assertFalse(page._paired_btn.isEnabled())
        page._on_start_paired()
        self.assertIsNone(page._paired)

    def test_full_pair_runs_two_checks_switches_zapret_and_puts_it_back(self) -> None:
        bypass = _Bypass(True)
        page = self._page(bypass)
        starts: list[str] = []
        page._on_start = lambda: starts.append("run")

        with patch("blockcheck.ui.page.QTimer.singleShot", lambda _ms, callback: callback()):
            page._on_start_paired()
            self.assertEqual((starts, page._paired.phase), (["run"], paired_run.PHASE_FIRST))
            self.assertFalse(page._paired_btn.isEnabled())

            page._paired_after(REPORT)
            self.assertEqual((bypass.calls, page._paired.phase), (["stop"], paired_run.PHASE_SWITCHING))
            page._paired_tick()
            self.assertEqual((starts, page._paired.phase), (["run", "run"], paired_run.PHASE_SECOND))

            page._paired_after(REPORT)

        self.assertEqual(bypass.calls, ["stop", "start"])
        self.assertTrue(bypass.running)
        self.assertFalse(page._paired.active)
        self.assertTrue(page._paired_btn.isEnabled())
        self.assertIn("Готово", page._status_label.text())

    def test_stopping_the_first_check_leaves_zapret_alone(self) -> None:
        bypass = _Bypass(True)
        page = self._page(bypass)
        page._on_start = lambda: None
        page._on_start_paired()

        page._paired_after(None)

        self.assertEqual(bypass.calls, [])
        self.assertFalse(page._paired.active)
        self.assertIn("остановлена", page._status_label.text())

    def test_stopping_between_the_checks_returns_zapret(self) -> None:
        bypass = _Bypass(True)
        page = self._page(bypass)
        page._on_start = lambda: None
        page._on_start_paired()
        page._paired_after(REPORT)
        self.assertFalse(bypass.running)

        page._on_stop()

        self.assertTrue(bypass.running)
        self.assertEqual(bypass.calls, ["stop", "start"])
        self.assertFalse(page._paired.active)

    def test_zapret_that_never_changes_ends_the_pair_after_the_wait(self) -> None:
        bypass = _Bypass(True)
        bypass.stop = lambda: bypass.calls.append("stop") or True  # команда принята, процесс остался
        page = self._page(bypass)
        page._paired = paired_run.PairedRun(is_running=bypass.is_running, start=bypass.start, stop=bypass.stop)
        page._on_start = lambda: None
        page._on_start_paired()
        page._paired_after(REPORT)

        page._paired_waited = paired_run.SETTLE_WAIT_SECONDS
        page._paired_tick()

        self.assertFalse(page._paired.active)
        self.assertIn("не сменил состояние", page._status_label.text())


if __name__ == "__main__":
    unittest.main()
