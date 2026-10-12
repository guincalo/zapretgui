"""Одно правило уровней: «открывается», порядок важности и худший уровень."""

from __future__ import annotations

import unittest

from diagnostics import levels
from diagnostics.verdict import Level


class LevelsTests(unittest.TestCase):
    def test_open_means_ok_or_warn(self) -> None:
        self.assertTrue(levels.is_open("ok"))
        self.assertTrue(levels.is_open(Level.WARN))
        self.assertFalse(levels.is_open("fail"))
        self.assertFalse(levels.is_open("unknown"))

    def test_rank_puts_broken_first_and_strangers_last(self) -> None:
        ranked = sorted(["ok", "что-то", "unknown", "fail", Level.WARN], key=levels.level_rank)
        self.assertEqual(ranked, ["fail", Level.WARN, "unknown", "ok", "что-то"])

    def test_worst(self) -> None:
        self.assertEqual(levels.worst(["ok", "warn", "unknown"]), "warn")
        self.assertEqual(levels.worst([]), "ok")
        self.assertEqual(levels.worst(["мусор"]), "ok")

    def test_engine_levels_match_report_strings(self) -> None:
        # Уровни отчёта — значения Level движка, и других быть не должно.
        self.assertEqual(set(levels.LEVELS), {level.value for level in Level})


if __name__ == "__main__":
    unittest.main()
