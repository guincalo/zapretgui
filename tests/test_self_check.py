"""Самопроверка: соединение с несуществующим адресом не должно «устанавливаться»."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from diagnostics import problems, sections, self_check, system_state
from diagnostics.run_context import Run
from utils.socket_cancel import SocketCancel


class _Sock:
    """Сокет, который на connect делает то, что ему сказали."""

    def __init__(self, outcome) -> None:
        self.outcome = outcome
        self.connected_to = None

    def settimeout(self, _seconds) -> None:
        pass

    def connect(self, address) -> None:
        self.connected_to = address
        if isinstance(self.outcome, BaseException):
            raise self.outcome

    def shutdown(self, _how) -> None:
        pass

    def close(self) -> None:
        pass


def _collect(outcome, cancel: SocketCancel | None = None) -> tuple[self_check.SelfFacts, _Sock]:
    sock = _Sock(outcome)
    with patch.object(self_check.socket, "socket", return_value=sock):
        return self_check.collect(cancel=cancel), sock


class CollectTests(unittest.TestCase):
    def test_accepted_connection_to_documentation_address_is_interception(self) -> None:
        facts, sock = _collect(None)

        self.assertEqual(sock.connected_to, (self_check.DOCUMENTATION_ADDRESS, 443))
        self.assertTrue(facts.accepted)

    def test_silence_and_refusal_are_normal(self) -> None:
        for outcome in (TimeoutError(), ConnectionRefusedError(), OSError(10051, "сеть недоступна")):
            with self.subTest(outcome=type(outcome).__name__):
                facts, _sock = _collect(outcome)
                self.assertIs(facts.accepted, False)

    def test_cancelled_probe_says_nothing(self) -> None:
        cancel = SocketCancel()
        cancel.cancel()

        facts, sock = _collect(None, cancel)

        self.assertIsNone(facts.accepted)
        self.assertIsNone(sock.connected_to)


class JudgeTests(unittest.TestCase):
    def test_only_accepted_connection_raises_a_warning(self) -> None:
        self.assertIsNone(self_check.judge(self_check.SelfFacts(False)))
        self.assertIsNone(self_check.judge(self_check.SelfFacts(None)))

        item = self_check.judge(self_check.SelfFacts(True))

        self.assertEqual((item.key, item.level), ("self_check", system_state.LEVEL_WARN))
        self.assertIn(self_check.DOCUMENTATION_ADDRESS, item.text)
        self.assertTrue(item.advice)

    def test_warning_reaches_computer_section_and_problems(self) -> None:
        run = Run(None, workers=2)
        self.addCleanup(run.close)
        with (
            patch.object(system_state, "collect_facts", return_value=None),
            patch.object(system_state, "judge", return_value=()),
            patch.object(self_check, "collect", return_value=self_check.SelfFacts(True)),
        ):
            items = sections.check_system(run, {})

        self.assertEqual([item.key for item in items], ["self_check"])
        self.assertIn(system_state.LEVEL_WARN, problems.SYSTEM_PROBLEM_LEVEL)


if __name__ == "__main__":
    unittest.main()
