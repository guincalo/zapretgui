"""Прошлая проверка «DNS-серверов» для плиток «Настройки DNS»: какая берётся и какая нет."""

from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from dns import line_check
from dns.server_check_plans import CARD_NETWORK, CARD_OK, CARD_UNKNOWN
from dns.state import LineCheck

NOW = datetime(2026, 10, 13, 12, 0)
REPORT = object()
CARDS = (
    SimpleNamespace(server="Quad9", status=CARD_NETWORK, note="подменяет обычные ответы"),
    SimpleNamespace(server="Google DNS", status=CARD_OK, note=""),
    # До сервера проверка не дошла — о нём ничего не известно.
    SimpleNamespace(server="AdGuard", status=CARD_UNKNOWN, note=""),
)


def _settings(*runs: dict) -> dict:
    return {"blockcheck": {"servers_history": list(runs)}}


class LineCheckTests(unittest.TestCase):
    def _load(self, settings: dict, report=REPORT):
        with (
            patch("settings.store.read_settings", return_value=settings),
            patch("dns.commands.load_past_server_check_report", return_value=report) as loader,
            patch("dns.server_check_plans.build_cards", return_value=CARDS),
        ):
            return line_check.load_line_check(now=NOW), loader

    def test_last_saved_check_gives_card_per_known_server(self) -> None:
        old = {"time": "2026-10-10T10:00:00", "log_file": "old.json"}
        last = {"time": "2026-10-12T21:05:00", "log_file": "last.json"}

        found, loader = self._load(_settings(old, last))

        loader.assert_called_once_with("last.json")
        self.assertEqual(found.time, "2026-10-12T21:05:00")
        self.assertEqual(
            found.cards, (("Quad9", CARD_NETWORK, "подменяет обычные ответы"), ("Google DNS", CARD_OK, ""))
        )

    def test_old_check_is_not_shown(self) -> None:
        found, loader = self._load(_settings({"time": "2026-09-20T10:00:00", "log_file": "x.json"}))

        self.assertIsNone(found)
        loader.assert_not_called()

    def test_no_history_unreadable_time_or_lost_file_give_nothing(self) -> None:
        for settings, report in (
            ({}, REPORT),
            (_settings({"time": "2026-10-12T21:05:00"}), REPORT),
            (_settings({"time": "вчера", "log_file": "x.json"}), REPORT),
            (_settings({"time": "2026-10-12T21:05:00", "log_file": "x.json"}), None),
        ):
            with self.subTest(settings=settings, report=report is not None):
                self.assertIsNone(self._load(settings, report)[0])

    def test_card_lookup_by_server_name(self) -> None:
        check = LineCheck(time="2026-10-12T21:05:00", cards=(("Quad9", CARD_OK, ""),))

        self.assertEqual(check.card("Quad9"), (CARD_OK, ""))
        self.assertIsNone(check.card("Google DNS"))


if __name__ == "__main__":
    unittest.main()
