"""Парный запуск BlockCheck: порядок действий с обходом без окон."""

from __future__ import annotations

import unittest

from blockcheck import paired_run as pr


class _Bypass:
    def __init__(self, running: bool, *, start_ok: bool = True, stop_ok: bool = True) -> None:
        self.running = running
        self.start_ok, self.stop_ok = start_ok, stop_ok
        self.calls: list[str] = []

    def is_running(self) -> bool:
        return self.running

    def start(self) -> bool:
        self.calls.append("start")
        if self.start_ok:
            self.running = True
        return self.start_ok

    def stop(self) -> bool:
        self.calls.append("stop")
        if self.stop_ok:
            self.running = False
        return self.stop_ok

    def paired(self) -> pr.PairedRun:
        return pr.PairedRun(is_running=self.is_running, start=self.start, stop=self.stop)


class FlowTests(unittest.TestCase):
    def test_running_bypass_is_checked_then_stopped_checked_and_started_again(self) -> None:
        bypass = _Bypass(True)
        run = bypass.paired()

        run.begin()
        self.assertEqual((run.phase, run.first_label, run.second_label), (pr.PHASE_FIRST, "с обходом", "без обхода"))
        self.assertEqual(run.switch(), "")
        self.assertTrue(run.settled())
        run.second_started()
        self.assertEqual(run.finish(), "")

        self.assertEqual(bypass.calls, ["stop", "start"])
        self.assertEqual((bypass.running, run.phase), (True, pr.PHASE_IDLE))

    def test_stopped_bypass_is_started_for_the_second_check_and_stopped_after(self) -> None:
        bypass = _Bypass(False)
        run = bypass.paired()

        run.begin()
        self.assertEqual((run.first_label, run.second_label), ("без обхода", "с обходом"))
        run.switch()
        self.assertTrue(run.settled())
        run.finish()

        self.assertEqual(bypass.calls, ["start", "stop"])
        self.assertFalse(bypass.running)

    def test_not_settled_until_the_bypass_really_changed(self) -> None:
        bypass = _Bypass(True)
        bypass.stop = lambda: True  # команда принята, но процесс ещё работает
        run = bypass.paired()

        run.begin()
        run.switch()
        self.assertFalse(run.settled())


class FailureTests(unittest.TestCase):
    def test_bypass_that_does_not_start_ends_the_pair_with_a_reason(self) -> None:
        bypass = _Bypass(False, start_ok=False)
        run = bypass.paired()

        run.begin()
        reason = run.switch()

        self.assertIn("не запустился", reason)
        self.assertEqual(run.phase, pr.PHASE_IDLE)
        # Ничего не переключалось, возвращать нечего.
        self.assertEqual(bypass.calls, ["start"])

    def test_cancel_after_the_switch_returns_the_bypass_as_it_was(self) -> None:
        bypass = _Bypass(True)
        run = bypass.paired()
        run.begin()
        run.switch()

        self.assertEqual(run.finish(), "")
        self.assertTrue(bypass.running)

    def test_cancel_before_the_switch_touches_nothing(self) -> None:
        bypass = _Bypass(True)
        run = bypass.paired()
        run.begin()

        self.assertEqual(run.finish(), "")
        self.assertEqual(bypass.calls, [])

    def test_failed_restore_is_reported(self) -> None:
        bypass = _Bypass(True)
        run = bypass.paired()
        run.begin()
        run.switch()
        bypass.start_ok = False

        self.assertIn("не вернулся", run.finish())

    def test_exception_in_a_command_is_a_reason_not_a_crash(self) -> None:
        def boom():
            raise RuntimeError("служба занята")

        run = pr.PairedRun(is_running=lambda: True, start=lambda: True, stop=boom)
        run.begin()

        self.assertEqual(run.switch(), "служба занята")
        self.assertEqual(run.phase, pr.PHASE_IDLE)


if __name__ == "__main__":
    unittest.main()
