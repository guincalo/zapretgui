"""Окно только что вернулось из долгого фона: фоновая работа программы ждёт (core.runtime.presence)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.runtime import presence  # noqa: E402


class ReturnSettleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = (presence._window_shown, presence._shown_since, presence._hidden_since, presence._hidden_for_s)
        presence._window_shown = None
        presence._shown_since = None
        presence._hidden_since = 0.0
        presence._hidden_for_s = 0.0
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        (presence._window_shown, presence._shown_since, presence._hidden_since, presence._hidden_for_s) = self._saved

    def test_long_background_then_return_makes_the_work_wait_ten_seconds(self) -> None:
        presence.note_window_shown(False, now=100.0)
        presence.note_window_shown(True, now=1700.0)  # 1600 с в трее, как в журнале пользователя

        self.assertEqual(presence.return_settle_remaining_ms(now=1700.0), 10_000)
        self.assertEqual(presence.return_settle_remaining_ms(now=1706.5), 3_500)
        self.assertEqual(presence.return_settle_remaining_ms(now=1710.0), 0)
        self.assertEqual(presence.return_settle_remaining_ms(now=1800.0), 0)

    def test_short_minimize_does_not_delay_anything(self) -> None:
        presence.note_window_shown(False, now=100.0)
        presence.note_window_shown(True, now=110.0)  # 10 с: страницы памяти ещё на месте

        self.assertEqual(presence.return_settle_remaining_ms(now=110.0), 0)

    def test_hidden_window_and_unknown_state_wait_for_nothing(self) -> None:
        self.assertEqual(presence.return_settle_remaining_ms(now=5.0), 0)
        presence.note_window_shown(False, now=100.0)
        self.assertEqual(presence.return_settle_remaining_ms(now=5000.0), 0)

    def test_repeated_notes_of_the_same_state_do_not_restart_the_clock(self) -> None:
        presence.note_window_shown(False, now=100.0)
        presence.note_window_shown(False, now=900.0)  # второе «скрыто» подряд: отсчёт скрытия от первого
        presence.note_window_shown(True, now=1700.0)
        presence.note_window_shown(True, now=1705.0)  # и второе «видно» подряд не сдвигает возврат

        self.assertEqual(presence.return_settle_remaining_ms(now=1705.0), 5_000)

    def test_start_in_tray_counts_the_time_since_the_process_started(self) -> None:
        # Окно с запуска скрыто и ни разу не показывалось: «скрыто» не отмечалось.
        presence._hidden_since = 100.0
        presence.note_window_shown(True, now=1700.0)

        self.assertEqual(presence.return_settle_remaining_ms(now=1700.0), 10_000)


if __name__ == "__main__":
    unittest.main()
