"""Итог вкладки «DNS подмена» один: из него и панель, и строка состояния, и история."""

from __future__ import annotations

import unittest

from dns.dns_check_plans import build_finish_plan, summarize_dns_results


class FinishPlanTests(unittest.TestCase):
    def test_failed_or_stopped_check_is_not_green(self) -> None:
        failed = build_finish_plan({})
        stopped = build_finish_plan({"stopped": True})
        self.assertEqual((failed.status_tone, failed.save_enabled), ("error", False))
        self.assertEqual(stopped.status_tone, "muted")
        self.assertNotIn("✅", failed.status_text + stopped.status_text)

    def test_spoofing_and_partial_and_ok(self) -> None:
        self.assertEqual(build_finish_plan({"domains": {"a": {"state": "spoofed"}}}).status_tone, "error")
        self.assertEqual(build_finish_plan({"domains": {"a": {"state": "unknown"}}}).status_tone, "warning")
        self.assertEqual(build_finish_plan({"domains": {"a": {"state": "ok"}}}).status_tone, "success")

    def test_addresses_all_set_locally_prove_nothing_about_the_provider(self) -> None:
        summary = summarize_dns_results({"domains": {"a": {"state": "local"}, "b": {"state": "local"}}})
        self.assertEqual(summary["kind"], "partial")
        self.assertNotIn("честный", summary["title"])

    def test_history_of_a_check_that_learned_nothing_is_unknown(self) -> None:
        from diagnostics.history import dns_check_entry

        entry = dns_check_entry({"domains": {"a": {"state": "unknown"}, "b": {"state": "unknown"}}})
        self.assertEqual(entry["level"], "unknown")
        partly = dns_check_entry({"domains": {"a": {"state": "unknown"}, "b": {"state": "ok"}}})
        self.assertEqual(partly["level"], "warn")


if __name__ == "__main__":
    unittest.main()
