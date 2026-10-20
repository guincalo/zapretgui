"""Видеосерверы YouTube этой сети: откуда список, чей узел и чем запасной отличается от первого."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from diagnostics import video_nodes as vn
from utils.ip_owner import IpOwner

PLAYER = ("rr1---sn-aaa.googlevideo.com", "rr2---sn-bbb.googlevideo.com")
MAPPED = "rr1---sn-ccc.googlevideo.com"


class CombineTests(unittest.TestCase):
    def test_player_nodes_come_first_then_the_redirector_node(self) -> None:
        nodes = vn.combine(PLAYER, MAPPED)

        self.assertEqual(nodes.hosts, (*PLAYER, MAPPED))
        self.assertEqual(nodes.sources, (vn.SOURCE_PLAYER, vn.SOURCE_PLAYER, vn.SOURCE_REDIRECTOR))
        self.assertIn("получен от YouTube", nodes.note)
        self.assertIn("добавлен запасным", nodes.note)

    def test_redirector_node_that_repeats_the_player_is_not_doubled(self) -> None:
        nodes = vn.combine(PLAYER, PLAYER[1])

        self.assertEqual(nodes.hosts, PLAYER)
        self.assertIn("совпал с ответом redirector", nodes.note)

    def test_without_the_page_the_redirector_node_is_used_and_the_reason_is_named(self) -> None:
        nodes = vn.combine((), MAPPED, page_failure="страница YouTube не открылась")

        self.assertEqual((nodes.hosts, nodes.sources), ((MAPPED,), (vn.SOURCE_REDIRECTOR,)))
        self.assertIn("страница YouTube не открылась", nodes.note)
        self.assertIn("узел вашей сети", nodes.note)

    def test_without_a_real_node_nothing_is_probed_and_the_general_address_is_not_used(self) -> None:
        nodes = vn.combine((), "", page_failure="страница YouTube грузилась слишком медленно")

        # Общий адрес googlevideo.com — не видеосервер, прямой запрос к нему всегда кончается ошибкой.
        self.assertEqual((nodes.hosts, nodes.sources), ((), ()))
        self.assertIn("слишком медленно", nodes.note)
        self.assertIn("видеосервер не проверялся", nodes.note)

    def test_list_is_cut_to_the_limit_but_keeps_the_redirector_node(self) -> None:
        many = tuple(f"rr{n}---sn-x{n}.googlevideo.com" for n in range(1, 6))

        self.assertEqual(len(vn.combine(many, MAPPED, limit=3).hosts), 4)
        self.assertEqual(vn.combine(many, MAPPED, limit=3).hosts[:3], many[:3])

    def test_source_of_a_host(self) -> None:
        nodes = vn.combine(PLAYER, MAPPED)

        self.assertEqual((nodes.source_of(PLAYER[0]), nodes.source_of(MAPPED), nodes.source_of("x")), (vn.SOURCE_PLAYER, vn.SOURCE_REDIRECTOR, ""))


class DiscoverTests(unittest.TestCase):
    def test_both_sources_are_asked_and_merged(self) -> None:
        nodes = vn.discover(object(), player=lambda _run: (PLAYER, ""), mapped=lambda _run: MAPPED)

        self.assertEqual(nodes.hosts, (*PLAYER, MAPPED))

    def test_blocked_youtube_still_gives_the_network_node(self) -> None:
        nodes = vn.discover(object(), player=lambda _run: ((), "страница YouTube не открылась"), mapped=lambda _run: MAPPED)

        self.assertEqual(nodes.hosts, (MAPPED,))


class OwnerNoteTests(unittest.TestCase):
    def _note(self, owner):
        with patch("utils.ip_owner.lookup_ip_owner", return_value=owner):
            return vn.owner_note(SimpleNamespace(probe_cancel=None), "188.43.1.1")

    def test_node_in_a_providers_network_is_called_a_cache_inside_it(self) -> None:
        note = self._note(IpOwner(asn="20485", prefix="188.43.0.0/16", country="RU", owner="TRANSTELECOM"))

        self.assertIn("узел в сети провайдера, AS20485 (TRANSTELECOM)", note)
        self.assertIn("кэш Google внутри сети", note)

    def test_node_in_googles_own_network_is_named_so(self) -> None:
        self.assertIn("узел в сети Google, AS15169", self._note(IpOwner(asn="15169", prefix="", country="US", owner="GOOGLE")))

    def test_unknown_owner_gives_no_note(self) -> None:
        self.assertEqual(self._note(None), "")
        self.assertEqual(self._note(IpOwner(asn="", prefix="", country="", owner="")), "")


class SpareNoteTests(unittest.TestCase):
    @staticmethod
    def _probe(level: str, code: str = "pause", host: str = "rr2.example"):
        return SimpleNamespace(host=host, transfer=SimpleNamespace(level=level, code=code))

    def test_slow_first_and_fast_spare_points_at_the_node(self) -> None:
        note = vn.spare_note(self._probe("warn"), self._probe("ok", "ok"))

        self.assertIn("дело в первом узле", note)

    def test_both_slow_points_away_from_one_node(self) -> None:
        self.assertIn("дело не в одном узле", vn.spare_note(self._probe("warn"), self._probe("warn")))

    def test_fast_first_or_unknown_spare_says_nothing(self) -> None:
        self.assertEqual(vn.spare_note(self._probe("ok", "ok"), self._probe("ok", "ok")), "")
        self.assertEqual(vn.spare_note(self._probe("warn"), self._probe("unknown", "unknown")), "")


if __name__ == "__main__":
    unittest.main()
