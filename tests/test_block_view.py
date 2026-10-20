"""Отдельные проверки отчёта: карточка и текст берут слова из одного модуля ``block_view``."""

from __future__ import annotations

import unittest

from blockcheck.ui.result_cards_model import build_cards
from diagnostics import block_view as bv

REPORT = {
    "network": {"external_ip": "109.252.1.xxx", "provider": "MTS · AS8359", "lines": [{"state": "info", "name": "Внешний адрес", "text": "109.252.1.xxx (RU)"}]},
    "freeze": {
        "level": "fail",
        "headline": "Обрыв у части серверов",
        "advice": ["Подберите стратегию"],
        "servers": [
            {"provider": "OVH", "host": "a.example", "id": "ovh-1", "state": "freeze", "text": "оборвалось на 14 КБ", "direction": "download", "seconds": 1.2},
            {"provider": "OVH", "host": "b.example", "id": "ovh-2", "state": "ok", "text": "прошло", "direction": "", "seconds": 0.8},
        ],
    },
    "voice": {
        "level": "warn",
        "headline": "Звонки работают с перебоями",
        "advice": [],
        "items": [{"name": "Discord", "ok": True, "state": "ok", "text": "отвечает"}],
        "burst": {"state": "freeze", "text": "поток замирает после 20 пакетов", "servers": [{"name": "Discord", "series": ["20 из 30"]}]},
    },
    "telegram": {"level": "ok", "headline": "Telegram открывается", "advice": [], "items": [{"name": "DC1", "address": "149.154.175.50", "state": "ok", "text": "соединение за 20 мс"}]},
    "ipv6": {"state": "absent", "text": "в этой сети его нет"},
    "system": [{"key": "admin", "title": "Права администратора", "level": "ok", "text": "есть", "advice": ""}],
    "speed": {"level": "ok", "headline": "Скорость одинаковая", "names": None, "items": [{"name": "OVH", "host": "o.example", "domestic": False, "state": "ok", "text": "40 Мбит/с"}]},
    "compare": {
        "level": "warn",
        "headline": "Пресет чинит не всё",
        "preset": "Универсальный",
        "zapret_in": "current",
        "other_time": "2026-10-08T12:00:00",
        "helped": ["YouTube"],
        "not_helped": ["Telegram"],
        "notes": ["Это итог одного пресета"],
    },
    "dns_servers": {"level": "warn", "findings": [{"level": "warn", "text": "Провайдерский DNS подменяет адреса"}], "text": "1.1.1.1  ok"},
}


class OneSourceTests(unittest.TestCase):
    def test_every_card_is_built_from_a_block_view(self) -> None:
        cards = {card.key: card for card in build_cards(REPORT)}
        views = {view.key: view for view in bv.build_blocks(REPORT)}

        self.assertEqual(set(views), {"network", "hostings", "voice", "telegram", "dns_servers", "speed", "ipv6", "system", "compare"})
        for key, view in views.items():
            card = cards[key]
            self.assertEqual((card.title, card.level, card.status), (view.title, view.level, view.status), key)
            self.assertEqual([section.title for section in card.sections], [section.title for section in view.sections], key)
            self.assertEqual(
                [[(line.state, line.name, line.text) for line in section.lines] for section in card.sections],
                [[(row.level, row.title, row.text) for row in section.rows] for section in view.sections],
                key,
            )

    def test_text_has_banner_and_every_detail_row_but_not_the_screen_explanations(self) -> None:
        for key in ("network", "freeze", "voice", "telegram", "ipv6", "system", "speed", "dns_servers", "compare"):
            text = "\n".join(bv.text_lines(key, REPORT[key]))
            view = bv.build_block(key, REPORT[key])

            self.assertIn(f"━━━━━━━━ {view.title} ━━━━━━━━", text, key)
            for section in view.sections:
                if section.screen_only:
                    self.assertNotIn(section.title, text, key)
                    continue
                for row in section.rows:
                    self.assertIn(row.title, text, key)

    def test_section_named_like_the_block_is_not_repeated_under_the_banner(self) -> None:
        lines = bv.text_lines("ipv6", REPORT["ipv6"])

        self.assertEqual(lines, ["", "━━━━━━━━ IPv6 ━━━━━━━━", "   ❔ IPv6 в этой сети его нет"])

    def test_missing_data_gives_no_block_and_no_text(self) -> None:
        self.assertIsNone(bv.build_block("voice", None))
        self.assertEqual(bv.text_lines("freeze", {"servers": []}), [])
        self.assertEqual(bv.text_lines("system", []), [])
        self.assertEqual(bv.text_lines("unknown", {"x": 1}), [])

    def test_slow_by_name_leads_the_speed_block(self) -> None:
        speed = {
            "level": "ok",
            "headline": "Скорость одинаковая",
            "items": [],
            "names": {"headline": "Режут по имени", "server": "s", "control": "быстро", "items": [{"name": "YouTube", "host": "y.com", "state": "warn", "text": "медленно"}]},
        }
        view = bv.build_block("speed", speed)

        self.assertEqual((view.level, view.status), ("warn", "Замедляют: YouTube"))
        self.assertEqual(view.sections[0].title, "Режут по имени")

    def test_registry_block_is_text_only(self) -> None:
        summary = {"state": "fresh", "updated": 1000.0, "hosts": 12345, "networks": 7}
        text = "\n".join(bv.text_lines("registry", summary))

        self.assertIn("━━━━━━━━ Реестр РКН ━━━━━━━━", text)
        self.assertIn("Имён сайтов: 12 345", text)
        self.assertNotIn("registry", {view.key for view in bv.build_blocks({"registry": summary})})


if __name__ == "__main__":
    unittest.main()
