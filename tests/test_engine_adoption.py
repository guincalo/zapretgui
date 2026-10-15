from __future__ import annotations

"""Приём движка, которого программа не запускала: он остался после самообновления."""

import contextlib
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from winws_runtime.engine import process_control, winapi

ENGINE = "C:\\Zapret\\Dev\\exe\\winws2.exe"


class _FakeWinApi:
    def __init__(self, *, image: str = ENGINE, exited: bool = False) -> None:
        self.image, self.exited = image, exited
        self.closed: list[int] = []
        self.terminated: list[int] = []

    def patches(self):
        return [
            patch.object(winapi, "is_available", return_value=True),
            patch.object(winapi, "open_process", return_value=77),
            patch.object(winapi, "query_image_path", side_effect=lambda _h: self.image),
            patch.object(winapi, "close_handle", side_effect=self.closed.append),
            patch.object(winapi, "wait_for_handle", side_effect=lambda _h, _t: self.exited),
            patch.object(winapi, "get_exit_code", return_value=0),
            patch.object(winapi, "terminate_process", side_effect=lambda h, _c: self.terminated.append(h)),
        ]


class AdoptedProcessTests(unittest.TestCase):
    def _adopt(self, fake: _FakeWinApi, pid: int = 4242):
        with contextlib.ExitStack() as stack:
            for item in fake.patches():
                stack.enter_context(item)
            process = process_control.adopt_engine_process(pid, ENGINE)
            if process is None:
                return None, None
            return process, (process.poll(), self._wait(process), process.terminate())

    @staticmethod
    def _wait(process):
        try:
            return process.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            return "timeout"

    def test_engine_from_its_own_folder_is_taken_by_pid(self) -> None:
        fake = _FakeWinApi()
        process, (polled, waited, _) = self._adopt(fake)

        self.assertEqual(process.pid, 4242)
        self.assertEqual(process._handle, 77)
        self.assertIsNone(polled)  # ещё работает
        self.assertEqual(waited, "timeout")
        self.assertEqual(fake.terminated, [77])

    def test_process_from_another_folder_is_not_taken(self) -> None:
        fake = _FakeWinApi(image="C:\\Other\\winws2.exe")
        process, _ = self._adopt(fake)

        self.assertIsNone(process)
        self.assertEqual(fake.closed, [77])  # хэндл не утёк


class RunnerAdoptionTests(unittest.TestCase):
    def _runner(self):
        from winws_runtime.runners.zapret2_runner import Winws2StrategyRunner

        runner = object.__new__(Winws2StrategyRunner)
        runner.winws_exe = ENGINE
        runner.running_process = None
        runner.current_launch_label = None
        runner._operation_guard = contextlib.nullcontext
        return runner

    def test_runner_takes_the_first_engine_and_remembers_extra_copies(self) -> None:
        runner = self._runner()
        records = [SimpleNamespace(pid=11), SimpleNamespace(pid=22)]
        adopted = SimpleNamespace(pid=11, poll=lambda: None)
        with patch(
            "winws_runtime.runtime.process_probe.find_expected_winws_processes", return_value=records
        ), patch("winws_runtime.runners.zapret2_runner.adopt_engine_process", return_value=adopted):
            self.assertTrue(runner.adopt_running_engine())

        self.assertIs(runner.running_process, adopted)
        self.assertEqual(runner._adopted_extra_pids, [22])

    def test_runner_that_already_holds_an_engine_adopts_nothing(self) -> None:
        runner = self._runner()
        runner.running_process = SimpleNamespace(poll=lambda: None)
        find = Mock()
        with patch("winws_runtime.runtime.process_probe.find_expected_winws_processes", find):
            self.assertFalse(runner.adopt_running_engine())
        find.assert_not_called()

    def test_extra_copies_are_stopped_one_by_one(self) -> None:
        runner = self._runner()
        runner._adopted_extra_pids = [22]
        extra = Mock(pid=22)
        with patch("winws_runtime.runners.zapret2_runner.adopt_engine_process", return_value=extra), patch(
            "winws_runtime.runners.zapret2_runner.stop_process", return_value=True
        ) as stop:
            runner.stop_extra_adopted_engines()

        stop.assert_called_once()
        self.assertIs(stop.call_args.args[0], extra)
        extra.close.assert_called_once_with()
        self.assertEqual(runner._adopted_extra_pids, [])


if __name__ == "__main__":
    unittest.main()
