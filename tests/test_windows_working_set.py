"""Снимок страниц памяти и подтягивание их пачкой (utils.windows_working_set)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils import windows_working_set as ws  # noqa: E402


class MergePagesTests(unittest.TestCase):
    def test_neighbouring_pages_become_one_range(self) -> None:
        page = ws.PAGE_SIZE
        pages = [0x10000, 0x10000 + page, 0x10000 + 2 * page, 0x40000, 0x80000, 0x80000 + page]

        merged = ws._merge_pages(pages)

        self.assertEqual(merged, [(0x10000, 3 * page), (0x40000, page), (0x80000, 2 * page)])

    def test_empty_input_gives_no_ranges(self) -> None:
        self.assertEqual(ws._merge_pages([]), [])


class OutsideWindowsTests(unittest.TestCase):
    def test_other_systems_do_nothing(self) -> None:
        with patch.object(ws.sys, "platform", "linux"):
            self.assertIsNone(ws.snapshot_working_set())
            snapshot = ws.WorkingSetSnapshot(ranges=((0x1000, 0x1000),), total_bytes=0x1000, taken_ms=0.0)
            self.assertIsNone(ws.prefetch_working_set(snapshot))

    def test_prefetch_without_snapshot_does_nothing(self) -> None:
        self.assertIsNone(ws.prefetch_working_set(None))
        empty = ws.WorkingSetSnapshot(ranges=(), total_bytes=0, taken_ms=0.0)
        self.assertIsNone(ws.prefetch_working_set(empty))

    def test_module_never_empties_the_working_set(self) -> None:
        source = Path(ws.__file__).read_text(encoding="utf-8")
        self.assertNotIn("EmptyWorkingSet(", source)
        self.assertNotIn("SetProcessWorkingSetSize", source)


if __name__ == "__main__":
    unittest.main()
