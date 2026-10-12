"""Сервисы на экран по ходу проверки: готовый — сразу, и каждый собирается один раз."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from diagnostics import sections


def _service(targets: int) -> SimpleNamespace:
    return SimpleNamespace(targets=tuple(range(targets)))


class LiveServicesTests(unittest.TestCase):
    def test_ready_services_go_out_in_check_order_each_built_once(self) -> None:
        services = {"youtube": _service(2), "discord": _service(1), "telegram": _service(1)}
        published: list[list[str]] = []
        built: list[str] = []

        def services_report(chosen, _verdicts, collected):
            (key,) = chosen
            built.append(key)
            return [{"key": key, "probes": len(collected[key])}]

        with patch.object(sections.report_text, "services_report", side_effect=services_report):
            on_probe = sections.live_services(
                services, lambda _service, probes: probes, lambda items: published.append([item["key"] for item in items])
            )
            on_probe("youtube", "адрес 1")
            on_probe("telegram", "адрес")
            on_probe("youtube", "адрес 2")
            on_probe("discord", "адрес")

        # Половина адресов YouTube — ещё не готов; порядок — как в списке проверки.
        self.assertEqual(published, [["telegram"], ["youtube", "telegram"], ["youtube", "discord", "telegram"]])
        self.assertEqual(sorted(built), ["discord", "telegram", "youtube"])


if __name__ == "__main__":
    unittest.main()
