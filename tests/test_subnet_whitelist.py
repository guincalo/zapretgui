"""Подтверждение «белых списков» по подсетям: вывод по долям и сбор с повтором закрытых."""

from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from diagnostics import subnet_whitelist as sw
from diagnostics.subnet_catalog import REGION_FOREIGN, REGION_RU, SUBNET_GROUPS
from utils.socket_cancel import SocketCancel


def _facts(ru_open: int, ru_total: int, foreign_open: int, foreign_total: int) -> sw.SubnetFacts:
    groups = [(f"ru{index}", REGION_RU, index < ru_open) for index in range(ru_total)]
    groups += [(f"fo{index}", REGION_FOREIGN, index < foreign_open) for index in range(foreign_total)]
    return sw.SubnetFacts(groups=tuple(groups))


class JudgeTests(unittest.TestCase):
    def test_verdict_by_shares(self) -> None:
        for name, facts, code in (
            ("белый список", _facts(16, 20, 1, 15), sw.CODE_CONFIRMED),
            ("обычная линия", _facts(20, 20, 15, 15), sw.CODE_REFUTED),
            # РКН закрыл часть зарубежных хостингов — это не белый список.
            ("закрыты отдельные хостинги", _facts(20, 20, 5, 15), sw.CODE_REFUTED),
            ("нет интернета", _facts(0, 20, 0, 15), sw.CODE_UNCLEAR),
            ("почти ничего не отвечает", _facts(6, 20, 0, 15), sw.CODE_UNCLEAR),
        ):
            with self.subTest(name):
                self.assertEqual(sw.judge(facts).code, code)

    def test_no_verdict_when_cancelled_or_empty(self) -> None:
        self.assertIsNone(sw.judge(sw.SubnetFacts(groups=_facts(20, 20, 0, 15).groups, cancelled=True)))
        self.assertIsNone(sw.judge(_facts(20, 20, 0, 0)))

    def test_text_names_the_counts(self) -> None:
        self.assertEqual(
            sw.judge(_facts(18, 20, 0, 15)).text, "из российских сетей отвечают 18 из 20, из зарубежных — 0 из 15"
        )


class CollectTests(unittest.TestCase):
    def test_network_is_open_if_any_address_answers_and_closed_ones_are_asked_again(self) -> None:
        groups = (
            ("Яндекс", REGION_RU, ("192.0.2.1", "192.0.2.2")),
            ("Hetzner", REGION_FOREIGN, ("198.51.100.1",)),
        )
        asked: list[str] = []
        answers = {"192.0.2.1": False, "192.0.2.2": True, "198.51.100.1": False}

        def connect(ip, *, cancel):
            asked.append(ip)
            return answers[ip]

        with ThreadPoolExecutor(4) as pool, patch.object(sw, "connect", connect), patch.object(sw, "RETRY_PAUSE_S", 0.0):
            facts = sw.collect(submit=pool.submit, cancel=SocketCancel(), groups=groups)

        self.assertEqual(facts.groups, (("Яндекс", REGION_RU, True), ("Hetzner", REGION_FOREIGN, False)))
        # Закрытая сеть спрошена дважды, открытая — один раз.
        self.assertEqual(asked.count("198.51.100.1"), 2)
        self.assertEqual(asked.count("192.0.2.2"), 1)

    def test_refused_connection_means_the_packet_got_through(self) -> None:
        cancel = SocketCancel()
        with patch.object(sw.socket.socket, "connect", side_effect=ConnectionRefusedError()):
            self.assertTrue(sw.connect("192.0.2.1", cancel=cancel))
        with patch.object(sw.socket.socket, "connect", side_effect=TimeoutError()):
            self.assertFalse(sw.connect("192.0.2.1", cancel=cancel))


class CatalogTests(unittest.TestCase):
    def test_catalog_has_both_regions_and_unique_subnets(self) -> None:
        regions = [region for _label, region, _ips in SUBNET_GROUPS]
        self.assertGreaterEqual(regions.count(REGION_RU), 10)
        self.assertGreaterEqual(regions.count(REGION_FOREIGN), 10)
        nets = [ip.rsplit(".", 1)[0] for _label, _region, ips in SUBNET_GROUPS for ip in ips]
        self.assertEqual(len(nets), len(set(nets)))


if __name__ == "__main__":
    unittest.main()
