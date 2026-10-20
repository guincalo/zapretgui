"""Доля повторных передач TCP за загрузку: вывод по счётчикам без системы."""

from __future__ import annotations

import unittest

from blockcheck.ui.result_cards_model import build_cards
from diagnostics import block_view
from diagnostics import tcp_stats as ts

SNMP = (
    "Ip: Forwarding DefaultTTL InReceives\nIp: 2 64 100\n"
    "Tcp: RtoAlgorithm RtoMin RtoMax MaxConn ActiveOpens PassiveOpens AttemptFails EstabResets CurrEstab "
    "InSegs OutSegs RetransSegs InErrs OutRsts InCsumErrors\n"
    "Tcp: 1 200 120000 -1 10 5 1 2 3 5000 4000 80 0 7 0\n"
    "Udp: InDatagrams\nUdp: 5\n"
)


class ParseTests(unittest.TestCase):
    def test_counters_come_from_the_tcp_lines_of_proc_net_snmp(self) -> None:
        self.assertEqual(ts.parse_snmp(SNMP), ts.Counters(out_segments=4000, retransmitted=80))

    def test_garbage_or_missing_lines_give_nothing(self) -> None:
        self.assertIsNone(ts.parse_snmp(""))
        self.assertIsNone(ts.parse_snmp("Tcp: A B\nTcp: x y\n"))
        self.assertIsNone(ts.parse_snmp("Ip: A\nIp: 1\n"))


class JudgeTests(unittest.TestCase):
    def test_few_segments_is_no_conclusion(self) -> None:
        self.assertEqual(ts.judge(100, 50).code, ts.LOSS_UNKNOWN)

    def test_thresholds(self) -> None:
        self.assertEqual(ts.judge(2000, 4).code, ts.LOSS_NONE)
        small = ts.judge(2000, 30)
        heavy = ts.judge(2000, 100)

        self.assertEqual((small.code, heavy.code), (ts.LOSS_SMALL, ts.LOSS_HEAVY))
        self.assertIn("1,5 %", small.text)
        self.assertIn("линия теряет пакеты", heavy.text)
        self.assertEqual(heavy.as_dict()["segments"], 2000)

    def test_counter_that_went_backwards_is_not_negative_loss(self) -> None:
        self.assertEqual(ts.judge(-5, -2).code, ts.LOSS_UNKNOWN)


class WindowTests(unittest.TestCase):
    def test_window_takes_the_difference_of_two_readings(self) -> None:
        readings = iter([ts.Counters(1000, 10), ts.Counters(3000, 70)])

        with ts.measure(lambda: next(readings)) as window:
            pass
        verdict = window.verdict()

        self.assertEqual((verdict.segments, verdict.retransmitted, verdict.code), (2000, 60, ts.LOSS_HEAVY))

    def test_unreadable_counters_are_unknown_not_a_crash(self) -> None:
        with ts.measure(lambda: None) as window:
            pass

        self.assertEqual(window.verdict().code, ts.LOSS_UNKNOWN)

    def test_window_closes_even_if_the_download_failed(self) -> None:
        readings = iter([ts.Counters(0, 0), ts.Counters(1000, 0)])

        with self.assertRaises(RuntimeError):
            with ts.measure(lambda: next(readings)) as window:
                raise RuntimeError("обрыв")

        self.assertEqual(window.verdict().segments, 1000)

    def test_this_system_can_be_read(self) -> None:
        # На Linux-хосте и в Windows счётчики читаются; где нельзя — None, и проверка это переживает.
        counters = ts.read_counters()

        self.assertTrue(counters is None or counters.out_segments >= counters.retransmitted >= 0)


class SpeedBlockTests(unittest.TestCase):
    SPEED = {
        "level": "ok",
        "headline": "Скорость одинаковая",
        "names": None,
        "items": [
            {"name": "OVH", "host": "o.example", "domestic": False, "state": "ok", "text": "40 Мбит/с",
             "retransmits": ts.judge(2000, 100).as_dict()},
            {"name": "Яндекс", "host": "y.example", "domestic": True, "state": "ok", "text": "90 Мбит/с",
             "retransmits": ts.judge(2000, 2).as_dict()},
            {"name": "Нет счётчиков", "host": "n.example", "domestic": False, "state": "ok", "text": "30 Мбит/с", "retransmits": None},
            {"name": "Мало данных", "host": "m.example", "domestic": False, "state": "ok", "text": "30 Мбит/с",
             "retransmits": ts.judge(10, 5).as_dict()},
        ],
    }

    def test_losses_get_their_own_section_without_unknown_rows(self) -> None:
        view = block_view.build_block("speed", self.SPEED)
        losses = next(section for section in view.sections if section.title.startswith("Потери"))

        self.assertEqual([(row.title, row.level) for row in losses.rows], [("OVH", "warn"), ("Яндекс", "ok")])
        self.assertIn("заметные потери", losses.rows[0].text)

    def test_text_and_card_carry_the_same_loss_rows(self) -> None:
        text = "\n".join(block_view.text_lines("speed", self.SPEED))
        card = next(card for card in build_cards({"speed": self.SPEED}) if card.key == "speed")

        self.assertIn("заметные потери", text)
        self.assertTrue(any(section.title.startswith("Потери") for section in card.sections))

    def test_report_without_the_field_has_no_section(self) -> None:
        old = {**self.SPEED, "items": [{k: v for k, v in item.items() if k != "retransmits"} for item in self.SPEED["items"]]}

        self.assertFalse(any(section.title.startswith("Потери") for section in block_view.build_block("speed", old).sections))


if __name__ == "__main__":
    unittest.main()
