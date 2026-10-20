"""Единый модуль слов отчёта: карточка, текст проверки и «Проверка домена» берут строки отсюда."""

from __future__ import annotations

import unittest

from blockcheck.ui import result_cards_model as cards
from diagnostics import service_view as view
from diagnostics.service_view import ViewRow


def _target(**fields) -> dict:
    base = {
        "host": "youtube.com",
        "purpose": "сайт",
        "main": True,
        "ok": True,
        "state": "ok",
        "text": "открывается (12 мс, TLS 1.3)",
        "address": "203.0.113.9",
        "protocols": [{"key": "tls13", "title": "TLS 1.3", "state": "ok", "text": "открывается"}],
        "dns_state": "ok",
        "dns_reason": "адрес совпадает с эталоном",
        "dns_detail": "DNS системы: 203.0.113.9 · эталон: 203.0.113.9",
    }
    return {**base, **fields}


def _service(**fields) -> dict:
    base = {"key": "youtube", "label": "YouTube", "level": "ok", "targets": [_target()]}
    return {**base, **fields}


def _rows(item: dict) -> dict[str, ViewRow]:
    return {row.title: row for row in view.target_rows(item)}


class TargetRowsTests(unittest.TestCase):
    def test_every_measurement_has_its_row_with_a_level(self) -> None:
        item = _target(
            ok=False,
            state="dpi",
            cause_text="Блокировка по имени сайта",
            kind_title="Обрыв после 16 КБ",
            quic="blocked_by_name",
            quic_text="блокируется по имени",
            volume="cut",
            volume_text="замирает после 14 КБ",
            transfer={"code": "pause", "level": "warn", "text": "пауза 2,9 с"},
            outside="up",
            note="проверен запасной узел",
        )
        rows = _rows(item)

        self.assertEqual(rows["Соединение"].level, "fail")
        self.assertEqual(rows["Как блокируют"], ViewRow("fail", "Как блокируют", "Блокировка по имени сайта"))
        self.assertEqual(rows["Вид блокировки"].text, "Обрыв после 16 КБ")
        self.assertEqual(rows["QUIC (UDP 443)"].level, "warn")
        self.assertEqual(rows["Объём загрузки"].level, "warn")
        self.assertEqual(rows["Скорость ответа"], ViewRow("warn", "Скорость ответа", "пауза 2,9 с"))
        self.assertIn("закрыт он именно на вашей линии", rows["С сервера Zapret DNS в Нидерландах"].text)
        self.assertEqual(rows["Заметка"].text, "проверен запасной узел")

    def test_quic_levels_follow_the_verdict(self) -> None:
        for code, level in (("ok", "ok"), ("blocked_by_name", "warn"), ("silent", "info")):
            self.assertEqual(_rows(_target(quic=code, quic_text="слова"))["QUIC (UDP 443)"].level, level, code)

    def test_unknown_transfer_and_old_record_add_nothing(self) -> None:
        rows = _rows(_target(transfer={"code": "unknown", "level": "unknown", "text": "мало данных"}))

        self.assertNotIn("Скорость ответа", rows)
        self.assertEqual(list(_rows({"host": "a.com", "text": "открывается", "ok": True})), ["Соединение"])

    def test_dns_spoofing_is_a_warning_and_the_detail_is_a_reference(self) -> None:
        rows = _rows(_target(dns_state="spoofed", dns_reason="адрес подменён"))

        self.assertEqual(rows["DNS"].level, "warn")
        self.assertEqual(rows["Адреса DNS"].level, "info")


class SectionsTests(unittest.TestCase):
    STEPS = [
        {"key": "dns", "title": "DNS", "level": "ok", "text": "совпал", "confidence": "high"},
        {"key": "data", "title": "Передача данных", "level": "warn", "text": "пауза", "confidence": ""},
    ]

    def test_path_goes_only_after_the_main_address(self) -> None:
        main = _target(path=self.STEPS)
        other = _target(host="i.ytimg.com", main=False, path=self.STEPS)

        titles = [section.title for section in view.service_sections(_service(targets=[main, other]))]

        self.assertEqual(titles, ["youtube.com — сайт", "Путь запроса", "i.ytimg.com — сайт"])
        path = view.path_section(main)
        self.assertEqual(path.rows[0].text, "совпал (уверенность высокая)")
        self.assertEqual(path.rows[1].text, "пауза")

    def test_service_level_sections_come_in_a_fixed_order(self) -> None:
        service = _service(
            level="fail",
            kind="sni",
            advice=["Подберите стратегию"],
            dns_note="DNS подменяет адрес",
            names={"headline": "Режут по имени", "server": "x", "control": "быстро", "items": [{"name": "A", "host": "a.com", "state": "warn", "text": "медленно"}]},
        )

        titles = [section.title for section in view.service_sections(service)]

        self.assertEqual(titles[:3], ["youtube.com — сайт", "Что делать", "DNS"])
        self.assertEqual(titles[3:5], ["Режут по имени", "Как проверяется замедление по имени"])
        self.assertEqual(len(titles), 6)

    def test_kind_is_named_only_for_a_broken_site(self) -> None:
        self.assertEqual(view.site_kind({"level": "ok", "kind": "sni"}), "")
        self.assertEqual(view.site_kind({"level": "fail", "kind": "sni"}), "sni")
        self.assertEqual(view.site_kind({"level": "fail", "kind": "other"}), "")

    def test_text_has_title_then_rows_with_level_icons(self) -> None:
        lines = view.render_text(
            [view.ViewSection("Раздел", (ViewRow("ok", "Строка", "слова"), ViewRow("fail", "Совет без текста")))],
            indent="  ",
        )

        self.assertEqual(lines, ["  Раздел", "     ✅ Строка: слова", "     ❌ Совет без текста"])


class OneSourceTests(unittest.TestCase):
    """Карточка и текст не расходятся: у них один набор разделов."""

    def test_card_details_are_exactly_the_view_sections(self) -> None:
        service = _service(
            level="fail",
            kind="sni",
            advice=["Подберите стратегию"],
            targets=[_target(path=SectionsTests.STEPS, quic="ok", quic_text="отвечает", transfer={"code": "pause", "level": "warn", "text": "пауза"})],
        )

        card = cards.site_card(service)
        shown = [(section.title, [(line.state, line.name, line.text) for line in section.lines]) for section in card.sections]
        expected = [(section.title, [(row.level, row.title, row.text) for row in section.rows]) for section in view.service_sections(service)]

        self.assertEqual(shown, expected)

    def test_text_lines_carry_every_card_row(self) -> None:
        service = _service(targets=[_target(path=SectionsTests.STEPS, transfer={"code": "pause", "level": "warn", "text": "пауза"})])
        text = "\n".join(view.render_text(view.service_sections(service)))

        for section in cards.site_card(service).sections:
            self.assertIn(section.title, text)
            for line in section.lines:
                self.assertIn(f"{line.name}: {line.text}" if line.text else line.name, text)


if __name__ == "__main__":
    unittest.main()
