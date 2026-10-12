"""Журнал winws под нынешним именем находят все: обращение, страница журналов, очистка."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from log import log_kinds


class WinwsDebugLogTests(unittest.TestCase):
    def test_pattern_matches_the_name_the_preset_writes(self) -> None:
        import fnmatch

        from profile.settings import _build_stable_debug_log_file

        name = Path(_build_stable_debug_log_file("Default")).name
        self.assertTrue(any(fnmatch.fnmatch(name, pattern) for pattern in log_kinds.WINWS_DEBUG_LOG_PATTERNS), name)

    def test_blockcheck_support_takes_the_winws_log(self) -> None:
        import support_request_actions as actions
        from support_request_bundle import find_recent_logs

        with tempfile.TemporaryDirectory() as folder:
            for order, name in enumerate(("Default_debug.log", "blockcheck_run_1_full.json")):
                path = Path(folder) / name
                path.write_text("x", encoding="utf-8")
                os.utime(path, (1000 + order, 1000 + order))
            found = find_recent_logs(logs_folder=folder, patterns=actions.BLOCKCHECK_RECENT_PATTERNS, limit_per_pattern=2)
        self.assertIn("Default_debug.log", [path.name for path in found])

    def test_logs_page_counts_the_winws_log(self) -> None:
        from log import commands

        with tempfile.TemporaryDirectory() as folder:
            for name in ("Default_debug.log", "zapret_winws2_debug_20260101.log", "zapret_log_1.txt"):
                (Path(folder) / name).write_text("x", encoding="utf-8")
            with patch.object(commands, "LOGS_FOLDER", folder):
                stats = commands.build_stats()
        self.assertEqual((stats.debug_logs, stats.app_logs), (2, 1))


if __name__ == "__main__":
    unittest.main()
