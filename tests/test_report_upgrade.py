"""Старые отчёты открываются новой версией: внешний адрес в них скрывается так же, как в новых."""

from __future__ import annotations

import unittest

from diagnostics.report_upgrade import REPORT_VERSION, upgrade


def _report(version: int | None) -> dict:
    report = {
        "services": [],
        "problems": [],
        "network": {
            "external_ip": "109.252.1.77",
            "local_ip": "192.168.1.5",
            "prefix": "109.252.1.64/28",
            "provider": "MTS · AS8359",
            "lines": [
                {"state": "info", "name": "Внешний адрес", "text": "109.252.1.77 (RU)"},
                {"state": "info", "name": "Провайдер", "text": "MTS · AS8359, RU"},
            ],
        },
        "text": ["━━━━━━━━ Ваша сеть ━━━━━━━━", "ℹ️ Внешний адрес: 109.252.1.77 (RU)", "Ещё строка"],
    }
    if version is not None:
        report["report_version"] = version
    return report


class MaskTests(unittest.TestCase):
    def test_version_two_report_gets_its_external_address_hidden_everywhere(self) -> None:
        result = upgrade(_report(2))
        network = result["network"]

        self.assertEqual(result["report_version"], REPORT_VERSION)
        self.assertEqual((network["external_ip"], network["prefix"], network["ip_masked"]), ("109.252.1.xxx", "109.252.1.xxx/28", True))
        self.assertEqual(network["local_ip"], "192.168.1.5")
        self.assertEqual(network["lines"][0]["text"], "109.252.1.xxx (RU)")
        self.assertEqual(network["provider"], "MTS · AS8359")
        self.assertNotIn("109.252.1.77", str(result))

    def test_a_report_without_a_version_is_upgraded_in_one_pass(self) -> None:
        result = upgrade(_report(None))

        self.assertEqual(result["report_version"], REPORT_VERSION)
        self.assertEqual(result["network"]["external_ip"], "109.252.1.xxx")

    def test_current_report_is_returned_as_it_is(self) -> None:
        report = _report(REPORT_VERSION)

        self.assertIs(upgrade(report), report)

    def test_the_original_report_is_not_changed(self) -> None:
        report = _report(2)
        upgrade(report)

        self.assertEqual(report["network"]["external_ip"], "109.252.1.77")
        self.assertIn("109.252.1.77", report["text"][1])

    def test_report_without_the_network_section_only_gets_the_version(self) -> None:
        result = upgrade({"report_version": 2, "services": []})

        self.assertEqual(result, {"report_version": REPORT_VERSION, "services": []})


if __name__ == "__main__":
    unittest.main()
